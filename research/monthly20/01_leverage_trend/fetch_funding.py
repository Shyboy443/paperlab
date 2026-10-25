import sys, time
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/01_leverage_trend")
from run import *
p = pool_for(30); p += [s for s in pool_for(50) if s not in p]
t0 = time.time()
for i, s in enumerate(p):
    try:
        funding(s)
    except Exception as e:
        print("ERR", s, e, flush=True)
    print(i, len(p), s, round(time.time() - t0), flush=True)
print("funding done", flush=True)
