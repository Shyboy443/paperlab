"""Portfolio engine for conditional intraday-momentum rules on 1h bars.

Rule IM(H, k, X, uni, K): on each UTC day d, for every coin in the universe known at 00:00 (U40 minus BTC/ETH for 'ALT',
BTC+ETH for 'BE'), z = log(open[H]/open[0]) / (trailing 480h hourly vol * sqrt(H)). If |z| >= k, at the open of hour H
take a position in the direction of the move (mode 'mom') or against it ('rev') and close at the open of hour X
(X may be > 24, i.e. the next day). Capital: each position gets equity/max(n_signals, K) * lev (isolated margin),
so gross exposure <= lev. Liquidation: if the adverse excursion (bar highs/lows while held) reaches 1/lev - 0.5%,
the position loses its margin (plus the exit cost). Costs: fee+slippage each side, funding of stamps inside the hold.
Notional is capped at 1% of the entry bar's quote volume (excess stays in cash).
Positions only overlap days if X > 24; the engine books each day's P&L on the signal day (calendar day it covers)."""
import numpy as np
from base import *


def signals(H_, k, X, uni, d_from, d_to, mode="mom"):
    rows = []
    for d in range(d_from, d_to):
        b = d * 24
        if b + X >= len(th):
            break
        js = np.array([BTC, ETH]) if uni == "BE" else np.array([j for j in np.where(U40[d])[0] if j not in (BTC, ETH)])
        if len(js) == 0:
            continue
        o0, oH = O[b, js], O[b + H_, js]
        v = hvol(np.array([b]))[0, js]
        with np.errstate(invalid="ignore", divide="ignore"):
            z = np.log(oH / o0) / (v * np.sqrt(H_))
        ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[b + X, js])
        for j, zz in zip(js[ok], z[ok]):
            s = np.sign(zz) * (1 if mode == "mom" else -1)
            rows.append((d, b + H_, b + X, j, s))
    return rows


def run(rows, d_from, d_to, K, lev=1.0, cost_mult=1.0, eq0=10_000.0, liq_cap=True, override=None):
    """rows: (day, ie, ix, j, side). Returns daily equity curve [(ms end of day, eq)], per-trade list."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(r)
    eq = eq0
    curve = [(int(td[d_from]), eq)]
    trades, liqs = [], 0
    cost = (FEE + SLIP) * cost_mult
    for d in range(d_from, d_to):
        rs = by_day.get(d, [])
        if rs:
            n = len(rs)
            w = 1.0 / max(n, K)
            pnl = 0.0
            for (_, ie, ix, j, s) in rs:
                notional = eq * w * lev
                if liq_cap:
                    notional = min(notional, 0.01 * QV[ie, j]) if np.isfinite(QV[ie, j]) else notional
                margin = notional / lev
                px = O[ix, j]
                if not np.isfinite(px):  # coin stopped trading inside the hold: exit at its last available close
                    seg = C[ie:ix, j]; fin = seg[np.isfinite(seg)]
                    px = fin[-1] if len(fin) else O[ie, j]
                g = s * (px / O[ie, j] - 1)
                f = s * (FCUM[ix, j] - FCUM[ie, j])
                if lev > 1:
                    a = 1 - np.nanmin(L[ie:ix, j]) / O[ie, j] if s > 0 else np.nanmax(Hh[ie:ix, j]) / O[ie, j] - 1
                    if a >= 1.0 / lev - MAINT:
                        liqs += 1
                        p = -margin - notional * cost
                        pnl += p; trades.append((d, j, s, p, p / eq)); continue
                p = notional * (g - f - 2 * cost)
                pnl += p
                trades.append((d, j, s, p, p / eq))
            eq = max(eq + pnl, 0.0)
        curve.append((int(td[d] + DAY - 1), eq))  # booked inside day d (calendar day the trade covers)
        if eq <= 0:
            break
    return curve, trades, liqs


from common import MAINT_MARGIN as MAINT
