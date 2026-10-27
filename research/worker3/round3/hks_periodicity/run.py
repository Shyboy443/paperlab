"""The ONE pre-registered config (PREREG.md): B12 K40 k3 buf3 continuation, top-40, 1x. DEV/TEST/full, 8 checks,
W1/W2 correlation, 500 USDT. TEST touched once here."""
import json, math, time, warnings
from datetime import datetime, timezone
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np
import lib
from lib import run, tstat, DEV_START, DEV_END, TEST_START, TEST_END

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
MAIN = dict(B=12, K=40, k=3, buf=3.0)
t0 = time.time()


def cfg(start=DEV_START, end=TEST_END, **over):
    kw = dict(MAIN); kw.update(over); return run(start=start, end=end, **kw)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def stats(r):
    m = r["months"]; nm = len(m)
    return dict(median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"], up=f'{r["months_up"]}/{nm}', max_dd=r["max_dd_pct"],
                net=r["net_pct"], t=round(tstat(m), 2), trades_per_month=round(r["trades"] / nm, 1), worst=r["worst_month"], best=r["best_month"])


def mstats(vals):
    v = np.array(vals, float); eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    return dict(median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2), mean=round(float(v.mean()), 2),
                up=f"{int((v > 0).sum())}/{len(v)}", max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2))


res = dict(agent="w3r3_hks_periodicity", config=MAIN, checks={})
full = cfg(record=True)
dev, test = cfg(end=DEV_END), cfg(start=TEST_START, record=True)
dev2, test2, full2 = cfg(end=DEV_END, cost_mult=2), cfg(start=TEST_START, cost_mult=2), cfg(cost_mult=2)
s2501 = cfg(start=int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000), cost_mult=2)
gross_t = cfg(start=TEST_START, cost_mult=0)
months = full["months"]; assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res.update(dev_1x=stats(dev), test_1x=stats(test), full_1x=stats(full), dev_2x=stats(dev2), test_2x=stats(test2), full_2x=stats(full2),
           test_gross_geo=gross_t["geo_month"], net_2025_01_2026_10_2xcost=s2501["net_pct"], monthly_1x=months, test_months=test["months"])
res["ytd_2026"] = round(compound([v for k, v in test["months"].items() if k.startswith("2026")]), 2)
res["oct_2026"] = test["months"].get("2026-10")
print("dev", res["dev_1x"], "\ntest", res["test_1x"], "\nfull", res["full_1x"], "\ntest gross geo", gross_t["geo_month"], f"[{time.time()-t0:.0f}s]", flush=True)
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2x=dev2["net_pct"], test_net_2x=test2["net_pct"])
res["checks"]["1"] = dict(pass_=all(v > 0 for v in c1.values()), numbers=c1)
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex21 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
res["checks"]["2"] = dict(pass_=sum(v > 0 for v in years.values()) >= 4 and ex21 > 0, numbers=dict(years=years, net_ex2021=ex21))
ts = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
rng = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
st = rng.integers(0, len(vals) - 2, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st])
p5 = float(np.percentile(boot, 5))
res["checks"]["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(float(ts), 3), boot_p5=round(p5, 3)))
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
res["checks"]["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: months[k] for k in best3}, net_without=round(rest, 2)))
GRID = [("K", v) for v in (20, 30, 50, 60)] + [("k", v) for v in (2, 4, 5)] + [("buf", v) for v in (2.0, 2.5, 3.5, 4.0)] + [("B", v) for v in (6, 8)]
nbr = []
for name, v in GRID:
    d = cfg(end=DEV_END, **{name: v}); t = cfg(start=TEST_START, **{name: v})
    nbr.append(dict(param=name, value=v, dev_net=d["net_pct"], dev_geo=d["geo_month"], test_net=t["net_pct"], test_geo=t["geo_month"],
                    test_median=t["median_month"], both=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nbr[-1], flush=True)
fr = sum(x["both"] for x in nbr) / len(nbr)
res["checks"]["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), n=len(nbr), grid=nbr))
rn = np.array([cfg(rand_seed=1000 + s)["net_pct"] for s in range(200)], float)
pct = float((rn < full["net_pct"]).mean() * 100)
res["checks"]["6"] = dict(pass_=pct >= 95, numbers=dict(beats_pct=pct, rand_median=round(float(np.median(rn)), 2),
                                                    rand_p95=round(float(np.percentile(rn, 95)), 2), strat=full["net_pct"], n=200))
print("c6", res["checks"]["6"], f"[{time.time()-t0:.0f}s]", flush=True)
pos = full["_positions"]
cp = np.zeros(lib.N)
for d, b, j, side, pnl in pos:
    cp[j] += pnl
netp = full["_curve"][-1][1] - full["_curve"][0][1]
top5 = np.argsort(-cp)[:5]
r7 = cfg(exclude=tuple(int(j) for j in top5))
mx = max(p[4] for p in pos) / netp * 100 if netp > 0 else float("inf")
res["checks"]["7"] = dict(pass_=bool(r7["net_pct"] > 0 and netp > 0 and mx <= 10),
                          numbers=dict(top5={lib.syms[j]: round(float(cp[j] / netp * 100), 2) if netp > 0 else float(cp[j]) for j in top5},
                                       net_without_top5=r7["net_pct"], largest_position_share_pct=round(float(mx), 2)))
res["checks"]["8"] = dict(pass_=bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
                          numbers=dict(geo=full["geo_month"], mean=full["mean_month"]))
for c in res["checks"].values():
    c["pass"] = bool(c.pop("pass_"))
res["n_pass"] = sum(c["pass"] for c in res["checks"].values())
W1m = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2m = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
ks = [k for k in months if k in W1m and k in W2m]; kt = [k for k in ks if k >= "2024-11"]
cor = lambda a, b, kk: round(float(np.corrcoef([a[k] for k in kk], [b[k] for k in kk])[0, 1]), 3)
res["corr"] = dict(monthly_w1_full=cor(months, W1m, ks), monthly_w1_test=cor(months, W1m, kt), monthly_w2_full=cor(months, W2m, ks),
                   monthly_w2_test=cor(months, W2m, kt))
X = test["months"]; tk = [k for k in kt if k in X]
res["portfolio_test_monthly"] = dict(W1=mstats([W1m[k] for k in tk]), W1W2_5050=mstats([(W1m[k] + W2m[k]) / 2 for k in tk]),
                                     W1W2X_thirds=mstats([(W1m[k] + W2m[k] + X[k]) / 3 for k in tk]), X=mstats([X[k] for k in tk]))
tp = test["_positions"]; sy = [lib.syms[p[2]] for p in tp]
res["usdt500"] = dict(per_leg_usdt=round(500 * 0.5 / MAIN["k"], 1), trades_per_month_test=round(test["trades"] / len(test["months"]), 1),
                      share_btc_legs=round(sy.count("BTCUSDT") / len(tp) * 100, 2), share_eth_legs=round(sy.count("ETHUSDT") / len(tp) * 100, 2),
                      note="83 USDT per leg > 5 USDT min notional for alts; BTCUSDT min notional is 100 USDT, so BTC legs would need rounding up.")
print(json.dumps({k: res[k] for k in ("corr", "portfolio_test_monthly", "usdt500", "ytd_2026", "oct_2026", "n_pass")}, default=float), flush=True)
print({k: (v["pass"], v["numbers"] if k != "5" else v["numbers"]["frac"]) for k, v in res["checks"].items()}, flush=True)
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
