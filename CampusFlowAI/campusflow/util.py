import re
from datetime import datetime

def now_dt():
    return datetime.now().replace(microsecond=0)

def iso(dt=None):
    return (dt or now_dt()).strftime("%Y-%m-%dT%H:%M:%S")

def parse_iso(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
def valid_hhmm(s):
    return isinstance(s, str) and bool(_HHMM.match(s))

def to_min(hhmm):
    h, m = hhmm.split(":"); return int(h) * 60 + int(m)

def from_min(m):
    m = max(0, min(m, 23 * 60 + 59)); return f"{m // 60:02d}:{m % 60:02d}"

def valid_date(s):
    try: datetime.strptime(s, "%Y-%m-%d"); return True
    except Exception: return False

def clean_text(s, maxlen=1000):
    """Strip control characters and collapse whitespace. Output is escaped again at render time."""
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(s))
    return re.sub(r"\s+", " ", s).strip()[:maxlen]
