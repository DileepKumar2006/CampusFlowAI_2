# CampusFlow AI

**Turn campus problems into verified, trackable operational actions.**
A smart-campus operations prototype for **PromptWars X - Error Zero**: classroom utilisation, energy wastage, delayed maintenance and hard-to-find campus information.

> Local hackathon prototype. Not production software (see *Limitations*).

## Quick start (Windows 10/11, PowerShell)

Requires **Python 3.9+** (developed and tested on 3.12). **No `pip install` is needed** - the app uses only the Python standard library.

```powershell
cd CampusFlowAI
py run.py
```

Open **http://127.0.0.1:8000**. The first start creates `data\campusflow.db` and loads clearly-labelled sample data. No API keys, no internet.

Options: `py run.py --port 8080`. Stop with `Ctrl+C`.
Run the tests: `.\run_tests.ps1` (or `py -m unittest discover -s tests -t . -v`). Tests use a temporary database.
If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\run_tests.ps1`.

## What it does

Core workflow (Report Incident page): a user types
*"The projector in B204 has stopped working. We have a presentation at 2 PM for 60 students."*
and the pipeline runs: **validate -> classify -> extract -> retrieve records -> prioritise -> recommend -> verify -> persist -> audit**.

Result for that report: Equipment Failure, room B204, Projector, 60 people, event *presentation*, deadline 14:00, **P2 High (score 60)**, routed to AV & IT Support. The room search checks every room in the database against capacity, equipment, availability, status and open fault tickets, and suggests **A101** (rejected rooms are listed with reasons, e.g. B202 booked 13:00-15:00, B201 too small). A ticket `CF-0006` is stored in SQLite, shows in the queue and dashboard, and has a status history and audit trail.

**Recommendation vs action.** The suggested room is only a *suggestion*. Reserving it is a separate, role-restricted, conflict-checked action that writes a booking. The app does **not** notify technicians, repair devices or send messages - the result panel states this explicitly.

| Module | Page | Notes |
|---|---|---|
| Command Center | `#/` | KPIs computed from the DB; provenance banner |
| Incident Intelligence | Report Incident, Operations Queue | search/filter, detail drawer, status + assignment, history, audit |
| Classroom Optimizer | `#/rooms` | hard constraints then ranking; real bookings with double-booking prevention |
| Energy Intelligence | `#/energy` | **simulated** hourly readings; baseline + anomaly rules |
| Predictive Maintenance | `#/maintenance` | **heuristic** risk score with factor breakdown; "record inspection" updates risk |
| Campus Copilot | `#/copilot` | rule-based Q&A over the same services; says "not found" when data is absent |
| Prompt & Audit Lab | `#/audit` | per-ticket decision trace, verification checks, rule docs, audit log |
| Analytics & Reports | `#/analytics` | counts, resolution time, utilisation, CSV exports |
| Demo Mode | `#/demo` | scenario loader; guarded reset (admin + typed `RESET`) |

## Decision rules (all deterministic and documented in-app at *Audit Lab*)

**Priority score** = Equipment failure +15 | Facilities issue +15 | Safety keyword +100 (forces P1) | scheduled event mentioned +20 | explicit deadline time +10 | urgency wording +20 | people affected >=100: +25, 40-99: +15, 10-39: +8.
Thresholds: **P1 >= 70, P2 >= 50, P3 >= 30, P4 >= 15, else P5**. SLA hours: P1 4, P2 8, P3 24, P4 72, P5 168 (a ticket is *overdue* when active past its SLA).

**Room feasibility (hard constraints):** capacity >= people; all required equipment; room status Available; no overlapping confirmed booking; no active fault ticket on the required equipment. **Ranking:** capacity fit 0-40 + equipment 30 + extra equipment 0-10 + same-building proximity 0/20.

**Energy:** baseline = median kWh for the same building and hour-of-day over earlier days; hour is anomalous at >=1.25x baseline (warning) or >=1.5x (critical); >=3 consecutive anomalous hours escalate to critical. Excess kWh is *an estimate versus baseline*, not a verified saving.

**Maintenance risk (0-100):** condition (Poor 30/Fair 12/Good 0) + age vs expected life (max 25) + incidents in last 90 days for that room+equipment (8 each, max 25) + days since inspection (>365: 20, >180: 12, >90: 5). High >= 60, Medium >= 35. Weights are judgement-based defaults - **not a trained or evaluated model**.

**Verification** (independent module) checks: required fields; room exists; equipment listed in the room inventory; priority re-computed from the rules and compared; the recommended room re-checked against capacity/equipment/availability/status; time-window assumptions; and, after commit, that the ticket and audit event can be read back. A failed check (e.g. unknown room `Z999`) means **no ticket is created**, an `incident.rejected` audit event is written, and the user gets the reason. Missing room/people produce a flagged ticket (never a guess).

## Architecture

