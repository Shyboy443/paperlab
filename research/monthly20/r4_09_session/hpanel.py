"""Hourly panel (hour x coin) for every coin that was ever in the point-in-time top-40 (30-day quote volume)
of the survivorship-free daily panel (r2_rotation/data.py: agent 07's data.binance.vision cache incl. delisted coins).
Also stores the daily top-40 / top-20 membership masks (known at each day's 00:00 UTC open) and hourly funding.
Cache: data/monthly20_r4_session/hpanel.npz"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DATA_FROM, TEST_END, DAY, HOUR, PROJECT
import engine_r2 as E
from data import build, V07

OUT = PROJECT / "data" / "monthly20_r4_session" / "hpanel.npz"


def make(force=False):
    if OUT.exists() and not force:
        z = np.load(OUT, allow_pickle=False)
        return {k: z[k] for k in z.files}
    D = build(); P = E.Panel(D)
    U40 = E.universe_mask(P, 40); U20 = E.universe_mask(P, 20)
    cols = np.where(U40.any(0))[0]
    syms = [P.syms[j] for j in cols]
    th = np.arange(DATA_FROM, TEST_END, HOUR, dtype=np.int64)
    T, N = len(th), len(cols)
    arr = {k: np.full((T, N), np.nan, np.float32) for k in ("o", "h", "l", "c", "qv")}
    fund = np.zeros((T, N), np.float32); has_f = np.zeros(N, bool)
    for j, s in enumerate(syms):
        a = np.array([r[:7] for r in json.loads((V07 / "1h" / f"{s}.json").read_text())], dtype=float)
        a = a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < TEST_END)]
        i = ((a[:, 0].astype(np.int64) - DATA_FROM) // HOUR).astype(int)
        for k, c in (("o", 1), ("h", 2), ("l", 3), ("c", 4), ("qv", 6)):
            arr[k][i, j] = a[:, c]
        fp = V07 / "funding" / f"{s}.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            if f:
                fa = np.array(f, dtype=float)
                # a funding stamp at time ts is charged to a position held across ts: attribute it to the hour containing ts
                fi = ((fa[:, 0].astype(np.int64) - DATA_FROM) // HOUR).astype(int)
                ok = (fi >= 0) & (fi < T)
                np.add.at(fund[:, j], fi[ok], fa[ok, 1]); has_f[j] = True
        if j % 50 == 0:
            print("hpanel", j, "/", N, flush=True)
    # coins without funding history: +0.01% per 8h stamp at 00/08/16 (longs pay), as agent 02's rule
    hrs = ((th // HOUR) % 24)
    fund[:, ~has_f] = np.where(np.isin(hrs, [0, 8, 16]), 0.0001, 0.0)[:, None]
    out = dict(th=th, td=P.t, syms=np.array(syms), U40=U40[:, cols], U20=U20[:, cols], fund=fund, has_f=has_f, **arr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, **out)
    return out


if __name__ == "__main__":
    H = make(force="force" in sys.argv)
    print(len(H["syms"]), "coins", H["c"].shape)
