"""Signals + two backtest engines for the taker-flow family.

fast_eval: vectorised screen (weights held fixed between rebalances, open-to-open returns, turnover costs, funding).
run_units: the real engine (units held between rebalances, fills at the bar open, 1% of previous bar volume cap,
           funding, liquidation on bar high/low at 1/lev - 0.5%, per-position attribution). Final numbers use run_units.
Signals at bar i use bars <= i-1 only (decided at the close of i-1, filled at the open of i).
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, DAY, summary  # noqa
import data_tf

BARS_PER_DAY = {"1h": 24, "4h": 6, "1d": 1}
_CACHE = {}


def panel(tf):
    if tf not in _CACHE:
        P = data_tf.load(tf)
        P["tf"] = tf
        P["bpd"] = BARS_PER_DAY[tf]
        _CACHE[tf] = P
    return _CACHE[tf]


def csum(x):
    x = np.nan_to_num(x.astype(np.float64))
    return np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, 0)])


def rolling_sum(x, L):
    """out[i] = sum of x[i-L .. i-1] (bars strictly before i), nan for i < L."""
    cs = csum(x)
    out = np.full(x.shape, np.nan)
    out[L:] = cs[L:-1] - cs[:-1 - L] if L > 0 else 0
    return out


def shifted(x, k=1):
    out = np.full(x.shape, np.nan)
    out[k:] = x[:-k]
    return out


def universe_mask(P, n, lookback_days=30):
    """Top-n by trailing 30-day quote volume known at bar i's open; coin needs >= 80% of bars and >= 30 days of history."""
    key = ("um", n)
    if key in P:
        return P[key]
    bpd = P["bpd"]; L = lookback_days * bpd
    vol = rolling_sum(P["qv"], L)
    cnt = rolling_sum((~np.isnan(P["c"])).astype(float), L)
    ok = (cnt >= 0.8 * L) & (P["first"][None, :] <= P["t"][:, None] - lookback_days * DAY)
    v = np.where(ok & ~np.isnan(vol), vol, -1)
    T, N = v.shape
    mask = np.zeros((T, N), bool)
    # recompute once a day (at the first bar of each day) and hold for the day, like a daily rebalance of the list
    for i in range(L, T):
        if i % bpd == 0 or i == L:
            top = np.argpartition(-v[i], n)[:n]
            top = top[v[i, top] > 0]
            cur = np.zeros(N, bool); cur[top] = True
        mask[i] = cur
    mask &= ~np.isnan(P["o"])
    P[key] = mask
    return mask


# ---------------------------------------------------------------- signals
def taker_share(P, L):
    key = ("share", L)
    if key not in P:
        with np.errstate(all="ignore"):
            P[key] = rolling_sum(P["tbq"], L) / rolling_sum(P["qv"], L)
    return P[key]


def ret_lb(P, L):
    key = ("ret", L)
    if key not in P:
        c = P["c"].astype(np.float64)
        cf = pd_ffill(c)
        out = np.full(c.shape, np.nan)
        with np.errstate(all="ignore"):
            out[L + 1:] = cf[L:-1] / cf[:-1 - L] - 1
        P[key] = out
    return P[key]


