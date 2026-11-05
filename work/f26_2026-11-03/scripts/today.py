import pandas as pd, numpy as np, math
D='/mnt/user-data/attachments/de409fd0-b429-5963-a4ee-4c3949cb1762/'
st=pd.read_pickle('stock_new.pkl'); bal=st.groupby('code').qty.sum()
t=pd.read_pickle('t_all.pkl'); t.index=t.index.astype(int)
a=pd.read_pickle('alloc_v2.pkl'); k=pd.read_pickle('kashkool.pkl')
s=pd.read_excel(D+'3dbdeb2f-________________1-11____________.xlsx'); s=s[pd.to_numeric(s['ك.الصنف'],errors='coerce').notna()]
s['code']=s['ك.الصنف'].astype(int); nov=s.groupby('code')['كمية البيع'].sum()
t['bal_now']=bal.reindex(t.index).fillna(0); t['nov']=nov.reindex(t.index).fillna(0)
def need(r,days=7):
    if r['months']<2: return 0
    return max(0,math.ceil(r['avg']/30*days-r['bal_now']-1e-9))
t['wk_new']=t.apply(need,axis=1)
kc=set(k.code)
rows={}
for c,r in a.iterrows():
    bn=bal.get(c,0.0); base=t.loc[c,'wk_new'] if c in t.index else 0
    q=max(int(r.wk),base) if c in kc else (base if c in t.index and t.loc[c,'months']>=2 else int(r.wk))
    rows[c]=dict(name=r['name'],wk=q,bal=bn,src='امبارح'+(' + كشكول' if c in kc else ''))
# new: zero now / need, not in yesterday list
for c,r in t[(t.wk_new>0)&(~t.index.isin(a.index))].iterrows():
    rows[c]=dict(name=r['name'],wk=int(r.wk_new),bal=r.bal_now,src='جديد (اتباع من 1/11)')
# new kashkool (code, qty, note)
NK=[(9393,1,'نيفروميد ابتسام 2/11'),(26571,2,'روسيتور 20 سعيد جلهوم 2/11 (علبة ونص)'),(1319,2,'ازروليد 2/11'),(3195,1,'بانادول اكيوت 2/11'),
    (5786,1,'جاست ريج 200 احمد عزت 2/11 (شريط)'),(2149,1,'كاندالكان 8 احمد عزت 2/11'),(29147,1,'اومنيفورا 3/11'),(29946,1,'ابيكوبريد 5 رغد 3/11'),
    (4999,1,'ايزوجاست 40 رغد 3/11 (شريط)'),(5342,1,'فليكتور رغد 3/11 (2 شريط)'),(29489,1,'دي دب 10000 رغد 3/11 (شريط)')]
pn=pd.concat([t['name'],st.drop_duplicates('code').set_index('code')['name'],pd.read_pickle('purch.pkl').drop_duplicates('code').set_index('code')['name']]); pn=pn[~pn.index.duplicated()]
for c,q,note in NK:
    bn=bal.get(c,0.0); base=t.loc[c,'wk_new'] if c in t.index else 0
    short=q-max(bn-(t.loc[c,'avg']/30*7 if c in t.index else 0),0)
    add=math.ceil(short-0.25) if short>0.25 else 0
    if c in rows:
        rows[c]['wk']=max(rows[c]['wk'],base+add); rows[c]['src']+=' + كشكول جديد'; rows[c]['note']=note
    else:
        rows[c]=dict(name=pn.get(c,'?'),wk=base+add,bal=bn,src='كشكول جديد',note=note)
o=pd.DataFrame(rows).T; o.index.name='code'; o['wk']=o.wk.astype(int)
print(len(o),'zero-qty lines',(o.wk==0).sum()); print(o.src.value_counts())
print(o[o.src.str.contains('كشكول جديد')].to_string())
print(o[(o.wk==0)].head(30).to_string())
o.to_pickle('o_today.pkl')
