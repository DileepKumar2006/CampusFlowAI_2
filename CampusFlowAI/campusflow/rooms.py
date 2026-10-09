"""Room search/ranking with hard constraints, and conflict-safe bookings."""
from . import audit
from .db import tx
from .errors import bad, not_found, conflict
from .util import to_min, valid_hhmm, valid_date, iso, clean_text

def _equip(r): return {e for e in (r["equipment"] or "").split("|") if e}

def booked_conflicts(conn, room_id, date, start, end):
    s, e = to_min(start), to_min(end)
    out = []
    for b in conn.execute("SELECT * FROM bookings WHERE room_id=? AND date=? AND status='Confirmed'", (room_id, date)):
        if to_min(b["start"]) < e and to_min(b["end"]) > s: out.append(dict(b))
    return out

def check_room(conn, room, capacity, required, date, start, end):
    """Return list of hard-constraint violations for one room (empty list = feasible)."""
    why = []
    if room["status"] != "Available": why.append(f"room status is {room['status']}")
    if capacity and room["capacity"] < capacity: why.append(f"capacity {room['capacity']} < required {capacity}")
    missing = sorted(set(required) - _equip(room))
    if missing: why.append("missing equipment: " + ", ".join(missing))
    for eq in required:  # equipment with an unresolved fault ticket is not a usable alternative
        t = conn.execute("SELECT id FROM incidents WHERE room_id=? AND equipment=? AND status IN ('Open','In Progress') LIMIT 1", (room["id"], eq)).fetchone()
        if t: why.append(f"{eq} has an active fault ticket ({t['id']})")
    if date and start and end:
        for b in booked_conflicts(conn, room["id"], date, start, end):
            why.append(f"already booked {b['start']}-{b['end']} ({b['title']})")
    return why

def search(conn, capacity=None, equipment=(), date=None, start=None, end=None, building=None, near_room=None, exclude_room=None, limit=8):
    if capacity is not None and (not isinstance(capacity, int) or capacity < 1 or capacity > 2000): raise bad("capacity must be an integer between 1 and 2000")
    for k, v in (("start", start), ("end", end)):
        if v is not None and not valid_hhmm(v): raise bad(f"{k} must be HH:MM (24h)")
    if (start is None) != (end is None): raise bad("provide both start and end, or neither")
    if start and to_min(start) >= to_min(end): raise bad("start must be before end")
    if date is not None and not valid_date(date): raise bad("date must be YYYY-MM-DD")
    if date is None and start: raise bad("date is required when a time window is given")
    near_b = None
    if near_room:
        nr = conn.execute("SELECT building FROM rooms WHERE id=?", (near_room,)).fetchone()
        near_b = nr["building"] if nr else None
    feasible, rejected = [], []
    for r in conn.execute("SELECT * FROM rooms ORDER BY id").fetchall():
        if exclude_room and r["id"] == exclude_room: continue
        if building and r["building"].lower() != building.lower(): continue
        why = check_room(conn, r, capacity, equipment, date, start, end)
        if why: rejected.append({"room": r["id"], "building": r["building"], "reasons": why}); continue
        have = _equip(r); need = capacity or 1
        fit = round(40 * min(need / r["capacity"], 1.0), 1)            # tight fit scores higher than a huge hall
        eqm = 30.0                                                     # hard requirement already satisfied
        extra = min(10, 2 * len(have - set(equipment)))
        prox = 20 if (near_b and r["building"] == near_b) else 0
        score = round(fit + eqm + extra + prox, 1)
        reasons = [f"capacity {r['capacity']} for {need} needed (fit {fit}/40)",
                   "all required equipment present" if equipment else "no specific equipment required",
                   (f"free {start}-{end} on {date}" if start else "availability not checked (no time window)"),
                   (f"same building as {near_room} (+20)" if prox else (f"different building from {near_room} (+0)" if near_b else "no proximity preference"))]
        feasible.append({"room": r["id"], "building": r["building"], "capacity": r["capacity"],
                         "equipment": sorted(have), "score": score, "reasons": reasons,
                         "status": "Suggested - not booked"})
    feasible.sort(key=lambda x: (-x["score"], x["room"]))
    return {"candidates": feasible[:limit], "total_feasible": len(feasible), "rejected": rejected,
            "criteria": {"capacity": capacity, "equipment": list(equipment), "date": date, "start": start, "end": end,
                         "building": building, "near_room": near_room},
            "scoring": "score = capacity fit (0-40) + equipment (30, hard) + extra equipment (0-10) + proximity (0/20)"}

def create_booking(conn, room_id, date, start, end, title, actor, attendees=None, incident_id=None):
    if not valid_date(date or ""): raise bad("date must be YYYY-MM-DD")
    if not (valid_hhmm(start) and valid_hhmm(end)): raise bad("start/end must be HH:MM (24h)")
    if to_min(start) >= to_min(end): raise bad("start must be before end")
    title = clean_text(title or "", 120)
    if not title: raise bad("title is required")
    if attendees is not None and (not isinstance(attendees, int) or attendees < 1): raise bad("attendees must be a positive integer")
    with tx(conn):  # BEGIN IMMEDIATE: the overlap check and insert are atomic -> no double booking
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if not room: raise not_found(f"Room {room_id} does not exist")
        if room["status"] != "Available": raise conflict(f"Room {room_id} is not available ({room['status']})")
        if attendees and attendees > room["capacity"]: raise conflict(f"Room {room_id} holds {room['capacity']}, requested {attendees}")
        clash = booked_conflicts(conn, room_id, date, start, end)
        if clash: raise conflict(f"Room {room_id} is already booked in that window", {"conflicts": clash})
        cur = conn.execute("INSERT INTO bookings(room_id,date,start,end,title,booked_by,status,source,incident_id,created_at) VALUES(?,?,?,?,?,?,'Confirmed','user',?,?)",
                           (room_id, date, start, end, title, actor, incident_id, iso()))
        bid = cur.lastrowid
        audit.log(conn, actor, "booking.created", "booking", str(bid), {"room": room_id, "date": date, "start": start, "end": end, "incident": incident_id})
    return dict(conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone())

def utilization(conn, date):
    """Booked minutes / available minutes within 08:00-18:00 for rooms with status Available."""
    OPEN, CLOSE = 480, 1080
    rooms = [dict(r) for r in conn.execute("SELECT * FROM rooms")]
    per = {}; tot_b = tot_a = 0
    for r in rooms:
        if r["status"] != "Available": continue
        used = 0
        for b in conn.execute("SELECT start,end FROM bookings WHERE room_id=? AND date=? AND status='Confirmed'", (r["id"], date)):
            used += max(0, min(to_min(b["end"]), CLOSE) - max(to_min(b["start"]), OPEN))
        used = min(used, CLOSE - OPEN)
        d = per.setdefault(r["building"], [0, 0]); d[0] += used; d[1] += CLOSE - OPEN
        tot_b += used; tot_a += CLOSE - OPEN
    return {"date": date, "overall_pct": round(100 * tot_b / tot_a, 1) if tot_a else 0.0,
            "by_building": {b: round(100 * u / a, 1) for b, (u, a) in sorted(per.items())},
            "definition": "confirmed booked minutes / (08:00-18:00 minutes of rooms with status Available)"}
