"""DEV ONLY (2021-01..2024-10). Pair table, static selection, comparisons on DEV, walk-forward on DEV, personality metrics.
Nothing here reads TEST (>= 2024-11) or HOLDOUT (< 2021) returns; spot pre-2021 closes are used only as signal warm-up."""
import json, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from lib import DEV

HERE = Path(__file__).resolve().parent
t0 = time.time()
D = lib.load(20)
t = D["t"]
print("universe", D["syms"])
R, A, P, pairs = lib.pair_returns(D)
m = (t >= DEV[0]) & (t < DEV[1])
tab = {}
for i, (coin, rule) in enumerate(pairs):
    x = R[m, i][A[m, i]]
    tab.setdefault(coin, {})[rule] = round(lib.sharpe(x), 2)
rules = lib.RULES
print("DEV Sharpe per pair")
print("coin".ljust(10), " ".join(r[:9].rjust(9) for r in rules))
for coin, d in tab.items():
    print(coin.ljust(10), " ".join(f"{d[r]:9.2f}" for r in rules))
print("rule mean ".ljust(10), " ".join(f"{np.nanmean([tab[c][r] for c in tab]):9.2f}" for r in rules))

W, choice = lib.select_static(R, A, t, pairs, DEV)
print("selection", choice, "K=", int(W.sum()))


def show(name, Wv):
    rb = lib.book(R, A, Wv, P)
    tt, x = lib.window(t, rb, DEV)
    s = lib.mstats(lib.monthly(tt, x), x, lib.count_trades(P, Wv, t, DEV))
    print(name.ljust(22), s)
    return s


show("CHOSEN (in-sample)", W)
show("ALL 120 pairs", np.ones(len(pairs)))
for r in rules:
    show("rule " + r, np.array([p[1] == r for p in pairs], float))
print(f"[{time.time()-t0:.0f}s]")
