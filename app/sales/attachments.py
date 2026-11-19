"""Transfer proof: the screenshot of an InstaPay / Vodafone Cash transfer saved with the sale,
so the owner or a manager can check that the money really arrived."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

MAX_BYTES = 8 * 1024 * 1024
KINDS = {b"\x89PNG": ("image/png", ".png"), b"\xff\xd8\xff": ("image/jpeg", ".jpg"), b"%PDF": ("application/pdf", ".pdf")}


def _sniff(data: bytes) -> tuple[str, str]:
    for magic, v in KINDS.items():
        if data.startswith(magic):
            return v
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValidationFailed("يجب أن يكون الملف صورة (JPG / PNG / WEBP) أو PDF")


def _sale(db: Database, ctx: Context, sale_id: str) -> dict:
    from .service import Sales
    s = db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sale_id, ctx.org_id))
    if not s:
        raise NotFound("الفاتورة غير موجودة")
    Sales._may_view(ctx, dict(s))
    return dict(s)


def attach(db: Database, upload_dir: Path, ctx: Context, sale_id: str, data: bytes, filename: str = "",
           note: str | None = None) -> dict:
    ctx.require("sales.create")
    s = _sale(db, ctx, sale_id)
    if s["status"] == "voided":
        raise Conflict("الفاتورة ملغاة")
    if not data:
        raise ValidationFailed("الملف فارغ")
    if len(data) > MAX_BYTES:
        raise ValidationFailed("الصورة أكبر من 8 ميجا")
    mime, ext = _sniff(data)
    sha = hashlib.sha256(data).hexdigest()
    folder = upload_dir / ctx.org_id / "transfers"          # path never built from user input
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}{ext}"
    if not path.exists():
        tmp = path.with_suffix(ext + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    aid, fid = new_id(), new_id()
    # the duplicate checks, the save and the alert in one write: two tills saving the same screenshot at the
    # same moment cannot both find nothing (#324 M2-24)
    with db.transaction():
        if db.one("SELECT 1 FROM sale_attachments a JOIN source_files f ON f.id=a.file_id WHERE a.sale_id=? AND f.sha256=?"
                  " AND a.org_id=?", (sale_id, sha, ctx.org_id)):
            raise Conflict("هذه الصورة مسجلة مع هذه الفاتورة من قبل")
        other = db.one("SELECT s.id, s.sale_no FROM sale_attachments a JOIN source_files f ON f.id=a.file_id"
                       " JOIN sales s ON s.id=a.sale_id WHERE a.org_id=? AND f.sha256=? AND a.sale_id<>?"
                       " ORDER BY s.sale_no IS NULL, a.created_at LIMIT 1", (ctx.org_id, sha, sale_id))
        db.execute("INSERT INTO source_files (id, org_id, sha256, filename, mime_type, size_bytes, storage_path, uploaded_by)"
                   " VALUES (?,?,?,?,?,?,?,?)", (fid, ctx.org_id, sha, Path(filename or "transfer").name[:200], mime,
                                                len(data), str(path), ctx.user_id))
        db.execute("INSERT INTO sale_attachments (id, org_id, sale_id, file_id, kind, note, created_by) VALUES (?,?,?,?,?,?,?)",
                   (aid, ctx.org_id, sale_id, fid, "transfer", (note or "").strip()[:200] or None, ctx.user_id))
        audit.record(db, ctx, "sale.attach", "sale", sale_id, after={"file": fid, "size": len(data)})
        if other:   # the same screenshot used for two invoices; one still open has no number yet
            name = lambda no: str(no) if no else "لم تكتمل بعد"  # noqa: E731
            notify.emit(db, ctx, "transfer_duplicate", "error",
                        f"صورة التحويل نفسها سُجّلت مع فاتورتين ({name(other['sale_no'])} و{name(s['sale_no'])})",
                        "راجع التحويل: قد يكون المبلغ نفسه محسوبًا مرتين", "sale", sale_id)
    return {"attachments": listing(db, ctx, sale_id),
            "duplicate_of": (other["sale_no"] or "(لم تكتمل بعد)") if other else None,
            "duplicate_sale_id": other["id"] if other else None}


def listing(db: Database, ctx: Context, sale_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT a.id, a.kind, a.note, a.review, a.review_note, a.confirmed_amount, a.reviewed_at, a.created_at, f.mime_type, f.size_bytes,"
        " u.full_name by_name, r.full_name reviewed_name FROM sale_attachments a JOIN source_files f ON f.id=a.file_id"
        " LEFT JOIN users u ON u.id=a.created_by LEFT JOIN users r ON r.id=a.reviewed_by"
        " WHERE a.org_id=? AND a.sale_id=? ORDER BY a.created_at", (ctx.org_id, sale_id))]


def file(db: Database, upload_dir: Path, ctx: Context, att_id: str) -> tuple[bytes, str]:
    ctx.require("sales.read")
    r = db.one("SELECT a.sale_id, f.storage_path, f.mime_type, f.org_id FROM sale_attachments a JOIN source_files f"
               " ON f.id=a.file_id WHERE a.id=? AND a.org_id=?", (att_id, ctx.org_id))
    if not r:
        raise NotFound("المرفق غير موجود")
    _sale(db, ctx, r["sale_id"])
    # only the current upload folder, by the file's own name: a path saved elsewhere is never read (#324 X26)
    p = upload_dir / r["org_id"] / "transfers" / Path(r["storage_path"]).name
    if not p.exists():
        raise NotFound("ملف الصورة غير موجود على هذا الجهاز")
    return p.read_bytes(), r["mime_type"]


def review(db: Database, ctx: Context, att_id: str, ok: bool, note: str | None = None,
           amount: int | None = None) -> dict:
    """ok with `amount`: this proof shows only that much of the sale's transfer arrived (#324 M2-01). The rest
    is then not counted as received: it funds no refund and shows as a problem in the transfers report."""
    ctx.require("sales.totals")
    r = db.one("SELECT * FROM sale_attachments WHERE id=? AND org_id=?", (att_id, ctx.org_id))
    if not r:
        raise NotFound("المرفق غير موجود")
    note = (note or "").strip()[:200] or None
    if not ok and not note:
        raise ValidationFailed("اكتب المشكلة (مثال: المبلغ لم يصل / المبلغ أقل)")
    sale = db.one("SELECT sale_no, paid_transfer FROM sales WHERE id=? AND org_id=?", (r["sale_id"], ctx.org_id))
    if amount is not None:
        if not ok or isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= (sale["paid_transfer"] or 0):
            raise ValidationFailed("اكتب المبلغ الذي وصل (أكبر من صفر ولا يزيد عن مبلغ التحويل)")
    with db.transaction():
        db.execute("UPDATE sale_attachments SET review=?, review_note=?, confirmed_amount=?, reviewed_by=?,"
                   " reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                   ("ok" if ok else "problem", note, amount if ok else None, ctx.user_id, att_id))
        audit.record(db, ctx, "sale.transfer_review", "sale", r["sale_id"], before={"review": r["review"]},
                     after={"review": "ok" if ok else "problem", "note": note, "amount": amount})
        got = transfer_state(db, ctx.org_id, r["sale_id"], sale["paid_transfer"] or 0)
        if not ok:
            notify.emit(db, ctx, "transfer_problem", "error", f"مشكلة في تحويل فاتورة {sale['sale_no']} ({sale['paid_transfer'] / 100:,.2f} ج)",
                        note, "sale", r["sale_id"])
        elif got["state"] == "problem":     # a part confirmed and nothing else waiting: the rest did not come
            notify.emit(db, ctx, "transfer_problem", "error",
                        f"وصل {got['verified'] / 100:,.2f} ج فقط من تحويل فاتورة {sale['sale_no']}"
                        f" ({sale['paid_transfer'] / 100:,.2f} ج)", note, "sale", r["sale_id"])
    return dict(db.one("SELECT * FROM sale_attachments WHERE id=?", (att_id,)))


def transfer_state(db: Database, org_id: str, sale_id: str, paid_transfer: int) -> dict:
    """What a sale's transfer proofs say: `verified` (confirmed amounts; a proof approved without an amount
    confirms the whole transfer), `received` (what may fund a refund: the whole transfer until a reviewer
    says otherwise, by a problem or by approving only part of it), and the report's `state`."""
    return _state(db.all("SELECT review, confirmed_amount FROM sale_attachments WHERE org_id=? AND sale_id=?",
                         (org_id, sale_id)), paid_transfer)


