"""Link the pharmacy's items to the reference catalog (plan stage M2).

Conservative by design (tests: tests/test_reference_links.py):
- Every link starts as a suggestion. Nothing is approved without a person.
- 'strong' = barcode with agreeing name and pack, or a near-identical name whose strength, form, pack
  and name-numbers all agree. Only strong suggestions can be approved in bulk.
- Different strength, form, pack, modifier or name-number (B6/B12, Omega-3/6) is never strong.
- Approving a link changes nothing on the item, unless the person also asks to fill EMPTY details
  (active ingredient, strength, form) from the reference; those go through the normal audited edit.
"""
from __future__ import annotations

import json
from dataclasses import replace

from ..core.permissions import Context
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..db import Database
from . import matching
from . import service as cat
from .normalize import normalize_barcode, normalize_form, parse_name

STRONG_MIN = matching.AUTO_FUZZY_MIN      # 0.97, same bar as invoice matching
SUGGEST_MIN = 0.75                        # stricter than invoices: a wrong link would move prices
MAX_PER_ITEM = 3
NAME_AGREE_MIN = matching.NAME_CONFLICT_MAX


def _ref_index(db: Database, source: str | None) -> matching._OrgIndex:
    where, args = "removed_at IS NULL AND replaced_by IS NULL", []
    if source:
        where += " AND source=?"
        args.append(source)
    items = []
    for r in db.all(f"SELECT id, name_en, name_ar, strength_norm, dosage_form, units_per_package FROM ref_products"
                    f" WHERE {where}", tuple(args)):
        parsed = [parse_name(n) for n in (r["name_en"], r["name_ar"]) if n]
        if not parsed:
            continue
        items.append(matching._IndexedItem(
            item_id=r["id"], label=r["name_en"] or r["name_ar"], parsed=parsed,
            strength=r["strength_norm"] or next((p.strength for p in parsed if p.strength), None),
            form=normalize_form(r["dosage_form"]) or r["dosage_form"] or next((p.form for p in parsed if p.form), None),
            content=next((p.content for p in parsed if p.content), None),
            pack_count=r["units_per_package"] or next((p.pack_count for p in parsed if p.pack_count), None),
            modifiers=frozenset().union(*(p.modifiers for p in parsed)),
        ))
    idx = matching._OrgIndex(items)
    idx.full_tokens = {}                 # exact name word -> references (ranks true matches first)
    for it in items:
        for p in it.parsed:
            for tok in p.core_tokens:
                idx.full_tokens.setdefault(tok, set()).add(it.item_id)
    return idx


MAX_CANDIDATES = 60


def _top_candidates(idx: matching._OrgIndex, qs) -> list[str]:
    """Only the references sharing the most name pieces get the (slow) full score."""
    hits: dict[str, int] = {}
    for q in qs:
        keys = {t[:3] for t in q.core_tokens} | {"#" + sk[:3] for sk in q.skeleton.split()}
        for k in keys:
            for rid in idx.by_key.get(k, ()):
                hits[rid] = hits.get(rid, 0) + 1
        for tok in q.core_tokens:
            for rid in getattr(idx, "full_tokens", {}).get(tok, ()):
                hits[rid] = hits.get(rid, 0) + 10
    return sorted(hits, key=hits.get, reverse=True)[:MAX_CANDIDATES]


def _form_differs(qs, ref_item) -> bool:
    """Invoice matching treats tablets and capsules as one group; a price link must not."""
    qf = next((q.form for q in qs if q.form), None)
    return bool(qf and ref_item.form and qf != ref_item.form)


def _query(item: dict):
    names = [n for n in (item["name_en"], item["name_ar"]) if n]
    out = []
    for n in names:
        p = parse_name(n)
        out.append(replace(p, strength=item["strength_norm"] or p.strength, form=item["dosage_form"] or p.form))
    return out


