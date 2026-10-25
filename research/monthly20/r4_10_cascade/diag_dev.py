"""DEV-only diagnostics for FLUSH reversion: OI-condition vs control, long vs short, by year (raw, no costs)."""
import sys, numpy as np
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")
import engine as E
coins = E.load_coins()
for W, Z, V in ((6, 3.5, 3.0), (12, 3.5, 3.0), (12, 5.0, 3.0)):
    feats = {s: E.features(c, W) for s, c in coins.items()}
    for label, lo, hi in (("OI drop>=2%", -9, -0.02), ("OI flat +-1%", -0.01, 0.01), ("OI up>=2%", 0.02, 9), ("any OI", -9, 9)):
        rows = []
        for s, c in coins.items():
            z, vs, doi = feats[s]
            with np.errstate(invalid="ignore"):
                ev = (np.abs(z) >= Z) & (vs >= V) & c.tradable & (doi >= lo) & (doi <= hi)
            last = -10 ** 9
            for k in np.nonzero(ev)[0]:
                e = k + 2
                if e - last < 12 or e + 72 >= c.n or not (E.DEV_START <= c.t[e] < E.DEV_END):
                    continue
                last = e
                d = -int(np.sign(z[k]))
                rows.append((datetime.fromtimestamp(c.t[e] / 1000, timezone.utc).year, d, d * (c.o[e + 36] / c.o[e] - 1)))
        r = np.array(rows)
        out = f"W{W} Z{Z} {label:13s} n={len(r):5d} all={r[:,2].mean()*100:+.3f}"
        for d in (1, -1):
            m = r[:, 1] == d
            out += f" {'long' if d>0 else 'short'}={r[m,2].mean()*100:+.3f}(n{m.sum()})"
        for y in (2021, 2022, 2023, 2024):
            m = r[:, 0] == y
            if m.any():
                out += f" {y}={r[m,2].mean()*100:+.3f}(n{m.sum()})"
        print(out, flush=True)