def _state(rows, paid_transfer: int) -> dict:
    verified = min(paid_transfer, sum((r["confirmed_amount"] if r["confirmed_amount"] is not None else paid_transfer)
                                      for r in rows if r["review"] == "ok"))
    problem = any(r["review"] == "problem" for r in rows)
    pending = any(r["review"] == "pending" for r in rows)
    partial = any(r["review"] == "ok" and r["confirmed_amount"] is not None for r in rows) and verified < paid_transfer
    if not rows:
        state = "missing"
    elif verified >= paid_transfer:
        state = "ok"
    elif problem or (partial and not pending):
        state = "problem"
    else:
        state = "pending"
    short = problem or (partial and not pending)
    return {"verified": verified, "received": verified if short else paid_transfer, "state": state}


def transfers(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Every sale paid (partly) by transfer in the period, with its proof and review state."""
    from .service import local_day_bounds
    ctx.require("sales.totals")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    rows = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.paid_transfer, s.total, s.status, u.full_name cashier,"
        " (SELECT group_concat(DISTINCT c.name) FROM sale_payments p JOIN payment_channels c ON c.id=p.channel_id"
        "   WHERE p.sale_id=s.id AND p.method='transfer') channel,"
        " (SELECT count(*) FROM sale_attachments a WHERE a.org_id=s.org_id AND a.sale_id=s.id) proofs,"   # #324 B8
        " (SELECT CASE WHEN sum(a.review='problem') THEN 'problem' WHEN sum(a.review='pending') THEN 'pending'"
        "   WHEN count(*) THEN 'ok' END FROM sale_attachments a WHERE a.org_id=s.org_id AND a.sale_id=s.id) review"
        " FROM sales s LEFT JOIN users u ON u.id=s.created_by WHERE s.org_id=? AND s.paid_transfer>0 AND s.status<>'draft'"
        " AND s.completed_at>=? AND s.completed_at<? ORDER BY s.completed_at DESC",
        (ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))]
    # the confirmed amounts of the period's proofs in one read (#324 M2-01): "ok" counts what was confirmed, the rest
    # of a sale's transfer counts under its state
    proofs: dict[str, list] = {}
    for p in db.all("SELECT a.sale_id, a.review, a.confirmed_amount FROM sale_attachments a JOIN sales s ON s.id=a.sale_id"
                    " WHERE a.org_id=? AND s.org_id=? AND s.paid_transfer>0 AND s.completed_at>=? AND s.completed_at<?",
                    (ctx.org_id, ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1])):
        proofs.setdefault(p["sale_id"], []).append(p)
    for r in rows:
        st = _state(proofs.get(r["id"], []), r["paid_transfer"])
        r["verified"] = st["verified"]
        r["state"] = "voided" if r["status"] == "voided" else st["state"]
    live = [r for r in rows if r["state"] != "voided"]
    rest = lambda st: sum(r["paid_transfer"] - r["verified"] for r in live if r["state"] == st)  # noqa: E731
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "totals": {"all": sum(r["paid_transfer"] for r in live), "missing": rest("missing"),
                       "pending": rest("pending"), "ok": sum(r["verified"] for r in live), "problem": rest("problem")}}
