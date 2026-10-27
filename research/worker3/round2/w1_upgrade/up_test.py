"""The ONE TEST run of the pre-registered PRIMARY (V4) and SECONDARY (V4+V3), the 8 checks (r4_07 procedure)."""
import json, math
from datetime import datetime, timezone
import numpy as np
import up
from up import *  # noqa
from common import utc

TEST_PERIOD_25 = utc(2025, 1, 1)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def boot_p5(vals, seed=7):
    rng = np.random.default_rng(seed)
    nb = math.ceil(len(vals) / 3)
    st = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def day(i):
    return datetime.fromtimestamp(int(D.day[min(i, D.T - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")


def corr(a, b):
    k = sorted(set(a) & set(b))
    return round(float(np.corrcoef([a[x] for x in k], [b[x] for x in k])[0, 1]), 3), len(k)


W2REF = json.loads((PROJECT / "research/worker2/session/results.json").read_text())["primary"]["monthly_1x"]


def evaluate(name, v, nbs):
    out = {"name": name, "variant": v, "checks": {}}
    full = run_variant(v, FULL_START, TEST_END, record=True)
    wp = full["wpath"]
    dev, test = run_variant(v, DEV_START, DEV_END), run_variant(v, TEST_START, TEST_END)
    dev2, test2 = run_variant(v, DEV_START, DEV_END, cost_mult=2), run_variant(v, TEST_START, TEST_END, cost_mult=2)
    full2 = run_variant(v, FULL_START, TEST_END, cost_mult=2)
    y25 = run_variant(v, TEST_PERIOD_25, TEST_END); y25_2 = run_variant(v, TEST_PERIOD_25, TEST_END, cost_mult=2)
    for nm, r in (("full", full), ("dev", dev), ("test", test), ("dev_2xcost", dev2), ("test_2xcost", test2),
                  ("full_2xcost", full2), ("y2025_26", y25), ("y2025_26_2xcost", y25_2)):
        out[nm] = brief(r)
        log(name, nm, out[nm])
    months = full["stats"]["months"]
    assert len(months) == 70, len(months)
    out["monthly_1x"] = months
    out["test_months_1x"] = test["stats"]["months"]
    vals = np.array(list(months.values()))
    c1 = dict(dev_net=dev["stats"]["net_pct"], test_net=test["stats"]["net_pct"], dev_net_2xcost=dev2["stats"]["net_pct"],
              test_net_2xcost=test2["stats"]["net_pct"])
    out["checks"]["1"] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
    years = {str(y): round(compound([x for k, x in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
    ex21 = round(compound([x for k, x in months.items() if k >= "2022-01"]), 2)
    out["checks"]["2"] = {"pass": sum(x > 0 for x in years.values()) >= 4 and ex21 > 0,
                          "numbers": dict(years=years, net_2022_2026=ex21)}
    t = float(vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))); p5 = boot_p5(vals)
    out["checks"]["3"] = {"pass": t >= 2 and p5 > 0, "numbers": dict(t_stat=round(t, 3), boot_p5_mean=round(p5, 3))}
    best3 = sorted(months, key=months.get, reverse=True)[:3]
    rest = compound([x for k, x in months.items() if k not in best3])
    out["checks"]["4"] = {"pass": rest > 0, "numbers": dict(best3={k: months[k] for k in best3}, net_without_best3=round(rest, 2))}
    rows = []
    for nm, nv in nbs:
        d_ = run_variant(nv, DEV_START, DEV_END); t_ = run_variant(nv, TEST_START, TEST_END)
        rows.append(dict(name=nm, dev_net=d_["stats"]["net_pct"], dev_geo=d_["stats"]["geo_month"],
                         test_net=t_["stats"]["net_pct"], test_geo=t_["stats"]["geo_month"],
                         test_median=t_["stats"]["median_month"], test_dd=t_["stats"]["max_dd_pct"],
                         both_positive=bool(d_["stats"]["net_pct"] > 0 and t_["stats"]["net_pct"] > 0)))
    frac = sum(r["both_positive"] for r in rows) / len(rows)
    out["checks"]["5"] = {"pass": frac >= 0.7, "numbers": dict(n=len(rows), frac=round(frac, 3), grid=rows)}
    log(name, "check5", frac)
    rnd = []
    for s in range(200):
        rng = np.random.default_rng(5000 + s)
        r = run_variant(v, FULL_START, TEST_END, rng=rng, wpath=wp)
        rnd.append(r["stats"]["net_pct"])
        if s % 50 == 0:
            log(name, "rand", s, rnd[-1])
    rnd = np.array(rnd)
    beat = float((rnd < full["stats"]["net_pct"]).mean() * 100)
    out["checks"]["6"] = {"pass": beat >= 95, "numbers": dict(n_random=200, strategy_net=full["stats"]["net_pct"],
                          random_median_net=round(float(np.median(rnd)), 2), random_p95_net=round(float(np.percentile(rnd, 95)), 2),
                          strategy_beats_pct=round(beat, 1))}
    ep = full["episodes"]
    by = {}
    for j, a, b, p in ep:
        by[D.syms[j]] = by.get(D.syms[j], 0.0) + p
    top5 = sorted(by, key=by.get, reverse=True)[:5]
    total = sum(p for *_, p in ep)
    big = max(ep, key=lambda x: x[3])
    r7 = run_variant(v, FULL_START, TEST_END, drop=tuple(top5))
    share = big[3] / total * 100 if total > 0 else float("inf")
    out["checks"]["7"] = {"pass": r7["stats"]["net_pct"] > 0 and share <= 10,
                          "numbers": dict(top5_coins={s: round(by[s], 0) for s in top5}, net_without_top5=r7["stats"]["net_pct"],
                                          geo_without_top5=r7["stats"]["geo_month"],
                                          biggest_position=dict(coin=D.syms[big[0]], open=day(big[1]), close=day(big[2]), pnl=round(big[3], 0)),
                                          biggest_position_share_pct=round(share, 2))}
    g, m = full["stats"]["geo_month"], full["stats"]["mean_month"]
    out["checks"]["8"] = {"pass": g > 0 and g >= 0.5 * m, "numbers": dict(geo=g, mean=m)}
    out["n_pass"] = sum(c["pass"] for c in out["checks"].values())
    log(name, "checks", {k: c["pass"] for k, c in out["checks"].items()})
    # extras
    W1m = json.loads((PROJECT / "research/monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
    tm = out["test_months_1x"]
    out["corr_test_monthly"] = dict(W1=corr(tm, {k: W1m[k] for k in tm}), W2=corr(tm, {k: W2REF[k] for k in tm if k in W2REF}))
    out["corr_full_monthly"] = dict(W1=corr(months, W1m), W2=corr(months, W2REF))
    ytd = {k: x for k, x in months.items() if k.startswith("2026")}
    out["ytd_2026"] = dict(months=ytd, compounded=round(compound(list(ytd.values())), 2))
    out["oct_2026"] = months.get("2026-10")
    # trades / month (TEST): W1 position changes approximated by episodes opened + W2 trades used
    nt = sum(1 for j, a, b, p in ep if a >= D.idx(TEST_START))
    out["positions_opened_per_month_test"] = round(nt / 24, 1)
    # 500 USDT: W2 orders = f*x*equity; W1 legs as in Worker 1
    if "w2" in v:
        fx = []
        i0 = full["i0"]
        tr = w2_trades(dict(W2P, **v["w2"].get("p", {})))
        pos = np.zeros((D.T, D.N))
        simulate(D, [make_leg(k) for k in "RMT"], TEST_START, TEST_END, wpath=wp, pos_out=pos)
        bd = {}
        for t in tr:
            if D.idx(TEST_START) <= t[0] < D.idx(TEST_END):
                bd.setdefault(t[0], []).append(t)
        for di, ts in bd.items():
            nv = pos[di]; G1 = np.abs(nv).sum()
            dG = sum(abs(nv[j] + s * x) - abs(nv[j]) for _, j, s, x, _ in ts)
            room = max(0.0, 1 - G1); f = 1.0 if dG <= room else room / dG
            fx += [f * x for *_, x, _ in ts]
        fx = np.array(fx)
        out["w2_test"] = dict(n=len(fx), mean_frac=round(float(fx.mean()), 4), share_zero=round(float((fx == 0).mean()), 3),
                              share_below_5usdt_at_500=round(float(((fx > 0) & (fx * 500 < 5)).mean()), 3),
                              median_usdt_at_500=round(float(np.median(fx[fx > 0]) * 500), 2))
        g1 = np.abs(pos[D.idx(TEST_START):D.idx(TEST_END)]).sum(1)
        out["w1_gross_test"] = dict(mean=round(float(g1.mean()), 3), p90=round(float(np.percentile(g1, 90)), 3))
    return out


def main():
    dv = json.loads((HERE / "dev.json").read_text())
    assert dv["primary"] == "V4" and dv["secondary"] == ["V4", "V3"]
    res = {"agent": "w3r2_w1_upgrade", "W1_test_ref": dict(median=3.84, geo=5.33, months_up=17, dd=23.27)}
    res["W1"] = {nm: brief(run_variant(V["W1"], a, b, cost_mult=c)) for nm, a, b, c in
                 (("dev", DEV_START, DEV_END, 1), ("test", TEST_START, TEST_END, 1), ("full", FULL_START, TEST_END, 1),
                  ("y2025_26_2xcost", TEST_PERIOD_25, TEST_END, 2))}
    res["primary"] = evaluate("V4", V["V4"], neighbours("V4"))
    sec = combine("V4", "V3")
    nbs = [(f"V4:{n}", dict(nv, scheme="IVAR")) for n, nv in neighbours("V4")] + \
          [(f"V3:{n}", dict(nv, w2={})) for n, nv in neighbours("V3")]
    res["secondary"] = evaluate("V4+V3", sec, nbs)
    # also (info only, not selected): V1/V2/V3 alone on TEST so nothing is hidden
    res["info_not_selected_test"] = {n: brief(run_variant(V[n], TEST_START, TEST_END)) for n in ("V1", "V2", "V3")}
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
    log("DONE")


if __name__ == "__main__":
    main()
