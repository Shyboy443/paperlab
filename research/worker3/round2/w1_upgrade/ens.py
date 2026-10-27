"""COPY of research/monthly20/r4_07_ensemble/ens.py (w1_upgrade: only addition = simulate(pos_out=...)).
r4_07_ensemble: one position-level engine for a portfolio of the round-2 "weak edges" + causal vol targeting.

Price panel: r2_trend's survivorship-free daily panel (data/monthly20_r2_trend/panel.npz; data.binance.vision archive,
delisted coins included, non-crypto contracts excluded). Daily bar d = 00:00..24:00 UTC.

Legs (each gives a signed target fraction of its sleeve capital per coin, F[d, j], plus a mask of the days/coins on which
the leg (re)sizes, exactly when the round-2 strategy would trade):
  R  rotation (r2_rotation = agent 02 P4): top-40 by 30-day quote volume, 14-day return, long top 10 / short bottom 10,
     0.05 each, resized every 7 days from the run start (positions held as units in between).
  M  ML walk-forward (r2_ml A_hgb_H6_k3): the CACHED walk-forward predictions of r2_ml (pred_full_H6_all_s0.npz),
     long the 3 best / short the 3 worst, 1/6 each, resized daily at 00:00 UTC.
  T  60-day momentum (r2_trend T2): sign(close d-1 vs close d-61), monthly top-50 universe, inverse 30-day-vol weights
     (cap 2.5/N), opens / closes / flips on any day, resized on the 1st of the month.
  B  BTC trend (optional leg): sign of BTC's 60-day return, 1.0 x sleeve, resized on flips and on the 1st.

Engine: book equity E. Sleeve budget_i = L x w_i x E. Units per sleeve; the account holds the NET units per coin.
Costs (FEE+SLIP) x cost_mult on every net traded notional, funding on net notional, net notional capped at 1% of the
previous day's quote volume, isolated-margin liquidation per coin at the leverage in force when the position was last
sized: lost when the day's low/high crosses entry x (1 -/+ (1/L - 0.5%)) -> whole margin (notional/L) lost.
A coin with no later bars (delisted) is closed at its last close. Research only: no orders, no keys.
"""
from __future__ import annotations
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, DAY, HOUR, DEV_START, DEV_END, TEST_START, TEST_END, monthly_returns  # noqa

PROJECT = Path(r"D:/Projects/BinanceBOt/paperlab")
PANEL = PROJECT / "data" / "monthly20_r2_trend" / "panel.npz"
MLD = PROJECT / "data" / "monthly20_r2_ml"
DATA_FROM_ML = 1577836800000
BAR4 = 4 * HOUR


class Data:
    def __init__(self):
        z = np.load(PANEL)
        self.syms = [str(s) for s in z["syms"]]
        self.col = {s: j for j, s in enumerate(self.syms)}
        self.day = z["day"].astype(np.int64)
        self.O, self.H, self.L, self.C, self.Q, self.F = (z[k] for k in ("O", "H", "L", "C", "QV", "FUND"))
        self.T, self.N = self.O.shape
        self.has = ~np.isnan(self.O)
        idx = np.arange(self.T)[:, None]
        self.first = np.where(self.has.any(0), np.argmax(self.has, 0), self.T)
        self.last = np.where(self.has.any(0), self.T - 1 - np.argmax(self.has[::-1], 0), -1)
        self.Cf = pd.DataFrame(self.C).ffill().values
        dts = [datetime.fromtimestamp(t / 1000, timezone.utc) for t in self.day]
        self.dom = np.array([d.day for d in dts])
        self.mkey = np.array([d.strftime("%Y-%m") for d in dts])
        lr = np.log(pd.DataFrame(self.Cf)).diff()
        lr[~pd.DataFrame(self.has)] = np.nan
        self.vol30 = (lr.rolling(30, min_periods=20).std() * math.sqrt(365)).values
        self.qcs = np.vstack([np.zeros((1, self.N)), np.cumsum(np.nan_to_num(self.Q), 0)])
        self.hcs = np.vstack([np.zeros((1, self.N)), np.cumsum(self.has, 0)])

    def idx(self, ms):
        return int(np.searchsorted(self.day, ms))


