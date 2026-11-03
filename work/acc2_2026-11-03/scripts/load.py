import openpyxl,glob,datetime,pandas as pd,warnings;warnings.filterwarnings('ignore')
def rows(f): return list(openpyxl.load_workbook(f,read_only=True).active.iter_rows(values_only=True))
# sales
S=[]
for f in sorted(glob.glob('claude_sales_*.xlsx')):
    m=f[13:20]
    for r in rows(f)[1:]:
        if r[8] is None: continue
        S.append(dict(month=m,code=int(r[8]),name=r[7],co=r[6],qty=r[5] or 0,bal=r[4],price=r[3],rev=r[2],cost=r[1]))
S=pd.DataFrame(S)
# stock
st=[]
for r in rows('claude_stock_expiry_2026-11-03.xlsx')[2:]:
    if not isinstance(r[10],(int,float)): continue
    st.append(dict(code=int(r[10]),name=r[9],co=r[8],exp=r[6],qty=r[5],cost=r[4],price=r[3],tcost=r[2]))
st=pd.DataFrame(st)
# purchases
P=[];hdr=None
for r in rows('claude_purchases_2025-11-01_to_2026-11-03.xlsx'):
    if r[0]=='الموظف': hdr=dict(date=r[13],type=r[21],inv=r[23]);continue
    if hdr and isinstance(r[12],(int,float)) and r[11]:
        P.append(dict(**hdr,code=int(r[12]),name=r[11],exp=r[9],qty=r[8],bonus=r[7],ret=r[6],sale=r[4],cost=r[3],tax=r[2],disc=r[0]))
P=pd.DataFrame(P)
S.to_pickle('S.pkl');st.to_pickle('st.pkl');P.to_pickle('P.pkl')
print(S.groupby('month').agg(n=('code','size'),q=('qty','sum')))
print('stock rows',len(st),'codes',st.code.nunique(),'qty',st.qty.sum(),'cost',round((st.qty*st.cost).sum()))
print('purch lines',len(P),'invoices',P.inv.nunique(),P.date.min(),P.date.max())
print(P.type.value_counts())
