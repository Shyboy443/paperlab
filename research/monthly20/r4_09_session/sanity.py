import numpy as np
from base import *
from engine import signals, run
from common import summary
d0, d1 = dayidx(DEV_START), dayidx(DEV_END)
# 1) open vs previous close gaps by hour-of-day (alts in U40, DEV)
gap = np.abs(O[1:] / C[:-1] - 1)
hr = (np.arange(1, len(th)) % 24)
sel = slice(d0 * 24, d1 * 24)
for h in (0, 1, 8, 16, 18, 23):
    m = hr[sel] == h
    print("hour", h, "median |open-prevclose| bp", np.nanmedian(gap[sel][m]) * 1e4, "p99", np.nanpercentile(gap[sel][m], 99) * 1e4)
# 2) exit profile for H=18 k=1.5 / H=16 k=2
for H_, k in ((18, 1.5), (16, 2.0)):
    for X in (22, 24, 25, 26, 27, 28, 30, 32, 36, 40):
        if X <= H_: continue
        rows = signals(H_, k, X, "ALT", d0, d1)
        c, tr, _ = run(rows, d0, d1, 5)
        s = summary(c)
        c0, _, _ = run(rows, d0, d1, 5, cost_mult=0)
        print(f"H={H_} k={k} X={X} mean={s['mean_month']:.2f} geo={s['geo_month']:.2f} dd={s['max_dd_pct']:.1f} gross_geo={summary(c0)['geo_month']:.2f} n={len(tr)}")