# ------------------------------------------------------------------ legs
def leg_rotation(D, lb=14, k=10, univ=40, drop=(), rng=None):
    """F on every day (the engine only applies it on the leg's rebalance days)."""
    T, N = D.T, D.N
    dropj = np.array([D.col[s] for s in drop if s in D.col], int)
    score = np.full((T, N), np.nan)
    score[1 + lb:] = D.C[lb:-1] / D.C[:-1 - lb] - 1  # close d-1 / close d-1-lb
    F = np.zeros((T, N))
    for d in range(30, T):
        vol = D.qcs[d] - D.qcs[d - 30]
        ok = (D.hcs[d] - D.hcs[d - 30] >= 24) & (D.first <= d - 30)
        if len(dropj):
            ok[dropj] = False
        v = np.where(ok, vol, -1)
        top = np.argsort(-v)[:univ]
        top = top[v[top] > 0]
        s = score[d, top]
        el = top[~np.isnan(s) & D.has[d, top]]
        if len(el) < 2 * k:
            continue
        if rng is not None:
            order = rng.permutation(el)
        else:
            order = el[np.argsort(-score[d, el], kind="stable")]
        F[d, order[:k]] = 0.5 / k
        F[d, order[::-1][:k]] = -0.5 / k
    return F


def leg_ml(D, k=3, seed=0, drop=(), rng=None):
    z = np.load(MLD / f"pred_full_H6_all_s{seed}.npz")
    uni = json.loads((MLD / "universes.json").read_text())
    msyms = sorted(set(s for v in uni.values() for s in v) | {"BTCUSDT"})
    cols = np.array([D.col.get(s, -1) for s in msyms])
    keep = cols >= 0
    for s in drop:
        if s in msyms:
            keep[msyms.index(s)] = False
    F = np.zeros((D.T, D.N))
    for t, s in zip(z["t"], z["s"]):
        dms = DATA_FROM_ML + (int(t) + 1) * BAR4
        d = D.idx(dms)
        if d >= D.T or D.day[d] != dms:
            continue
        ok = np.where(np.isfinite(s) & keep)[0]
        ok = ok[D.has[d, cols[ok]]]
        if len(ok) < 2 * k:
            continue
        order = rng.permutation(ok) if rng is not None else ok[np.argsort(s[ok])]
        F[d, cols[order[::-1][:k]]] = 1 / (2 * k)
        F[d, cols[order[:k]]] = -1 / (2 * k)
    return F


def month_universe(D, n, drop=()):
    U = np.zeros((D.T, D.N), bool)
    dropj = [D.col[s] for s in drop if s in D.col]
    starts = [i for i in range(31, D.T) if D.dom[i] == 1] + [D.T]
    for a, b in zip(starts[:-1], starts[1:]):
        q = D.Q[a - 30:a]
        s = np.nan_to_num(q).sum(0)
        s[(q > 0).sum(0) < 24] = 0
        s[dropj] = 0
        top = np.argsort(-s)[:n]
        U[a:b, top[s[top] > 0]] = True
    return U


def leg_mom(D, look=60, univ=50, drop=(), rng=None):
    U = month_universe(D, univ, drop)
    C = D.Cf
    prev = np.vstack([np.full((look, D.N), np.nan), C[:-look]])
    raw = np.where(C > prev, 1, np.where(C < prev, -1, 0))
    raw[np.isnan(prev) | np.isnan(C) | (D.hcs[1:] <= look)] = 0
    S = np.zeros_like(raw)
    S[1:] = raw[:-1]
    if rng is not None:  # same counts / days / weights rule, coins re-assigned inside each month's universe
        starts = [i for i in range(31, D.T) if D.dom[i] == 1] + [D.T]
        S2 = np.zeros_like(S)
        for a, b in zip(starts[:-1], starts[1:]):
            mem = np.where(U[a])[0]
            S2[a:b, rng.permutation(mem)] = S[a:b, mem]
        S = S2
    S = S * U * D.has
    v = np.vstack([np.full((1, D.N), np.nan), D.vol30[:-1]])
    ok = U & ~np.isnan(v) & (v > 0)
    iv = np.where(ok, 1 / np.where(ok, v, 1), 0)
    W = np.minimum(iv / np.maximum(iv.sum(1, keepdims=True), 1e-12), 2.5 / univ)
    S[~ok] = 0
    return (S * W)


def leg_btc(D, look=60, rng=None):
    j = D.col["BTCUSDT"]
    C = D.Cf[:, j]
    prev = np.r_[np.full(look, np.nan), C[:-look]]
    raw = np.where(C > prev, 1.0, -1.0)
    raw[np.isnan(prev)] = 0
    s = np.r_[0, raw[:-1]]
    if rng is not None:  # random side, re-drawn at the same flip / month days
        chg = np.r_[True, s[1:] != s[:-1]] | (D.dom == 1)
        r = rng.choice([-1.0, 1.0], size=D.T)
        cur = 0.0
        for d in range(D.T):
            if chg[d]:
                cur = r[d] if s[d] != 0 else 0.0
            s[d] = cur
    F = np.zeros((D.T, D.N))
    F[:, j] = s
    return F


