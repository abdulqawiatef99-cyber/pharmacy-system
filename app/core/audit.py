from __future__ import annotations

import hashlib
import json
from typing import Any

from ..db import Database, new_id
from .permissions import Context

# Keys never written to the audit trail, at any depth.
SECRET_KEYS = {"password", "password_hash", "token", "token_hash", "api_key", "secret",
               "anthropic_api_key", "authorization"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in SECRET_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_scrub(value), ensure_ascii=False, default=str, sort_keys=True)


def _now() -> str:
    from .accounts import iso, utcnow
    return iso(utcnow())


def _row_hash(prev: str, fields: tuple) -> str:
    payload = json.dumps([prev, *fields], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(db: Database, ctx: Context | None, action: str, entity_type: str | None = None,
           entity_id: str | None = None, before: Any = None, after: Any = None,
           *, org_id: str | None = None, user_id: str | None = None) -> None:
    """Append an audit entry. Must be called inside the same transaction as the
    change it describes, so a change can never exist without its audit row.
    Each row is chained to the previous one (sha256) for tamper evidence."""
    last = db.one("SELECT seq, row_hash FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")
    seq = (last["seq"] + 1) if last else 1
    prev = last["row_hash"] if last else "GENESIS"
    rid = new_id()
    fields = (rid, org_id or (ctx.org_id if ctx else None), user_id or (ctx.user_id if ctx else None),
              action, entity_type, entity_id, _dump(before), _dump(after),
              ctx.ip if ctx else None, _now(), seq)
    db.execute(
        "INSERT INTO audit_log (id, org_id, user_id, action, entity_type, entity_id,"
        " before_json, after_json, ip, at, seq, prev_hash, row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*fields, prev, _row_hash(prev, fields)))


def verify_chain(db: Database) -> list[dict]:
    """Returns problems found in the audit chain (empty list = intact)."""
    problems = []
    prev = "GENESIS"
    expected_seq = 1
    for r in db.all("SELECT * FROM audit_log WHERE seq IS NOT NULL ORDER BY seq"):
        if r["seq"] != expected_seq:
            problems.append({"seq": expected_seq, "problem": "missing_row"})
            expected_seq = r["seq"]
        if r["prev_hash"] != prev:
            problems.append({"seq": r["seq"], "problem": "broken_link"})
        fields = (r["id"], r["org_id"], r["user_id"], r["action"], r["entity_type"], r["entity_id"],
                  r["before_json"], r["after_json"], r["ip"], r["at"], r["seq"])
        if _row_hash(r["prev_hash"], fields) != r["row_hash"]:
            problems.append({"seq": r["seq"], "problem": "row_modified"})
        prev = r["row_hash"]
        expected_seq += 1
    return problems


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
