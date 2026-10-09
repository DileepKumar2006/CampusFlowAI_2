"""Dashboard + analytics, computed live from the database."""
from collections import Counter
from datetime import timedelta
from . import energy, maintenance, rooms, audit, incidents as inc
from .util import iso, parse_iso

def dashboard(conn, now):
    items = inc.list_incidents(conn, limit=500, now=now)
    active = [i for i in items if i["status"] in ("Open", "In Progress")]
    sm = energy.summary(conn, now)
    risks = maintenance.assess_all(conn, now, persist=False)
    util = rooms.utilization(conn, now.strftime("%Y-%m-%d"))
    nowm = now.hour * 60 + now.minute
    busy = {r["room_id"] for r in conn.execute("SELECT room_id,start,end FROM bookings WHERE date=? AND status='Confirmed'", (now.strftime("%Y-%m-%d"),)).fetchall()
            if int(r["start"][:2]) * 60 + int(r["start"][3:]) <= nowm < int(r["end"][:2]) * 60 + int(r["end"][3:])}
    total_rooms = conn.execute("SELECT COUNT(*) FROM rooms WHERE status='Available'").fetchone()[0]
    seeded = conn.execute("SELECT value FROM meta WHERE key='seeded_at'").fetchone()
    return {"generated_at": iso(now),
            "kpis": {"active_incidents": len(active), "high_priority_active": sum(i["priority"] in ("P1", "P2") for i in active),
                     "overdue": sum(i["overdue"] for i in active), "rooms_free_now": total_rooms - len(busy), "rooms_total_available": total_rooms,
                     "utilization_pct_today": util["overall_pct"], "energy_anomaly_events_24h": len(sm["anomalies"]),
                     "critical_energy_events": sum(e["severity"] == "critical" for e in sm["anomalies"]),
                     "high_risk_assets": sum(r["level"] == "High" for r in risks), "assets_total": len(risks)},
            "top_incidents": active[:6], "top_risks": risks[:4], "top_anomalies": sm["anomalies"][:3],
            "recent_activity": audit.list_events(conn, limit=10),
            "provenance": {"energy": "SIMULATED sample readings", "rooms/timetable/assets/seeded tickets": "SAMPLE seed data",
                           "user-created tickets/bookings": "real records created in this app", "seeded_at": seeded["value"] if seeded else None}}

def analytics(conn, now):
    items = inc.list_incidents(conn, limit=500, now=now)
    by = lambda k: dict(Counter(i[k] for i in items))
    res = {}
    for i in items:
        if i["resolved_at"] and i["status"] in ("Resolved", "Closed"):
            h = (parse_iso(i["resolved_at"]) - parse_iso(i["created_at"])).total_seconds() / 3600
            res.setdefault(i["priority"], []).append(h)
    sm = energy.summary(conn, now); risks = maintenance.assess_all(conn, now, persist=False)
    return {"generated_at": iso(now), "incidents_total": len(items), "by_category": by("category"), "by_priority": dict(sorted(by("priority").items())), "by_status": by("status"),
            "resolution_hours_by_priority": {p: {"count": len(v), "avg_hours": round(sum(v) / len(v), 1)} for p, v in sorted(res.items())},
            "resolution_note": "Averages use only tickets with a resolved timestamp; seeded tickets are sample data.",
            "room_utilization": rooms.utilization(conn, now.strftime("%Y-%m-%d")),
            "energy": {"by_building": [{"building": b["building"], "total_24h_kwh": b["total_24h_kwh"], "baseline_24h_kwh": b["baseline_24h_kwh"], "ratio": b["ratio"]} for b in sm["buildings"]],
                       "anomalies": sm["anomalies"], "provenance": sm.get("provenance")},
            "risk_distribution": dict(Counter(r["level"] for r in risks)),
            "data_sources": {"seeded_tickets": sum(i["source"] == "seed" for i in items), "user_tickets": sum(i["source"] == "user" for i in items)}}

def csv_export(conn, name, now):
    import csv, io
    out = io.StringIO(); w = csv.writer(out)
    if name == "incidents":
        w.writerow(["id", "created_at", "priority", "category", "room", "equipment", "affected_people", "status", "assigned_team", "overdue", "source", "report"])
        for i in inc.list_incidents(conn, limit=500, now=now):
            w.writerow([i["id"], i["created_at"], i["priority"], i["category"], i["room_id"], i["equipment"], i["affected_people"], i["status"], i["assigned_team"], i["overdue"], i["source"], i["report_text"]])
    elif name == "energy_anomalies":
        w.writerow(["building", "start", "end", "hours", "peak_observed_kwh", "baseline_kwh", "peak_ratio", "excess_kwh_est", "severity", "data_provenance"])
        for e in energy.summary(conn, now)["anomalies"]:
            w.writerow([e["building"], e["start"], e["end"], e["hours"], e["peak_observed_kwh"], e["baseline_kwh"], e["peak_ratio"], e["excess_kwh_est"], e["severity"], "SIMULATED"])
    elif name == "maintenance_risk":
        w.writerow(["asset_id", "room", "type", "score", "level", "top_actions"])
        for r in maintenance.assess_all(conn, now, persist=False): w.writerow([r["asset_id"], r["room_id"], r["type"], r["score"], r["level"], " | ".join(r["actions"])])
    else: return None
    return out.getvalue()
