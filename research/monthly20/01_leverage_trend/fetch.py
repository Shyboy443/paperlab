"""Fill the shared cache (gently: one process, common.py's rate limiter)."""
import sys, time
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
BIG = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","LTCUSDT"]
t0 = time.time()
for s in BIG:
    klines(s, "1d"); klines(s, "4h"); funding(s)
print("big done", time.time()-t0, flush=True)
syms = sorted(perp_symbols(), key=lambda s: s["onboard"])
syms = [s for s in syms if s["onboard"] < TEST_END - 40*DAY]
for i, s in enumerate(syms):
    try:
        klines(s["symbol"], "1d")
    except Exception as e:
        print("ERR", s["symbol"], e, flush=True)
    if i % 25 == 0:
        print(i, len(syms), s["symbol"], round(time.time()-t0), flush=True)
print("1d done", time.time()-t0, flush=True)
