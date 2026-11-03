import openpyxl, sys, datetime, pandas as pd
wb=openpyxl.load_workbook(sys.argv[1]); ws=wb.active
sup=None; inv=None; rows=[]; odd=[]
for r in ws.iter_rows(values_only=True):
    v=[x for x in r if x is not None]
    if not v: continue
    if 'الاسم' in v and 'الكود' in v and len(v)==4:
        sup=v[0]; supcode=v[2]; continue
    if 'الموظف' in v:
        i=v.index('م'); h=v[i+1:i+13]
        inv={'date':h[1],'inv_net':h[2],'type':h[9],'inv_no':h[10]}; continue
    if len(v)==13 and isinstance(v[-1],(int,float)) and isinstance(v[-2],str):
        rows.append(dict(supplier=sup,supcode=supcode,**inv,profit_pct=v[0],cost=v[3],sale=v[4],bonus_val=v[5],ret_qty=v[6],bonus=v[7],qty=v[8],exp=v[9],unit=v[10],name=v[11],code=v[12],tax=v[2]))
    elif len(v)==12 and isinstance(v[-1],(int,float)) and isinstance(v[-2],str) and inv:
        rows.append(dict(supplier=sup,supcode=supcode,**inv,profit_pct=v[0],cost=v[3],sale=v[4],bonus_val=v[5],ret_qty=v[6],bonus=v[7],qty=v[8],exp=None,unit=v[9],name=v[10],code=v[11],tax=v[2]))
    else: odd.append(v)
df=pd.DataFrame(rows)
df.to_pickle(sys.argv[2])
print(len(df)); print(df.groupby('supplier').agg(lines=('code','size'),invs=('inv_no','nunique'),first=('date','min'),last=('date','max')).sort_values('lines',ascending=False).to_string())
print('odd',len(odd)); [print(o) for o in odd if not (('الموظف' in o) or ('الاسم' in o))][:0]
import collections; print(collections.Counter(len(o) for o in odd))
print('tax>0',(df.tax>0).sum(),'bonus>0',(df.bonus>0).sum(),'cost<=0',(df.cost<=0).sum(),'sale<=0',(df.sale<=0).sum(), 'chk',((df.sale-df.cost-df.tax)/df.sale*100-df.profit_pct).abs().max())
