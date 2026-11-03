import pandas as pd,numpy as np
exec(open('order.py').read().split("def save")[0].split("T=pd.read_pickle")[0])
from openpyxl.styles import Font,Alignment,PatternFill
src=open('order.py').read(); exec('def save'+src.split('def save')[1].split("save(X")[0])
T=pd.read_pickle('T.pkl');months=[c for c in T.columns if str(c).startswith('20')]
today=pd.Timestamp('2026-11-03')
T['val']=T.stock*T.cost.fillna(T.last_cost)
ex=pd.to_datetime(T.exp_min,errors='coerce')
D=T[(T.stock>0)&T['class'].isin(['ما اتباعش سنة','متوقف (3 شهور)'])].copy()
def tier(r):
    e=pd.to_datetime(r.exp_min,errors='coerce')
    if pd.notna(e) and e<today: return '1 منتهي'
    if pd.notna(e) and e<today+pd.Timedelta(days=180): return '2 ينتهي خلال 6 شهور'
    if r.val>=100: return '3 قيمته ≥100 ج وصلاحيته كويسة'
    return '4 قيمته أقل من 100 ج'
act={'1 منتهي':'مرتجع منتهي للمورد/الشركة (اسأل كل مورد عن نظامه)','2 ينتهي خلال 6 شهور':'يتعرض على صيدليات تانية أو مرتجع قبل ما يخلص','3 قيمته ≥100 ج وصلاحيته كويسة':'لستة للصيدليات التانية + مرتجع للمورد لو بيقبل','4 قيمته أقل من 100 ج':'يفضل على الرف، وما يتطلبش تاني'}
D['الشريحة']=D.apply(tier,axis=1); D['الإجراء المقترح']=D['الشريحة'].map(act)
D=D.sort_values(['الشريحة','val'],ascending=[True,False])
Dx=D.reset_index()[['code','الشريحة','name','stock','exp_min','cost','val','m12','last_sold','last_buy','الإجراء المقترح']]
Dx.columns=['الكود','الشريحة','الصنف','الرصيد','أقرب صلاحية','التكلفة','قيمة الرصيد','شهور بيع من 12','آخر شهر بيع','آخر شراء','الإجراء المقترح']
Dx['أقرب صلاحية']=pd.to_datetime(Dx['أقرب صلاحية'],errors='coerce').dt.date; Dx['آخر شراء']=pd.to_datetime(Dx['آخر شراء']).dt.date
Dx=Dx.round(2); save(Dx,'2_الرواكد.xlsx')
print(D.groupby('الشريحة').agg(n=('val','size'),v=('val','sum')).round())
fix={'رصيد كسري':'جرد الصنف وتصحيح الرصيد','تكلفة = سعر':'شوف الفاتورة: لو من صيدلية تمام، لو من مورد صحّح التكلفة','تكلفة ≈ صفر':'لو بونص: تمام، بس الأحسن يتسجل بونص جوه نفس الفاتورة','الاسم رقم':'تعديل اسم الصنف وسعره','خصم':'راجع الفاتورة الأصلية','سعر البيع أقل':'راجع سعر البيع وسعر الجمهور'}
B=T[T['flags']!=''].copy()
B['الحل']=B['flags'].apply(lambda f: next(v for k,v in fix.items() if k in f))
Bx=B.reset_index()[['code','name','stock','price','cost','last_disc','last_buy','flags','الحل']]
Bx.columns=['الكود','الصنف','الرصيد','سعر البيع','التكلفة','خصم آخر شراء %','آخر شراء','المشكلة','الحل']
Bx['آخر شراء']=pd.to_datetime(Bx['آخر شراء']).dt.date; save(Bx.round(2),'3_بيانات_مشكوك_فيها.xlsx')
A=T.reset_index()[['code','name','class','stock','m12','q12','m3','q3','last_sold','last_buy','last_disc','exp_min','cost','price','val']+months]
A.columns=['الكود','الصنف','التصنيف','الرصيد','شهور بيع من 12','كمية 12 شهر','شهور بيع آخر 3','كمية آخر 3','آخر شهر بيع','آخر شراء','خصم آخر شراء %','أقرب صلاحية','التكلفة','سعر البيع','قيمة الرصيد']+months
A['آخر شراء']=pd.to_datetime(A['آخر شراء']).dt.date; A['أقرب صلاحية']=pd.to_datetime(A['أقرب صلاحية'],errors='coerce').dt.date
save(A.round(2),'4_تصنيف_كل_الأصناف.xlsx')
