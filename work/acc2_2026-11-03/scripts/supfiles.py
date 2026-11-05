import pandas as pd,numpy as np
o=open('order.py').read(); from openpyxl.styles import Font,Alignment,PatternFill
exec('def save'+o.split('def save')[1].split("save(X")[0])
A=pd.read_pickle('alloc.pkl')
for s in ['ابن سينا','النهضة','ابن خلدون','الوطنية']:
    d=A[A.sup==s].sort_values('name')
    F=pd.DataFrame({'الصنف':d.name.str.replace(r'\s*#.*','',regex=True),'الكمية':d.qty.astype(int)})
    save(F,f'مسودة_{s}_4-11.xlsx'); print(s,len(F),int(F['الكمية'].sum()))
ask=A[A.sup=='اسأل عليه'][['code','name','qty','price_sys']]; ask.columns=['الكود','الصنف','الكمية','سعرنا']
save(ask,'اسأل_عليها_4-11.xlsx')
# price check: current public price vs our system price
P=A[pd.notna(A.pub)&pd.notna(A.price_sys)].copy(); P['diff']=(P.pub-P.price_sys).round(2)
up=P[P['diff']>=0.5].sort_values('diff',ascending=False)
U=up[['code','name','price_sys','pub','diff']]; U.columns=['الكود','الصنف','سعرنا على السيستم','سعر الجمهور الحالي','الفرق']
U['المصدر']='ابن سينا / i\'Supply'; save(U,'3_أسعار_محتاجة_تحديث.xlsx'); print('price updates',len(U))
N=P[(P.pub-P.price_sys).abs()/P.pub>0.25][['code','name','price_sys','pub','ib_name','is_name']]
N.columns=['الكود','اسمنا على السيستم','سعرنا','سعر الجمهور الحالي','الاسم عند ابن سينا','الاسم عند i\'Supply']
N['المطلوب']='اتأكد من العبوة/الاسم وعدّل'; save(N,'4_أسماء_وعبوات_محتاجة_مراجعة.xlsx'); print('naming',len(N)); print(N[['اسمنا على السيستم','سعرنا','سعر الجمهور الحالي']].to_string())
