"""high52_v2: perp DEV/TEST panel (round-2 data) + spot holdout panel, variant scores/weights, engine copy with weights."""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/backfill")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END, FEE, SLIP, MAINT_MARGIN, DAY, summary, utc  # noqa

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r4_high52_v2")
HO_START, HO_END = utc(2018, 1, 1), utc(2021, 1, 1)


def holdout_panel():
    import w2bf
    p = OUT / "holdout_panel.npz"
    if not p.exists():
        syms = [s for s in json.loads((w2bf.CACHE / "list.json").read_text()) if not w2bf.excluded(s)]
        t = np.arange(w2bf.DATA_FROM, w2bf.DATA_END, DAY, dtype=np.int64)
        keep, arrs = [], []
        for s in syms:
            a = w2bf.load("1d", s)
            if a is None or not len(a):
                continue
            a = a[a[:, 5] > 0]
            if len(a):
                keep.append(s); arrs.append(a)
        T, N = len(t), len(keep)
        o, h, l, c, qv = (np.full((T, N), np.nan) for _ in range(5))
        first = np.zeros(N, np.int64)
        for j, a in enumerate(arrs):
            i = ((a[:, 0].astype(np.int64) - w2bf.DATA_FROM) // DAY).astype(int)
            o[i, j], h[i, j], l[i, j], c[i, j], qv[i, j] = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 6]
            first[j] = int(a[0, 0])
        np.savez_compressed(p, t=t, syms=np.array(keep), o=o, h=h, l=l, c=c, qv=qv, first=first)
    z = np.load(p)
    D = {k: z[k] for k in z.files}
    D["fund"] = np.zeros_like(D["c"])
    return D


def make_panel(D, cols=None):
    return E.Panel(D, cols)


_C = {}


def um(Pn, n=40, age=0):
    key = (id(Pn), n, age)
    if key not in _C:
        m = E.universe_mask(Pn, n)
        if age:
            m = m & ((Pn.t[:, None] - Pn.onboard[None, :]) >= age * DAY)
        _C[key] = m
    return _C[key]


def _shift(a):
    out = np.full(a.shape, np.nan); out[1:] = a[:-1]; return out


def rng(Pn, L):
    key = (id(Pn), "rng", L)
    if key not in _C:
        h = pd.DataFrame(Pn.h); l = pd.DataFrame(Pn.l)
        mp = int(round(L * 0.9))
        hx = h.rolling(L, min_periods=mp).max().values; lx = l.rolling(L, min_periods=mp).min().values
        with np.errstate(invalid="ignore", divide="ignore"):
            s = (Pn.c - lx) / (hx - lx)
        _C[key] = _shift(s)
    return _C[key]


def blend(Pn, Ls=(20, 30, 60)):
    return np.mean([rng(Pn, L) for L in Ls], axis=0)


def ivol(Pn, W=30):
    key = (id(Pn), "iv", W)
    if key not in _C:
        lr = np.log(pd.DataFrame(Pn.c)).diff()
        v = lr.rolling(W, min_periods=int(W * 0.8)).std().values
        with np.errstate(divide="ignore"):
            _C[key] = _shift(1.0 / v)
    return _C[key]


def _waterfill(w, cap):
    w = w / w.sum()
    for _ in range(20):
        over = w > cap + 1e-12
        if not over.any():
            break
        ex = (w[over] - cap).sum(); w[over] = cap
        free = w < cap - 1e-12
        if not free.any():
            break
        w[free] += ex * w[free] / w[free].sum()
    return w


