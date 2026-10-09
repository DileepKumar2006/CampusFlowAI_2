import unittest
from datetime import datetime, timedelta
from tests.helpers import *
from campusflow import energy, maintenance
from campusflow.util import iso

def series(vals_by_day, b="Block X", start=datetime(2026, 10, 1)):
    out = []; t = start
    for day in vals_by_day:
        for h, v in enumerate(day): out.append({"building": b, "ts": iso(start + timedelta(days=len(out)//24, hours=h)), "kwh": v})
    return out

class Energy(unittest.TestCase):
    def base(self, days=5): return [[10.0] * 24 for _ in range(days)]
    def test_flat_has_no_anomaly(self):
        r = series(self.base(6)); self.assertEqual(energy.detect(r, datetime(2026, 10, 6)), [])
    def test_warning_and_critical_by_ratio(self):
        d = self.base(6); d[5][10] = 13.0; d[5][20] = 16.0
        ev = energy.detect(series(d), datetime(2026, 10, 6))
        self.assertEqual(sorted(e["severity"] for e in ev), ["critical", "warning"])
    def test_just_under_threshold_not_flagged(self):
        d = self.base(6); d[5][10] = 12.4; self.assertEqual(energy.detect(series(d), datetime(2026, 10, 6)), [])
    def test_sustained_escalates(self):
        d = self.base(6)
        for h in (2, 3, 4): d[5][h] = 12.6          # ratio 1.26 each (warning level) but 3 consecutive hours
        ev = energy.detect(series(d), datetime(2026, 10, 6))
        self.assertEqual(len(ev), 1); self.assertEqual((ev[0]["hours"], ev[0]["severity"]), (3, "critical"))
        self.assertAlmostEqual(ev[0]["excess_kwh_est"], 7.8, 1)
    def test_insufficient_baseline_never_flags(self):
        d = self.base(3); d[2][5] = 99.0           # only 2 reference days -> < MIN_REF
        self.assertEqual(energy.detect(series(d), datetime(2026, 10, 3)), [])
    def test_fields_present(self):
        d = self.base(6); d[5][8] = 20.0
        e = energy.detect(series(d), datetime(2026, 10, 6))[0]
        for k in ("severity", "peak_observed_kwh", "baseline_kwh", "recommended_action", "evidence"): self.assertIn(k, e)
        self.assertEqual((e["peak_observed_kwh"], e["baseline_kwh"]), (20.0, 10.0))

class EnergySeed(DBCase):
    def test_seeded_anomalies_detected_and_labelled_simulated(self):
        s = energy.summary(self.conn, self.NOW)
        self.assertTrue(s["simulated"]); self.assertIn("SIMULATED", s["provenance"])
        self.assertIn("Block C", {e["building"] for e in s["anomalies"]})
        self.assertNotIn("Block A", {e["building"] for e in s["anomalies"]})   # normal buildings stay clean

class Maint(DBCase):
    def asset(self, **o):
        a = dict(id="X", room_id="R", type="Projector", condition="Good", installed_on="2026-01-01", expected_life_years=6, last_inspection="2026-09-20"); a.update(o); return a
    def test_healthy_low(self):
        r = maintenance.score_asset(self.asset(), 0, self.NOW); self.assertEqual(r["level"], "Low"); self.assertLess(r["score"], 15)
    def test_worst_case_high_and_explained(self):
        r = maintenance.score_asset(self.asset(condition="Poor", installed_on="2018-01-01", last_inspection="2025-01-01"), 4, self.NOW)
        self.assertEqual(r["level"], "High"); self.assertEqual(r["score"], 100.0)
        self.assertEqual({f["factor"] for f in r["factors"]}, {"Condition", "Age", "Recent incidents", "Inspection gap"})
        self.assertTrue(any("replacement" in a for a in r["actions"]))
    def test_incidents_raise_score_and_cap(self):
        a = self.asset(); s0 = maintenance.score_asset(a, 0, self.NOW)["score"]
        self.assertAlmostEqual(maintenance.score_asset(a, 1, self.NOW)["score"] - s0, 8, places=6)
        self.assertAlmostEqual(maintenance.score_asset(a, 9, self.NOW)["score"] - s0, 25, places=6)
    def test_seeded_b204_projector_is_high(self):
        top = maintenance.assess_all(self.conn, self.NOW)[0]; self.assertEqual((top["asset_id"], top["level"]), ("AST-B204-PROJ", "High"))
    def test_new_ticket_changes_risk_and_inspection_lowers_it(self):
        from campusflow import incidents
        before = next(r for r in maintenance.assess_all(self.conn, self.NOW) if r["asset_id"] == "AST-C101-PROJ")["score"]
        incidents.create_incident(self.conn, "Projector in C101 is flickering and not displaying, lecture at 3 pm", "u", self.NOW)
        mid = next(r for r in maintenance.assess_all(self.conn, self.NOW) if r["asset_id"] == "AST-C101-PROJ")["score"]
        self.assertGreater(mid, before)
        after = maintenance.mark_inspected(self.conn, "AST-C101-PROJ", "Good", "tech", self.NOW)["score"]
        self.assertLess(after, mid)
    def test_persisted_assessments(self):
        maintenance.assess_all(self.conn, self.NOW); self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM risk_assessments").fetchone()[0], self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
if __name__ == "__main__": unittest.main()
