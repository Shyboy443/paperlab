"""4h and 1d bar panels (price + premium + funding) for every perp ever in the top-40 universe, delisted coins included.

Prices/funding: agent 07's data.binance.vision 1h cache (data/monthly20_07_pump_scanner). Premium: our own
premiumIndexKlines 1h cache (data/monthly20_r4_premium/1h). Bars are aggregated from 1h:
  o first open, h max, l min, c last close, qv sum, fund = sum of funding stamps inside the bar,
  pc = premium close of the last hour in the bar, pm = mean of the hourly premium closes in the bar.
Cache: data/monthly20_r4_premium/panel_{4h,1d}.npz
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from data import build, V07
from common import DATA_FROM, TEST_END, DAY, HOUR, PROJECT

OUT = PROJECT / "data" / "monthly20_r4_premium"


def make(step_h, force=False):
    tag = {4: "4h", 24: "1d"}[step_h]
    path = OUT / f"panel_{tag}.npz"
    if path.exists() and not force:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    D = build()
    syms = sorted(p.stem for p in (OUT / "1h").glob("*.json"))
    dsyms = [str(s) for s in D["syms"]]
    syms = [s for s in syms if s in dsyms]
    step = step_h * HOUR
    t = np.arange(DATA_FROM, TEST_END, step, dtype=np.int64)
    T, N = len(t), len(syms)
    o, h, l, c, qv, pc, pm = (np.full((T, N), np.nan) for _ in range(7))
    fund = np.zeros((T, N)); first = np.zeros(N, np.int64)
    for j, s in enumerate(syms):
        jj = dsyms.index(s); first[j] = D["first"][jj]
        rows = json.loads((V07 / "1h" / f"{s}.json").read_text())
        a = np.array([r[:7] for r in rows], dtype=float)
        a = a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < TEST_END)]
        if len(a):
            d = ((a[:, 0].astype(np.int64) - DATA_FROM) // step).astype(int)
            u, i0 = np.unique(d, return_index=True); i1 = np.r_[i0[1:] - 1, len(d) - 1]
            o[u, j] = a[i0, 1]; c[u, j] = a[i1, 4]
            h[u, j] = np.maximum.reduceat(a[:, 2], i0); l[u, j] = np.minimum.reduceat(a[:, 3], i0)
            qv[u, j] = np.add.reduceat(a[:, 6], i0)
        pr = json.loads((OUT / "1h" / f"{s}.json").read_text())
        if pr:
            b = np.array(pr, dtype=float)
            b = b[(b[:, 0] >= DATA_FROM) & (b[:, 0] < TEST_END)]
            if len(b):
                d = ((b[:, 0].astype(np.int64) - DATA_FROM) // step).astype(int)
                u, i0 = np.unique(d, return_index=True); i1 = np.r_[i0[1:] - 1, len(d) - 1]
                pc[u, j] = b[i1, 4]
                pm[u, j] = np.add.reduceat(b[:, 4], i0) / np.diff(np.r_[i0, len(d)])
        fp = V07 / "funding" / f"{s}.json"
        f = json.loads(fp.read_text()) if fp.exists() else []
        if f:
            fa = np.array(f, dtype=float)
            fi = ((fa[:, 0].astype(np.int64) - DATA_FROM) // step).astype(int)
            ok = (fi >= 0) & (fi < T)
            np.add.at(fund[:, j], fi[ok], fa[ok, 1])
        else:
            fund[:, j] = 0.0001 * step_h / 8
    out = dict(t=t, syms=np.array(syms), o=o, h=h, l=l, c=c, qv=qv, pc=pc, pm=pm, fund=fund, first=first, step=np.int64(step))
    np.savez_compressed(path, **out)
    return out


if __name__ == "__main__":
    for s in (24, 4):
        P = make(s, force=True)
        print(s, P["c"].shape, np.isfinite(P["pc"]).sum(), np.isfinite(P["c"]).sum())
