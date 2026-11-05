import pandas as pd
s=pd.read_pickle('stock_items.pkl').set_index('code'); t=pd.read_pickle('t_all.pkl'); p=pd.read_pickle('purch.pkl'); st=pd.read_pickle('stock.pkl')
sold=t[['أغسطس','سبتمبر','أكتوبر']].fillna(0).sum(axis=1)
s['sold3']=sold.reindex(s.index).fillna(0)
pr=p[p.supplier!='جرد']
s['lastbuy']=pr.groupby('code').date.max().reindex(s.index)
s['exp']=st[st.qty>0].groupby('code').exp.min().reindex(s.index)
s['val']=s.qty*s.cost
d=s[(s.qty>0)&(s.sold3==0)].copy()
today=pd.Timestamp('2026-11-02')
d['new']=d.lastbuy>=today-pd.Timedelta(days=75)
d['exp_m']=((d.exp-today).dt.days/30.4).round(1)
print('dead',len(d),round(d.val.sum()))
print('new (bought <75d)',d.new.sum(),round(d[d.new].val.sum()))
dd=d[~d.new]
print('real dead',len(dd),round(dd.val.sum()))
for lo,hi in [(-99,0),(0,3),(3,6),(6,12),(12,99)]:
    x=dd[(dd.exp_m>lo)&(dd.exp_m<=hi)]; print('exp',lo,hi,len(x),round(x.val.sum()))
print('no exp',dd.exp_m.isna().sum())
for th in [500,200,100,50]:
    x=dd[dd.val>=th]; print('val>=',th,len(x),round(x.val.sum()))
print(dd.sort_values('val',ascending=False).head(25)[['name','qty','cost','price','val','exp_m','lastbuy']].to_string())
d.to_pickle('dead.pkl')
