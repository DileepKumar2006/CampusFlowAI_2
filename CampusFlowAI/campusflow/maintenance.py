"""Heuristic maintenance-risk score (0-100). NOT a trained or evaluated predictive model."""
import json
from datetime import datetime, timedelta
from . import audit
from .db import tx
from .errors import not_found, bad
from .util import iso, parse_iso

LEVELS = [(60, "High"), (35, "Medium"), (0, "Low")]
RULES = ["Condition: Poor 30 / Fair 12 / Good 0 (max 30).",
         "Age: 25 x min(age / expected life, 1) (max 25).",
         "Incidents in last 90 days for the same room+equipment: 8 each (max 25).",
         "Inspection gap: >365d 20, >180d 12, >90d 5, else 0 (max 20).",
         "High >= 60, Medium >= 35, Low otherwise. Weights are judgement-based defaults, not fitted to data."]

def score_asset(asset, incidents_90d, now):
    inst = datetime.strptime(asset["installed_on"], "%Y-%m-%d")
    age = max((now - inst).days / 365.25, 0)
    insp_days = max((now - datetime.strptime(asset["last_inspection"], "%Y-%m-%d")).days, 0)
    cond = {"Poor": 30, "Fair": 12, "Good": 0}[asset["condition"]]
    agep = round(25 * min(age / asset["expected_life_years"], 1), 1)
    incp = min(25, 8 * incidents_90d)
    inspp = 20 if insp_days > 365 else 12 if insp_days > 180 else 5 if insp_days > 90 else 0
    factors = [
        {"factor": "Condition", "points": cond, "max": 30, "detail": f"recorded condition: {asset['condition']}"},
        {"factor": "Age", "points": agep, "max": 25, "detail": f"{age:.1f} y of {asset['expected_life_years']:g} y expected life"},
        {"factor": "Recent incidents", "points": incp, "max": 25, "detail": f"{incidents_90d} incident(s) in last 90 days"},
        {"factor": "Inspection gap", "points": inspp, "max": 20, "detail": f"{insp_days} days since last inspection"}]
    total = round(sum(f["points"] for f in factors), 1)
    level = next(l for floor, l in LEVELS if total >= floor)
    actions = []
    if inspp >= 12: actions.append(f"Schedule an inspection of {asset['type']} in {asset['room_id']} (last inspected {insp_days} days ago).")
    if cond >= 30: actions.append(f"Plan repair or replacement of {asset['type']} in {asset['room_id']} (condition Poor).")
    if incp >= 8: actions.append("Review root cause of recent incidents before closing further tickets for this asset.")
    if agep >= 20: actions.append("Asset is at/over expected life: budget for replacement.")
    if not actions: actions.append("No preventive action suggested by the rules; continue routine inspection.")
    return {"asset_id": asset["id"], "room_id": asset["room_id"], "type": asset["type"], "score": total, "level": level,
            "factors": factors, "actions": actions, "label": "heuristic risk score (not a trained model)"}

def assess_all(conn, now, persist=True):
    cutoff = iso(now - timedelta(days=90))
    out = []
    for a in conn.execute("SELECT * FROM assets ORDER BY id").fetchall():
        n = conn.execute("SELECT COUNT(*) FROM incidents WHERE room_id=? AND equipment=? AND created_at>=?",
                         (a["room_id"], a["type"], cutoff)).fetchone()[0]
        out.append(score_asset(dict(a), n, now))
    out.sort(key=lambda x: -x["score"])
    if persist:
        with tx(conn):
            for r in out:
                conn.execute("INSERT INTO risk_assessments(asset_id,score,level,factors_json,actions_json,computed_at) VALUES(?,?,?,?,?,?) "
                             "ON CONFLICT(asset_id) DO UPDATE SET score=excluded.score,level=excluded.level,factors_json=excluded.factors_json,actions_json=excluded.actions_json,computed_at=excluded.computed_at",
                             (r["asset_id"], r["score"], r["level"], json.dumps(r["factors"]), json.dumps(r["actions"]), iso(now)))
    return out

def mark_inspected(conn, asset_id, condition, actor, now):
    if condition not in (None, "Good", "Fair", "Poor"): raise bad("condition must be Good, Fair or Poor")
    with tx(conn):
        a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a: raise not_found(f"Asset {asset_id} not found")
        new_cond = condition or a["condition"]
        conn.execute("UPDATE assets SET last_inspection=?, condition=? WHERE id=?", (now.strftime("%Y-%m-%d"), new_cond, asset_id))
        audit.log(conn, actor, "asset.inspected", "asset", asset_id, {"condition_before": a["condition"], "condition_after": new_cond}, now)
    return next(x for x in assess_all(conn, now) if x["asset_id"] == asset_id)
