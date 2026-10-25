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
    approver = getattr(ctx, "approver_id", None) if ctx is not None else None
    if approver:
        after = ({"approved_by": approver} if after is None else
                 {**after, "approved_by": approver} if isinstance(after, dict) else
                 {"value": after, "approved_by": approver})
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


ACTION_LABELS = {
    "attendance.fix": "تعديل حضور", "auth.login_failed": "دخول فشل", "auth.pin_failed": "رقم سري غلط",
    "backup.settings": "إعدادات النسخ الاحتياطي", "cash.drawer_open": "فتح الدرج", "cash.shift_close": "قفل شيفت",
    "cash.shift_open": "فتح شيفت", "category.save": "حفظ قسم", "count.approve": "اعتماد جرد", "count.cancel": "إلغاء جرد",
    "count.create": "بدء جرد", "customer.create": "عميل جديد", "customer.payment": "تحصيل من عميل",
    "customer.refill_remove": "شيل من الطلب الشهري", "customer.refill_save": "طلب شهري", "customer.update": "تعديل عميل",
    "customer.whatsapp_reminder": "تذكير واتساب", "customer.contract": "ربط عميل بتعاقد",
    "contract.create": "تعاقد جديد", "contract.update": "تعديل تعاقد", "entitlement.set": "تفعيل خاصية", "expense.approve": "قرار مصروف",
    "expense.record": "تسجيل مصروف", "expense_schedule.create": "بند مصروف مجدول", "expense_schedule.skip": "تخطي مصروف مجدول",
    "expense_schedule.update": "تعديل مصروف مجدول", "import.analyze": "تحليل ملف استيراد", "import.apply": "تطبيق استيراد",
    "import.decide": "قرار في الاستيراد", "import.discard": "إلغاء استيراد", "item.alias_remap": "ربط اسم صنف",
    "item.barcode_add": "إضافة باركود", "item.barcode_remove": "شيل باركود", "item.category": "تغيير قسم صنف",
    "item.create": "صنف جديد", "item.shortage": "علامة ناقص", "item.update": "تعديل صنف", "org.create": "إنشاء الصيدلية",
    "payment_channel.save": "مكنة / محفظة", "payroll.advance": "سلفة", "payroll.approve": "اعتماد مرتب", "payroll.pay": "صرف مرتب",
    "payroll.set_pay": "إعداد مرتب", "print.labels": "طباعة ملصقات", "print.receipt": "طباعة إيصال",
    "procurement.consumption_import": "رفع مبيعات للطلبية", "procurement.offer_eval": "تقييم عرض", "purchase.approve": "اعتماد فاتورة شراء",
    "purchase.cancel": "إلغاء فاتورة شراء", "purchase.header_edit": "تعديل رأس فاتورة شراء", "purchase.import": "رفع فاتورة شراء",
    "purchase.issue_ack": "قبول فرق في فاتورة شراء", "purchase.line_add": "إضافة سطر شراء", "purchase.line_delete": "حذف سطر شراء",
    "purchase.line_edit": "تعديل سطر شراء", "recall.decide": "قرار سحب", "recall.matched": "مطابقة سحب", "recall.notice_add": "منشور سحب",
    "role.discount_limit": "حد خصم نوع", "roles.sync": "تحديث الأدوار", "safety.ingredients_set": "مواد فعالة",
    "safety.ingredients_verify": "اعتماد مواد فعالة", "safety.rule_approve": "اعتماد قاعدة سلامة", "safety.rule_create": "قاعدة سلامة",
    "safety.rule_disable": "تعطيل قاعدة سلامة", "safety.source_add": "مصدر سلامة", "sale.attach": "صورة تحويل",
    "sale.complete": "بيع", "sale.return": "مرتجع", "sale.transfer_review": "مراجعة تحويل", "sale.void": "إلغاء فاتورة بيع",
    "settings.line_order": "ترتيب الفاتورة", "settings.receipt": "شكل الإيصال", "settings.return_window": "مدة المرتجع",
    "supplier.create": "مورد جديد", "supplier.return": "مرتجع للمورد", "stock.adjust": "تسوية رصيد", "supplier.payment": "دفعة لمورد", "supplier.update": "تعديل مورد", "system.network": "الموبايل على الواي فاي",
    "system.update": "تحديث البرنامج", "user.active": "إيقاف/تشغيل مستخدم", "user.create": "مستخدم جديد",
    "user.discount_limit": "حد خصم شخص", "user.password_change": "تغيير كلمة السر", "user.password_reset": "إعادة كلمة السر",
    "user.permission": "صلاحية شخص", "user.pin_set": "رقم سري", "user.reset_permissions": "رجوع الصلاحيات للافتراضي", "user.role": "تغيير النوع",
}
GROUPS = {"sale": "البيع", "purchase": "الشراء", "cash": "الشيفت والدرج", "user": "المستخدمين", "settings": "الإعدادات",
          "item": "الأصناف", "expense": "المصاريف", "supplier": "الموردين", "customer": "العملاء", "count": "الجرد",
          "payroll": "الرواتب", "auth": "الدخول", "stock": "تسويات المخزون"}


def listing(db: Database, ctx: Context, date_from: str, date_to: str, user_id: str | None = None,
            group: str | None = None) -> dict:
    """Who did what, for the owner (audit.read). Read-only; the log itself can never change."""
    from datetime import date as _d

    from ..core.errors import ValidationFailed
    from ..sales.service import local_day_bounds
    ctx.require("audit.read")
    try:
        d1, d2 = _d.fromisoformat(date_from), _d.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختار الفترة")
    q = ("SELECT a.at, a.action, a.entity_type, a.entity_id, a.before_json, a.after_json, a.ip, u.full_name user_name"
         " FROM audit_log a LEFT JOIN users u ON u.id=a.user_id WHERE a.org_id=? AND a.at>=? AND a.at<?")
    p: list = [ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]]
    if user_id:
        q += " AND a.user_id=?"
        p.append(user_id)
    if group:
        if group not in GROUPS:
            raise ValidationFailed("نوع غير معروف")
        q += " AND (a.action LIKE ? ESCAPE '\\' OR a.action LIKE ? ESCAPE '\\')"
        p += [group + ".%", group + "\\_%"]
    q += " ORDER BY a.at DESC LIMIT 5000"
    rows = []
    for r in db.all(q, tuple(p)):
        d = dict(r)
        d["label"] = ACTION_LABELS.get(d["action"], d["action"])
        rows.append(d)
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows, "groups": GROUPS, "truncated": len(rows) >= 5000}
