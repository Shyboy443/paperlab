"""TEST (once) + the 8 'not luck' checks + leverage table for the pre-registered config (see PREREG.md).

    python dev.py   # DEV grid + selection (dev_grid.json)
    python run.py   # everything else (results.json)
"""
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
import engine_r4 as E
from factors import Factors
from dev import setup, tstat
from common import DEV_START, DEV_END, TEST_START, TEST_END

t0 = time.time()
D, P, F = setup()
BTC = D["c"][:, list(D["syms"]).index("BTCUSDT")]
MAIN = dict(f="combo", L=14, k=5, univ=40, rebal=7, neu="beta")
UM = {}
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "top3_share_of_profit", "wiped")


def um(n, PP=P, cache=UM):
    if n not in cache:
        cache[n] = E.universe_mask(PP, n)
    return cache[n]


def cfg(f="combo", L=14, k=5, univ=40, rebal=7, neu="beta", lev=1.0, start=DEV_START, end=TEST_END, cost_mult=1.0,
        record=False, PP=None, FF=None, ucache=None):
    PP = PP or P; FF = FF or F; ucache = UM if ucache is None else ucache
    beta = FF.beta_raw(L) if neu == "beta" else None
    return E.run(PP, um(univ, PP, ucache), FF.score(f, L), dict(k_long=k, k_short=k, rebal=rebal, lev=lev), start, end,
                 cost_mult=cost_mult, beta=beta, record=record)


def short(r):
    return {k: (r[k] if isinstance(r[k], (bool, type(None))) else float(r[k])) for k in KEYS if k in r}


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


res = {"agent": "r4_06_factors", "idea": "Beta-neutral long/short of a 'low-risk' combo factor (low vol, low beta, low idio vol, "
       "no lottery day, low skew) on the top-40 USDT perps incl. delisted coins, weekly", "config": MAIN, "checks": {}}

full = cfg(record=True)
dev, test = cfg(start=DEV_START, end=DEV_END), cfg(start=TEST_START, end=TEST_END)
dev2, test2 = cfg(start=DEV_START, end=DEV_END, cost_mult=2), cfg(start=TEST_START, end=TEST_END, cost_mult=2)
full2 = cfg(cost_mult=2)
months = full["months"]; assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res["monthly_1x"] = months
for nm, r in (("full", full), ("dev", dev), ("test", test), ("dev_2xcost", dev2), ("test_2xcost", test2), ("full_2xcost", full2)):
    res[nm + "_1x"] = short(r) | {"t_stat": round(tstat(r["months"]), 3)}
res["test_months_1x"] = test["months"]
res["attempts"] = [{"name": "combo14_k5_beta_u40", "prereg": True, "dev": dev, "test": test, "test_double_cost": test2}]
print("full", res["full_1x"], "\ndev", res["dev_1x"], "\ntest", res["test_1x"], "\ntest2x", res["test_2xcost_1x"],
      f"[{time.time()-t0:.0f}s]", flush=True)

# 1
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2xcost=dev2["net_pct"], test_net_2xcost=test2["net_pct"])
res["checks"]["1"] = {"pass": bool(all(v > 0 for v in c1.values())), "numbers": c1}
# 2
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex2021 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
c2 = dict(years=years, positive_years=int(sum(v > 0 for v in years.values())), net_2022_01_to_2026_10=ex2021)
res["checks"]["2"] = {"pass": bool(c2["positive_years"] >= 4 and ex2021 > 0), "numbers": c2}
# 3
ts = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
rng = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
starts = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in starts])
c3 = dict(mean_month=round(float(vals.mean()), 3), t_stat=round(float(ts), 3), boot_p5_mean=round(float(np.percentile(boot, 5)), 3))
res["checks"]["3"] = {"pass": bool(ts >= 2.0 and c3["boot_p5_mean"] > 0), "numbers": c3}
# 4
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": dict(best3={k: round(months[k], 2) for k in best3}, net_without_best3=round(rest, 2))}
print({k: res["checks"][k] for k in "1234"}, flush=True)

# 5
GRID = [("L", v) for v in (7, 10, 18, 21)] + [("k", v) for v in (3, 4, 6, 8)] + [("univ", v) for v in (20, 30, 50, 60)] + \
       [("rebal", v) for v in (1, 3, 5, 10, 14)]
