"""Hourly survivorship-free panel for the r4 short-term reversal study, built ONLY from local caches (no downloads).

Source: agent 07's archive cache data/monthly20_07_pump_scanner/{1h,funding}/SYM.json (every USDT perp that ever had a
data.binance.vision monthly file, delisted coins included), filtered to crypto contracts by r2_rotation/data.py
(TradFi + index contracts dropped). Columns kept = every coin that was ever in the daily top-60 by trailing 30-day quote
volume (r2's universe_mask) from 2021-01-01 on, plus BTCUSDT.

Arrays (hours x coins, float32): o h l c qv (NaN where no bar), fund (funding rate stamped on the hour, 0 otherwise).
The daily top-n mask is computed by r2's universe_mask (known at the day's 00:00 UTC open) and expanded to hours.
Output: data/monthly20_r4_01_reversal/panel_1h.npz
"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from data import build as build_daily, V07  # noqa: E402
import engine_r2 as E2  # noqa: E402
from common import DEV_START, TEST_END, HOUR, DAY, PROJECT  # noqa: E402

OUT = PROJECT / "data" / "monthly20_r4_01_reversal"
H0 = DEV_START - 40 * DAY  # hourly panel starts 40 days before DEV for look-backs
UNIVS = (20, 30, 40, 50, 60)


def build(force=False):
    path = OUT / "panel_1h.npz"
    if path.exists() and not force:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    D = build_daily()
    P = E2.Panel(D)
    masks = {n: E2.universe_mask(P, n) for n in UNIVS}
    d0 = int(np.searchsorted(P.t, H0))
    cols = np.where(masks[60][d0:].any(0))[0]
    btc = list(P.syms).index("BTCUSDT")
    if btc not in cols:
        cols = np.r_[btc, cols]
    syms = [P.syms[j] for j in cols]
    t = np.arange(H0, TEST_END, HOUR, dtype=np.int64)
    T, N = len(t), len(cols)
    arr = {k: np.full((T, N), np.nan, np.float32) for k in ("o", "h", "l", "c", "qv")}
    fund = np.zeros((T, N), np.float32)
    has_f = np.zeros(N, bool)
    for j, s in enumerate(syms):
        rows = json.loads((V07 / "1h" / f"{s}.json").read_text())
        a = np.array([r[:7] for r in rows], dtype=float)
        a = a[(a[:, 0] >= H0) & (a[:, 0] < TEST_END)]
        if len(a):
            i = ((a[:, 0].astype(np.int64) - H0) // HOUR).astype(int)
            for k, c in (("o", 1), ("h", 2), ("l", 3), ("c", 4), ("qv", 6)):
                arr[k][i, j] = a[:, c]
        fp = V07 / "funding" / f"{s}.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            if f:
                fa = np.array(f, dtype=float)
                fi = ((fa[:, 0].astype(np.int64) - H0) // HOUR).astype(int)
                ok = (fi >= 0) & (fi < T)
                np.add.at(fund[:, j], fi[ok], fa[ok, 1]); has_f[j] = True
        if j % 50 == 0:
            print("hourly", j, "/", N, flush=True)
    # coins without funding history: +0.01% every 8h (00/08/16 UTC), longs pay (same rule as r2)
    hrs = ((t // HOUR) % 24)
    for j in np.where(~has_f)[0]:
        fund[hrs % 8 == 0, j] = 0.0001
    # daily universe masks -> hourly, restricted to kept columns
    dayidx = ((t - P.t[0]) // DAY).astype(int)
    um = {f"um{n}": masks[n][dayidx][:, cols] for n in UNIVS}
    out = dict(t=t, syms=np.array(syms), fund=fund, has_f=has_f, **arr, **um,
               trading_today=D["trading_today"][cols])
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return out


if __name__ == "__main__":
    X = build(force="force" in sys.argv)
    print(X["c"].shape, int(X["trading_today"].sum()), "trading today of", len(X["syms"]))
