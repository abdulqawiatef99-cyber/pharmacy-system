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
     "قول كام وحدة في العلبة للصنف ده؛ بيتكتب في launch-night\\packs.json كده: {\"packs\": {\"<الكود>\": <عدد الوحدات>}}"),
    (r"stock_source", "مش واضح أنهي جدول رصيد هو الشغال",
     "قارن إجمالي المخزون بتقرير إي ستوك وقول أنهي جدول هو الصح"),
    (r"مش موجود في جدول الأصناف", "رصيد على صنف ممسوح من إي ستوك",
     "قول الرصيد ده يتنقل على صنف جديد ولا يتساب في الأرشيف"),
    (r"مش رقم", "قيمة مكتوبة غلط في إي ستوك", "صحّح القيمة دي في إي ستوك واعمل القراية تاني"),
    (r"رقم التشغيلة موجود .* ببيانات مختلفة \(مكرر\)", "رقم التشغيلة موجود مرتين في إي ستوك نفسه ببيانات مختلفة (مكرر)",
     "قول أنهي سطر الصح؛ بيتكتب في launch-night\\packs.json كده: {\"duplicates\": {\"<رقم التشغيلة>\": 1 أو 2}}"
     " (والسطر التاني بيتساب في الأرشيف)"),
    (r"(أكتر من مرة|مرتين) في إي ستوك نفسه", "الرقم موجود أكتر من مرة في إي ستوك نفسه (مكرر)",
     "قولّي أنهي واحد الصح قبل النقل"),
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
    (r"^class$", "نوع فواتير شراء (class)",
     ("قول الرقم ده معناه إيه، في نفس ملف القرار launch-night\\packs.json:"
     " {\"classes\": {\"<الرقم>\": \"كاش\" أو \"آجل\" أو \"تحت التصريف\"}} — "
     "كاش = اتدفعت وقتها: الفواتير تاريخ في الأرشيف بس ومفيش رصيد ولا مخزون بيتغيّر؛ "
     "آجل = على حساب المورد: رصيد المورد اللي بيتنقل هو رصيده في إي ستوك (فيه الفواتير دي)؛ "
     "تحت التصريف = بضاعة المورد عندك لحد ما تتباع: البرنامج لسه مابيفرّقهاش عن بضاعتك، فرصيدها بيتنقل في مخزونك"
     " ورصيد المورد زي إي ستوك، واتعلّمت كده في التقرير"),
     "الفواتير محفوظة في الأرشيف زي ما هي؛ ولا واحدة اتحسبت بضاعة تحت التصريف ولا دين جديد"),
    (r"مالوش حساب في البرنامج", "خزنة من غير حساب في البرنامج وعليها رصيد", "قول الرصيد ده يروح أنهي حساب",
     "ما اتعملهاش حساب (قرارك)؛ رصيدها مكتوب هنا وتاريخها في الأرشيف"),
    (r"^impossible values$", "سطور بقيم مستحيلة في إي ستوك",
     "القراية النهائية تبقى وإي ستوك مقفول خالص؛ لو السطور دي فضلت بعدها، ابعت أرقام الفواتير دي من شاشة إي ستوك",
     "اتحفظت لوحدها في الأرشيف ومش داخلة في أي حساب ولا مرتجع ولا تقرير ولا مطابقة فلوس"),
    (r"درج الشيفت", "مش واضح أنهي درج هو درج الشيفت", "قول أنهي درج هو درج الشيفت",
     "ما اتنقلش لسه: الرصيد مستني خدمة الحسابات (F105)، ومكتوب هنا بمصدره"),
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


def _names(plan) -> dict:
    """E-Stock's names as the owner sees them: items by product id and by code, parties by kind and id."""
    items, codes, parties = {}, {}, {}
    for it in getattr(plan, "items", []):
        en, ar = it.get("name_en") or "", it.get("name_ar") or ""
        nm = f"«{en}» / «{ar}»" if en and ar and en != ar else (f"«{en or ar}»" if en or ar else "")
        items[str(it.get("product_id"))] = (it.get("code") or "", nm)
        if it.get("code"):
            codes[str(it["code"])] = (it["code"], nm)
    for d in list(getattr(plan, "suppliers", [])) + list(getattr(plan, "customers", [])):
        parties[(d.get("kind"), str(d.get("estock_id")))] = (d.get("code") or "", f"«{d['name']}»" if d.get("name") else "")
    return {"items": items, "codes": codes, "parties": parties}


