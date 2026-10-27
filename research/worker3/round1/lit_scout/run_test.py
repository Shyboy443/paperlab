"""The ONE TEST run of the pre-registered stablecoin timer (PREREG.md) + the 8 checks + W1/W2 comparison."""
import json, time
from stable import *  # noqa

t0 = time.time()
SEL = dict(L=30, thr=0.01, K=3, mode="LF", recon=7)
mk = make(**SEL)
nbrs = []
for L in (15, 22, 38, 45):
    nbrs.append(make(L, SEL["thr"], SEL["K"], "LF", 7))
for th in (0.005, 0.0075, 0.0125, 0.015):
    nbrs.append(make(SEL["L"], th, SEL["K"], "LF", 7))
for K in (2, 4, 5):
    nbrs.append(make(SEL["L"], SEL["thr"], K, "LF", 7))
for rc in (3, 14):
    nbrs.append(make(SEL["L"], SEL["thr"], SEL["K"], "LF", rc))

on = on_series(SEL["L"], SEL["thr"])
i0, i1 = idx(FULL_START), idx(TEST_END)
rng = np.random.default_rng(5)
n = i1 - i0
rand = []
for _ in range(200):
    sh = int(rng.integers(60, n - 60))
    o2 = on.copy(); o2[i0:i1] = np.roll(on[i0:i1], sh)
    rand.append(make(**SEL, on_override=o2)())

chk, S, S2, base = checks(mk, nbrs, rand)
npass = sum(v["pass"] for v in chk.values())
w1, w2 = worker_months()
months = S["full"]["months"]
tm = S["test"]["months"]
tkeys = sorted(tm)
res = {"agent": "lit_scout", "idea": "stablecoin float growth timer (long top-3 liquid perps while 30d float growth > 1%)",
       "selected": SEL, "windows_1x": {k: brief(v) for k, v in S.items()},
       "windows_2xcost": {k: brief(v) for k, v in S2.items()}, "checks": chk, "n_pass": npass,
       "monthly_1x": months, "test_months_1x": tm,
       "on_share": {"dev": on_fraction(SEL["L"], SEL["thr"], DEV_START, DEV_END),
                    "test": on_fraction(SEL["L"], SEL["thr"], TEST_START, TEST_END)},
       "corr_w1_full": corr_with(months, w1), "corr_w2_full": corr_with(months, w2),
       "corr_w1_test": corr_with(tm, w1), "corr_w2_test": corr_with(tm, w2),
       "ytd_2026": round(compound([v for k, v in months.items() if k.startswith("2026")]), 2),
       "oct_2026": months.get("2026-10")}
# 2025-01..2026-10 at double cost
r2 = stats(simulate(mk(), utc(2025, 1, 1), TEST_END, cost_mult=2.0))
res["y2025_26_2xcost"] = brief(r2)
# portfolio checks (TEST months, equal weight, monthly rebalanced)
ks = [k for k in tkeys if k in w1 and k in w2]
res["portfolio_test"] = {"W1": portfolio_stats([w1], ks), "W2": portfolio_stats([w2], ks), "X": portfolio_stats([tm], ks),
                         "W1+W2": portfolio_stats([w1, w2], ks), "W1+W2+X": portfolio_stats([w1, w2, tm], ks)}
# bar
t = S["test"]
res["bar"] = {"a_8of8": npass == 8, "b_geo_gt_5.33": t["geo_month"] > 5.33, "b_median_gt_3.84": t["median_month"] > 3.84,
              "c_up_ge_18": t["months_up"] >= 18, "c_dd_le_23": t["max_dd_pct"] <= 23,
              "d_2xcost_2025_26_pos": r2["net_pct"] > 0, "d_500usdt": "yes: 3 legs x ~167 USDT > 100 USDT BTC min notional"}
res["secs"] = round(time.time() - t0)
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(json.dumps({k: v for k, v in res.items() if k not in ("monthly_1x",)}, indent=1, default=float))
