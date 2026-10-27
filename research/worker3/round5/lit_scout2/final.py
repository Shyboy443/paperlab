"""The ONE TEST + ONE HOLDOUT run for the two pre-registered configs (PREREG.md). Writes results.json."""
import json, math, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib5 as L
from lib5 import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END, DAY

HERE = Path(__file__).resolve().parent; R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
t0 = time.time()
D, P, H, HP = L.panels()
NR = 200
SEL = {"TSD": dict(univ=20, tv=0.5, band=0.25), "CTR": dict(fs="p", Wk=104)}
NBR = {"TSD": [("scale", v) for v in (0.5, 0.75, 1.25, 1.5)] + [("tv", v) for v in (0.25, 0.375, 0.625, 0.75)] +
              [("univ", v) for v in (10, 15, 25, 30)] + [("rebal", v) for v in (2, 3, 5, 7)],
       "CTR": [("Wk", v) for v in (52, 78, 130, 156)] + [("k", v) for v in (5, 8, 12, 15)] +
              [("univ", v) for v in (20, 30, 50, 60)] + [("rebal", v) for v in (3, 5, 10, 14)]}


def run(idea, Pn, cfg, s, e, cm=1.0, rec=False):
    return (L.run_tsd if idea == "TSD" else L.run_ctr)(Pn, cfg, s, e, cost_mult=cm, record=rec)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def tsd_random(Pn, cfg, rs):
    """same daily sorted weight vector as the strategy, assigned to random eligible coins (rs = T x N priorities)."""
    W = L.tsd_weights(Pn, cfg); c = dict(L.TSD_DEFAULT); c.update(cfg)
    U = L.L4.um(Pn, c["univ"], c["age"]); okp = np.isfinite(L._shift(Pn.c))
    out = np.zeros_like(W)
    for i in range(W.shape[0]):
        w = np.sort(W[i][W[i] > 0])[::-1]
        if not len(w):
            continue
        el = np.where(U[i] & okp[i])[0]
        pick = el[np.argsort(-rs[i, el])][:len(w)]
        out[i, pick] = w[:len(pick)]
    return out


def random_nets(idea, Pn, cfg, s, e):
    T, N = Pn.c.shape; blk = np.arange(T) // 30
    rn, rb = [], []
    for k in range(NR):
        sa = np.random.default_rng(1000 + k).random((T, N))
        sb = np.random.default_rng(5000 + k).random((blk.max() + 1, N))[blk]
        for sc, lst in ((sa, rn), (sb, rb)):
            if idea == "TSD":
                c = dict(L.TSD_DEFAULT); c.update(cfg)
                lst.append(L.run_w(Pn, tsd_random(Pn, cfg, sc), s, e, band=c["band"], rebal=c["rebal"])["net_pct"])
            else:
                c = dict(L.CTR_DEFAULT); c.update(cfg)
                prm = dict(k_long=c["k"], k_short=c["k"], rebal=c["rebal"], lev=1.0)
                lst.append(L.L4.run(Pn, L.L4.um(Pn, c["univ"]), sc, prm, s, e)["net_pct"])
    return np.array(rn), np.array(rb)


def coin_pnl(idea, r, n):
    if idea == "TSD":
        return r["_coin_pnl"]
    cp = np.zeros(n)
    for p in r["_positions"]:
        cp[p[0]] += p[4]
    return cp


