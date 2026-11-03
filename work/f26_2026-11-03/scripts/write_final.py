import pandas as pd, numpy as np, re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
d=pd.read_pickle('alloc_v2.pkl')
d=d.drop([9268])  # Nasonex: market shortage
d['name_c']=d['name'].str.replace(r'\s*#\s*(UNIT|ثلاجة|\d*STRIP)\s*$','',regex=True,flags=re.I).str.strip()
thin=Side(style='thin',color='999999'); B=Border(top=thin,bottom=thin,left=thin,right=thin)
hdr=PatternFill('solid',fgColor='E7E6E6'); alt=PatternFill('solid',fgColor='EEF3F8')
F=lambda sz=14,c='000000':Font(name='Arial',bold=True,size=sz,color=c)
def sheet(ws,lines,data,cols):
    cols=cols[::-1]; n=len(cols); ws.sheet_view.rightToLeft=False; ws.sheet_view.zoomScale=110
    r0=1
    for txt,f,h in lines:
        ws.cell(r0,1,txt).font=f; ws.cell(r0,1).alignment=Alignment(horizontal='center',vertical='center')
        ws.merge_cells(start_row=r0,start_column=1,end_row=r0,end_column=n); ws.row_dimensions[r0].height=h; r0+=1
    for j,(nm,k,w,fmt) in enumerate(cols,1):
        c=ws.cell(r0,j,nm); c.font=F(); c.fill=hdr; c.border=B; c.alignment=Alignment(horizontal='center',vertical='center')
        ws.column_dimensions[get_column_letter(j)].width=w
    ws.row_dimensions[r0].height=30
    for i,(_,r) in enumerate(data.iterrows()):
        rn=r0+1+i; ws.row_dimensions[rn].height=24
        for j,(nm,k,w,fmt) in enumerate(cols,1):
            v=r[k]; v=None if (isinstance(v,float) and np.isnan(v)) else v
            c=ws.cell(rn,j,v); c.font=F(); c.border=B
            if i%2: c.fill=alt
            c.alignment=Alignment(horizontal='right' if k in('name_c','note') else 'center')
            if fmt: c.number_format=fmt
    ws.freeze_panes=f'A{r0+1}'; ws.print_title_rows=f'{r0}:{r0}'
    ws.page_setup.orientation='portrait' if n<=3 else 'landscape'; ws.page_setup.paperSize=9
    ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.sheet_properties.pageSetUpPr.fitToPage=True
DATE='2/11/2026'
d=d.sort_values('name_c',key=lambda s:s.str.upper())
polite=['لو سمحت: الأصناف والكميات زي الملف بالظبط، ولو فيه بديل أو تغيير بلغنا الأول 🙏','ياريت تبعتلنا صورة الفاتورة قبل الطباعة والأصناف اللي مش متوفرة. شكرًا ليك']
summ={}
for sup,fn in (('النهضة','طلبية_النهضة'),('ابن خلدون','طلبية_ابن_خلدون'),('الوطنية','طلبية_الوطنية'),('ابن سينا','طلبية_ابن_سينا')):
    x=d[d.rec==sup]; wb=Workbook(); ws=wb.active; ws.title='الطلبية'
    lines=[(f'طلبية {sup} — {DATE}',F(18,'1F6FB2'),34),(f'عدد الأصناف: {len(x)}   |   إجمالي العلب: {int(x.wk.sum())}',F(13,'C00000'),24)]+[(t,F(13,'C00000'),24) for t in polite]
    sheet(ws,lines,x,[('اسم الصنف','name_c',60,None),('الكمية','wk',12,'0')])
    wb.save(f'orders_final/{fn}.xlsx'); summ[sup]=(len(x),int(x.wk.sum()),x.cost.sum())
x=d[d.rec=='غير محدد']; wb=Workbook(); ws=wb.active
sheet(ws,[('اسأل عليها — مالهاش مورد واضح',F(18,'1F6FB2'),34),(f'{len(x)} صنف (أغلبها إكسسوار، أو مش موجودة على i\'Supply)',F(13,'C00000'),24)],x,[('اسم الصنف','name_c',60,None),('الكمية','wk',12,'0')])
wb.save('orders_final/اسأل_عليها.xlsx'); summ['غير محدد']=(len(x),int(x.wk.sum()),0)
# analysis
d['src_ar']=d['src'].map({'isup':"i'Supply النهارده",'hist':'تاريخ الشراء'})
d['note']=np.where(d.nomarket,'مش ظاهر في أي مخزن — اتأكد',np.where((d.rec=='ابن سينا')&(d.co_src=='po'),'خصم فارما كتقدير لابن سينا',''))
wb=Workbook(); ws=wb.active; ws.title='التحليل'
cash=sum(summ[k][2] for k in ('ابن خلدون','الوطنية')); cred=sum(summ[k][2] for k in ('النهضة','ابن سينا'))
lines=[(f'تحليل طلبية {DATE} — كمية تكفي أسبوع',F(18,'1F6FB2'),34),
       (' | '.join(f'{k}: {v[0]} صنف ≈ {v[2]:,.0f} ج' for k,v in summ.items()),F(12,'C00000'),24),
       (f'💵 كاش ≈ {cash:,.0f} ج   |   🗓 آجل ≈ {cred:,.0f} ج   |   النهضة = i\'Supply − 2%   ابن خلدون = +1%',F(12,'375623'),24)]
d['ord']=d.rec.map({'النهضة':0,'ابن خلدون':1,'الوطنية':2,'ابن سينا':3,'غير محدد':4})
sheet(ws,lines,d.sort_values(['ord','name_c']),[('اسم الصنف','name_c',50,None),('الكمية','wk',9,'0'),('المورد','rec',13,None),('خصمه %','disc',10,'0.0'),('النهضة %','N',11,'0.0'),('ابن خلدون %','K',13,'0.0'),('الوطنية %','W',12,'0.0'),('فارما/ابن سينا %','CO',16,'0.0'),('الرصيد','bal',9,'0.00'),('متوسط شهري','avg',12,'0.0'),('التكلفة','cost',11,'#,##0'),('المصدر','src_ar',17,None),('ملاحظة','note',34,None)])
wb.save('orders_final/تحليل_الطلبية.xlsx')
for k,v in summ.items(): print(k,v[0],v[1],round(v[2]))
print('total lines',sum(v[0] for v in summ.values()),'of',len(d),'cash',round(cash),'credit',round(cred))