ITEM_TABLES = {"Products", "Product_Amount", "Branches_Product_Amount", "stock"}


def _who(d: dict, names: dict | None = None) -> str:
    kind = TABLE_AR.get(d.get("table", ""), d.get("table", ""))
    names = names or {}
    t, eid, code = d.get("table", ""), str(d.get("estock_id") or ""), str(d.get("code") or "")
    hit = None
    if t == "Products":
        hit = names.get("items", {}).get(eid) or names.get("codes", {}).get(code)
    elif t in ITEM_TABLES and d.get("field") != "product_id":      # a missing product's id is not a code (#384)
        hit = names.get("codes", {}).get(code)
    elif t == "Customer":
        hit = names.get("parties", {}).get(("customer", eid))
    elif t == "Vendor":
        hit = names.get("parties", {}).get(("supplier", eid))
    if hit and hit[1]:
        # the name and code as E-Stock shows them, then E-Stock's own number (manager 20/11 00:25)
        return f"{kind} {hit[1]} (كود {hit[0] or code or '-'}، رقمه في إي ستوك {eid or '-'})"
    out = kind
    if d.get("field") == "impossible values" and kind != d.get("table"):
        out += f" ({d['table']})"                  # the E-Stock table to look at
    if d.get("estock_id"):
        out += f" رقم {d['estock_id']}"
    if d.get("code") and d.get("code") != d.get("estock_id"):
        out += f" (كود {d['code']})"
    return out


def _for_owner(d: dict) -> bool:
    return d["severity"] == "block" or (d.get("action") or "").startswith("OWNER:")


def _balances_line(plan) -> dict | None:
    """E-Stock's own balances (estock_balance) and, apart, what the owner corrected (balance - estock_balance)."""
    real = [d for d in plan.suppliers + plan.customers if not d.get("internal")
            and (d.get("estock_balance") or d.get("balance"))]
    if not real:
        return None
    sup = sum(1 for d in real if d["kind"] == "supplier")
    cus = len(real) - sup
    src = Decimal(sum(abs(d.get("estock_balance") or 0) for d in real)) / 100
    fixed = [d for d in real if d.get("balance") != d.get("estock_balance")]
    adj = Decimal(sum(abs((d.get("balance") or 0) - (d.get("estock_balance") or 0)) for d in fixed)) / 100
    done = "اتسجلت رصيد افتتاحي لكل واحد، والحسابات الداخلية برّه الإجمالي"
    if fixed:
        done += (f"؛ {len(fixed)} رصيد أكدته برقم تاني: الفرق {_money(adj)} ج اتسجل تسوية افتتاحية"
                 " (والباقي زي إي ستوك)")
    return {"what": f"أرصدة {sup} مورد و{cus} عميل" + (" اتنقلت زي ما هي في إي ستوك" if not fixed else " اتنقلت"),
            "estock": f"إجمالي الأرصدة في إي ستوك {_money(src)} ج (الأكبر الأول في openings-to-confirm.xlsx)",
            "done": done,
            "ask": "أكّد الرصيد الحقيقي لكل مورد وعميل: بعد النقل من شاشة «تصحيح الأرصدة بعد النقل» في البرنامج"
                   " (من «الخزنة والحسابات»)، أو في openings-to-confirm.xlsx قبله؛ الفرق بيتسجل تسوية افتتاحية مش دفع",
            "value": src}


PENDING_ACTIONS = ("account", "account_stopped", "first_shift_opening")


