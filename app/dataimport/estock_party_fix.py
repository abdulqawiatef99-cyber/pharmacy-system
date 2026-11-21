"""Update 1 (owner 21/11 20:34): customers and suppliers moved from E-Stock with is_active=0 were hidden from every
search: the move read E-Stock's `deleted` flag the wrong way (it is '1' on live rows, see estock_db.product_inactive),
so «د محمد الشربيني» and others could not be sold to. From now on the move never hides a party (a deleted one is only
tagged). This repair re-activates, once, every customer and supplier the move created with is_active=0: the move is
the only place that set it, and its raw flags were not kept, so all of them come back. Idempotent, audited, counted."""
from __future__ import annotations

import json

from ..core import audit
from ..core.permissions import Context
from ..db import Database

KEY = "estock_parties_reactivated"


def run(db: Database) -> dict[str, int]:
    """{org_id: parties re-activated} for the organisations repaired now; nothing after the first run."""
    out: dict[str, int] = {}
    for org in [r["id"] for r in db.all("SELECT id FROM organizations")]:
        if db.one("SELECT 1 FROM org_settings WHERE org_id=? AND key=?", (org, KEY)):
            continue
        ids: dict[str, list[str]] = {"customers": [], "suppliers": []}
        for table in ids:
            ids[table] = [r["id"] for r in db.all(
                f"SELECT DISTINCT p.id FROM {table} p JOIN import_rows ir ON ir.target_id=p.id"
                " JOIN import_runs r ON r.id=ir.run_id AND r.org_id=p.org_id"
                f" WHERE p.org_id=? AND p.is_active=0 AND r.source_system='estock_db' AND r.kind=? AND r.status='applied'",
                (org, table))]
        n = sum(len(v) for v in ids.values())
        with db.transaction():
            for table, v in ids.items():
                for k in range(0, len(v), 500):
                    part = v[k:k + 500]
                    db.execute(f"UPDATE {table} SET is_active=1 WHERE org_id=? AND id IN ({','.join('?' * len(part))})",
                               (org, *part))
            db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?)",
                       (org, KEY, json.dumps({"customers": len(ids["customers"]), "suppliers": len(ids["suppliers"])})))
            if n:
                audit.record(db, Context(user_id="system", org_id=org, branch_id=None), "estock.parties_reactivated",
                             "organization", org, after={"customers": len(ids["customers"]),
                                                         "suppliers": len(ids["suppliers"]), "ids": ids})
        out[org] = n
    return out


def result(db: Database, org_id: str) -> dict | None:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    return json.loads(r["value"]) if r else None
