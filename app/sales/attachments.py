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
        "SELECT a.id, a.kind, a.note, a.review, a.review_note, a.reviewed_at, a.created_at, f.mime_type, f.size_bytes,"
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


def review(db: Database, ctx: Context, att_id: str, ok: bool, note: str | None = None) -> dict:
    ctx.require("sales.totals")
    r = db.one("SELECT * FROM sale_attachments WHERE id=? AND org_id=?", (att_id, ctx.org_id))
    if not r:
        raise NotFound("المرفق غير موجود")
    note = (note or "").strip()[:200] or None
    if not ok and not note:
        raise ValidationFailed("اكتب المشكلة (مثال: المبلغ لم يصل / المبلغ أقل)")
    with db.transaction():
        db.execute("UPDATE sale_attachments SET review=?, review_note=?, reviewed_by=?, reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                   " WHERE id=?", ("ok" if ok else "problem", note, ctx.user_id, att_id))
        audit.record(db, ctx, "sale.transfer_review", "sale", r["sale_id"], before={"review": r["review"]},
                     after={"review": "ok" if ok else "problem", "note": note})
        if not ok:
            s = db.one("SELECT sale_no, paid_transfer FROM sales WHERE id=?", (r["sale_id"],))
            notify.emit(db, ctx, "transfer_problem", "error", f"مشكلة في تحويل فاتورة {s['sale_no']} ({s['paid_transfer'] / 100:,.2f} ج)",
                        note, "sale", r["sale_id"])
    return dict(db.one("SELECT * FROM sale_attachments WHERE id=?", (att_id,)))


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
    for r in rows:
        r["state"] = "voided" if r["status"] == "voided" else "missing" if not r["proofs"] else r["review"]
    tot = lambda st: sum(r["paid_transfer"] for r in rows if r["state"] == st)  # noqa: E731
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "totals": {"all": sum(r["paid_transfer"] for r in rows if r["state"] != "voided"), "missing": tot("missing"),
                       "pending": tot("pending"), "ok": tot("ok"), "problem": tot("problem")}}