def _money_pending(plan) -> list[dict]:
    """Cash_depots balances that move with the accounts service (F105), not by this move: never hidden."""
    out = []
    for d in (plan.checks.get("money_info") or {}).get("cash_depots", []):
        try:
            bal = Decimal(str(d.get("balance") or 0))
        except InvalidOperation:
            continue
        if bal and d.get("action") in PENDING_ACTIONS and not d.get("reference_only"):
            out.append({"name": d.get("name") or d.get("estock_id"), "balance": bal,
                        "shift": d.get("action") == "first_shift_opening"})
    return out


def _money_moved_line(pending: list[dict], money: dict) -> dict:
    """The candidate schema: the account places became accounts with their openings (estock_db money step)."""
    accts = [d for d in pending if not d["shift"]]
    shift = [d for d in pending if d["shift"]]
    tot = sum(abs(d["balance"]) for d in accts)
    parts = "، ".join(f"«{d['name']}» {_money(d['balance'])} ج" for d in sorted(accts, key=lambda d: -abs(d["balance"])))
    done = (f"اتنقلت حسابات في البرنامج ({money['accounts']} حساب) برصيدها الافتتاحي من إي ستوك، واتطابقت"
            + ("" if not money.get("mismatches") else f" إلا {money['mismatches']} (القسم ٢)"))
    if shift:
        done += "؛ " + "، ".join(f"«{d['name']}» {_money(d['balance'])} ج" for d in shift) + \
            " درج الشيفت: مش حساب، يبقى نقدية أول شيفت لما تفتح الشيفت"
    return {"what": "حسابات الفلوس من إي ستوك (الخزنة والبنك والمحافظ)", "estock": f"{parts} — القيمة {_money(tot)} ج",
            "done": done, "ask": "اعدّ الخزنة وراجع كل محفظة وحساب بنك؛ أي فرق يتسجل بالجرد من شاشة الحسابات",
            "value": tot}


