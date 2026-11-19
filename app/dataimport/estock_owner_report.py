"""owner-report.txt: the one page the owner reads after the trial run and on launch night (manager 19/11).

Arabic, plain words. First what needs him (a line that stops the move, or one marked for the owner), largest
value first; each says what it is, E-Stock's figure, what the program did and the one thing he must decide or
confirm. Then how much matched and how much did not, so the error margin is always in sight. Then everything
else grouped by kind with counts (the full list stays in data-quality.csv and reconciliation.txt).
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TABLE_AR = {
    "Products": "صنف", "Product_Amount": "رصيد تشغيلة", "Branches_Product_Amount": "رصيد تشغيلة (نسخة الفرع)",
    "Customer": "عميل", "Vendor": "مورد", "Purchase_header": "فاتورة شراء", "Sales_header": "فاتورة بيع",
    "sales lines": "سطر بيع", "Cash_depots": "خزنة أو محفظة", "Gedo_customers": "كشوف حساب العملاء",
    "Gedo_Vendors": "كشوف حساب الموردين", "Gedo_branches": "حساب الصيدلية التانية", "stock": "المخزون",
    "export": "قراية إي ستوك", "Shortcoming": "النواقص", "Stores": "مخزن", "Companys": "شركة",
    "Cash_disk_close": "قفل الخزنة", "Gedo_Financial": "دفتر الفلوس", "Back_sales_header": "مرتجع بيع",
    "Branches_sales_details": "سطور البيع", "Sales_details": "سطور البيع", "Purchase_details": "سطور الشراء",
    "Product_groups": "مجموعة أصناف", "Product_units": "وحدة", "Users": "موظف", "Employees": "موظف",
}

FIELD_AR = {"product_unit1_2": "معامل الوحدة الوسطى", "product_unit1_3": "معامل الوحدة الصغرى", "amount": "الكمية",
            "customer_current_money": "الرصيد", "vendor_current_money": "الرصيد", "customer_max_money": "حد الآجل",
            "total": "الرصيد", "line value": "قيمة السطر", "last_close": "اتقفل فيه", "balance": "الرصيد"}

# (how to recognise the line, what it is in plain words, the one thing the owner decides). First match wins.
ASKS: list[tuple] = [   # (pattern, what, ask[, what the program did])
    (r"table not read", "جدول ماتقراش من إي ستوك",
     "شغّل estock-db-import.bat تاني (بيعيد قراية الجدول ده لوحده). لو فضل، اقفل إي ستوك واعمل القراية من الأول"),
    (r"--packs", "عدد الوحدات في العلبة مش واضح في إي ستوك",
     "قول كام وحدة في العلبة للصنف ده (بيتكتب في ملف القرار packs.json)"),
    (r"stock_source", "مش واضح أنهي جدول رصيد هو الشغال",
     "قارن إجمالي المخزون بتقرير إي ستوك وقول أنهي جدول هو الصح"),
    (r"مش موجود في جدول الأصناف", "رصيد على صنف ممسوح من إي ستوك",
     "قول الرصيد ده يتنقل على صنف جديد ولا يتساب في الأرشيف"),
    (r"مش رقم", "قيمة مكتوبة غلط في إي ستوك", "صحّح القيمة دي في إي ستوك واعمل القراية تاني"),
    (r"اتقرا مرتين", "القراية اتعملت وإي ستوك شغال", "اقفل إي ستوك خالص واعمل القراية تاني"),
    (r"حد آجل أكبر", "حد آجل أكبر من اللي البرنامج يشيله", "قول حد الآجل الحقيقي للعميل ده"),
    (r"كسر قرش", "رصيد فيه كسر قرش", "قول الرصيد الحقيقي بالقرش، أو سيب التقريب نص لفوق (الافتراضي)"),
    (r"confirmed balance", "رصيد اتغيّر في إي ستوك بعد ما أكدته", "أكّد الرصيد تاني على التقرير الجديد"),
    (r"balance sign", "إشارة الأرصدة شكلها بالعكس", "افتح كشف حساب 3 عملاء أو موردين في إي ستوك وقول الإشارة الصح"),
    (r"مش موجود في قايمة إي ستوك", "التاريخ فيه أرقام عملاء أو موردين مش في القايمة",
     "مفيش حاجة لازمة: السطور محفوظة. لو تعرف الأرقام دي مين قول", "السطور محفوظة في الأرشيف زي ما هي"),
    (r"فاتورة شراء متعلقة ومورّدها", "فاتورة شراء متعلقة من غير مورد معروف", "قول الفاتورة دي من أنهي مورد",
     "ما اتعملتش مسودة؛ مكتوبة هنا وفي الأرشيف بس"),
    (r"سطر بيع كبير", "سطر بيع بقيمة كبيرة", "اتأكد إن الرقم ده صح في إي ستوك (غالبًا غلطة إدخال)",
     "السطر محفوظ في الأرشيف زي ما هو (تاريخ بس، مابيأثرش على المخزون ولا الأرصدة)"),
    (r"^class$", "نوع فواتير شراء (class)", "قول الرقم ده معناه إيه: كاش ولا آجل ولا تحت التصريف",
     "الفواتير محفوظة في الأرشيف زي ما هي؛ ولا واحدة اتحسبت بضاعة تحت التصريف ولا دين جديد"),
    (r"مالوش حساب في البرنامج", "خزنة من غير حساب في البرنامج وعليها رصيد", "قول الرصيد ده يروح أنهي حساب",
     "ما اتعملهاش حساب (قرارك)؛ رصيدها مكتوب هنا وتاريخها في الأرشيف"),
    (r"درج الشيفت", "مش واضح أنهي درج هو درج الشيفت", "قول أنهي درج هو درج الشيفت",
     "اتعمل حساب عادي برصيده لحد ما تقول"),
]


def _value(d: dict) -> Decimal:
    try:
        return abs(Decimal(d.get("value_egp") or 0))
    except InvalidOperation:
        return Decimal(0)


def _money(v) -> str:
    try:
        d = Decimal(str(v).replace(",", ""))
    except InvalidOperation:
        return str(v)
    q = d.quantize(Decimal("0.01"), ROUND_HALF_UP)        # the project's rounding, not format()'s half-even
    return f"{q if q else Decimal('0.00'):,}"            # never "-0.00"


def _plain(action: str) -> str:
    return re.sub(r"^OWNER:\s*", "", action or "").strip()


def _ask(d: dict) -> tuple[str, str, str | None]:
    hay = f"{d.get('field', '')}\n{d.get('action', '')}"
    for pat, what, ask, *done in ASKS:
        if re.search(pat, d.get("field", "") if pat.startswith("^") else hay):   # ^...$ = the field itself
            return what, ask, (done[0] if done else None)
    return _plain(d.get("action", "")).split(":")[0], "راجع السطر ده وقول القرار", None


def _who(d: dict) -> str:
    kind = TABLE_AR.get(d.get("table", ""), d.get("table", ""))
    out = kind
    if d.get("estock_id"):
        out += f" رقم {d['estock_id']}"
    if d.get("code") and d.get("code") != d.get("estock_id"):
        out += f" (كود {d['code']})"
    return out


def _for_owner(d: dict) -> bool:
    return d["severity"] == "block" or (d.get("action") or "").startswith("OWNER:")


def _balances_line(plan) -> dict | None:
    real = [d for d in plan.suppliers + plan.customers if not d.get("internal") and d.get("balance")]
    if not real:
        return None
    sup = sum(1 for d in real if d["kind"] == "supplier")
    cus = len(real) - sup
    tot = Decimal(sum(abs(d["balance"]) for d in real)) / 100
    return {"what": f"أرصدة {sup} مورد و{cus} عميل اتنقلت زي ما هي في إي ستوك",
            "estock": f"إجمالي الأرصدة {_money(tot)} ج (الأكبر الأول في openings-to-confirm.xlsx)",
            "done": "اتسجلت رصيد افتتاحي لكل واحد، والحسابات الداخلية برّه الإجمالي",
            "ask": "أكّد الرصيد الحقيقي لكل مورد وعميل من البرنامج بعد النقل (أو في openings-to-confirm.xlsx قبله)؛"
                   " الفرق بيتسجل تسوية افتتاحية مش دفع",
            "value": tot}


def _pct(a, b) -> str:
    try:
        a, b = Decimal(a), Decimal(b)
    except (InvalidOperation, TypeError):
        return "?"
    if b == 0:
        return "100%" if a == 0 else "0%"
    return f"{(min(a, b) / max(a, b) * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)}%".replace(".0%", "%")


def _margin(plan, rec: dict) -> list[str]:
    out = []
    if "items" not in rec:
        return ["  النقل وقف قبل ما يكتب حاجة: المطابقة بتتعمل بعد ما تقرر اللي في أول التقرير."]
    it = rec["items"]
    out.append(f"  الأصناف: إي ستوك {it['estock']}، البرنامج {it['program']} — اتطابق {min(it['estock'], it['program'])}"
               f" من {it['estock']} ({_pct(it['program'], it['estock'])})")
    st = rec.get("stock", {})
    if st:
        est, prog = Decimal(st["estock_cost"] or 0), Decimal(st["program_cost"] or 0)
        listed = Decimal(st["cost_difference_listed_in_data_quality"] or 0)
        unit = Decimal(st["cost_difference_from_unit_cost_rounding"] or 0)
        loose = prog - est - listed - unit
        out.append(f"  المخزون: {st['items_with_stock']} صنف عليه رصيد، {st['rows_posted']} تشغيلة. القيمة بالتكلفة: إي ستوك"
                   f" {_money(est)} ج، البرنامج {_money(prog)} ج — الفرق {_money(prog - est)} ج")
        out.append(f"    الفرق ده: {_money(listed)} ج مكتوب سطر سطر في القسم ٣ (سالب اتشال، تكلفة ناقصة اتصلحت)،"
                   f" {_money(unit)} ج تقريب تكلفة الوحدة، و{_money(loose)} ج مش مفسَّر"
                   f" — المطابقة بعد الفروق المكتوبة {_pct(prog - listed - unit, est)}")
    for k, label in (("suppliers", "الموردين"), ("customers", "العملاء")):
        r = rec.get(k)
        if not r:
            continue
        bad = sum(1 for m in rec["mismatches"] if m["what"] == k and m["code"])
        n = r["estock"]
        out.append(f"  {label}: إي ستوك {n}، البرنامج {r['program']} — اتطابق {n - bad} من {n} ({_pct(n - bad, n)})."
                   f" إجمالي الرصيد: إي ستوك {_money(r['estock_balance_total'])} ج، البرنامج {_money(r['program_balance_total'])} ج"
                   f" — الفرق {_money(Decimal(r['program_balance_total']) - Decimal(r['confirmed_balance_total']))} ج"
                   + (f"، منه تسوية افتتاحية أكدتها {_money(r['opening_adjustment_total'])} ج"
                      if Decimal(r["opening_adjustment_total"]) else ""))
    hist = rec.get("history") or {}
    if hist:
        same = sum(1 for v in hist.values() if v.get("archive") in (None, v["csv"]))
        rows = sum(v["csv"] for v in hist.values())
        out.append(f"  التاريخ: {len(hist)} جدول ({rows:,} سطر) اتحفظ في الأرشيف — اتطابق {same} من {len(hist)}"
                   f" ({_pct(same, len(hist))})")
    rnd = plan.checks.get("rounding") or {}
    if rnd:
        n = sum(v["values"] for v in rnd.values())
        diff = sum(Decimal(str(v["difference"])) for v in rnd.values())
        out.append(f"  تقريب كسور القرش (نص لفوق): {n} قيمة، الفرق كله {diff} ج")
    mm = rec.get("mismatches") or []
    if mm:
        tot = sum(Decimal(m["value"] or 0) for m in mm)
        out.append(f"  عدم مطابقة: {len(mm)} سطر بقيمة {_money(tot)} ج — الأكبر:")
        out += [f"    - {m['name'] or m['code']}: {m['detail']} ({_money(m['value'])} ج)" for m in mm[:5]]
        if len(mm) > 5:
            out.append(f"    والباقي ({len(mm) - 5}) في reconciliation.txt")
    else:
        out.append("  عدم مطابقة: مفيش — كل رقم في البرنامج زي إي ستوك أو فرقه مكتوب سطر سطر.")
    return out


def _group_key(d: dict) -> str:
    label = TABLE_AR.get(d["table"], d["table"])
    parts = [x.strip() for x in _plain(d["action"]).split("←")[0].split(":")]
    head = parts[1] if len(parts) > 1 and parts[0] == label else parts[0]     # «النواقص: النواقص» once
    return f"{label}: {head}"


def write(path: Path, plan, rec: dict) -> Path:
    owner = [d for d in plan.dq if _for_owner(d)]
    rest = [d for d in plan.dq if not _for_owner(d)]
    blocked = bool(plan.blocking)
    if blocked:
        result = f"النتيجة: النقل وقف — فيه {len(plan.blocking)} حاجة لازم تقررها الأول، ومفيش أي حاجة اتكتبت"
    elif rec.get("ok"):
        result = "النتيجة: كل حاجة اتنقلت والمطابقة 100% — اللي تحت محتاج تأكيدك بس"
    else:
        result = f"النتيجة: اتنقل لكن المطابقة فيها {len(rec.get('mismatches') or [])} فرق — شوف القسم ٢"
    lines = ["تقرير نقل إي ستوك للمالك", result, ""]
    entries = []
    for d in owner:
        what, ask, done = _ask(d)
        v = _value(d)
        entries.append({"what": f"{_who(d)} — {what}",
                        "estock": (f"{FIELD_AR[d['field']]} " if d.get("field") in FIELD_AR else "") + f"{d['estock_value']}"
                                  + (f" — القيمة {_money(v)} ج" if v else "")
                                  + (f" ({_plain(d['action']).split(':')[0]})" if done else ""),
                        "done": "وقف النقل لحد قرارك (مفيش حاجة اتكتبت)" if d["severity"] == "block"
                                else (done or _plain(d["action"])),
                        "ask": ask, "value": v, "stops": d["severity"] == "block"})
    bl = _balances_line(plan)
    if bl and not blocked:
        bl["estock"] += f" — القيمة {_money(bl['value'])} ج"
        entries.append(bl)
    entries.sort(key=lambda e: (not e.get("stops"), -e["value"]))      # what stops the move first, then by value
    lines.append(f"١. محتاج قرارك أو تأكيدك ({len(entries)})، اللي موقّف النقل الأول، وبعده الأكبر قيمة:")
    if not entries:
        lines.append("  مفيش.")
    for n, e in enumerate(entries, start=1):
        lines += [f"  {n}) {e['what']}", f"     في إي ستوك: {e['estock']}", f"     البرنامج عمل: {e['done']}",
                  f"     المطلوب منك: {e['ask']}"]
    lines += ["", "٢. المطابقة (قد إيه اتطابق وقد إيه لأ):"] + _margin(plan, rec)
    groups: dict[str, list] = defaultdict(list)
    for d in rest:
        groups[_group_key(d)].append(d)
    sev = {"review": "للمراجعة", "fixed": "اتصلح لوحده", "info": "للعلم"}
    lines += ["", f"٣. الباقي حسب النوع ({len(rest)} سطر، كلهم في data-quality.csv؛ مش محتاجين قرار منك):"]
    for key, ds in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        tot = sum(_value(d) for d in ds)
        kinds = "، ".join(sorted({sev.get(d["severity"], d["severity"]) for d in ds}))
        lines.append(f"  - {key}: {len(ds)} سطر ({kinds})" + (f"، قيمتهم {_money(tot)} ج" if tot else ""))
    if not rest:
        lines.append("  مفيش.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path
