"""lit_scout shared engine: daily weight-based backtest on the survivorship-free daily panel (r2_rotation/data.py),
plus the 8 luck checks of research/monthly20/ROBUSTNESS.md.

Day d: targets decided from data known before day d's open (00:00 UTC), filled at day d's open, held to close.
Costs: (FEE+SLIP) x traded notional; funding: -notional x summed daily funding (long pays positive funding).
Months are counted by the calendar day a daily bar covers.
"""
from __future__ import annotations
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
from common import FEE, SLIP, DAY, DEV_START, DEV_END, TEST_START, TEST_END, summary, utc  # noqa
import data as R2DATA  # noqa
from engine_r2 import Panel, universe_mask  # noqa

PROJECT = Path(r"D:/Projects/BinanceBOt/paperlab")
HERE = Path(__file__).resolve().parent
DATA = PROJECT / "data" / "w3r1_lit_scout"
FULL_START = DEV_START

D = R2DATA.build()
P = Panel(D)
T, N = P.c.shape
SYMS = P.syms
_UM = {}


def umask(n):
    if n not in _UM:
        _UM[n] = universe_mask(P, n)
    return _UM[n]


def idx(ms):
    return int(np.searchsorted(P.t, ms))


def daystr(i):
    return datetime.fromtimestamp(int(P.t[i]) / 1000, timezone.utc).strftime("%Y-%m-%d")


def simulate(W, start, end, cost_mult=1.0, eq0=10_000.0, exclude=None):
    """W: (T,N) target weights (fraction of equity, signed) or NaN row = keep current holdings (no rebalance).
    Returns dict(curve, pos=list of (j, open_i, close_i, pnl), trades, turnover)."""
    i0, i1 = idx(start), idx(end)
    cost = (FEE + SLIP) * cost_mult
    o = P.o; c = P.c; fund = P.fund
    eq = eq0
    notional = np.zeros(N)
    curve = [(int(P.t[i0]), eq)]
    pos_pnl = np.zeros(N); pos_open = np.full(N, -1)
    positions = []
    n_trades = 0
    for i in range(i0, i1):
        w = W[i]
        if not np.isnan(w).all():
            w = np.nan_to_num(w)
            if exclude is not None:
                w = np.where(exclude, 0.0, w)
            tradable = ~np.isnan(o[i])
            w = np.where(tradable, w, 0.0)
            tgt = w * eq
            tgt = np.where(tradable, tgt, notional)  # cannot trade a coin without a bar; keep (should be 0)
            tr = np.abs(tgt - notional)
            fee = tr * cost
            eq -= fee.sum()
            n_trades += int((tr > 1e-9).sum())
            # position bookkeeping
            for j in np.where((notional != 0) & ((tgt == 0) | (np.sign(tgt) != np.sign(notional))))[0]:
                positions.append((j, pos_open[j], i, pos_pnl[j])); pos_pnl[j] = 0; pos_open[j] = -1
            pos_pnl -= fee
            new = (tgt != 0) & (pos_open < 0)
            pos_open[new] = i
            notional = tgt
        live = notional != 0
        if live.any():
            r = np.where(live, c[i] / o[i] - 1, 0.0)
            r = np.nan_to_num(r)  # missing bar (delisted) -> flat; position closed at last price below
            f = -notional * fund[i]
            g = notional * r + np.where(live, f, 0.0)
            eq += g.sum(); pos_pnl += g
            notional = notional * (1 + r)
            dead = live & (np.isnan(c[i]) | ((i + 1 < T) & np.isnan(o[min(i + 1, T - 1)])))
            for j in np.where(dead)[0]:  # coin stops trading: close at last close
                eq -= abs(notional[j]) * cost; pos_pnl[j] -= abs(notional[j]) * cost
                positions.append((j, pos_open[j], i + 1, pos_pnl[j])); pos_pnl[j] = 0; pos_open[j] = -1
                notional[j] = 0
        curve.append((int(P.t[i] + DAY - 1), eq))
        if eq <= 0:
            break
    for j in np.where(notional != 0)[0]:
        positions.append((j, pos_open[j], i1, pos_pnl[j]))
    return {"curve": curve, "pos": positions, "trades": n_trades}


def stats(res):
    s = summary(res["curve"], [p[3] for p in res["pos"]])
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / v.std(ddof=1) * math.sqrt(len(v))), 2) if len(v) > 2 and v.std() > 0 else 0.0
    s["trades_per_month"] = round(res["trades"] / max(len(v), 1), 1)
    return s


def brief(s):
    return {k: s[k] for k in ("median_month", "geo_month", "mean_month", "months_up", "n_months", "max_dd_pct",
                              "t_stat", "trades_per_month", "net_pct", "worst_month") if k in s}


