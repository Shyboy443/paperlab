"""coin_fit: does matching a rule to a coin ("the coin that suits the strategy") have out-of-sample skill?

Data (local caches only, no API):
- perps 2020-12..2026-10: data/monthly20_r2_rotation/panel_1d.npz (daily, funding summed per day)
- Binance spot 2017-08..2021-06: data/w3r4_high52_v2/holdout_panel.npz (no funding; shorts assumed possible, no borrow cost)
One spliced close series per coin: spot before 2020-12-01, perp from 2020-12-01. The spot part is used to TRADE only in
the 2018-2020 HOLDOUT and as signal warm-up for DEV; DEV and TEST trade the perp part (with funding).

Pair = (coin, rule). A pair's daily net return at 1x: pos(d) * r(d) - cost*|pos(d)-pos(d-1)| - pos(d)*funding(d), where
pos(d) is computed from closes up to d-1 (trade at the close of d-1), r(d) = close(d)/close(d-1)-1.
A book = equal weight across its pairs that are AVAILABLE that day (coin has data and the rule has full look-back),
rebalanced daily between pair sub-accounts; the drift-rebalance turnover is charged too.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(r"D:/Projects/BinanceBOt/paperlab")
OUT = ROOT / "data" / "w3r5_coin_fit"
DAY = 86_400_000


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


DEV = (utc(2021, 1, 1), utc(2024, 11, 1))
TEST = (utc(2024, 11, 1), utc(2026, 10, 22))
HOLD = (utc(2018, 1, 1), utc(2021, 1, 1))
SPLICE = utc(2020, 12, 1)
COST = 0.0005 + 0.0002


def load(n_coins=20):
    P = np.load(ROOT / "data/monthly20_r2_rotation/panel_1d.npz")
    H = np.load(ROOT / "data/w3r4_high52_v2/holdout_panel.npz")
    ps, hs = list(P["syms"]), list(H["syms"])
    pc, pt = P["c"], P["t"]
    # universe: top-n perps by Dec-2020 quote volume, perp live on 2020-12-01, spot history from before 2020-01-01
    i0 = int(np.searchsorted(pt, SPLICE)); i1 = int(np.searchsorted(pt, utc(2021, 1, 1)))
    qv = np.nan_to_num(P["qv"][i0:i1]).sum(0)
    cand = []
    for j in np.argsort(-qv):
        s = str(ps[j])
        if np.isnan(pc[i0, j]) or s not in hs:
            continue
        hc = H["c"][:, hs.index(s)]
        ok = np.where(~np.isnan(hc))[0]
        if not len(ok) or H["t"][ok[0]] >= utc(2020, 1, 1):
            continue
        cand.append(s)
    syms = cand[:n_coins]
    t = np.arange(int(H["t"][0]), int(pt[-1]) + DAY, DAY, dtype=np.int64)
    T, N = len(t), len(syms)
    c = np.full((T, N), np.nan); f = np.zeros((T, N))
    for k, s in enumerate(syms):
        jh = hs.index(s); jp = ps.index(s)
        ih = ((H["t"] - t[0]) // DAY).astype(int); m = H["t"] < SPLICE
        c[ih[m], k] = H["c"][m, jh]
        ip = ((pt - t[0]) // DAY).astype(int); m = pt >= SPLICE
        c[ip[m], k] = pc[m, jp]; f[ip[m], k] = np.nan_to_num(P["fund"][m, jp])
    return dict(t=t, syms=syms, c=c, fund=f, all_ranked=cand)


# ---------------- rules (fixed literature parameters; scale multiplies every look-back) ----------------
RULES = ["TSMOM60_LS", "EMA20_100", "DON55_20", "HIGH52", "RSI2_CONNORS", "BOLL20_MR"]


def _rsi(c, n):
    d = c.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(); ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))


def _state(entry, exit_, valid):
    """long/flat state machine: enter on entry, leave on exit; NaN where not valid."""
    out = np.full(len(entry), np.nan); s = 0.0
    for i in range(len(entry)):
        if not valid[i]:
            s = 0.0; continue
        if s == 0 and entry[i]:
            s = 1.0
        elif s == 1 and exit_[i]:
            s = 0.0
        out[i] = s
    return out


def signals(c, rule, scale=1.0):
    """c: 1-D close series (np). Returns position decided at close of day i (NaN = not available)."""
    L = lambda x: max(2, int(round(x * scale)))
    s = pd.Series(c)
    if rule == "TSMOM60_LS":       # Moskowitz, Ooi & Pedersen (2012); Liu & Tsyvinski (2021) for crypto
        r = s / s.shift(L(60)) - 1
        return np.where(r.isna(), np.nan, np.sign(r))
    if rule == "EMA20_100":        # moving-average trend filter (Faber 2007; Hurst, Ooi & Pedersen 2017), long/flat
        a = s.ewm(span=L(20), adjust=False).mean(); b = s.ewm(span=L(100), adjust=False).mean()
        ok = s.rolling(L(100)).count() >= L(100)
        return np.where(ok & s.notna(), (a > b).astype(float), np.nan)
    if rule == "DON55_20":         # Turtle System 2 breakout on closes (Faith 2007), long/flat
        hi = s.shift(1).rolling(L(55)).max(); lo = s.shift(1).rolling(L(20)).min()
        valid = (hi.notna() & s.notna()).values
        return _state((s > hi).values, (s < lo).values, valid)
    if rule == "HIGH52":           # 52-week-high nearness (George & Hwang 2004): long while close >= 90% of 365d high
        hi = s.rolling(L(365)).max()
        return np.where(hi.isna() | s.isna(), np.nan, (s >= 0.9 * hi).astype(float))
    if rule == "RSI2_CONNORS":     # Connors & Alvarez (2008): long RSI(2)<10 above SMA200, exit close > SMA5
        r = _rsi(s, L(2)); m200 = s.rolling(L(200)).mean(); m5 = s.rolling(L(5)).mean()
        valid = (m200.notna() & r.notna()).values
        return _state(((r < 10) & (s > m200)).values, (s > m5).values, valid)
    if rule == "BOLL20_MR":        # Bollinger (2001) band reversion, long/flat: buy close < mid-2sd, exit close >= mid
        m = s.rolling(L(20)).mean(); sd = s.rolling(L(20)).std()
        valid = (m.notna()).values
        return _state((s < m - 2 * sd).values, (s >= m).values, valid)
    raise ValueError(rule)


def pair_returns(D, rules=RULES, scale=1.0, cost_mult=1.0):
    """Returns R (T x N*K net daily return of each pair at 1x), A (availability), POS (position), pair list."""
    c, f = D["c"], D["fund"]
    T, N = c.shape
    ret = np.full((T, N), np.nan); ret[1:] = c[1:] / c[:-1] - 1
    Rs, As, Ps, pairs = [], [], [], []
    for k in range(N):
        for rule in rules:
            sig = signals(c[:, k], rule, scale)
            pos = np.full(T, np.nan); pos[1:] = sig[:-1]
            avail = ~np.isnan(pos) & ~np.isnan(ret[:, k])
            p0 = np.where(avail, pos, 0.0)
            prev = np.r_[0.0, p0[:-1]]
            r = p0 * np.nan_to_num(ret[:, k]) - COST * cost_mult * np.abs(p0 - prev) - p0 * f[:, k]
            Rs.append(np.where(avail, r, 0.0)); As.append(avail); Ps.append(p0); pairs.append((D["syms"][k], rule))
    return np.array(Rs).T, np.array(As).T, np.array(Ps).T, pairs


def book(R, A, W, P=None, cost_mult=1.0):
    """W: (n_pairs,) or (T, n_pairs) 0/1 inclusion. Equal weight across included & available pairs.
    Returns daily book returns (np) incl. a drift-rebalance cost between sub-accounts (only positions trade)."""
    W = np.broadcast_to(W, R.shape) if W.ndim == 1 else W
    M = A & (W > 0)
    n = M.sum(1)
    w = np.where(M, 1.0 / np.maximum(n, 1)[:, None], 0.0)
    rb = (w * R).sum(1)
    if P is not None:
        drift = (w * np.abs(R - rb[:, None]) * np.abs(P)).sum(1)
        rb = rb - COST * cost_mult * drift
    return rb


def window(t, rb, win):
    m = (t >= win[0]) & (t < win[1])
    return t[m], rb[m]


def monthly(t, rb):
    key = pd.to_datetime(t, unit="ms", utc=True).strftime("%Y-%m")
    s = pd.Series(1 + rb).groupby(key).prod() - 1
    return {k: float(v * 100) for k, v in s.items()}


def mstats(months, daily=None, trades_pm=None):
    v = np.array(list(months.values()), float)
    eq = np.cumprod(1 + v / 100)
    out = dict(n=len(v), median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
               mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
               t=round(float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))), 2) if v.std() > 0 else None,
               net=round(float((eq[-1] - 1) * 100), 2))
    if daily is not None:
        e = np.cumprod(1 + daily); pk = np.maximum.accumulate(e)
        out["max_dd"] = round(float((1 - e / pk).max() * 100), 2)
        out["sharpe_ann"] = round(float(daily.mean() / daily.std() * math.sqrt(365)), 2) if daily.std() > 0 else None
    else:
        pk = np.maximum.accumulate(np.r_[1, eq])[1:]
        out["max_dd_monthly"] = round(float((1 - eq / pk).max() * 100), 2)
    if trades_pm is not None:
        out["trades_per_month"] = round(trades_pm, 1)
    return out


def sharpe(x):
    return float(x.mean() / x.std() * math.sqrt(365)) if len(x) > 20 and x.std() > 0 else float("nan")


def select_static(R, A, t, pairs, win, metric="sharpe", thr=0.5, rules=RULES):
    """per coin: rule with the best DEV metric; skip the coin if the best metric <= thr. Returns 0/1 vector."""
    m = (t >= win[0]) & (t < win[1])
    W = np.zeros(len(pairs)); choice = {}
    coins = sorted(set(p[0] for p in pairs), key=[p[0] for p in pairs].index)
    for coin in coins:
        idx = [i for i, p in enumerate(pairs) if p[0] == coin]
        sc = []
        for i in idx:
            a = A[m, i]
            x = R[m, i][a]
            if a.mean() < 0.5:
                sc.append(float("nan")); continue
            if metric == "sharpe":
                sc.append(sharpe(x))
            elif metric == "net":
                sc.append(float(np.prod(1 + x) - 1))
            elif metric == "calmar":
                e = np.cumprod(1 + x); dd = (1 - e / np.maximum.accumulate(e)).max()
                cagr = e[-1] ** (365 / len(x)) - 1
                sc.append(float(cagr / max(dd, 1e-6)))
        sc = np.array(sc)
        if np.all(np.isnan(sc)):
            choice[coin] = None; continue
        b = int(np.nanargmax(sc))
        thr_eff = thr if metric == "sharpe" else (0.0 if metric == "net" else thr)
        if sc[b] > thr_eff:
            W[idx[b]] = 1; choice[coin] = (pairs[idx[b]][1], round(float(sc[b]), 3))
        else:
            choice[coin] = ("SKIP", round(float(sc[b]), 3))
    return W, choice


def count_trades(P, W, t, win):
    m = (t >= win[0]) & (t < win[1])
    sel = W > 0 if W.ndim == 1 else None
    Pm = P[m][:, sel] if sel is not None else P[m]
    ch = (np.abs(np.diff(Pm, axis=0)) > 0).sum()
    return ch / 2 / max(m.sum() / 30.44, 1)  # round trips per month


def tstat(v):
    v = np.asarray(v, float)
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else float("nan")


def boot_p5(v, seed=7, n=5000):
    v = np.asarray(v, float); g = np.random.default_rng(seed); nb = math.ceil(len(v) / 3)
    st = g.integers(0, len(v) - 2, size=(n, nb))
    return float(np.percentile([np.concatenate([v[s:s + 3] for s in row])[:len(v)].mean() for row in st], 5))


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)
