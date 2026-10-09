import unittest
from tests.helpers import *
from campusflow import rooms
from campusflow.errors import AppError

class Rooms(DBCase):
    D = "2026-10-07"
    def S(self, **k):
        a = dict(capacity=60, equipment=["Projector"], date=self.D, start="14:00", end="15:00"); a.update(k)
        return rooms.search(self.conn, **a)
    def rej(self, res, room): return next((r["reasons"] for r in res["rejected"] if r["room"] == room), None)
    def test_ranks_feasible_best_first(self):
        res = self.S(near_room="B204", exclude_room="B204")
        self.assertEqual(res["candidates"][0]["room"], "A101")
        scores = [c["score"] for c in res["candidates"]]; self.assertEqual(scores, sorted(scores, reverse=True))
    def test_insufficient_capacity(self):
        self.assertTrue(any("capacity" in r for r in self.rej(self.S(), "B201")))
    def test_missing_equipment(self):
        self.assertTrue(any("missing equipment: Projector" in r for r in self.rej(self.S(), "D105")))
    def test_time_conflict_and_boundaries(self):
        self.assertTrue(any("already booked" in r for r in self.rej(self.S(), "B202")))       # 13-15 vs 14-15
        self.assertIsNone(self.rej(self.S(start="15:00", end="16:00"), "B202"))               # touching end is free
    def test_fully_booked_room(self):
        self.assertTrue(any("already booked" in r for r in self.rej(self.S(capacity=100, equipment=[]), "A201")))
    def test_maintenance_status_excluded(self):
        self.assertTrue(any("Maintenance" in r for r in self.rej(self.S(capacity=40, equipment=[]), "C103")))
    def test_active_fault_ticket_excludes_equipment(self):
        self.assertTrue(any("fault ticket" in r for r in self.rej(self.S(), "C101")))        # seeded open projector ticket
        from campusflow import incidents
        self.assertIsNone(self.rej(self.S(), "B204"))                       # healthy before any ticket
        incidents.create_incident(self.conn, SCENARIO, "u", self.NOW)
        self.assertTrue(any("fault ticket" in r for r in self.rej(self.S(), "B204")))   # excluded once ticketed
    def test_no_feasible_room(self):
        res = self.S(capacity=1000)
        self.assertEqual(res["candidates"], [])
    def test_validation(self):
        for kw in (dict(capacity=0), dict(start="25:00"), dict(start="15:00", end="14:00"), dict(date="07/10/2026")):
            with self.assertRaises(AppError): self.S(**kw)
    def test_booking_persists_and_prevents_double_booking(self):
        b = rooms.create_booking(self.conn, "A101", self.D, "14:00", "15:00", "Presentation", "faculty1", 60)
        self.assertEqual(b["status"], "Confirmed")
        with self.assertRaises(AppError) as cm: rooms.create_booking(self.conn, "A101", self.D, "14:30", "15:30", "Other", "faculty2")
        self.assertEqual(cm.exception.status, 409)
        res = self.S()
        self.assertTrue(any("already booked" in r for r in self.rej(res, "A101")))        # search now excludes it
        self.assertTrue(self.conn.execute("SELECT 1 FROM audit_events WHERE event_type='booking.created'").fetchone())
    def test_booking_rules(self):
        for args in (("Z999", self.D, "14:00", "15:00", "x"), ("C103", self.D, "14:00", "15:00", "x"), ("B201", self.D, "14:00", "15:00", "x", "u", 100)):
            with self.assertRaises(AppError): rooms.create_booking(self.conn, *args[:5], "u", *(args[6:] if len(args) > 6 else ()))
        with self.assertRaises(AppError): rooms.create_booking(self.conn, "A101", self.D, "15:00", "14:00", "x", "u")
    def test_utilization_is_derived(self):
        u = rooms.utilization(self.conn, self.D); self.assertGreater(u["overall_pct"], 0)
        self.assertEqual(rooms.utilization(self.conn, "2030-01-01")["overall_pct"], 0)
if __name__ == "__main__": unittest.main()
