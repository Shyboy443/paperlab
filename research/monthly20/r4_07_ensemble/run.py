"""r4_07_ensemble: portfolio of the round-2 weak edges (rotation, ML, 60-day momentum, + optional BTC trend)
with causal weights and a causal volatility target (leverage cap 10x).

    python sanity.py      # each leg alone vs its round-2 monthly numbers
    python run.py dev     # DEV-only grid + selection (written to dev.json) -> then PREREG.md is written by hand
    python run.py test    # the ONE TEST run, the 8 checks, leverage / vol-target tables -> results.json
"""
import json, math, sys, time
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble")
from ens import *  # noqa
from common import utc

HERE = Path(__file__).resolve().parent
FULL_START = DEV_START
t0 = time.time()
D = Data()


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


LEGP = dict(R=dict(lb=14, k=10, univ=40), M=dict(k=3, seed=0), T=dict(look=60, univ=50), B=dict(look=60))


def make_leg(kind, p=None, drop=(), rng=None):
    p = dict(LEGP[kind], **(p or {}))
    if kind == "R":
        F = leg_rotation(D, p["lb"], p["k"], p["univ"], drop=drop, rng=rng)
    elif kind == "M":
        F = leg_ml(D, p["k"], p["seed"], drop=drop, rng=rng)
    elif kind == "T":
        F = leg_mom(D, p["look"], p["univ"], drop=drop, rng=rng)
    else:
        F = leg_btc(D, p["look"], rng=rng) if "BTCUSDT" not in drop else np.zeros((D.T, D.N))
    return (kind, F, trade_mask(D, F, kind))


def standalone(leg):
    """Daily returns of one leg alone at 1x over the full period, T-aligned (NaN where not run)."""
    r = simulate(D, [leg], FULL_START, TEST_END)
    out = np.full(D.T, np.nan)
    out[r["i0"]:r["i0"] + len(r["ret"])] = r["ret"]
    return out


def book(cfg, legs=None, start=FULL_START, end=TEST_END, lev=1.0, lpath=None, cost_mult=1.0, record=False, wpath=None):
    legs = legs or [make_leg(k, cfg.get("legp", {}).get(k)) for k in cfg["legs"]]
    if wpath is None:
        Rm = np.column_stack([standalone(l) for l in legs])
        wpath = weight_path(D, Rm, cfg["scheme"], cfg["wlook"], cfg["wevery"], start_i=D.idx(FULL_START))
    r = simulate(D, legs, start, end, wpath=wpath, lpath=lpath, lev=lev, cost_mult=cost_mult, record=record)
    r["stats"] = stats_of(r["curve"])
    r["wpath"] = wpath
    return r


def brief(r):
    s = r["stats"]
    return {k: s[k] for k in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct", "t_stat",
                              "months_up", "n_months")} | {"liq": r["liquidations"], "wiped": r["wiped"],
                                                           "lev_mean": r["lev_mean"], "lev_max": r["lev_max"]}


def months_dev(s):
    return {k: v for k, v in s["months"].items() if k <= "2024-10"}


def stage_dev():
    res = {"legs_dev": {}, "grid": []}
    legs = {k: make_leg(k) for k in "RMTB"}
    SR = {k: standalone(l) for k, l in legs.items()}
    i0, i1 = D.idx(DEV_START), D.idx(DEV_END)
    for k, l in legs.items():
        r = simulate(D, [l], DEV_START, DEV_END)
        res["legs_dev"][k] = brief(r | {"stats": stats_of(r["curve"])})
        log("leg", k, res["legs_dev"][k])
    X = np.column_stack([SR[k][i0:i1] for k in "RMTB"])
    res["corr_dev_daily"] = np.round(np.corrcoef(X.T), 3).tolist()
    log("corr dev (R,M,T,B)", res["corr_dev_daily"])
    for legset in ("RMT", "RMTB"):
        for scheme in ("EW", "IV", "ERC"):
            cfg = dict(legs=legset, scheme=scheme, wlook=60, wevery=7)
            Rm = np.column_stack([SR[k] for k in legset])
            wp = weight_path(D, Rm, scheme, 60, 7, start_i=D.idx(FULL_START))
            r = book(cfg, [legs[k] for k in legset], DEV_START, DEV_END, wpath=wp)
            row = dict(cfg=cfg, dev=brief(r), mean_w_dev=np.round(wp[i0:i1].mean(0), 3).tolist())
            res["grid"].append(row)
            log("grid", row)
    best = max(res["grid"], key=lambda x: x["dev"]["t_stat"])
    res["selected"] = best["cfg"]
    log("SELECTED", best)
    # DEV-only look at leverage: constant L and vol targets (informational, no selection)
    cfg = best["cfg"]
    lg = [legs[k] for k in cfg["legs"]]
    Rm = np.column_stack([SR[k] for k in cfg["legs"]])
    wp = weight_path(D, Rm, cfg["scheme"], cfg["wlook"], cfg["wevery"], start_i=D.idx(FULL_START))
    full1 = simulate(D, lg, FULL_START, TEST_END, wpath=wp)
    rb = np.full(D.T, np.nan); rb[full1["i0"]:full1["i0"] + len(full1["ret"])] = full1["ret"]
    np.save(HERE / "_rbook_full_1x.npy", rb)
    res["dev_leverage"] = []
    for L in (1, 2, 3, 5, 7.5, 10):
        r = book(cfg, lg, DEV_START, DEV_END, lev=L, wpath=wp)
        res["dev_leverage"].append(dict(kind="const", L=L, dev=brief(r))); log("devL", L, brief(r))
    for sig in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        lp = vol_target_path(D, rb, D.idx(FULL_START), sig)
        r = book(cfg, lg, DEV_START, DEV_END, lpath=lp, wpath=wp)
        res["dev_leverage"].append(dict(kind="voltarget", sigma=sig, dev=brief(r))); log("devVT", sig, brief(r))
    (HERE / "dev.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    {"dev": stage_dev}[sys.argv[1]]() if sys.argv[1] == "dev" else __import__("run_test").main()