def boot_p5(vals):
    g = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
    st = g.integers(0, len(vals) - 2, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def checks(idea, cfg, Dsrc, Pn, windows, holdout):
    """holdout=False: ROBUSTNESS.md 70-month version (full = DEV_START..TEST_END); True: round-4 36-month version."""
    s, e = windows["full"]
    r = run(idea, Pn, cfg, s, e, rec=True); r2 = run(idea, Pn, cfg, s, e, cm=2)
    months = r["months"]; vals = np.array(list(months.values()))
    c = {}
    if holdout:
        c["1"] = dict(pass_=r["net_pct"] > 0 and r2["net_pct"] > 0, numbers=dict(net_1x=r["net_pct"], net_2x=r2["net_pct"]))
        yrs = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in (2018, 2019, 2020)}
        ex = round(compound([v for k, v in months.items() if k >= "2019-01"]), 2)
        c["2"] = dict(pass_=sum(v > 0 for v in yrs.values()) >= 2 and ex > 0, numbers=dict(years=yrs, net_2019_2020=ex))
    else:
        dv1, dv2 = run(idea, Pn, cfg, *windows["DEV"]), run(idea, Pn, cfg, *windows["DEV"], cm=2)
        te1, te2 = run(idea, Pn, cfg, *windows["TEST"]), run(idea, Pn, cfg, *windows["TEST"], cm=2)
        nn = dict(dev_1x=dv1["net_pct"], dev_2x=dv2["net_pct"], test_1x=te1["net_pct"], test_2x=te2["net_pct"])
        c["1"] = dict(pass_=all(v > 0 for v in nn.values()), numbers=nn)
        yrs = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
        ex = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
        c["2"] = dict(pass_=sum(v > 0 for v in yrs.values()) >= 4 and ex > 0, numbers=dict(years=yrs, net_2022_2026=ex))
    ts = L.tstat(months); p5 = boot_p5(vals)
    c["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(ts, 3), boot_p5=round(p5, 3)))
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([v for k, v in months.items() if k not in best3])
    c["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
    nb = []
    for name, v in NBR[idea]:
        cc = dict(cfg); cc[name] = v
        if holdout:
            x = run(idea, Pn, cc, s, e)
            nb.append(dict(param=name, value=v, net=x["net_pct"], ok=x["net_pct"] > 0))
        else:
            a, b = run(idea, Pn, cc, *windows["DEV"]), run(idea, Pn, cc, *windows["TEST"])
            nb.append(dict(param=name, value=v, dev_net=a["net_pct"], test_net=b["net_pct"],
                           ok=a["net_pct"] > 0 and b["net_pct"] > 0))
    fr = sum(x["ok"] for x in nb) / len(nb)
    c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), grid=nb))
    rn, rb = random_nets(idea, Pn, cfg, s, e)
    pa, pb = float((rn < r["net_pct"]).mean() * 100), float((rb < r["net_pct"]).mean() * 100)
    c["6"] = dict(pass_=pa >= 95 and pb >= 95, numbers=dict(daily_redraw_beats_pct=pa, block30_beats_pct=pb,
                  rand_daily_median=round(float(np.median(rn)), 2), rand_daily_p95=round(float(np.percentile(rn, 95)), 2),
                  rand_block_median=round(float(np.median(rb)), 2), rand_block_p95=round(float(np.percentile(rb, 95)), 2),
                  strat=r["net_pct"]))
    netp = r["_curve"][-1][1] - r["_curve"][0][1]
    cp = coin_pnl(idea, r, len(Pn.syms)); top5 = np.argsort(-cp)[:5]
    P5 = L.L4.make_panel(Dsrc, np.setdiff1d(np.arange(len(Pn.syms)), top5))
    r7 = run(idea, P5, cfg, s, e)
    mx = round(max(p[4] for p in r["_positions"]) / netp * 100, 2) if netp > 0 else float("nan")
    c["7"] = dict(pass_=bool(r7["net_pct"] > 0 and mx <= 10),
                  numbers=dict(top5_coins={Pn.syms[j]: round(float(cp[j]), 0) for j in top5}, net_without_top5=r7["net_pct"],
                               largest_position_share_pct=mx))
    c["8"] = dict(pass_=bool(r["geo_month"] > 0 and r["geo_month"] >= 0.5 * r["mean_month"]),
                  numbers=dict(geo=r["geo_month"], mean=r["mean_month"]))
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    print(idea, "holdout" if holdout else "perp", {k: x["pass"] for k, x in c.items()}, f"[{time.time()-t0:.0f}s]", flush=True)
    return dict(checks=c, n_pass=sum(x["pass"] for x in c.values()), full_stats_1x=L.stats(r), monthly_1x=months)


