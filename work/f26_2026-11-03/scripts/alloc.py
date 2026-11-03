import pandas as pd,numpy as np,re
o=open('order.py').read(); from openpyxl.styles import Font,Alignment,PatternFill
exec('def save'+o.split('def save')[1].split("save(X")[0])
X=pd.read_pickle('order_draft.pkl')
ib={}
for l in open('ibnsina_match.txt').read().strip().split('\n'):
    c,n,p,pp,s=l.split('|'); ib[int(c)]=(n,float(p),float(pp),float(s))
bad_is={31132,31226}
isup={}
for l in open('isup.txt').read().strip().split('\n'):
    f=l.split('|'); c=int(f[0])
    if c in bad_is: continue
    num=lambda v: float(v) if v else np.nan
    isup[c]=dict(t=f[1],p=float(f[2]),top=f[3],N=num(f[4]),K=num(f[5]),W=num(f[6]),T=num(f[7]))
rows=[]
for _,r in X.iterrows():
    c=int(r['الكود']); qty=r['كمية أسبوع']
    if qty<=0: continue
    d=dict(code=c,name=r['الصنف'],qty=qty,price_sys=r['سعر البيع'],last_disc=r['خصم آخر شراء %'],alert=r['تنبيه'])
    opts={}
    if c in ib and ib[c][3]>=5:
        n,p,pp,s=ib[c]; disc=round((1-pp/p)*100,1); d['ib_name']=n; d['pub']=p; d['IS']=disc
        eff=round((1-pp*1.14/p)*100,1); d['tax_q']= abs(eff-20)<0.15  # 29.8% shown = 20% after 14% tax (supplements pattern, verified on Pharma 3/11)
        opts['ابن سينا']=eff if d['tax_q'] else disc
    if c in isup:
        u=isup[c]; d['is_name']=u['t']; d.setdefault('pub',u['p']); d['top']=u['top']
        for k,nm in [('N','النهضة'),('K','ابن خلدون'),('W','الوطنية')]:
            v=u[k]
            if pd.notna(v) and v>=1: opts[nm]=v; d[nm]=v
    if opts:
        best=max(opts,key=opts.get); d['sup']=best; d['disc']=opts[best]
        srt=sorted(opts.values(),reverse=True); d['gap']=round(srt[0]-srt[1],1) if len(srt)>1 else None
    else: d['sup']='اسأل عليه'; d['disc']=np.nan
    rows.append(d)
A=pd.DataFrame(rows)
A['pub']=A['pub'].fillna(A.price_sys)
notes=[]
for _,r in A.iterrows():
    n=[]
    if isinstance(r.alert,str) and r.alert: n.append(r.alert.strip())
    if r.get('tax_q')==True: n.append('ابن سينا: الخصم الظاهر %.1f%% وغالبًا قبل ضريبة 14%% — اتأكد من صفحة الصنف'%r.IS)
    if pd.notna(r.pub) and pd.notna(r.price_sys) and r.pub-r.price_sys>=0.5: n.append('سعرنا %.2f والجمهور الجديد %.2f'%(r.price_sys,r.pub))
    if pd.notna(r.disc) and pd.notna(r.last_disc) and r.disc<r.last_disc-0.5: n.append('الخصم أقل من آخر شراء (%.0f%%)'%r.last_disc)
    if r.pub>=300: n.append('غالي — موافقتك')
    notes.append('، '.join(n))
A['ملاحظات']=notes
A['تكلفة تقريبية']=(A.qty*A.pub*(1-A.disc.fillna(25)/100)).round(1)
A.to_pickle('alloc.pkl')
M=A[['code','name','qty','sup','disc','ابن سينا' if 'ابن سينا' in A else 'IS','IS','النهضة','ابن خلدون','الوطنية','top','pub','price_sys','last_disc','تكلفة تقريبية','ملاحظات']].copy() if False else A[['code','name','qty','sup','disc','IS','النهضة','ابن خلدون','الوطنية','top','pub','price_sys','last_disc','تكلفة تقريبية','ملاحظات']].copy()
M.columns=['الكود','الصنف','الكمية','المورد المقترح','الخصم المختار %','ابن سينا %','النهضة %','ابن خلدون %','الوطنية %','أعلى 3 مخازن (i\'Supply)','سعر الجمهور','سعرنا على السيستم','خصم آخر شراء %','تكلفة تقريبية','ملاحظات']
M=M.sort_values(['المورد المقترح','الصنف'])
save(M,'1_طلبية_4-11_توزيع_الموردين.xlsx')
print(M.groupby('المورد المقترح').agg(n=('الكمية','size'),q=('الكمية','sum'),c=('تكلفة تقريبية','sum')).round())
print('notes',(M['ملاحظات']!='').sum(),'tax_q',int(A.tax_q.fillna(False).sum()))
