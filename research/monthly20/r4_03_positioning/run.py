"""r4_03 positioning: reproduces every number in PREREG.md / REPORT.md / results.json.

    python fetch.py 0/1        # data.binance.vision UM daily metrics -> data/monthly20_r4_positioning (cached)
    python explore_dev.py      # DEV-only grid -> dev_grid.json (selection)
    python run.py dev          # DEV numbers of the selected config only (before PREREG)
    python run.py              # the one TEST run + 8 robustness checks + leverage table -> results.json
"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning")
import signals as S, engine as G
from common import DEV_START, DEV_END, TEST_START, TEST_END

HERE = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning"
SEL = dict(fam="retail_contra", k=3, kL=5, kS=5, rb=7)
NEIGH = [dict(SEL, k=k) for k in (1, 2, 4, 5)] + [dict(SEL, kL=n, kS=n) for n in (3, 4, 6, 8)] + \
        [dict(SEL, rb=r) for r in (1, 3, 5, 10, 14)]
P, UM, M = S.load()
ALL_MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]


def weights(cfg, rand=None):
    sc = S.scores(P, M, cfg["fam"], cfg["k"])
    return S.xs_weights(P, UM, sc, cfg["kL"], cfg["kS"], cfg["rb"], DEV_START, rand=rand)


def run(cfg, start, end, W=None, **kw):
    if W is None:
        W = weights(cfg)
    W, reb = W
    return G.sim(P, W, start, end, rebal=reb, **kw)


def mvec(r):
    return np.array([r["months"].get(m, 0.0) for m in ALL_MONTHS])


def tstat(v):
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def boot5(v, n=5000, b=3, seed=7):
    rng = np.random.default_rng(seed); L = len(v); out = []
    for _ in range(n):
        st = rng.integers(0, L - b + 1, size=L // b + 1)
        s = np.concatenate([v[x:x + b] for x in st])[:L]
        out.append(s.mean())
    return float(np.percentile(out, 5))


def brief(r):
    v = np.array(list(r["months"].values()))
    keep = {k: r[k] for k in ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "net_pct",
                              "max_dd_pct", "n_months", "months_up", "trades", "liquidations", "wiped") if k in r}
    keep["t_stat"] = round(tstat(v), 2) if len(v) > 2 else None
    act = v[v != 0]
    keep["t_stat_active_months"] = round(tstat(act), 2) if len(act) > 2 else None
    keep["months"] = r["months"]
    return keep


def show(name, r):
    print(name, {k: v for k, v in brief(r).items() if k != "months"}, flush=True)


if __name__ == "__main__":
    Wsel = weights(SEL)
    dev = run(SEL, DEV_START, DEV_END, Wsel); dev2 = run(SEL, DEV_START, DEV_END, Wsel, cost_mult=2)
    show("DEV", dev); show("DEV 2x cost", dev2)
    if len(sys.argv) > 1 and sys.argv[1] == "dev":
        print(dev["months"])
        sys.exit()
    # ---------------- the one TEST run (after PREREG.md) ----------------
    test = run(SEL, TEST_START, TEST_END, Wsel); test2 = run(SEL, TEST_START, TEST_END, Wsel, cost_mult=2)
    full = run(SEL, DEV_START, TEST_END, Wsel, record=True); full2 = run(SEL, DEV_START, TEST_END, Wsel, cost_mult=2)
    v = mvec(full)
    show("TEST", test); show("TEST 2x cost", test2); show("FULL", full); show("FULL 2x cost", full2)
    checks = {}
    # 1 both halves
    checks[1] = dict(pass_=bool(dev["net_pct"] > 0 and test["net_pct"] > 0 and dev2["net_pct"] > 0 and test2["net_pct"] > 0),
                     dev=dev["net_pct"], test=test["net_pct"], dev_2x=dev2["net_pct"], test_2x=test2["net_pct"])
    # 2 most years
    yrs = {}
    for y in range(2021, 2027):
        yrs[y] = round((np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m.startswith(str(y))]) - 1) * 100, 2)
    ex21 = (np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m >= "2022-01"]) - 1) * 100
    checks[2] = dict(pass_=bool(sum(x > 0 for x in yrs.values()) >= 4 and ex21 > 0), years=yrs, from_2022=round(ex21, 2),
                     note="2021 is flat except December: the metrics archive starts 2021-12-01")
    # 3 statistics (70 calendar months, 2021-01..2021-11 count as 0%)
    t = tstat(v); b5 = boot5(v)
    checks[3] = dict(pass_=bool(t >= 2 and b5 > 0), t_stat_70m=round(t, 2), boot5_mean_month=round(b5, 3),
                     t_stat_active=round(tstat(v[v != 0]), 2))
    # 4 drop 3 best months
    srt = np.argsort(-v); w3 = np.delete(v, srt[:3])
    net3 = (np.prod(1 + w3 / 100) - 1) * 100
    checks[4] = dict(pass_=bool(net3 > 0), net_without_best3=round(net3, 2),
                     best3={ALL_MONTHS[i]: round(float(v[i]), 2) for i in srt[:3]})
    # 5 neighbours
    nb = []
    for cfg in NEIGH:
        Wn = weights(cfg)
        d = run(cfg, DEV_START, DEV_END, Wn)["net_pct"]; te = run(cfg, TEST_START, TEST_END, Wn)["net_pct"]
        nb.append(dict(cfg=cfg, dev=d, test=te, ok=bool(d > 0 and te > 0))); print("neighbour", nb[-1], flush=True)
    frac = sum(x["ok"] for x in nb) / len(nb)
    checks[5] = dict(pass_=bool(frac >= 0.7), share_ok=round(frac, 3), neighbours=nb)
    # 6 random picks (same 5 long / 5 short, same weekly dates, same eligible coins, same costs)
    rng = np.random.default_rng(42); rnd = []
    for k in range(300):
        rnd.append(run(SEL, DEV_START, TEST_END, weights(SEL, rand=rng))["net_pct"])
    beat = float(np.mean(np.array(rnd) < full["net_pct"]))
    checks[6] = dict(pass_=bool(beat >= 0.95), beats_share=round(beat, 3), random_median=round(float(np.median(rnd)), 2),
                     random_p95=round(float(np.percentile(rnd, 95)), 2), n_random=len(rnd))
    # 7 coins / single positions
    pos = full["_positions"]
    by_coin = {}
    for j, side, o, c, pnl in pos:
        by_coin[j] = by_coin.get(j, 0) + pnl
    top5 = sorted(by_coin, key=lambda j: -by_coin[j])[:5]
    ex = run(SEL, DEV_START, TEST_END, Wsel, exclude=top5)
    tot = sum(p[4] for p in pos); maxpos = max(p[4] for p in pos)
    checks[7] = dict(pass_=bool(ex["net_pct"] > 0 and maxpos / tot <= 0.10), top5=[P.syms[j] for j in top5],
                     net_without_top5=ex["net_pct"], max_single_position_share=round(maxpos / tot, 4),
                     total_profit_usd=round(tot, 2))
    # 8 compounds
    arith = float(v.mean()); geo = full["geo_month"]
    checks[8] = dict(pass_=bool(geo > 0 and geo >= 0.5 * arith), geo=geo, arith=round(arith, 3))
    npass = sum(c["pass_"] for c in checks.values())
    for k, c in checks.items():
        print(k, "PASS" if c["pass_"] else "FAIL", {a: b for a, b in c.items() if a not in ("neighbours",)}, flush=True)
    # leverage table
    lev = []
    for L in (1, 2, 3, 4, 5, 6, 7, 8, 10):
        f = run(SEL, DEV_START, TEST_END, Wsel, lev=L); te = run(SEL, TEST_START, TEST_END, Wsel, lev=L)
        fv = mvec(f)
        lev.append(dict(lev=L, full_mean=round(float(fv.mean()), 2), full_median=round(float(np.median(fv)), 2),
                        full_geo=f["geo_month"], full_dd=f["max_dd_pct"], full_worst=f["worst_month"], full_liq=f["liquidations"],
                        wiped=f["wiped"], test_mean=te["mean_month"], test_median=te["median_month"], test_geo=te["geo_month"],
                        test_dd=te["max_dd_pct"], test_worst=te["worst_month"], test_liq=te["liquidations"], test_wiped=te["wiped"]))
        print("lev", lev[-1], flush=True)
    need = next((x["lev"] for x in lev if x["full_mean"] >= 20), None)
    need_test = next((x["lev"] for x in lev if x["test_mean"] >= 20), None)
    ytd_m = {m: full["months"].get(m, 0) for m in ALL_MONTHS if m >= "2026-01"}
    ytd = (np.prod([1 + x / 100 for x in ytd_m.values()]) - 1) * 100
    out = {"agent": "r4_03_positioning",
           "idea": "Cross-sectional contrarian on Binance retail positioning: weekly, among the top-40 liquid USDT perps "
                   "(delisted included), long the 5 coins whose global account long/short ratio (3-day mean) is lowest "
                   "(retail most short) and short the 5 where it is highest.",
           "selected": SEL,
           "data_coverage": "UM metrics archive: BTC from 2020-09-01, every other coin from 2021-12-01; "
                            "count_long_short_ratio populated for the universe from 2022-02 (41% in 2022-01); "
                            "the strategy is flat (0%) 2021-01..2021-11.",
           "attempts": [{"name": "retail_contra_k3_5x5_w7", "prereg": True, "dev": brief(dev), "test": brief(test),
                         "test_double_cost": brief(test2), "dev_double_cost": brief(dev2), "full_2021_2026": brief(full),
                         "full_double_cost": brief(full2)}],
           "checks": {str(k): {("pass" if a == "pass_" else a): b for a, b in c.items()} for k, c in checks.items()},
           "checks_passed": npass, "robust": npass == 8,
           "monthly_1x": {m: round(float(x), 2) for m, x in zip(ALL_MONTHS, v)},
           "leverage_table": lev, "leverage_needed_for_20pct": need, "leverage_needed_for_20pct_test": need_test,
           "ytd_2026_pct_1x": round(float(ytd), 2), "ytd_2026_months": ytd_m,
           "best_honest": "retail_contra_k3_5x5_w7",
           "verdict": "PASS" if (test["mean_month"] >= 20 and test2["mean_month"] > 0 and not test["wiped"]) else "FAIL"}
    json.dump(out, open(HERE + "/results.json", "w"), indent=1, default=float)
    print("checks passed", npass, "/8; 2026 YTD", round(float(ytd), 2), "; lev for 20%:", need, need_test)
