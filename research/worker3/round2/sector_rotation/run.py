"""The ONE pre-registered config (PREREG.md). Runs DEV/TEST/full, the 8 checks (ROBUSTNESS.md), W1/W2 correlation,
portfolio mix and 500 USDT notes. TEST is touched once, here."""
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
from core import run_cfg, daily_returns, tstat, P, D, E, DEV_START, DEV_END, TEST_START, TEST_END

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
MAIN = json.loads((HERE / "selected.json").read_text())   # written from DEV before this script exists in final form
FULL = DEV_START
t0 = time.time()


def cfg(start=FULL, end=TEST_END, cost_mult=1.0, record=False, Pn=None, rng=None, **over):
    kw = dict(MAIN); kw.update(over)
    return run_cfg(kw["K"], kw["S"], kw["mode"], kw["univ"], kw["rb"], start=start, end=end, cost_mult=cost_mult,
                   record=record, Pn=Pn, rng=rng, gate_n=kw["gate_n"])


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def stats(r):
    m = r["months"]; nm = len(m)
    return dict(median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"], up=f'{r["months_up"]}/{nm}',
                max_dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(m), 2), trades_per_month=round(r["trades"] / nm, 1),
                orders_per_month=round(r["orders"] / nm, 1), worst=r["worst_month"], best=r["best_month"])


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    return dict(median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2))


res = dict(agent="w3r2_sector_rotation", config=MAIN, checks={})
full = cfg(record=True)
dev, test = cfg(start=DEV_START, end=DEV_END), cfg(start=TEST_START, end=TEST_END, record=True)
dev2, test2 = cfg(start=DEV_START, end=DEV_END, cost_mult=2), cfg(start=TEST_START, end=TEST_END, cost_mult=2)
full2 = cfg(cost_mult=2)
s2501 = cfg(start=int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000), end=TEST_END, cost_mult=2)
months = full["months"]
assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res.update(dev_1x=stats(dev), test_1x=stats(test), full_1x=stats(full), dev_2x=stats(dev2), test_2x=stats(test2),
           full_2x=stats(full2), net_2025_01_2026_10_2xcost=s2501["net_pct"], monthly_1x=months, test_months=test["months"])
res["ytd_2026"] = round(compound([v for k, v in test["months"].items() if k.startswith("2026")]), 2)
res["oct_2026"] = test["months"].get("2026-10")
print("dev", res["dev_1x"], "\ntest", res["test_1x"], "\nfull", res["full_1x"], f"[{time.time()-t0:.0f}s]", flush=True)

# sector picks in TEST
_, labs = core.sectors_for(P)
picks = {}
for i, (nl, ns, tops, bots) in sorted(test["_log"].items()):
    for s in tops:
        picks.setdefault("long_" + labs[s], 0); picks["long_" + labs[s]] += 1
    for s in bots:
        picks.setdefault("short_" + labs[s], 0); picks["short_" + labs[s]] += 1
res["test_sector_picks"] = picks
res["avg_positions_test"] = dict(longs=round(float(np.mean([v[0] for v in test["_log"].values()])), 1),
                                 shorts=round(float(np.mean([v[1] for v in test["_log"].values()])), 1))

