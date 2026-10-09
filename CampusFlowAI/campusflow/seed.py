"""Seed data. EVERYTHING here is SAMPLE data created for the demo: room inventory (from the original
prototype CSV, B204 given a projector so the demo scenario is coherent), a sample timetable,
sample assets, sample past tickets and SIMULATED hourly energy readings."""
import csv, json, random
from datetime import timedelta
from pathlib import Path
from . import audit
from .db import tx, ROOT
from .util import iso

ROOM_CSV = ROOT / "data" / "campus_rooms_seed.csv"
ABBR = {"Projector": "PROJ", "Smart Board": "SBRD", "Computer": "COMP", "Microphone": "MIC", "AC": "AC"}
LIFE = {"Projector": 6, "Smart Board": 8, "Computer": 5, "Microphone": 7, "AC": 10}
ROOM_STATUS_OVERRIDE = {"C103": "Maintenance"}
TIMETABLE = [("A201", "08:00", "18:00", "Sample timetable: Advanced Algorithms"), ("E201", "08:00", "18:00", "Sample timetable: Workshop block"),
             ("B202", "13:00", "15:00", "Sample timetable: Data Structures"), ("A102", "13:30", "14:30", "Sample timetable: Databases lab"),
             ("C201", "10:00", "12:00", "Sample timetable: Networks"), ("D101", "09:00", "11:00", "Sample timetable: Guest lecture")]
# asset overrides: (room,type) -> (condition, years_old, last_inspection_days_ago)
ASSET_OVERRIDE = {("B204", "Projector"): ("Poor", 7.0, 400), ("D101", "AC"): ("Fair", 9.0, 200), ("A102", "Smart Board"): ("Fair", 5.0, 150),
                  ("C101", "Projector"): ("Fair", 5.5, 210), ("A101", "Projector"): ("Good", 2.0, 40)}
PAST = [  # id seq order; (hours_ago, text, cat, pri, score, room, eq, people, status, team, resolved_after_h)
    (30, "AC not cooling in lecture hall D101, students complaining about heat", "Equipment Failure", "P3", 38, "D101", "AC", None, "Open", "Facilities & Electrical", None),
    (3, "Wi-Fi is down in C201 during networks lab for 40 students", "Equipment Failure", "P2", 58, "C201", "Wi-Fi", 40, "In Progress", "AV & IT Support", None),
    (50, "Tube light flickering in B201", "Equipment Failure", "P4", 18, "B201", "Lighting", None, "Resolved", "Facilities & Electrical", 10),
    (120, "Smart board in A102 is not responding to touch", "Equipment Failure", "P3", 30, "A102", "Smart Board", None, "Closed", "AV & IT Support", 20),
    (48, "Projector in C101 is very dim, lamp may be failing", "Equipment Failure", "P4", 18, "C101", "Projector", None, "Open", "AV & IT Support", None)]

