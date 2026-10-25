"""Round-4 factor engine: r2_rotation/engine_r2.run (agent 02's weekly long/short rotation engine) with ONE addition:
an optional beta-neutral side scaling.

At a rebalance bar i the long and short legs are picked exactly as in engine_r2 (top k / bottom k of the score among the
eligible universe). Dollar-neutral (beta=None): each leg gets 0.5 x lev x equity, equal weight inside the leg.
Beta-neutral: bL = mean beta of the long leg, bS = mean beta of the short leg (betas known at the open of i, clipped
to >= 0.1); long leg gross = lev x eq x bS/(bL+bS), short leg gross = lev x eq x bL/(bL+bS). Gross exposure stays
lev x equity and the book's beta to BTC is 0 (bL*gL = bS*gS). Everything else (costs, funding, 1% volume cap,
liquidation rule, per-position attribution) is unchanged from engine_r2.
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
from common import FEE, SLIP, MAINT_MARGIN, summary  # noqa
from engine_r2 import Panel, universe_mask  # noqa  (re-exported)


def run(P, umask_bar, score, params, start, end, cost_mult=1.0, equity0=10_000.0, record=False, beta=None):
    kL, kS, rb, lev = params["k_long"], params["k_short"], params["rebal"], params["lev"]
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    N = P.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N)
    curve = [(int(P.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N)
    pos_open = np.zeros(N, int); pos_eq0 = np.zeros(N); pos_side = np.zeros(N)
    positions = []
    n_liq = 0; wiped = False
    last_px = np.full(N, np.nan)

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j], pos_eq0[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]
        live = units != 0
        if (i - i0) % rb == 0 and not wiped:
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            s = score[i]
            elig = umask_bar[i] & ~np.isnan(s) & ~np.isnan(o)
            if beta is not None:
                elig &= ~np.isnan(beta[i])
            idx = np.where(elig)[0]
            tgt = np.zeros(N)
            if len(idx) >= (kL + kS) and eq > 0:
                order = idx[np.argsort(-s[idx], kind="stable")]
                longs = order[:kL] if kL else np.array([], int)
                shorts = order[::-1][:kS] if kS else np.array([], int)
                nside = (kL > 0) + (kS > 0)
                gL = gS = 1.0 / nside
                if beta is not None and kL and kS:
                    bL = max(float(np.mean(beta[i][longs])), 0.1); bS = max(float(np.mean(beta[i][shorts])), 0.1)
                    gL, gS = bS / (bL + bS), bL / (bL + bS)
                if kL:
                    tgt[longs] = lev * eq * gL / kL
                if kS:
                    tgt[shorts] = -lev * eq * gS / kS
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
            pos_open[new] = i; pos_eq0[new] = eq; pos_side[new] = np.sign(tgt[new])
            units = np.where(tgt != 0, tgt / np.where(np.isnan(o), 1, o), 0.0)
            entry = np.where(tgt != 0, o, 0.0)
            gross = np.abs(tgt)
            margin = np.where(gross > 0, gross / max(lev, 1e-9), 0.0)
            last_px = np.where(tgt != 0, o, last_px)
            live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(P.t[i] + P.step), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * P.fund[i]), 0.0)
        eq += f.sum(); pos_pnl += f
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
                pnl_from_entry_so_far = units[j] * (last_px[j] - entry[j])
                loss = -margin[j] - pnl_from_entry_so_far
                eq += loss; pos_pnl[j] += loss
                close(j, i + 1)
                units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        if eq <= 0:
            eq = 1e-9; wiped = True; units[:] = 0
        curve.append((int(P.t[i] + P.step), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["wiped"] = wiped
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out
