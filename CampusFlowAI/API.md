# API reference (base `http://127.0.0.1:8000`)

JSON in/out. Errors: `{"error":{"code","message","details"}}` with 4xx/5xx and no stack traces.
Demo role header: `X-CF-Role: student|faculty|maintenance|admin` (default `student`); optional `X-CF-User`. **Not real authentication.**

| Method & path | Role | Purpose |
|---|---|---|
| GET `/api/health` | any | status, version, incident count |
| GET `/api/dashboard` | any | KPIs, top incidents/risks/anomalies, recent audit, provenance |
| GET `/api/analytics` | any | counts by category/priority/status, resolution hours, utilisation, energy, risk distribution |
| GET `/api/rules` | any | priority/energy/maintenance rules, thresholds, SLA, allowed transitions, teams |
| POST `/api/incidents` `{report}` | any | run pipeline, create ticket (201). 422 `validation_error` / `not_an_incident` / `verification_failed`. Duplicate within 120 s returns the existing ticket |
| GET `/api/incidents?status&priority&category&q&overdue&limit` | any | list/search |
| GET `/api/incidents/{CF-0006}` | any | detail with extraction, recommendation, verification, pipeline trace, history, audit |
| POST `/api/incidents/{id}/status` `{status,note}` | maintenance, admin | transitions: Open->In Progress/Resolved; In Progress->Open/Resolved; Resolved->Closed/In Progress; Closed final (409 otherwise) |
| POST `/api/incidents/{id}/assign` `{team,assignee}` | maintenance, admin | assignment (audited) |
| GET `/api/rooms` | any | rooms + today's utilisation |
| GET `/api/rooms/search?capacity&equipment=A,B&date&start&end&building&near` | any | feasible rooms ranked + rejected with reasons |
| GET `/api/bookings` / POST `/api/bookings` `{room_id,date,start,end,title,attendees?,incident_id?}` | POST: faculty, maintenance, admin | confirmed bookings; POST is transactional, 409 on conflict/full/maintenance room |
| GET `/api/energy` | any | per-building 24 h series vs baseline, anomaly events, rules (simulated) |
| GET `/api/maintenance/risks` | any | risk scores with factors and actions |
| POST `/api/assets/{id}/inspect` `{condition?}` | maintenance, admin | record inspection, recompute risk |
| POST `/api/copilot` `{query}` / GET `/api/copilot/examples` | any | answer + intent + sources + `found` flag |
| GET `/api/audit?entity_type&entity_id&limit` | any | audit events |
| GET `/api/export/{incidents\|energy_anomalies\|maintenance_risk}.csv` | any | CSV download |
| POST `/api/demo/reset` `{"confirm":"RESET"}` | admin | deletes ALL data and reseeds sample data |
