import json
from .util import iso

def log(conn, actor, event_type, entity_type=None, entity_id=None, detail=None, now=None):
    """Append an audit event. Call inside the caller's transaction so it commits atomically."""
    conn.execute("INSERT INTO audit_events(ts,actor,event_type,entity_type,entity_id,detail_json) VALUES(?,?,?,?,?,?)",
                 (iso(now), actor, event_type, entity_type, entity_id, json.dumps(detail or {}, default=str)))

def list_events(conn, entity_type=None, entity_id=None, limit=100):
    q, a = "SELECT * FROM audit_events WHERE 1=1", []
    if entity_type: q += " AND entity_type=?"; a.append(entity_type)
    if entity_id:   q += " AND entity_id=?";   a.append(entity_id)
    q += " ORDER BY id DESC LIMIT ?"; a.append(max(1, min(int(limit), 500)))
    out = []
    for r in conn.execute(q, a).fetchall():
        d = dict(r); d["detail"] = json.loads(d.pop("detail_json") or "{}"); out.append(d)
    return out