base = dict(L=14, k=5, univ=40, rebal=7)
rows = []
for name, v in GRID:
    kw = dict(base); kw[name] = v
    d = cfg(**kw, start=DEV_START, end=DEV_END); t = cfg(**kw, start=TEST_START, end=TEST_END)
    rows.append(dict(param=name, value=v, dev_net=d["net_pct"], dev_mean=d["mean_month"], test_net=t["net_pct"],
                     test_mean=t["mean_month"], both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", rows[-1], flush=True)
nbp = sum(r["both_positive"] for r in rows)
res["checks"]["5"] = {"pass": bool(nbp / len(rows) >= 0.7), "numbers": dict(n=len(rows), n_both_positive=nbp, frac=round(nbp / len(rows), 3), grid=rows)}
dd_ = cfg(neu="dollar", start=DEV_START, end=DEV_END); td_ = cfg(neu="dollar", start=TEST_START, end=TEST_END)
res["info_dollar_neutral"] = dict(dev=short(dd_), test=short(td_))

# 6
combo = F.score("combo", 14); beta = F.beta_raw(14)
elig_nan = np.isnan(combo)
rand = []
for s in range(500):
    sc = np.random.default_rng(1000 + s).random(P.c.shape); sc[elig_nan] = np.nan
    r = E.run(P, um(40), sc, dict(k_long=5, k_short=5, rebal=7, lev=1.0), DEV_START, TEST_END, beta=beta)
    rand.append(r["net_pct"])
    if s % 100 == 0:
        print("rand", s, r["net_pct"], f"[{time.time()-t0:.0f}s]", flush=True)
rand = np.array(rand, float)
pct = float((rand < full["net_pct"]).mean() * 100)
res["checks"]["6"] = {"pass": bool(pct >= 95), "numbers": dict(n_random=500, strategy_net=full["net_pct"], random_median_net=round(float(np.median(rand)), 2),
                                                             random_p95_net=round(float(np.percentile(rand, 95)), 2), strategy_beats_pct=round(pct, 1))}
print("check6", res["checks"]["6"], flush=True)

# 7
pos = full["_positions"]
coin_pnl = np.zeros(len(P.syms))
for j, side, oi, ci, pnl, eq0 in pos:
    coin_pnl[j] += pnl
top5 = np.argsort(-coin_pnl)[:5]
net_profit = full["_curve"][-1][1] - full["_curve"][0][1]
keep = np.setdiff1d(np.arange(len(P.syms)), top5)
P5 = E.Panel(D, keep); F5 = Factors(P5, BTC)
r7 = cfg(PP=P5, FF=F5, ucache={})
best_pos = max(pos, key=lambda x: x[4])
max_share = float(best_pos[4] / net_profit * 100) if net_profit > 0 else None


def day(i):
    return datetime.fromtimestamp(int(P.t[min(i, len(P.t) - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")


c7 = dict(top5_coins={P.syms[j]: round(float(coin_pnl[j]), 2) for j in top5}, full_net_profit_usdt=round(float(net_profit), 2),
          net_without_top5_coins=r7["net_pct"], mean_month_without_top5=r7["mean_month"],
          largest_position=dict(coin=P.syms[best_pos[0]], side=int(best_pos[1]), opened=day(best_pos[2]), pnl=round(float(best_pos[4]), 2)),
          largest_position_share_pct=round(max_share, 2) if max_share is not None else None, n_positions=len(pos))
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and max_share is not None and max_share <= 10), "numbers": c7}
print("check7", c7, flush=True)
# 8
c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"])
res["checks"]["8"] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]), "numbers": c8}
res["n_pass"] = int(sum(c["pass"] for c in res["checks"].values()))
res["robust"] = res["n_pass"] == 8

# leverage
lt = []
for lev in (1, 1.5, 2, 3, 4, 5, 6, 8, 10):
    row = {"lev": lev}
    for nm, a, b in (("full", DEV_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
        row[nm] = short(cfg(lev=lev, start=a, end=b))
    lt.append(row); print("lev", lev, row["full"]["mean_month"], row["test"]["mean_month"], row["full"]["max_dd_pct"], row["full"]["wiped"], flush=True)
res["leverage_table"] = lt
need = [r["lev"] for r in lt if r["full"]["mean_month"] >= 20 and r["test"]["mean_month"] >= 20 and not r["full"]["wiped"]]
res["leverage_needed_for_20pct"] = need[0] if need else None
y26 = {k: v for k, v in months.items() if k.startswith("2026")}
res["ytd_2026_1x"] = dict(months=y26, compounded=round(compound(list(y26.values())), 2))
res["best_honest"] = "combo14_k5_beta_u40"
res["verdict"] = "PASS" if (test["mean_month"] >= 20 and test2["mean_month"] > 0 and not test["wiped"]) else "FAIL"
for a in res["attempts"]:
    for k in ("dev", "test", "test_double_cost"):
        a[k] = {kk: vv for kk, vv in a[k].items() if not kk.startswith("_")}
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print("n_pass", res["n_pass"], {k: v["pass"] for k, v in res["checks"].items()}, "ytd", res["ytd_2026_1x"], "need", res["leverage_needed_for_20pct"],
      f"[{time.time()-t0:.0f}s]")
