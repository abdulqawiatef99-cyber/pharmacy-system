import pandas as pd, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
d=pd.read_pickle('dead.pkl'); today=pd.Timestamp('2026-11-02')
B=Font(name='Arial',bold=True,size=13); H=Font(name='Arial',bold=True,size=14,color='FFFFFF'); R=Font(name='Arial',bold=True,size=13,color='C00000')
thin=Side(style='thin',color='999999'); bd=Border(left=thin,right=thin,top=thin,bottom=thin)
def note(r):
    n=[]
    if pd.notna(r.exp_m):
        if r.exp_m<=0: n.append('منتهي')
        elif r.exp_m<=3: n.append('ينتهي خلال 3 شهور')
        elif r.exp_m<=6: n.append('ينتهي خلال 6 شهور')
    if 'ثلاجة' in str(r['name']): n.append('ثلاجة')
    return ' + '.join(n)
def sheet(wb,title,df,t1,t2):
    ws=wb.create_sheet(title)
    ws['A1']=t1; ws['A1'].font=Font(name='Arial',bold=True,size=15); ws['A2']=t2; ws['A2'].font=B
    hdr=['ملاحظة','أقرب صلاحية','آخر شراء','القيمة بالتكلفة','سعر الجمهور','الرصيد','اسم الصنف']
    for j,h in enumerate(hdr,1):
        c=ws.cell(3,j,h); c.font=H; c.fill=PatternFill('solid',fgColor='1F4E78'); c.alignment=Alignment(horizontal='center',vertical='center'); c.border=bd
    for i,(code,r) in enumerate(df.iterrows(),4):
        q=r.qty; q=int(q) if q==int(q) else round(q,1)
        vals=[note(r), r.exp.strftime('%m/%Y') if pd.notna(r.exp) else '', r.lastbuy.strftime('%d/%m') if pd.notna(r.lastbuy) else 'قبل 5/2026', round(r.val), r.price, q, r['name']]
        fill=PatternFill('solid',fgColor='F2F2F2' if i%2 else 'FFFFFF')
        for j,v in enumerate(vals,1):
            c=ws.cell(i,j,v); c.font=R if (j==1 and v) else B; c.fill=fill; c.border=bd
            c.alignment=Alignment(horizontal='right' if j==7 else 'center',vertical='center')
    for col,w in zip('ABCDEFG',[26,13,13,15,12,9,48]): ws.column_dimensions[col].width=w
    ws.freeze_panes='A4'; ws.auto_filter.ref=f'A3:G{3+len(df)}'
    ws.page_setup.orientation='landscape'; ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.print_title_rows='3:3'
    return ws
dd=d[~d.new].sort_values('val',ascending=False); nw=d[d.new].sort_values('val',ascending=False)
wb=openpyxl.Workbook(); wb.remove(wb.active)
ws=wb.create_sheet('ملخص')
live=dd[~(dd.exp_m<=0)]
lines=[('الرواكد — أصناف عليها رصيد وما اتباعتش ولا علبة من 1/8 لـ 31/10',15),
(f'عدد الأصناف: {len(dd):,}    —    قيمتها بالتكلفة ≈ {dd.val.sum():,.0f} ج',14),
(f'منهم منتهي: {(dd.exp_m<=0).sum()} صنف ≈ {dd[dd.exp_m<=0].val.sum():,.0f} ج (ده مش هيتباع — مرتجع أو إعدام)',13),
(f'ينتهي خلال 3 شهور: {((dd.exp_m>0)&(dd.exp_m<=3)).sum()} صنف ≈ {dd[(dd.exp_m>0)&(dd.exp_m<=3)].val.sum():,.0f} ج  ← أولوية أولى',13),
(f'ينتهي من 3 لـ 6 شهور: {((dd.exp_m>3)&(dd.exp_m<=6)).sum()} صنف ≈ {dd[(dd.exp_m>3)&(dd.exp_m<=6)].val.sum():,.0f} ج',13),
(f'أغلى {(live.val>=100).sum()} صنف (قيمة الصنف ≥ 100 ج) = {live[live.val>=100].val.sum():,.0f} ج — يعني ثلثين الفلوس في 30% بس من الأصناف',13),
('',13),
(f'شيت منفصل: {len(nw)} صنف اتشروا آخر 75 يوم ولسه ما اتباعوش ≈ {nw.val.sum():,.0f} ج — دول جداد، مش رواكد',13),
('تنبيه: البيع المتاح 3 شهور بس — أصناف الشتا (برد/أنفلونزا) ممكن تكون موسمية، راجعها قبل ما تبيعها بخصم',13)]
for i,(tx,sz) in enumerate(lines,1):
    c=ws.cell(i,1,tx); c.font=Font(name='Arial',bold=True,size=sz); c.alignment=Alignment(horizontal='right')
ws.column_dimensions['A'].width=110; ws.sheet_view.rightToLeft=True
sheet(wb,'الرواكد',dd,'الرواكد مترتبة بالقيمة (الأغلى فوق)',f'{len(dd):,} صنف ≈ {dd.val.sum():,.0f} ج بالتكلفة — فيه فلتر على كل عمود')
sheet(wb,'اتشرى قريب ومتباعش',nw,'اتشروا آخر 75 يوم ولسه ما اتباعوش',f'{len(nw)} صنف ≈ {nw.val.sum():,.0f} ج')
wb.save('orders_final/الرواكد.xlsx'); print('ok',len(dd),len(nw))
