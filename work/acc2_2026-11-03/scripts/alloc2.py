# Re-allocation with owner rules (21:19): Ibn Khaldun real discount = i'Supply +1; Nahda real = i'Supply -2 (40-day credit).
import pandas as pd,numpy as np
o=open('order.py').read(); from openpyxl.styles import Font,Alignment,PatternFill
exec('def save'+o.split('def save')[1].split("save(X")[0])
A=pd.read_pickle('alloc.pkl')
for i,r in A.iterrows():
    opt={}
    if pd.notna(r.get('IS')): opt['ابن سينا']=20.0 if r.get('tax_q')==True else r.IS
    if pd.notna(r.get('النهضة')): opt['النهضة']=r['النهضة']-2
    if pd.notna(r.get('ابن خلدون')): opt['ابن خلدون']=r['ابن خلدون']+1
    if pd.notna(r.get('الوطنية')): opt['الوطنية']=r['الوطنية']
    if opt: b=max(opt,key=opt.get); A.at[i,'sup2']=b; A.at[i,'disc2']=opt[b]
    else: A.at[i,'sup2']='اسأل عليه'
A.to_pickle('alloc2.pkl')
M=A[['code','name','qty','sup2','disc2','IS','النهضة','ابن خلدون','الوطنية','top','pub','price_sys','last_disc','ملاحظات']].copy()
M.columns=['الكود','الصنف','الكمية','المورد المقترح (بعد قواعد +1/-2)','الخصم الفعلي %','ابن سينا % (ظاهر)','النهضة % (i\'Supply)','ابن خلدون % (i\'Supply)','الوطنية % (i\'Supply)','أعلى مخزن في i\'Supply','سعر الجمهور','سعرنا','خصم آخر شراء %','ملاحظات']
save(M.sort_values([M.columns[3],'الصنف']),'1ب_طلبية_4-11_بعد_قواعد_الموردين.xlsx')
for s in ['ابن سينا','النهضة','ابن خلدون','الوطنية']:
    d=A[A.sup2==s]; save(pd.DataFrame({'الصنف':d.name.str.replace(r'\s*#.*','',regex=True),'الكمية':d.qty.astype(int)}),f'مسودة2_{s}_4-11.xlsx')
print(A.sup2.value_counts())
