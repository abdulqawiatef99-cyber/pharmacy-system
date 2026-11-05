import pandas as pd, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
r=pd.read_pickle('price_all.pkl'); hi=pd.read_pickle('hi_checked.pkl')
B=Font(name='Arial',bold=True,size=13); H=Font(name='Arial',bold=True,size=14,color='FFFFFF'); RD=Font(name='Arial',bold=True,size=13,color='C00000'); GR=Font(name='Arial',bold=True,size=13,color='00703C')
thin=Side(style='thin',color='999999'); bd=Border(left=thin,right=thin,top=thin,bottom=thin)
def stat(c,x):
    if c in hi.index:
        v=hi.at[c,'v']; return {'مطابق':'سعرك أعلى — مطابق، راجعه','مش متأكد':'سعرك أعلى — مش متأكد من المطابقة','صنف مختلف':'صنف مختلف — اتجاهل'}[v]
    ok=x.score>=1.9 and 0.6<=x.ratio<=2.0
    if not ok: return 'مطابقة ضعيفة — اتجاهل'
    if x.newp>x.price+0.5: return 'سعرك أقل — زوّده' if x.score>=3.9 else 'سعرك أقل — اتأكد إنه نفس الصنف'
    return 'زي السوق'
r['st']=[stat(c,x) for c,x in r.iterrows()]
r['diff']=((r.newp-r.price)*r.qty).round()
order={'سعرك أقل — زوّده':0,'سعرك أقل — اتأكد إنه نفس الصنف':1,'سعرك أعلى — مطابق، راجعه':2,'سعرك أعلى — مش متأكد من المطابقة':3,'زي السوق':4,'مطابقة ضعيفة — اتجاهل':5,'صنف مختلف — اتجاهل':6}
r['o']=r.st.map(order); r=r.sort_values(['o','diff'],ascending=[True,False])
print(r.st.value_counts())
f='orders_final/الصلاحية_والأسعار.xlsx'; wb=openpyxl.load_workbook(f)
for n in ['كل الأصناف (بحث)','سعرك أعلى (مراجَع)']:
    if n in wb.sheetnames: wb.remove(wb[n])
def mk(title,df,t1,t2,pos):
    ws=wb.create_sheet(title,pos)
    ws['A1']=t1; ws['A1'].font=Font(name='Arial',bold=True,size=15); ws['A2']=t2; ws['A2'].font=B
    hdr=['الحالة','الفرق في الرصيد','سعر السوق','سعرك','الرصيد','اسم الصنف']
    for j,h in enumerate(hdr,1):
        c=ws.cell(3,j,h); c.font=H; c.fill=PatternFill('solid',fgColor='1F4E78'); c.alignment=Alignment(horizontal='center',vertical='center'); c.border=bd
    for i,(c0,x) in enumerate(df.iterrows(),4):
        q=x.qty; q=int(q) if q==int(q) else round(q,1)
        vals=[x.st,int(x['diff']),x.newp,x.price,q,x['name']]
        fill=PatternFill('solid',fgColor='F2F2F2' if i%2 else 'FFFFFF')
        for j,v in enumerate(vals,1):
            c=ws.cell(i,j,v); c.fill=fill; c.border=bd; c.alignment=Alignment(horizontal='right' if j==6 else 'center',vertical='center')
            c.font=(GR if 'زوّده' in x.st else RD if 'مطابق، راجعه' in x.st else B) if j==1 else B
    for col,w in zip('ABCDEF',[34,15,12,11,9,48]): ws.column_dimensions[col].width=w
    ws.freeze_panes='A4'; ws.auto_filter.ref=f'A3:F{3+len(df)}'
    ws.page_setup.orientation='landscape'; ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.print_title_rows='3:3'
h2=r[r.index.isin(hi.index)]
mk('سعرك أعلى (مراجَع)',h2,'الأصناف اللي سعرك فيها أعلى من i\'Supply — بعد مراجعة المطابقة واحد واحد',
   f'من {len(h2)} صنف: {(hi.v=="مطابق").sum()} مطابق فعلًا | {(hi.v=="مش متأكد").sum()} مش متأكد | {(hi.v=="صنف مختلف").sum()} صنف مختلف (البحث جاب حاجة تانية)',1)
mk('كل الأصناف (بحث)',r,f'كل الأصناف اللي اتبحث عليها — {len(r):,} صنف في جدول واحد','مترتبة: اللي محتاج تزوّده الأول، وبعده اللي تراجعه، وفي الآخر اللي تتجاهله — فيه فلتر على كل عمود',1)
wb.save(f); print(wb.sheetnames)
