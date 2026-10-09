"""Deterministic, testable language rules: classification, entity extraction, priority.
No external model is used. Every output carries the evidence (matched terms) that produced it."""
import re

ROOM_RE = re.compile(r"\b([A-Za-z])-?(\d{3})\b")  # any letter+3 digits; unknown rooms are rejected by verification. No space variant: "a 100 students" must not become room A100
EQUIPMENT = {  # canonical name -> regex (word-boundary safe: 'ac' must not match 'back')
    "Projector":   r"\bprojectors?\b",
    "Smart Board": r"\bsmart ?boards?\b|\binteractive (?:board|panel)s?\b",
    "Computer":    r"\bcomputers?\b|\bpcs?\b|\bdesktops?\b|\bworkstations?\b",
    "Microphone":  r"\bmicrophones?\b|\bmics?\b|\bpa system\b",
    "AC":          r"\bac\b|\bair ?conditioner\b|\bair ?conditioning\b|\bcooling\b",
    "Wi-Fi":       r"\bwi-?fi\b|\binternet\b|\bnetwork\b",
    "Lighting":    r"\blights?\b|\blighting\b|\btube ?lights?\b",
}
INVENTORY_EQUIPMENT = ("Projector", "Smart Board", "Computer", "Microphone", "AC")
FAILURE = (r"stopped working|not working|isn'?t working|doesn'?t work|won'?t (?:turn|start|work|connect)|"
           r"not (?:turning|switching) on|broken|broke|failed|failure|dead|malfunction\w*|faulty|damaged|"
           r"flicker\w*|not cooling|too hot|no signal|not responding|unresponsive|down\b|out of order|not displaying")
SAFETY = r"\bfire\b|\bsmoke\b|\bsparks?\b|\bsparking\b|electric(?:al)? shock|gas leak|\bunsafe\b|\binjur\w+|\bemergency\b|short[- ]circuit|exposed wires?"
ENERGY = (r"energy|electricity|power consumption|wastage|wasting|left (?:on|running)|lights? (?:are |is )?(?:on|left on)|"
          r"running (?:empty|all night|overnight)|overconsum\w+|high (?:power|energy) (?:use|usage|bill)")
FACILITY = r"\bleak\w*|\bdoor\b|\bwindow\b|\bchairs?\b|\bdesks?\b|\btoilet\b|\bwashroom\b|\bcleaning\b|\bplumbing\b|\bceiling\b|\bwater\b"
ROOM_REQ = (r"need (?:a|an|another)? ?(?:room|classroom|hall)|find (?:me )?(?:a|an)? ?(?:room|classroom|hall)|"
            r"available (?:room|classroom|hall)s?|book (?:a|an)? ?(?:room|classroom|hall)|(?:room|classroom|hall) for\b")
INFO = r"^(?:how|where|when|what|which|who|is there|are there|can i|do you)\b|\?\s*$"
EVENTS = ["presentation", "exam", "lecture", "class", "seminar", "workshop", "meeting", "viva", "test", "defense", "defence"]
URGENT = r"\burgent\w*|\bimmediately\b|\basap\b|\bright now\b|\bcritical\b"

TEAMS = {"Equipment Failure": "AV & IT Support", "Facilities Issue": "Facilities & Electrical",
         "Safety": "Campus Security", "Energy": "Energy Management",
         "Classroom Request": "Academic Operations", "Information": "Campus Help Desk", "Other": "Campus Help Desk"}

def _find(pattern, text):
    return [m.group(0) for m in re.finditer(pattern, text, re.I)]

def extract(text):
    """Structured extraction. Missing values are None (never guessed)."""
    t = text
    m = ROOM_RE.search(t)
    room = f"{m.group(1).upper()}{m.group(2)}" if m else None
    equip = [n for n, p in EQUIPMENT.items() if re.search(p, t, re.I)]
    people = None
    pm = re.search(r"\b(\d{1,4})\s*(?:students|people|persons|attendees|participants|staff|faculty|members|seats|learners)\b", t, re.I) \
        or re.search(r"\b(?:class|group|batch) of (\d{1,4})\b", t, re.I)
    if pm: people = int(pm.group(1))
    time24 = None
    tm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t, re.I)
    if tm and 1 <= int(tm.group(1)) <= 12:
        h = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
        time24 = f"{h:02d}:{tm.group(2) or '00'}"
    else:
        tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
        if tm: time24 = f"{int(tm.group(1)):02d}:{tm.group(2)}"
        elif re.search(r"\bnoon\b", t, re.I): time24 = "12:00"
    event = next((e for e in EVENTS if re.search(rf"\b{e}s?\b", t, re.I)), None)
    return {"room": room, "equipment": equip, "affected_people": people, "event": event,
            "deadline_time": time24, "failure_terms": _find(FAILURE, t), "urgent_terms": _find(URGENT, t)}

