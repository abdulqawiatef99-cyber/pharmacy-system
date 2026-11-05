"""Prototype (2026-11-02) of the Smart Order Report — see docs/SMART_ORDER_REPORT.md.
Reads 3 monthly E-Stock sales exports and writes a 2-sheet Excel order file."""
import pandas as pd, numpy as np, sys, math
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
# Usage: python -I smart_order_from_estock.py OUT.xlsx LABEL1=month1.xlsx LABEL2=month2.xlsx LABEL3=month3.xlsx
# Each input = E-Stock "تقرير مبيعات أصناف عن فترة" exported for one month (oldest first).
out=sys.argv[1]; D=''
files=[tuple(a.split('=',1)) for a in sys.argv[2:]]
assert len(files)==3, 'pass exactly 3 monthly files'
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
def need(r,days):
    if r['months']<2: return np.nan
    n=math.ceil(r['avg']/30*days-r['bal']-1e-9)
    return n if n>0 else np.nan
t['wk']=t.apply(lambda r:need(r,7),axis=1)
t['sug']=t.apply(lambda r:need(r,30),axis=1)
t['wcost']=t['wk']*t['ucost']
t['scost']=t['sug']*t['ucost']
zero=t[t['bal']==0].sort_values(['months','avg'],ascending=False)
t['hw']=t['wk'].notna(); t['hm']=t['sug'].notna()
have=t[t['bal']>0].sort_values(['hw','hm','avg'],ascending=False)
assert len(t)>0
print(len(t),len(zero),len(have),'zero by months',zero['months'].value_counts().to_dict())
print('wk',t['wk'].notna().sum(),round(t['wcost'].sum()),'mo',t['sug'].notna().sum(),round(t['scost'].sum()))
thin=Side(style='thin',color='BBBBBB'); B=Border(top=thin,bottom=thin,left=thin,right=thin)
hdr=PatternFill('solid',fgColor='E7E6E6'); alt=PatternFill('solid',fgColor='EEF3F8')
red=PatternFill('solid',fgColor='F8CBAD'); yel=PatternFill('solid',fgColor='FFF2CC')
F=lambda sz=14,col='000000':Font(name='Arial',bold=True,size=sz,color=col)
cols=[('اسم الصنف','name',58),('كمية أسبوع','wk',13),('كمية شهر','sug',12),('الرصيد','bal',11),('متوسط شهري','avg',14),('اتباع في كام شهر','months',18)]+[(m,m,11) for m,_ in files]+[('تكلفة أسبوع','wcost',14),('تكلفة شهر','scost',13)]
cols=cols[::-1]
def sheet(ws,title,data,colorby):
    ws.sheet_view.rightToLeft=False; ws.sheet_view.zoomScale=110
    n=len(cols)
    ws.append([title]); ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=n)
    ws['A1'].font=F(18,'1F6FB2'); ws['A1'].alignment=Alignment(horizontal='center',vertical='center'); ws.row_dimensions[1].height=34
    ws.append([f"طلب أسبوع: {data['wk'].notna().sum()} صنف بحوالي {data['wcost'].sum():,.0f} جنيه   |   طلب شهر: {data['sug'].notna().sum()} صنف بحوالي {data['scost'].sum():,.0f} جنيه"]); ws.merge_cells(start_row=2,start_column=1,end_row=2,end_column=n)
    ws['A2'].font=F(14,'C00000'); ws['A2'].alignment=Alignment(horizontal='center',vertical='center'); ws.row_dimensions[2].height=26
    ws.append([c[0] for c in cols])
    for j in range(1,n+1):
        c=ws.cell(3,j); c.font=F(); c.fill=hdr; c.border=B; c.alignment=Alignment(horizontal='center',vertical='center')
    ws.row_dimensions[3].height=32
    for _,r in data.iterrows():
        ws.append([None if (isinstance(r[k],float) and np.isnan(r[k])) else r[k] for _,k,_ in cols])
        rn=ws.max_row; ws.row_dimensions[rn].height=22
        fill=alt if rn%2==0 else None
        mark=None; markk='months' if colorby=='months' else 'bal'
        if colorby=='months':
            mark=red if r['months']==3 else yel if r['months']==2 else None
        elif r['months']>=2:
            mark=red if r['days']<15 else yel if r['days']<30 else None
        for j,(name,k,w) in enumerate(cols,1):
            c=ws.cell(rn,j); c.border=B; c.font=F()
            if fill: c.fill=fill
            if mark is not None and k==markk: c.fill=mark
            c.alignment=Alignment(horizontal='right' if k=='name' else 'center')
            if k in [m for m,_ in files]+['avg','bal']: c.number_format='0.00'
            elif k in ('wk','sug','months'): c.number_format='0'
            elif k in ('scost','wcost'): c.number_format='#,##0'
    for j,(_,_,w) in enumerate(cols,1): ws.column_dimensions[get_column_letter(j)].width=w
    ws.freeze_panes='A4'; ws.auto_filter.ref=f"A3:{get_column_letter(n)}{ws.max_row}"
    ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=9; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
    ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.print_title_rows='3:3'
wb=Workbook(); ws=wb.active; ws.title='رصيد صفر'
sheet(ws,'رصيد صفر — خانة (اتباع في كام شهر): أحمر = 3 شهور، أصفر = شهرين',zero,'months')
sheet(wb.create_sheet('ليها رصيد'),'ليها رصيد — خانة الرصيد: أحمر = يكفي أقل من 15 يوم، أصفر = أقل من 30',have,'days')
wb.save(out)
# checks
assert len(zero)+len(have)==len(t)
assert (t['wk'].fillna(0)<=t['sug'].fillna(0)).all()