def trade_mask(D, F, kind):
    """bool T x N: days/coins on which the leg re-sizes (before the run-start anchor for weekly legs)."""
    if kind == "R":
        return None  # every 7 days from the run start, all coins: built in the engine
    if kind == "M":
        return np.ones_like(F, bool)
    side = np.sign(F)
    chg = np.vstack([np.ones((1, D.N), bool), side[1:] != side[:-1]])
    return chg | (D.dom == 1)[:, None]


# ------------------------------------------------------------------ engine
def simulate(D, legs, start, end, wpath=None, lpath=None, lev=1.0, cost_mult=1.0, record=False, eq0=10_000.0, pos_out=None):
    """legs: list of (kind, F, mask). wpath: T x nlegs weights (rows used on book rebalance days) or None = equal.
    lpath: length-T book leverage (vol target) or None = constant lev. Returns dict with curve, daily returns, stats."""
    nl = len(legs)
    c_side = (FEE + SLIP) * cost_mult
    i0, i1 = D.idx(start), D.idx(end)
    N = D.N
    su = np.zeros((nl, N))      # sleeve units
    u = np.zeros(N)             # net units
    lastpx = np.full(N, np.nan)
    entry = np.zeros(N); plev = np.ones(N)
    epi_pnl = np.zeros(N); epi_start = np.zeros(N, int)
    episodes = []
    E = eq0
    curve = [(int(D.day[i0]), E)]
    w_cur = None; L_cur = None
    nliq = 0; wiped = False; turnover = 0.0
    lev_used = []

    def end_episode(j, d):
        if record:
            episodes.append((j, int(epi_start[j]), d, float(epi_pnl[j])))
        epi_pnl[j] = 0.0

    for d in range(i0, i1):
        o = D.O[d]
        hasd = D.has[d]
        live = u != 0
        # mark open
        mj = np.where(live & hasd)[0]
        g = u[mj] * (o[mj] - lastpx[mj]); E += g.sum(); epi_pnl[mj] += g; lastpx[mj] = o[mj]
        # delisted while held: close at the last close
        gone = np.where(live & ~hasd & (D.last < d))[0]
        for j in gone:
            c = abs(u[j]) * lastpx[j] * c_side
            E -= c; epi_pnl[j] -= c; end_episode(j, d)
            u[j] = 0; su[:, j] = 0
        if E <= 0:
            wiped = True; E = 1e-9
            curve.append((int(D.day[d]) + DAY - 1, E)); break
        # book weights / leverage
        resize_all = False
        w_new = (np.full(nl, 1 / nl) if wpath is None else wpath[d])
        if w_cur is None or (wpath is not None and np.any(np.abs(w_new - w_cur) > 1e-12)):
            w_cur = w_new.copy(); resize_all = True
        L_new = lev if lpath is None else lpath[d]
        if L_cur is None or L_new != L_cur:
            L_cur = L_new; resize_all = True
        lev_used.append(L_cur)
        newsu = su.copy()
        touched = np.zeros(N, bool)
        for i, (kind, F, M) in enumerate(legs):
            if kind == "R":
                rs = np.full(N, (d - i0) % 7 == 0)
            else:
                rs = M[d].copy()
            if resize_all:
                rs[:] = True
            rs &= hasd
            budget = L_cur * w_cur[i] * E
            newsu[i, rs] = F[d, rs] * budget / o[rs]
            touched |= rs
        tgt = newsu.sum(0)
        # liquidity cap on the net notional
        cap = 0.01 * np.nan_to_num(D.Q[d - 1])
        notional = np.abs(tgt) * np.nan_to_num(o)
        over = touched & (notional > cap) & (notional > 0)
        if over.any():
            f = np.ones(N); f[over] = cap[over] / notional[over]
            tgt *= f; newsu *= f
        du = tgt - u
        tj = np.where((np.abs(du) > 1e-15) & hasd)[0]
        if len(tj):
            cost = np.abs(du[tj]) * o[tj] * c_side
            E -= cost.sum(); turnover += (np.abs(du[tj]) * o[tj]).sum() / max(E, 1e-9)
            for j in tj:
                old, new = u[j], tgt[j]
                if old != 0 and (new == 0 or np.sign(new) != np.sign(old)):
                    end_episode(j, d)
                if new != 0 and (old == 0 or np.sign(new) != np.sign(old)):
                    entry[j] = o[j]; epi_start[j] = d; plev[j] = L_cur
                elif new != 0 and abs(new) > abs(old):
                    entry[j] = (old * entry[j] + (new - old) * o[j]) / new; plev[j] = L_cur
                elif new != 0:
                    plev[j] = L_cur
            epi_pnl[tj] -= cost
            lastpx[tj] = o[tj]
            u[tj] = tgt[tj]
        su = newsu
        if pos_out is not None:  # w1_upgrade addition: net notional / equity per coin after the 00:00 re-size
            pos_out[d] = u * np.nan_to_num(o) / max(E, 1e-9)
        # liquidation
        hj = np.where((u != 0) & hasd)[0]
        if len(hj):
            sd = np.sign(u[hj]); thr = 1 / plev[hj] - MAINT_MARGIN
            liq_px = entry[hj] * (1 - sd * thr)
            hit = np.where(sd > 0, D.L[d, hj] <= liq_px, D.H[d, hj] >= liq_px)
            for j in hj[hit]:
                px = entry[j] * (1 - np.sign(u[j]) / plev[j])
                pnl = u[j] * (px - lastpx[j])
                E += pnl; epi_pnl[j] += pnl; nliq += 1
                end_episode(j, d)
                u[j] = 0; su[:, j] = 0
        # close + funding
        hj = np.where((u != 0) & hasd)[0]
        c = D.C[d]
        g = u[hj] * (c[hj] - lastpx[hj]) - u[hj] * c[hj] * D.F[d, hj]
        E += g.sum(); epi_pnl[hj] += g; lastpx[hj] = c[hj]
        if E <= 0:
            wiped = True; E = 1e-9
            curve.append((int(D.day[d]) + DAY - 1, E)); break
        curve.append((int(D.day[d]) + DAY - 1, E))
    for j in np.where(u != 0)[0]:
        end_episode(j, i1)
    eqs = np.array([e for _, e in curve])
    out = dict(curve=curve, ret=eqs[1:] / eqs[:-1] - 1, i0=i0, liquidations=nliq, wiped=wiped,
               turnover_per_year=turnover / max((len(curve) - 1) / 365, 1e-9), lev_mean=float(np.mean(lev_used)) if lev_used else None,
               lev_max=float(np.max(lev_used)) if lev_used else None)
    if record:
        out["episodes"] = episodes
    return out