def suggest(db: Database, ctx: Context, *, source: str | None = None, item_ids: list[str] | None = None) -> dict:
    """Recompute suggestions for items that have no approved link (for that source). Owner/manager only."""
    ctx.require("items.write")
    idx = _ref_index(db, source)
    src_of = {r["id"]: r["source"] for r in db.all("SELECT id, source FROM ref_products")}
    bc_refs: dict[str, list[str]] = {}
    for r in db.all("SELECT b.barcode, b.ref_id FROM ref_barcodes b JOIN ref_products p ON p.id=b.ref_id"
                    " WHERE p.removed_at IS NULL AND p.replaced_by IS NULL" + (" AND p.source=?" if source else ""),
                    (source,) if source else ()):
        bc_refs.setdefault(r["barcode"], []).append(r["ref_id"])
    sql = ("SELECT id, name_en, name_ar, strength_norm, dosage_form FROM items WHERE org_id=? AND status='active'")
    args: list = [ctx.org_id]
    if item_ids:
        sql += f" AND id IN ({','.join('?' * len(item_ids))})"
        args += item_ids
    items = [dict(r) for r in db.all(sql, tuple(args))]
    approved = {(r["item_id"], r["source"]) for r in db.all(
        "SELECT item_id, source FROM ref_links WHERE org_id=? AND status='approved'", (ctx.org_id,))}
    rejected = {(r["item_id"], r["ref_id"]) for r in db.all(
        "SELECT item_id, ref_id FROM ref_links WHERE org_id=? AND status='rejected'", (ctx.org_id,))}
    barcodes: dict[str, list[str]] = {}
    for r in db.all("SELECT item_id, barcode FROM item_barcodes WHERE org_id=?", (ctx.org_id,)):
        barcodes.setdefault(r["item_id"], []).append(r["barcode"])

    rep = {"items": len(items), "strong": 0, "weak": 0, "none": 0, "skipped_approved": 0}
    with db.transaction():
        db.execute("DELETE FROM ref_links WHERE org_id=? AND status='suggested'"
                   + (f" AND item_id IN ({','.join('?' * len(item_ids))})" if item_ids else ""),
                   (ctx.org_id, *(item_ids or [])))
        for it in items:
            qs = _query(it)
            if not qs:
                continue
            found: dict[str, tuple] = {}
            # 1) barcode
            for bc in barcodes.get(it["id"], []):
                for rid in bc_refs.get(normalize_barcode(bc) or "", []):
                    if rid not in idx.items:
                        continue
                    c = max((matching.score_item(q, idx.items[rid]) for q in qs), key=lambda c: c.score)
                    name_ok = max(matching.name_similarity(q, p) for q in qs for p in idx.items[rid].parsed) \
                        >= NAME_AGREE_MIN
                    hard = {x for x in c.reasons if x.endswith("_mismatch")}
                    if _form_differs(qs, idx.items[rid]):
                        hard.add("exact_form_mismatch")
                        c.reasons.append("exact_form_mismatch")
                    shared = len(bc_refs[normalize_barcode(bc)]) > 1
                    strong = name_ok and not hard and not shared
                    reasons = ["barcode"] + c.reasons + ([] if name_ok else ["name_disagrees"]) \
                        + (["barcode_shared"] if shared else [])
                    found[rid] = ("barcode", 1.0 if strong else round(c.score, 4), strong, reasons)
            # 2) name
            for rid in _top_candidates(idx, qs):
                if rid in found:
                    continue
                c = max((matching.score_item(q, idx.items[rid]) for q in qs), key=lambda c: c.score)
                if c.score < SUGGEST_MIN:
                    continue
                reasons = list(c.reasons)
                if _form_differs(qs, idx.items[rid]):
                    reasons.append("exact_form_mismatch")      # tabs vs caps, syrup vs suspension
                strong = c.score >= STRONG_MIN and not reasons
                found[rid] = ("name", c.score, strong, reasons)
            best = sorted(found.items(), key=lambda kv: (kv[1][2], kv[1][1]), reverse=True)
            n_written = 0
            for rid, (method, score, strong, reasons) in best:
                if (it["id"], src_of[rid]) in approved:
                    rep["skipped_approved"] += 1
                    continue
                if (it["id"], rid) in rejected or n_written >= MAX_PER_ITEM:
                    continue
                db.execute("INSERT OR IGNORE INTO ref_links (org_id, item_id, ref_id, source, method, score, strength,"
                           " reasons) VALUES (?,?,?,?,?,?,?,?)",
                           (ctx.org_id, it["id"], rid, src_of[rid], method, score, "strong" if strong else "weak",
                            json.dumps(reasons)))
                n_written += 1
            if not n_written:
                rep["none"] += 1
            elif best and best[0][1][2]:
                rep["strong"] += 1
            else:
                rep["weak"] += 1
    return rep


def queue(db: Database, ctx: Context, *, status: str = "suggested", strength: str | None = None,
          limit: int = 200, offset: int = 0) -> dict:
    ctx.require("items.read")
    if status not in ("suggested", "approved", "rejected"):
        raise ValidationFailed("حالة غير معروفة")
    where, args = "l.org_id=? AND l.status=?", [ctx.org_id, status]
    if strength in ("strong", "weak"):
        where += " AND l.strength=?"
        args.append(strength)
    limit = max(1, min(int(limit), 500))
    rows = db.all(
        f"SELECT l.item_id, l.ref_id, l.source, l.method, l.score, l.strength, l.reasons, l.status,"
        f" i.name_en item_name_en, i.name_ar item_name_ar, i.public_price item_price,"
        f" p.name_en ref_name_en, p.name_ar ref_name_ar, p.strength ref_strength, p.dosage_form ref_form,"
        f" p.units_per_package ref_units, p.company ref_company, p.scientific_name ref_ingredients,"
        f" (SELECT r.price FROM ref_prices r WHERE r.ref_id=p.id ORDER BY coalesce(r.effective_date,'') DESC,"
        f"  r.observed_at DESC LIMIT 1) ref_price"
        f" FROM ref_links l JOIN items i ON i.id=l.item_id AND i.org_id=l.org_id JOIN ref_products p ON p.id=l.ref_id"
        f" WHERE {where} ORDER BY l.strength='strong' DESC, l.score DESC LIMIT ? OFFSET ?",
        (*args, limit, max(0, int(offset))))
    total = db.one(f"SELECT count(*) n FROM ref_links l WHERE {where}", tuple(args))["n"]
    out = []
    for r in rows:
        d = dict(r)
        d["reasons"] = json.loads(d["reasons"] or "[]")
        out.append(d)
    return {"rows": out, "total": total}


