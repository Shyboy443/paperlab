"""Target-weight engine: agent 02 / r2_rotation accounting (engine_r2.run) generalised from k-long/k-short picks to an
arbitrary target-weight matrix W[i] (signed fraction of equity per coin, applied at bar i's open on rebalance bars).

Costs (fee+slip) on traded notional, funding every bar on held notional (longs pay), liquidation at 1/lev - 0.5% on
bar high/low from entry, position size capped at 1% of the previous bar's quote volume, per-position attribution.
Month labels: a bar's result counts on the bar it covers (curve stamped at bar end - 1 ms).
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, DAY, summary  # noqa


class Panel:
    def __init__(self, D):
        self.t = D["t"]; self.step = int(D["step"])
        self.syms = [str(s) for s in D["syms"]]
        for k in ("o", "h", "l", "c", "qv", "fund", "pc", "pm"):
            setattr(self, k, D[k])
        self.onboard = D["first"]


def universe_daily(day, n=40, lookback_days=30):
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


def run(P, W, rb, start, end, lev=1.0, cost_mult=1.0, equity0=10_000.0, record=False, phase=0):
    """W: (T,N) signed weights summing in |.| to <= 1 (gross 1x); scaled by lev. Rebalance when (i - i0 - phase) % rb == 0."""
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    N = P.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N)
    curve = [(int(P.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N)
    pos_open = np.zeros(N, int); pos_side = np.zeros(N)
    positions = []
    n_liq = 0; wiped = False
    last_px = np.full(N, np.nan)

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]
        live = units != 0
        if (i - i0 - phase) % rb == 0 and not wiped:
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            w = np.nan_to_num(W[i]) * (~np.isnan(o))
            tgt = w * lev * eq if eq > 0 else np.zeros(N)
            cap = 0.01 * np.nan_to_num(P.qv[i - 1])
            tgt = np.clip(tgt, -cap, cap)
            cur = units * np.nan_to_num(px)
            traded = np.abs(tgt - cur)
            eq -= traded.sum() * cost
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    close(j, i)
            pos_pnl -= traded * cost
            new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
            pos_open[new] = i; pos_side[new] = np.sign(tgt[new])
            units = np.where(tgt != 0, tgt / np.where(np.isnan(o), 1, o), 0.0)
            entry = np.where(tgt != 0, o, 0.0)
            margin = np.where(tgt != 0, np.abs(tgt) / max(lev, 1e-9), 0.0)
            last_px = np.where(tgt != 0, o, last_px)
            live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(P.t[i] + P.step - 1), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * P.fund[i]), 0.0)
        eq += f.sum(); pos_pnl += f
        hh = np.where(np.isnan(h), last_px, h); ll = np.where(np.isnan(l), last_px, l)
        if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9:
            thr = 1.0 / lev - MAINT_MARGIN
            ent = np.where(entry > 0, entry, 1)
            adv = np.where(units > 0, (entry - ll) / ent, (hh - entry) / ent)
            liq = live & (adv >= thr)
        else:
            liq = np.zeros(N, bool)
        cc = np.where(np.isnan(c), last_px, c)
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        eq += g.sum(); pos_pnl += g
        if liq.any():
            for j in np.where(liq)[0]:
                loss = -margin[j] - units[j] * (last_px[j] - entry[j])
                eq += loss; pos_pnl[j] += loss
                close(j, i + 1)
                units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        if eq <= 0:
            eq = 1e-9; wiped = True; units[:] = 0
        curve.append((int(P.t[i] + P.step - 1), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["wiped"] = wiped
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out
