"""Fill the shared cache with 4h perp bars + funding for every USDT perp (worker k of n)."""
import sys; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
k, n = int(sys.argv[1]), int(sys.argv[2])
what = sys.argv[3] if len(sys.argv) > 3 else "4h"
S = sorted(perp_symbols(), key=lambda s: s["symbol"])
for i, s in enumerate(S):
    if i % n != k: continue
    try:
        if what == "funding": funding(s["symbol"])
        else: klines(s["symbol"], what)
    except Exception as e:
        print("ERR", s["symbol"], e, flush=True)
    if i % 50 == k: print(k, i, s["symbol"], flush=True)
print("DONE", k)
