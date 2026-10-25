"""The ONE TEST run of the pre-registered ensemble (PREREG.md) + the 8 checks + leverage / vol-target tables."""
import json, math, sys, time
from datetime import datetime, timezone
import numpy as np
import run as RN
from run import D, book, brief, make_leg, standalone, log, HERE, FULL_START
from ens import *  # noqa

SEL = dict(legs="RMT", scheme="IV", wlook=60, wevery=7)
SIGMA_PRIMARY = 0.75


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def boot_p5(vals, seed=7):
    rng = np.random.default_rng(seed)
    nb = math.ceil(len(vals) / 3)
    st = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def day(i):
    return datetime.fromtimestamp(int(D.day[min(i, D.T - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")


def main():
    res = {"agent": "r4_07_ensemble", "idea": "inverse-vol portfolio of rotation + ML walk-forward + 60d momentum, "
           "then a causal vol target (cap 10x)", "selected": SEL, "checks": {}}
    legs = [make_leg(k) for k in SEL["legs"]]
    SR = np.column_stack([standalone(l) for l in legs])
    wp = weight_path(D, SR, SEL["scheme"], SEL["wlook"], SEL["wevery"], start_i=D.idx(FULL_START))
    run = lambda **kw: book(SEL, legs, wpath=wp, **kw)
    full = run(record=True)
    dev, test = run(start=DEV_START, end=DEV_END), run(start=TEST_START, end=TEST_END)
    dev2, test2 = run(start=DEV_START, end=DEV_END, cost_mult=2), run(start=TEST_START, end=TEST_END, cost_mult=2)
    full2 = run(cost_mult=2)
    for nm, r in (("full", full), ("dev", dev), ("test", test), ("dev_2xcost", dev2), ("test_2xcost", test2), ("full_2xcost", full2)):
        res[f"{nm}_1x"] = brief(r)
        log(nm, brief(r))
    months = full["stats"]["months"]
    assert len(months) == 70, len(months)
    res["monthly_1x"] = months
    res["test_months_1x"] = test["stats"]["months"]
    vals = np.array(list(months.values()))
    # legs alone + correlations (info)
    i0, i1 = D.idx(FULL_START), D.idx(TEST_END)
    res["legs_alone"] = {}
    for l in legs:
        for nm, a, b in (("full", FULL_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
            r = simulate(D, [l], a, b); r["stats"] = stats_of(r["curve"])
            res["legs_alone"][f"{l[0]}_{nm}"] = brief(r)
    res["corr_daily_full"] = np.round(np.corrcoef(SR[i0:i1].T), 3).tolist()
    res["mean_weights_full"] = np.round(wp[i0:i1].mean(0), 3).tolist()
    log("legs", res["legs_alone"])

    c1 = dict(dev_net=dev["stats"]["net_pct"], test_net=test["stats"]["net_pct"], dev_net_2xcost=dev2["stats"]["net_pct"],
              test_net_2xcost=test2["stats"]["net_pct"])
    res["checks"]["1"] = {"pass": all(v > 0 for v in c1.values()), "numbers": c1}
    years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
    ex21 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
    res["checks"]["2"] = {"pass": sum(v > 0 for v in years.values()) >= 4 and ex21 > 0,
                          "numbers": dict(years=years, positive_years=sum(v > 0 for v in years.values()), net_2022_2026=ex21)}
    t = float(vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))); p5 = boot_p5(vals)
    res["checks"]["3"] = {"pass": t >= 2 and p5 > 0, "numbers": dict(mean_month=round(float(vals.mean()), 3), t_stat=round(t, 3),
                                                                      boot_p5_mean=round(p5, 3))}
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([v for k, v in months.items() if k not in best3])
    res["checks"]["4"] = {"pass": rest > 0, "numbers": dict(best3={k: months[k] for k in best3}, net_without_best3=round(rest, 2),
                                                            net_all=full["stats"]["net_pct"])}
    log("checks 1-4", {k: res["checks"][k] for k in "1234"})

    # 5 neighbours
    NB = ([("wlook", v) for v in (30, 45, 75, 90)] + [("wevery", v) for v in (1, 3, 14, 30)] +
          [("scheme", v) for v in ("EW", "ERC")] + [("legs", "RMTB")] +
          [("R.lb", v) for v in (7, 10, 18, 21)] + [("M.k", v) for v in (2, 5)] + [("M.seed", 1)] +
          [("T.look", v) for v in (30, 45, 75, 90)])
    rows = []
    for name, v in NB:
        cfg = dict(SEL)
        if "." in name:
            leg, p = name.split(".")
            cfg["legp"] = {leg: {p: v}}
        else:
            cfg[name] = v
        lg = [make_leg(k, cfg.get("legp", {}).get(k)) for k in cfg["legs"]]
        Rm = np.column_stack([standalone(l) for l in lg])
        w = weight_path(D, Rm, cfg["scheme"], cfg["wlook"], cfg["wevery"], start_i=D.idx(FULL_START))
        d_ = book(cfg, lg, DEV_START, DEV_END, wpath=w); t_ = book(cfg, lg, TEST_START, TEST_END, wpath=w)
        rows.append(dict(param=name, value=v, dev_net=d_["stats"]["net_pct"], dev_mean=d_["stats"]["mean_month"],
                         dev_t=d_["stats"]["t_stat"], test_net=t_["stats"]["net_pct"], test_mean=t_["stats"]["mean_month"],
                         test_dd=t_["stats"]["max_dd_pct"], both_positive=bool(d_["stats"]["net_pct"] > 0 and t_["stats"]["net_pct"] > 0)))
        log("nb", rows[-1])
    frac = sum(r["both_positive"] for r in rows) / len(rows)
    res["checks"]["5"] = {"pass": frac >= 0.7, "numbers": dict(n=len(rows), n_both_positive=sum(r["both_positive"] for r in rows),
                                                               frac=round(frac, 3), grid=rows)}

    # 6 random versions
    rnd = []
    for s in range(200):
        rng = np.random.default_rng(5000 + s)
        lg = [make_leg(k, rng=rng) for k in SEL["legs"]]
        r = simulate(D, lg, FULL_START, TEST_END, wpath=wp)
        rnd.append(stats_of(r["curve"])["net_pct"])
        if s % 25 == 0:
            log("rand", s, rnd[-1])
    rnd = np.array(rnd)
    beat = float((rnd < full["stats"]["net_pct"]).mean() * 100)
    res["checks"]["6"] = {"pass": beat >= 95, "numbers": dict(n_random=len(rnd), strategy_net=full["stats"]["net_pct"],
                          random_median_net=round(float(np.median(rnd)), 2), random_p95_net=round(float(np.percentile(rnd, 95)), 2),
                          strategy_beats_pct=round(beat, 1))}
    log("check6", res["checks"]["6"])

    # 7 coins / positions
    ep = full["episodes"]
    by = {}
    for j, a, b, p in ep:
        by[D.syms[j]] = by.get(D.syms[j], 0.0) + p
    top5 = sorted(by, key=by.get, reverse=True)[:5]
    total = sum(p for *_, p in ep)
    big = max(ep, key=lambda x: x[3])
    lg = [make_leg(k, drop=tuple(top5)) for k in SEL["legs"]]
    Rm = np.column_stack([standalone(l) for l in lg])
    w = weight_path(D, Rm, SEL["scheme"], SEL["wlook"], SEL["wevery"], start_i=D.idx(FULL_START))
    r7 = book(SEL, lg, wpath=w)
    share = big[3] / total * 100 if total > 0 else float("inf")
    res["checks"]["7"] = {"pass": r7["stats"]["net_pct"] > 0 and share <= 10,
                          "numbers": dict(top5_coins={s: round(by[s], 0) for s in top5}, total_position_pnl=round(total, 0),
                                          full_net_profit=round(full["curve"][-1][1] - full["curve"][0][1], 0),
                                          net_without_top5=r7["stats"]["net_pct"], mean_month_without_top5=r7["stats"]["mean_month"],
                                          n_positions=len(ep), biggest_position=dict(coin=D.syms[big[0]], open=day(big[1]), close=day(big[2]),
                                                                                     pnl=round(big[3], 0)),
                                          biggest_position_share_pct=round(share, 2))}
    log("check7", res["checks"]["7"])
    g, m = full["stats"]["geo_month"], full["stats"]["mean_month"]
    res["checks"]["8"] = {"pass": g > 0 and g >= 0.5 * m, "numbers": dict(mean_month=m, geo_month=g, median_month=full["stats"]["median_month"],
                                                                          ratio=round(g / m, 3) if m else None)}
    res["robust"] = all(c["pass"] for c in res["checks"].values())
    res["n_pass"] = sum(c["pass"] for c in res["checks"].values())

    # 2026 YTD
    ytd = {k: v for k, v in months.items() if k.startswith("2026")}
    res["ytd_2026_1x"] = dict(months=ytd, compounded=round(compound(list(ytd.values())), 2))

    # leverage / vol target
    rb = np.full(D.T, np.nan); rb[full["i0"]:full["i0"] + len(full["ret"])] = full["ret"]
    lt = []
    for L in (1, 1.5, 2, 3, 5, 7.5, 10):
        row = dict(kind="const", L=L)
        for nm, a, b in (("full", FULL_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
            row[nm] = brief(run(start=a, end=b, lev=L))
        row["test_2xcost"] = brief(run(start=TEST_START, end=TEST_END, lev=L, cost_mult=2))
        lt.append(row); log("lev", L, row["full"], row["test"])
    for sig in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        lp = vol_target_path(D, rb, D.idx(FULL_START), sig)
        row = dict(kind="voltarget", sigma=sig, primary=sig == SIGMA_PRIMARY)
        for nm, a, b in (("full", FULL_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
            row[nm] = brief(run(start=a, end=b, lpath=lp))
        row["test_2xcost"] = brief(run(start=TEST_START, end=TEST_END, lpath=lp, cost_mult=2))
        lt.append(row); log("vt", sig, row["full"], row["test"])
    res["leverage_table"] = lt
    reach = [r for r in lt if r["test"]["mean_month"] >= 20 or r["full"]["mean_month"] >= 20]
    res["leverage_needed_for_20pct"] = (reach[0].get("L") or reach[0].get("sigma")) if reach else None
    res["max_mean_month_any_setting"] = dict(full=max(r["full"]["mean_month"] for r in lt), test=max(r["test"]["mean_month"] for r in lt))
    # round-format attempt list
    res["attempts"] = [dict(name="RMT_IV_1x", prereg=True, dev=res["dev_1x"], test=res["test_1x"], test_double_cost=res["test_2xcost_1x"]),
                       dict(name=f"RMT_IV_voltarget_{SIGMA_PRIMARY}", prereg=True,
                            dev=[r for r in lt if r.get("primary")][0]["dev"], test=[r for r in lt if r.get("primary")][0]["test"],
                            test_double_cost=[r for r in lt if r.get("primary")][0]["test_2xcost"])]
    res["best_honest"] = "RMT_IV_1x"
    res["verdict"] = "PASS" if any(r["test"]["mean_month"] >= 20 and not r["test"]["wiped"] for r in lt) else "FAIL"
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
    log("ROBUST", res["robust"], res["n_pass"], {k: v["pass"] for k, v in res["checks"].items()})


if __name__ == "__main__":
    main()