def pd_ffill(a):
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(a.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    return a[idx, np.arange(a.shape[1])[None, :]]


def flow_dev(P, L, base_days=30):
    """Taker-buy share over the last L bars minus the coin's own share over the last base_days (removes each coin's
    structural baseline, e.g. coins whose share always sits at 0.47)."""
    key = ("fdev", L, base_days)
    if key not in P:
        P[key] = taker_share(P, L) - taker_share(P, base_days * P["bpd"])
    return P[key]


def flow_vol(P, L, base_days=30):
    """Net taker flow (buy - sell quote) over L bars divided by the average L-bar quote volume over base_days:
    imbalance x relative volume ("CVD relative to normal activity")."""
    key = ("fvol", L, base_days)
    if key not in P:
        net = rolling_sum(2 * P["tbq"].astype(np.float64) - P["qv"], L)
        B = base_days * P["bpd"]
        avg = rolling_sum(P["qv"], B) / B * L
        with np.errstate(all="ignore"):
            P[key] = net / avg - 0  # nan where no history
    return P[key]


def xs_rank(x, mask):
    """Cross-sectional percentile rank in [0,1] among mask & finite."""
    out = np.full(x.shape, np.nan)
    for i in range(x.shape[0]):
        m = mask[i] & np.isfinite(x[i])
        n = m.sum()
        if n < 2:
            continue
        v = x[i, m]
        r = np.empty(n); r[np.argsort(v, kind="stable")] = np.arange(n)
        out[i, m] = r / (n - 1)
    return out


# ---------------------------------------------------------------- weights
def xs_weights(score, mask, k_long, k_short, rb, bpd_offset=0):
    """Long the k_long highest scores / short the k_short lowest at every rb-th bar; 50/50 gross if both legs."""
    T, N = score.shape
    W = np.full((T, N), np.nan)
    nside = (k_long > 0) + (k_short > 0)
    for i in range(bpd_offset, T, rb):
        m = mask[i] & np.isfinite(score[i])
        idx = np.where(m)[0]
        w = np.zeros(N)
        if len(idx) >= k_long + k_short and len(idx) > 0:
            order = idx[np.argsort(-score[i, idx], kind="stable")]
            if k_long:
                w[order[:k_long]] = 1.0 / nside / k_long
            if k_short:
                w[order[::-1][:k_short]] = -1.0 / nside / k_short
        W[i] = w
    return W


def ts_weights(sig, mask, n_slots, rb):
    """Per coin: sig in {-1,0,+1}; each active coin gets 1/n_slots of equity (n_slots = universe size, so gross <= 1)."""
    T, N = sig.shape
    W = np.full((T, N), np.nan)
    for i in range(0, T, rb):
        s = np.where(mask[i] & np.isfinite(sig[i]), sig[i], 0.0)
        W[i] = s / n_slots
    return W


# ---------------------------------------------------------------- fast screen
def fast_eval(P, W, start, end, cost_mult=1.0):
    """Monthly-return summary with weights held constant between rebalance bars (screen only)."""
    t = P["t"]; i0 = int(np.searchsorted(t, start)); i1 = int(np.searchsorted(t, end))
    if "_r_oo" not in P:
        o = pd_ffill(P["o"].astype(np.float64))
        r = np.zeros_like(o)
        with np.errstate(all="ignore"):
            r[:-1] = o[1:] / o[:-1] - 1
        P["_r_oo"] = np.nan_to_num(r)
    r = P["_r_oo"]
    Wf = pd_ffill_rows(W)
    Wf = np.nan_to_num(Wf)[i0:i1]
    cost = (FEE + SLIP) * cost_mult
    turn = np.abs(np.diff(np.vstack([np.zeros((1, Wf.shape[1])), Wf]), axis=0)).sum(1)
    pr = (Wf * r[i0:i1]).sum(1) - turn * cost - (Wf * np.nan_to_num(P["fund"][i0:i1])).sum(1)
    eq = np.cumprod(1 + pr) * 10_000
    curve = [(int(t[i0]), 10_000.0)] + [(int(t[i0 + k] + (t[1] - t[0]) - 1), float(e)) for k, e in enumerate(eq)]  # stamp inside the covered bar
    out = summary(curve)
    out["turnover_per_year"] = float(turn.sum() / ((i1 - i0) / P["bpd"] / 365))
    return out


def pd_ffill_rows(W):
    """Forward fill whole rows: rows that are all-nan take the last defined row."""
    defined = ~np.isnan(W).all(1)
    idx = np.where(defined, np.arange(len(W)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = W[idx]
    out[~defined & (idx == 0) & ~defined[0]] = np.nan
    return out


# ---------------------------------------------------------------- real engine
def run_units(P, W, start, end, lev=1.0, cost_mult=1.0, equity0=10_000.0, record=False, cols=None):
    """Units held between rebalances; W rows that are not all-nan are rebalance targets (weight x lev x equity).
    cols: restrict to columns that are ever traded (speed)."""
    t = P["t"]; step = int(t[1] - t[0])
    i0 = int(np.searchsorted(t, start)); i1 = int(np.searchsorted(t, end))
    if cols is None:
        cols = np.where(np.nan_to_num(np.abs(W[i0:i1])).sum(0) > 0)[0]
    O, H, L_, C = (P[k][:, cols].astype(np.float64) for k in ("o", "h", "l", "c"))
    QV = P["qv"][:, cols].astype(np.float64); F = P["fund"][:, cols].astype(np.float64)
    Wc = W[:, cols]
    N = len(cols)
    cost = (FEE + SLIP) * cost_mult
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N)
    last_px = np.full(N, np.nan)
    curve = [(int(t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N); pos_open = np.zeros(N, int); pos_side = np.zeros(N)
    positions = []
    n_liq = 0; wiped = False

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((int(cols[j]), int(pos_side[j]), int(pos_open[j]), int(i), float(pos_pnl[j])))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o = O[i]
        live = units != 0
        w = Wc[i]
        if not wiped and not np.isnan(w).all():
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                g = np.nan_to_num(g)
                eq += g.sum(); pos_pnl[live] += g
                last_px[live] = px[live]
            tgt = np.nan_to_num(w) * lev * max(eq, 0)
            tgt = np.where(np.isnan(o), 0.0, tgt)          # cannot open a coin without a price
            cap = 0.01 * np.nan_to_num(QV[i - 1])
            tgt = np.clip(tgt, -cap, cap)
            cur = units * np.nan_to_num(px)
            # keep an unchanged-sign position's units if target equal within 0? -> trade the difference
            traded = np.abs(tgt - cur)
            # coins with no price now but held: forced to keep (cannot trade) -> treat as closed at last price
            eq -= traded.sum() * cost
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    pos_pnl[j] -= traded[j] * cost; traded[j] = 0
                    close(j, i)
            pos_pnl -= traded * cost
            new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
            pos_open[new] = i; pos_side[new] = np.sign(tgt[new])
            pxs = np.where(np.isnan(px), 1.0, px)
            units = np.where(tgt != 0, tgt / pxs, 0.0)
            # entry for liquidation: new positions at px; resized ones keep a blended entry approximated by px
            entry = np.where(tgt != 0, pxs, 0.0)
            margin = np.abs(tgt) / max(lev, 1e-9)
            last_px = np.where(tgt != 0, pxs, last_px)
            live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(C[i]), last_px, C[i])
            curve.append((int(t[i] + step - 1), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * np.nan_to_num(F[i])), 0.0)
        eq += f.sum(); pos_pnl += f
        hh = np.where(np.isnan(H[i]), last_px, H[i]); ll = np.where(np.isnan(L_[i]), last_px, L_[i])
        if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9:
            thr = 1.0 / lev - MAINT_MARGIN
            ent = np.where(entry > 0, entry, 1)
            adv = np.where(units > 0, (entry - ll) / ent, (hh - entry) / ent)
            liq = live & (adv >= thr)
        else:
            liq = np.zeros(N, bool)
        cc = np.where(np.isnan(C[i]), last_px, C[i])
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        g = np.nan_to_num(g)
        eq += g.sum(); pos_pnl += g
        if liq.any():
            for j in np.where(liq)[0]:
                loss = -margin[j] - units[j] * (last_px[j] - entry[j])
                eq += loss; pos_pnl[j] += loss
                close(j, i + 1); units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(C[i]), last_px, C[i])
        if eq <= 0:
            eq = 1e-9; wiped = True; units[:] = 0
        curve.append((int(t[i] + step - 1), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["wiped"] = wiped
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out
