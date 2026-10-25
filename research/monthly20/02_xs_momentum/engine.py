"""Cross-sectional rotation engine on Binance USDT perps (agent 02).

Panels are day x coin (or 4h x coin) numpy arrays. Signals use closed bars only and fill at the
next bar's open. Point-in-time universe = same rule as common.liquid_universe (top-n by 30-day
quote volume among perps listed >= 30 days before, >= 80% of bars present), recomputed at every
rebalance from data known at that time.
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa

CACHE_DIR = CACHE / "perp"
MY_DATA = PROJECT / "data" / "monthly20_02_xs_momentum"


def all_symbols():
    return [s for s in perp_symbols() if s["onboard"] < TEST_END - 30 * DAY]


class Panel:
    def __init__(self, interval, symbols, cached_only=True):
        step = INTERVAL_MS[interval]
        self.interval, self.step = interval, step
        self.t = np.arange(DATA_FROM, TEST_END, step, dtype=np.int64)
        T, N = len(self.t), len(symbols)
        self.syms = list(symbols)
        self.o, self.h, self.l, self.c, self.qv = (np.full((T, N), np.nan) for _ in range(5))
        onboard = {s["symbol"]: s["onboard"] for s in perp_symbols()}
        self.onboard = np.array([onboard.get(s, 0) for s in symbols], dtype=np.int64)
        for j, s in enumerate(symbols):
            p = CACHE_DIR / interval / f"{s}.json"
            if cached_only and not p.exists():
                continue
            rows = klines(s, interval)
            if not rows:
                continue
            a = np.array([r[:7] for r in rows], dtype=float)
            idx = ((a[:, 0].astype(np.int64) - DATA_FROM) // step).astype(int)
            ok = (idx >= 0) & (idx < T)
            idx, a = idx[ok], a[ok]
            self.o[idx, j], self.h[idx, j], self.l[idx, j], self.c[idx, j], self.qv[idx, j] = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 6]
        self.fund = np.zeros((T, N))       # sum of funding rates stamped inside each bar
        self.has_fund = np.zeros(N, bool)

    def load_funding(self):
        for j, s in enumerate(self.syms):
            p = CACHE / "funding" / f"{s}.json"
            if not p.exists():
                continue
            rows = funding(s)
            if not rows:
                continue
            a = np.array(rows, dtype=float)
            idx = ((a[:, 0].astype(np.int64) - DATA_FROM) // self.step).astype(int)
            ok = (idx >= 0) & (idx < len(self.t))
            np.add.at(self.fund[:, j], idx[ok], a[ok, 1])
            self.has_fund[j] = True
        # coins without funding data: assume the default +0.01% per 8h (longs pay)
        per_bar = 0.0001 * self.step / (8 * HOUR)
        self.fund[:, ~self.has_fund] = per_bar


def universe_mask(day: Panel, n, lookback_days=30):
    """mask[d, j] = coin j is in the top-n liquid universe at the START of day d (data up to d-1)."""
    T, N = day.c.shape
    qv = np.nan_to_num(day.qv)
    cs = np.vstack([np.zeros((1, N)), np.cumsum(qv, 0)])
    cnt = np.vstack([np.zeros((1, N)), np.cumsum(~np.isnan(day.c), 0)])
    mask = np.zeros((T, N), bool)
    for d in range(lookback_days, T):
        vol = cs[d] - cs[d - lookback_days]
        ok = (cnt[d] - cnt[d - lookback_days] >= lookback_days * 0.8)
        ok &= ~((day.onboard > 0) & (day.onboard > day.t[d] - lookback_days * DAY))
        v = np.where(ok, vol, -1)
        top = np.argsort(-v)[:n]
        top = top[v[top] > 0]
        mask[d, top] = True
    return mask


def run(P: Panel, umask_bar, score, params, start, end, cost_mult=1.0, equity0=10_000.0, record=False):
    """Generic rotation backtest.

    P: bar panel. umask_bar[i, j]: coin eligible when deciding at bar i open (data up to i-1).
    score[i, j]: signal known at the open of bar i (built from bars <= i-1). NaN = not rankable.
    params: k_long, k_short, rebal (bars), lev (gross leverage on equity), mode ('mom'|'rev').
    At a rebalance bar i we fill at open[i]; P&L marked bar by bar; isolated-margin liquidation of
    each position when its adverse move from entry reaches 1/lev_pos - MAINT_MARGIN (on highs/lows).
    """
    kL, kS, rb, lev = params["k_long"], params["k_short"], params["rebal"], params["lev"]
    sign = 1.0 if params.get("mode", "mom") == "mom" else -1.0
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    N = P.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N)
    curve = [(int(P.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N)  # per-position P&L since opened (a "trade")
    n_liq = 0; wiped = False
    fund_mult = cost_mult  # funding is not a cost estimate; keep it 1x... (see below)
    fund_mult = 1.0
    last_px = np.full(N, np.nan)
    for i in range(i0, i1):
        o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]
        live = units != 0
        if (i - i0) % rb == 0 and not wiped:
            # --- rebalance at open[i] ---
            # mark open positions to the open (gap from last close to this open)
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            s = score[i] * sign
            elig = umask_bar[i] & ~np.isnan(s) & ~np.isnan(o)
            idx = np.where(elig)[0]
            tgt = np.zeros(N)
            if len(idx) >= (kL + kS) and eq > 0:
                order = idx[np.argsort(-s[idx])]
                longs = order[:kL] if kL else np.array([], int)
                shorts = order[::-1][:kS] if kS else np.array([], int)
                nside = (kL > 0) + (kS > 0)
                if kL:
                    tgt[longs] = lev * eq / nside / kL
                if kS:
                    tgt[shorts] = -lev * eq / nside / kS
                # liquidity cap: <= 1% of the previous bar's quote volume
                cap = 0.01 * np.nan_to_num(P.qv[i - 1])
                tgt = np.clip(tgt, -cap, cap)
            cur = units * np.nan_to_num(px)
            traded = np.abs(tgt - cur)
            eq -= traded.sum() * cost
            # positions whose side flips or close count as finished trades
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    trade_pnl.append(pos_pnl[j]); pos_pnl[j] = 0.0
            pos_pnl -= traded * cost
            units = np.where(tgt != 0, tgt / np.where(np.isnan(o), 1, o), 0.0)
            entry = np.where(tgt != 0, o, 0.0)
            gross = np.abs(tgt)
            margin = np.where(gross > 0, gross / max(lev, 1e-9), 0.0)
            last_px = np.where(tgt != 0, o, last_px)
            live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(P.t[i] + P.step), eq)); continue
        # funding on notional at bar start (long pays positive rate)
        notional = units * np.nan_to_num(last_px)
        f = -(notional * P.fund[i] * fund_mult)
        f = np.where(live, f, 0.0)
        eq += f.sum(); pos_pnl += f
        # liquidation check per position (isolated margin = notional/lev)
        hh = np.where(np.isnan(h), last_px, h); ll = np.where(np.isnan(l), last_px, l)
        if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9:
            thr = 1.0 / lev - MAINT_MARGIN
            adv_long = (entry - ll) / np.where(entry > 0, entry, 1)
            adv_short = (hh - entry) / np.where(entry > 0, entry, 1)
            adv = np.where(units > 0, adv_long, adv_short)
            liq = live & (adv >= thr)
        else:
            liq = np.zeros(N, bool)
        cc = np.where(np.isnan(c), last_px, c)
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        eq += g.sum(); pos_pnl += g
        if liq.any():
            for j in np.where(liq)[0]:
                # lose the whole isolated margin relative to entry: P&L from entry = -margin
                pnl_from_entry_so_far = units[j] * (last_px[j] - entry[j])
                loss = -margin[j] - pnl_from_entry_so_far
                eq += loss; pos_pnl[j] += loss
                trade_pnl.append(pos_pnl[j]); pos_pnl[j] = 0.0
                units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        if eq <= 0:
            eq = 1e-9; wiped = True; units[:] = 0
        curve.append((int(P.t[i] + P.step), eq))
    for j in np.where(units != 0)[0]:
        trade_pnl.append(pos_pnl[j])
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["wiped"] = wiped
    if record:
        out["_curve"] = curve; out["_trades"] = trade_pnl
    return out
