"""One-shot migration from E-Stock exports: catalog -> pack sizes -> opening stock.

Every decision the generic importer leaves to a person is taken here only by
an explicit, documented rule passed in by the operator (and recorded in the
report); nothing else is decided silently:

- catalog rows with the same name as another row are kept as separate items
  ('keep_duplicates'): every E-Stock code keeps its own item, because stock
  and history refer to codes; the items are tagged '|dup:' for a later merge;
- pack sizes come only from the reviewed packs file;
- E-Stock unit drift (0.336 for 1/3 pack) is rounded to the nearest unit only
  up to `max_unit_error` units, each rounded row is listed;
- zero-cost batches (bonus / donation) are posted at cost 0 only with
  `accept_zero_cost`; otherwise skipped and listed;
- rows that still cannot be posted (unknown pack size) are skipped and listed
  for the next physical count — never posted with a guessed quantity.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from ..catalog import service as catalog
from ..core.errors import ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database
from ..purchasing.importers import files
from .service import DataImport


def read_packs(path: Path) -> dict[str, int]:
    """Packs file: .json {code: n} or .xlsx with columns 'الكود' and a column whose
    header contains 'القرار' or 'عدد الوحدات' (the review sheet we send)."""
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw = raw.get("packs", raw)
        out = {}
        for k, v in raw.items():
            if not v:
                continue
            try:
                n = whole_number(v)               # 2.5 is refused like in the xlsx sheet, never cut to 2
            except (TypeError, ValueError):
                n = 0
            if n < 1:
                raise ValidationFailed(f"عدد وحدات غير صحيح للصنف {k}: {v}")
            out[str(k)] = n
        return out
    rows = files.read_excel_sheets(path.read_bytes())[0][1]
    head = [str(c or "") for c in rows[0]]
    try:
        ci = head.index("الكود")
        ni = next(i for i, h in enumerate(head) if "القرار" in h) if any("القرار" in h for h in head) \
            else next(i for i, h in enumerate(head) if "عدد الوحدات" in h)
    except (ValueError, StopIteration):
        raise ValidationFailed("ملف عدد الوحدات يجب أن يحتوي على عمود 'الكود' وعمود 'القرار'")
    out = {}
    for r in rows[1:]:
        if len(r) > max(ci, ni) and r[ci] not in (None, "") and r[ni] not in (None, ""):
            try:
                n = Decimal(str(r[ni]).strip())
            except ArithmeticError:                       # InvalidOperation: not a number at all
                n = Decimal("NaN")
            if not n.is_finite() or n.adjusted() > 6 or n != n.to_integral_value() or n < 1:
                raise ValidationFailed(f"عدد وحدات غير صحيح للصنف {r[ci]}: {r[ni]}")
            out[str(int(r[ci])) if isinstance(r[ci], float) else str(r[ci])] = int(n)
    return out


def run(db: Database, ctx: Context, *, catalog_file: Path, stock_file: Path, packs_file: Path | None,
        stores: list[str], keep_duplicates: bool = True, accept_zero_cost: bool = False,
        max_unit_error: str = "0.15") -> dict:
    di = DataImport(db)
    rep: dict = {"rules": {"keep_duplicates": keep_duplicates, "accept_zero_cost": accept_zero_cost,
                           "max_unit_error": max_unit_error, "stores": stores}}
    # 1) catalog. Each step commits on its own, so after a failure in a later step the catalog is
    # already in and its file would be refused as "already imported": go on from step 2 instead.
    data = catalog_file.read_bytes()
    done = db.one("SELECT id, report FROM import_runs WHERE org_id=? AND kind='items' AND file_sha256=?"
                  " AND status='applied'", (ctx.org_id, hashlib.sha256(data).hexdigest()))
    if done:      # the figures of the run that applied it (the summary in manage.py reads these keys)
        kept = db.one("SELECT count(*) n FROM import_rows WHERE run_id=? AND action='review'"
                      " AND decision='create'", (done["id"],))["n"]
        rep["catalog"] = {**json.loads(done["report"]).get("applied", {}), "kept_duplicates": kept,
                          "already_applied": True}
    else:
        run_id = di.analyze(ctx, data, catalog_file.name, kind="items", source_system="estock")
        reviews = db.all("SELECT id, data FROM import_rows WHERE run_id=? AND action='review'", (run_id,))
        if reviews and not keep_duplicates:
            raise ValidationFailed(f"{len(reviews)} صنفًا بالاسم نفسه تحتاج إلى قرار؛ راجعها من الشاشة")
        for r in reviews:
            di.decide(ctx, run_id, r["id"], "create")
        a = di.apply(ctx, run_id)
        rep["catalog"] = {**a["applied"], "kept_duplicates": len(reviews)}
    # 2) pack sizes (reviewed file only)
    packs = read_packs(packs_file) if packs_file else {}
    unknown = []
    for code, n in packs.items():
        row = db.one("SELECT id, pack_size FROM items WHERE org_id=? AND code=?", (ctx.org_id, code))
        if not row:
            unknown.append(code)
        elif row["pack_size"] != n:
            catalog.update_item(db, ctx, row["id"], {"pack_size": n})
    rep["packs"] = {"applied": len(packs) - len(unknown), "codes_not_in_catalog": unknown}
    # 3) opening stock
    run_id = di.analyze(ctx, stock_file.read_bytes(), stock_file.name, kind="opening_stock",
                        source_system="estock",
                        options={"estock_stores": stores, "max_unit_error": max_unit_error})
    listed = {"skipped_unknown_pack": [], "skipped_zero_cost": [], "posted_zero_cost": [],
              "rounded": [], "no_expiry": 0}
    for r in db.all("SELECT id, action, data, issues FROM import_rows WHERE run_id=?", (run_id,)):
        d, iss = json.loads(r["data"]), {i["code"]: i for i in json.loads(r["issues"])}
        line = {"code": d.get("code"), "name": d.get("_name"), "qty": d.get("qty")}
        if "rounded_units" in iss:
            listed["rounded"].append({**line, "from": iss["rounded_units"]["from"],
                                      "to": iss["rounded_units"]["to"]})
        if "no_batch_or_expiry" in iss:
            listed["no_expiry"] += 1
        if r["action"] == "error":
            key = "skipped_unknown_pack" if "fractional_qty_needs_pack_size" in iss else "errors"
            listed.setdefault(key, []).append({**line, "issues": list(iss)})
            di.decide(ctx, run_id, r["id"], "skip")
        elif r["action"] == "review":
            if accept_zero_cost and set(iss) <= {"zero_cost", "no_batch_or_expiry", "rounded_units"} \
                    and "zero_cost" in iss:
                di.decide(ctx, run_id, r["id"], "create")
                listed["posted_zero_cost"].append(line)
            else:
                key = "skipped_zero_cost" if "zero_cost" in iss else "skipped_review"
                listed.setdefault(key, []).append({**line, "issues": list(iss)})
                di.decide(ctx, run_id, r["id"], "skip")
    a = di.apply(ctx, run_id)
    rep["stock"] = {"applied": a["applied"], "reconciliation": a["reconciliation"], **listed}
    return rep
