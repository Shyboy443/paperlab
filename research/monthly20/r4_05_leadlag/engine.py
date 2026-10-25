"""Lead-lag event backtest (USDT perps, compounding, one event at a time).

Signal at the CLOSE of bar t (all inputs closed at or before t):
  leader move  m = L-bar log return of the leader (BTC, or the equal-weight top-5-by-volume basket) ending at t
  trigger      |m| > z * sigma_1bar(30 days, leader) * sqrt(L)
  beta_i       30-day rolling OLS beta of coin i's 1-bar returns on the leader's (rows up to t)
  gap_i        beta_i * m - r_i(L)     (> 0 means coin i has not yet followed the leader by that much)
  followers    coins with point-in-time volume rank in [rmin, rmax) (rank from the 30 completed days before t's day)
side='lag'  : trade the K followers with the largest sign(m)*gap in the leader's direction (catch-up)
side='lead' : trade the K followers with the smallest sign(m)*gap AGAINST the leader's direction (fade the overshoot)
dirs        : 'both' | 'up' | 'down' (only BTC-up or BTC-down events)
hedge       : also take an opposite leader position of size beta_i (beta-neutral catch-up)
Entry at the OPEN of bar t+1+delay (delay=latency in bars), exit at the open H bars after entry.
No new event while a position is open. Weight 1/K of equity per coin, x lev.
Costs (fee+slip) per side on notional; funding: longs pay rate x notional at each stamp inside the hold.
Liquidation: a coin leg is wiped when its adverse move on bar high/low reaches 1/lev - 0.5% (lev>1 only).
"""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN
from lib import logret, roll_beta, roll_std, lret
from data15 import load

_CACHE = {}


def features(tf, leader):
    key = (tf, leader)
    if key in _CACHE:
        return _CACHE[key]
    P = load(tf)
    bpd = 24 if tf == "1h" else 96
    syms = list(P["syms"]); ib = syms.index("BTCUSDT")
    c = P["c"]
    r = logret(c)
    if leader == "BTC":
        lr = r[:, ib]
        lc = np.log(c[:, ib].astype(np.float64))
    else:  # equal-weight basket of the 5 highest-volume coins at each bar (point-in-time rank)
        m5 = P["rank"] < 5
        lr = np.nanmean(np.where(m5, r, np.nan), 1)
        lr = np.nan_to_num(lr)
        lc = np.cumsum(lr)
    beta = roll_beta(r, lr, 30 * bpd)
    sig = roll_std(lr, 30 * bpd)
    F = dict(P=P, bpd=bpd, ib=ib, r=r, lr=lr, lc=lc, beta=beta, sig=sig, logc=np.log(c.astype(np.float64)))
    _CACHE[key] = F
    return F