# 1
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2x=dev2["net_pct"], test_net_2x=test2["net_pct"])
res["checks"]["1"] = dict(pass_=all(v > 0 for v in c1.values()), numbers=c1)
# 2
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex21 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
res["checks"]["2"] = dict(pass_=sum(v > 0 for v in years.values()) >= 4 and ex21 > 0, numbers=dict(years=years, net_ex2021=ex21))
# 3
ts = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
rng = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
st = rng.integers(0, len(vals) - 2, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st])
p5 = float(np.percentile(boot, 5))
res["checks"]["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(float(ts), 3), boot_p5=round(p5, 3)))
# 4
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
res["checks"]["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
# 5
GRID = json.loads((HERE / "neighbours.json").read_text())
nbr = []
for over in GRID:
    d = cfg(start=DEV_START, end=DEV_END, **over); t = cfg(start=TEST_START, end=TEST_END, **over)
    nbr.append(dict(over=over, dev_net=d["net_pct"], dev_geo=d["geo_month"], test_net=t["net_pct"], test_geo=t["geo_month"],
                    test_median=t["median_month"], both=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nbr[-1], flush=True)
fr = sum(x["both"] for x in nbr) / len(nbr)
res["checks"]["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), n=len(nbr), grid=nbr))
# 6: random coin picks, same counts per rebalance, same eligible pool, same gate/rebalance/costs
rn = []
for s in range(200):
    rn.append(cfg(rng=np.random.default_rng(1000 + s))["net_pct"])
rn = np.array(rn, float); pct = float((rn < full["net_pct"]).mean() * 100)
res["checks"]["6"] = dict(pass_=pct >= 95, numbers=dict(beats_pct=pct, rand_median=round(float(np.median(rn)), 2),
                                                        rand_p95=round(float(np.percentile(rn, 95)), 2), strat=full["net_pct"]))
print("c6", res["checks"]["6"], f"[{time.time()-t0:.0f}s]", flush=True)
# 7
pos = full["_positions"]
cp = np.zeros(len(P.syms))
for j, side, oi, ci, pnl in pos:
    cp[j] += pnl
top5 = np.argsort(-cp)[:5]
netp = full["_curve"][-1][1] - full["_curve"][0][1]
keep = np.setdiff1d(np.arange(len(P.syms)), top5)
P5 = E.Panel(D, keep)
r7 = cfg(Pn=P5)
mx = max(p[4] for p in pos) / netp * 100 if netp > 0 else float("inf")
res["checks"]["7"] = dict(pass_=bool(r7["net_pct"] > 0 and mx <= 10),
                          numbers=dict(top5={P.syms[j]: round(float(cp[j] / netp * 100), 2) for j in top5} if netp > 0 else
                                       {P.syms[j]: round(float(cp[j]), 0) for j in top5},
                                       net_without_top5=r7["net_pct"], largest_position_share_pct=round(float(mx), 2)))
# 8
res["checks"]["8"] = dict(pass_=bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
                          numbers=dict(geo=full["geo_month"], mean=full["mean_month"]))
for c in res["checks"].values():
    c["pass"] = bool(c.pop("pass_"))
res["n_pass"] = sum(c["pass"] for c in res["checks"].values())

# correlations with Worker 1 / Worker 2
W1m = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2m = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
ks = [k for k in months if k in W1m and k in W2m]
kt = [k for k in ks if k >= "2024-11"]
cor = lambda a, b, kk: round(float(np.corrcoef([a[k] for k in kk], [b[k] for k in kk])[0, 1]), 3)
W1d = np.load(R / "monthly20/r4_07_ensemble/_rbook_full_1x.npy")
tsd, dr = daily_returns(full["_curve"]); ii = ((tsd - P.t[0]) // 86400000).astype(int)
w = W1d[ii]; m = ~np.isnan(w)
mt = m & (tsd >= TEST_START)
res["corr"] = dict(monthly_w1_full=cor(months, W1m, ks), monthly_w1_test=cor(months, W1m, kt),
                   monthly_w2_full=cor(months, W2m, ks), monthly_w2_test=cor(months, W2m, kt),
                   daily_w1_full=round(float(np.corrcoef(dr[m], w[m])[0, 1]), 3),
                   daily_w1_test=round(float(np.corrcoef(dr[mt], w[mt])[0, 1]), 3))
tk = [k for k in kt if k in test["months"]]
X = test["months"]
res["portfolio_test_monthly"] = dict(W1=mstats([W1m[k] for k in tk]), W1W2_5050=mstats([(W1m[k] + W2m[k]) / 2 for k in tk]),
                                     W1W2X_thirds=mstats([(W1m[k] + W2m[k] + X[k]) / 3 for k in tk]), X=mstats([X[k] for k in tk]))
tpos = test["_positions"]
syms_t = [P.syms[p[0]] for p in tpos]
res["usdt500"] = dict(avg_longs=res["avg_positions_test"]["longs"], avg_shorts=res["avg_positions_test"]["shorts"],
                      share_btc=round(syms_t.count("BTCUSDT") / max(len(tpos), 1) * 100, 2),
                      share_eth=round(syms_t.count("ETHUSDT") / max(len(tpos), 1) * 100, 2))
print(json.dumps({k: res[k] for k in ("corr", "portfolio_test_monthly", "usdt500", "ytd_2026", "oct_2026", "n_pass",
                                      "test_sector_picks")}, default=float), flush=True)
print({k: (v["pass"], v["numbers"] if k != "5" else v["numbers"]["frac"]) for k, v in res["checks"].items()})
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
