"""Incident pipeline: validate -> classify -> extract -> retrieve -> prioritise -> recommend -> verify -> persist -> audit."""
import hashlib, json
from datetime import timedelta
from . import audit, nlp, rooms, verify as verifier
from .db import tx
from .errors import AppError, bad, not_found, conflict
from .util import clean_text, iso, now_dt, parse_iso, from_min, to_min

TRANSITIONS = {"Open": {"In Progress", "Resolved"}, "In Progress": {"Open", "Resolved"},
               "Resolved": {"Closed", "In Progress"}, "Closed": set()}
TEAMS = ["AV & IT Support", "Facilities & Electrical", "Campus Security", "Energy Management", "Academic Operations", "Campus Help Desk"]
DUP_WINDOW_S = 120
TRACE_NOTE = "Concise decision trace (rules, evidence, outcomes). No private reasoning is recorded."

def _row(conn, iid):
    r = conn.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    return dict(r) if r else None

def serialize(r, now=None):
    now = now or now_dt()
    d = dict(r)
    for k in ("extraction", "recommendation", "verification", "pipeline"):
        v = d.pop(k + "_json", None); d[k] = json.loads(v) if v else None
    d.pop("fingerprint", None)
    due = parse_iso(d["created_at"]) + timedelta(hours=nlp.SLA_HOURS[d["priority"]])
    d["sla_due"] = iso(due)
    d["overdue"] = d["status"] in ("Open", "In Progress") and now > due
    return d

def _window(ex, now):
    assumed = None
    date = now.strftime("%Y-%m-%d")
    if ex["deadline_time"]: start = to_min(ex["deadline_time"])
    else:
        start = now.hour * 60; assumed = f"No event time given; availability checked for {from_min(start)}-{from_min(start + 60)} today"
    end = min(start + 60, 23 * 60 + 59)
    note = "Assumed 60-minute duration and today's date (not stated in the report)"
    return {"date": date, "start": from_min(start), "end": from_min(end), "assumption": note}, assumed

