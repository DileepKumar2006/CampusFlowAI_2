"""SQLite access: schema, connections, explicit transactions."""
import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def db_path():
    return os.environ.get("CAMPUSFLOW_DB") or str(ROOT / "data" / "campusflow.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms(
  id TEXT PRIMARY KEY, building TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0),
  equipment TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Available'
    CHECK(status IN ('Available','Maintenance')));
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT NOT NULL REFERENCES rooms(id),
  date TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, title TEXT NOT NULL,
  booked_by TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Cancelled')),
  source TEXT NOT NULL DEFAULT 'user', incident_id TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_bookings_room_date ON bookings(room_id,date);
CREATE TABLE IF NOT EXISTS assets(
  id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), type TEXT NOT NULL,
  condition TEXT NOT NULL CHECK(condition IN ('Good','Fair','Poor')),
  installed_on TEXT NOT NULL, expected_life_years REAL NOT NULL, last_inspection TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  resolved_at TEXT, report_text TEXT NOT NULL, reporter TEXT NOT NULL, category TEXT NOT NULL,
  priority TEXT NOT NULL, priority_score INTEGER NOT NULL, room_id TEXT, equipment TEXT,
  affected_people INTEGER, event TEXT, deadline TEXT, status TEXT NOT NULL DEFAULT 'Open'
    CHECK(status IN ('Open','In Progress','Resolved','Closed')),
  assigned_team TEXT NOT NULL, assignee TEXT, confidence REAL, source TEXT NOT NULL DEFAULT 'user',
  extraction_json TEXT, recommendation_json TEXT, verification_json TEXT, pipeline_json TEXT,
  next_action TEXT, fingerprint TEXT);
CREATE INDEX IF NOT EXISTS idx_inc_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_inc_fp ON incidents(fingerprint,created_at);
CREATE TABLE IF NOT EXISTS status_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL REFERENCES incidents(id),
  from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, note TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS energy_readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, building TEXT NOT NULL, ts TEXT NOT NULL, kwh REAL NOT NULL,
  simulated INTEGER NOT NULL DEFAULT 1, UNIQUE(building,ts));
CREATE INDEX IF NOT EXISTS idx_energy ON energy_readings(building,ts);
CREATE TABLE IF NOT EXISTS risk_assessments(
  asset_id TEXT PRIMARY KEY REFERENCES assets(id), score REAL NOT NULL, level TEXT NOT NULL,
  factors_json TEXT NOT NULL, actions_json TEXT NOT NULL, computed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, event_type TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, detail_json TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type,entity_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

def connect():
    p = db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=15, isolation_level=None)  # autocommit; explicit BEGIN in tx()
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db(conn):
    conn.executescript(SCHEMA)

@contextmanager
def tx(conn):
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK: serialises writers so related writes are atomic."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

def rows(cur):
    return [dict(r) for r in cur.fetchall()]
