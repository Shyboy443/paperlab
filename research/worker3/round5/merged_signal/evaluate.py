"""The ONE TEST run and the ONE HOLDOUT run of the pre-registered E1_rank_w_plus_H (PREREG.md), 8 checks on both
windows, portfolio with W1/W2, 500-USDT sizing. Writes results.json.   python evaluate.py
Research only: no orders, no keys."""
import json, math, sys, time
from itertools import product
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib, configs
from lib import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END, FULL_START

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
SEL = "E1_rank_w_plus_H"
CFG = configs.CAND[SEL]
NRAND = 200
t0 = time.time()


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if v.std() > 0 else float("nan")
    return dict(n=len(v), median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                max_dd_month_end=round(float((1 - eq / pk).max() * 100), 2), t=round(t, 2), net=round(float((eq[-1] - 1) * 100), 2))


def boot_p5(vals, seed=7):
    g = np.random.default_rng(seed); nb = math.ceil(len(vals) / 3)
    st = g.integers(0, len(vals) - 3 + 1, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def book(D, cfg=CFG, P=None, drop=(), rng_=None, wlook=60, wevery=7, scheme=None, wpath="auto"):
    legs, sch = configs.build(D, cfg, drop=drop, rng_=rng_, P=P)
    wp = lib.weights_for(D, legs, scheme or sch, wlook, wevery) if isinstance(wpath, str) else wpath
    return legs, wp


def run(D, legs, wp, a, b, cm=1.0, record=False):
    r = lib.run(D, legs, a, b, wpath=wp, cost_mult=cm, record=record)
    b_ = lib.brief(r)
    if record:
        b_["positions_per_month"] = round(len(r["episodes"]) / r["stats"]["n_months"], 1)
    return r, b_


NB = ([("L", v) for v in (15, 22, 38, 45)] + [("lb", v) for v in (7, 10, 18, 21)] + [("k", v) for v in (5, 8, 12, 15)] +
      [("w_mom", v) for v in (0.25, 0.75)] + [("wlook", v) for v in (30, 45, 75, 90)] + [("wevery", v) for v in (1, 3, 14, 30)] +
      [("scheme", v) for v in ("EW", "ERC")] + [("rank_daily", True)] + [("look", v) for v in (30, 90)])


def nb_book(D, name, v):
    cfg = dict(CFG); P = {}; kw = {}
    if name in ("wlook", "wevery", "scheme"):
        kw[name] = v
    elif name == "rank_daily":
        cfg = dict(CFG, legs=["RANKd", "M", "T", "H"])
    else:
        P[name] = v
    return book(D, cfg, P=P, **kw)


def size500(D, legs, wp, a, b):
    """Share of held coin-days whose net target notional at 500 USDT equity is below the exchange minimum
    (5 USDT; 100 for BTC, 20 for ETH on Binance USD-M)."""
    i0, i1 = D.idx(a), D.idx(b)
    frac = np.zeros((i1 - i0, D.N))
    for i, (_, F, _) in enumerate(legs):
        w = wp[i0:i1, i] if wp is not None else 1.0
        frac += (w[:, None] if wp is not None else 1.0) * F[i0:i1]
    mn = np.full(D.N, 5.0)
    for s, m in (("BTCUSDT", 100.0), ("ETHUSDT", 20.0)):
        if s in D.col:
            mn[D.col[s]] = m
    held = frac != 0
    notional = np.abs(frac) * 500
    small = held & (notional < mn[None, :])
    return dict(held_coin_days=int(held.sum()), below_min_pct=round(float(small.sum() / max(held.sum(), 1) * 100), 1),
                median_position_usdt=round(float(np.median(notional[held])), 2),
                mean_coins_held=round(float(held.sum(1).mean()), 1))


def checks_test(D):
    out = {}
    legs, wp = book(D)
    full, fb = run(D, legs, wp, FULL_START, TEST_END, record=True)
    full2, _ = run(D, legs, wp, FULL_START, TEST_END, 2.0)
    dev, db = run(D, legs, wp, DEV_START, DEV_END, record=True)
    dev2, db2 = run(D, legs, wp, DEV_START, DEV_END, 2.0)
    tst, tb = run(D, legs, wp, TEST_START, TEST_END, record=True)
    tst2, tb2 = run(D, legs, wp, TEST_START, TEST_END, 2.0)
    out.update(dev_1x=db, dev_2x=db2, test_1x=tb, test_2x=tb2, full_1x=fb, test_months_1x=tst["stats"]["months"],
               full_monthly_1x=full["stats"]["months"], full_monthly_2x=full2["stats"]["months"],
               mean_weights_test=np.round(wp[D.idx(TEST_START):D.idx(TEST_END)].mean(0), 3).tolist(),
               size_500usdt_test=size500(D, legs, wp, TEST_START, TEST_END))
    W1l = [configs.leg(D, n, {}) for n in "RMT"]
    w1wp = lib.weights_for(D, W1l, "IV")
    out["W1_size_500usdt_test"] = size500(D, W1l, w1wp, TEST_START, TEST_END)
    log("TEST", tb, "2x", tb2["net"], "DEV", db)
    months = full["stats"]["months"]; vals = np.array(list(months.values()))
    c = {}
    c1 = dict(dev_net=db["net"], test_net=tb["net"], dev_net_2x=db2["net"], test_net_2x=tb2["net"])
    c["1"] = dict(pass_=all(v > 0 for v in c1.values()), numbers=c1)
    years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
    ex21 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
    c["2"] = dict(pass_=sum(v > 0 for v in years.values()) >= 4 and ex21 > 0, numbers=dict(years=years, net_2022_2026=ex21))
    t = float(vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))); p5 = boot_p5(vals)
    c["3"] = dict(pass_=t >= 2 and p5 > 0, numbers=dict(t=round(t, 3), boot_p5=round(p5, 3), n_months=len(vals)))
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([v for k, v in months.items() if k not in best3])
    c["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
    rows = []
    for name, v in NB:
        l2, w2 = nb_book(D, name, v)
        _, d_ = run(D, l2, w2, DEV_START, DEV_END); _, t_ = run(D, l2, w2, TEST_START, TEST_END)
        rows.append(dict(param=name, value=v, dev_net=d_["net"], dev_geo=d_["geo"], test_net=t_["net"], test_geo=t_["geo"],
                         test_median=t_["median"], both=bool(d_["net"] > 0 and t_["net"] > 0)))
        log("nb", rows[-1])
    fr = sum(r["both"] for r in rows) / len(rows)
    c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), n=len(rows), grid=rows))
    rnd = []
    for s in range(NRAND):
        lr, _ = book(D, rng_=np.random.default_rng(5000 + s), wpath=None)
        rnd.append(lib.run(D, lr, FULL_START, TEST_END, wpath=wp)["stats"]["net_pct"])
        if s % 50 == 0:
            log("rand", s, rnd[-1])
    rnd = np.array(rnd); beat = float((rnd < fb["net"]).mean() * 100)
    c["6"] = dict(pass_=beat >= 95, numbers=dict(n=NRAND, strategy_net=fb["net"], rand_median=round(float(np.median(rnd)), 2),
                                                 rand_p95=round(float(np.percentile(rnd, 95)), 2), beats_pct=beat))
    c["7"] = check7(D, full, fb, FULL_START, TEST_END)
    g, m = full["stats"]["geo_month"], full["stats"]["mean_month"]
    c["8"] = dict(pass_=g > 0 and g >= 0.5 * m, numbers=dict(geo=g, mean=m))
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    out["checks"] = c; out["n_pass"] = sum(x["pass"] for x in c.values())
    log("checks TEST", {k: x["pass"] for k, x in c.items()})
    return out


