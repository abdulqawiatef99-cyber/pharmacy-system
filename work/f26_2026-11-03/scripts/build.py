import pandas as pd, sys
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
src=sys.argv[1]; out=sys.argv[2]
df=pd.read_excel(src).iloc[:823]
num=['ن.الربح','إجمالى التكلفة','إجمالى س.البيع','سعر البيع','الرصيد','كمية البيع','ك.الصنف']
for c in num: df[c]=pd.to_numeric(df[c])
assert len(df)==823 and df['ك.الصنف'].notna().all()
z=df[df['الرصيد']==0].sort_values(['كمية البيع','إجمالى س.البيع'],ascending=[False,False]).reset_index(drop=True)
print(len(z)); print(z.head(15)[['إسم الصنف','كمية البيع','إجمالى س.البيع']].to_string())
cols=[('م',None),('كود الصنف','ك.الصنف'),('اسم الصنف','إسم الصنف'),('الشركة','الشركة'),('كمية البيع','كمية البيع'),('سعر البيع','سعر البيع'),('إجمالي البيع','إجمالى س.البيع'),('إجمالي التكلفة','إجمالى التكلفة'),('نسبة الربح %','ن.الربح')]
wb=Workbook(); ws=wb.active; ws.title='نواقص مرتبة'; ws.sheet_view.rightToLeft=True
ws.append(['أصناف رصيدها صفر مرتبة بكمية البيع (من 1/10 إلى 2/11/2026)']); ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=len(cols))
ws['A1'].font=Font(bold=True,size=14); ws['A1'].alignment=Alignment(horizontal='center')
ws.append([c[0] for c in cols])
hdr=PatternFill('solid',fgColor='1F4E78'); thin=Side(style='thin',color='BBBBBB')
for i in range(1,len(cols)+1):
    c=ws.cell(2,i); c.font=Font(bold=True,color='FFFFFF'); c.fill=hdr; c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
for i,r in z.iterrows():
    ws.append([i+1]+[r[k] for _,k in cols[1:]])
alt=PatternFill('solid',fgColor='EEF3F8')
for row in ws.iter_rows(min_row=3,max_row=ws.max_row):
    for c in row:
        c.border=Border(top=thin,bottom=thin,left=thin,right=thin)
        if row[0].row%2==0: c.fill=alt
    for idx in (5,6,7,8,9): row[idx-1].number_format='#,##0.00'
    row[4].number_format='0.##'
widths=[6,10,48,24,11,11,13,13,11]
for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
ws.freeze_panes='A3'; ws.auto_filter.ref=f"A2:{get_column_letter(len(cols))}{ws.max_row}"
wb.save(out)
