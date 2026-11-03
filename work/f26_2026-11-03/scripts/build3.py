import pandas as pd, sys
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
src,out=sys.argv[1:3]; DAYS=32
df=pd.read_excel(src).iloc[:823]
for c in ['ن.الربح','إجمالى التكلفة','إجمالى س.البيع','سعر البيع','الرصيد','كمية البيع']: df[c]=pd.to_numeric(df[c])
df['أيام']=(df['الرصيد']/(df['كمية البيع']/DAYS)).round(0)
zero=df[df['الرصيد']==0].sort_values(['كمية البيع','إجمالى س.البيع'],ascending=False).reset_index(drop=True)
have=df[df['الرصيد']>0].sort_values(['كمية البيع','إجمالى س.البيع'],ascending=False).reset_index(drop=True)
assert len(zero)+len(have)==823
thin=Side(style='thin',color='BBBBBB'); B=Border(top=thin,bottom=thin,left=thin,right=thin)
hdr=PatternFill('solid',fgColor='E7E6E6'); alt=PatternFill('solid',fgColor='EEF3F8')
red=PatternFill('solid',fgColor='F8CBAD'); yel=PatternFill('solid',fgColor='FFF2CC')
def sheet(ws,title,data,cols,widths,cover=False):
    ws.sheet_view.rightToLeft=True
    ws.append([title]); ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=len(cols))
    ws['A1'].font=Font(name='Arial',bold=True,size=20,color='1F6FB2'); ws['A1'].alignment=Alignment(horizontal='center')
    ws.append([c[0] for c in cols])
    for i in range(1,len(cols)+1):
        c=ws.cell(2,i); c.font=Font(name='Arial',bold=True,size=14,color='000000'); c.fill=hdr; c.border=B
        c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
    for i,r in data.iterrows():
        ws.append([r[k] for _,k in cols])
        rn=ws.max_row
        fill=alt if rn%2==0 else None
        if cover:
            d=r['أيام']
            if d<15: fill=red
            elif d<30: fill=yel
        for j,(name,k) in enumerate(cols,1):
            c=ws.cell(rn,j); c.border=B; c.font=Font(name='Arial',bold=True,size=14)
            if fill: c.fill=fill
            c.alignment=Alignment(horizontal='right' if name=='اسم الصنف' else 'center')
            if name in ('سعر البيع','إجمالي البيع','إجمالي التكلفة','نسبة الربح %'): c.number_format='#,##0.00'
            elif name in ('كمية البيع','الرصيد'): c.number_format='0.00'
            elif name=='يكفي كام يوم': c.number_format='0'
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    ws.row_dimensions[2].height=36; ws.row_dimensions[1].height=34
    for rr in range(3,ws.max_row+1): ws.row_dimensions[rr].height=22
    ws.sheet_view.zoomScale=110
    ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=9; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.print_title_rows='2:2'
    ws.freeze_panes='B3'; ws.auto_filter.ref=f"A2:{get_column_letter(len(cols))}{ws.max_row}"
base=[('اسم الصنف','إسم الصنف'),('كمية البيع','كمية البيع'),('الرصيد','الرصيد')]
tail=[('سعر البيع','سعر البيع'),('إجمالي البيع','إجمالى س.البيع'),('إجمالي التكلفة','إجمالى التكلفة'),('نسبة الربح %','ن.الربح')]
wb=Workbook()
sheet(wb.active,'أصناف رصيدها صفر — مرتبة بكمية البيع (1/10 – 2/11/2026)',zero,base+tail,[58,14,12,13,15,16,14])
wb.active.title='رصيد صفر'
sheet(wb.create_sheet('ليها رصيد'),'أصناف ليها رصيد — مرتبة بكمية البيع (أحمر أقل من 15 يوم، أصفر أقل من 30)',have,base+[('يكفي كام يوم','أيام')]+tail,[58,14,12,15,13,15,16,14],cover=True)
wb.save(out)
print(len(zero),len(have),'red',(have['أيام']<15).sum(),'yellow',((have['أيام']>=15)&(have['أيام']<30)).sum())
print(have.head(8)[['إسم الصنف','كمية البيع','الرصيد','أيام']].to_string())
