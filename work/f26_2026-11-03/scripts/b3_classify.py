import pandas as pd,numpy as np,datetime as dt
S=pd.read_pickle('S.pkl');st=pd.read_pickle('st.pkl');P=pd.read_pickle('P.pkl')
months=sorted(S.month.unique())
piv=S.pivot_table(index='code',columns='month',values='qty',aggfunc='sum').reindex(columns=months).fillna(0)
names=pd.concat([st[['code','name']],S[['code','name']],P[['code','name']]]).drop_duplicates('code').set_index('code').name
stk=st.groupby('code').agg(stock=('qty','sum'),cost=('cost','max'),price=('price','max'),exp_min=('exp','min'))
P=P.sort_values('date')
lp=P.groupby('code').agg(last_buy=('date','last'),last_disc=('disc','last'),last_cost=('cost','last'),last_exp=('exp','last'),buys=('inv','nunique'),bought=('qty','sum'))
codes=sorted(set(piv.index)|set(stk.index)|set(lp.index))
T=pd.DataFrame(index=pd.Index(codes,name='code'))
T['name']=names.reindex(codes)
T=T.join(stk).join(lp).join(piv)
T[months]=T[months].fillna(0)
T['stock']=T.stock.fillna(0)
sold=T[months]>0
T['m12']=sold.sum(1); T['q12']=T[months].sum(1)
T['m3']=sold[months[-3:]].sum(1); T['q3']=T[months[-3:]].sum(1)
T['m6']=sold[months[-6:]].sum(1)
T['last_sold']=sold.apply(lambda r: r[r].index.max() if r.any() else '',axis=1)
today=pd.Timestamp('2026-11-03')
new=(T.last_buy>=today-pd.Timedelta(days=75))
def cls(r):
    if r.m12==0: return 'جديد' if pd.notna(r.last_buy) and r.last_buy>=today-pd.Timedelta(days=75) else 'ما اتباعش سنة'
    if r.m3==0: return 'اتشرى حديثًا' if pd.notna(r.last_buy) and r.last_buy>=today-pd.Timedelta(days=75) else 'متوقف (3 شهور)'
    if r.m12>=6 and r.m3>=2: return 'منتظم'
    if r.m12>=4: return 'شبه منتظم'
    return 'متقطع'
T['class']=T.apply(cls,axis=1)
T['val']=T.stock*T.cost.fillna(T.last_cost)
# flags
fl=[]
for c,r in T.iterrows():
    f=[]
    if str(r['name']).strip().isdigit(): f.append('اسم = رقم')
    if r.stock<0: f.append('رصيد سالب')
    if r.stock>0 and abs(r.stock-round(r.stock))>1e-6: f.append('رصيد كسري')
    if pd.notna(r.price) and pd.notna(r.cost) and r.price>0 and r.price<r.cost: f.append('سعر البيع أقل من التكلفة')
    if pd.notna(r.last_disc) and (r.last_disc<5 or r.last_disc>60): f.append(f'خصم شراء غريب {r.last_disc:.0f}%')
    fl.append('، '.join(f))
T['flags']=fl
T.to_pickle('T.pkl')
print(T['class'].value_counts())
d=T[T.stock>0]
print(d.groupby('class').agg(n=('val','size'),val=('val','sum')).round())
print('flags',(T['flags']!='').sum()); print(T['flags'].str.split('، ').explode().value_counts())
