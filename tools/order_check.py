"""Order validation gate (owner rules, 2026-11-04).

Every candidate line must pass these checks before it can enter a supplier file.
A line that fails is NOT dropped silently: it goes to the review sheet with the reason.

Inputs (pickles/xlsx produced by the export loaders):
  stock   : columns code, name, qty            (fresh E-Stock balance export - REQUIRED fresh)
  sales   : columns month(YYYY-MM), code, name, qty   (12 months)
  purch   : columns date, code, name, qty, disc, tax  (purchase lines)
  cand    : columns code, name, qty, pub, offers  where offers = list of (supplier, shown_disc)
  rules   : dict with keys below (edit in RULES)

Checks (order matters, all reasons are collected):
  1. in_stock        : item itself has balance > 0
  2. other_pack      : same base name (brand + strength) with another pack in stock
  3. slow_or_single  : no sale in last N months, or sales come from one big sale only
  4. shortage        : item is on the market-shortage list (owner/marketplace)
  5. excluded        : owner said don't order now
  6. vat_suspect     : company discount looks pre-VAT (e.g. Ibn Sina 29.8%) -> compare after VAT
  7. price_changed   : public price differs from our system price
  8. offer_outlier   : one seller's discount > others by >= 10 points (coding error risk, e.g. Mebo C)
Allocation (only for passing lines):
  - real discount = shown + adjustment (Nahda -2, Ibn Khaldun +1). NO assumed claim discounts.
  - pick best real discount among allowed suppliers; credit preference only if owner says so.
  - consolidation: any supplier with < MIN_LINES lines -> move each line to the next-best
    supplier already in the order; report the EGP cost of every move.
Output: review xlsx (all lines + reasons + per-supplier comparison) - shown to owner BEFORE any file is sent.
"""
import re
import pandas as pd

RULES = {
    'adj': {'النهضة': -2.0, 'ابن خلدون': +1.0},     # owner-stated real discount adjustments
    'allowed': ['ابن سينا', 'ابن خلدون', 'النهضة', 'الوطنية'],  # main suppliers
    'min_lines': 6,             # below this a supplier order is not worth a delivery
    'slow_months': 4,           # no sale in this many months -> ask owner
    'single_sale_share': 0.7,   # one month holds >= 70% of yearly qty -> single-patient risk
    'outlier_pts': 10.0,
    'min_year_qty': 3,          # fewer boxes per year -> slow item, ask owner (e.g. Cymbatex)
    'vat_suspect_disc': {'ابن سينا': [29.8]},
    'shortage': [],             # fill from owner / market list
    'excluded': [],             # owner "don't order now"
}

STRENGTH = re.compile(r'(\d+(?:[.,]\d+)?)\s*(mg|mcg|g|gm|ml|iu|%)?', re.I)
PACKWORDS = re.compile(r'\b(\d+\s*(tab|tabs|cap|caps|amp|sach|film|strip|supp)s?|#\d*strip|#unit|#صغير|#ثلاجة)\b', re.I)


def base_name(name: str) -> str:
    """Brand + first strength, without pack size: 'ROMANTIGRA 20MG 4TAB' -> 'romantigra 20mg'."""
    n = PACKWORDS.sub(' ', str(name).lower())
    words = re.findall(r'[a-z؀-ۿ]+|\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|iu|%)?', n)
    brand = next((w for w in words if re.match(r'[a-z؀-ۿ]', w)), '')
    strength = next((w.replace(' ', '') for w in words if re.match(r'\d', w)), '')
    return f'{brand} {strength}'.strip()


def check(cand, stock, sales, rules=RULES, today_month=None, arrived=None):
    """arrived: {code: qty} for invoices received but not yet entered in E-Stock."""
    stock = stock.copy(); stock['base'] = stock.name.map(base_name)
    bal = stock.groupby('code').qty.sum()
    base_bal = stock[stock.qty > 0].groupby('base').name.apply(lambda s: ', '.join(sorted(set(s))))
    months = sorted(sales.month.unique()); today_month = today_month or months[-1]
    recent = set(months[-rules['slow_months']:])
    out = []
    for _, r in cand.iterrows():
        reasons = []
        if bal.get(r.code, 0) > 0: reasons.append(f'رصيده موجود ({bal[r.code]:g})')
        b = base_name(r['name'])
        if b in base_bal.index and bal.get(r.code, 0) <= 0:
            others = base_bal[b]
            if others and others != r['name']: reasons.append(f'فيه عبوة تانية رصيد: {others}')
        if arrived and arrived.get(r.code, 0) > 0: reasons.append(f'وصل ومش متسجل ({arrived[r.code]:g})')
        s = sales[sales.code == r.code]
        if 0 < s.qty.sum() < rules['min_year_qty']: reasons.append(f'بطيء: {s.qty.sum():g} علبة في السنة')
        if s.empty or not set(s.month) & recent: reasons.append(f'ما اتباعش آخر {rules["slow_months"]} شهور')
        elif s.qty.sum() > 0 and s.groupby('month').qty.sum().max() / s.qty.sum() >= rules['single_sale_share'] and s.qty.sum() > 1:
            reasons.append('أغلب بيعه في شهر واحد (عميل واحد؟)')
        if any(k.lower() in r['name'].lower() for k in rules['shortage']): reasons.append('ناقص في السوق')
        if any(k.lower() in r['name'].lower() for k in rules['excluded']): reasons.append('انت قلت ما يتطلبش دلوقتي')
        offers = [(sup, d) for sup, d in (r.offers or []) if d is not None]
        for sup, d in offers:
            if round(d, 1) in rules['vat_suspect_disc'].get(sup, []): reasons.append(f'{sup} {d}% غالبًا قبل الضريبة')
        if len(offers) >= 2:
            ds = sorted(d for _, d in offers)
            if ds[-1] - ds[-2] >= rules['outlier_pts']: reasons.append('خصم شاذ عند مورد واحد (تكويد غلط؟)')
        out.append({**r.to_dict(), 'base': b, 'reasons': '، '.join(reasons), 'ok': not reasons})
    return pd.DataFrame(out)


def allocate(df, rules=RULES):
    """df rows must have ok=True; offers = list of (supplier, shown_disc)."""
    rows = []
    for _, r in df[df.ok].iterrows():
        real = sorted(((d + rules['adj'].get(s, 0), s) for s, d in (r.offers or [])
                       if s in rules['allowed'] and d is not None), reverse=True)
        rows.append({**r.to_dict(), 'ranked': real, 'sup': real[0][1] if real else 'اسأل عليه',
                     'disc': real[0][0] if real else None, 'move_cost': 0.0})
    a = pd.DataFrame(rows)
    if a.empty: return a
    changed = True
    while changed:
        changed = False
        cnt = a.sup.value_counts()
        small = [s for s, n in cnt.items() if n < rules['min_lines'] and s != 'اسأل عليه']
        if not small: break
        weakest = min(small, key=lambda s: cnt[s])
        keep = set(cnt.index) - {weakest, 'اسأل عليه'}
        for k, r in a[a.sup == weakest].iterrows():
            alt = [(d, s) for d, s in r.ranked if s in keep]
            if alt:
                d, s = alt[0]
                a.loc[k, 'move_cost'] = round((r.disc - d) * r.qty * r.pub / 100, 1)
                a.loc[k, ['sup', 'disc']] = [s, d]
                changed = True
    return a