def create_incident(conn, report, reporter="anonymous", now=None):
    now = now or now_dt()
    trace = []
    def stage(name, status, summary, detail=None): trace.append({"stage": name, "status": status, "summary": summary, "detail": detail or {}})
    if not isinstance(report, str): raise bad("report must be text")
    text = clean_text(report, 1000)
    if len(text) < 10: raise bad("Please describe the problem in at least 10 characters.")
    if not any(c.isalpha() for c in text): raise bad("Report must contain words describing the problem.")
    stage("1 Validation", "pass", f"{len(text)} characters accepted after sanitising")

    fp = hashlib.sha1(text.lower().encode()).hexdigest()
    cutoff = iso(now - timedelta(seconds=DUP_WINDOW_S))
    dup = conn.execute("SELECT id FROM incidents WHERE fingerprint=? AND created_at>=? AND source='user' ORDER BY seq DESC LIMIT 1", (fp, cutoff)).fetchone()
    if dup:
        out = get_incident(conn, dup["id"], now); out["duplicate"] = True
        out["message"] = f"Identical report submitted within {DUP_WINDOW_S}s - returning existing ticket {dup['id']} instead of creating a duplicate."
        return out

    ex = nlp.extract(text)
    stage("2 Entity extraction", "pass", "Extracted " + ", ".join(k for k in ("room", "equipment", "affected_people", "event", "deadline_time") if ex[k]) if any(ex[k] for k in ("room", "equipment", "affected_people", "event", "deadline_time")) else "No structured entities found", ex)
    cl = nlp.classify(text, ex)
    stage("3 Intent classification", "pass", f"{cl['category']} (heuristic signal {cl['confidence']:.2f})", cl)
    if cl["category"] in ("Information", "Other", "Classroom Request"):
        hint = {"Classroom Request": "This looks like a room request. Use the Classroom Optimizer to search and book a room.",
                "Information": "This looks like a question rather than an incident. Ask Campus Copilot.",
                "Other": "I could not recognise an operational problem. Please mention what is broken or wrong and, ideally, the room."}[cl["category"]]
        with tx(conn): audit.log(conn, reporter, "incident.rejected", "incident", None, {"reason": "not_an_incident", "category": cl["category"], "text": text[:200]}, now)
        raise AppError(422, "not_an_incident", hint, {"classification": cl, "extraction": ex})

    room_row = conn.execute("SELECT * FROM rooms WHERE id=?", (ex["room"],)).fetchone() if ex["room"] else None
    room_row = dict(room_row) if room_row else None
    asset = None
    primary_eq = next((e for e in ex["equipment"] if e in nlp.INVENTORY_EQUIPMENT), None) or (ex["equipment"][0] if ex["equipment"] else None)
    if room_row and primary_eq:
        a = conn.execute("SELECT * FROM assets WHERE room_id=? AND type=?", (room_row["id"], primary_eq)).fetchone()
        asset = dict(a) if a else None
    stage("4 Context retrieval", "pass" if room_row or not ex["room"] else "fail",
          (f"Room {room_row['id']} ({room_row['building']}, cap {room_row['capacity']}, status {room_row['status']})" if room_row else
           (f"Room {ex['room']} not found" if ex["room"] else "No room to retrieve")) + (f"; asset {asset['id']} condition {asset['condition']}" if asset else ""))

    pr = nlp.priority(cl["category"], ex)
    stage("5 Priority", "pass", f"score {pr['score']} -> {pr['priority']} {pr['label']} (SLA {pr['sla_hours']}h)", pr)

    team = nlp.TEAMS[cl["category"]]
    if cl["category"] == "Equipment Failure" and primary_eq in ("AC", "Lighting"): team = "Facilities & Electrical"
    missing = []
    if not ex["room"]: missing.append("room / location")
    if ex["affected_people"] is None: missing.append("number of people affected")

    rec, window, assumed, attempted = None, None, None, False
    people = ex["affected_people"]
    if cl["category"] == "Equipment Failure" and room_row and people:
        attempted = True
        window, assumed = _window(ex, now)
        req = [e for e in ex["equipment"] if e in nlp.INVENTORY_EQUIPMENT and e != "AC"] if primary_eq != "AC" else ["AC"]
        res = rooms.search(conn, capacity=people, equipment=req, date=window["date"], start=window["start"], end=window["end"],
                           near_room=room_row["id"], exclude_room=room_row["id"], limit=4)
        rec = {"top": res["candidates"][0] if res["candidates"] else None, "alternatives": res["candidates"][1:4],
               "rejected": res["rejected"][:8], "criteria": res["criteria"], "window": window, "scoring": res["scoring"],
               "status": "SUGGESTION ONLY - no room has been reserved"}
        stage("6 Resource recommendation", "pass" if rec["top"] else "warn",
              (f"{rec['top']['room']} ranked first (score {rec['top']['score']})" if rec["top"] else "No feasible alternative in stored records"),
              {"checked_rooms": len(res["candidates"]) + len(res["rejected"]), "feasible": res["total_feasible"]})
    else:
        why = ("not an equipment failure" if cl["category"] != "Equipment Failure" else
               "room unknown" if not room_row else "number of people unknown - capacity cannot be checked")
        stage("6 Resource recommendation", "skip", f"Skipped: {why}")

    ver = verifier.verify(conn, {"report": text, "extraction": ex, "classification": cl, "priority": pr, "room_row": room_row,
                                 "recommendation": rec, "window": window, "people_needed": people,
                                 "window_assumed": assumed, "recommendation_attempted": attempted})
    if ver["withhold_recommendation"]: rec = None
    stage("7 Verification", ver["status"].lower(), f"{sum(c['status']=='pass' for c in ver['checks'])}/{len(ver['checks'])} checks passed", ver)
    if ver["status"] == "FAIL":
        with tx(conn): audit.log(conn, reporter, "incident.rejected", "incident", None, {"reason": "verification_failed", "checks": ver["checks"], "text": text[:200]}, now)
        raise AppError(422, "verification_failed", "Verification failed, so no ticket was created: " + "; ".join(c["detail"] for c in ver["checks"] if c["status"] == "fail"),
                       {"verification": ver, "extraction": ex})

    # next action: recommendation vs executed are kept strictly separate
    if rec and rec["top"]:
        nxt = (f"Review suggested room {rec['top']['room']} for {rec['window']['start']}-{rec['window']['end']} and reserve it via the Classroom Optimizer if suitable "
               f"(NOT yet booked). Assign {team} to inspect {primary_eq or 'the issue'} in {ex['room']}.")
    elif attempted:
        nxt = f"No alternative room satisfies the constraints in stored records. Escalate to {team} and contact Academic Operations to arrange a manual alternative."
    else:
        nxt = f"{team} to triage" + (f" the {primary_eq} issue in {ex['room']}." if ex["room"] and primary_eq else ".") + (" Request missing details: " + ", ".join(missing) + "." if missing else "")

    with tx(conn):
        cur = conn.execute(
            "INSERT INTO incidents(id,created_at,updated_at,report_text,reporter,category,priority,priority_score,room_id,equipment,affected_people,event,deadline,status,assigned_team,confidence,source,extraction_json,recommendation_json,verification_json,pipeline_json,next_action,fingerprint)"
            " VALUES('TMP',?,?,?,?,?,?,?,?,?,?,?,?,'Open',?,?,'user',?,?,?,?,?,?)",
            (iso(now), iso(now), text, reporter, cl["category"], pr["priority"], pr["score"], ex["room"], primary_eq, people, ex["event"],
             ex["deadline_time"], team, cl["confidence"], json.dumps(ex), json.dumps(rec), json.dumps(ver), "[]", nxt, fp))
        iid = f"CF-{cur.lastrowid:04d}"
        conn.execute("UPDATE incidents SET id=? WHERE seq=?", (iid, cur.lastrowid))
        conn.execute("INSERT INTO status_history(incident_id,from_status,to_status,actor,note,at) VALUES(?,?,?,?,?,?)", (iid, None, "Open", reporter, "Ticket created", iso(now)))
        audit.log(conn, reporter, "incident.created", "incident", iid, {"category": cl["category"], "priority": pr["priority"], "score": pr["score"], "room": ex["room"],
                                                                         "recommended_room": rec["top"]["room"] if rec and rec["top"] else None, "verification": ver["status"]}, now)
    back = _row(conn, iid)
    ok = bool(back and back["id"] == iid and conn.execute("SELECT 1 FROM audit_events WHERE entity_id=? AND event_type='incident.created'", (iid,)).fetchone())
    ver["checks"].append({"name": "persisted", "status": "pass" if ok else "fail", "detail": f"ticket {iid} read back from database with audit event" if ok else "read-back failed"})
    if not ok: ver["status"] = "FAIL"
    stage("8 Persistence", "pass" if ok else "fail", f"Ticket {iid} stored in SQLite and read back" if ok else "Could not confirm persistence")
    stage("9 Audit", "pass" if ok else "fail", "incident.created recorded; status history initialised")
    with tx(conn):
        conn.execute("UPDATE incidents SET verification_json=?, pipeline_json=? WHERE id=?", (json.dumps(ver), json.dumps(trace), iid))
        audit.log(conn, "system", "incident.verified", "incident", iid, {"status": ver["status"]}, now)
    if not ok: raise AppError(500, "persistence_failed", "The ticket could not be confirmed in storage. Please retry.")
    out = get_incident(conn, iid, now)
    out["executed_actions"] = [f"Ticket {iid} created and stored", "Audit trail recorded", "Dashboard data updated"]
    out["not_executed"] = ["No technician or team has been notified (no notification integration in this prototype)",
                           "No room has been reserved (recommendations are suggestions until booked)", "No device has been repaired"]
    out["message"] = f"Ticket {iid} created ({pr['priority']} {pr['label']})."
    return out

