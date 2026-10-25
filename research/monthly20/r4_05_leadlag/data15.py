"""load('1h') -> the hourly panel (panel.py). load('15m') -> a 15m panel for the 85-coin subset (fetch15.py), with the
same point-in-time daily volume rank as the hourly panel (rank among ALL perps, delisted included).
Cached to data/monthly20_r4_leadlag/panel_15m.npz."""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import TEST_END, DAY, PROJECT
from panel import build, OUT, T0

M15 = 900_000


def load(tf):
    if tf == "1h":
        return build()
    path = OUT / "panel_15m.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    H = build()
    files = sorted((OUT / "15m").glob("*.npy"))
    syms = np.array([f.stem for f in files])
    t = np.arange(T0, TEST_END, M15, dtype=np.int64)
    T, N = len(t), len(syms)
    o, h, l, c = (np.full((T, N), np.nan, np.float32) for _ in range(4))
    fund = np.zeros((T, N), np.float32)
    rank = np.full((T, N), 999, np.int16)
    hs = list(H["syms"])
    for j, f in enumerate(files):
        a = np.load(f)
        if not len(a):
            continue
        a = a[(a[:, 0] >= T0) & (a[:, 0] < TEST_END)]
        ix = ((a[:, 0].astype(np.int64) - T0) // M15).astype(int)
        o[ix, j], h[ix, j], l[ix, j], c[ix, j] = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        k = hs.index(syms[j])
        # funding stamps and rank from the hourly panel (rank is daily, funding stamped on its hour)
        fund[::4, j] = H["fund"][:, k][: len(fund[::4, j])]
        rank[:, j] = np.repeat(H["rank"][:, k], 4)[:T]
    out = dict(t=t, syms=syms, o=o, h=h, l=l, c=c, fund=fund, rank=rank)
    np.savez(path, **out)
    return out
