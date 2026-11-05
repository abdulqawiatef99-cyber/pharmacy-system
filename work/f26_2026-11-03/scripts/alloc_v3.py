import pandas as pd, numpy as np, sys
o=pd.read_pickle('order.pkl'); t=pd.read_pickle('t_all.pkl'); po=pd.read_pickle('po_df.pkl').set_index('code')
# add notebook items
for c,q in ((9393,1),(26571,2)):
    if c not in o.index:
        r=t.loc[c].reindex(o.columns); r['wk']=q; r['rec']=None; o.loc[c]=r
isup={}
for f in sys.argv[1:]:
    for l in open(f):
        a=l.strip().split(',')
        if len(a)<9 or a[1]=='X': continue
        f_=lambda x: float(x) if x not in ('','None') else None
        isup[int(a[0])]=dict(best=f_(a[4]),N=f_(a[5]),K=f_(a[6]),W=f_(a[7]),n=int(a[8]))
TH_WH,TH_CO=2.0,1.0
R=0.03; MIN_EGP=5.0
rows=[]
for c,r in o.iterrows():
    hist={'N':r.get('النهضة'),'K':r.get('ابن خلدون'),'W':r.get('الوطنية')}
    s=isup.get(c); src='isup' if s else 'hist'
    if s:
        N=s['N']-2 if s['N'] is not None else None
        K=s['K']+1 if s['K'] is not None else None
        W=s['W']
    else:
        N,K,W=[None if pd.isna(hist[k]) else float(hist[k]) for k in 'NKW']
    p=po.loc[c] if c in po.index else None
    co=None
    if p is not None and pd.notna(p['disc']) and p['score']>=1.9 and 10<=p['disc']<=45: co=float(p['disc'])
    if co is None:
        h=[x for x in (r.get('ابن سينا'),r.get('فارما أوفرسيز')) if pd.notna(x)]
        co=max(h) if h else None; co_src='hist' if co is not None else None
    else: co_src='po'
    best=None
    pub=r['pub'] if pd.notna(r['pub']) else None
    daily=(r['avg'] or 0)/30
    days=((r['bal'] or 0)+r['wk'])/daily if daily>0 else 90
    days=min(days,90)
    def cash_wins(x):
        if pub is None: return x>=N+2
        gain=(x-N)/100*pub*r['wk']
        carry=pub*(1-x/100)*r['wk']*R*days/30
        return gain-carry>=MIN_EGP
    if N is not None:
        best=('النهضة',N)
        for nm,x in (('ابن خلدون',K),('الوطنية',W)):
            if x is not None and x>N and cash_wins(x) and x>best[1]: best=(nm,x)
    else:
        c2=[(nm,x) for nm,x in (('ابن خلدون',K),('الوطنية',W)) if x is not None]
        if c2: best=max(c2,key=lambda z:z[1])
    if co is not None and (best is None or co>=best[1]-TH_CO): rec=('ابن سينا' if True else '',co)
    elif best: rec=best
    else: rec=('غير محدد',None)
    nomarket = s is not None and s['n']==0
    rows.append(dict(code=c,name=r['name'],wk=r['wk'],bal=r['bal'],avg=r['avg'],pub=r['pub'],N=N,K=K,W=W,CO=co,co_src=co_src,src=src,rec=rec[0],disc=rec[1],nomarket=nomarket))
d=pd.DataFrame(rows).set_index('code')
d['cost']=np.where(d['pub'].notna()&d['disc'].notna(), d['wk']*d['pub']*(1-d['disc'].astype(float)/100), np.nan)
d['days']=0
# consolidation: warehouse with <=2 items & small gain -> Nahda
for wh in ('ابن خلدون','الوطنية'):
    sub=d[d.rec==wh]
    if 0<len(sub)<=2:
        gain=((sub['disc']-sub['N'].fillna(-99))/100*sub['pub']*sub['wk']).sum()
        if sub['N'].notna().all() and gain<20: d.loc[sub.index,'rec']='النهضة'; d.loc[sub.index,'disc']=sub['N']
d.to_pickle('alloc_v2.pkl')
print(d.groupby('rec').agg(n=('wk','size'),pcs=('wk','sum'),cost=('cost','sum')).round(0).to_string())
print('src', d.src.value_counts().to_dict(), 'co_src', d.co_src.value_counts().to_dict(), 'nomarket', d.nomarket.sum())
print(d[d.nomarket][['name','wk','rec']].to_string())
