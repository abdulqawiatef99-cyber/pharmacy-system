"""EDA recall / falsified-product alerts.

Input today: a person enters (or pastes) the circular — product name + batch
numbers. extract_batches() only *proposes* batch numbers from pasted text; the
person confirms them before saving. (An automatic fetcher of the EDA page is not
built: the listing is not available as static HTML/API — see docs.)

Matching is exact on the normalized batch number. A batch hit whose product
name does not resemble the notice's product is still raised, flagged
'batch_only', because batch numbers can collide across manufacturers — a human
decides. Stock is never moved automatically.
"""
from __future__ import annotations

import json
import re

from ..catalog.matching import name_similarity
from ..catalog.normalize import parse_name
from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
NAME_MIN = 0.6


def norm_batch(b: str) -> str:
    return re.sub(r"[\s\-_/.]", "", str(b).translate(_AR_DIGITS)).upper()


def extract_batches(text: str) -> list[str]:
    """Propose batch-number-like tokens: 4-15 chars, contain a digit, not a
    date, not a pure strength like '500mg'. A proposal, never auto-saved."""
    t = text.translate(_AR_DIGITS)
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/]{2,16}", t):
        tok = tok.strip("-/")
        n = norm_batch(tok)
        if not 4 <= len(n) <= 15 or not re.search(r"\d", n):
            continue
        if re.fullmatch(r"\d{1,2}/\d{2,4}|\d{4}-\d{2}(-\d{2})?|\d{1,2}-\d{1,2}-\d{2,4}", tok):
            continue
        if re.fullmatch(r"\d+(MG|ML|G|GM|MCG|IU)", n):
            continue
        if re.fullmatch(r"(19|20)\d{2}", n):          # a year
            continue
        if n not in out:
            out.append(n)
    return out


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


