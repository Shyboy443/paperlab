"""4h panel resampled from agent 04's read-only 1h cache (data.binance.vision CSVs, cols: open_ms o h l c quote_vol taker_buy_quote trades).
Falls back to common.klines(sym, '4h') for symbols missing there (none expected)."""
import numpy as np
from engine import *

SRC = PROJECT / "data" / "monthly20_04_crash_reversal" / "1h"


def panel_4h(symbols):
    P = Panel.__new__(Panel)
    step = 4 * HOUR
    P.interval, P.step = "4h", step
    P.t = np.arange(DATA_FROM, TEST_END, step, dtype=np.int64)
    T, N = len(P.t), len(symbols)
    P.syms = list(symbols)
    P.o, P.h, P.l, P.c, P.qv = (np.full((T, N), np.nan) for _ in range(5))
    onboard = {s["symbol"]: s["onboard"] for s in perp_symbols()}
    P.onboard = np.array([onboard.get(s, 0) for s in symbols], dtype=np.int64)
    missing = []
    for j, s in enumerate(symbols):
        f = SRC / f"{s}.npz"
        if not f.exists():
            missing.append(s); continue
        a = np.load(f)["a"]
        if len(a) == 0:
            missing.append(s); continue
        a = a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < TEST_END)]
        idx = ((a[:, 0].astype(np.int64) - DATA_FROM) // step).astype(int)
        # aggregate 1h -> 4h (first open, max high, min low, last close, sum qv); only full 4-bar buckets kept
        cnt = np.bincount(idx, minlength=T)
        order = np.argsort(a[:, 0], kind="stable"); a, idx = a[order], idx[order]
        first = np.r_[True, idx[1:] != idx[:-1]]; last = np.r_[idx[1:] != idx[:-1], True]
        hi = np.full(T, -np.inf); lo = np.full(T, np.inf); qv = np.zeros(T)
        np.maximum.at(hi, idx, a[:, 2]); np.minimum.at(lo, idx, a[:, 3]); np.add.at(qv, idx, a[:, 5])
        full = cnt == 4
        o = np.full(T, np.nan); c = np.full(T, np.nan)
        o[idx[first]] = a[first, 1]; c[idx[last]] = a[last, 4]
        P.o[full, j], P.c[full, j], P.h[full, j], P.l[full, j], P.qv[full, j] = o[full], c[full], hi[full], lo[full], qv[full]
    P.fund = np.zeros((T, N)); P.has_fund = np.zeros(N, bool)
    P.missing = missing
    return P
