"""Deterministic checks for a purchase invoice (all money in piasters).

Two jobs:
  derive_line()   fill values that follow arithmetically from others
                  (net = public x (1 - discount), total = net x qty + tax),
                  tagging them source='inferred'. Never invents a quantity,
                  batch or expiry.
  validate()      list issues. level 'error' blocks approval; 'warning' is
                  shown but does not block; some errors can be acknowledged
                  with a written reason by a user allowed to approve.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from datetime import date

LOW_CONFIDENCE = 0.8
ACKNOWLEDGEABLE = {"total_mismatch", "line_total_mismatch", "net_price_mismatch"}

MESSAGES = {
    "supplier_missing": "حدد المورد",
    "invoice_no_missing": "رقم الفاتورة غير موجود",
    "invoice_date_missing": "تاريخ الفاتورة غير موجود",
    "duplicate_invoice": "رقم الفاتورة نفسه من المورد نفسه مسجل من قبل",
    "possible_duplicate": "توجد فاتورة بالأصناف والكميات نفسها من المورد نفسه — تأكد أنها غير مكررة",
    "no_lines": "الفاتورة لا تحتوي على أصناف",
    "total_mismatch": "إجمالي الفاتورة المطبوع لا يساوي مجموع الأصناف",
    "total_missing": "إجمالي الفاتورة المطبوع غير موجود — لا يمكن مقارنة الحسابات",
    "unmatched": "الصنف غير مربوط بصنف في قاعدة البيانات",
    "match_unconfirmed": "تأكيد مطابقة الصنف مطلوب",
    "qty_missing": "الكمية غير موجودة أو غير مقروءة",
    "qty_invalid": "الكمية يجب أن تكون أكبر من صفر",
    "bonus_invalid": "البونص لا يكون بالسالب",
    "price_missing": "لا يوجد سعر صافي ولا (سعر جمهور + خصم) لحساب التكلفة",
    "net_price_mismatch": "السعر الصافي لا يساوي سعر الجمهور بعد الخصم",
    "line_total_mismatch": "إجمالي السطر لا يساوي الكمية × السعر الصافي",
    "net_above_public": "السعر الصافي أعلى من سعر الجمهور",
    "public_price_changed": "سعر الجمهور مختلف عن السعر المسجل للصنف",
    "expired": "الصنف منتهي الصلاحية",
    "short_expiry": "صلاحية قصيرة",
    "expiry_missing": "تاريخ الصلاحية غير موجود",
    "low_confidence": "قيمة غير مؤكدة من القراءة — راجعها",
    "unreadable": "قيمة موجودة في المستند لكن لم تُقرأ — أدخلها يدويًا",
    "duplicate_line": "الصنف مكرر في الفاتورة بنفس التشغيلة",
    "extra_discount_too_large": "الخصم الإضافي على الفاتورة أكبر من إجمالي الأصناف",
    "negative_total": "إجمالي الفاتورة بالسالب — راجع الخصم الإضافي وإجماليات الأسطر",
}


@dataclass
class Issue:
    level: str
    code: str
    message: str
    line_id: str | None = None
    field: str | None = None
    details: dict = dc_field(default_factory=dict)
    acknowledged: bool = False

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _j(v) -> dict:
    if isinstance(v, dict):
        return v
    return json.loads(v or "{}")


def derive_line(line: dict) -> dict:
    """Mutates and returns line (DB-shaped dict). Only arithmetic consequences."""
    src, conf = _j(line.get("field_sources")), _j(line.get("field_confidence"))
    pub, disc, net = line.get("public_price"), line.get("discount_bp"), line.get("unit_net_price")
    qty, tax, total = line.get("qty"), line.get("tax_amount"), line.get("line_total")

    def put(k, v):
        line[k] = v
        src[k] = "inferred"
        conf[k] = 1.0

    if net is None and pub is not None and disc is not None:
        put("unit_net_price", round(pub * (10000 - disc) / 10000))
    elif disc is None and pub and net is not None and net <= pub:
        put("discount_bp", round((pub - net) * 10000 / pub))
    net = line.get("unit_net_price")
    if total is None and net is not None and qty:
        put("line_total", net * qty + (tax or 0))
    line["field_sources"], line["field_confidence"] = src, conf
    return line


def line_tolerance(expected: int, qty: int | None) -> int:
    return max(5, (qty or 0), round(abs(expected) * 0.005))


def effective_amount(line: dict) -> int | None:
    """What the pharmacy pays for the line (piasters)."""
    net, qty, tax, total = (line.get("unit_net_price"), line.get("qty"),
                            line.get("tax_amount") or 0, line.get("line_total"))
    if total is None:
        return None if net is None or not qty else net * qty + tax
    if net is not None and qty and tax:
        pre = net * qty
        if abs(total - pre) <= line_tolerance(pre, qty) < abs(total - (pre + tax)):
            return total + tax        # printed total excludes tax
    return total


def computed_total(invoice: dict, lines: list[dict]) -> int | None:
    amounts = [effective_amount(l) for l in lines]
    if not lines or any(a is None for a in amounts):
        return None
    return sum(amounts) - (invoice.get("extra_discount") or 0) + (invoice.get("extra_tax") or 0)


def validate(invoice: dict, lines: list[dict], *, items: dict[str, dict], today: date,
             short_expiry_days: int, total_tolerance: int,
             duplicate_no: bool = False, duplicate_content: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    add = lambda level, code, line_id=None, f=None, **d: issues.append(  # noqa: E731
        Issue(level, code, MESSAGES.get(code, code), line_id, f, d))

    if not invoice.get("supplier_id"):
        add("error", "supplier_missing")
    if not invoice.get("invoice_no"):
        add("warning", "invoice_no_missing")
    if not invoice.get("invoice_date"):
        add("warning", "invoice_date_missing")
    if duplicate_no:
        add("error", "duplicate_invoice")
    if duplicate_content:
        add("warning", "possible_duplicate")
    if not lines:
        add("error", "no_lines")

    seen: dict[tuple, str] = {}
    for l in lines:
        lid = l["id"]
        src, conf = _j(l.get("field_sources")), _j(l.get("field_confidence"))
        for f, c in conf.items():
            if src.get(f) == "user":
                continue
            if c == 0.0:
                add("error", "unreadable", lid, f)
            elif c < LOW_CONFIDENCE:
                add("error", "low_confidence", lid, f, confidence=c)
        if not l.get("item_id"):
            add("error", "unmatched", lid, "item_id")
        elif l.get("match_status") == "suggested":
            add("error", "match_unconfirmed", lid, "item_id")
        qty = l.get("qty")
        if qty is None:
            add("error", "qty_missing", lid, "qty")
        elif qty <= 0:
            add("error", "qty_invalid", lid, "qty")
        if (l.get("bonus_qty") or 0) < 0:
            add("error", "bonus_invalid", lid, "bonus_qty")
        pub, disc, net = l.get("public_price"), l.get("discount_bp"), l.get("unit_net_price")
        if net is None:
            add("error", "price_missing", lid, "unit_net_price")
        if pub is not None and net is not None:
            if net > pub:
                add("warning", "net_above_public", lid, "unit_net_price")
            if disc is not None and _j(l.get("field_sources")).get("unit_net_price") != "inferred":
                exp = round(pub * (10000 - disc) / 10000)
                if abs(exp - net) > max(2, round(pub * 0.005)):
                    add("warning", "net_price_mismatch", lid, "unit_net_price",
                        expected=exp, actual=net)
        total = l.get("line_total")
        if total is not None and net is not None and qty:
            pre = net * qty
            tax = l.get("tax_amount") or 0
            tol = line_tolerance(pre + tax, qty)
            if abs(total - (pre + tax)) > tol and abs(total - pre) > tol:
                add("error", "line_total_mismatch", lid, "line_total", expected=pre + tax,
                    actual=total)
        item = items.get(l.get("item_id") or "")
        if item and pub is not None and item.get("public_price") is not None \
                and item["public_price"] != pub:
            add("warning", "public_price_changed", lid, "public_price",
                old=item["public_price"], new=pub)
        exp_date = l.get("expiry_date")
        if exp_date:
            d = date.fromisoformat(exp_date)
            if d < today:
                add("error", "expired", lid, "expiry_date")
            elif (d - today).days < short_expiry_days:
                add("warning", "short_expiry", lid, "expiry_date", days=(d - today).days)
        elif item is None or item.get("is_drug") != 0:
            add("warning", "expiry_missing", lid, "expiry_date")
        key = (l.get("item_id"), (l.get("batch_no") or "").upper(), exp_date)
        if l.get("item_id") and key in seen:
            add("warning", "duplicate_line", lid, None, other_line=seen[key])
        seen[key] = lid

    comp = computed_total(invoice, lines)
    if comp is not None:      # a negative total would give a negative supplier balance and unit cost
        lines_amount = sum(effective_amount(l) for l in lines)
        if (invoice.get("extra_discount") or 0) > lines_amount:
            add("error", "extra_discount_too_large", None, "extra_discount",
                extra_discount=invoice.get("extra_discount"), lines_amount=lines_amount)
        elif comp < 0:
            add("error", "negative_total", None, None, computed=comp)
    declared = invoice.get("declared_total")
    if declared is None:
        add("warning", "total_missing")
    elif comp is not None:
        tol = max(total_tolerance, round(abs(declared) * 0.001))
        if abs(comp - declared) > tol:
            add("error", "total_mismatch", None, "declared_total", declared=declared,
                computed=comp, difference=declared - comp)

    acks = {a["code"] + "|" + (a.get("line_id") or "") for a in
            json.loads(invoice.get("acknowledged_issues") or "[]")}
    for i in issues:
        if i.code in ACKNOWLEDGEABLE and f"{i.code}|{i.line_id or ''}" in acks:
            i.acknowledged = True
    return issues


def blocking(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error" and not i.acknowledged]