class Recalls:
    def __init__(self, db: Database):
        self.db = db

    def add_notice(self, ctx: Context, *, title: str, items: list[dict], source: str = "EDA",
                   notice_no: str | None = None, notice_date: str | None = None,
                   url: str | None = None, raw_text: str | None = None) -> str:
        ctx.require("inventory.read")
        if not title.strip():
            raise ValidationFailed("عنوان المنشور مطلوب")
        clean = []
        for it in items:
            name = str(it.get("product_name") or "").strip()
            batches = [norm_batch(b) for b in (it.get("batch_numbers") or []) if str(b).strip()]
            if not name:
                raise ValidationFailed("اسم المستحضر مطلوب لكل بند")
            if it.get("action", "recall") not in ("recall", "falsified", "quarantine", "other"):
                raise ValidationFailed("نوع الإجراء غير صحيح")
            clean.append((name, sorted(set(batches)), it.get("action", "recall")))
        if not clean:
            raise ValidationFailed("أضف مستحضرًا واحدًا على الأقل")
        nid = new_id()
        with self.db.transaction():
            if notice_no and self.db.one("SELECT 1 FROM regulatory_notices WHERE org_id=? AND"
                                         " source=? AND notice_no=? AND notice_date IS ?",
                                         (ctx.org_id, source, notice_no, notice_date)):
                raise Conflict("المنشور مسجل بالفعل")
            self.db.execute("INSERT INTO regulatory_notices (id, org_id, source, notice_no, notice_date,"
                            " title, url, raw_text, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                            (nid, ctx.org_id, source, notice_no, notice_date, title.strip(), url,
                             raw_text, ctx.user_id))
            for name, batches, action in clean:
                self.db.execute("INSERT INTO notice_items (id, notice_id, product_name, batch_numbers,"
                                " action) VALUES (?,?,?,?,?)",
                                (new_id(), nid, name, json.dumps(batches), action))
            audit.record(self.db, ctx, "recall.notice_add", "regulatory_notice", nid,
                         after={"title": title, "items": clean})
        self.match(ctx, nid)
        return nid

    def match(self, ctx: Context, notice_id: str) -> int:
        """(Re)run matching for one notice; returns number of new matches."""
        ctx.require("inventory.read")
        notice = self.db.one("SELECT * FROM regulatory_notices WHERE id=? AND org_id=?",
                             (notice_id, ctx.org_id))
        if not notice:
            raise NotFound("المنشور غير موجود")
        new = 0
        with self.db.transaction():
            for ni in self.db.all("SELECT * FROM notice_items WHERE notice_id=?", (notice_id,)):
                wanted = set(json.loads(ni["batch_numbers"]))
                if not wanted:
                    continue
                qn = parse_name(ni["product_name"])
                for b in self.db.all(
                        "SELECT b.id, b.item_id, b.batch_no, coalesce(i.name_en, i.name_ar) label"
                        " FROM batches b JOIN items i ON i.id=b.item_id WHERE b.org_id=?",
                        (ctx.org_id,)):
                    if norm_batch(b["batch_no"]) not in wanted:
                        continue
                    sim = name_similarity(qn, parse_name(b["label"]))
                    basis = "batch+name" if sim >= NAME_MIN else "batch_only"
                    qty = self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=?"
                                      " AND item_id=? AND batch_key=?",
                                      (ctx.org_id, b["item_id"], b["id"]))["q"]
                    cur = self.db.execute(
                        "INSERT OR IGNORE INTO recall_matches (id, org_id, notice_item_id, item_id,"
                        " batch_id, match_basis, qty_in_stock) VALUES (?,?,?,?,?,?,?)",
                        (new_id(), ctx.org_id, ni["id"], b["item_id"], b["id"], basis, qty))
                    new += cur.rowcount
            if new:
                audit.record(self.db, ctx, "recall.matched", "regulatory_notice", notice_id,
                             after={"new_matches": new})
        return new

    def match_new_batches(self, ctx: Context) -> int:
        """Run all notices again (call after approving purchases: a recalled
        batch may arrive after the circular)."""
        return sum(self.match(ctx, n["id"]) for n in self.db.all(
            "SELECT id FROM regulatory_notices WHERE org_id=?", (ctx.org_id,)))

    def alerts(self, ctx: Context, status: str | None = "pending") -> list[dict]:
        ctx.require("inventory.read")
        q = ("SELECT m.*, n.title, n.notice_no, n.notice_date, n.source, ni.product_name, ni.action,"
             " b.batch_no, b.expiry_date, coalesce(i.name_en, i.name_ar) item_label"
             " FROM recall_matches m JOIN notice_items ni ON ni.id=m.notice_item_id"
             " JOIN regulatory_notices n ON n.id=ni.notice_id JOIN batches b ON b.id=m.batch_id"
             " JOIN items i ON i.id=m.item_id WHERE m.org_id=?")
        params: list = [ctx.org_id]
        if status:
            q += " AND m.status=?"
            params.append(status)
        q += " ORDER BY m.qty_in_stock DESC, m.created_at DESC"
        return [dict(r) for r in self.db.all(q, tuple(params))]

    def decide(self, ctx: Context, match_id: str, status: str, note: str | None = None) -> None:
        ctx.require("inventory.adjust")
        if status not in ("confirmed", "not_ours", "handled"):
            raise ValidationFailed("حالة غير صحيحة")
        if status == "not_ours" and len((note or "").strip()) < 3:
            raise ValidationFailed("اكتب سبب أن التشغيلة لا تخصنا")
        with self.db.transaction():
            n = self.db.execute("UPDATE recall_matches SET status=?, note=?, decided_by=?, decided_at=?"
                                " WHERE id=? AND org_id=?",
                                (status, note, ctx.user_id, _now(), match_id, ctx.org_id)).rowcount
            if not n:
                raise NotFound("التنبيه غير موجود")
            audit.record(self.db, ctx, "recall.decide", "recall_match", match_id,
                         after={"status": status, "note": note})
