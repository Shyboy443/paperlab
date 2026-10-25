"""4h panel (bar x coin) built ONLY from agent 07's local data.binance.vision 1h cache, delisted coins included.
Same symbol filter as r2_rotation/data.py (TradFi + index contracts dropped). 4h bars aligned to 00/04/.../20 UTC.
Output: data/monthly20_r4_volregime/panel_4h.npz
"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DATA_FROM, TEST_END, HOUR, PROJECT
from data import eligible_symbols, V07, EI

OUT = PROJECT / "data" / "monthly20_r4_volregime"
STEP = 4 * HOUR


def build(force=False):
    path = OUT / "panel_4h.npz"
    if path.exists() and not force:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    syms, dropped, ei = eligible_symbols()
    t = np.arange(DATA_FROM, TEST_END, STEP, dtype=np.int64)
    T, N = len(t), len(syms)
    o, h, l, c, qv = (np.full((T, N), np.nan, dtype=np.float64) for _ in range(5))
    fund = np.zeros((T, N), dtype=np.float32); has_f = np.zeros(N, bool); first = np.zeros(N, np.int64)
    for j, s in enumerate(syms):
        rows = json.loads((V07 / "1h" / f"{s}.json").read_text())
        if rows:
            a = np.array([r[:7] for r in rows], dtype=float)
            a = a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < TEST_END)]
            if len(a):
                first[j] = int(a[0, 0])
                d = ((a[:, 0].astype(np.int64) - DATA_FROM) // STEP).astype(int)
                u, i0 = np.unique(d, return_index=True)
                i1 = np.r_[i0[1:] - 1, len(d) - 1]
                o[u, j] = a[i0, 1]; c[u, j] = a[i1, 4]
                h[u, j] = np.maximum.reduceat(a[:, 2], i0); l[u, j] = np.minimum.reduceat(a[:, 3], i0)
                qv[u, j] = np.add.reduceat(a[:, 6], i0)
        fp = V07 / "funding" / f"{s}.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            if f:
                fa = np.array(f, dtype=float)
                fi = ((fa[:, 0].astype(np.int64) - DATA_FROM) // STEP).astype(int)
                ok = (fi >= 0) & (fi < T)
                np.add.at(fund[:, j], fi[ok], fa[ok, 1]); has_f[j] = True
        if j % 100 == 0:
            print("panel4h", j, "/", N, flush=True)
    # coins without funding history: +0.01% per 8h (longs pay), same rule as agent 02 / r2
    nf = ~has_f
    fund[:, nf] = np.where((t % (8 * HOUR) == 0)[:, None], 0.0001, 0.0).astype(np.float32)
    today = {x["symbol"] for x in json.loads(EI.read_text(encoding="utf-8"))
             if x.get("status") == "TRADING" and x.get("contractType") == "PERPETUAL"}
    out = dict(t=t, syms=np.array(syms), o=o, h=h, l=l, c=c, qv=qv, fund=fund, has_f=has_f, first=first,
               trading_today=np.array([s in today for s in syms]))
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return out


if __name__ == "__main__":
    P = build(force="force" in sys.argv)
    print(P["c"].shape, int(P["trading_today"].sum()), "trading today")