def get_incident(conn, iid, now=None):
    r = _row(conn, iid)
    if not r: raise not_found(f"Incident {iid} not found")
    d = serialize(r, now)
    d["history"] = [dict(h) for h in conn.execute("SELECT * FROM status_history WHERE incident_id=? ORDER BY id", (iid,))]
    d["audit"] = list(reversed(audit.list_events(conn, "incident", iid, 100)))
    return d

def list_incidents(conn, status=None, priority=None, category=None, q=None, overdue=None, limit=200, now=None):
    sql, a = "SELECT * FROM incidents WHERE 1=1", []
    if status:
        if status not in TRANSITIONS: raise bad("status must be one of Open, In Progress, Resolved, Closed")
        sql += " AND status=?"; a.append(status)
    if priority:
        if priority not in nlp.SLA_HOURS: raise bad("priority must be P1..P5")
        sql += " AND priority=?"; a.append(priority)
    if category: sql += " AND category=?"; a.append(category)
    if q:
        like = "%" + clean_text(q, 80).replace("%", "").replace("_", "") + "%"
        sql += " AND (id LIKE ? OR report_text LIKE ? OR room_id LIKE ? OR assigned_team LIKE ?)"; a += [like] * 4
    sql += " ORDER BY seq DESC LIMIT ?"; a.append(max(1, min(int(limit), 500)))
    items = [serialize(r, now) for r in conn.execute(sql, a).fetchall()]
    if overdue is not None: items = [i for i in items if i["overdue"] == overdue]
    for i in items: i.pop("pipeline", None); i.pop("verification", None); i.pop("recommendation", None); i.pop("extraction", None)
    return items

