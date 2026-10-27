"""Meta-allocator engine: walk-forward sleeve weights over W1, W2 (+ optional BTC trend sleeve) and cash.
Weights for day t use sleeve returns up to day t-2 only (W2's day t-1 trade closes at 02:00 on day t), gross <= 1,
no leverage, no shorting sleeves. Rebalance every `freq` days; weights drift in between.
Rebalancing cost 0.07% x sum|target - drifted weight| (x2 in the doubled-cost run). Sleeve costs are inside their returns."""
import numpy as np
from datetime import datetime, timezone

Z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/w3r1_meta_allocator/sleeves.npz")
DAY = Z["day"].astype(np.int64)
MK = np.array([datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in DAY])
DEV = MK <= "2024-10"
TEST = MK >= "2024-11"
REB_COST = 0.0007
LAG = 2


def btc_trend(N, cost_mult=1.0):
    """BTCUSDT perp long-only trend sleeve at 1x: long on day t if close(t-1) > SMA_N(close up to t-1), else flat.
    Fee+slippage 0.07%/side on switches, funding paid while long."""
    C, F, i0 = Z["btc_close_full"], Z["btc_fund_full"], int(Z["first_idx"])
    n = len(C)
    sma = np.full(n, np.nan)
    cs = np.cumsum(np.r_[0, C])
    sma[N - 1:] = (cs[N:] - cs[:-N]) / N
    pos = np.zeros(n)
    pos[1:] = (C[:-1] > sma[:-1]).astype(float)
    r = np.zeros(n)
    r[1:] = C[1:] / C[:-1] - 1
    sw = np.abs(np.diff(np.r_[0, pos]))
    out = pos * r - pos * F - sw * 0.0007 * cost_mult
    return out[i0:i0 + len(DAY)], pos[i0:i0 + len(DAY)]


# W1 doubled-cost proxy: r4_07 results.json per-window log drag (1x vs 2x cost) spread evenly per day
_W1NET = {"dev": (283.4, 142.96), "test": (247.7, 194.3)}


def w1_2x():
    r = Z["w1"].copy()
    for m, (a, b) in (("dev", _W1NET["dev"]), ("test", _W1NET["test"])):
        msk = DEV if m == "dev" else TEST
        drag = (np.log1p(a / 100) - np.log1p(b / 100)) / msk.sum()
        r[msk] = (1 + r[msk]) * np.exp(-drag) - 1
    return r


def sleeves(btcN=None, cost_mult=1.0):
    if cost_mult == 1.0:
        R = [Z["w1"], Z["w2"]]
    else:
        R = [w1_2x(), Z["w2_2x"]]
    if btcN:
        R.append(btc_trend(btcN, cost_mult)[0])
    return np.column_stack(R)


# ---------------------------------------------------------------- target-weight rules (history h = R[:t-LAG+1])
def _vol(h, L):
    x = h[-L:]
    # floor 0.5%/day: a sleeve that sat flat (e.g. BTC trend out of the market) must not get infinite weight
    return np.maximum(x.std(axis=0, ddof=1), 0.005) if len(x) > 5 else None