def check7(D, full, fb, a, b):
    ep = full["episodes"]; by = {}
    for j, *_, p in ep:
        by[D.syms[j]] = by.get(D.syms[j], 0.0) + p
    top5 = sorted(by, key=by.get, reverse=True)[:5]
    total = sum(p for *_, p in ep); big = max(ep, key=lambda x: x[3])
    l7, w7 = book(D, drop=tuple(top5))
    _, r7 = run(D, l7, w7, a, b)
    share = big[3] / total * 100 if total > 0 else float("inf")
    return dict(pass_=r7["net"] > 0 and share <= 10,
                numbers=dict(top5_coins={s: round(by[s] / total * 100, 1) for s in top5}, net_without_top5=r7["net"],
                             geo_without_top5=r7["geo"], biggest_position=D.syms[big[0]], biggest_share_pct=round(share, 2)))


def checks_holdout(D):
    out = {}
    legs, wp = book(D)
    r, b1 = run(D, legs, wp, HO_START, HO_END, record=True)
    r2, b2 = run(D, legs, wp, HO_START, HO_END, 2.0)
    W1l = [configs.leg(D, n, {}) for n in "RMT"]
    _, w1b = run(D, W1l, lib.weights_for(D, W1l, "IV"), HO_START, HO_END)
    lH = [configs.leg(D, "H", {})]
    _, hb = run(D, lH, None, HO_START, HO_END)
    out.update(holdout_1x=b1, holdout_2x=b2, monthly_1x=r["stats"]["months"], monthly_2x=r2["stats"]["months"],
               W1_reproduction_on_this_engine=w1b, H_alone_on_W1_spot_panel=hb,
               mean_weights_by_year={y: np.round(wp[[i for i in range(D.idx(HO_START), D.idx(HO_END)) if D.mkey[i].startswith(y)]].mean(0), 3).tolist()
                                     for y in ("2018", "2019", "2020")},
               size_500usdt=size500(D, legs, wp, HO_START, HO_END))
    log("HOLDOUT", b1, "2x", b2["net"], "W1 repro", w1b["geo"], "H", hb["geo"])
    months = r["stats"]["months"]; vals = np.array(list(months.values()))
    years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in (2018, 2019, 2020)}
    out["years"] = years
    c = {}
    c["1"] = dict(pass_=b1["net"] > 0 and b2["net"] > 0, numbers=dict(net_1x=b1["net"], net_2x=b2["net"]))
    ex18 = round(compound([v for k, v in months.items() if k >= "2019-01"]), 2)
    c["2"] = dict(pass_=sum(v > 0 for v in years.values()) >= 2 and ex18 > 0, numbers=dict(years=years, net_2019_2020=ex18))
    t = float(vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))); p5 = boot_p5(vals)
    c["3"] = dict(pass_=t >= 2 and p5 > 0, numbers=dict(t=round(t, 3), boot_p5=round(p5, 3)))
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([v for k, v in months.items() if k not in best3])
    c["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
    rows = []
    for name, v in NB:
        l2, w2 = nb_book(D, name, v)
        _, h_ = run(D, l2, w2, HO_START, HO_END)
        rows.append(dict(param=name, value=v, net=h_["net"], geo=h_["geo"], median=h_["median"]))
        log("nb HO", rows[-1])
    fr = sum(x["net"] > 0 for x in rows) / len(rows)
    c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), n=len(rows), grid=rows))
    rnd = []
    for s in range(NRAND):
        lr, _ = book(D, rng_=np.random.default_rng(7000 + s), wpath=None)
        rnd.append(lib.run(D, lr, HO_START, HO_END, wpath=wp)["stats"]["net_pct"])
    rnd = np.array(rnd); beat = float((rnd < b1["net"]).mean() * 100)
    c["6"] = dict(pass_=beat >= 95, numbers=dict(n=NRAND, strategy_net=b1["net"], rand_median=round(float(np.median(rnd)), 2),
                                                 rand_p95=round(float(np.percentile(rnd, 95)), 2), beats_pct=beat))
    c["7"] = check7(D, r, b1, HO_START, HO_END)
    c["8"] = dict(pass_=b1["geo"] > 0 and b1["geo"] >= 0.5 * b1["mean"], numbers=dict(geo=b1["geo"], mean=b1["mean"]))
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    out["checks"] = c; out["n_pass"] = sum(x["pass"] for x in c.values())
    log("checks HOLDOUT", {k: x["pass"] for k, x in c.items()})
    return out


