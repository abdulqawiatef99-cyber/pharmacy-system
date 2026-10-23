from __future__ import annotations

import json
from typing import Any

from ..db import Database, new_id
from .permissions import Context


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def record(db: Database, ctx: Context | None, action: str, entity_type: str | None = None,
           entity_id: str | None = None, before: Any = None, after: Any = None,
           *, org_id: str | None = None, user_id: str | None = None) -> None:
    """Append an audit entry. Must be called inside the same transaction as the
    change it describes, so a change can never exist without its audit row."""
    db.execute(
        "INSERT INTO audit_log (id, org_id, user_id, action, entity_type, entity_id,"
        " before_json, after_json, ip) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id(), org_id or (ctx.org_id if ctx else None),
         user_id or (ctx.user_id if ctx else None), action, entity_type, entity_id,
         _dump(before), _dump(after), ctx.ip if ctx else None),
    )


def list_for_entity(db: Database, ctx: Context, entity_type: str, entity_id: str) -> list[dict]:
    ctx.require("audit.read")
    rows = db.all("SELECT * FROM audit_log WHERE org_id=? AND entity_type=? AND entity_id=?"
                  " ORDER BY at, id", (ctx.org_id, entity_type, entity_id))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("before_json", "after_json"):
            d[k] = json.loads(d[k]) if d[k] else None
        out.append(d)
    return out
