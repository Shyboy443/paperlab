"""DEV-only grid for the intraday-momentum family (portfolio, 1x, baseline costs)."""
import numpy as np
from base import *
from engine import signals, run
from common import summary
d0, d1 = dayidx(DEV_START), dayidx(DEV_END)
dm = dayidx(1672531200000)
res = []
for uni, K in (("BE", 2), ("ALT", 5)):
    for H_ in (8, 12, 14, 16, 18, 20, 22):
        for X in (24, 26):
            if X - H_ < 1: continue
            for k in (1.0, 1.5, 2.0, 2.5, 3.0):
                for mode in ("mom", "rev"):
                    rows = signals(H_, k, X, uni, d0, d1, mode)
                    c, tr, _ = run(rows, d0, d1, K)
                    s = summary(c)
                    a = [t for t in tr if t[0] < dm]; b = [t for t in tr if t[0] >= dm]
                    ca, _, _ = run([r for r in rows if r[0] < dm], d0, dm, K); cb, _, _ = run([r for r in rows if r[0] >= dm], dm, d1, K)
                    sa, sb = summary(ca), summary(cb)
                    v = np.array(list(s["months"].values()))
                    t = v.mean() / v.std() * np.sqrt(len(v))
                    line = f"{uni:3s} H={H_:2d} X={X} k={k} {mode} trades={len(tr):5d} mean={s['mean_month']:6.2f} med={s['median_month']:6.2f} geo={s['geo_month']:6.2f} dd={s['max_dd_pct']:5.1f} t={t:5.2f} 21-22geo={sa['geo_month']:6.2f} 23-24geo={sb['geo_month']:6.2f}"
                    print(line, flush=True); res.append((t, line))
print("\n=== top 30 by monthly t ===")
for t, l in sorted(res, reverse=True)[:30]: print(l)
