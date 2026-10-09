"""Independent rule-based verification of a proposed decision against stored records."""
from . import nlp, rooms

def verify(conn, ctx):
    """ctx keys: extraction, classification, priority, room_row, recommendation, window, people_needed.
    Returns {status: PASS|PASS_WITH_WARNINGS|FAIL, checks:[{name,status,detail}], withhold_recommendation:bool}"""
    ex, cl, pr = ctx["extraction"], ctx["classification"], ctx["priority"]
    checks, withhold = [], False
    def add(name, status, detail): checks.append({"name": name, "status": status, "detail": detail})

    add("required_fields", "pass" if cl.get("category") and pr.get("priority") and ctx.get("report") else "fail",
        "category, priority and report text present" if cl.get("category") and pr.get("priority") else "missing category/priority")
    if ex["room"]:
        if ctx["room_row"]: add("room_exists", "pass", f"{ex['room']} found in campus inventory ({ctx['room_row']['building']})")
        else: add("room_exists", "fail", f"{ex['room']} is not in campus inventory - cannot create an operational action for an unknown room")
    else:
        add("room_identified", "warn", "No room mentioned; ticket will be flagged as missing location")
    eq = [e for e in ex["equipment"] if e in nlp.INVENTORY_EQUIPMENT]
    if ctx["room_row"] and eq:
        have = set((ctx["room_row"]["equipment"] or "").split("|"))
        miss = [e for e in eq if e not in have]
        add("equipment_in_inventory", "warn" if miss else "pass",
            (f"Inventory lists no {', '.join(miss)} in {ex['room']} - report may be inaccurate or inventory outdated" if miss
             else f"{', '.join(eq)} listed for {ex['room']}"))
    recomputed = nlp.priority(cl["category"], ex)
    ok = recomputed["score"] == pr["score"] and recomputed["priority"] == pr["priority"]
    add("priority_rules_consistent", "pass" if ok else "fail",
        f"re-applied documented rules: score {recomputed['score']} -> {recomputed['priority']}")
    rec = ctx.get("recommendation")
    if rec and rec.get("top"):
        top = rec["top"]; w = ctx["window"]
        row = conn.execute("SELECT * FROM rooms WHERE id=?", (top["room"],)).fetchone()
        if not row:
            add("recommendation_supported", "warn", f"{top['room']} not found in records - recommendation withheld"); withhold = True
        else:
            why = rooms.check_room(conn, row, ctx.get("people_needed"), rec["criteria"]["equipment"], w["date"], w["start"], w["end"])
            if row["id"] == ex["room"]: why.append("recommended room is the failing room")
            if why: add("recommendation_constraints", "warn", "recommendation withheld: " + "; ".join(why)); withhold = True
            else: add("recommendation_constraints", "pass",
                      f"{top['room']}: capacity {row['capacity']} >= {ctx.get('people_needed')}, equipment satisfied, free {w['start']}-{w['end']} on {w['date']}")
        if ctx.get("window_assumed"): add("time_window", "warn", ctx["window_assumed"])
    elif ctx.get("recommendation_attempted"):
        add("recommendation_supported", "pass", "No feasible alternative exists in stored records; none is claimed")
    status = "FAIL" if any(c["status"] == "fail" for c in checks) else ("PASS_WITH_WARNINGS" if any(c["status"] == "warn" for c in checks) else "PASS")
    return {"status": status, "checks": checks, "withhold_recommendation": withhold}