def load_rooms():
    out = []
    with open(ROOM_CSV, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            eq = r["equipment"]
            if r["room"] == "B204" and "Projector" not in eq: eq = "Projector|" + eq   # original CSV lacked it; needed for the demo scenario
            out.append((r["room"], r["building"], int(r["capacity"]), eq, ROOM_STATUS_OVERRIDE.get(r["room"], "Available")))
    return out

def energy_rows(now):
    rng = random.Random(42)
    end = now.replace(minute=0, second=0, microsecond=0)
    scale = {"Block A": 52, "Block B": 41, "Block C": 63, "Block D": 78, "Block E": 35}
    prof = [0.35, 0.33, 0.32, 0.32, 0.34, 0.42, 0.6, 0.85, 1.0, 1.05, 1.05, 1.0, 0.95, 1.0, 1.05, 1.0, 0.95, 0.85, 0.7, 0.6, 0.5, 0.45, 0.4, 0.37]
    ws = end - timedelta(hours=23)
    rows = []
    for b, s in scale.items():
        for h in range(7 * 24, -1, -1):
            t = end - timedelta(hours=h)
            v = s * prof[t.hour] * (1 + rng.uniform(-0.06, 0.06))
            if t >= ws:
                back = int((end - t).total_seconds() // 3600)             # hours before the newest reading
                if b == "Block C" and 4 <= back <= 8: v *= 2.4            # injected SAMPLE anomaly: 5-hour sustained draw
                if b == "Block D" and 12 <= back <= 13: v *= 1.35         # injected SAMPLE anomaly: 2-hour spike
            rows.append((b, iso(t), round(v, 2), 1))
    return rows

def seed(conn, now):
    with tx(conn):
        for t in ("audit_events", "status_history", "risk_assessments", "incidents", "bookings", "assets", "energy_readings", "rooms"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('incidents','bookings','audit_events','status_history','energy_readings')")
        rooms = load_rooms()
        conn.executemany("INSERT INTO rooms VALUES(?,?,?,?,?)", rooms)
        today = now.strftime("%Y-%m-%d")
        for rid, st, en, title in TIMETABLE:
            conn.execute("INSERT INTO bookings(room_id,date,start,end,title,booked_by,status,source,created_at) VALUES(?,?,?,?,?,?,'Confirmed','seed',?)", (rid, today, st, en, title, "seed", iso(now)))
        rng = random.Random(7)
        for rid, _, _, eq, _ in rooms:
            for e in [x for x in eq.split("|") if x in ABBR]:
                cond, yrs, insp = ASSET_OVERRIDE.get((rid, e), (rng.choice(["Good", "Good", "Good", "Fair"]), round(rng.uniform(0.5, LIFE[e] * 0.8), 1), rng.randint(20, 170)))
                conn.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?)", (f"AST-{rid}-{ABBR[e]}", rid, e, cond, (now - timedelta(days=int(yrs * 365.25))).strftime("%Y-%m-%d"), LIFE[e], (now - timedelta(days=insp)).strftime("%Y-%m-%d")))
        conn.executemany("INSERT INTO energy_readings(building,ts,kwh,simulated) VALUES(?,?,?,?)", energy_rows(now))
        for i, (ago, text, cat, pri, score, room, eq, ppl, status, team, res_h) in enumerate(PAST, 1):
            c = now - timedelta(hours=ago); iid = f"CF-{i:04d}"
            resolved = iso(c + timedelta(hours=res_h)) if res_h else None
            conn.execute("INSERT INTO incidents(seq,id,created_at,updated_at,resolved_at,report_text,reporter,category,priority,priority_score,room_id,equipment,affected_people,status,assigned_team,confidence,source,next_action) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'seed',?)",
                         (i, iid, iso(c), resolved or iso(c), resolved, text, "seed", cat, pri, score, room, eq, ppl, status, team, None, "Seeded sample ticket"))
            conn.execute("INSERT INTO status_history(incident_id,from_status,to_status,actor,note,at) VALUES(?,?,?,?,?,?)", (iid, None, "Open", "seed", "Seeded sample ticket", iso(c)))
            if status != "Open": conn.execute("INSERT INTO status_history(incident_id,from_status,to_status,actor,note,at) VALUES(?,?,?,?,?,?)", (iid, "Open", "In Progress" if status == "In Progress" else "Resolved", "seed", "Seeded", iso(c + timedelta(hours=1))))
            if status == "Closed": conn.execute("INSERT INTO status_history(incident_id,from_status,to_status,actor,note,at) VALUES(?,?,?,?,?,?)", (iid, "Resolved", "Closed", "seed", "Seeded", resolved))
            audit.log(conn, "seed", "incident.seeded", "incident", iid, {"note": "sample ticket"}, c)
        conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='incidents'", (len(PAST),))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('seeded_at',?)", (iso(now),))
        audit.log(conn, "system", "demo.seeded", "system", None, {"rooms": len(rooms), "note": "all sample/simulated data"}, now)

def ensure_seeded(conn, now):
    if conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0: seed(conn, now)