W1p = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2p = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
W1h = json.loads((R / "worker1_backfill/results.json").read_text())["backfill_1x"]["monthly"]
W2h = json.loads((R / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]["1x"]["monthly"]


def port(X, A, B, ks):
    return {k: [(A[m] + B[m]) / 2 for m in ks] for k in ["_"]}["_"]


res = {"selected": SEL}
for idea, cfg in SEL.items():
    out = {}
    for wn, (s, e) in {"DEV": (DEV_START, DEV_END), "TEST": (TEST_START, TEST_END)}.items():
        a, b = run(idea, P, cfg, s, e, rec=True), run(idea, P, cfg, s, e, cm=2)
        out[wn] = dict(x1=L.stats(a), x2=L.stats(b), monthly=a["months"])
    a, b = run(idea, HP, cfg, HO_START, HO_END, rec=True), run(idea, HP, cfg, HO_START, HO_END, cm=2)
    out["HOLDOUT"] = dict(x1=L.stats(a), x2=L.stats(b), monthly=a["months"])
    for wn in out:
        print(idea, wn, out[wn]["x1"], "| 2x net", out[wn]["x2"]["net"], flush=True)
    out["checks_perp_2021_2026"] = checks(idea, cfg, D, P, {"full": (DEV_START, TEST_END), "DEV": (DEV_START, DEV_END),
                                                          "TEST": (TEST_START, TEST_END)}, False)
    out["checks_holdout"] = checks(idea, cfg, H, HP, {"full": (HO_START, HO_END)}, True)
    # portfolio
    Xp = out["checks_perp_2021_2026"]["monthly_1x"]; Xh = out["HOLDOUT"]["monthly"]
    kdev = sorted(k for k in W1p if k <= "2024-10" and k in W2p and k in Xp)
    ktest = sorted(k for k in W1p if k >= "2024-11" and k in W2p and k in Xp)
    kho = sorted(k for k in W1h if k in W2h and k in Xh)
    mix = lambda A, B, X, ks, x: [(1 - x) / 2 * (A[k] + B[k]) + x * X[k] for k in ks]
    xs = [round(0.1 * i, 1) for i in range(6)]
    devgeo = {x: L.mstats(mix(W1p, W2p, Xp, kdev, x))["geo"] for x in xs}
    xbest = max(xs, key=lambda x: (devgeo[x], -x))
    pt = {"dev_geo_by_x": devgeo, "x_dev_chosen": xbest}
    for wn, (A, B, ks) in {"DEV": (W1p, W2p, kdev), "TEST": (W1p, W2p, ktest), "HOLDOUT": (W1h, W2h, kho)}.items():
        X = Xp if wn != "HOLDOUT" else Xh
        pt[wn] = dict(months=f"{ks[0]}..{ks[-1]}", W1=L.mstats([A[k] for k in ks]), W2=L.mstats([B[k] for k in ks]),
                      X=L.mstats([X[k] for k in ks]), W1W2_5050=L.mstats(mix(A, B, X, ks, 0.0)),
                      W1W2X_thirds=L.mstats(mix(A, B, X, ks, 1 / 3)), W1W2X_devmix=L.mstats(mix(A, B, X, ks, xbest)),
                      corr=dict(W1=round(float(np.corrcoef([A[k] for k in ks], [X[k] for k in ks])[0, 1]), 3),
                                W2=round(float(np.corrcoef([B[k] for k in ks], [X[k] for k in ks])[0, 1]), 3)))
    out["portfolio"] = pt
    print(idea, json.dumps(pt, indent=1), flush=True)
    res[idea] = out
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
