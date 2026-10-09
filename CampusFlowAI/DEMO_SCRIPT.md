# Judge demo script (about 5 minutes)

Start: `py run.py` then open http://127.0.0.1:8000 . Optional: *Demo Mode -> Reset* (Administrator) for a clean state.

**Opening line**
"Imagine a university where a broken projector, an overcrowded classroom, or abnormal energy consumption doesn't become another complaint waiting in someone's inbox. CampusFlow converts the problem into a verified, trackable operational action."

1. **Command Center** - point out KPIs (active, high-priority, overdue, utilisation, energy events, high-risk assets) and the provenance banner: energy is simulated, seed data is labelled, nothing is hard-coded.
2. **Report Incident** (role: Student) - click *Use demo scenario* -> *Analyze and create ticket*.
3. **Extracted details and priority** - room B204, Projector, 60 people, presentation, 14:00; **P2 High, score 60**, each rule's points listed; routed to AV & IT Support.
4. **Database-backed recommendation** - A101 suggested with reasons; open *Why other rooms were rejected* (B202 booked 13:00-15:00, B201 too small, D105 no projector, C103 under maintenance, C101 projector already has a fault ticket). Note the badge: **SUGGESTION - not booked**, and the *NOT executed* list (no technician notified, no room reserved).
5. **Ticket + status tracking** - switch role to **Faculty**, click *Reserve A101* (a real, conflict-checked booking; try again -> conflict). Switch to **Maintenance**, open the ticket in **Operations Queue**, move *In Progress* then *Resolved*, assign a team member. Show that a Student cannot change status.
6. **Energy anomaly** - Energy page: Block C sustained 5-hour draw (critical) and Block D short spike (warning), with observed vs baseline, estimated excess kWh, recommended action. Say clearly: simulated data, no savings claimed.
7. **Maintenance risk** - B204 projector scores High; expand to see the four factors; the new ticket just raised it. As Maintenance, *Record inspection* and watch the score drop.
8. **Campus Copilot** - ask "Show high priority tickets", "Status of ticket CF-0006", "Find a room for 60 students with a projector at 14:00" (note it does *not* offer B204), then "who won the cricket match?" -> honest "not found in records".
9. **Verification and audit** - Prompt & Audit Lab: enter CF-0006 -> input, extraction, classification, priority evidence, recommendation, verification checks (including *persisted*), audit events. Then try a report mentioning room `Z999` on Report Incident: verification fails, **no ticket is created**.
10. **Updated analytics** - Analytics page now includes the new ticket; export the incidents CSV.

**Closing**: problem -> rule-based, testable pipeline -> verified, persisted action. Be upfront on limitations: rules not ML, simulated energy data, demo roles are not authentication, UI built without browser verification in the build environment.