def _money_line(pending: list[dict]) -> dict | None:
    if not pending:
        return None
    tot = sum(abs(d["balance"]) for d in pending)
    parts = "، ".join(f"«{d['name']}» {_money(d['balance'])} ج" + (" (درج الشيفت: نقدية أول شيفت)" if d["shift"] else "")
                      for d in sorted(pending, key=lambda d: -abs(d["balance"])))
    return {"what": f"رصيد الخزنة والمحافظ ({len(pending)} حساب) في إي ستوك",
            "estock": f"{parts} — القيمة {_money(tot)} ج",
            "done": "ما اتنقلتش لسه: بتتنقل أرصدة افتتاحية بخدمة الحسابات (F105) لما تنزل؛ مكتوبة هنا بمصدرها",
            "ask": "اعدّ الخزنة وراجع المحافظ وأكّد إن الأرصدة دي صح؛ ولحد ما تتنقل ماتعتمدش على رصيدها في البرنامج",
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
    qn = rec.get("history_quarantined") or {}
    if qn:
        out.append("  سطور اتعزلت (قيم مستحيلة، مش داخلة في أي حساب): "
                   + "، ".join(f"{t} {n} سطر" for t, n in sorted(qn.items())))
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


DOUBT_MAX = 200


def _owes(d: dict) -> str:
    """The sign in words: + for a supplier = we owe him, + for a customer = he owes us (part 3 item 3)."""
    ours = (Decimal(d["estock_balance"]) > 0) == (d["kind"] == "supplier")
    return "علينا له" if ours else "لينا عنده"


def _arabic(line: str) -> str:
    """The owner's report has no English program words: E-Stock's own number reads «رقم إي ستوك»."""
    return line.replace("(E-Stock ", "(رقم إي ستوك ").replace("E-Stock", "إي ستوك")
SETTLE_CUSTOMERS = 50


def _names_and_packs(plan) -> list[str]:
    from ..catalog.normalize import normalize_text
    from .estock_db import INTERNAL_MARK

    customers = []
    for customer in plan.customers:
        name, alternate = customer["name"], customer.get("alt")
        if customer.get("internal"):
            name = name.removesuffix(INTERNAL_MARK)
        if alternate and " ".join(name.split()).casefold() != " ".join(alternate.split()).casefold():
            customers.append(f"  - {customer['code'] or customer['estock_id']} · العربي «{name}» · الإنجليزي «{alternate}»")
    packs = []
    for item in plan.items:
        if item["pack_size"] != 1:
            continue
        hints = []
        for name in (item["name_ar"], item["name_en"]):
            if name:
                # Require a count next to a unit. Age and omega variant numbers are part of the name.
                text = normalize_text(re.sub(r"\d+\s*\+", " ", name).replace("×", " x "))
                text = re.sub(r"\b(?:omega|اوميجا|اوميغا)\s*[369]\b", "", text)
                cue = _UNIT_COUNT.search(text)
                if cue:
                    count = int(cue[1] or 1) * int(cue[2])
                    if count > 1:
                        hints.append(f"«{name}» ({count})")
        if hints:
            packs.append(f"  - {item['code']} · " + " / ".join(hints))
    return [
        "", f"٤. أسماء العملاء المختلفة بين العربي والإنجليزي ({len(customers)})",
        *(customers or ["  مفيش."]),
        "", f"٥. أصناف اسمها بيدل على أكتر من وحدة وعبوتها مسجلة بوحدة واحدة ({len(packs)})",
        "  راجع عدد الوحدات في إي ستوك. الرقم اللي في الاسم للمراجعة.",
        *(packs or ["  مفيش."]),
    ]


_UNIT_COUNT = re.compile(
    r"(?<![\w./])(?:(\d{1,4})\s*x\s*)?(\d{1,4})\s*"
    r"(?:tablets?|tabs?|capsules?|caps?|strips?|ampoules?|amps?|vials?|sachets?|suppositor(?:y|ies)|"
    r"قرص|اقراص|اقرص|كبسوله|كبسولات|شريط|شرائط|امبول|امبولات|فيال|لبوس|كيس|اكياس|وحده|وحدات)\b"
)


def write(path: Path, plan, rec: dict, *, rehearsal: bool = False) -> Path:
    names = _names(plan)
    owner = [d for d in plan.dq if _for_owner(d)]
    rest = [d for d in plan.dq if not _for_owner(d)]
    blocked = bool(plan.blocking)
    moved_money = rec.get("money")                 # the candidate schema posted the accounts (estock_db money step)
    places = _money_pending(plan)
    pending = [] if moved_money else places
    if blocked:
        result = f"النتيجة: النقل وقف — فيه {len(plan.blocking)} حاجة لازم تقررها الأول، ومفيش أي حاجة اتكتبت"
    elif rec.get("ok") and pending:
        result = ("النتيجة: الأصناف والمخزون والموردين والعملاء والتاريخ اتنقلوا والمطابقة 100%؛"
                  f" رصيد الخزنة والمحافظ ({_money(sum(abs(d['balance']) for d in pending))} ج) لسه ما اتنقلش — القسم ١")
    elif rec.get("ok"):
        result = "النتيجة: كل حاجة اتنقلت والمطابقة 100% — اللي تحت محتاج تأكيدك بس"
    else:
        result = f"النتيجة: اتنقل لكن المطابقة فيها {len(rec.get('mismatches') or [])} فرق — شوف القسم ٢"
    lines = ["تقرير نقل إي ستوك للمالك"]
    if rehearsal:
        lines.append("دي تجربة على نسخة من قاعدة البرنامج. البيانات الأصلية زي ما هي.")
    lines += [result, ""]
    entries = []
    for d in owner:
        what, ask, done = _ask(d)
        if rehearsal and d["field"] == "table not read":
            ask = "اقفل التسجيل في إي ستوك وارجع لشاشة النقل واعمل تجربة جديدة. لو الجدول فضل ناقص كلم الدعم."
        v = _value(d)
        entries.append({"what": f"{_who(d, names)} — {what}",
                        "estock": (f"{FIELD_AR[d['field']]} " if d.get("field") in FIELD_AR else "") + f"{d['estock_value']}"
                                  + (f" — القيمة {_money(v)} ج" if v else "")
                                  + (f" ({_plain(d['action']).split(':')[0]})" if done else ""),
                        "done": "وقف النقل لحد قرارك (مفيش حاجة اتكتبت)" if d["severity"] == "block"
                                else (done or _plain(d["action"])),
                        "ask": ask, "value": v, "stops": d["severity"] == "block"})
    ml = _money_moved_line(places, moved_money) if moved_money else _money_line(pending)
    if ml:
        entries.append(ml)
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
    if moved_money:
        lines.append(f"  الخزنة والمحافظ: {moved_money['accounts']} حساب اتنقل بإجمالي {_money(moved_money['program_total'])} ج"
                     f" — اتطابق {moved_money['accounts'] - moved_money['mismatches']} من {moved_money['accounts']}"
                     f" ({_pct(moved_money['accounts'] - moved_money['mismatches'], moved_money['accounts'])})")
    if pending:
        lines.append(f"  الخزنة والمحافظ: {len(pending)} حساب بإجمالي {_money(sum(abs(d['balance']) for d in pending))} ج"
                     " لسه ما اتنقلتش (خدمة الحسابات F105)؛ مش داخلين في المطابقة لحد ما يتنقلوا")
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
    owed = sorted((d for d in plan.suppliers + plan.customers if d.get("estock_balance")),
                  key=lambda d: -abs(d["estock_balance"]))
    if owed:
        sup = [d for d in owed if d["kind"] == "supplier"]
        cus = [d for d in owed if d["kind"] == "customer"]
        lines += ["", "  أرصدة للتسوية (الأكبر الأول؛ الكل في openings-to-confirm.xlsx بآخر فاتورة وآخر دفعة؛"
                      " التصحيح بعد النقل من شاشة «تصحيح الأرصدة بعد النقل»):"]
        for d in sup + cus[:SETTLE_CUSTOMERS]:
            lines.append(f"    - {'مورد' if d['kind'] == 'supplier' else 'عميل'} «{d['name']}» (كود {d['code'] or '-'}):"
                         f" رصيد {_money(abs(Decimal(d['estock_balance'])) / 100)} ج ({_owes(d)}) — آخر فاتورة {d.get('last_invoice') or 'مفيش'}،"
                         f" آخر دفعة {d.get('last_payment') or 'مفيش'}")
        if len(cus) > SETTLE_CUSTOMERS:
            lines.append(f"    والباقي ({len(cus) - SETTLE_CUSTOMERS} عميل) في openings-to-confirm.xlsx")
    refs = [d for d in (plan.checks.get("money_info") or {}).get("cash_depots", []) if d.get("reference_only")]
    if refs:
        lines += ["", "  أرقام مرجع بس (مش فلوس اتنقلت):"]
        lines += [f"    - «{d['name']}» في إي ستوك {_money(d.get('balance') or 0)} ج: قرار المالك إنها مش فلوس حقيقية؛"
                  " حسابها بيبدأ بصفر ودرج أول شيفت = اللي تعدّه ليلة النقل" for d in refs]
    doubt = [d for d in plan.dq if d["table"] == "Products" and d["severity"] in ("review", "block")
             and not _for_owner(d)]
    if doubt:
        lines += ["", f"  أصناف مشكوك فيها ({len(doubt)} سطر؛ اتنقلت زي ما هي في إي ستوك، الكل في data-quality.csv):"]
        for d in sorted(doubt, key=lambda d: (-_value(d), d["code"]))[:DOUBT_MAX]:
            v = _value(d)
            lines.append(f"    - {_who(d, names)}: {FIELD_AR.get(d['field'], d['field'])} {d['estock_value']}"
                         + (f" — القيمة {_money(v)} ج" if v else "") + f" — {_plain(d['action'])}")
        if len(doubt) > DOUBT_MAX:
            lines.append(f"    والباقي ({len(doubt) - DOUBT_MAX}) في data-quality.csv")
    if rehearsal:
        lines += _names_and_packs(plan)
    path.write_text("\n".join(_arabic(x) for x in lines) + "\n", encoding="utf-8-sig")
    return path
