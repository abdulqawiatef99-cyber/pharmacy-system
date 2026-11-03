import pandas as pd, numpy as np, json, math, sys
o=pd.read_pickle('o_today.pkl'); o=o[~o.acc].copy()
o=o.drop([4999],errors='ignore')   # Ezogast != Oxidogast
t=pd.read_pickle('t_all.pkl'); t.index=t.index.astype(int)
po=pd.read_pickle('po_df.pkl').set_index('code')
p=pd.read_pickle('purch.pkl'); p=p[p.supplier!='جرد']
last=p.sort_values('date').groupby('code').last()
MAP={'مخزن النهضه':'N','مخزن ابن خلدون':'K','الشركة الوطنية للصيادلة':'W','ابن سينا':'CO','فارما اوفرسيز':'CO'}
p['s']=p.supplier.map(MAP)
hist=p.dropna(subset=['s']).sort_values('date').groupby(['code','s']).profit_pct.last().unstack()
# yesterday i'Supply
yest={}
for f in ['isup_res.csv','isup_res2.csv']:
    for l in open(f):
        a=l.strip().split(',')
        if len(a)<9 or a[1]=='X': continue
        g=lambda x: float(x) if x not in ('','None') else None
        yest[int(a[0])]=dict(pub=g(a[2]),N=g(a[5]),K=g(a[6]),W=g(a[7]),best=g(a[4]))
# today i'Supply
tod={}
for code,offs in json.load(open('isup_today.json')):
    if not offs: continue
    d=dict(pub=offs[0][1],N=None,K=None,W=None,best=max(x[2] for x in offs),bestwh=max(offs,key=lambda x:x[2])[0])
    for wh,pub,disc in offs:
        k='N' if 'نهض' in wh else 'K' if 'خلدون' in wh else 'W' if 'وطني' in wh else None
        if k and d[k] is None: d[k]=disc
    tod[code]=d
iscm={int(x.split('|')[0]):(float(x.split('|')[1]),float(x.split('|')[2]),float(x.split('|')[3])) for x in open('is_res.txt').read().split()}
R=0.03; MIN_EGP=5.0; TH_CO=1.0
rows=[]
for c,r in o.iterrows():
    s=tod.get(c); src="i'Supply النهارده"
    if s is None: s=yest.get(c); src="i'Supply امبارح"
    if s is None: s={}; src='تاريخ الشراء'
    N=s.get('N'); K=s.get('K'); W=s.get('W')
    if s:
        N=N-2 if N is not None else None; K=K+1 if K is not None else None
    else:
        h=hist.loc[c] if c in hist.index else {}
        N,K,W=[(float(h[k]) if k in h and pd.notna(h[k]) else None) for k in 'NKW']
    co=None
    if c in iscm and iscm[c][2]>=3.9: co=round((1-iscm[c][1]/iscm[c][0])*100,1)
    elif c in po.index and po.loc[c,'score']>=1.9 and 10<=po.loc[c,'disc']<=45: co=float(po.loc[c,'disc'])
    elif c in hist.index and 'CO' in hist and pd.notna(hist.loc[c,'CO']): co=float(hist.loc[c,'CO'])
    pub=s.get('pub') or (last.loc[c,'sale'] if c in last.index else None)
    avg=t.loc[c,'avg'] if c in t.index else 0
    def cash_wins(x):
        if pub is None or N is None: return True
        days=min(((r.bal or 0)+r.wk)/(avg/30),90) if avg>0 else 90
        gain=(x-N)/100*pub*r.wk; carry=pub*(1-x/100)*r.wk*R*days/30
        return gain-carry>=MIN_EGP
    best=None
    if N is not None:
        best=('النهضة',N)
        for nm,x in (('ابن خلدون',K),('الوطنية',W)):
            if x is not None and x>best[1] and cash_wins(x): best=(nm,x)
    else:
        c2=[(nm,x) for nm,x in (('ابن خلدون',K),('الوطنية',W)) if x is not None]
        if c2: best=max(c2,key=lambda z:z[1])
    if co is not None and (best is None or co>=best[1]-TH_CO): rec=('ابن سينا',co)
    elif best: rec=best
    else: rec=('غير محدد',None)
    rows.append(dict(code=c,name=r['name'],wk=int(r.wk),bal=r.bal,avg=avg,pub=pub,N=N,K=K,W=W,CO=co,best_other=s.get('best'),src=src,rec=rec[0],disc=rec[1],kash='كشكول' in r.src,zero=(r.bal or 0)<1,note=r.get('note') if isinstance(r.get('note'),str) else ''))
d=pd.DataFrame(rows).set_index('code')
d['m2']=[max(math.ceil(a/30*14-(b or 0)-1e-9),w) if a>0 else w for a,b,w in zip(d.avg,d.bal,d.wk)]
d['m4']=[max(math.ceil(a/30*30-(b or 0)-1e-9),w) if a>0 else w for a,b,w in zip(d.avg,d.bal,d.wk)]
d['cost']=np.where(d.pub.notna()&d.disc.notna(), d.wk*d.pub*(1-d.disc.astype(float)/100), np.nan)
for wh in ('ابن خلدون','الوطنية'):
    sub=d[d.rec==wh]
    if 0<len(sub)<=2 and sub.N.notna().all():
        gain=((sub.disc-sub.N)/100*sub.pub*sub.wk).sum()
        if gain<20: d.loc[sub.index,'rec']='النهضة'; d.loc[sub.index,'disc']=sub.N
d['cost']=np.where(d.pub.notna()&d.disc.notna(), d.wk*d.pub*(1-d.disc.astype(float)/100), np.nan)
d.to_pickle('alloc_today.pkl')
print(d.groupby('rec').agg(n=('wk','size'),pcs=('wk','sum'),cost=('cost','sum')).round(0).to_string())
print(d.src.value_counts().to_dict(), 'kash',d.kash.sum(),'zero',d.zero.sum())
