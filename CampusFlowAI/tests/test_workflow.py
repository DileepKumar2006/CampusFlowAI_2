import json, os, unittest
from tests.helpers import *
from campusflow import incidents, audit, db, analytics, copilot
from campusflow.errors import AppError

class Workflow(DBCase):
    def create(self, text=SCENARIO, who="student1"): return incidents.create_incident(self.conn, text, who, self.NOW)

    def test_core_scenario_end_to_end(self):
        r = self.create()
        self.assertTrue(r["id"].startswith("CF-")); self.assertEqual(r["id"], "CF-0006")   # 5 seeded tickets precede it
        self.assertEqual((r["category"], r["priority"], r["room_id"], r["affected_people"], r["deadline"]), ("Equipment Failure", "P2", "B204", 60, "14:00"))
        self.assertEqual(r["assigned_team"], "AV & IT Support"); self.assertEqual(r["status"], "Open")
        top = r["recommendation"]["top"]; self.assertEqual(top["room"], "A101")
        self.assertIn("SUGGESTION ONLY", r["recommendation"]["status"])
        self.assertEqual(r["verification"]["status"], "PASS")
        self.assertTrue(any(c["name"] == "persisted" and c["status"] == "pass" for c in r["verification"]["checks"]))
        self.assertEqual([s["stage"][0] for s in r["pipeline"]], list("123456789"))
        self.assertTrue(any("No room has been reserved" in x for x in r["not_executed"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bookings WHERE source='user'").fetchone()[0], 0)   # recommendation != booking

    def test_recommendation_obeys_constraints_independently(self):
        top = self.create()["recommendation"]["top"]
        row = self.conn.execute("SELECT * FROM rooms WHERE id=?", (top["room"],)).fetchone()
        self.assertGreaterEqual(row["capacity"], 60); self.assertIn("Projector", row["equipment"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bookings WHERE room_id=? AND start<'15:00' AND end>'14:00'", (top["room"],)).fetchone()[0], 0)

    def test_no_alternative_is_never_invented(self):
        r = self.create("Projector in B204 is broken, exam at 2 pm for 500 students")
        self.assertIsNone(r["recommendation"]["top"]); self.assertIn("No alternative room", r["next_action"])

    def test_unknown_room_fails_verification_and_creates_nothing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        with self.assertRaises(AppError) as cm: self.create("The projector in Z999 stopped working, presentation at 2 pm for 60 students")
        self.assertEqual((cm.exception.status, cm.exception.code), (422, "verification_failed"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], before)

    def test_room_pattern_not_in_inventory_is_rejected(self):
        with self.assertRaises(AppError) as cm: self.create("The projector in E999 stopped working, presentation at 2 pm for 60 students")
        self.assertEqual(cm.exception.code, "verification_failed")
        self.assertTrue(self.conn.execute("SELECT 1 FROM audit_events WHERE event_type='incident.rejected'").fetchone())

    def test_missing_room_creates_flagged_ticket_without_guessing(self):
        r = self.create("The projector has stopped working, presentation at 2 pm for 60 students")
        self.assertIsNone(r["room_id"]); self.assertEqual(r["verification"]["status"], "PASS_WITH_WARNINGS")
        self.assertIsNone(r["recommendation"]); self.assertIn("room / location", r["next_action"])

    def test_invalid_inputs(self):
        for bad in ("", "   ", "short", 123, None, "!!!!!!!!!!!!!!!"):
            with self.assertRaises(AppError): incidents.create_incident(self.conn, bad, "u", self.NOW)

    def test_non_incidents_redirected(self):
        for t, code in (("I need a room for 50 students tomorrow", "not_an_incident"), ("where is the library?", "not_an_incident"), ("hello there friend", "not_an_incident")):
            with self.assertRaises(AppError) as cm: self.create(t)
            self.assertEqual(cm.exception.code, code)

    def test_safety_is_p1(self):
        r = self.create("Sparks and smoke coming from the socket in A101"); self.assertEqual((r["priority"], r["category"]), ("P1", "Safety")); self.assertEqual(r["assigned_team"], "Campus Security")

    def test_duplicate_submission_protection(self):
        a = self.create(); b = self.create()
        self.assertEqual(a["id"], b["id"]); self.assertTrue(b.get("duplicate"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM incidents WHERE source='user'").fetchone()[0], 1)

    def test_status_transitions_and_audit_trail(self):
        iid = self.create()["id"]
        incidents.change_status(self.conn, iid, "In Progress", "tech on way", "tech1", self.NOW)
        r = incidents.change_status(self.conn, iid, "Resolved", "lamp replaced", "tech1", self.NOW)
        self.assertIsNotNone(r["resolved_at"])
        r = incidents.change_status(self.conn, iid, "Closed", "", "admin1", self.NOW)
        self.assertEqual([(h["from_status"], h["to_status"]) for h in r["history"]], [(None, "Open"), ("Open", "In Progress"), ("In Progress", "Resolved"), ("Resolved", "Closed")])
        kinds = [e["event_type"] for e in r["audit"]]
        self.assertEqual(kinds.count("incident.status_changed"), 3); self.assertIn("incident.created", kinds); self.assertIn("incident.verified", kinds)

    def test_invalid_transitions_rejected(self):
        iid = self.create()["id"]
        for bad in ("Closed", "Open", "Done", ""):
            with self.assertRaises(AppError): incidents.change_status(self.conn, iid, bad, "", "t", self.NOW)
        incidents.change_status(self.conn, iid, "Resolved", "", "t", self.NOW); incidents.change_status(self.conn, iid, "Closed", "", "t", self.NOW)
        with self.assertRaises(AppError) as cm: incidents.change_status(self.conn, iid, "Open", "", "t", self.NOW)
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(AppError) as cm: incidents.change_status(self.conn, "CF-9999", "Resolved", "", "t", self.NOW)
        self.assertEqual(cm.exception.status, 404)

    def test_assignment_validated_and_audited(self):
        iid = self.create()["id"]
        r = incidents.assign(self.conn, iid, "Facilities & Electrical", "Ravi", "admin", self.NOW)
        self.assertEqual((r["assigned_team"], r["assignee"]), ("Facilities & Electrical", "Ravi"))
        with self.assertRaises(AppError): incidents.assign(self.conn, iid, "Made Up Team", "x", "admin", self.NOW)
        self.assertTrue(any(e["event_type"] == "incident.assigned" for e in incidents.get_incident(self.conn, iid)["audit"]))

    def test_filters_search_and_overdue(self):
        self.create()
        self.assertTrue(all(i["status"] == "Open" for i in incidents.list_incidents(self.conn, status="Open", now=self.NOW)))
        self.assertEqual([i["id"] for i in incidents.list_incidents(self.conn, q="B204", now=self.NOW)], ["CF-0006"])
        self.assertEqual([i["id"] for i in incidents.list_incidents(self.conn, overdue=True, now=self.NOW)], ["CF-0001"])
        with self.assertRaises(AppError): incidents.list_incidents(self.conn, status="Bogus")
        self.assertEqual(incidents.list_incidents(self.conn, q="x'; DROP TABLE incidents;--", now=self.NOW), [])   # parameterised SQL
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 0)

    def test_xss_text_stored_inert_and_controls_stripped(self):
        r = self.create("Projector in B204 broken <script>alert(1)</script>\x00 presentation 2 pm for 60 students")
        self.assertNotIn("\x00", r["report_text"])   # raw text is stored; the UI escapes on render (tested in README checklist)

    def test_persistence_survives_reconnect(self):
        iid = self.create()["id"]; self.conn.close()
        c2 = db.connect()
        try: self.assertEqual(incidents.get_incident(c2, iid)["room_id"], "B204")
        finally: c2.close()
        self.conn = db.connect()

    def test_dashboard_and_analytics_reflect_new_ticket(self):
        k0 = analytics.dashboard(self.conn, self.NOW)["kpis"]; self.create(); k1 = analytics.dashboard(self.conn, self.NOW)["kpis"]
        self.assertEqual(k1["active_incidents"], k0["active_incidents"] + 1); self.assertEqual(k1["high_priority_active"], k0["high_priority_active"] + 1)
        a = analytics.analytics(self.conn, self.NOW); self.assertEqual(a["by_priority"]["P2"], 2); self.assertEqual(a["data_sources"]["user_tickets"], 1)
        self.assertEqual(a["resolution_hours_by_priority"]["P4"]["count"], 1)

    def test_csv_exports(self):
        self.create()
        for n in ("incidents", "energy_anomalies", "maintenance_risk"): self.assertGreater(len(analytics.csv_export(self.conn, n, self.NOW).splitlines()), 1)
        self.assertIsNone(analytics.csv_export(self.conn, "nope", self.NOW))

    def test_copilot_uses_same_data_and_admits_unknowns(self):
        iid = self.create()["id"]
        self.assertIn("CF-0006", copilot.ask(self.conn, "show high priority tickets", self.NOW)["answer"])
        self.assertIn("A101", copilot.ask(self.conn, "find a room for 60 students with a projector at 14:00", self.NOW)["answer"])
        self.assertNotIn("B204 cap", copilot.ask(self.conn, "find a room for 60 students with a projector at 14:00", self.NOW)["answer"])   # no contradiction with open ticket
        self.assertFalse(copilot.ask(self.conn, "status of CF-9999", self.NOW)["found"])
        self.assertFalse(copilot.ask(self.conn, "who won the cricket match?", self.NOW)["found"])
        self.assertFalse(copilot.ask(self.conn, "what is room Z999", self.NOW)["found"])
        self.assertIn("B204", copilot.ask(self.conn, "room B204 status", self.NOW)["answer"])
if __name__ == "__main__": unittest.main()