# ------------------------------------------------------------------ weights / vol target (causal: data up to d-1)
def weight_path(D, R, scheme, lookback=60, every=7, start_i=0, min_obs=20):
    """R: T x nlegs standalone daily leg returns (NaN before available). Weights fixed at day d use rows < d."""
    T, n = R.shape
    W = np.full((T, n), 1 / n)
    cur = np.full(n, 1 / n)
    for d in range(T):
        if (d - start_i) % every == 0:
            X = R[max(0, d - lookback):d]
            X = X[~np.isnan(X).any(1)]
            if scheme == "EW" or len(X) < min_obs:
                cur = np.full(n, 1 / n)
            elif scheme == "IV":
                sd = X.std(0, ddof=1) + 1e-9
                cur = (1 / sd) / (1 / sd).sum()
            elif scheme == "ERC":
                S = np.cov(X.T) + np.eye(n) * 1e-10
                w = np.full(n, 1 / n)
                for _ in range(200):
                    rc = w * (S @ w)
                    w = w * np.sqrt((rc.mean() / np.maximum(rc, 1e-18)))
                    w = w / w.sum()
                cur = w
        W[d] = cur
    return W


def vol_target_path(D, r_book, start_i, sigma_target, lookback=30, cap=10.0, band=0.2, min_obs=20):
    """r_book: length-T 1x book daily returns (NaN where unknown). L_d = min(cap, target / realised vol up to d-1),
    changed only when it moves by more than `band` (relative)."""
    T = len(r_book)
    Lp = np.ones(T)
    cur = None
    for d in range(T):
        x = r_book[max(0, d - lookback):d]
        x = x[~np.isnan(x)]
        if len(x) < min_obs:
            tgt = 1.0
        else:
            sv = x.std(ddof=1) * math.sqrt(365)
            tgt = min(cap, sigma_target / max(sv, 1e-6))
        if cur is None or abs(tgt / cur - 1) > band:
            cur = tgt
        Lp[d] = cur
    return Lp


def stats_of(curve):
    months = monthly_returns(curve)
    v = np.array(list(months.values()))
    n = len(v)
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e); dd = max(dd, 1 - e / peak if peak > 0 else 1)
    g = curve[-1][1] / curve[0][1]
    return dict(months={k: round(float(x), 2) for k, x in months.items()}, n_months=n,
                mean_month=round(float(v.mean()), 2), median_month=round(float(np.median(v)), 2),
                geo_month=round(float((g ** (1 / n) - 1) * 100) if g > 0 else -100.0, 2),
                worst_month=round(float(v.min()), 2), best_month=round(float(v.max()), 2),
                months_up=int((v > 0).sum()), net_pct=round(float((g - 1) * 100), 2), max_dd_pct=round(dd * 100, 2),
                t_stat=round(float(v.mean() / (v.std(ddof=1) / math.sqrt(n))), 2) if n > 2 else None)
