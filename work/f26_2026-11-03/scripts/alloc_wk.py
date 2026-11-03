import pandas as pd, numpy as np, math, sys
exec(open('build6.py').read().split("thin=Side")[0].replace("D=sys.argv[1]; out=sys.argv[2]","D='/mnt/user-data/attachments/de409fd0-b429-5963-a4ee-4c3949cb1762/'; out=None"))
p=pd.read_pickle('purch.pkl'); p=p[p.supplier!='جرد']
MAP={'مخزن النهضه':'النهضة','مخزن ابن خلدون':'ابن خلدون','الشركة الوطنية للصيادلة':'الوطنية','ابن سينا':'ابن سينا','فارما اوفرسيز':'فارما أوفرسيز'}
p['s']=p.supplier.map(MAP).fillna('أخرى')
last=p.sort_values('date').groupby(['code','s']).last()
D=last['profit_pct'].unstack()   # latest effective discount per supplier
DT=last['date'].unstack()
lastsup=p.sort_values('date').groupby('code').last()
t=t.copy(); t.index=t.index.astype(int)
t=t.join(D,how='left'); 
t['last_sup']=lastsup['supplier'].map(lambda x: MAP.get(x,x)).reindex(t.index)
t['last_disc']=lastsup['profit_pct'].reindex(t.index)
t['last_date']=lastsup['date'].reindex(t.index)
t['pub']=lastsup['sale'].reindex(t.index)
TH_WH=2.0; TH_CO=1.0
def pick(r):
    g=lambda k: r.get(k) if k in r and pd.notna(r.get(k)) else None
    n,k,w=g('النهضة'),g('ابن خلدون'),g('الوطنية')
    co=max([x for x in (g('ابن سينا'),g('فارما أوفرسيز')) if x is not None],default=None)
    wh=None
    if n is not None:
        wh=('النهضة',n)
        for nm,x in (('ابن خلدون',k),('الوطنية',w)):
            if x is not None and x>n+TH_WH and x>wh[1]: wh=(nm,x)
    else:
        c=[(nm,x) for nm,x in (('ابن خلدون',k),('الوطنية',w)) if x is not None]
        if c: wh=max(c,key=lambda z:z[1])
    if co is not None and (wh is None or co>=wh[1]-TH_CO): return ('ابن سينا',co)
    if wh: return wh
    return ('غير محدد',np.nan)
pk=t.apply(pick,axis=1,result_type='expand'); t['rec']=pk[0]; t['rec_disc']=pk[1]
t['co']=t[[c for c in ('ابن سينا','فارما أوفرسيز') if c in t]].max(axis=1)
order=t[t['wk'].notna()].copy()
order['ucost']=np.where(order['pub'].notna()&order['rec_disc'].notna(), order['pub']*(1-order['rec_disc']/100), order['ucost'])
order['cost']=order['wk']*order['ucost']
print('items in sales',len(t),'with purchase hist',t['last_sup'].notna().sum())
print('order lines',len(order)); print(order.groupby('rec').agg(n=('sug','size'),cost=('cost','sum')).round(0).to_string())
print(order[order.rec=='غير محدد']['last_sup'].value_counts(dropna=False).head(8).to_string())
order.to_pickle('order.pkl'); t.to_pickle('t_all.pkl')
