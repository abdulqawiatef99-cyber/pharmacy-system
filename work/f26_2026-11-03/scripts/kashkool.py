import pandas as pd, numpy as np, math
exec(open('alloc_wk2.py').read().split("pk=t.apply")[0].split("exec(open('build6.py')")[0])  # imports only
t=pd.read_pickle('t_all.pkl'); o=pd.read_pickle('order.pkl'); p=pd.read_pickle('purch.pkl')
src=open('alloc_wk2.py').read(); fn=src[src.index('TH_WH='):src.index('pk=t.apply')]; exec(fn)
# (code, requested qty or None=bare request, note)
R=[(2597,None,'سريبرولايسن 27/10'),(1726,None,'بيزوكارد 27/10'),(9081,None,'ميوكوتك 28/10'),(899,None,'ابيدون 26/10'),
   (13446,1,'توبرايمت دينا 28/10'),(29969,2,'تيراتام دينا 28/10'),(2742,3,'كروميوم 29/10'),(31427,1,'هيبتا كارباميد 29/10'),
   (28762,5,'سليبيز احمد عزت 29/10'),(3111,2,'الحكمة ميت خلف 30/10'),(9371,2,'الحكمة ميت خلف 30/10'),(30957,3,'الحكمة ميت خلف 30/10'),
   (1159,1,'الحكمة ميت خلف 30/10'),(9077,1,'الحكمة ميت خلف 30/10'),(6959,2,'الحكمة ميت خلف 30/10'),(5950,1,'الحكمة ميت خلف 30/10'),
   (13644,2,'تريتكو 31/10'),(29743,None,'ليمتلس ماغنسيوم 31/10'),(3118,4,'كولد فري مستر عمار 31/10'),(30494,2,'ميلاكريست مستر عمار 31/10'),
   (5356,None,'فليكس 31/10'),(9268,None,'نازونكس 31/10'),(466,None,'الفانوفا 1/11'),(9488,2,'نيفيلوب مازن 1/11'),(21174,1,'بلينك مازن 1/11'),(31440,1,'بيلاستيجيك مازن 1/11')]
pn=p.drop_duplicates('code',keep='last').set_index('code')
rows=[]
for code,req,note in R:
    if code in t.index: r=t.loc[code]; bal=r['bal']; fc=r['avg']/30*7; name=r['name']
    else: r=None; bal=0.0; fc=0.0; name=pn.loc[code,'name']
    base=int(o.loc[code,'wk']) if code in o.index else 0
    if req is None:
        add=0 if (base>=1 or bal>=1) else 1
    else:
        short=req-max(bal-fc,0); add=math.ceil(short-0.25) if short>0.25 else 0
    final=base+add
    if code in o.index: rec=o.loc[code,'rec']
    elif r is not None: rec=pick(r)[0]
    else:
        ls=pn.loc[code,'supplier']; rec={'مخزن النهضه':'النهضة','مخزن ابن خلدون':'ابن خلدون','الشركة الوطنية للصيادلة':'الوطنية','ابن سينا':'ابن سينا'}.get(ls,'غير محدد')
    rows.append(dict(code=code,name=name,req=req,bal=round(bal,2),base=base,add=add,final=final,rec=rec,note=note))
k=pd.DataFrame(rows); pd.set_option('display.width',250)
print(k.to_string())
k.to_pickle('kashkool.pkl')
