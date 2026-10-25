"""Fill the shared cache (only missing files are fetched). python fetch.py <interval|funding> <worker> <nworkers> [symbols_json] [reverse]"""
import sys, json
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
iv, w, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
syms = json.load(open(sys.argv[4])) if len(sys.argv) > 4 and sys.argv[4] != "-" else \
    [s["symbol"] for s in perp_symbols() if s["onboard"] < TEST_END - 30 * DAY]
syms = sorted(syms)
if len(sys.argv) > 5: syms = syms[::-1]
syms = syms[w::n]
for i, s in enumerate(syms):
    try:
        if iv == "funding": funding(s)
        else:
            if not (CACHE / "perp" / iv / f"{s}.json").exists(): klines(s, iv)
    except Exception as e:
        print("ERR", s, e, flush=True)
    if i % 20 == 0: print(w, i, len(syms), s, flush=True)
print("DONE", w, flush=True)
