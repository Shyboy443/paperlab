"""Step 3 + 4: the ONE holdout run (2018-01..2020-12, Binance SPOT) for ORIGINAL and the variant selected by learn.py,
the adapted 8 checks, holdout diagnostics, and the W1/W2/X portfolio (holdout + 2021-2026 context). Writes results.json."""
import json, math, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
import learn
from lib import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
t0 = time.time()
L = json.loads((HERE / "learn.json").read_text())
SEL = L["selected"]
H = lib.holdout_panel(); HP = lib.make_panel(H)
P = learn.P
NRAND = 300


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if v.std() > 0 else float("nan")
    return dict(n=len(v), median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2), t=round(t, 2), net=round(float((eq[-1] - 1) * 100), 2))


def checks(var, extra=None):
    kw = extra or {}
    r = lib.run_var(HP, var, HO_START, HO_END, record=True, **kw)
    r2 = lib.run_var(HP, var, HO_START, HO_END, cost_mult=2, **kw)
    months = r["months"]; vals = np.array(list(months.values()))
    out = dict(stats_1x=lib.stats(r), stats_2x=lib.stats(r2), monthly_1x=months, liquidations=r["liquidations"])
    act = [v for k, v in months.items() if v != 0.0]
    out["active_months_only"] = mstats(act) if len(act) > 2 else None
    out["years"] = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in (2018, 2019, 2020)}
    c = {}
    c["1"] = dict(pass_=r["net_pct"] > 0 and r2["net_pct"] > 0, numbers=dict(net_1x=r["net_pct"], net_2x=r2["net_pct"]))
    ex18 = round(compound([v for k, v in months.items() if k >= "2019-01"]), 2)
    c["2"] = dict(pass_=sum(v > 0 for v in out["years"].values()) >= 2 and ex18 > 0, numbers=dict(years=out["years"], net_2019_2020=ex18))
    ts = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
    g = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
    st = g.integers(0, len(vals) - 2, size=(5000, nb))
    boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st])
    p5 = float(np.percentile(boot, 5))
    c["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(float(ts), 3), boot_p5=round(p5, 3)))
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([v for k, v in months.items() if k not in best3])
    c["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
    GRID = [("L", v) for v in (15, 22, 38, 45)] + [("k", v) for v in (5, 8, 12, 15)] + \
           [("rebal", v) for v in (2, 3, 5, 7)] + [("univ", v) for v in (20, 30, 50, 60)]
    nbr = []
    for name, v in GRID:
        vv = dict(lib.VARIANTS[var]); kk = dict(kw)
        if name == "rebal":
            vv["rebal"] = v
        else:
            kk[name] = v
        x = lib.run_var(HP, vv, HO_START, HO_END, **kk)
        nbr.append(dict(param=name, value=v, net=x["net_pct"], geo=x["geo_month"], median=x["median_month"]))
    fr = sum(x["net"] > 0 for x in nbr) / len(nbr)
    c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), grid=nbr))
    v = lib.VARIANTS[var]
    U = lib.um(HP, 40, v.get("age", 0)); wt = lib.ivol(HP) if v.get("iv") else None
    prm = dict(k_long=10, k_short=10, rebal=v.get("rebal", 1), lev=1.0)
    blk = np.arange(HP.c.shape[0]) // 30
    rn, rb = [], []
    for s in range(NRAND):
        sc = np.random.default_rng(1000 + s).random(HP.c.shape)
        rn.append(lib.run(HP, U, sc, prm, HO_START, HO_END, wt=wt)["net_pct"])
        sb = np.random.default_rng(5000 + s).random((blk.max() + 1, HP.c.shape[1]))[blk]
        rb.append(lib.run(HP, U, sb, prm, HO_START, HO_END, wt=wt)["net_pct"])
    rn, rb = np.array(rn), np.array(rb)
    pa, pb = float((rn < r["net_pct"]).mean() * 100), float((rb < r["net_pct"]).mean() * 100)
    c["6"] = dict(pass_=pa >= 95 and pb >= 95, numbers=dict(daily_redraw_beats_pct=pa, block30_beats_pct=pb,
                  rand_daily_median=round(float(np.median(rn)), 2), rand_daily_p95=round(float(np.percentile(rn, 95)), 2),
                  rand_block_median=round(float(np.median(rb)), 2), rand_block_p95=round(float(np.percentile(rb, 95)), 2), strat=r["net_pct"]))
    pos = r["_positions"]; netp = r["_curve"][-1][1] - r["_curve"][0][1]
    cp = np.zeros(len(HP.syms))
    for p in pos:
        cp[p[0]] += p[4]
    top5 = np.argsort(-cp)[:5]
    P5 = lib.make_panel(H, np.setdiff1d(np.arange(len(HP.syms)), top5))
    r7 = lib.run_var(P5, var, HO_START, HO_END, **kw)
    mx = lib.max_pos_share(r)
    c["7"] = dict(pass_=bool(r7["net_pct"] > 0 and mx <= 10),
                  numbers=dict(top5_coins={HP.syms[j]: round(float(cp[j] / netp * 100), 2) for j in top5} if netp > 0 else None,
                               net_without_top5=r7["net_pct"], largest_position_share_pct=mx,
                               top5_positions=[(HP.syms[p[0]], "L" if p[1] > 0 else "S", round(p[4] / netp * 100, 2))
                                               for p in sorted(pos, key=lambda p: -p[4])[:5]] if netp > 0 else None))
    c["8"] = dict(pass_=bool(r["geo_month"] > 0 and r["geo_month"] >= 0.5 * r["mean_month"]),
                  numbers=dict(geo=r["geo_month"], mean=r["mean_month"]))
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    out["checks"] = c; out["n_pass"] = sum(x["pass"] for x in c.values())
    print(var, kw, out["stats_1x"], "2x", out["stats_2x"]["net"], "checks", {k: x["pass"] for k, x in c.items()},
          f"[{time.time()-t0:.0f}s]", flush=True)
    return out, months