```
static/ (HTML+CSS+JS SPA)  --fetch-->  campusflow/server.py (stdlib HTTP, REST /api, role + validation layer)
                                         |-- nlp.py        classify, extract, priority (pure functions)
                                         |-- incidents.py  pipeline orchestration, transitions, assignment
                                         |-- rooms.py      search/ranking, bookings (BEGIN IMMEDIATE transactions), utilisation
                                         |-- verify.py     independent verification
                                         |-- energy.py     baseline + anomaly detection (pure)
                                         |-- maintenance.py risk scoring (pure) + inspection
                                         |-- copilot.py    intent routing over the services above
                                         |-- analytics.py  dashboard, analytics, CSV
                                         |-- audit.py      append-only audit events
                                         '-- db.py/seed.py SQLite schema, transactions, labelled sample data
```
SQLite tables: `rooms, bookings, assets, incidents, status_history, energy_readings, risk_assessments, audit_events, meta`. Related writes (ticket + history + audit) share one transaction. All SQL is parameterised. Endpoint list: see `API.md`.

**Why stdlib instead of FastAPI?** The FastAPI project named in the brief was not among the uploaded files (only three Streamlit prototypes whose `engine.py` was identical, plus an unrelated R project). The build environment also had no network, so FastAPI could not be installed or tested. The stdlib server provides the same REST/SQLite architecture, needs zero installation on Windows, and everything here was actually executed and tested. Porting `route()` to FastAPI would be mechanical.

## Honest status

**Verified in this build:** 61 automated tests pass (`py -m unittest discover -s tests -t .`) covering classification/extraction, priority rules and thresholds, room hard constraints (full room, insufficient capacity, missing equipment, time conflicts, boundary times), booking conflicts, ticket creation/persistence, transitions, audit, energy detection, risk scoring, validation, role checks, reset guard, path traversal, CSV, and the full workflow over real HTTP. A real server process was started, a ticket created, the server killed and restarted, and the ticket + audit trail were still present.

**Not verified:** the browser UI was **not** launched or visually inspected (no browser available in the build environment). The JavaScript passes `node --check` and every element id it references exists, but expect to find cosmetic issues on first run. The app has not been run on Windows itself (it uses `pathlib` and standard modules only). Not tested on Python < 3.12.

**Seed/sample data:** room inventory comes from the original prototype CSV (B204 was given a projector, because the original CSV listed none, which contradicted the demo scenario; C103 is set to *Maintenance*). The timetable, assets, tickets CF-0001..0005 and all energy readings are **sample/simulated**, labelled as such in the UI. Seeded ticket priorities are hard-coded sample values, not pipeline output.

## Limitations

- Classification/extraction are keyword and regex rules: phrasing outside the rules (other languages, unusual wording) may be mis-handled. The "heuristic signal" shown is not an accuracy figure; no accuracy has been measured.
- A report naming any letter+3 digits (e.g. `H264`) is treated as a room reference and rejected if not in inventory. "B 204" with a space is not recognised.
- Assumes **today** and a **60-minute** duration when scheduling a replacement room (shown in the UI). Bookings are single-slot, no recurrence, no cancellation endpoint.
- Roles (student/faculty/maintenance/admin) are a header chosen in the UI. **This is not authentication** - anyone with network access to the port can call the API with any role. Bind stays on `127.0.0.1` by default; do not expose it.
- No notifications, no real IoT/meter integration, no trained ML model, no LLM. Savings are never claimed.
- Single-process SQLite; no migrations framework (schema is created with `CREATE TABLE IF NOT EXISTS`; delete `data\campusflow.db` to rebuild).

## Next steps
Real authentication and per-user permissions; notification integration (email/Teams) so "assign" can actually notify; ingest real BMS/meter data; booking cancellation and recurring timetables; evaluate rule accuracy on labelled historical tickets before considering an optional LLM extraction step behind the same verification layer; port to FastAPI/Postgres for deployment.

## Troubleshooting
- **`py` not recognised** - install Python from python.org (tick *Add to PATH*) or use `python run.py`.
- **Port already in use** - `py run.py --port 8080`.
- **Blank page / "API offline" badge** - make sure the terminal running `py run.py` is still open; hard-refresh (Ctrl+F5).
- **Want a clean demo state** - stop the server, delete `data\campusflow.db*`, start again; or use *Demo Mode -> Reset* as Administrator.
- **Database locked** - close other processes using the file; the server waits up to 15 s.
- **Errors in the UI** - server details are in `logs\campusflow.log` (stack traces are never sent to the browser).

## Cloud deployment (frontend + backend together)

The frontend in `static/` and the REST API in `campusflow/` are served from the **same origin**. You do not need a separate frontend deployment or a frontend API URL.

### Deploy on Render

1. Push this `CampusFlowAI` folder to a GitHub repository (the `render.yaml` file must be at the repository root).
2. In Render, choose **New → Blueprint**, connect the repository, and apply the `render.yaml` configuration.
3. The service builds with Python and starts using Render's assigned `$PORT`; open the generated `https://...onrender.com` URL.
4. Check `https://YOUR-SERVICE.onrender.com/api/health`. It should return JSON with `"status": "ok"`.

The Blueprint config uses a persistent disk mounted at `/var/data` so the SQLite database survives service restarts and deploys. Persistent disks require a paid Render service; if you switch to a free instance, remove the `disk` and `CAMPUSFLOW_DB` configuration and understand that local SQLite data may be lost on restart/redeploy.

**Important demo limitation:** the current role selector is not real authentication. Do not use this prototype for sensitive campus data or treat its role headers as secure authorization. Add real authentication and production security before public/real-world use.
