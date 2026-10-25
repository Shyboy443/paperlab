"""Vectorised hourly long/short engine for the r4 short-term reversal study.

Book: at rebalance hours i0, i0+R, i0+2R, ... (i0 = 00:00 UTC of the window start) rank the eligible coins by a score known
at the open of hour i (built from closes up to hour i-1), long the k with the LOWEST score, short the k with the HIGHEST
(reversal), equal weight, lev/2 of equity per side (gross = lev x equity). Fill at the hour's open (if a held coin has no
bar, its last close). Units are constant until the next rebalance (weights drift), exactly like r2's engine.

Costs: (FEE+SLIP) x cost_mult on every unit of notional traded at a rebalance (drifted weights -> new targets).
Funding: each held position pays/receives rate x current notional at every funding stamp (archive data; +0.01%/8h when a
coin has no funding history). Liquidation (lev>1 only, as in PROTOCOL.md): a position whose adverse move from entry,
checked on hourly highs/lows, reaches 1/lev - 0.5% loses its whole margin (|w|/lev of equity at the rebalance) and is
gone until the next rebalance. Delisted coins: price frozen at the last close; they are not eligible once they have no
bar, and the position is closed at that last price at the next rebalance (costs paid).
Eligible at hour i: in the top-n daily volume universe (known at that day's 00:00 open), a bar at hour i, finite score.
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, HOUR, summary  # noqa: E402


class HPanel:
    def __init__(self, X, cols=None):
        cols = np.arange(len(X["syms"])) if cols is None else np.asarray(cols)
        self.t = X["t"]
        self.syms = [str(s) for s in X["syms"][cols]]
        self.o = X["o"][:, cols]; h = X["h"][:, cols]; l = X["l"][:, cols]
        c = X["c"][:, cols]; self.qv = X["qv"][:, cols]; self.fund = X["fund"][:, cols]
        self.um = {n: X[f"um{n}"][:, cols] for n in (20, 30, 40, 50, 60)}
        cf = c.copy()
        for i in range(1, len(cf)):  # forward fill closes (delisted / missing hours keep the last price)
            m = np.isnan(cf[i]); cf[i, m] = cf[i - 1, m]
        self.cf = cf
        self.hf = np.where(np.isnan(h), cf, h)
        self.lf = np.where(np.isnan(l), cf, l)
        del h, l, c
        self.has_bar = ~np.isnan(self.o)
        self.btc = self.syms.index("BTCUSDT") if "BTCUSDT" in self.syms else None


def run(P, score, k, R, univ, start, end, lev=1.0, cost_mult=1.0, equity0=10_000.0, record=False, umask=None, band=1.0):
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    nper = (i1 - i0 + R - 1) // R
    T = nper * R
    N = P.cf.shape[1]
    um = P.um[univ] if umask is None else umask
    # --- targets at each rebalance ------------------------------------------------------------------------------------
    reb = i0 + np.arange(nper) * R
    S = score[reb]
    elig = um[reb] & P.has_bar[reb] & np.isfinite(S)
    W = np.zeros((nper, N))
    big = np.where(elig, S, np.inf)
    small = np.where(elig, S, -np.inf)
    nel = elig.sum(1)
    ok = nel >= 2 * k
    lo = np.argsort(big, axis=1, kind="stable")[:, :k]      # lowest scores -> long
    hi = np.argsort(-small, axis=1, kind="stable")[:, :k]   # highest scores -> short
    rows = np.arange(nper)[:, None]
    W[rows, lo] = lev / 2 / k
    W[rows, hi] = -lev / 2 / k
    W[~ok] = 0.0
    if band > 1.0:  # hysteresis: a held coin stays while its rank is within band x k on its side
        kb = int(round(band * k))
        W[:] = 0.0
        cl, cs_ = set(), set()
        for p in range(nper):
            if not ok[p]:
                cl, cs_ = set(), set(); continue
            idx = np.where(elig[p])[0]
            order = idx[np.argsort(S[p, idx], kind="stable")]
            lowset, highset = set(order[:kb].tolist()), set(order[::-1][:kb].tolist())
            cl = {j for j in cl if j in lowset}; cs_ = {j for j in cs_ if j in highset}
            for j in order:
                if len(cl) >= k: break
                if j not in cl and j not in cs_: cl.add(int(j))
            for j in order[::-1]:
                if len(cs_) >= k: break
                if j not in cl and j not in cs_: cs_.add(int(j))
            W[p, list(cl)] = lev / 2 / k; W[p, list(cs_)] = -lev / 2 / k
    J = np.where((W != 0).any(0))[0]          # work only on coins that are ever held
    if not len(J):
        J = np.array([0])
    W = W[:, J].astype(np.float32); N = len(J)
    # --- paths inside each period (pad the last period with frozen prices) ---------------------------------------------
    idx = np.minimum(np.arange(i0, i0 + T), len(P.t) - 1)
    pad = np.arange(i0, i0 + T) >= i1
    cf = P.cf[np.ix_(idx, J)]; hf = P.hf[np.ix_(idx, J)]; lf = P.lf[np.ix_(idx, J)]
    fu = np.where(pad[:, None], np.float32(0), P.fund[np.ix_(idx, J)])
    if pad.any():
        last = cf[~pad][-1]
        cf[pad] = last; hf[pad] = last; lf[pad] = last
    entry = np.where(P.has_bar[reb][:, J], P.o[reb][:, J], P.cf[reb - 1][:, J]).astype(np.float32)
    entry = np.where(np.isfinite(entry) & (entry > 0), entry, np.nan)
    cf3 = cf.reshape(nper, R, N); hf3 = hf.reshape(nper, R, N); lf3 = lf.reshape(nper, R, N); fu3 = fu.reshape(nper, R, N)
    held = W != 0
    e3 = np.where(held, entry, 1.0)[:, None, :]
    rel = np.where(held[:, None, :], cf3 / e3 - 1, 0.0)     # price change from entry at each hour's close
    rel = np.nan_to_num(rel)
    w3 = W[:, None, :]
    if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9:
        thr = 1.0 / lev - MAINT_MARGIN
        adv = np.where(w3 > 0, (e3 - lf3) / e3, (hf3 - e3) / e3)
        adv = np.where(held[:, None, :], np.nan_to_num(adv), 0.0)
        liq_t = np.maximum.accumulate(adv >= thr, axis=1)
    else:
        liq_t = np.zeros_like(rel, dtype=bool)
    # position P&L as a fraction of equity at the rebalance: price P&L, then funding (on current notional)
    fund_cost = np.cumsum(np.where(liq_t, 0.0, w3 * (1 + rel) * fu3), axis=1)
    pos_path = np.where(liq_t, -np.abs(w3) / max(lev, 1e-9), w3 * rel) - fund_cost
    # for liquidated positions the funding paid before liquidation is still lost
    if liq_t.any():
        first = liq_t & ~np.concatenate([np.zeros((nper, 1, N), bool), liq_t[:, :-1]], axis=1)
        paid_before = np.cumsum(np.where(liq_t, 0.0, w3 * (1 + rel) * fu3), axis=1)
        frozen = np.maximum.accumulate(np.where(first, paid_before, 0.0), axis=1)
        pos_path = np.where(liq_t, -np.abs(w3) / max(lev, 1e-9) - frozen, pos_path)
    # --- turnover costs ------------------------------------------------------------------------------------------------
    end_val = pos_path[:, -1, :]                             # P&L fraction per coin at period end
    alive = ~liq_t[:, -1, :]
    port_end = end_val.sum(1)
    drift = np.where(alive, W * (1 + rel[:, -1, :]), 0.0)   # notional / equity_start at period end
    prev = np.vstack([np.zeros((1, N)), drift[:-1]])
    prev_eq = np.r_[1.0, 1 + port_end[:-1]]
    # drifted weight as a fraction of current equity (1 + period P&L; ignoring the ~0.1% cost term, a 2nd-order effect)
    trade = np.abs(W - prev / prev_eq[:, None])
    tcost = trade.sum(1) * cost
    port_path = pos_path.sum(2)                              # nper x R
    gstart = 1 - tcost                                       # equity after costs / equity before
    path = gstart[:, None] + port_path                       # equity at each hour / equity before rebalance costs
    path = np.maximum(path, 0.0)
    g = path[:, -1]
    eq_start = equity0 * np.r_[1.0, np.cumprod(g)[:-1]]
    wiped = False
    if (g <= 0).any():
        kz = int(np.argmax(g <= 0)); wiped = True
        eq_start[kz + 1:] = 0.0
    eq_h = (eq_start[:, None] * path).reshape(-1)[: i1 - i0]
    # each point is labelled with the OPEN time of the hour it closes, so an hour counts in the month it covers
    curve = [(int(P.t[i0]), equity0)] + list(zip(P.t[i0:i1].tolist(), np.maximum(eq_h, 1e-9).tolist()))
    # per-coin per-period P&L in money (cost of entering this period's position charged to it)
    pnl = eq_start[:, None] * (end_val - trade * cost)
    # positions = contiguous periods holding the same coin on the same side
    side = np.sign(W)
    trade_pnl, positions = [], []
    for j in np.where(held.any(0))[0]:
        s = side[:, j]
        chg = np.r_[True, s[1:] != s[:-1]]
        starts = np.where(chg & (s != 0))[0]
        if not len(starts):
            continue
        seg = np.cumsum(chg)
        sums = np.bincount(seg, weights=pnl[:, j])
        for a in starts:
            v = float(sums[seg[a]])
            trade_pnl.append(v)
            if record:
                b = a
                while b + 1 < nper and s[b + 1] == s[a]:
                    b += 1
                positions.append((int(J[j]), int(s[a]), int(reb[a]), int(reb[b] + R), v, float(eq_start[a])))
    out = summary(curve, trade_pnl)
    out["liquidations"] = int((liq_t[:, -1, :] & held).sum())
    out["wiped"] = wiped
    out["turnover_per_day"] = round(float(trade.sum(1).mean() * 24 / R), 3)
    out["cost_drag_month_pct"] = round(float(tcost.mean() * 24 / R * 30.4 * 100), 3)
    # volume-cap check: max notional / (1% of the previous hour's quote volume)
    qv = np.nan_to_num(P.qv[reb - 1][:, J].astype(np.float64))
    need = np.abs(W) * eq_start[:, None]
    ratio = np.where(held & (qv > 0), need / (0.01 * np.where(qv > 0, qv, 1)), 0.0)
    out["max_cap_ratio"] = round(float(ratio.max()), 3)
    out["cap_breaches_pct"] = round(float((ratio > 1).sum() / max(held.sum(), 1) * 100), 3)
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out
