"""Worker 2 improvement round: where does rule P make and lose money? DEV window only (no TEST numbers printed
except the final totals line, which are already known from session/results.json)."""
import sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
from core import *  # noqa

P = dict(SEL, sig_ref=0.01154)


def split(tr, rows, key, label):
    rmap = {(r[0], r[3]): r for r in rows}
    g = {}
    for t in tr:
        k = key(t, rmap[(t[0], t[1])])
        a = g.setdefault(k, [0, 0.0, 0.0])
        a[0] += 1; a[1] += t[4]; a[2] += t[6]
    print(f"\n{label}")
    for k in sorted(g):
        n, s, r = g[k]
        print(f"  {str(k):>14}  trades {n:5d}  sum pnl/eq {s*100:8.1f}%  mean trade ret {r/n*100:6.2f}%")


for tag, (a, b) in (("DEV", (d0, dT)),):
    c, tr, _, rows = sim(P, a, b)
    print(tag, "net", round(c[-1][1] / c[0][1] * 100 - 100, 1), "%")
    split(tr, rows, lambda t, r: "long" if t[2] > 0 else "short", "side")
    split(tr, rows, lambda t, r: datetime.fromtimestamp(td[t[0]] / 1000, timezone.utc).strftime("%a"), "weekday")
    nday = {}
    for r in rows:
        nday[r[0]] = nday.get(r[0], 0) + 1
    split(tr, rows, lambda t, r: min(nday[t[0]], 8), "signals that day (8 = 8+)")

    def btcday(t, r):
        bb = t[0] * 24
        x = np.log(C[bb + 15, BTC] / O[bb, BTC])
        same = np.sign(x) == t[2]
        return ("btc_same_dir" if same else "btc_against") + ("_big" if abs(x) > 0.02 else "")
    split(tr, rows, btcday, "BTC 00-16h move vs trade side")

    def btc_trend(t, r):
        d = t[0]
        x = np.log(O[d * 24, BTC] / O[(d - 30) * 24, BTC])
        return ("up30" if x > 0 else "down30") + ("_long" if t[2] > 0 else "_short")
    split(tr, rows, btc_trend, "BTC 30d trend x side")
