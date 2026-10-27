"""On-chain fundamental factor scores (DefiLlama TVL / fees / revenue) on the survivorship-free daily panel,
traded with the unchanged agent-02 rotation engine (engine_r2.run: next-open fills, 0.05%+0.02% per side, funding,
1% volume cap). Every score at bar i uses closes up to c[i-1] and DefiLlama points stamped <= t[i] - 1 day.

Scores (L = lookback in days):
  tvlg  : log TVL growth over L
  vchg  : value change = log TVL growth - log price change over L  (= change in log TVL/market-cap if supply is flat)
  feeg  : log growth of trailing-30d fees over L       revg : same for revenue
  fychg : fee-yield change = feeg - log price change   (= change in log fees/market-cap, i.e. minus the P/F change)
Eligibility: in the point-in-time top-`univ` perps by 30d quote volume AND TVL >= min_tvl (fees > 0 for fee scores).
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END  # noqa

D = build()
P = E.Panel(D)
G = np.load(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_tvl_value/fund_grid.npz")
TVL, FEE30, REV30 = G["TVL"], G["FEE30"], G["REV30"]
_UM, _SC = {}, {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


def lagged(x, L):
    out = np.full_like(x, np.nan)
    out[L:] = x[:-L]
    return out


def score(kind, L, min_tvl=20e6, cols=None):
    tv, fe, rv, c = TVL, FEE30, REV30, P.c
    if cols is not None:
        tv, fe, rv, c = tv[:, cols], fe[:, cols], rv[:, cols], c[:, cols]
    with np.errstate(invalid="ignore", divide="ignore"):
        cprev = lagged(c, 1)                       # c[i-1] at row i
        pch = np.log(cprev / lagged(cprev, L))
        tg = np.log(tv / lagged(tv, L))
        fg = np.log(fe / lagged(fe, L))
        rg = np.log(rv / lagged(rv, L))
    s = dict(tvlg=tg, vchg=tg - pch, feeg=fg, revg=rg, fychg=fg - pch)[kind]
    ok = (tv >= min_tvl)
    if kind in ("feeg", "fychg"):
        ok &= (fe > 1e4) & (lagged(fe, L) > 1e4)
    if kind == "revg":
        ok &= (rv > 1e3) & (lagged(rv, L) > 1e3)
    s = np.where(ok & np.isfinite(s), s, np.nan)
    return s


def btc_gate(Pn, sma=100):
    """True at row i if BTC close c[i-1] > SMA(sma) of closes up to c[i-1]."""
    b = Pn.syms.index("BTCUSDT"); cb = Pn.c[:, b]
    cs = np.r_[0, np.nancumsum(cb)]
    out = np.zeros(len(cb), bool)
    for i in range(sma + 1, len(cb)):
        m = (cs[i] - cs[i - sma]) / sma
        out[i] = cb[i - 1] > m
    return out


def run_cfg(kind, L, k, legs, rebal, univ=100, min_tvl=20e6, gate=False, start=DEV_START, end=TEST_END, cost_mult=1.0,
            record=False, Pn=None, cols=None, score_override=None):
    Pn = Pn or P
    key = (kind, L, min_tvl, None if cols is None else tuple(cols))
    if score_override is not None:
        sc = score_override
    else:
        if key not in _SC:
            _SC[key] = score(kind, L, min_tvl, cols)
        sc = _SC[key]
    U = um(univ, Pn)
    if gate:
        g = btc_gate(Pn)
        U = U & g[:, None]
    kS = k if legs == "LS" else 0
    prm = dict(k_long=k, k_short=kS, rebal=rebal, lev=1.0, mode="mom")
    return E.run(Pn, U, sc, prm, start, end, cost_mult=cost_mult, record=record)


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")


DAYMS = 86_400_000


def run_tranched(kind, L, k, legs, rebal=28, ntr=4, univ=100, min_tvl=20e6, start=DEV_START, end=TEST_END, cost_mult=1.0,
                 record=False, Pn=None, cols=None, score_override=None, gate=False):
    """ntr staggered sub-books, each 1/ntr of capital, each rebalanced every `rebal` days; sub-book m starts its
    rebalance clock m*rebal/ntr days BEFORE `start` and its equity is normalised at `start` (no look-ahead: the
    pre-start part only sets the phase). Combined equity = mean of the normalised sub-book curves (buy-and-hold of the
    sub-books, no cross-rebalancing). Equivalent to rebalancing 1/ntr of the book every rebal/ntr days."""
    step = rebal // ntr
    curves, positions, trades, liq = [], [], 0, 0
    for m in range(ntr):
        s0 = start - m * step * DAYMS
        r = run_cfg(kind, L, k, legs, rebal, univ=univ, min_tvl=min_tvl, start=s0, end=end, cost_mult=cost_mult,
                    record=True, Pn=Pn, cols=cols, score_override=score_override, gate=gate)
        cv = [(t, e) for t, e in r["_curve"] if t >= start]
        e0 = cv[0][1]
        curves.append(np.array([e / e0 for _, e in cv]))
        ts = [t for t, _ in cv]
        # positions closed after start, P&L scaled to the 1/ntr share and normalised by the sub-book equity at start
        for (j, side, oi, ci, pnl, eq0) in r["_positions"]:
            if Pn is None or True:
                pass
            if (Pn or P).t[min(ci, len((Pn or P).t) - 1)] >= start or ci >= len((Pn or P).t):
                positions.append((j, side, oi, ci, pnl / e0 / ntr, m))
                trades += 1
    n = min(len(c) for c in curves)
    eq = np.mean([c[:n] for c in curves], axis=0) * 10_000.0
    curve = list(zip(ts[:n], eq))
    out = E.summary(curve, [p[4] for p in positions])
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out
