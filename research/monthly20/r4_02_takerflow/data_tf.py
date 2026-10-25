"""Hourly / 4h / daily panels WITH taker-buy quote volume, survivorship-free, from local archive caches only.

Source: agent 07's data.binance.vision 1h cache (data/monthly20_07_pump_scanner/1h/SYM.json, rows
[open_ms, o, h, l, c, base_vol, quote_vol, trades, taker_buy_quote]) incl. delisted coins, plus its funding cache.
Non-crypto contracts dropped with r2_rotation/data.py's eligible_symbols() (same universe as round 2).
Output: data/monthly20_r4_takerflow/panel_{1h,4h,1d}.npz (float32), all bars aligned on UTC grids.
"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import TEST_END, DAY, HOUR, utc, PROJECT
from data import eligible_symbols, V07

OUT = PROJECT / "data" / "monthly20_r4_takerflow"
T0 = utc(2020, 12, 1)
FIELDS = ("o", "h", "l", "c", "qv", "tbq", "fund")


def build_1h():
    syms, _, _ = eligible_symbols()
    t = np.arange(T0, TEST_END, HOUR, dtype=np.int64)
    T, N = len(t), len(syms)
    A = {k: np.full((T, N), np.nan, np.float32) for k in FIELDS[:-1]}
    fund = np.zeros((T, N), np.float32); has_f = np.zeros(N, bool); first = np.zeros(N, np.int64)
    for j, s in enumerate(syms):
        rows = json.loads((V07 / "1h" / f"{s}.json").read_text())
        if not rows:
            continue
        a = np.array([r[:5] + [r[6], r[8]] for r in rows], dtype=float)
        a = a[(a[:, 0] >= T0) & (a[:, 0] < TEST_END)]
        if not len(a):
            continue
        first[j] = int(a[0, 0])
        i = ((a[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
        for k, col in zip(("o", "h", "l", "c", "qv", "tbq"), range(1, 7)):
            A[k][i, j] = a[:, col]
        fp = V07 / "funding" / f"{s}.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            if f:
                fa = np.array(f, dtype=float)
                fi = ((fa[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
                ok = (fi >= 0) & (fi < T)
                np.add.at(fund[:, j], fi[ok], fa[ok, 1].astype(np.float32)); has_f[j] = True
        if j % 100 == 0:
            print("1h", j, "/", N, flush=True)
    # coins without funding history: +0.01%/8h (longs pay), the round-2 rule
    nf = ~has_f
    fund[(t // HOUR % 8 == 0)[:, None] & nf[None, :]] = 0.0001
    A["fund"] = fund
    return dict(t=t, syms=np.array(syms), first=first, **A)


def aggregate(P, k):
    """k hourly bars -> one bar (grid aligned to 00:00 UTC because T0 is a midnight)."""
    T = len(P["t"]) // k * k
    out = dict(t=P["t"][:T:k], syms=P["syms"], first=P["first"])
    sh = lambda x: x[:T].reshape(T // k, k, -1)
    with np.errstate(all="ignore"):
        import warnings
        warnings.simplefilter("ignore")
        c = sh(P["c"]); o = sh(P["o"])
        # open = first non-nan open, close = last non-nan close
        idx_f = np.argmax(~np.isnan(o), axis=1); idx_l = k - 1 - np.argmax(~np.isnan(c[:, ::-1]), axis=1)
        out["o"] = np.take_along_axis(o, idx_f[:, None], 1)[:, 0]
        out["c"] = np.take_along_axis(c, idx_l[:, None], 1)[:, 0]
        out["h"] = np.nanmax(sh(P["h"]), 1); out["l"] = np.nanmin(sh(P["l"]), 1)
        cnt = (~np.isnan(c)).sum(1)
        out["qv"] = np.where(cnt > 0, np.nansum(sh(P["qv"]), 1), np.nan).astype(np.float32)
        out["tbq"] = np.where(cnt > 0, np.nansum(sh(P["tbq"]), 1), np.nan).astype(np.float32)
        out["fund"] = sh(P["fund"]).sum(1)
        out["nbars"] = cnt
    return out


def load(tf):
    p = OUT / f"panel_{tf}.npz"
    if not p.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        P1 = build_1h()
        np.savez(OUT / "panel_1h.npz", **P1)
        for name, k in (("4h", 4), ("1d", 24)):
            np.savez(OUT / f"panel_{name}.npz", **aggregate(P1, k))
        del P1
    z = np.load(p, allow_pickle=False)
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    for tf in ("1d", "4h"):
        P = load(tf)
        tb = P["tbq"] / P["qv"]
        print(tf, P["c"].shape, "taker share mean", np.nanmean(tb), "frac nan tbq where qv>0",
              np.mean(np.isnan(P["tbq"][P["qv"] > 0])), "zeros", np.mean(P["tbq"][P["qv"] > 0] == 0))
