import http.client, json, os, tempfile, threading, unittest
from tests.helpers import SCENARIO

class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(cls.path)
        os.environ["CAMPUSFLOW_DB"] = cls.path
        from campusflow import server
        cls.srv = server.make_server("127.0.0.1", 0); cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True); cls.t.start()
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        for ext in ("", "-wal", "-shm"):
            try: os.unlink(cls.path + ext)
            except OSError: pass
    def call(self, method, path, body=None, role="student", raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"X-CF-Role": role}; data = raw
        if body is not None: data = json.dumps(body); h["Content-Type"] = "application/json"
        c.request(method, path, data, h); r = c.getresponse(); b = r.read(); c.close()
        try: return r.status, json.loads(b)
        except Exception: return r.status, b

    def test_01_health_and_static(self):
        s, b = self.call("GET", "/api/health"); self.assertEqual((s, b["status"]), (200, "ok"))
        s, b = self.call("GET", "/"); self.assertEqual(s, 200); self.assertIn(b"CampusFlow", b)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/../campusflow/db.py")[0], 404)       # path traversal blocked
        self.assertEqual(self.call("GET", "/%2e%2e/campusflow/db.py")[0], 404)
    def test_02_dashboard_has_provenance(self):
        s, b = self.call("GET", "/api/dashboard"); self.assertEqual(s, 200)
        self.assertIn("kpis", b); self.assertIn("SIMULATED", b["provenance"]["energy"])
    def test_03_core_workflow_over_http(self):
        s, b = self.call("POST", "/api/incidents", {"report": SCENARIO}); self.assertEqual(s, 201, b)
        iid = b["id"]; self.assertEqual((b["priority"], b["recommendation"]["top"]["room"]), ("P2", "A101"))
        s, g = self.call("GET", f"/api/incidents/{iid}"); self.assertEqual((s, g["room_id"]), (200, "B204"))
        s, l = self.call("GET", "/api/incidents?q=B204"); self.assertIn(iid, [i["id"] for i in l["items"]])
        # students cannot change status; maintenance can
        s, e = self.call("POST", f"/api/incidents/{iid}/status", {"status": "In Progress"}, role="student"); self.assertEqual((s, e["error"]["code"]), (403, "forbidden"))
        s, e = self.call("POST", f"/api/incidents/{iid}/status", {"status": "In Progress", "note": "on it"}, role="maintenance"); self.assertEqual((s, e["status"]), (200, "In Progress"))
        s, e = self.call("POST", f"/api/incidents/{iid}/status", {"status": "Closed"}, role="maintenance"); self.assertEqual(s, 409)
        s, a = self.call("GET", f"/api/audit?entity_type=incident&entity_id={iid}"); self.assertGreaterEqual(len(a["items"]), 3)
        s, d = self.call("GET", "/api/dashboard"); self.assertGreaterEqual(d["kpis"]["high_priority_active"], 2)
    def test_04_booking_roles_and_conflicts(self):
        body = {"room_id": "A101", "date": "2031-01-05", "start": "14:00", "end": "15:00", "title": "Presentation", "attendees": 60}
        self.assertEqual(self.call("POST", "/api/bookings", body, role="student")[0], 403)
        self.assertEqual(self.call("POST", "/api/bookings", body, role="faculty")[0], 201)
        s, e = self.call("POST", "/api/bookings", body, role="faculty"); self.assertEqual((s, e["error"]["code"]), (409, "conflict"))
    def test_05_invalid_requests(self):
        self.assertEqual(self.call("POST", "/api/incidents", {"report": ""})[0], 422)
        self.assertEqual(self.call("POST", "/api/incidents", {})[0], 422)
        self.assertEqual(self.call("POST", "/api/incidents", raw="not json")[0], 422)
        self.assertEqual(self.call("POST", "/api/incidents", raw="[1,2]")[0], 422)
        self.assertEqual(self.call("POST", "/api/incidents", raw="x" * 30000)[0], 413)
        self.assertEqual(self.call("GET", "/api/rooms/search?capacity=abc")[0], 422)
        self.assertEqual(self.call("GET", "/api/rooms/search?capacity=50&equipment=Flamethrower")[0], 422)
        self.assertEqual(self.call("GET", "/api/incidents?status=Bogus")[0], 422)
        self.assertEqual(self.call("GET", "/api/incidents/CF-9999")[0], 404)
        self.assertEqual(self.call("GET", "/api/health", role="hacker")[0], 422)
        s, e = self.call("POST", "/api/incidents", {"report": "The projector in Z999 stopped working, presentation at 2 pm for 60 students"})
        self.assertEqual((s, e["error"]["code"]), (422, "verification_failed"))
        self.assertNotIn("Traceback", json.dumps(e))
    def test_06_room_search_energy_maintenance_copilot(self):
        s, r = self.call("GET", "/api/rooms/search?capacity=60&equipment=Projector&date=2031-01-05&start=14:00&end=15:00"); self.assertEqual(s, 200); self.assertTrue(r["candidates"])
        s, e = self.call("GET", "/api/energy"); self.assertTrue(e["simulated"]); self.assertTrue(e["anomalies"])
        s, m = self.call("GET", "/api/maintenance/risks"); self.assertIn("not a trained", m["label"]); self.assertTrue(m["items"][0]["factors"])
        s, c = self.call("POST", "/api/copilot", {"query": "are there energy anomalies?"}); self.assertTrue(c["found"]); self.assertIn("SIMULATED", c["answer"])
        s, c = self.call("POST", "/api/copilot", {"query": "recipe for pasta"}); self.assertFalse(c["found"])
        self.assertEqual(self.call("POST", "/api/assets/AST-B204-PROJ/inspect", {"condition": "Good"}, role="student")[0], 403)
        s, i = self.call("POST", "/api/assets/AST-B204-PROJ/inspect", {"condition": "Good"}, role="maintenance"); self.assertEqual(s, 200); self.assertLess(i["score"], 83)
    def test_07_exports_and_rules(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port); c.request("GET", "/api/export/incidents.csv"); r = c.getresponse(); b = r.read().decode("utf-8-sig")
        self.assertEqual(r.status, 200); self.assertTrue(b.startswith("id,created_at")); c.close()
        self.assertEqual(self.call("GET", "/api/export/secrets.csv")[0], 404)
        s, r = self.call("GET", "/api/rules"); self.assertIn("priority_rules", r)
    def test_99_reset_requires_admin_and_confirmation(self):
        self.assertEqual(self.call("POST", "/api/demo/reset", {"confirm": "RESET"}, role="faculty")[0], 403)
        self.assertEqual(self.call("POST", "/api/demo/reset", {}, role="admin")[0], 422)
        n = self.call("GET", "/api/incidents")[1]["items"]; self.assertGreater(len(n), 5)             # nothing deleted yet
        self.assertEqual(self.call("POST", "/api/demo/reset", {"confirm": "RESET"}, role="admin")[0], 200)
        self.assertEqual(len(self.call("GET", "/api/incidents")[1]["items"]), 5)
if __name__ == "__main__": unittest.main()
