"""Fill the shared cache: python fetch.py <interval> <worker> <nworkers> [symbols_file]"""
import sys, json, os
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
iv, w, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
if len(sys.argv) > 4:
    syms = json.load(open(sys.argv[4]))
else:
    syms = [s["symbol"] for s in perp_symbols() if s["onboard"] < TEST_END - 30 * DAY]
syms = sorted(syms, reverse=os.environ.get("REV") == "1")[w::n]
for i, s in enumerate(syms):
    try:
        if iv == "funding":
            funding(s)
        else:
            klines(s, iv)
    except Exception as e:
        print("ERR", ascii(s), e, flush=True)
    if i % 20 == 0:
        print(w, i, len(syms), ascii(s), flush=True)
print("DONE", w, flush=True)