res = dict(agent="w3r4_high52_v2", selected=SEL, holdout={}, devtest={})
HOm = {}
for var in ["ORIG"] + SEL:
    res["holdout"][var], HOm[var] = checks(var)
    d1, d2 = lib.run_var(P, var, DEV_START, DEV_END), lib.run_var(P, var, DEV_START, DEV_END, cost_mult=2)
    e1, e2 = lib.run_var(P, var, TEST_START, TEST_END), lib.run_var(P, var, TEST_START, TEST_END, cost_mult=2)
    f1 = lib.run_var(P, var, DEV_START, TEST_END)
    res["devtest"][var] = dict(dev_1x=lib.stats(d1), dev_2x=lib.stats(d2), test_1x=lib.stats(e1), test_2x=lib.stats(e2),
                               full_monthly_1x=f1["months"])
ctx, _ = checks("ORIG", extra=dict(k=5))
res["holdout_context_ORIG_k5"] = ctx
res["holdout_diagnostics_ORIG"] = learn.diagnostics(HP, {"HOLDOUT": (HO_START, HO_END)})

# Step 4: portfolio
W1h = json.loads((R / "worker1_backfill/results.json").read_text())["backfill_1x"]["monthly"]
W2h = json.loads((R / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]["1x"]["monthly"]
W1p = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2p = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
port = {}
for win, (A, B, src) in {"holdout_2018_2020": (W1h, W2h, HOm), "context_2021_2026": (W1p, W2p, {v: res["devtest"][v]["full_monthly_1x"] for v in ["ORIG"] + SEL})}.items():
    ks = sorted(k for k in A if k in B and all(k in s for s in src.values()))
    d = dict(months=f"{ks[0]}..{ks[-1]}", W1=mstats([A[k] for k in ks]), W2=mstats([B[k] for k in ks]),
             W1W2_5050=mstats([(A[k] + B[k]) / 2 for k in ks]))
    for v, X in src.items():
        d[f"X_{v}"] = mstats([X[k] for k in ks])
        d[f"W1W2X_{v}_thirds"] = mstats([(A[k] + B[k] + X[k]) / 3 for k in ks])
        d[f"corr_X_{v}"] = dict(W1=round(float(np.corrcoef([A[k] for k in ks], [X[k] for k in ks])[0, 1]), 3),
                                W2=round(float(np.corrcoef([B[k] for k in ks], [X[k] for k in ks])[0, 1]), 3))
    port[win] = d
res["portfolio"] = port
print(json.dumps(port, indent=1))
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
