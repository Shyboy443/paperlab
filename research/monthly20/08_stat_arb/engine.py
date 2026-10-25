"""Agent 08 - stat-arb engine: data alignment, signals and a leveraged, cost-aware portfolio simulator.

Conventions (no look-ahead):
  bar t covers [open_t, open_{t+1}). Signals use closes up to and including bar t.
  Target weights decided at close of bar t are filled at open of bar t+1 and held over bar t+1
  (return open_{t+1} -> open_{t+2}).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa

# Universe fixed BEFORE the TEST run (PREREG.md): the 13 established perps whose 1h bars were in the shared cache
# when DEV work ended. The wider 26-coin list in fetch.py was not fully downloaded (shared-IP rate limit).
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT", "BNBUSDT",
         "XRPUSDT", "LTCUSDT", "LINKUSDT", "DOGEUSDT"]

SECTORS = {
    "L1": ["SOLUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT", "ALGOUSDT"],
    "OLD_PAY": ["LTCUSDT", "BCHUSDT", "ETCUSDT", "XRPUSDT", "XLMUSDT", "TRXUSDT"],
    "DEFI": ["UNIUSDT", "AAVEUSDT", "CRVUSDT", "SUSHIUSDT", "LINKUSDT"],
    "MEME": ["DOGEUSDT", "1000SHIBUSDT"],
    "L2": ["ARBUSDT", "OPUSDT"],
    "MAJORS": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
}


def load():
    idx = pd.RangeIndex(DATA_FROM, TEST_END, HOUR)
    cols = {k: {} for k in ("o", "h", "l", "c", "qv", "fr")}
    for s in COINS:
        b = klines(s, "1h")
        if not b:
            continue
        a = np.array(b)
        t = a[:, 0].astype(np.int64)
        for k, j in (("o", 1), ("h", 2), ("l", 3), ("c", 4), ("qv", 6)):
            cols[k][s] = pd.Series(a[:, j], index=t)
        f = funding(s)
        # a stamp at time T is paid by the position held over the bar that ends at T (bar open = T - 1h)
        fs = pd.Series(0.0, index=idx)
        for ts, r in f:
            bo = int(round(int(ts) / HOUR)) * HOUR - HOUR
            if bo in fs.index:
                fs.loc[bo] += r
        cols["fr"][s] = fs
    D = {k: pd.DataFrame(v).reindex(idx) for k, v in cols.items()}
    D["fr"] = D["fr"].fillna(0.0)
    return D


def sim(D, W, start, end, lev=1.0, cost_mult=1.0, rebal_every=1, init=10_000.0, vol_cap=0.01):
    """W: DataFrame of target weights (fraction of equity, gross ~1) decided at close of each bar.
    Returns (curve [(ms, equity)], per-trade-ish pnl list (per rebalance-period pnl), info)."""
    O, H, L, Q, F = (D[k].values for k in ("o", "h", "l", "qv", "fr"))
    idx = D["o"].index.values
    Wv = np.nan_to_num(W.reindex(columns=D["o"].columns).values)
    c = (FEE + SLIP) * cost_mult
    t0 = int(np.searchsorted(idx, start))
    t1 = int(np.searchsorted(idx, end))
    n = O.shape[1]
    p = np.zeros(n)
    E = init
    curve = [(int(idx[t0]), E)]
    period_pnl, cur_pnl = [], 0.0
    liq_events, wiped, capped = 0, False, 0
    mm = MAINT_MARGIN
    for t in range(t0, t1 - 1):
        o, o2 = O[t], O[t + 1]
        valid = np.isfinite(o) & np.isfinite(o2) & np.isfinite(H[t]) & np.isfinite(L[t])
        if (t - t0) % rebal_every == 0:
            tgt = E * lev * Wv[t - 1]
            tgt[~valid] = 0.0
            capv = vol_cap * np.nan_to_num(Q[t - 1])
            over = np.abs(tgt) > capv
            if over.any():
                capped += int(over.sum())
                tgt = np.sign(tgt) * np.minimum(np.abs(tgt), capv)
            if cur_pnl != 0.0:
                period_pnl.append(cur_pnl)
                cur_pnl = 0.0
            trade = tgt - p
            cost = np.abs(trade).sum() * c
            E -= cost
            cur_pnl -= cost
            p = tgt
        else:
            p[~valid] = 0.0
        # isolated-margin liquidation per leg, checked on the bar's high/low
        if lev > 1.0 and np.any(p != 0):
            adverse = np.where(p > 0, 1 - L[t] / o, H[t] / o - 1)
            adverse = np.nan_to_num(adverse)
            hit = (p != 0) & (adverse >= 1.0 / lev - mm)
            if hit.any():
                liq_events += int(hit.sum())
                loss = (np.abs(p[hit]) / lev).sum()  # the leg's margin is lost
                E -= loss
                cur_pnl -= loss
                p[hit] = 0.0
        r = np.where(valid, o2 / o - 1, 0.0)
        pnl = float((p * r).sum() - (p * F[t]).sum())
        E += pnl
        cur_pnl += pnl
        p = p * (1 + r)
        if E <= 0:
            wiped = True
            E = 0.0
            curve.append((int(idx[t + 1]), 1e-9))
            break
        curve.append((int(idx[t + 1]), E))
    if cur_pnl:
        period_pnl.append(cur_pnl)
    return curve, period_pnl, {"liq_legs": liq_events, "wiped": wiped, "vol_capped_legs": capped}


# ---------------------------------------------------------------- signals
def logc(D):
    return np.log(D["c"])


def rolling_beta(r, y_cols, f_cols, win=720, refit=24):
    """OLS betas of each y on factors f (with intercept) over the past `win` hours, refit every `refit` hours,
    using data up to bar t only. Returns dict col -> DataFrame of betas (index = bars, columns = factors)."""
    T = len(r)
    Rv = r.values
    ci = {c: i for i, c in enumerate(r.columns)}
    fi = [ci[f] for f in f_cols]
    out = {y: np.full((T, len(f_cols)), np.nan) for y in y_cols}
    last = {y: None for y in y_cols}
    for t in range(win, T):
        if t % refit == 0:
            Xf = Rv[t - win + 1:t + 1][:, fi]
            for y in y_cols:
                yy = Rv[t - win + 1:t + 1, ci[y]]
                m = np.isfinite(yy) & np.isfinite(Xf).all(1)
                if m.sum() < win * 0.8:
                    last[y] = None
                    continue
                X = np.column_stack([np.ones(m.sum()), Xf[m]])
                b = np.linalg.lstsq(X, yy[m], rcond=None)[0]
                last[y] = b[1:]
        for y in y_cols:
            if last[y] is not None:
                out[y][t] = last[y]
    return {y: pd.DataFrame(v, index=r.index, columns=f_cols) for y, v in out.items()}