def portfolio(Xp, Xh):
    W1p = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
    W2p = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
    W1h = json.loads((R / "worker1_backfill/results.json").read_text())["backfill_1x"]["monthly"]
    W2h = json.loads((R / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]["1x"]["monthly"]
    wins = {"DEV": ("2021-01", "2024-10", W1p, W2p, Xp), "TEST": ("2024-11", "2026-10", W1p, W2p, Xp),
            "HOLDOUT": ("2018-01", "2020-12", W1h, W2h, Xh)}
    ser = {}
    for w, (a, b, A, B, X) in wins.items():
        ks = sorted(k for k in A if a <= k <= b and k in B and k in X)
        ser[w] = (ks, np.array([A[k] for k in ks]), np.array([B[k] for k in ks]), np.array([X[k] for k in ks]))
    ks, A, B, X = ser["DEV"]
    base_dd = mstats((A + B) / 2)["max_dd_month_end"]
    best = None
    for i, j in product(range(11), range(11)):
        if i + j > 10:
            continue
        w = np.array([i, j, 10 - i - j]) / 10
        s = mstats(w[0] * A + w[1] * B + w[2] * X)
        if s["max_dd_month_end"] <= base_dd and (best is None or s["geo"] > best[1]["geo"]):
            best = (w, s)
    wb = best[0]
    out = dict(best_dev_mix_weights_W1_W2_X=wb.tolist(), dev_5050_dd_limit=base_dd)
    for w, (ks, A, B, X) in ser.items():
        out[w] = dict(months=f"{ks[0]}..{ks[-1]} ({len(ks)})", W1=mstats(A), W2=mstats(B), X=mstats(X),
                      W1W2_5050=mstats((A + B) / 2), W1W2X_thirds=mstats((A + B + X) / 3),
                      W1W2X_best_dev_mix=mstats(wb[0] * A + wb[1] * B + wb[2] * X), W2X_5050=mstats((B + X) / 2),
                      corr_X_W1=round(float(np.corrcoef(A, X)[0, 1]), 3), corr_X_W2=round(float(np.corrcoef(B, X)[0, 1]), 3))
    return out


def main():
    res = dict(agent="w3r5_merged_signal", selected=SEL, config=CFG)
    Dp = lib.load("perp")
    res["test"] = checks_test(Dp)
    del Dp
    Ds = lib.load("spot")
    res["holdout"] = checks_holdout(Ds)
    res["portfolio"] = portfolio(res["test"]["full_monthly_1x"], res["holdout"]["monthly_1x"])
    log("portfolio", json.dumps(res["portfolio"], indent=1))
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
    log("done")


if __name__ == "__main__":
    main()