def classify(text, ex=None):
    """Rule-scored category. 'confidence' is a heuristic signal strength, NOT a measured accuracy."""
    ex = ex or extract(text)
    s = {}
    safety = _find(SAFETY, text)
    if safety: s["Safety"] = (10, safety)
    fail = ex["failure_terms"]
    eq = [e for e in ex["equipment"]]
    if fail and eq: s["Equipment Failure"] = (4 + len(fail) + len(eq), fail + eq)
    energy = _find(ENERGY, text)
    if energy: s["Energy"] = (3 + len(energy), energy)
    fac = _find(FACILITY, text)
    if fac and (fail or "leak" in text.lower()): s["Facilities Issue"] = (3 + len(fac), fac + fail)
    req = _find(ROOM_REQ, text)
    if req and not fail: s["Classroom Request"] = (4 + len(req), req)
    if re.search(INFO, text.strip(), re.I): s["Information"] = (2, ["question form"])
    if fail and not s: s["Equipment Failure"] = (2, fail)  # generic breakage with no recognised equipment
    if not s: return {"category": "Other", "confidence": 0.2, "evidence": [], "scores": {}}
    ranked = sorted(s.items(), key=lambda kv: -kv[1][0])
    cat, (score, ev) = ranked[0]
    conf = min(0.95, 0.45 + 0.1 * len(set(x.lower() for x in ev)))
    if len(ranked) > 1 and ranked[1][1][0] >= score - 1: conf -= 0.15
    return {"category": cat, "confidence": round(max(conf, 0.2), 2),
            "evidence": sorted(set(x.lower() for x in ev)), "scores": {k: v[0] for k, v in ranked}}

PRIORITY_RULES = [  # documented in the UI and README; applied by priority()
    ("Safety keyword present", "+100 (forces P1)"),
    ("Equipment failure category", "+15"),
    ("Facilities issue category", "+15"),
    ("Scheduled event mentioned (presentation/exam/lecture/...)", "+20"),
    ("Specific deadline time mentioned", "+10"),
    ("Urgency wording (urgent/immediately/asap)", "+20"),
    ("Affected people >=100 / 40-99 / 10-39", "+25 / +15 / +8"),
    ("Room unknown", "+0 (flagged as missing information)"),
]
THRESHOLDS = [(70, "P1", "Critical"), (50, "P2", "High"), (30, "P3", "Medium"), (15, "P4", "Low"), (0, "P5", "Minimal")]
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72, "P5": 168}

def level_for(score):
    for floor, p, label in THRESHOLDS:
        if score >= floor: return p, label

def priority(category, ex):
    f = []
    if category == "Safety": f.append({"rule": "Safety keyword present", "points": 100})
    if category == "Equipment Failure": f.append({"rule": "Equipment failure category", "points": 15})
    if category == "Facilities Issue": f.append({"rule": "Facilities issue category", "points": 15})
    if ex.get("event"): f.append({"rule": f"Scheduled event: {ex['event']}", "points": 20})
    if ex.get("deadline_time"): f.append({"rule": f"Deadline time {ex['deadline_time']}", "points": 10})
    if ex.get("urgent_terms"): f.append({"rule": "Urgency wording: " + ", ".join(ex["urgent_terms"]), "points": 20})
    n = ex.get("affected_people")
    if n is not None:
        if n >= 100: f.append({"rule": f"{n} people affected (>=100)", "points": 25})
        elif n >= 40: f.append({"rule": f"{n} people affected (40-99)", "points": 15})
        elif n >= 10: f.append({"rule": f"{n} people affected (10-39)", "points": 8})
    score = sum(x["points"] for x in f)
    p, label = level_for(score)
    return {"score": score, "priority": p, "label": label, "factors": f, "sla_hours": SLA_HOURS[p]}
