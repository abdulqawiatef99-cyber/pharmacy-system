"""Deterministic checks for a purchase invoice (all money in piasters).

Two jobs:
  derive_line()   fill values that follow arithmetically from others
                  (net = (public - 2 x margin) x (1 - discount), total = net x qty + tax;
                  margin = the distributor's fixed margin per pack, B3, 0 for most lines;
                  integers only, rounded half up; with an inferred net the total
                  is qty x public x (1 - discount) rounded once),
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
ACKNOWLEDGEABLE = {"total_mismatch", "line_total_mismatch", "net_price_mismatch",
                  "short_expiry", "slow_before_expiry"}      # U2-22: approved only with a written reason
UNUSUAL = {"unusual_qty", "unusual_public_price", "unusual_last_cost"}
ACKNOWLEDGEABLE |= UNUSUAL
MAX_NUMBER = 2**53 - 1  # Exact across the browser, JSON and SQLite.
NUMERIC_FIELDS = {
    "qty": ("الكمية", 1, MAX_NUMBER),
    "bonus_qty": ("البونص", 0, MAX_NUMBER),
    "public_price": ("سعر الجمهور", 1, MAX_NUMBER),
    "unit_net_price": ("سعر الصيدلي", 1, MAX_NUMBER),
    "discount_bp": ("الخصم", 0, 10000),
    "distributor_margin": ("هامش الموزع", 0, MAX_NUMBER),
    "tax_amount": ("الضريبة", 0, MAX_NUMBER),
    "line_total": ("إجمالي السطر", 0, MAX_NUMBER),
}

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
    "margin_too_large": "هامش الموزع أكبر من نص سعر الجمهور: مستحيل، راجع الهامش",
    "public_price_changed": "سعر الجمهور مختلف عن السعر المسجل للصنف",
    "expired": "الصنف منتهي الصلاحية: الصلاحية لازم تكون النهارده أو بعده",
    "short_expiry": "صلاحية قصيرة: اكتب سبب قبولها",
    "slow_before_expiry": "الكمية أكبر من اللي هيتباع قبل ما يقرب ينتهي: اكتب السبب",
    "expiry_missing": "تاريخ الصلاحية غير موجود",
    "low_confidence": "قيمة غير مؤكدة من القراءة — راجعها",
    "unreadable": "قيمة موجودة في المستند لكن لم تُقرأ — أدخلها يدويًا",
    "duplicate_line": "الصنف مكرر في الفاتورة بنفس التشغيلة والصلاحية",
    "duplicate_line_no_batch": "الصنف مكرر في الفاتورة بنفس الصلاحية (من غير تشغيلة)",
    "same_item_other_expiry": "الصنف موجود في سطر تاني بصلاحية مختلفة (عادي: تشغيلتين)",
    "extra_discount_too_large": "الخصم الإضافي على الفاتورة أكبر من إجمالي الأصناف",
    "negative_total": "إجمالي الفاتورة بالسالب — راجع الخصم الإضافي وإجماليات الأسطر",
    "tax_above_total": "الضريبة المشمولة أكبر من إجمالي السطر",
    "negative_amount": "قيمة بالسالب (سعر أو ضريبة أو إجمالي) — صحّحها، ومرتجع المورد يُسجَّل من شاشة المرتجعات",
    "positive_cost": "تكلفة السطر المدفوع لازم تكون أكبر من صفر؛ سجّل الكمية المجانية في البونص",
    "unusual_qty": "إجمالي الكمية لهذا الصنف أعلى من المعتاد. راجع الكمية واكتب سبب قبولها",
    "unusual_public_price": "فيه فرق سعر كبير عن سعر الجمهور. راجع السعر واكتب سبب قبوله",
    "unusual_last_cost": "تكلفة الشراء مختلفة بشكل ملحوظ عن آخر تكلفة في الفرع. راجع السعر واكتب السبب",
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


def half_up_div(n: int, d: int) -> int:
    """n / d as an integer, rounded half up, with integers only (float round() is half to even)."""
    return (2 * n + d) // (2 * d)


def margin_of(line: dict) -> int:
    """The distributor's fixed margin per pack (B3), piasters; 0 when the line has none."""
    return line.get("distributor_margin") or 0


def _base(pub: int | None, line: dict) -> int | None:
    """What the discount applies to: the public price less the margin twice (the paper's «m+m», B3)."""
    return None if pub is None else pub - 2 * margin_of(line)


def _line_net(line: dict, net: int, qty: int, sources: dict) -> int:
    base, discount = _base(line.get("public_price"), line), line.get("discount_bp")
    if sources.get("unit_net_price") == "inferred" and base is not None and base >= 0 and discount is not None:
        return half_up_div(qty * base * (10000 - discount), 10000)
    return net * qty


