import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime

class DBCase(unittest.TestCase):
    """Fresh seeded SQLite DB per test. NOW is fixed (Wed 10:00) so results are deterministic."""
    NOW = datetime(2026, 10, 7, 10, 0, 0)
    def setUp(self):
        from campusflow import db, seed
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(self.path)
        os.environ["CAMPUSFLOW_DB"] = self.path
        self.conn = db.connect(); db.init_db(self.conn); seed.seed(self.conn, self.NOW)
    def tearDown(self):
        self.conn.close()
        for ext in ("", "-wal", "-shm"):
            try: os.unlink(self.path + ext)
            except OSError: pass

SCENARIO = "The projector in B204 has stopped working. We have a presentation at 2 PM for 60 students."
