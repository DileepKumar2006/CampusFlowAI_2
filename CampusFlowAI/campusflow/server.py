"""HTTP server (stdlib). REST API under /api plus static frontend. Local prototype only."""
import json, logging, mimetypes, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from . import __version__, analytics, audit, copilot, db, energy, incidents, maintenance, nlp, rooms, seed
from .errors import AppError, bad, forbidden, not_found
from .util import now_dt, iso, clean_text

STATIC = db.ROOT / "static"
ROLES = {"student", "faculty", "maintenance", "admin"}
PERMS = {"status": {"maintenance", "admin"}, "assign": {"maintenance", "admin"}, "booking": {"faculty", "maintenance", "admin"},
         "inspect": {"maintenance", "admin"}, "reset": {"admin"}}
MAX_BODY = 20_000
log = logging.getLogger("campusflow")
_init_lock = threading.Lock()

def setup_logging():
    (db.ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(db.ROOT / "logs" / "campusflow.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)])

def bootstrap():
    with _init_lock:
        c = db.connect()
        try: db.init_db(c); seed.ensure_seeded(c, now_dt())
        finally: c.close()

def need(role, action):
    if role not in PERMS[action]:
        raise forbidden(f"Role '{role}' cannot perform this action. Allowed roles: {', '.join(sorted(PERMS[action]))}. (Local demo roles - not real authentication.)")

def _int(v, name):
    if v in (None, ""): return None
    try: return int(v)
    except ValueError: raise bad(f"{name} must be an integer")

def route(conn, method, path, qs, body, role, actor):
    now = now_dt(); g = lambda k: (qs.get(k) or [None])[0]
    p = path.rstrip("/") or "/"
    if p == "/api/health" and method == "GET":
        n = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        return {"status": "ok", "version": __version__, "time": iso(now), "database": "sqlite", "incidents": n, "llm": "not used (deterministic rules)"}
    if p == "/api/dashboard" and method == "GET": return analytics.dashboard(conn, now)
    if p == "/api/analytics" and method == "GET": return analytics.analytics(conn, now)
    if p == "/api/rules" and method == "GET":
        return {"priority_rules": nlp.PRIORITY_RULES, "thresholds": [{"min_score": a, "priority": b, "label": c} for a, b, c in nlp.THRESHOLDS], "sla_hours": nlp.SLA_HOURS,
                "energy_rules": energy.RULES, "maintenance_rules": maintenance.RULES, "transitions": {k: sorted(v) for k, v in incidents.TRANSITIONS.items()},
                "teams": incidents.TEAMS, "trace_note": incidents.TRACE_NOTE}
    if p == "/api/incidents":
        if method == "POST":
            return 201, incidents.create_incident(conn, (body or {}).get("report"), actor, now)
        ov = g("overdue"); ov = None if ov is None else ov.lower() in ("1", "true", "yes")
        return {"items": incidents.list_incidents(conn, g("status"), g("priority"), g("category"), g("q"), ov, _int(g("limit"), "limit") or 200, now)}
    m = re.fullmatch(r"/api/incidents/(CF-\d{4,6})(?:/(status|assign))?", p)
    if m:
        iid, sub = m.groups()
        if not sub and method == "GET": return incidents.get_incident(conn, iid, now)
        if sub == "status" and method == "POST":
            need(role, "status"); b = body or {}; return incidents.change_status(conn, iid, b.get("status"), b.get("note"), actor, now)
        if sub == "assign" and method == "POST":
            need(role, "assign"); b = body or {}; return incidents.assign(conn, iid, b.get("team"), b.get("assignee"), actor, now)
    if p == "/api/rooms" and method == "GET":
        return {"items": db.rows(conn.execute("SELECT * FROM rooms ORDER BY id")), "utilization": rooms.utilization(conn, now.strftime("%Y-%m-%d"))}
    if p == "/api/rooms/search" and method == "GET":
        eq = [e for e in (g("equipment") or "").split(",") if e]
        for e in eq:
            if e not in nlp.INVENTORY_EQUIPMENT: raise bad(f"Unknown equipment '{e}'. Valid: {', '.join(nlp.INVENTORY_EQUIPMENT)}")
        return rooms.search(conn, _int(g("capacity"), "capacity"), eq, g("date"), g("start"), g("end"), g("building"), g("near"), None)
    if p == "/api/bookings":
        if method == "GET":
            return {"items": db.rows(conn.execute("SELECT * FROM bookings WHERE status='Confirmed' ORDER BY date DESC, start LIMIT 200"))}
        if method == "POST":
            need(role, "booking"); b = body or {}
            return 201, rooms.create_booking(conn, b.get("room_id"), b.get("date"), b.get("start"), b.get("end"), b.get("title"), actor, b.get("attendees"), b.get("incident_id"))
    if p == "/api/energy" and method == "GET": return energy.summary(conn, now)
    if p == "/api/maintenance/risks" and method == "GET":
        r = maintenance.assess_all(conn, now); return {"items": r, "rules": maintenance.RULES, "label": "Heuristic risk score - not a trained predictive model"}
    m = re.fullmatch(r"/api/assets/([A-Z0-9-]+)/inspect", p)
    if m and method == "POST":
        need(role, "inspect"); return maintenance.mark_inspected(conn, m.group(1), (body or {}).get("condition"), actor, now)
    if p == "/api/copilot" and method == "POST": return copilot.ask(conn, (body or {}).get("query"), now)
    if p == "/api/copilot/examples" and method == "GET": return {"examples": copilot.HELP}
    if p == "/api/audit" and method == "GET": return {"items": audit.list_events(conn, g("entity_type"), g("entity_id"), _int(g("limit"), "limit") or 100)}
    if p == "/api/demo/reset" and method == "POST":
        need(role, "reset")
        if (body or {}).get("confirm") != "RESET": raise bad('Reset deletes ALL records, including tickets and bookings you created. Send {"confirm":"RESET"} to proceed.')
        seed.seed(conn, now)
        return {"status": "reset", "message": "Database reset to labelled sample seed data."}
    raise not_found("Unknown endpoint")

class Handler(BaseHTTPRequestHandler):
    server_version = "CampusFlow/" + __version__
    def log_message(self, fmt, *a): log.info("%s %s", self.address_string(), fmt % a)
    def _send(self, code, payload, ctype="application/json; charset=utf-8", extra=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"): self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)
    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file(): return self._send(404, {"error": {"code": "not_found", "message": "Not found"}})
        self._send(200, f.read_bytes(), (mimetypes.guess_type(str(f))[0] or "application/octet-stream") + ("; charset=utf-8" if f.suffix in (".html", ".js", ".css") else ""))
    def _api(self, method):
        u = urlparse(self.path); qs = parse_qs(u.query); body = None
        role = (self.headers.get("X-CF-Role") or "student").lower(); actor = clean_text(self.headers.get("X-CF-User") or role, 40) or role
        try:
            if role not in ROLES: raise bad("Unknown role")
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY: raise AppError(413, "too_large", "Request body too large")
                raw = self.rfile.read(n) if n else b""
                try: body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception: raise bad("Body must be valid JSON")
                if not isinstance(body, dict): raise bad("Body must be a JSON object")
            if u.path.startswith("/api/export/") and method == "GET":
                c = db.connect()
                try: txt = analytics.csv_export(c, u.path.rsplit("/", 1)[1].removesuffix(".csv"), now_dt())
                finally: c.close()
                if txt is None: raise not_found("Unknown export")
                return self._send(200, txt.encode("utf-8-sig"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{u.path.rsplit("/", 1)[1]}"'})
            c = db.connect()
            try: res = route(c, method, u.path, qs, body, role, actor)
            finally: c.close()
            code, payload = res if isinstance(res, tuple) else (200, res)
            self._send(code, payload)
        except AppError as e:
            if e.status >= 500: log.error("AppError %s", e.message)
            self._send(e.status, {"error": {"code": e.code, "message": e.message, "details": e.details}})
        except Exception:
            log.exception("Unhandled error on %s %s", method, self.path)  # stack trace goes to the log file only
            self._send(500, {"error": {"code": "internal_error", "message": "Something went wrong on the server. The error was logged; please retry."}})
    def do_GET(self):
        if self.path.startswith("/api/"): return self._api("GET")
        self._static(urlparse(self.path).path)
    def do_POST(self):
        if self.path.startswith("/api/"): return self._api("POST")
        self._send(405, {"error": {"code": "method_not_allowed", "message": "POST only supported under /api"}})

def make_server(host="127.0.0.1", port=8000):
    bootstrap(); return ThreadingHTTPServer((host, port), Handler)

def main():
    import argparse
    ap = argparse.ArgumentParser(description="CampusFlow AI local server")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1")); ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    a = ap.parse_args(); setup_logging()
    srv = make_server(a.host, a.port)
    log.info("CampusFlow AI running at http://%s:%s  (Ctrl+C to stop)  DB: %s", a.host, a.port, db.db_path())
    try: srv.serve_forever()
    except KeyboardInterrupt: log.info("Shutting down")
    finally: srv.server_close()

if __name__ == "__main__": main()
