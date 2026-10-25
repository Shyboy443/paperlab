"""Round-4 taker-flow agent: TEST run of the pre-registered config + the 8 "not luck" checks + leverage table + 2026 YTD.

    python data_tf.py      # once: panels with taker-buy volume from the local archive cache (incl. delisted coins)
    python explore_dev.py  # DEV-only exploration -> dev_explore.json
    python select_dev.py   # DEV-only selection + neighbour grid definition -> prereg.json (PREREG.md written from it)
    python run.py          # this file -> results.json
"""
import json, math, sys, time
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_02_takerflow")
import lib as Lb
from strat import build_W
from common import DEV_START, DEV_END, TEST_START, TEST_END

t0 = time.time()
PR = json.load(open("prereg.json"))
CFG = PR["selected"]
P = Lb.panel(CFG["tf"])
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "win_rate", "top3_share_of_profit", "wiped")


def short(r):
    return {k: r[k] for k in KEYS if k in r}


def run(W, start=DEV_START, end=TEST_END, **kw):
    return Lb.run_units(P, W, start, end, **kw)


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


def tstat(v):
    v = np.asarray(v); return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))


W = build_W(P, CFG)
res = {"agent": "r4_02_takerflow", "idea": PR["idea"], "selected": CFG, "checks": {}}
full = run(W, record=True)
dev, test = run(W, DEV_START, DEV_END), run(W, TEST_START, TEST_END)
dev2, test2 = run(W, DEV_START, DEV_END, cost_mult=2), run(W, TEST_START, TEST_END, cost_mult=2)
full2 = run(W, cost_mult=2)
months = full["months"]
vals = np.array(list(months.values()))
assert len(months) == 70, len(months)
res.update(full_1x=short(full), dev_1x=short(dev), test_1x=short(test), dev_1x_2xcost=short(dev2),
           test_1x_2xcost=short(test2), full_1x_2xcost=short(full2), monthly_1x=months, test_months_1x=test["months"],
           tstat_dev=tstat(list(dev["months"].values())), tstat_test=tstat(list(test["months"].values())),
           tstat_full=tstat(vals))
print("full", short(full), "\ndev", short(dev), "\ntest", short(test), "\ntest2x", short(test2), f"[{time.time()-t0:.0f}s]", flush=True)

# 1
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2xcost=dev2["net_pct"], test_net_2xcost=test2["net_pct"])
res["checks"]["1"] = {"pass": bool(all(v > 0 for v in c1.values())), "numbers": c1}
# 2
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex21 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
c2 = dict(years=years, positive_years=int(sum(v > 0 for v in years.values())), net_2022_01_to_2026_10=ex21)
res["checks"]["2"] = {"pass": bool(c2["positive_years"] >= 4 and ex21 > 0), "numbers": c2}
# 3
rng = np.random.default_rng(7)
nb = math.ceil(len(vals) / 3)
starts = rng.integers(0, len(vals) - 2, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in starts])
c3 = dict(mean_month=round(float(vals.mean()), 3), t_stat=round(tstat(vals), 3), boot_p5_mean=round(float(np.percentile(boot, 5)), 3))
res["checks"]["3"] = {"pass": bool(c3["t_stat"] >= 2.0 and c3["boot_p5_mean"] > 0), "numbers": c3}
# 4
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": dict(best3={k: months[k] for k in best3}, net_without_best3=round(rest, 2))}
print({k: res["checks"][k] for k in "1234"}, flush=True)

