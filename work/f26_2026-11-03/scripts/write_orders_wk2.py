import pandas as pd, numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
o=pd.read_pickle('order.pkl'); t=pd.read_pickle('t_all.pkl')
thin=Side(style='thin',color='999999'); B=Border(top=thin,bottom=thin,left=thin,right=thin)
hdr=PatternFill('solid',fgColor='E7E6E6'); alt=PatternFill('solid',fgColor='EEF3F8')
F=lambda sz=14,c='000000':Font(name='Arial',bold=True,size=sz,color=c)
def sheet(ws,title,sub,data,cols):
    cols=cols[::-1]; n=len(cols)
    ws.sheet_view.rightToLeft=False; ws.sheet_view.zoomScale=110
    for i,txt,f,h in ((1,title,F(18,'1F6FB2'),34),(2,sub,F(13,'C00000'),24)):
        ws.cell(i,1,txt).font=f; ws.cell(i,1).alignment=Alignment(horizontal='center',vertical='center')
        ws.merge_cells(start_row=i,start_column=1,end_row=i,end_column=n); ws.row_dimensions[i].height=h
    for j,(name,k,w,fmt) in enumerate(cols,1):
        c=ws.cell(3,j,name); c.font=F(); c.fill=hdr; c.border=B; c.alignment=Alignment(horizontal='center',vertical='center')
        ws.column_dimensions[get_column_letter(j)].width=w
    ws.row_dimensions[3].height=30
    for i,(_,r) in enumerate(data.iterrows()):
        rn=4+i; ws.row_dimensions[rn].height=24
        for j,(name,k,w,fmt) in enumerate(cols,1):
            v=r[k]; v=None if (isinstance(v,float) and np.isnan(v)) else v
            c=ws.cell(rn,j,v); c.font=F(); c.border=B
            if i%2: c.fill=alt
            c.alignment=Alignment(horizontal='right' if k=='name' else 'center')
            if fmt: c.number_format=fmt
    ws.freeze_panes='A4'
    ws.page_setup.orientation='portrait' if n<=3 else 'landscape'; ws.page_setup.paperSize=9
    ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.print_title_rows='3:3'
    if n>3: ws.auto_filter.ref=f"A3:{get_column_letter(n)}{3+len(data)}"
DATE='2/11/2026'
import re
o['name_raw']=o['name']
o['name']=o['name'].str.replace(r'\s*#\s*(UNIT|ثلاجة|\d*STRIP)\s*$','',regex=True,flags=re.I).str.strip()
o=o.sort_values('name',key=lambda s:s.str.upper().str.strip())
files={}
for sup,fname in (('النهضة','طلبية_النهضة'),('ابن خلدون','طلبية_ابن_خلدون'),('الوطنية','طلبية_الوطنية'),('ابن سينا','طلبية_ابن_سينا'),('فيوتشر','طلبية_فيوتشر'),('المصطفى','طلبية_المصطفى'),('غير محدد','اسأل_عليها_قبل_الطلب')):
    d=o[o.rec==sup]
    wb=Workbook(); ws=wb.active; ws.title='الطلبية'
    if sup=='غير محدد':
        sheet(ws,'أصناف مالهاش مخزن محدد — اسأل عليها',f'{len(d)} صنف — آخر مورد مكتوب لو معروف',d,[('اسم الصنف','name',60,None),('الكمية','wk',12,'0'),('آخر مورد','last_sup',22,None)])
    else:
        sheet(ws,f'طلبية {sup} — {DATE}',f'عدد الأصناف: {len(d)}',d,[('اسم الصنف','name',60,None),('الكمية','wk',12,'0')])
    p=f'orders/{fname}.xlsx'; wb.save(p); files[sup]=(p,len(d),d['cost'].sum())
# master
wb=Workbook(); ws=wb.active; ws.title='الطلبية كلها'
m=o.copy(); m['rec_order']=m['rec'].map({'النهضة':0,'ابن خلدون':1,'الوطنية':2,'ابن سينا':3,'فيوتشر':4,'المصطفى':5,'غير محدد':6})
m=m.sort_values(['rec_order','name'])
tot=' | '.join(f"{k}: {v[1]} صنف ≈ {v[2]:,.0f} ج" for k,v in files.items())
sheet(ws,f'تحليل الطلبية — {DATE} (كمية تكفي أسبوع)',tot,m,[('اسم الصنف','name',52,None),('الكمية','wk',10,'0'),('المورد المقترح','rec',17,None),('خصمه %','rec_disc',11,'0.0'),('النهضة %','النهضة',12,'0.0'),('ابن خلدون %','ابن خلدون',15,'0.0'),('الوطنية %','الوطنية',13,'0.0'),('ابن سينا %','ابن سينا',13,'0.0'),('فارما %','فارما أوفرسيز',11,'0.0'),('الرصيد','bal',10,'0.00'),('متوسط شهري','avg',14,'0.00'),('التكلفة','cost',12,'#,##0')])
one=t[(t['bal']==0)&(t['months']==1)].copy().sort_values('avg',ascending=False)
sheet(wb.create_sheet('رصيد صفر - شهر واحد'),'رصيد صفر واتباعوا في شهر واحد بس — قرارك',f'{len(one)} صنف — مش داخلين في الطلبية',one,[('اسم الصنف','name',52,None),('أغسطس','أغسطس',11,'0.00'),('سبتمبر','سبتمبر',11,'0.00'),('أكتوبر','أكتوبر',11,'0.00'),('آخر مورد','last_sup',18,None)])
wb.save('orders/تحليل_الطلبية.xlsx')
for k,v in files.items(): print(k,v[1],round(v[2]))
print('sum lines',sum(v[1] for v in files.values()),'of',len(o))
