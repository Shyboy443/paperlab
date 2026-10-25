"""Download helper (shared cache via common.py). usage: python fetch.py daily|funding|bars <syms file>"""
import sys, json, random
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
mode = sys.argv[1]
syms = [s["symbol"] for s in perp_symbols()]
if mode == "daily":
    random.seed(3); random.shuffle(syms)
    for i, s in enumerate(syms):
        try: klines(s, "1d")
        except Exception as e: print("ERR", s, e, flush=True)
        if i % 50 == 0: print("daily", i, flush=True)
elif mode in ("funding", "funding_rev"):
    if mode == "funding_rev": syms = syms[::-1]
    for i, s in enumerate(syms):
        try: funding(s)
        except Exception as e: print("ERR", s, e, flush=True)
        if i % 50 == 0: print("funding", i, flush=True)
elif mode == "bars":
    want = json.load(open(sys.argv[2]))[int(sys.argv[3])::int(sys.argv[4])]
    for i, (p, sp) in enumerate(want):
        try: klines(p, "1d")
        except Exception as e: print("ERR perp", p, e, flush=True)
        if sp:
            try: klines(sp, "1d", venue="spot")
            except Exception as e: print("ERR spot", sp, e, flush=True)
        try: funding(p)
        except Exception as e: print("ERR fund", p, e, flush=True)
        print("bars", i, p, flush=True)
print("DONE", mode, flush=True)