def rule_targets(R, cfg):
    """cfg: dict(kind=..., params). returns target matrix T (n x k)."""
    n, k = R.shape
    T = np.full((n, k), 1.0 / k)
    kind = cfg["kind"]
    L = cfg.get("L", 60)
    eq = np.cumprod(1 + R, axis=0)
    for t in range(n):
        e = t - LAG + 1  # history R[:e] = returns through day t-LAG
        if e < 10:
            continue
        h = R[max(0, e - 400):e]
        w = np.full(k, 1.0 / k)
        if kind == "fixed":
            w = np.array(cfg["w"], float)
        elif kind == "invvol":
            s = _vol(h, L)
            w = (1 / s) / (1 / s).sum()
        elif kind == "minvar":
            x = h[-L:]
            S = np.cov(x.T)
            S = (1 - cfg["shrink"]) * S + cfg["shrink"] * np.diag(np.diag(S))
            try:
                w = np.linalg.solve(S, np.ones(k))
            except np.linalg.LinAlgError:
                w = np.ones(k)
            w = np.clip(w, 0, None)
            w = w / w.sum() if w.sum() > 0 else np.full(k, 1.0 / k)
        elif kind == "voltarget":
            base = cfg.get("base", "equal")
            if base == "invvol":
                s = _vol(h, L)
                w = (1 / s) / (1 / s).sum()
            x = h[-L:] @ w
            sp = x.std(ddof=1) * np.sqrt(365)
            w = w * min(1.0, cfg["tgt"] / sp) if sp > 0 else w
        elif kind == "relmom":  # tilt toward the sleeve with the better trailing L-day compounded return
            g = np.prod(1 + h[-L:], axis=0)
            order = np.argsort(-g)
            w = np.full(k, (1 - cfg["tilt"] - 1.0 / k) / (k - 1) if k > 2 else 0.5 - cfg["tilt"])
            if k == 2:
                w = np.array([0.5, 0.5])
                w[order[0]] += cfg["tilt"]; w[order[1]] -= cfg["tilt"]
            else:
                w = np.full(k, 1.0 / k)
                w[order[0]] += cfg["tilt"]; w[order[-1]] -= cfg["tilt"]
        elif kind == "absmom":  # time-series sleeve momentum: sleeve with negative trailing L-day return cut to `cut` of base
            g = np.prod(1 + h[-L:], axis=0) - 1
            w = np.full(k, 1.0 / k) * np.where(g > 0, 1.0, cfg["cut"])
        elif kind == "ddcut":  # sleeve drawdown de-risking
            ee = eq[e - 1]
            pk = eq[:e].max(axis=0)
            dd = 1 - ee / pk
            w = np.full(k, 1.0 / k) * np.where(dd > cfg["X"], cfg["cut"], 1.0)
        elif kind == "ivmom":  # inverse-vol base x time-series momentum filter
            s = _vol(h, L)
            w = (1 / s) / (1 / s).sum()
            g = np.prod(1 + h[-cfg["M"]:], axis=0) - 1
            w = w * np.where(g > 0, 1.0, cfg["cut"])
        w = np.clip(w, 0, None)
        if w.sum() > 1:
            w = w / w.sum()
        T[t] = w
    return T


def simulate(R, T, freq=1, cost_mult=1.0):
    n, k = R.shape
    w = T[0].copy()
    port = np.zeros(n)
    turn = np.zeros(n)
    gross = np.zeros(n)
    for t in range(n):
        if t % freq == 0:
            tc = np.abs(T[t] - w).sum()
            w = T[t].copy()
        else:
            tc = 0.0
        turn[t] = tc
        gross[t] = w.sum()
        rp = (w * R[t]).sum() - REB_COST * cost_mult * tc
        port[t] = rp
        # drift
        v = w * (1 + R[t])
        tot = 1 + (w * R[t]).sum()
        w = v / tot if tot > 0 else v
    return port, turn, gross


def mstats(r, mask=None):
    if mask is None:
        mask = np.ones(len(r), bool)
    r = r[mask]
    mk = MK[mask]
    mons = {}
    for m, x in zip(mk, r):
        mons[m] = mons.get(m, 1.0) * (1 + x)
    v = np.array([(g - 1) * 100 for g in mons.values()])
    g = np.cumprod(1 + r)
    dd = (1 - g / np.maximum.accumulate(g)).max()
    t = v.mean() / v.std(ddof=1) * np.sqrt(len(v)) if len(v) > 2 else np.nan
    return {"median_month": round(float(np.median(v)), 2),
            "geo_month": round(float((g[-1] ** (1 / len(v)) - 1) * 100), 2),
            "mean_month": round(float(v.mean()), 2), "worst_month": round(float(v.min()), 2),
            "months_up": int((v > 0).sum()), "n_months": len(v), "max_dd_pct": round(float(dd) * 100, 2),
            "net_pct": round(float(g[-1] - 1) * 100, 1), "t_stat": round(float(t), 2),
            "months": {m: round((x - 1) * 100, 2) for m, x in mons.items()}}


def run_cfg(cfg, cost_mult=1.0, R=None):
    if R is None:
        R = sleeves(cfg.get("btcN"), cost_mult)
    T = rule_targets(R, cfg)
    return simulate(R, T, cfg.get("freq", 1), cost_mult)
