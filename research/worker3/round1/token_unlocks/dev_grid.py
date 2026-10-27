"""DEV-only portfolio grid (2021-01..2024-10). Never touches TEST."""
import itertools, json
from lib import *
rows=[]
for mn,X,Y,h,cap in itertools.product((0.02,0.05,0.10),(14,21,30,45),(0,3,7),(0,1),(0.2,0.33,0.5)):
    tr=candidates(mn,X=X,Y=Y)
    r,_=book(tr,cap=cap,hedge=bool(h))
    s=stats(monthly(r,DEV_START,DEV_END)); s2=stats(monthly(book(tr,cap=cap,hedge=bool(h),cost_mult=2)[0],DEV_START,DEV_END))
    ntr=sum(1 for t in tr if DEV_START<=T[t[0]]<DEV_END)
    rows.append(dict(mn=mn,X=X,Y=Y,hedge=h,cap=cap,ntr=ntr,**s,geo2=s2['geo']))
rows.sort(key=lambda r:-r['geo'])
for r in rows: print(r)
json.dump(rows,open('dev_grid.json','w'))