# 5 neighbour grid (pre-declared in prereg.json)
nbr = []
for ch in PR["neighbours"]:
    c = dict(CFG); c.update(ch)
    Pn = Lb.panel(c["tf"])
    Wn = build_W(Pn, c)
    d = Lb.run_units(Pn, Wn, DEV_START, DEV_END); t = Lb.run_units(Pn, Wn, TEST_START, TEST_END)
    nbr.append(dict(change=ch, dev_net=d["net_pct"], dev_mean=d["mean_month"], test_net=t["net_pct"], test_mean=t["mean_month"],
                    both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nbr[-1], flush=True)
nbp = sum(r["both_positive"] for r in nbr)
res["checks"]["5"] = {"pass": bool(nbp / len(nbr) >= 0.7), "numbers": dict(n=len(nbr), n_both_positive=nbp, frac=round(nbp / len(nbr), 3), grid=nbr)}

# 6 random coin picks with the same structure: at each rebalance, the same weights are dealt to random coins of that
# bar's universe (same count of longs/shorts, same gross, same times, same costs)
mask = Lb.universe_mask(P, CFG.get("U", 40))
reb = np.where(~np.isnan(W).all(1))[0]
i0, i1 = np.searchsorted(P["t"], DEV_START), np.searchsorted(P["t"], TEST_END)
reb = reb[(reb >= i0) & (reb < i1)]
rnet = []
rng = np.random.default_rng(11)
for k in range(200):
    Wr = np.full(W.shape, np.nan)
    for i in reb:
        el = np.where(mask[i])[0]
        w = W[i, el] if len(el) else np.array([])
        nz = np.nan_to_num(W[i]); vals_w = nz[nz != 0]
        row = np.zeros(W.shape[1])
        if len(vals_w) and len(el) >= len(vals_w):
            pick = rng.choice(el, len(vals_w), replace=False)
            row[pick] = vals_w
        Wr[i] = row
    rnet.append(Lb.run_units(P, Wr, DEV_START, TEST_END)["net_pct"])
    if k % 50 == 0:
        print("random", k, rnet[-1], f"[{time.time()-t0:.0f}s]", flush=True)
rnet = np.array(rnet)
pct = float((rnet < full["net_pct"]).mean() * 100)
res["checks"]["6"] = {"pass": bool(pct >= 95), "numbers": dict(strategy_net=full["net_pct"], random_median=float(np.median(rnet)),
                      random_p95=float(np.percentile(rnet, 95)), beats_pct=pct)}

# 7 top-5 profit coins removed; largest single position share
pos = full["_positions"]
by_coin = {}
for j, side, oi, ci, pnl in pos:
    by_coin[j] = by_coin.get(j, 0) + pnl
top5 = sorted(by_coin, key=by_coin.get, reverse=True)[:5]
m2 = mask.copy(); m2[:, top5] = False
W2 = build_W(P, CFG, mask=m2)
r7 = run(W2)
total = sum(p[4] for p in pos)
maxpos = max(p[4] for p in pos)
share = maxpos / total * 100 if total > 0 else None
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and share is not None and share <= 10),
                      "numbers": dict(top5=[str(P["syms"][j]) for j in top5], net_without_top5=r7["net_pct"],
                                      max_single_position_share_pct=share, n_positions=len(pos), total_pnl=total)}
# 8
geo, ar = full["geo_month"], full["mean_month"]
res["checks"]["8"] = {"pass": bool(geo > 0 and geo >= 0.5 * ar), "numbers": dict(geo_month=geo, mean_month=ar)}
res["n_pass"] = int(sum(v["pass"] for v in res["checks"].values()))
res["robust"] = res["n_pass"] == 8

# leverage table (full 2021..2026 and TEST), cap 10x
lt = []
for lev in (1, 1.5, 2, 3, 5, 7, 10):
    f = run(W, lev=lev); t = run(W, TEST_START, TEST_END, lev=lev)
    lt.append(dict(lev=lev, full=short(f), test=short(t)))
    print("lev", lev, f["mean_month"], f["geo_month"], f["max_dd_pct"], f["liquidations"], flush=True)
res["leverage_table"] = lt
need = next((r["lev"] for r in lt if r["full"]["mean_month"] >= 20 and not r["full"]["wiped"]), None)
res["leverage_needed_for_20pct"] = need
ytd = {k: v for k, v in months.items() if k.startswith("2026")}
res["ytd_2026_1x"] = dict(months=ytd, compounded=round(compound(ytd.values()), 2))
res["verdict"] = "PASS" if (test["mean_month"] >= 20) else "FAIL"
res["attempts"] = [{"name": "selected", "prereg": True, "dev": short(dev), "test": short(test), "test_double_cost": short(test2)}]
res["best_honest"] = "selected"
json.dump(res, open("results.json", "w"), indent=1, default=float)
print("n_pass", res["n_pass"], {k: v["pass"] for k, v in res["checks"].items()}, "ytd", res["ytd_2026_1x"]["compounded"],
      f"[{time.time()-t0:.0f}s]")
