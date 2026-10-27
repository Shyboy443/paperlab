"""Worker 3's frozen rule, a copy of research/worker3/round5/high52_plus/lib5.py run5 with the selected config
(book LS, exit D, chan 30, univ 20, k 5; PREREG.md + dev.json "selected"):

- score of a coin at day i = where day i-1's close sits inside its 30-day high-low range (highs/lows of days i-30..i-1,
  needs >= 27 of them): 1 = at the 30-day high, 0 = at the 30-day low;
- universe of day i: the top 20 USDT perps by quote volume over days i-30..i-1 (>= 24 of those days traded, listed
  >= 30 days); needs >= 10 coins with a score and an open;
- at day i's open: long the 5 highest scores, short the 5 lowest, each 1/10 of equity (0.5 gross per leg, 1x), every
  position re-sized every day; each target capped at 1 % of the coin's previous-day quote volume;
- costs 0.05 % fee + 0.02 % slippage per side on traded notional; funding on the held notional.

- 1x liquidation (the research engine's rule): a position whose intraday move against it reaches 99.5 % of its entry
  (in practice a short on a coin that doubles within the day) loses its whole margin and is closed. This matters: the
  research book had 9 such short liquidations in 2024-11..2026-10; without the cap the book would lose far more.
"""
from __future__ import annotations

from typing import Any

import numpy as np

DAY = 86_400_000
CHAN, UNIV, K, LEG = 30, 20, 5, 0.5
UNIV_DAYS = 30
FEE, SLIP = 0.0005, 0.0002
MAINT_MARGIN = 0.005


def score(H: np.ndarray, L: np.ndarray, C: np.ndarray, chan: int = CHAN) -> np.ndarray:
    """Position of the previous close inside the trailing `chan`-day range (row i uses days i-chan..i-1)."""
    T, N = C.shape
    mp = int(round(chan * 0.9))
    out = np.full((T, N), np.nan)
    for i in range(1, T):
        a = max(0, i - chan)
        h, l = H[a:i], L[a:i]
        n = np.isfinite(h).sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            hx, lx = np.nanmax(np.where(np.isfinite(h), h, -np.inf), 0), np.nanmin(np.where(np.isfinite(l), l, np.inf), 0)
            s = (C[i - 1] - lx) / (hx - lx)
        out[i] = np.where((n >= mp) & np.isfinite(s), s, np.nan)
    return out


def universe(C: np.ndarray, QV: np.ndarray, t: np.ndarray, onboard: np.ndarray, n: int = UNIV,
             days: int = UNIV_DAYS) -> np.ndarray:
    """research/monthly20/r2_rotation/engine_r2.py universe_mask: top-n by quote volume over the `days` days before."""
    T, N = C.shape
    qv = np.nan_to_num(QV)
    cs = np.vstack([np.zeros((1, N)), np.cumsum(qv, 0)])
    cnt = np.vstack([np.zeros((1, N)), np.cumsum(~np.isnan(C), 0)])
    mask = np.zeros((T, N), bool)
    for d in range(days, T):
        vol = cs[d] - cs[d - days]
        ok = cnt[d] - cnt[d - days] >= days * 0.8
        ok &= ~((onboard > 0) & (onboard > t[d] - days * DAY))
        v = np.where(ok, vol, -1)
        top = np.argsort(-v, kind="stable")[:n]
        top = top[v[top] > 0]
        mask[d, top] = True
    return mask


def run(O: np.ndarray, H: np.ndarray, L: np.ndarray, C: np.ndarray, QV: np.ndarray, F: np.ndarray, t: np.ndarray,
        onboard: np.ndarray, i0: int, i1: int, equity0: float = 10_000.0, last_open_only: bool = False) -> dict[str, Any]:
    """Replay days i0..i1-1. With last_open_only the last day is only rebalanced at its open (its close is not known yet).
    Returns the daily curve (equity at each day's close), the units after the last rebalance, the units before it and
    the closed positions."""
    S = score(H, L, C)
    U = universe(C, QV, t, onboard)
    cost = FEE + SLIP
    N = C.shape[1]
    eq = equity0
    units = np.zeros(N)
    last_px = np.full(N, np.nan)
    pos_pnl = np.zeros(N)
    pos_open = np.zeros(N, int)
    entry = np.zeros(N)
    n_liq = 0
    liquidated: list[tuple[int, int]] = []
    closed: list[tuple[int, int, int, int, float]] = []
    curve: list[tuple[int, float]] = []
    prev_units = units.copy()
    for i in range(i0, i1):
        o, c = O[i], C[i]
        live = units != 0
        px = np.where(np.isnan(o), last_px, o)
        if live.any():
            g = units[live] * (px[live] - last_px[live])
            eq += g.sum()
            pos_pnl[live] += g
        s = S[i]
        valid = ~np.isnan(s) & ~np.isnan(o)
        idx = np.where(U[i] & valid)[0]
        tgt = np.zeros(N)
        cur = units * np.nan_to_num(px)
        if len(idx) >= 2 * K and eq > 0:
            for sg in (1, -1):
                order = idx[np.argsort(-(sg * s)[idx], kind="stable")]
                tgt[order[:K]] = sg * LEG * eq / K
            cap = 0.01 * np.nan_to_num(QV[i - 1])
            tgt = np.clip(tgt, -cap, cap)
        traded = np.abs(tgt - cur)
        eq -= traded.sum() * cost
        pos_pnl -= traded * cost
        for j in np.where(live)[0]:
            if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                closed.append((j, int(np.sign(units[j])), pos_open[j], i, float(pos_pnl[j])))
                pos_pnl[j] = 0.0
        new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
        pos_open[new] = i
        prev_units = units.copy()
        pxs = np.where(np.isnan(px) | (px == 0), 1, px)
        units = np.where(tgt != 0, tgt / pxs, 0.0)
        last_px = np.where(tgt != 0, px, last_px)
        entry = np.where(tgt != 0, px, 0.0)
        live = units != 0
        if last_open_only and i == i1 - 1:
            break
        if live.any():
            f = np.where(live, -(units * np.nan_to_num(last_px) * F[i]), 0.0)
            eq += f.sum()
            pos_pnl += f
            hh = np.where(np.isnan(H[i]), last_px, H[i])
            ll = np.where(np.isnan(L[i]), last_px, L[i])
            with np.errstate(invalid="ignore", divide="ignore"):
                adv = np.where(units > 0, entry - ll, hh - entry) / np.where(entry > 0, entry, 1)
            liq = live & (adv >= 1.0 - MAINT_MARGIN)
            cc = np.where(np.isnan(c), last_px, c)
            g = np.where(live & ~liq, units * (cc - last_px), 0.0)
            eq += g.sum()
            pos_pnl += g
            for j in np.where(liq)[0]:
                loss = -abs(units[j]) * entry[j] - units[j] * (last_px[j] - entry[j])
                eq += loss
                pos_pnl[j] += loss
                closed.append((j, int(np.sign(units[j])), pos_open[j], i + 1, float(pos_pnl[j])))
                pos_pnl[j] = 0.0
                units[j] = 0.0
                n_liq += 1
                liquidated.append((j, i))
        last_px = np.where(np.isnan(c), last_px, c)
        curve.append((int(t[i]), eq))
    return {"curve": curve, "equity": eq, "liquidations": n_liq, "liquidated": liquidated, "units": units, "prev_units": prev_units, "closed": closed,
            "open": {int(j): (int(np.sign(units[j])), int(pos_open[j]), float(pos_pnl[j])) for j in np.where(units != 0)[0]}}
