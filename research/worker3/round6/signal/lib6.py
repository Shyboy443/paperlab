"""high52_plus engine: Donchian-position (nearness to the L-day high) books with
- book:  LS (10/10, 0.5 gross each leg), LO (long-only, 1x), GC (BTC>SMA gate: long-only, else cash),
         GL (BTC>SMA gate: long-only, else the LS book), GLS (gate: LS book, else cash),
         TILT (gate: long 2/3 + short 1/3 gross, else the LS book)
- exit:  D (daily full rebalance to top-k, = round-2/4 engine), H (rank hysteresis: hold while rank <= hyst*k, sizes drift),
         C (channel exit: hold long while score >= cmid, short while score <= 1-cmid, sizes drift)
- chan:  30 (single 30d channel) or BL (mean of 20/30/60/120d channel positions); lscale multiplies every length
- vol:   entries need 5d mean quote volume > 30d mean (both known at the open)
- trim:  in H/C, a position that drifts above 2x its equal weight is trimmed back to 2x
All signals use data up to the previous close; trades at the daily open; costs per side on traded notional; perp funding;
1% of previous-day quote-volume cap per position; same 1x liquidation rule as engine_r2 (only reachable by a +99.5% short).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round4/high52_v2")
import lib as L4  # noqa
from lib import FEE, SLIP, MAINT_MARGIN, DAY, summary, DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END  # noqa

BASE = dict(book="LS", exit="D", chan="30", univ=40, k=10, vol=False, trim=False, gate_ma=100, hyst=2.0, cmid=0.5,
            every=1, lscale=1.0, age=0)
_C = {}


def _shift(a):
    return L4._shift(a)


def score(Pn, chan, lscale=1.0):
    Ls = (30,) if chan == "30" else (20, 30, 60, 120)
    Ls = tuple(max(3, int(round(x * lscale))) for x in Ls)
    key = (id(Pn), "sc", Ls)
    if key not in _C:
        _C[key] = np.mean([L4.rng(Pn, x) for x in Ls], axis=0)
    return _C[key]


def gate(Pn, ma):
    key = (id(Pn), "gate", ma)
    if key not in _C:
        b = Pn.syms.index("BTCUSDT")
        cb = pd.Series(Pn.c[:, b]).ffill()
        g = (cb > cb.rolling(ma, min_periods=ma).mean()).values.astype(float)
        g[np.isnan(cb.rolling(ma, min_periods=ma).mean().values)] = np.nan
        _C[key] = _shift(g)
    return _C[key]


def volconf(Pn):
    key = (id(Pn), "vc")
    if key not in _C:
        q = pd.DataFrame(np.nan_to_num(Pn.qv))
        _C[key] = _shift((q.rolling(5).mean() > q.rolling(30).mean()).values)
        _C[key] = np.where(np.isnan(_C[key]), 0, _C[key]).astype(bool)
    return _C[key]


def run5(Pn, cfg, start, end, cost_mult=1.0, record=False, score_override=None, equity0=10_000.0, long_score=None,
         short_score=None):
    """lib6 additions (default path bit-identical to lib5): cfg age = min listing age (days) for the universe;
    long_score / short_score = optional per-leg score matrices (NaN = not eligible for that leg)."""
    c_ = dict(BASE); c_.update(cfg)
    k, ex = c_["k"], c_["exit"]
    S = score_override if score_override is not None else score(Pn, c_["chan"], c_["lscale"])
    U = L4.um(Pn, c_["univ"], c_["age"]); UR = L4.um(Pn, 2 * c_["univ"])
    G = gate(Pn, c_["gate_ma"]) if c_["book"] in ("GC", "GL", "GLS", "TILT") else None
    VC = volconf(Pn) if c_["vol"] else None
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(Pn.t, start)); i1 = int(np.searchsorted(Pn.t, end))
    N = Pn.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); margin = np.zeros(N); n_liq = 0
    curve = [(int(Pn.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N)
    pos_open = np.zeros(N, int); pos_eq0 = np.zeros(N); pos_side = np.zeros(N)
    positions = []; turnover = 0.0; costs = 0.0; active = np.zeros(i1 - i0, bool); n_long_days = 0
    last_px = np.full(N, np.nan)

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j], pos_eq0[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = Pn.o[i], Pn.h[i], Pn.l[i], Pn.c[i]
        live = units != 0
        if (i - i0) % c_["every"] == 0:
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            s = S[i]
            b = c_["book"]
            up = G[i] if G is not None else 1.0
            if G is not None and np.isnan(up):
                legs = ()
            elif b == "LS":
                legs = ((1, .5), (-1, .5))
            elif b == "LO":
                legs = ((1, 1.0),)
            elif b == "GC":
                legs = ((1, 1.0),) if up == 1 else ()
            elif b == "GL":
                legs = ((1, 1.0),) if up == 1 else ((1, .5), (-1, .5))
            elif b == "GLS":
                legs = ((1, .5), (-1, .5)) if up == 1 else ()
            elif b == "TILT":
                legs = ((1, 2 / 3), (-1, 1 / 3)) if up == 1 else ((1, .5), (-1, .5))
            else:
                raise ValueError(b)
            valid = ~np.isnan(s) & ~np.isnan(o)
            elig = U[i] & valid
            idx = np.where(elig)[0]
            tgt = np.zeros(N)
            cur = units * np.nan_to_num(px)
            if legs and len(idx) >= k * len(legs) and eq > 0:
                for sg, budget in legs:
                    LSC = long_score if sg > 0 else short_score
                    if LSC is None:
                        ss = sg * s  # higher = better for this leg
                        order = idx[np.argsort(-ss[idx], kind="stable")]
                    else:
                        ss = sg * LSC[i]
                        jdx = idx[~np.isnan(ss[idx])]
                        order = jdx[np.argsort(-ss[jdx], kind="stable")]
                    cand = order if VC is None else order[VC[i, order]]
                    if ex == "D":
                        pick = (order if VC is None else cand)[:k]
                        tgt[pick] = sg * budget * eq / k
                        continue
                    held = np.where((np.sign(units) == sg))[0]
                    keep = []
                    for j in held:
                        if not (UR[i, j] and valid[j]):
                            continue
                        if ex == "H":
                            rank = int((ss[idx] > ss[j]).sum()) + 1
                            if rank <= c_["hyst"] * k:
                                keep.append(j)
                        else:  # C
                            lvl = c_["cmid"] if sg > 0 else 1 - c_["cmid"]
                            if sg * (s[j] - lvl) >= 0:
                                keep.append(j)
                    keep = np.array(keep, int)
                    lb = budget * eq; ew = lb / k
                    kv = np.abs(cur[keep])
                    if c_["trim"]:
                        kv = np.minimum(kv, 2 * ew)
                    if kv.sum() > lb:
                        kv = kv * lb / kv.sum()
                    tgt[keep] = sg * kv
                    nfree = k - len(keep)
                    if nfree > 0:
                        top = [j for j in cand[:k] if j not in set(keep.tolist())]
                        if ex == "C":
                            top = [j for j in top if sg * (s[j] - (c_["cmid"] if sg > 0 else 1 - c_["cmid"])) > 0]
                        top = top[:nfree]
                        room = lb - kv.sum()
                        if top and room > 0:
                            tgt[top] = sg * min(ew, room / len(top))
                cap = 0.01 * np.nan_to_num(Pn.qv[i - 1])
                tgt = np.clip(tgt, -cap, cap)
            traded = np.abs(tgt - cur)
            eq -= traded.sum() * cost; turnover += traded.sum() / max(eq, 1e-9); costs += traded.sum() * cost
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    close(j, i)
            pos_pnl -= traded * cost
            new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
            pos_open[new] = i; pos_eq0[new] = eq; pos_side[new] = np.sign(tgt[new])
            pxs = np.where(np.isnan(px) | (px == 0), 1, px)
            units = np.where(tgt != 0, tgt / pxs, 0.0)
            entry = np.where(tgt != 0, px, 0.0) if ex == "D" else np.where(new, px, np.where(tgt != 0, entry, 0.0))
            margin = np.where(tgt != 0, np.abs(units) * entry, 0.0)
            last_px = np.where(tgt != 0, px, last_px)
            live = units != 0
        active[i - i0] = live.any()
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(Pn.t[i] + Pn.step), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * Pn.fund[i]), 0.0)
        eq += f.sum(); pos_pnl += f
        hh = np.where(np.isnan(h), last_px, h); ll = np.where(np.isnan(l), last_px, l)
        thr = 1.0 - MAINT_MARGIN
        with np.errstate(invalid="ignore", divide="ignore"):
            adv = np.where(units > 0, (entry - ll), (hh - entry)) / np.where(entry > 0, entry, 1)
        liq = live & (adv >= thr)
        cc = np.where(np.isnan(c), last_px, c)
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        eq += g.sum(); pos_pnl += g
        if liq.any():
            for j in np.where(liq)[0]:
                loss = -margin[j] - units[j] * (last_px[j] - entry[j])
                eq += loss; pos_pnl[j] += loss
                close(j, i + 1); units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        curve.append((int(Pn.t[i] + Pn.step), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary([curve[0]] + [(t - 1, e) for t, e in curve[1:]], trade_pnl)
    nm = max(out["n_months"], 1)
    out["turnover_per_month"] = round(turnover / nm, 2); out["cost_usd"] = costs
    out["liquidations"] = n_liq; out["active_day_frac"] = round(float(active.mean()), 3)
    out["_curve"] = curve
    if record:
        out["_positions"] = positions
    return out


stats = L4.stats
tstat = L4.tstat


def max_pos_share(r):
    return L4.max_pos_share(r)