def derive_line(line: dict) -> dict:
    """Mutates and returns line (DB-shaped dict). Only arithmetic consequences."""
    src, conf = _j(line.get("field_sources")), _j(line.get("field_confidence"))
    pub, disc, net = line.get("public_price"), line.get("discount_bp"), line.get("unit_net_price")
    qty, tax, total = line.get("qty"), line.get("tax_amount"), line.get("line_total")
    base = _base(pub, line)

    def put(k, v):
        line[k] = v
        src[k] = "inferred"
        conf[k] = 1.0

    if base is not None and base < 0:
        base = None             # a margin above half the public price: refused by validate(), nothing derived
    if net is None and base is not None and disc is not None:
        put("unit_net_price", half_up_div(base * (10000 - disc), 10000))
    elif disc is None and base and net is not None and net <= base:
        put("discount_bp", half_up_div((base - net) * 10000, base))
    net = line.get("unit_net_price")
    if total is None and net is not None and qty:
        added_tax = 0 if line.get("tax_included") == 1 else tax or 0
        put("line_total", _line_net(line, net, qty, src) + added_tax)
    line["field_sources"], line["field_confidence"] = src, conf
    return line


def line_tolerance(expected: int, qty: int | None) -> int:
    return max(5, (qty or 0), round(abs(expected) * 0.005))


def effective_amount(line: dict) -> int | None:
    """What the pharmacy pays for the line (piasters): the line's amount + qty x the distributor's margin (B3)."""
    amount = line_amount(line)
    return None if amount is None else amount + (line.get("qty") or 0) * margin_of(line)


def line_amount(line: dict) -> int | None:
    """The line as the paper prints it (tax included), without the distributor's margin."""
    net, qty, tax, total = (line.get("unit_net_price"), line.get("qty"),
                            line.get("tax_amount") or 0, line.get("line_total"))
    included = line.get("tax_included")
    if total is None:
        return None if net is None or not qty else net * qty + (0 if included == 1 else tax)
    if included is not None:
        return total
    if net is not None and qty and tax:
        pre = net * qty
        # printed total excludes tax: it is within tolerance of the pre-tax amount and closer to it than to the
        # taxed one (a small tax inside the tolerance of both was dropped from the invoice, #324 S26)
        if abs(total - pre) <= line_tolerance(pre, qty) and abs(total - pre) < abs(total - (pre + tax)):
            return total + tax
    return total


def computed_total(invoice: dict, lines: list[dict]) -> int | None:
    amounts = [effective_amount(l) for l in lines]
    if not lines or any(a is None for a in amounts):
        return None
    return sum(amounts) - (invoice.get("extra_discount") or 0) + (invoice.get("extra_tax") or 0)


def numeric_message(field: str) -> str:
    label, minimum, maximum = NUMERIC_FIELDS[field]
    if field == "discount_bp":
        return f"{label} لازم يكون من 0% إلى 100%"
    return f"{label} لازم يكون رقم صحيح من {minimum} إلى {maximum}"


def impossible_line(line: dict, today: date):
    """Cross-field refusals shared by the typed line and approval of imported or older drafts."""
    public, net = line.get("public_price"), line.get("unit_net_price")
    if public is not None and margin_of(line) * 2 > public and margin_of(line) > 0:
        yield "margin_too_large", "distributor_margin", {"public_price", "distributor_margin"}
    if line.get("tax_included") == 1 and line.get("line_total") is not None \
            and (line.get("tax_amount") or 0) > line["line_total"]:
        yield "tax_above_total", "tax_amount", {"tax_amount", "tax_included", "line_total", "qty", "unit_net_price",
                                             "public_price", "discount_bp", "distributor_margin"}
    if line.get("expiry_date") and date.fromisoformat(line["expiry_date"]) < today:
        yield "expired", "expiry_date", {"expiry_date"}
    sources = _j(line.get("field_sources"))
    if public == 0 or (net == 0 and sources.get("unit_net_price") != "inferred") or effective_amount(line) == 0:
        yield "positive_cost", "unit_net_price", {"qty", "public_price", "unit_net_price", "discount_bp",
                                                "tax_amount", "tax_included", "line_total", "distributor_margin"}


