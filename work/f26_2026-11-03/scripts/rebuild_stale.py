import pandas as pd, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
st=pd.read_pickle('stale_all.pkl').sort_values('gain',ascending=False)
st=st[st.qty>0]
f='orders_final/الصلاحية_والأسعار.xlsx'
wb=openpyxl.load_workbook(f)
old=wb['أسعار قديمة (بحث)']; idx=wb.sheetnames.index(old.title); wb.remove(old)
ws=wb.create_sheet('أسعار قديمة (بحث)',idx); wb.active=idx
B=Font(name='Arial',bold=True,size=13); H=Font(name='Arial',bold=True,size=14,color='FFFFFF')
thin=Side(style='thin',color='999999'); bd=Border(left=thin,right=thin,top=thin,bottom=thin)
ws['A1']="أسعار قديمة — كل الدفعات (1,365 صنف ما اتشروش من 6 شهور)"; ws['A1'].font=Font(name='Arial',bold=True,size=15)
strong=st[st.score>=3.9]
ws['A2']=f"{len(st)} صنف سعرهم أقل من سعر الجمهور الحالي — الفرق ≈ {st.gain.sum():,.0f} ج  |  منهم {len(strong)} مطابقة أكيدة ≈ {strong.gain.sum():,.0f} ج  (المصدر: i'Supply)"
ws['A2'].font=B
hdr=['ملاحظة','الفرق في الرصيد','السعر الجديد','سعرك','الرصيد','اسم الصنف']
for j,h in enumerate(hdr,1):
    c=ws.cell(3,j,h); c.font=H; c.fill=PatternFill('solid',fgColor='1F4E78'); c.alignment=Alignment(horizontal='center',vertical='center'); c.border=bd
for i,(code,r) in enumerate(st.iterrows(),4):
    note='' if r.score>=3.9 else 'اتأكد إنه نفس الصنف'
    vals=[note,round(r.gain),r.newp,r.price,int(r.qty) if r.qty==int(r.qty) else round(r.qty,1),r['name']]
    fill=PatternFill('solid',fgColor='F2F2F2' if i%2 else 'FFFFFF')
    for j,v in enumerate(vals,1):
        c=ws.cell(i,j,v); c.font=B; c.fill=fill; c.border=bd
        c.alignment=Alignment(horizontal='right' if j==6 else 'center',vertical='center')
    if note: ws.cell(i,1).font=Font(name='Arial',bold=True,size=13,color='C00000')
for col,w in zip('ABCDEF',[22,16,13,11,9,48]): ws.column_dimensions[col].width=w
ws.freeze_panes='A4'
ws.page_setup.orientation='landscape'; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.sheet_properties.pageSetUpPr.fitToPage=True
ws.print_title_rows='3:3'
wb.save(f); print(len(st), round(st.gain.sum()), len(strong), round(strong.gain.sum()))
print(st.head(10)[['name','qty','price','newp','gain','score']].to_string())