def run(P, umask_bar, score, params, start, end, cost_mult=1.0, equity0=10_000.0, record=False, wt=None, wcap=1.5):
    """engine_r2.run copied (incl. its 1x liquidation rule); only change: optional wt (T x N, > 0) gives
    within-leg weights, water-filled to a cap of wcap x equal weight. With wt=None it equals engine_r2.run."""
    kL, kS, rb, lev = params["k_long"], params["k_short"], params["rebal"], params["lev"]
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    N = P.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N); n_liq = 0
    curve = [(int(P.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N)
    pos_open = np.zeros(N, int); pos_eq0 = np.zeros(N); pos_side = np.zeros(N)
    positions = []; turnover = 0.0; costs = 0.0; active = np.zeros(i1 - i0, bool)
    last_px = np.full(N, np.nan)

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j], pos_eq0[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]
        live = units != 0
        if (i - i0) % rb == 0:
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            s = score[i]
            elig = umask_bar[i] & ~np.isnan(s) & ~np.isnan(o)
            if wt is not None:
                elig &= np.isfinite(wt[i]) & (np.nan_to_num(wt[i], posinf=0) > 0)
            idx = np.where(elig)[0]
            tgt = np.zeros(N)
            if len(idx) >= (kL + kS) and eq > 0:
                order = idx[np.argsort(-s[idx], kind="stable")]
                longs = order[:kL] if kL else np.array([], int)
                shorts = order[::-1][:kS] if kS else np.array([], int)
                nside = (kL > 0) + (kS > 0)
                for leg, k, sg in ((longs, kL, 1), (shorts, kS, -1)):
                    if not k:
                        continue
                    if wt is None:
                        tgt[leg] = sg * lev * eq / nside / k
                    else:
                        w = _waterfill(wt[i, leg].astype(float), wcap / k)
                        tgt[leg] = sg * lev * eq / nside * w
                cap = 0.01 * np.nan_to_num(P.qv[i - 1])
                tgt = np.clip(tgt, -cap, cap)
            cur = units * np.nan_to_num(px)
            traded = np.abs(tgt - cur)
            eq -= traded.sum() * cost; turnover += traded.sum() / max(eq, 1e-9); costs += traded.sum() * cost
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    close(j, i)
            pos_pnl -= traded * cost
            new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
            pos_open[new] = i; pos_eq0[new] = eq; pos_side[new] = np.sign(tgt[new])
            units = np.where(tgt != 0, tgt / np.where(np.isnan(o), 1, o), 0.0)
            entry = np.where(tgt != 0, o, 0.0)
            margin = np.where(np.abs(tgt) > 0, np.abs(tgt) / max(lev, 1e-9), 0.0)
            last_px = np.where(tgt != 0, o, last_px)
            live = units != 0
        active[i - i0] = live.any()
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(P.t[i] + P.step), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * P.fund[i]), 0.0)
        eq += f.sum(); pos_pnl += f
        hh = np.where(np.isnan(h), last_px, h); ll = np.where(np.isnan(l), last_px, l)
        if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9:
            thr = 1.0 / lev - MAINT_MARGIN
            with np.errstate(invalid="ignore", divide="ignore"):
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
                loss = -margin[j] - units[j] * (last_px[j] - entry[j])
                eq += loss; pos_pnl[j] += loss
                close(j, i + 1); units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        curve.append((int(P.t[i] + P.step), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    # calendar-month fix: the equity point stamped T+1d 00:00 is the close of day T, so it belongs to T's month
    # (the shared summary() otherwise creates a 1-day stub month after the window end, e.g. DEV '2024-11').
    out = summary([curve[0]] + [(t - 1, e) for t, e in curve[1:]], trade_pnl)
    nm = max(out["n_months"], 1)
    out["turnover_per_month"] = round(turnover / nm, 2); out["cost_usd"] = costs
    out["liquidations"] = n_liq
    out["active_day_frac"] = round(float(active.mean()), 3)
    out["_curve"] = curve; out["_active"] = active
    if record:
        out["_positions"] = positions
    return out


VARIANTS = {
    "ORIG": dict(), "A60": dict(age=60), "A90": dict(age=90), "IV": dict(iv=True), "BL": dict(bl=True), "W7": dict(rebal=7),
    "A90+IV": dict(age=90, iv=True), "A90+BL": dict(age=90, bl=True), "BL+IV": dict(bl=True, iv=True),
}


def run_var(Pn, var, start, end, cost_mult=1.0, record=False, L=30, k=10, rebal=1, univ=40, kL=None, kS=None, lev=1.0,
            score_override=None):
    v = dict(VARIANTS[var]) if isinstance(var, str) else dict(var)
    rebal = v.get("rebal", rebal)
    sc = score_override if score_override is not None else (blend(Pn) if v.get("bl") else rng(Pn, L))
    wt = ivol(Pn) if v.get("iv") else None
    prm = dict(k_long=k if kL is None else kL, k_short=k if kS is None else kS, rebal=rebal, lev=lev)
    return run(Pn, um(Pn, univ, v.get("age", 0)), sc, prm, start, end, cost_mult=cost_mult, record=record, wt=wt)


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else float("nan")


def stats(r):
    m = r["months"]; nm = len(m)
    return dict(median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"], up=f'{r["months_up"]}/{nm}',
                max_dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(m), 2), trades_per_month=round(r["trades"] / nm, 1),
                worst=r["worst_month"], best=r["best_month"], turnover_x_per_month=r["turnover_per_month"],
                active_days=r["active_day_frac"])


def max_pos_share(r):
    pos = r["_positions"]; netp = r["_curve"][-1][1] - r["_curve"][0][1]
    if netp <= 0:
        return float("nan")
    return round(max(p[4] for p in pos) / netp * 100, 2)