def boot_p5(vals, seed=7):
    rng = np.random.default_rng(seed)
    vals = np.asarray(vals); nb = math.ceil(len(vals) / 3)
    st = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def run_windows(make_W, cost_mult=1.0, exclude=None):
    W = make_W()
    out = {}
    for nm, a, b in (("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END), ("full", FULL_START, TEST_END)):
        out[nm] = simulate(W, a, b, cost_mult=cost_mult, exclude=exclude)
    return out


def checks(make_W, neighbours, random_Ws):
    """make_W(): weights of the selected config. neighbours: list of make_W callables. random_Ws: list of W arrays."""
    res = {}
    base = run_windows(make_W); dbl = run_windows(make_W, 2.0)
    S = {k: stats(v) for k, v in base.items()}; S2 = {k: stats(v) for k, v in dbl.items()}
    c1 = dict(dev=S["dev"]["net_pct"], test=S["test"]["net_pct"], dev_2x=S2["dev"]["net_pct"], test_2x=S2["test"]["net_pct"])
    res["1"] = {"pass": all(v > 0 for v in c1.values()), "numbers": c1}
    months = S["full"]["months"]
    yrs = {}
    for k, v in months.items():
        yrs.setdefault(k[:4], []).append(v)
    ycomp = {y: round(compound(v), 2) for y, v in yrs.items()}
    no21 = compound([v for k, v in months.items() if k >= "2022"])
    res["2"] = {"pass": sum(v > 0 for v in ycomp.values()) >= 4 and no21 > 0, "numbers": {"years": ycomp, "2022_on": round(no21, 2)}}
    vals = np.array(list(months.values()))
    t = float(vals.mean() / vals.std(ddof=1) * math.sqrt(len(vals)))
    p5 = boot_p5(vals)
    res["3"] = {"pass": t >= 2.0 and p5 > 0, "numbers": {"t": round(t, 2), "boot_p5": round(p5, 3), "n_months": len(vals)}}
    rest = sorted(vals)[:-3]
    res["4"] = {"pass": compound(rest) > 0, "numbers": {"net_without_best3": round(compound(rest), 2)}}
    nb_ok = []
    for mk in neighbours:
        r = run_windows(mk)
        nb_ok.append((stats(r["dev"])["net_pct"], stats(r["test"])["net_pct"]))
    share = float(np.mean([a > 0 and b > 0 for a, b in nb_ok])) if nb_ok else 0
    res["5"] = {"pass": share >= 0.7, "numbers": {"share_both_positive": round(share, 3), "n": len(nb_ok),
                                                  "neighbours_dev_test_net": [[round(a, 1), round(b, 1)] for a, b in nb_ok]}}
    full_net = S["full"]["net_pct"]
    rnd = [stats(simulate(Wr, FULL_START, TEST_END))["net_pct"] for Wr in random_Ws]
    beat = float(np.mean([full_net > x for x in rnd]))
    res["6"] = {"pass": beat >= 0.95, "numbers": {"beats_share": round(beat, 3), "n_random": len(rnd),
                                                  "random_median_net": round(float(np.median(rnd)), 1), "net": full_net}}
    contrib = {}
    for j, a, b, p in base["full"]["pos"]:
        contrib[j] = contrib.get(j, 0) + p
    top5 = [j for j, _ in sorted(contrib.items(), key=lambda x: -x[1])[:5]]
    ex = np.zeros(N, bool); ex[top5] = True
    W_ex = make_W(exclude=ex) if "exclude" in make_W.__code__.co_varnames else None
    if W_ex is None:
        r_ex = simulate(make_W(), FULL_START, TEST_END, exclude=ex)
    else:
        r_ex = simulate(W_ex, FULL_START, TEST_END)
    net_ex = stats(r_ex)["net_pct"]
    tot = sum(p[3] for p in base["full"]["pos"])
    maxpos = max(p[3] for p in base["full"]["pos"]) if base["full"]["pos"] else 0
    sh = maxpos / tot if tot > 0 else 9.99
    res["7"] = {"pass": net_ex > 0 and sh <= 0.10, "numbers": {"top5": [SYMS[j] for j in top5], "net_without_top5": net_ex,
                                                               "max_single_position_share": round(sh, 3)}}
    g = S["full"]["geo_month"]; m = S["full"]["mean_month"]
    res["8"] = {"pass": g > 0 and g >= 0.5 * m, "numbers": {"geo": g, "mean": m}}
    return res, S, S2, base


def worker_months():
    w1 = json.loads((PROJECT / "research/monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
    w2 = json.loads((PROJECT / "research/worker2/session/results.json").read_text())["primary"]["monthly_1x"]
    return w1, w2


def corr_with(months, other):
    ks = [k for k in months if k in other]
    a = np.array([months[k] for k in ks]); b = np.array([other[k] for k in ks])
    return round(float(np.corrcoef(a, b)[0, 1]), 3) if len(ks) > 3 else None


def portfolio_stats(series_list, keys):
    """Equal-weight monthly-rebalanced mix of monthly % series over keys."""
    v = np.array([[s[k] for s in series_list] for k in keys]).mean(1)
    eq = np.cumprod(1 + v / 100)
    dd = float(np.max(1 - eq / np.maximum.accumulate(np.r_[1.0, eq])[1:]))
    return {"median": round(float(np.median(v)), 2), "geo": round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
            "mean": round(float(v.mean()), 2), "up": int((v > 0).sum()), "n": len(v), "monthly_dd_pct": round(dd * 100, 1)}
