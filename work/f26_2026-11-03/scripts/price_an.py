import pandas as pd, sys
it=pd.read_pickle('stock_items.pkl').set_index('code')
rows=[]
for f in sys.argv[1:]:
    for l in open(f):
        a=l.strip().split(':')
        try: rows.append((int(a[0]),float(a[1]),float(a[2])))
        except: pass
r=pd.DataFrame(rows,columns=['code','newp','score']).drop_duplicates('code').set_index('code').join(it[['name','qty','price','cost','tcost']])
r['ratio']=r.newp/r.price
ok=r[(r.score>=1.9)&(r.ratio.between(0.6,2.0))].copy()
st=ok[ok.newp>ok.price+0.5].copy(); st['gain']=(st.newp-st.price)*st.qty
print('searched',len(r),'reliable',len(ok),'stale',len(st),'gap',round(st.gain.sum()),'strong-match stale',(st.score>=3.9).sum(),'gap strong',round(st[st.score>=3.9].gain.sum()))
r.to_pickle('price_all.pkl'); st.to_pickle('stale_all.pkl')