def line_total_mismatch(line: dict) -> dict | None:
    total, net, qty = (line.get(k) for k in ("line_total", "unit_net_price", "qty"))
    if total is None or net is None or not qty:
        return None
    tax, included = line.get("tax_amount") or 0, line.get("tax_included")
    if included is None:
        pre = net * qty
        expected = pre + tax
        tol = line_tolerance(expected, qty)
        mismatch = abs(total - expected) > tol and abs(total - pre) > tol
    else:
        sources = _j(line.get("field_sources"))
        expected = _line_net(line, net, qty, sources) + (0 if included else tax)
        # A split or merge preserves a validated total and assigns any rounding difference.
        tol = line_tolerance(expected, qty) if sources.get("line_total") == "allocated" else 0
        mismatch = abs(total - expected) > tol
    return {"expected": expected, "actual": total} if mismatch else None


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

    seen: dict[str, list] = {}
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
        # a file can carry what the screen refuses: a negative price gave a negative unit and average cost
        for f in ("public_price", "unit_net_price", "tax_amount", "line_total", "distributor_margin"):
            if (l.get(f) or 0) < 0:
                add("error", "negative_amount", lid, f)
        if disc is not None and not 0 <= disc <= 10000:
            add("error", "negative_amount", lid, "discount_bp")
        if net is None:
            add("error", "price_missing", lid, "unit_net_price")
        base = _base(pub, l)
        if base is not None and base < 0:
            base = None
        for code, field, _ in impossible_line(l, today):
            add("error", code, lid, field)
        if pub is not None and net is not None:
            if net + margin_of(l) > pub:      # the pack's cost (with the distributor's margin) above the public price
                add("warning", "net_above_public", lid, "unit_net_price")
            if disc is not None and base is not None and _j(l.get("field_sources")).get("unit_net_price") != "inferred":
                exp = half_up_div(base * (10000 - disc), 10000)
                if abs(exp - net) > max(2, round(pub * 0.005)):
                    add("warning", "net_price_mismatch", lid, "unit_net_price",
                        expected=exp, actual=net)
        mismatch = line_total_mismatch(l)
        if mismatch:
            add("error", "line_total_mismatch", lid, "line_total", **mismatch)
        item = items.get(l.get("item_id") or "")
        if item and pub is not None and item.get("public_price") is not None \
                and item["public_price"] != pub:
            add("warning", "public_price_changed", lid, "public_price",
                old=item["public_price"], new=pub)
        exp_date = l.get("expiry_date")
        if exp_date:
            d = date.fromisoformat(exp_date)
            if 0 <= (d - today).days < short_expiry_days:
                # details without the day count: an acceptance must not expire at midnight (independent review)
                add("error", "short_expiry", lid, "expiry_date", expiry=exp_date, qty=qty)
                issues[-1].message += f" ({(d - today).days} يوم)"
        elif item is None or item.get("is_drug") != 0:
            add("warning", "expiry_missing", lid, "expiry_date")
        # U2-3: the same item twice. Same batch and expiry: a real duplicate (merge it when the prices match too);
        # no batch on either: said so; another expiry: normal (two lots), information only
        if l.get("item_id"):
            batch = (l.get("batch_no") or "").strip().upper()
            earlier = seen.get(l["item_id"], [])
            twin = next((o for o in earlier if o.get("expiry_date") == exp_date
                         and (o.get("batch_no") or "").strip().upper() == batch), None)
            if twin:
                same = all(twin.get(k) == l.get(k) for k in ("public_price", "discount_bp", "unit_net_price",
                                                             "expiry_precision")) \
                    and margin_of(twin) == margin_of(l) \
                    and twin.get("qty") is not None and l.get("qty") is not None
                add("warning", "duplicate_line" if batch else "duplicate_line_no_batch", lid, None,
                    other_line=twin["id"], mergeable=same)
            elif earlier and all(o.get("expiry_date") != exp_date for o in earlier):
                add("info", "same_item_other_expiry", lid, None, other_line=earlier[0]["id"])
            seen.setdefault(l["item_id"], []).append(l)

    for f in ("extra_discount", "extra_tax", "declared_total"):
        if (invoice.get(f) or 0) < 0:
            add("error", "negative_amount", None, f)
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

    apply_acknowledgements(invoice, issues)
    return issues


def apply_acknowledgements(invoice: dict, issues: list[Issue]) -> None:
    # An acceptance covers the figures it accepted: a changed difference needs a new one (#324 S25).
    acks = {(a["code"], a.get("line_id") or "", json.dumps(a.get("details"), sort_keys=True)) for a in
            json.loads(invoice.get("acknowledged_issues") or "[]")}
    for i in issues:
        if i.code in ACKNOWLEDGEABLE and (i.code, i.line_id or "", json.dumps(i.details, sort_keys=True)) in acks:
            i.acknowledged = True


def blocking(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error" and not i.acknowledged]
