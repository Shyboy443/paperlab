"""DEV-only event study (trades fully inside 2021-01..2024-10). Prints count, mean/median short return, hit rate."""
import numpy as np
from lib import *
dev_end_d = int((DEV_END - DATA_FROM) // DAY); dev_start_d = int((DEV_START - DATA_FROM) // DAY)
def tr(a,b,j,hedge):
    g = O[b,j]/O[a,j]-1; f = FUND[a:b,j].sum()
    r = -g + f - 2*(FEE+SLIP)
    if hedge: r += (O[b,BTC]/O[a,BTC]-1) - FUND[a:b,BTC].sum() - 2*(FEE+SLIP)
    return r
for mn in (0.005, 0.01, 0.02, 0.05, 0.10):
  for X,Y in ((30,0),(14,0),(7,0),(7,3),(3,3),(1,1),(0,7),(0,14),(14,7)):
    for h in (0,1):
      c=[t for t in candidates(mn,X=X,Y=Y) if t[0]>=dev_start_d and t[1]<dev_end_d]
      r=np.array([tr(a,b,j,h) for a,b,j,e in c])
      if len(r)<3: continue
      print(f"size>={mn:.3f} X={X:2d} Y={Y:2d} hedge={h} n={len(r):3d} coins={len({x[2] for x in c}):2d} mean={r.mean()*100:6.2f}% med={np.median(r)*100:6.2f}% hit={np.mean(r>0)*100:4.0f}% t={r.mean()/r.std(ddof=1)*np.sqrt(len(r)):5.2f}")
