import json, pandas as pd, re
R = json.load(open('/tmp/w/r.json'))
a = pd.read_pickle('/tmp/w/alloc2.pkl').set_index('code')
QTY = {11276: 4, 5144: 3, 10024: 3}  # owner: Rani-F, Fawar fruit, Oplex several boxes
CLAIM_CD = 2.2   # Ibn Sina avg claim cash discount (regular 3 / special 2 / other 0.25)
NAHDA_ADJ = -2.0
KHALDUN_ADJ = +1.0
CREDIT_MONTH = 3.0  # owner: ~3%/month value of credit (only for info)

def seller_key(s):
    if 'النهض' in s: return 'N'
    if 'خلدون' in s: return 'K'
    if 'الوطني' in s: return 'W'
    return 'O'

rows = []
for r in R:
    code = r['code']; name = r['name']
    base = a.loc[code] if code in a.index else None
    qty = QTY.get(code, float(base.qty) if base is not None else 1)
    pub = float(base.pub) if base is not None and pd.notna(base.pub) else (r['pick']['price'] if r.get('pick') else None)
    offers = r.get('offers') or []
    best_cash = None; nahda = None
    for s, pp, ph, d in offers:
        k = seller_key(s)
        if k == 'N':
            eff = d + NAHDA_ADJ
            if nahda is None or eff > nahda[1]: nahda = (s, eff, d)
        else:
            eff = d + (KHALDUN_ADJ if k == 'K' else 0)
            if best_cash is None or eff > best_cash[1]: best_cash = (s, eff, d)
    IS = float(base.IS) if base is not None and pd.notna(base.IS) else None
    tax = bool(base.tax_q) if base is not None and pd.notna(base.tax_q) else False
    is_eff = IS + CLAIM_CD if IS is not None else None
    cands = []
    if best_cash: cands.append(('cash', best_cash[0], best_cash[1]))
    if nahda: cands.append(('nahda', 'النهضة', nahda[1]))
    if is_eff is not None: cands.append(('is', 'ابن سينا', is_eff))
    pick = max(cands, key=lambda c: c[2]) if cands else None
    flags = []
    if pub and pub >= 300: flags.append('غالي ≥300')
    if 'ثلاج' in name: flags.append('تلاجة')
    if tax and pick and pick[0] == 'is': flags.append('اتأكد من ضريبة 14%')
    ld = float(base.last_disc) if base is not None and pd.notna(base.last_disc) else None
    if pick and ld and pick[2] < ld - 0.5: flags.append(f'أقل من آخر شراء {ld:.0f}%')
    if not offers and r.get('pick') is None: flags.append('مش لاقيه على i\'Supply')
    rows.append(dict(code=code, name=name, qty=qty, pub=pub,
        cash_s=best_cash[0] if best_cash else None, cash_d=best_cash[1] if best_cash else None,
        nahda_d=nahda[1] if nahda else None, is_d=is_eff,
        sup=pick[1] if pick else 'اسأل عليه', disc=pick[2] if pick else None, kind=pick[0] if pick else None,
        flags='، '.join(flags), cands=r.get('cands')))
df = pd.DataFrame(rows)
df['value'] = df.qty * df.pub.fillna(0)
df['cost'] = df.value * (1 - df.disc.fillna(0) / 100)
# Nahda vs best cash in EGP for lines where both exist
m = df.nahda_d.notna() & df.cash_d.notna()
df['nahda_minus_cash_egp'] = None
df.loc[m, 'nahda_minus_cash_egp'] = (df.loc[m, 'value'] * (df.loc[m, 'cash_d'] - df.loc[m, 'nahda_d']) / 100).round(1)
df.to_pickle('/tmp/w/alloc3.pkl')
print(df.groupby('sup').agg(n=('code', 'size'), value=('value', 'sum'), cost=('cost', 'sum')).round(0).to_string())
print('total cost', round(df.cost.sum()))
mm = df[m]
print('lines with both nahda & cash:', len(mm), 'value', round(mm.value.sum()),
      'cash saves vs nahda (EGP, this order):', round((mm.value * (mm.cash_d - mm.nahda_d) / 100).sum(), 1))
print('nahda-picked value', round(df[df.kind == 'nahda'].value.sum()))
print(df[df.flags != ''][['name', 'qty', 'pub', 'sup', 'disc', 'flags']].to_string())
