import pandas as pd, numpy as np, sys, math
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
D=sys.argv[1]; out=sys.argv[2]
files=[('أغسطس','03ad4a64-ORDER_8.xlsx'),('سبتمبر','6a60b91a-ORDER_9.xlsx'),('أكتوبر','2b9a92c1-ORDER_NOV.xlsx')]
frames=[]
for i,(m,f) in enumerate(files):
    d=pd.read_excel(D+f); d=d[d['ك.الصنف'].notna()].copy()
    for c in ['إجمالى التكلفة','الرصيد','كمية البيع','ك.الصنف']: d[c]=pd.to_numeric(d[c])
    d['m']=m; d['ord']=i; frames.append(d)
allr=pd.concat(frames)
q=allr.pivot_table(index='ك.الصنف',columns='m',values='كمية البيع',aggfunc='sum').reindex(columns=[m for m,_ in files]).fillna(0)
last=allr.sort_values('ord').groupby('ك.الصنف').last()
t=q.copy()
t['name']=last['إسم الصنف']; t['bal']=last['الرصيد']
t['ucost']=last['إجمالى التكلفة']/last['كمية البيع']
t['months']=(q>0).sum(axis=1)
t['avg']=q.sum(axis=1)/3
t['days']=np.where(t['avg']>0,(t['bal']/(t['avg']/30)).round(0),np.nan)
def sug(r):
    if r['months']<2: return np.nan
    n=math.ceil(r['avg']-r['bal']-1e-9)
    return n if n>0 else np.nan
t['sug']=t.apply(sug,axis=1)
t['scost']=t['sug']*t['ucost']
zero=t[t['bal']==0].sort_values(['months','avg'],ascending=False)
have=t[t['bal']>0].sort_values(['months','avg'],ascending=False)
print(len(t),len(zero),len(have),'zero by months',zero['months'].value_counts().to_dict())
print('order items',t['sug'].notna().sum(),'cost',round(t['scost'].sum()), 'zero part',round(zero['scost'].sum()))
thin=Side(style='thin',color='BBBBBB'); B=Border(top=thin,bottom=thin,left=thin,right=thin)
hdr=PatternFill('solid',fgColor='E7E6E6'); alt=PatternFill('solid',fgColor='EEF3F8')
red=PatternFill('solid',fgColor='F8CBAD'); yel=PatternFill('solid',fgColor='FFF2CC')
F=lambda sz=14,col='000000':Font(name='Arial',bold=True,size=sz,color=col)
cols=[('اسم الصنف','name',60),('أغسطس','أغسطس',11),('سبتمبر','سبتمبر',11),('أكتوبر','أكتوبر',11),('عدد الشهور','months',13),('متوسط شهري','avg',14),('الرصيد','bal',11),('يكفي كام يوم','days',15),('الكمية المقترحة','sug',17),('تكلفة تقريبية','scost',15)]
cols=cols[::-1]
def sheet(ws,title,data,colorby):
    ws.sheet_view.rightToLeft=False; ws.sheet_view.zoomScale=110
    n=len(cols)
    ws.append([title]); ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=n)
    ws['A1'].font=F(18,'1F6FB2'); ws['A1'].alignment=Alignment(horizontal='center',vertical='center'); ws.row_dimensions[1].height=34
    tot=data['scost'].sum(); cnt=data['sug'].notna().sum()
    ws.append([f"إجمالي التكلفة التقريبية للطلب: {tot:,.0f} جنيه — عدد الأصناف المقترح طلبها: {cnt}"]); ws.merge_cells(start_row=2,start_column=1,end_row=2,end_column=n)
    ws['A2'].font=F(14,'C00000'); ws['A2'].alignment=Alignment(horizontal='center',vertical='center'); ws.row_dimensions[2].height=26
    ws.append([c[0] for c in cols])
    for j in range(1,n+1):
        c=ws.cell(3,j); c.font=F(); c.fill=hdr; c.border=B; c.alignment=Alignment(horizontal='center',vertical='center')
    ws.row_dimensions[3].height=32
    for _,r in data.iterrows():
        ws.append([None if (isinstance(r[k],float) and np.isnan(r[k])) else r[k] for _,k,_ in cols])
        rn=ws.max_row; ws.row_dimensions[rn].height=22
        fill=alt if rn%2==0 else None
        if colorby=='months':
            if r['months']==3: fill=red
            elif r['months']==2: fill=yel
        else:
            if r['months']>=2 and r['days']<15: fill=red
            elif r['months']>=2 and r['days']<30: fill=yel
        for j,(name,k,w) in enumerate(cols,1):
            c=ws.cell(rn,j); c.border=B; c.font=F()
            if fill: c.fill=fill
            c.alignment=Alignment(horizontal='right' if k=='name' else 'center')
            if k in ('أغسطس','سبتمبر','أكتوبر','avg','bal'): c.number_format='0.00'
            elif k in ('days','sug','months'): c.number_format='0'
            elif k=='scost': c.number_format='#,##0'
    for j,(_,_,w) in enumerate(cols,1): ws.column_dimensions[get_column_letter(j)].width=w
    ws.freeze_panes='A4'; ws.auto_filter.ref=f"A3:{get_column_letter(n)}{ws.max_row}"
    ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=9; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
    ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.print_title_rows='3:3'
wb=Workbook(); ws=wb.active; ws.title='رصيد صفر'
sheet(ws,'رصيد صفر — أحمر: اتباع في الـ3 شهور، أصفر: في شهرين',zero,'months')
sheet(wb.create_sheet('ليها رصيد'),'ليها رصيد — أحمر: يكفي أقل من 15 يوم، أصفر: أقل من 30',have,'days')
wb.save(out)
# checks
assert len(zero)+len(have)==len(t)
x=t.loc[t['name'].str.contains('COGINTOL',case=False)].iloc[0]; print(x[['أغسطس','سبتمبر','أكتوبر','months','avg','bal','days','sug','ucost']].to_dict())
print(zero.head(6)[['name','أغسطس','سبتمبر','أكتوبر','avg','sug']].to_string())
print(have[have['sug'].notna()].head(6)[['name','avg','bal','days','sug']].to_string())
