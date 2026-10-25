import numpy as np
from base import *
from engine import signals, run
from common import summary
d0, d1 = dayidx(DEV_START), dayidx(DEV_END)
def rows_entry(H_, k, E, X, uni="ALT", mode="mom"):
    rs = signals(H_, k, X, uni, d0, d1, mode)
    return [(d, d * 24 + E, ix, j, s) for (d, ie, ix, j, s) in rs]
for H_, k in ((16, 2.0), (18, 1.5)):
    rows = signals(H_, k, 26, "ALT", d0, d1)
    c, tr, _ = run(rows, d0, d1, 5)
    tr = np.array([(t[0], t[2], t[4]) for t in tr])
    yrs = np.array([datetime.fromtimestamp(td[int(d)] / 1000, timezone.utc).year for d in tr[:, 0]])
    for y in range(2021, 2025):
        m = yrs == y
        print(H_, k, y, "n", m.sum(), "long n", (tr[m, 1] > 0).sum(), "sum ret% long", tr[m & (tr[:, 1] > 0), 2].sum() * 100, "short", tr[m & (tr[:, 1] < 0), 2].sum() * 100)
    print({y: v for y, v in summary(c)["months"].items()})
# entry/exit matrix
for H_, k in ((16, 2.0), (18, 1.5), (12, 2.0)):
    for E in sorted({H_, 20, 22, 23}):
        if E < H_: continue
        line = []
        for X in (24, 25, 26, 28):
            c, tr, _ = run(rows_entry(H_, k, E, X), d0, d1, 5)
            s = summary(c); line.append(f"X={X}:{s['geo_month']:6.2f}/{s['max_dd_pct']:4.0f}")
        print(f"H={H_} k={k} E={E}", " ".join(line))
