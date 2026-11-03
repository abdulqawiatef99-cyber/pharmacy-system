import pandas as pd,numpy as np
from openpyxl.styles import Font,Alignment,PatternFill
T=pd.read_pickle('T.pkl');months=[c for c in T.columns if str(c).startswith('20')]
def frac_flag(x):
    f=x-np.floor(x); return x>0 and (0<f<0.02 or 0.98<f<1)
fl=[]
for c,r in T.iterrows():
    f=[s for s in str(r['flags']).split('، ') if s and s!='رصيد كسري' and not s.startswith('خصم')]
    if frac_flag(r.stock): f.append('رصيد كسري غريب (تقريب)')
    d=r.last_disc
    if pd.notna(d) and (d<=0 or d>=90): f.append(f'خصم آخر شراء {d:.0f}% (غلط إدخال غالبًا)')
    elif pd.notna(d) and (d<5): f.append(f'خصم آخر شراء {d:.0f}% (قليل جدًا)')
    elif pd.notna(d) and d>60: f.append(f'خصم آخر شراء {d:.0f}% (عالي — اتأكد)')
    fl.append('، '.join(f))
T['flags']=fl
today=pd.Timestamp('2026-11-03')
col={'name':'الصنف','class':'التصنيف','stock':'الرصيد','m12':'شهور بيع من 12','q12':'كمية 12 شهر','m3':'شهور بيع آخر 3','q3':'كمية آخر 3','last_sold':'آخر شهر بيع','last_buy':'آخر شراء','last_disc':'خصم آخر شراء %','last_exp':'صلاحية آخر شراء','exp_min':'أقرب صلاحية بالرصيد','cost':'التكلفة','price':'سعر البيع','val':'قيمة الرصيد بالتكلفة','flags':'ملاحظات بيانات'}
base=['name','class','stock','m12','q12','m3','q3','last_sold','last_buy','last_disc','exp_min','cost','price','val','flags']
A=T.reset_index()[['code']+base+months].rename(columns={'code':'الكود',**col})
A['آخر شراء']=pd.to_datetime(A['آخر شراء']).dt.date; A['أقرب صلاحية بالرصيد']=pd.to_datetime(A['أقرب صلاحية بالرصيد'],errors='coerce').dt.date
for c in ['التكلفة','سعر البيع','قيمة الرصيد بالتكلفة','كمية 12 شهر','كمية آخر 3','خصم آخر شراء %','الرصيد']: A[c]=A[c].round(2)
dead=A[(A['الرصيد']>0)&A['التصنيف'].isin(['ما اتباعش سنة','متوقف (3 شهور)'])].sort_values('قيمة الرصيد بالتكلفة',ascending=False)
bad=A[A['ملاحظات بيانات']!=''].sort_values('ملاحظات بيانات')
summ=T[T.stock>0].groupby('class').agg(**{'عدد الأصناف':('val','size'),'قيمة الرصيد':('val','sum')}).round().reset_index().rename(columns={'class':'التصنيف'})
summ.loc[len(summ)]=['الإجمالي',summ['عدد الأصناف'].sum(),summ['قيمة الرصيد'].sum()]
defs=pd.DataFrame({'التصنيف':['منتظم','شبه منتظم','متقطع','متوقف (3 شهور)','ما اتباعش سنة','جديد','اتشرى حديثًا'],
 'المعنى':['اتباع 6 شهور أو أكتر من 12، و2 من آخر 3','اتباع 4 أو 5 شهور من 12','اتباع 1–3 شهور من 12 (غالبًا مريض واحد أو طلب)','ما اتباعش أغسطس–أكتوبر واتباع قبل كده','ما اتباعش ولا مرة في 12 شهر','اتشرى آخر 75 يوم وما اتباعش','اتشرى آخر 75 يوم وما اتباعش آخر 3 شهور']})
IK=pd.DataFrame([
 ('150292','2/11','بلينك تيرز قطرة',2,320,21,'01/2028'),
 ('150745','3/11','امباجليماكس 10 مجم',1,154.5,28,'09/2029'),('150745','3/11','ايتراكون 14 كبسولة',1,130,25,'06/2029'),
 ('150745','3/11','بلافكس',1,311,29,'09/2028'),('150745','3/11','تشيكس جروي شراب',1,290,26,'10/2028'),
 ('150745','3/11','جلوكوادجاست 12.5/500 (مرتجع)',1,196.5,32,'12/2027'),('150745','3/11','دي ديب 10000 30 كبسولة',1,300,21,'03/2028'),
 ('150745','3/11','سيانوهيبتان 6 أمبول',2,114,36,'11/2027'),('150745','3/11','فاموتاك 40',1,42,30,'07/2029'),
 ('150745','3/11','فليكس سبراي',1,76,21,'09/2028'),('150745','3/11','كويتابين 100',1,222,36,'08/2028'),
 ('150745','3/11','ليمتلس ماغنسيوم',2,230,24,'08/2028'),('150745','3/11','مونوماك ديبو',1,48,20,'06/2030'),
 ('150745','3/11','نيرهاسيلدا 75 مجم 8 فيلم',1,88,64,'10/2028'),('150745','3/11','نيفروميد ادفانس 14 كيس',1,375,28,'05/2028'),
 ('150745','3/11','نيوكاربون 30 قرص (سعر جديد)',1,200,21,'11/2028')],
 columns=['رقم الفاتورة','التاريخ','الصنف','الكمية','سعر الجمهور','الخصم %','الصلاحية'])
IK['الصافي']=(IK['الكمية']*IK['سعر الجمهور']*(1-IK['الخصم %']/100)).round(2)
with pd.ExcelWriter('تحليل_سنة_2026-11-03.xlsx',engine='openpyxl') as w:
    for n,df in [('ملخص',summ),('التعريفات',defs),('الرواكد',dead),('كل الأصناف',A.sort_values(['التصنيف','قيمة الرصيد بالتكلفة'],ascending=[True,False])),('بيانات مشكوك فيها',bad),('ابن خلدون مش متسجل',IK)]:
        df.to_excel(w,sheet_name=n,index=False)
    for ws in w.book.worksheets:
        ws.sheet_view.rightToLeft=True; ws.freeze_panes='A2'
        for row in ws.iter_rows():
            for c in row: c.font=Font(size=14,bold=True)
        for c in ws[1]: c.fill=PatternFill('solid',fgColor='FFE699'); c.alignment=Alignment(wrap_text=True,vertical='center')
        for col_cells in ws.columns:
            L=max(len(str(c.value or '')) for c in col_cells[:200]); ws.column_dimensions[col_cells[0].column_letter].width=min(max(10,L*1.3),45)
print(summ.to_string()); print('dead',len(dead),dead['قيمة الرصيد بالتكلفة'].sum().round()); print('bad',len(bad)); print(bad['ملاحظات بيانات'].value_counts().head(8)); print('IK total',IK['الصافي'].sum())
