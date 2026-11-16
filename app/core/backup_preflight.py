"""Read-only check of a backup before moving a pharmacy's data to another machine (R5).

It reads the backup ZIP only (the snapshot is extracted to a temporary folder that is
deleted afterwards); it never opens the live database and never changes or deletes
anything. Its verdict is `verify_archive`'s, so "can restore" here means restore accepts
the archive; the extra part is the per-attachment detail the owner needs to decide.

Every attachment problem blocks a normal restore (no silent partial restore). When attachment
problems are the only ones, `manage.py restore --allow-missing-attachments` restores the data
without those files, as an explicit decision. Importance says what is lost if a file cannot be
recovered:
  financial  original of an approved purchase invoice, transfer screenshot of a completed sale
  optional   draft/cancelled purchase, voided sale, "other" attachment, file nothing uses
"""
from __future__ import annotations

import lzma
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from ..db import Database
from . import backup

STATE_AR = {"approved": "معتمدة", "draft": "مسودة", "cancelled": "ملغاة", "completed": "مكتملة", "voided": "ملغاة"}
STATUS_AR = {"ok": "موجود", "missing": "ناقص", "changed": "متغيّر", "bad_path": "مسار غير معروف"}


def _status(problem: str | None) -> str:
    if not problem:
        return "ok"
    if problem.startswith("missing"):
        return "missing"
    if "mismatch" in problem:
        return "changed"
    return "bad_path"


def _uses(db: Database, org_id: str, file_id: str) -> list[tuple[str, str]]:
    """[(importance, what)] for everything that points at this file."""
    tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    uses = []
    if "purchase_invoices" in tables:
        for r in db.all("SELECT invoice_no, supplier_name_raw, status FROM purchase_invoices WHERE org_id=? AND source_file_id=?",
                        (org_id, file_id)):
            what = f"أصل فاتورة شراء {r['invoice_no'] or '(بدون رقم)'} — {r['supplier_name_raw'] or ''} ({STATE_AR.get(r['status'], r['status'])})"
            uses.append(("financial" if r["status"] == "approved" else "optional", what))
    if "sale_attachments" in tables:
        for r in db.all("SELECT a.kind, s.sale_no, s.status FROM sale_attachments a JOIN sales s ON s.id=a.sale_id"
                        " AND s.org_id=a.org_id WHERE a.org_id=? AND a.file_id=?", (org_id, file_id)):
            what = f"{'صورة تحويل' if r['kind'] == 'transfer' else 'مرفق'} لفاتورة بيع {r['sale_no'] or '-'} ({STATE_AR.get(r['status'], r['status'])})"
            uses.append(("financial" if r["kind"] == "transfer" and r["status"] == "completed" else "optional", what))
    return uses or [("optional", "ملف غير مرتبط بأي فاتورة")]


def check(archive: Path) -> dict:
    archive = Path(archive)
    try:
        info = backup.verify_archive(archive)
    except (OSError, zipfile.BadZipFile) as e:
        return {"archive": str(archive), "problems": [f"not a readable backup file: {e}"], "can_restore": False,
                "counts": None, "attachments": []}
    report = {"archive": str(archive), "problems": info["problems"], "can_restore": not info["problems"],
              "counts": info.get("counts"), "attachments": []}
    try:
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
            db = Database(backup._extract_db(z, tmp))
            try:
                for row, problem in backup._attachment_checks(db, z):
                    uses = _uses(db, row["org_id"], row["id"])
                    report["attachments"].append({
                        "source_id": row["id"], "filename": row["filename"], "size_bytes": row["size_bytes"],
                        "status": _status(problem), "problem": problem,
                        "importance": "financial" if any(i == "financial" for i, _ in uses) else "optional",
                        "used_by": [w for _, w in uses]})
            finally:
                db.close()
    except (OSError, KeyError, zipfile.BadZipFile, lzma.LZMAError, sqlite3.DatabaseError) as e:
        # unreadable or not a backup: verify_archive already said why
        report["detail_error"] = f"{type(e).__name__}: {e}"
    return report


def render(r: dict) -> list[str]:
    att = r["attachments"]
    bad = [a for a in att if a["status"] != "ok"]
    lines = [f"النسخة: {r['archive']}",
             (f"المرفقات: {len(att)} — سليمة {len(att) - len(bad)}، فيها مشكلة {len(bad)}"
              f" (مالية {sum(a['importance'] == 'financial' for a in bad)}،"
              f" اختيارية {sum(a['importance'] == 'optional' for a in bad)})")]
    for a in bad:
        lines.append(f"  [{STATUS_AR[a['status']]}] [{'مالي مهم' if a['importance'] == 'financial' else 'اختياري'}]"
                     f" {a['filename']} — {'؛ '.join(a['used_by'])} — يمنع النقل")
    other = [p for p in r["problems"] if "attachment" not in p]   # those are listed per file above
    for p in other:
        lines.append(f"  [مشكلة في النسخة] {p} — يمنع النقل")
    if "detail_error" in r:
        lines.append(f"  [تعذّر قراءة التفاصيل] {r['detail_error']}")
    lines.append("النتيجة: النسخة تنفع للنقل" if r["can_restore"]
                 else "النتيجة: النسخة لا تنفع للنقل كما هي — الاسترجاع سيرفضها ولن يغيّر أي بيانات."
                      " أرسل هذا التقرير للمطوّر قبل أي خطوة.")
    if r["problems"] and not backup.split_problems(r["problems"])[0]:
        lines.append("ملحوظة: البيانات نفسها سليمة والناقص ملفات مرفقة فقط. لو الملفات دي مش هترجع، الاسترجاع ممكن"
                     " بالأمر restore مع --allow-missing-attachments (بقرار صريح، والملفات الناقصة بتتكتب في الآخر).")
    return lines