def decide(db: Database, ctx: Context, item_id: str, ref_id: str, decision: str, *,
           fill_empty_details: bool = False) -> dict:
    ctx.require("items.write")
    if decision not in ("approve", "reject"):
        raise ValidationFailed("القرار لازم يكون اعتماد أو رفض")
    with db.transaction():
        link = db.one("SELECT * FROM ref_links WHERE org_id=? AND item_id=? AND ref_id=?",
                      (ctx.org_id, item_id, ref_id))
        if not link:
            raise NotFound("الاقتراح غير موجود")
        if decision == "approve":
            other = db.one("SELECT ref_id FROM ref_links WHERE org_id=? AND item_id=? AND source=? AND status='approved'"
                           " AND ref_id<>?", (ctx.org_id, item_id, link["source"], ref_id))
            if other:
                raise Conflict("الصنف مربوط بصنف تاني من نفس المصدر؛ الغِ الربط القديم الأول", code="already_linked")
        db.execute("UPDATE ref_links SET status=?, decided_by=?, decided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                   " WHERE org_id=? AND item_id=? AND ref_id=?",
                   ("approved" if decision == "approve" else "rejected", ctx.user_id, ctx.org_id, item_id, ref_id))
        if decision == "approve":   # other suggestions of the same source for this item are now moot
            db.execute("DELETE FROM ref_links WHERE org_id=? AND item_id=? AND source=? AND status='suggested'",
                       (ctx.org_id, item_id, link["source"]))
        from ..core import audit
        audit.record(db, ctx, f"ref_link.{decision}", "item", item_id, after={"ref_id": ref_id, "source": link["source"]})
        filled = _fill_details(db, ctx, item_id, ref_id) if (decision == "approve" and fill_empty_details) else {}
    return {"status": "approved" if decision == "approve" else "rejected", "filled": filled}


def unlink(db: Database, ctx: Context, item_id: str, ref_id: str) -> None:
    ctx.require("items.write")
    with db.transaction():
        n = db.execute("DELETE FROM ref_links WHERE org_id=? AND item_id=? AND ref_id=? AND status='approved'",
                       (ctx.org_id, item_id, ref_id)).rowcount
        if not n:
            raise NotFound("الربط غير موجود")
        from ..core import audit
        audit.record(db, ctx, "ref_link.unlink", "item", item_id, after={"ref_id": ref_id})


def approve_all_strong(db: Database, ctx: Context, *, source: str | None = None) -> dict:
    """One click for the owner: approve every STRONG suggestion that is the only strong one for its item."""
    ctx.require("items.write")
    rows = db.all("SELECT item_id, ref_id, source FROM ref_links WHERE org_id=? AND status='suggested' AND strength='strong'"
                  + (" AND source=?" if source else ""), (ctx.org_id, *([source] if source else [])))
    per: dict[tuple, list] = {}
    for r in rows:
        per.setdefault((r["item_id"], r["source"]), []).append(r["ref_id"])
    done, ambiguous = 0, 0
    for (item_id, _src), refs in per.items():
        if len(refs) != 1:
            ambiguous += 1
            continue
        try:
            decide(db, ctx, item_id, refs[0], "approve")
            done += 1
        except Conflict:
            ambiguous += 1
    return {"approved": done, "left_for_review": ambiguous}


def _fill_details(db: Database, ctx: Context, item_id: str, ref_id: str) -> dict:
    it = cat.get_item(db, ctx, item_id)
    r = db.one("SELECT scientific_name, strength, dosage_form FROM ref_products WHERE id=?", (ref_id,))
    ch = {}
    if not it.get("active_ingredient") and r["scientific_name"]:
        ch["active_ingredient"] = r["scientific_name"]
    if not it.get("strength") and r["strength"]:
        ch["strength"] = r["strength"]
    form = normalize_form(r["dosage_form"])
    if not it.get("dosage_form") and form:
        ch["dosage_form"] = form
    if ch:
        cat.update_item(db, ctx, item_id, ch)
    return ch


def coverage(db: Database, ctx: Context) -> dict:
    ctx.require("items.read")
    q = lambda sql: db.one(sql, (ctx.org_id,))["n"]  # noqa: E731
    return {
        "active_items": q("SELECT count(*) n FROM items WHERE org_id=? AND status='active'"),
        "linked": q("SELECT count(DISTINCT item_id) n FROM ref_links WHERE org_id=? AND status='approved'"),
        "suggested_strong": q("SELECT count(DISTINCT item_id) n FROM ref_links WHERE org_id=? AND status='suggested'"
                              " AND strength='strong'"),
        "suggested_weak_only": q("SELECT count(*) n FROM (SELECT item_id FROM ref_links WHERE org_id=?"
                                 " AND status='suggested' GROUP BY item_id HAVING max(strength='strong')=0)"),
    }
