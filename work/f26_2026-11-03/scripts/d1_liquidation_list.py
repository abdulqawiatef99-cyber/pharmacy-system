import pandas as pd,numpy as np
o=open('order.py').read(); from openpyxl.styles import Font,Alignment,PatternFill
exec('def save'+o.split('def save')[1].split("save(X")[0])
T=pd.read_pickle('T.pkl');months=[c for c in T.columns if str(c).startswith('20')]
st=pd.read_pickle('st.pkl'); today=pd.Timestamp('2026-11-03')
T['cost_']=T.cost.fillna(T.last_cost); T['avg12']=T[months].sum(axis=1)/12; T['avg6']=T[months[-6:]].sum(axis=1)/6
T['avg']=T[['avg12','avg6']].max(axis=1)
supp=T.name.str.split('#').str[0].str.contains(r'[؀-ۿ]',na=False)
rows=[]
# per expiry batch, so near-expiry is exact
for c,g in st[st.qty>0].groupby('code'):
    if c not in T.index: continue
    r=T.loc[c]; dead=r['class'] in ('ما اتباعش سنة','متوقف (3 شهور)')
    keep=0 if dead else r.avg*3          # keep 3 months of sales on the shelf
    for _,b in g.sort_values('exp').iterrows():
        e=pd.to_datetime(b.exp,errors='coerce'); q=b.qty
        if pd.notna(e) and e<today: why='منتهي'; x=q
        elif pd.notna(e) and e<today+pd.Timedelta(days=180) and (dead or r.avg*((e-today).days/30)<q): why='بيخلص خلال 6 شهور'; x=q if dead else q-r.avg*((e-today).days/30)
        elif dead: why='راكد'; x=q
        else:
            x=max(min(q,q-keep),0); keep=max(keep-q,0); why='زيادة عن 3 شهور بيع'
        if x>=0.5 or (x>0 and why!='زيادة عن 3 شهور بيع'):
            rows.append(dict(code=c,name=r['name'],why=why,qty=round(x,2),exp=e.date() if pd.notna(e) else None,cost=round(r.cost_,2),val=round(x*r.cost_,2),price=r.price,cls=r['class'],m12=r.m12,last_sold=r.last_sold,supp=bool(supp.loc[c])))
L=pd.DataFrame(rows)
L=L[~((L.why=='زيادة عن 3 شهور بيع')&(L.val<50))]
order={'منتهي':0,'بيخلص خلال 6 شهور':1,'راكد':2,'زيادة عن 3 شهور بيع':3}
L['o']=L.why.map(order); L=L.sort_values(['o','val'],ascending=[True,False])
print(L.groupby('why').agg(n=('val','size'),v=('val','sum')).round())
print('total',round(L.val.sum()),'| drugs only',round(L[~L.supp].val.sum()))
L['حالة السعر']='لسه ما اتراجعش'
Lx=L[['code','name','why','qty','exp','cost','val','price','حالة السعر','cls','m12','last_sold']]
Lx.columns=['الكود','الصنف','السبب','الكمية المطلوب تصريفها','الصلاحية','التكلفة','القيمة بالتكلفة','سعر البيع الحالي','حالة السعر','التصنيف','شهور بيع من 12','آخر شهر بيع']
Lx.to_pickle('liq.pkl'); save(Lx,'2_أصناف_للتصريف.xlsx')
