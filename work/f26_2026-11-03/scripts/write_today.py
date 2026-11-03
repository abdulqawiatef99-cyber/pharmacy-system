import pandas as pd, numpy as np, re, os
src=open('write_final.py').read()
exec(src[:src.index("DATE='2/11/2026'")].replace("d=pd.read_pickle('alloc_v2.pkl')","d=pd.read_pickle('alloc_today.pkl')").replace("d=d.drop([9268])  # Nasonex: market shortage",""))
NONDRUG=[17483,18692,23219,27927,28784,30108,30365,30475,30888]
d=d.drop(NONDRUG,errors='ignore')
add=[(30390,'EXEEDOGAST 40MG 20CAPS','النهضة',33,168,'اكسيدوجاست رغد 3/11 (شريط)'),
     (3578,'dantrelax compound 30 cap','النهضة',26,147,'اتباع 1/11 ورصيده صفر'),(5025,'FAMOTAK 40mg 20/TAB','ابن خلدون',30,42,'اتباع 1/11 ورصيده صفر'),(8978,'ROT MOOV CREAM 40GM','النهضة',40,40,'اتباع 1/11 ورصيده صفر'),(29115,'CONCOR AMLO 5-5MG 30TAB','الوطنية',18.01,210,'اتباع 1/11 ورصيده صفر'),(29221,'diacurimap10 mg 30tab','النهضة',23,204,'اتباع 1/11 ورصيده صفر'),(30895,'SANTORIN 20 CAP','الوطنية',28.01,190,'اتباع 1/11 ورصيده صفر'),
     (-11,'ACTI-COLLA C 10 SACH','النهضة',31-2,295,'اكتي كولا سي رغد 3/11'),
     (-12,'BARICTOMAP 4MG 30TAB','ابن سينا',25.6,756,'باريكتوماب رغد 3/11 (شريط) — اتأكد متوفر'),
     (-13,'MINOXIGROW 5% FOAM','ابن سينا',25.8,280,'مينوكسي جرو 1/11 — مش موجود في المخازن، اتأكد متوفر عند ابن سينا'),
     (-14,'DOZOVA MELATONIN 5MG 30CAP','غير محدد',None,149,'دوزوفا ميلاتونين احمد عزت 2/11 — موجود بس في Orient وتاج')]
for c,n,rec,disc,pub,note in add:
    d.loc[c,['name','wk','bal','rec','disc','pub','note','kash','zero','m2','m4','src']]=[n,1,0,rec,disc,pub,note,'كشكول' in note or 'رغد' in note or 'احمد' in note or '1/11 —' in note,True,1,1,"i'Supply النهارده"]
for c,q in ((29946,1),(5342,1),(29489,1),(1319,2)): d.loc[c,'wk']=q
d=d.drop([30023,25715,2597],errors='ignore')
d.loc[29115,['rec','disc','CO']]=['ابن سينا',20.3,20.3]; d.loc[29279,['rec','disc','CO']]=['ابن سينا',23.7,23.7]
d['pub']=d.pub.astype(float)
HOLD=d[((d.pub>=300)|d.name.astype(str).str.contains('ثلاجة'))&(d.rec!='غير محدد')].copy()
HOLD.to_pickle('hold_today.pkl')
KEEP={-12:('ابن سينا',25.6),9393:('ابن خلدون',28.0),8896:('ابن سينا',19.3),21174:('الوطنية',19.01),10744:('ابن خلدون',29.0),29489:('ابن خلدون',21.0)}
d=d.drop([c for c in HOLD.index if c not in KEEP])
for c,(r,x) in KEEP.items(): d.loc[c,['rec','disc']]=[r,x]
d.loc[8896,['name','pub']]=['MIXTARD 30 100IU/ML 1 VIAL #ثلاجة',130.0]
# taxed supplements at Ibn Sina (pharmacy price excludes 14% VAT) -> warehouse
MOVE={29279:('ابن خلدون',21.0),30102:('النهضة',27.0),30802:('ابن خلدون',29.0),30534:('النهضة',28.0),31403:('النهضة',20.0),31508:('النهضة',23.0),28903:('النهضة',25.0),29743:('ابن خلدون',24.0),31363:('النهضة',25.0)}
for c,(r,x) in MOVE.items():
    if c in d.index: d.loc[c,['rec','disc']]=[r,x]
