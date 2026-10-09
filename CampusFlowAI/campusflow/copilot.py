"""Campus Copilot: rule-based intent routing over the SAME services/records as the rest of the app.
It answers only from stored records and says so when information is unavailable."""
import re
from . import analytics, energy, incidents as inc, maintenance, nlp, rooms
from .util import clean_text

HELP = ["Which incidents are overdue?", "Show high priority tickets", "Status of ticket CF-0006", "Find a room for 60 students with a projector at 14:00",
        "Are there any energy anomalies?", "Which assets have the highest maintenance risk?", "What is the status of room B204?", "Give me an overview"]

def _ans(text, intent, data=None, sources=None, found=True):
    return {"answer": text, "intent": intent, "data": data or {}, "sources": sources or [], "found": found}

def ask(conn, query, now):
    q = clean_text(query or "", 300)
    if len(q) < 2: return _ans("Please type a question.", "invalid", found=False)
    ql = q.lower()
    m = re.search(r"\bCF-?(\d{1,6})\b", q, re.I)
    if m:
        iid = f"CF-{int(m.group(1)):04d}"
        try: t = inc.get_incident(conn, iid, now)
        except Exception: return _ans(f"I found no ticket {iid} in the database.", "ticket_status", found=False, sources=["incidents"])
        s = f"{t['id']}: {t['priority']} {t['category']} in {t['room_id'] or 'unknown room'} - status {t['status']}, team {t['assigned_team']}" + (f", assignee {t['assignee']}" if t["assignee"] else "") + \
            (", OVERDUE vs SLA" if t["overdue"] else "") + f". SLA due {t['sla_due']}."
        return _ans(s, "ticket_status", {"incident": t["id"], "history": t["history"]}, [f"incidents:{t['id']}", "status_history"])
    ex = nlp.extract(q)
    if re.search(r"overdue|late|past (?:sla|due)", ql):
        items = [i for i in inc.list_incidents(conn, overdue=True, now=now)]
        return _ans(f"{len(items)} active ticket(s) are past their SLA: " + "; ".join(f"{i['id']} ({i['priority']}, {i['room_id'] or '?'})" for i in items) if items else "No active tickets are past their SLA.",
                    "overdue", {"ids": [i["id"] for i in items]}, ["incidents"])
    if re.search(r"high[- ]priority|critical|p1|p2|urgent", ql) and re.search(r"ticket|incident|issue|show|list|open", ql):
        items = [i for i in inc.list_incidents(conn, now=now) if i["priority"] in ("P1", "P2") and i["status"] in ("Open", "In Progress")]
        return _ans(f"{len(items)} active high-priority ticket(s): " + "; ".join(f"{i['id']} {i['priority']} {i['category']} {i['room_id'] or ''} [{i['status']}]" for i in items) if items else "There are no active P1/P2 tickets.",
                    "high_priority", {"ids": [i["id"] for i in items]}, ["incidents"])
    if re.search(r"energy|electric|power|kwh|consumption", ql):
        sm = energy.summary(conn, now)
        bm = re.search(r"block ([a-e])\b", ql); b = f"Block {bm.group(1).upper()}" if bm else None
        if b:
            bd = next((x for x in sm["buildings"] if x["building"] == b), None)
            if not bd: return _ans(f"No energy readings exist for {b}.", "energy", found=False, sources=["energy_readings"])
            ev = [e for e in sm["anomalies"] if e["building"] == b]
            return _ans(f"{b}: {bd['total_24h_kwh']} kWh in the last 24h vs baseline {bd['baseline_24h_kwh']} kWh (x{bd['ratio']}). " + (f"{len(ev)} anomaly event(s), worst: {max(ev, key=lambda e: e['peak_ratio'])['severity']}." if ev else "No anomalies.") + " (SIMULATED sample data)", "energy", {"building": b}, ["energy_readings"])
        if sm["anomalies"]:
            return _ans(f"{len(sm['anomalies'])} energy anomaly event(s) in the last 24h (SIMULATED data): " + "; ".join(f"{e['building']} {e['severity']} {e['hours']}h peak x{e['peak_ratio']} (~{e['excess_kwh_est']} kWh over baseline)" for e in sm["anomalies"]), "energy", {"events": len(sm["anomalies"])}, ["energy_readings"])
        return _ans("No energy anomalies in the last 24h of the loaded (simulated) readings.", "energy", sources=["energy_readings"])
    if re.search(r"risk|maintenance|asset|inspect|replace", ql) and not re.search(r"\bticket\b", ql):
        rs = maintenance.assess_all(conn, now, persist=False)
        if ex["room"]: rs = [r for r in rs if r["room_id"] == ex["room"]]
        elif ex["equipment"]: rs = [r for r in rs if r["type"] in ex["equipment"]]
        if not rs: return _ans("No matching assets are recorded.", "maintenance", found=False, sources=["assets"])
        top = rs[:3]
        return _ans("Highest maintenance risk (heuristic score): " + "; ".join(f"{r['asset_id']} {r['score']} {r['level']} - {r['actions'][0]}" for r in top), "maintenance", {"assets": [r["asset_id"] for r in top]}, ["assets", "incidents"])
    if re.search(r"\b(room|classroom|hall|free|available|book|seat)", ql) and (ex["affected_people"] or re.search(r"find|need|free|available|book", ql)) and not re.search(r"status of room", ql):
        t = ex["deadline_time"]; date = now.strftime("%Y-%m-%d")
        start = t or None; end = None
        if start:
            from .util import to_min, from_min
            end = from_min(min(to_min(start) + 60, 23 * 60 + 59))
        req = [e for e in ex["equipment"] if e in nlp.INVENTORY_EQUIPMENT]
        res = rooms.search(conn, capacity=ex["affected_people"], equipment=req, date=date if start else None, start=start, end=end, limit=3)
        if not res["candidates"]: return _ans("No room in the database satisfies those constraints" + (f" at {start}" if start else "") + ".", "room_search", {"criteria": res["criteria"]}, ["rooms", "bookings"], found=False)
        w = f" free {start}-{end}" if start else " (no time given, availability not checked)"
        return _ans("Suggested (not booked): " + "; ".join(f"{c['room']} cap {c['capacity']}, score {c['score']}" for c in res["candidates"]) + w + ". Use the Classroom Optimizer to reserve.", "room_search", {"rooms": [c["room"] for c in res["candidates"]]}, ["rooms", "bookings"])
    if ex["room"]:
        r = conn.execute("SELECT * FROM rooms WHERE id=?", (ex["room"],)).fetchone()
        if not r: return _ans(f"Room {ex['room']} is not in the campus inventory.", "room_status", found=False, sources=["rooms"])
        bk = [f"{b['start']}-{b['end']}" for b in conn.execute("SELECT * FROM bookings WHERE room_id=? AND date=? AND status='Confirmed' ORDER BY start", (r["id"], now.strftime("%Y-%m-%d")))]
        tk = [i for i in inc.list_incidents(conn, now=now) if i["room_id"] == r["id"] and i["status"] in ("Open", "In Progress")]
        return _ans(f"{r['id']} ({r['building']}): capacity {r['capacity']}, equipment {r['equipment'].replace('|', ', ')}, status {r['status']}. Bookings today: {', '.join(bk) or 'none'}. Active tickets: {', '.join(t['id'] for t in tk) or 'none'}.", "room_status", {"room": r["id"]}, ["rooms", "bookings", "incidents"])
    if re.search(r"overview|summary|dashboard|status|how (?:are|is) (?:we|things|campus)", ql):
        k = analytics.dashboard(conn, now)["kpis"]
        return _ans(f"Active incidents {k['active_incidents']} ({k['high_priority_active']} high priority, {k['overdue']} overdue); rooms free now {k['rooms_free_now']}/{k['rooms_total_available']}; today's utilization {k['utilization_pct_today']}%; energy anomaly events {k['energy_anomaly_events_24h']} (simulated); high-risk assets {k['high_risk_assets']}.", "overview", k, ["incidents", "rooms", "energy_readings", "assets"])
    return _ans("I can only answer from CampusFlow's records and I could not match that question to incidents, rooms, energy or maintenance data. Try one of the examples.", "unknown", {"examples": HELP}, [], found=False)
