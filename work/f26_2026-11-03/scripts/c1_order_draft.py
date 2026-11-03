import pandas as pd,numpy as np,re
from openpyxl.styles import Font,Alignment,PatternFill
T=pd.read_pickle('T.pkl');months=[c for c in T.columns if str(c).startswith('20')]
# arrivals not yet in E-Stock (Ibn Khaldun 150292 + 150745) by code
arr={21174:2,29717:1,7214:1,10744:1,30473:1,29489:1,30790:2,5356:1,29743:2,29157:1,9393:1,29279:1}
S=pd.read_pickle('S.pkl');lastp=S.sort_values('month').groupby('code').price.last();T['price']=T.price.fillna(lastp)
T['arrived']=pd.Series(arr).reindex(T.index).fillna(0)
T['stock2']=T.stock+T.arrived
T['avg3']=T[months[-3:]].sum(axis=1)/3; T['avg6']=T[months[-6:]].sum(axis=1)/6
T['avg']=T[['avg3','avg6']].max(axis=1)   # protect against one weak month
need=lambda days: np.ceil(np.maximum(T.avg*days/30-T.stock2,0)-1e-9)
T['wk']=need(7);T['wk2']=need(14);T['mo']=need(30)
fridge=re.compile(r'ثلاجة|INSUL|MIXTARD|LANTUS|NOVORAPID|NOVOMIX|HUMULIN|HUMALOG|APIDRA|TOUJEO|RYZODEG|TRESIBA|LEVEMIR|VACC|OZEMPIC|SAXENDA|VICTOZA|TRULICITY|MOUNJARO|EPREX|RECORMON|PROLIA|ENBREL|HUMIRA|SYNAGIS|ANTI.?D|RHOPHYLAC|ROTARIX|INFANRIX|ENGERIX|ACTRAPID|INSULATARD|GONAL|MENOPUR|OVITRELLE|CETROTIDE|PUREGON|CHORIOMON|PREGNYL|DIPHERELINE|ZOLADEX|NEUPOGEN|CALCITONIN|MIACALCIC|LIRAGLU|SEMAGLU|DULAGLU|TIRZEP|ERYTHROPO|LATANO|XALATAN|XALACOM|TRAVATAN|LUMIGAN',re.I)
O=T[T['class'].isin(['منتظم','شبه منتظم'])&(T.wk2>0)].copy()
K={30494:'ميلاكريست',5342:'فليكتور',5786:'جاست ريج',30390:'اكسيدوجاست',1319:'ازروليد',29147:'اومنيفورا',3195:'بانادول اكيوت',8896:'مكسترد فايل',2149:'كاندالكان (8؟ اتأكد التركيز)',1726:'بيزوكارد (2.5؟ اتأكد التركيز)'}
T['kash']=pd.Series(K).reindex(T.index)
supp=T.name.str.split('#').str[0].str.contains(r'[\u0600-\u06FF]',na=False)
O=T[((T['class'].isin(['منتظم','شبه منتظم'])&(T.wk>0))|T.kash.notna())&~supp].copy()
for c in K: 
    for q in ['wk','wk2','mo']: O.loc[c,q]=max(O.loc[c,q],1.0)
pend_ibnsina=r'BARICT|ANAFR.*75|SERVITAM|APICO|ROSITOR\s*20|ROSUTOR\s*20'
O['تنبيه']=O.kash.apply(lambda k: f'كشكول: {k}. ' if isinstance(k,str) else '')
O.loc[O.price>=300,'تنبيه']+='غالي ≥300 — موافقتك. '
O.loc[O.name.str.contains(fridge,na=False),'تنبيه']+='تلاجة؟ — موافقتك. '
O.loc[O.name.str.contains(pend_ibnsina,case=False,na=False,regex=True),'تنبيه']+='في طلبية ابن سينا 3/11 اللي لسه ما وصلتش. '
O.loc[O['flags']!='','تنبيه']+=O['flags']+'. '
O.loc[O.name.str.split('#').str[0].str.contains(r'[\u0600-\u06FF]',na=False)&O.kash.isna(),'تنبيه']+='مستلزمات (مؤجلة؟). '
O['k']=O.kash.notna(); O=O.sort_values(['k','class','q3'],ascending=[False,False,False])
cols={'name':'الصنف','class':'التصنيف','stock':'الرصيد E-Stock','arrived':'وصل ومش متسجل','avg':'متوسط بيع الشهر','wk':'كمية أسبوع','wk2':'كمية أسبوعين','mo':'كمية شهر','price':'سعر البيع','last_buy':'آخر شراء','last_disc':'خصم آخر شراء %','تنبيه':'تنبيه'}
X=O.reset_index()[['code']+list(cols)].rename(columns={'code':'الكود',**cols})
X['آخر شراء']=pd.to_datetime(X['آخر شراء']).dt.date
for c in ['كمية أسبوع','كمية أسبوعين','كمية شهر']: X[c]=X[c].abs()
for c in ['الرصيد E-Stock','متوسط بيع الشهر','سعر البيع','خصم آخر شراء %']: X[c]=X[c].round(2)
X.to_pickle('order_draft.pkl')
def save(df,fn):
    with pd.ExcelWriter(fn,engine='openpyxl') as w:
        df.to_excel(w,index=False,sheet_name='الشيت')
        ws=w.book.active; ws.sheet_view.rightToLeft=True; ws.freeze_panes='B2'
        for row in ws.iter_rows():
            for c in row: c.font=Font(size=14,bold=True)
        for c in ws[1]: c.fill=PatternFill('solid',fgColor='FFE699'); c.alignment=Alignment(wrap_text=True,vertical='center')
        for cc in ws.columns:
            L=max(len(str(c.value or '')) for c in cc[:200]); ws.column_dimensions[cc[0].column_letter].width=min(max(10,L*1.3),45)
save(X,'1_مسودة_طلبية_4-11.xlsx')
import pickle;pickle.dump(save,open('/dev/null','wb')) if False else None
print(len(X),'lines; week lines',(X['كمية أسبوع']>0).sum(),'| alerts',(X['تنبيه']!='').sum())
print('approx cost week',round((O.wk*O.cost.fillna(O.last_cost)).sum()),'2wk',round((O.wk2*O.cost.fillna(O.last_cost)).sum()),'month',round((O.mo*O.cost.fillna(O.last_cost)).sum()))
print(X[X['تنبيه']!=''][['الصنف','كمية أسبوع','سعر البيع','تنبيه']].to_string())