def change_status(conn, iid, new_status, note, actor, now=None):
    now = now or now_dt()
    if new_status not in TRANSITIONS: raise bad("status must be one of Open, In Progress, Resolved, Closed")
    note = clean_text(note or "", 300)
    with tx(conn):
        r = _row(conn, iid)
        if not r: raise not_found(f"Incident {iid} not found")
        old = r["status"]
        if new_status == old: raise conflict(f"Incident is already {old}")
        if new_status not in TRANSITIONS[old]:
            raise conflict(f"Invalid transition {old} -> {new_status}. Allowed: {', '.join(sorted(TRANSITIONS[old])) or 'none (closed is final)'}")
        resolved = iso(now) if new_status == "Resolved" else (None if new_status in ("Open", "In Progress") else r["resolved_at"])
        conn.execute("UPDATE incidents SET status=?, updated_at=?, resolved_at=? WHERE id=?", (new_status, iso(now), resolved, iid))
        conn.execute("INSERT INTO status_history(incident_id,from_status,to_status,actor,note,at) VALUES(?,?,?,?,?,?)", (iid, old, new_status, actor, note, iso(now)))
        audit.log(conn, actor, "incident.status_changed", "incident", iid, {"from": old, "to": new_status, "note": note}, now)
    return get_incident(conn, iid, now)

def assign(conn, iid, team, assignee, actor, now=None):
    now = now or now_dt()
    if team not in TEAMS: raise bad("team must be one of: " + ", ".join(TEAMS))
    assignee = clean_text(assignee or "", 80) or None
    with tx(conn):
        r = _row(conn, iid)
        if not r: raise not_found(f"Incident {iid} not found")
        conn.execute("UPDATE incidents SET assigned_team=?, assignee=?, updated_at=? WHERE id=?", (team, assignee, iso(now), iid))
        audit.log(conn, actor, "incident.assigned", "incident", iid, {"team_before": r["assigned_team"], "team": team, "assignee": assignee}, now)
    return get_incident(conn, iid, now)