d.loc[d.index.isin(list(MOVE)),'note']=d.loc[d.index.isin(list(MOVE)),'note'].fillna('').astype(str)+' ابن سينا عليه ضريبة 14% فاتنقل للمخزن'
d['wk']=d.wk.astype(int); d['m2']=d.m2.astype(int); d['m4']=d.m4.astype(int)
d['name_c']=d['name'].astype(str).str.replace(r'\s*#\s*(UNIT|ثلاجة|\d*STRIP)\s*$','',regex=True,flags=re.I).str.strip()
d['cost']=np.where(d.pub.notna()&d.disc.notna(), d.wk*d.pub.astype(float)*(1-d.disc.astype(float)/100), np.nan)
d['k_mark']=np.where(d.kash,'كشكول','')
DATE='3/11/2026'; os.makedirs('orders_today',exist_ok=True)
d=d.sort_values('name_c',key=lambda s:s.str.upper())
polite=['لو سمحت: الأصناف والكميات زي الملف بالظبط، ولو فيه بديل أو تغيير بلغنا الأول 🙏','ياريت تبعتلنا صورة الفاتورة قبل الطباعة والأصناف اللي مش متوفرة. شكرًا ليك']
summ={}
for sup,fn in (('النهضة','طلبية_النهضة'),('ابن خلدون','طلبية_ابن_خلدون'),('الوطنية','طلبية_الوطنية'),('ابن سينا','طلبية_ابن_سينا')):
    x=d[d.rec==sup]; wb=Workbook(); ws=wb.active; ws.title='الطلبية'
    lines=[(f'طلبية {sup} — {DATE}',F(18,'1F6FB2'),34),(f'عدد الأصناف: {len(x)}   |   إجمالي العلب: {int(x.wk.sum())}',F(13,'C00000'),24)]+[(t,F(13,'C00000'),24) for t in polite]
    sheet(ws,lines,x,[('اسم الصنف','name_c',60,None),('الكمية','wk',12,'0')])
    wb.save(f'orders_today/{fn}.xlsx'); summ[sup]=(len(x),int(x.wk.sum()),np.nansum(x.cost))
x=d[d.rec=='غير محدد']; wb=Workbook(); ws=wb.active
sheet(ws,[('اسأل عليها — مش لاقي لها عرض في النهضة/ابن خلدون/الوطنية/ابن سينا',F(16,'1F6FB2'),34),(f'{len(x)} صنف',F(13,'C00000'),24)],x,[('اسم الصنف','name_c',60,None),('الكمية','wk',12,'0'),('ملاحظة','note',40,None)])
wb.save('orders_today/اسأل_عليها.xlsx'); summ['غير محدد']=(len(x),int(x.wk.sum()),0)
wb=Workbook(); ws=wb.active; ws.title='التحليل'
cash=sum(summ[k][2] for k in ('ابن خلدون','الوطنية')); cred=sum(summ[k][2] for k in ('النهضة','ابن سينا'))
lines=[(f'تحليل طلبية {DATE} — الطلب = كمية أسبوع (وجنبها أسبوعين وشهر لو حبيت تكبّر)',F(16,'1F6FB2'),34),
       (' | '.join(f'{k}: {v[0]} صنف ≈ {v[2]:,.0f} ج' for k,v in summ.items()),F(12,'C00000'),24),
       (f'💵 كاش ≈ {cash:,.0f} ج   |   🗓 آجل ≈ {cred:,.0f} ج   |   النهضة = i\'Supply − 2%   ابن خلدون = +1%',F(12,'375623'),24)]
d['ord']=d.rec.map({'النهضة':0,'ابن خلدون':1,'الوطنية':2,'ابن سينا':3,'غير محدد':4})
sheet(ws,lines,d.sort_values(['ord','name_c']),[('اسم الصنف','name_c',46,None),('كشكول','k_mark',9,None),('أسبوع','wk',8,'0'),('أسبوعين','m2',9,'0'),('شهر','m4',8,'0'),('المورد','rec',12,None),('خصمه %','disc',9,'0.0'),('النهضة %','N',10,'0.0'),('ابن خلدون %','K',12,'0.0'),('الوطنية %','W',11,'0.0'),('ابن سينا %','CO',11,'0.0'),('الرصيد','bal',9,'0.00'),('التكلفة','cost',10,'#,##0'),('مصدر الخصم','src',16,None),('ملاحظة','note',40,None)])
wb.save('orders_today/تحليل_الطلبية.xlsx')
for k,v in summ.items(): print(k,v[0],v[1],round(v[2]))
print('total',sum(v[0] for v in summ.values()),'cash',round(cash),'credit',round(cred),'kash',int(d.kash.sum()))
d.to_pickle('final_today.pkl')