def run(tf="1h", leader="BTC", L=1, z=3.0, H=1, K=5, rmin=1, rmax=50, side="lag", dirs="both", hedge=False,
        lev=1.0, cost_mult=1.0, delay=0, start=None, end=None, exclude=(), random_seed=None, return_trades=True):
    F = features(tf, leader)
    P = F["P"]; t = P["t"]; o = P["o"]; hi = P["h"]; lo = P["l"]; c = P["c"]; fund = P["fund"]; rank = P["rank"]
    ib, beta, sig, lc, logc = F["ib"], F["beta"], F["sig"], F["lc"], F["logc"]
    T, N = c.shape
    m = np.full(T, np.nan); m[L:] = lc[L:] - lc[:-L]
    gap = beta * m[:, None] - (np.r_[np.full((L, N), np.nan), logc[L:] - logc[:-L]])
    win = np.ones(T, bool)
    if start is not None: win &= t >= start
    if end is not None: win &= t < end
    trig = win & (np.abs(m) > z * sig * np.sqrt(L))
    if dirs == "up": trig &= m > 0
    if dirs == "down": trig &= m < 0
    excl = np.zeros(N, bool)
    for s in exclude:
        excl[list(P["syms"]).index(s)] = True
    excl[ib] = True
    rng = np.random.default_rng(random_seed) if random_seed is not None else None
    cps = (FEE + SLIP) * cost_mult
    eq = 1.0
    curve_t, curve_e = [int(t[0])], [1.0]
    trades = []
    liqs = [0]
    i = int(np.argmax(trig)) if trig.any() else T
    nxt_free = 0
    idxs = np.where(trig)[0]
    for i in idxs:
        if i < nxt_free:
            continue
        e0 = i + 1 + delay; e1 = e0 + H
        if e1 >= T or (end is not None and t[e0] >= end):
            break
        s = 1.0 if m[i] > 0 else -1.0
        elig = (rank[i] >= rmin) & (rank[i] < rmax) & ~excl & ~np.isnan(gap[i]) & ~np.isnan(o[e0]) & ~np.isnan(beta[i])
        cand = np.where(elig)[0]
        if len(cand) < K:
            continue
        if rng is not None:
            pick = rng.choice(cand, K, replace=False)
        else:
            sc = s * gap[i, cand]
            pick = cand[np.argsort(-sc)[:K]] if side == "lag" else cand[np.argsort(sc)[:K]]
        d = s if side == "lag" else -s  # +1 long, -1 short
        w = lev / K
        pnl_legs = []
        for j in pick:
            seg_o = o[e0:e1 + 1, j].astype(np.float64)
            px_in = seg_o[0]
            ok = ~np.isnan(seg_o)
            if np.isnan(seg_o[-1]):  # delisted / gap: exit at last known close inside the hold
                cc = c[e0:e1, j]
                v = np.where(~np.isnan(cc))[0]
                px_out = float(cc[v[-1]]) if len(v) else px_in
            else:
                px_out = seg_o[-1]
            rel = px_out / px_in
            ret = d * (rel - 1)
            if lev > 1:  # liquidation on highs/lows during the hold
                hh = np.nanmax(hi[e0:e1, j]) if np.any(~np.isnan(hi[e0:e1, j])) else px_in
                ll = np.nanmin(lo[e0:e1, j]) if np.any(~np.isnan(lo[e0:e1, j])) else px_in
                adverse = (px_in - ll) / px_in if d > 0 else (hh - px_in) / px_in
                if adverse >= 1 / lev - MAINT_MARGIN:
                    liqs[0] += 1
                    ret = -(1 / lev)  # margin wiped (loss of this leg's margin, in leg-notional units)
            f = float(np.sum(fund[e0 + 1:e1 + 1, j]))  # stamps after entry open up to exit open
            leg = w * (ret - d * f - cps * (1 + rel))
            if hedge:
                b = float(beta[i, j])
                bo = o[e0:e1 + 1, ib].astype(np.float64)
                hret = -d * b * (bo[-1] / bo[0] - 1)
                hf = float(np.sum(fund[e0 + 1:e1 + 1, ib]))
                leg += w * (hret + d * b * hf - cps * abs(b) * 2)
            pnl_legs.append((P["syms"][j], leg))
        tot = sum(x for _, x in pnl_legs)
        tot = max(tot, -1.0)
        pnl_abs = eq * tot
        # mark-to-market path at bar opens for drawdown (approximate: linear legs on open prices, costs at entry)
        path = np.zeros(H + 1)
        for j in pick:
            seg = o[e0:e1 + 1, j].astype(np.float64)
            seg = np.where(np.isnan(seg), np.nan, seg)
            seg = np.array(seg); last = seg[0]
            for k in range(len(seg)):
                if np.isnan(seg[k]): seg[k] = last
                last = seg[k]
            path += (lev / K) * d * (seg / seg[0] - 1)
        path[-1] = tot
        for k in range(1, H + 1):
            curve_t.append(int(t[e0 + k])); curve_e.append(eq * (1 + max(path[k], -1)))
        for sym, leg in pnl_legs:
            trades.append((int(t[e0]), sym, eq * leg))
        eq = eq * (1 + tot)
        if eq <= 0:
            eq = 0.0
            break
        nxt_free = e1  # can trigger again on the bar that closes at/after exit
    # month-boundary points so every calendar month exists in the curve
    if start is not None and end is not None:
        pass
    ct = np.array(curve_t); ce = np.array(curve_e)
    if start is not None:
        ct[0] = start
    return dict(curve=list(zip(ct.tolist(), ce.tolist())), trades=trades, final=eq, liqs=liqs[0])


def fill_months(curve, start, end):
    """Add flat points at each month start (equity carried) so months without trades count as 0%."""
    from datetime import datetime, timezone
    pts = []
    d = datetime.fromtimestamp(start / 1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(d.timestamp() * 1000) < end:
        pts.append(int(d.timestamp() * 1000))
        d = d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)
    pts.append(end - 1)
    out = sorted(curve + [(p, None) for p in pts], key=lambda x: (x[0], x[1] is None))
    res, last = [], curve[0][1]
    for tt, e in out:
        if e is None:
            res.append((tt, last))
        else:
            res.append((tt, e)); last = e
    return [x for x in res if start <= x[0] < end]
