"""r4_01_reversal: pre-registered TEST run + the 8 "not luck" checks for the selected short-term reversal book.

    python data_r4.py   # once: hourly survivorship-free panel (local archive cache only)
    python dev.py       # DEV grid + selection -> dev_grid.json
    python run.py       # TEST (once) + checks -> results.json
The selected config is read from dev_grid.json (the PREREG.md config); nothing here is tuned on TEST.
"""
import json, math, sys, time
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, '.')
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
from data_r4 import build
from engine_r4 import HPanel, run
from scores import Scores
from common import DEV_START, DEV_END, TEST_START, TEST_END, DAY
import engine_r2 as E2
from data import build as build_daily

t0 = time.time()
X = build()
P = HPanel(X); S = Scores(P)
sel = json.load(open("dev_grid.json"))["selected"]
MAIN = dict(kind=sel["kind"], L=sel["L"], R=sel["R"], band=sel["band"], k=10, univ=40)
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "top3_share_of_profit", "turnover_per_day", "cost_drag_month_pct",
        "max_cap_ratio", "cap_breaches_pct", "wiped")


def short(r):
    return {k: (r[k] if isinstance(r[k], (bool, type(None))) else float(r[k])) for k in KEYS if k in r}


def cfg_run(kind=None, L=None, R=None, band=None, k=None, univ=None, start=DEV_START, end=TEST_END, cost_mult=1.0,
            lev=1.0, record=False, umask=None, score=None):
    c = dict(MAIN)
    for n, v in (("kind", kind), ("L", L), ("R", R), ("band", band), ("k", k), ("univ", univ)):
        if v is not None:
            c[n] = v
    sc = S.get(c["kind"], c["L"]) if score is None else score
    return run(P, sc, c["k"], c["R"], c["univ"], start, end, lev=lev, cost_mult=cost_mult, record=record,
               umask=umask, band=c["band"])


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


def tstat(v):
    v = np.asarray(v, float)
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))


res = {"agent": "r4_01_reversal",
       "idea": "Short-term cross-sectional reversal, market-neutral: long the 10 coins with the lowest score, short the 10 "
               "with the highest, among the top-40 USDT perps by trailing 30-day quote volume (delisted coins included), "
               "equal weight, 1x gross, hourly data",
       "selected": MAIN, "checks": {}, "attempts": []}

full = cfg_run(record=True)
dev, test = cfg_run(start=DEV_START, end=DEV_END), cfg_run(start=TEST_START, end=TEST_END)
dev2, test2 = cfg_run(start=DEV_START, end=DEV_END, cost_mult=2), cfg_run(start=TEST_START, end=TEST_END, cost_mult=2)
devg, testg = cfg_run(start=DEV_START, end=DEV_END, cost_mult=0), cfg_run(start=TEST_START, end=TEST_END, cost_mult=0)
fullg = cfg_run(cost_mult=0)
months = full["months"]
assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res["monthly_1x"] = months
res["full_1x"], res["dev_1x"], res["test_1x"] = short(full), short(dev), short(test)
res["dev_1x_2xcost"], res["test_1x_2xcost"] = short(dev2), short(test2)
res["gross_no_costs"] = {"dev": short(devg), "test": short(testg), "full": short(fullg)}
res["test_months_1x"] = test["months"]
for nm, r in (("full", full), ("dev", dev), ("test", test)):
    res[nm + "_1x"]["t_stat"] = round(tstat(list(r["months"].values())), 3)
res["attempts"].append({"name": "PREREG " + json.dumps(MAIN), "prereg": True, "dev": short(dev), "test": short(test),
                        "test_double_cost": short(test2)})
print("full", short(full), "\ndev", short(dev), "\ntest", short(test), "\ngross test", short(testg), f"[{time.time()-t0:.0f}s]", flush=True)

# 1. both halves
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2xcost=dev2["net_pct"], test_net_2xcost=test2["net_pct"])
res["checks"]["1"] = {"pass": bool(all(v > 0 for v in c1.values())), "numbers": c1}
# 2. most years
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex2021 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
c2 = dict(years=years, positive_years=int(sum(v > 0 for v in years.values())), net_2022_01_to_2026_10=ex2021)
res["checks"]["2"] = {"pass": bool(c2["positive_years"] >= 4 and ex2021 > 0), "numbers": c2}
# 3. statistically real
rng = np.random.default_rng(7)
nb = math.ceil(len(vals) / 3)
starts = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in starts])
c3 = dict(mean_month=round(float(vals.mean()), 3), sd_month=round(float(vals.std(ddof=1)), 3), t_stat=round(tstat(vals), 3),
          boot_p5_mean=round(float(np.percentile(boot, 5)), 3), boot_p50_mean=round(float(np.percentile(boot, 50)), 3))
res["checks"]["3"] = {"pass": bool(c3["t_stat"] >= 2.0 and c3["boot_p5_mean"] > 0), "numbers": c3}
# 4. not a few months
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
c4 = dict(best3={k: round(months[k], 2) for k in best3}, net_without_best3=round(rest, 2), net_all=full["net_pct"])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": c4}
print("checks 1-4", {k: res["checks"][k]["pass"] for k in "1234"}, f"[{time.time()-t0:.0f}s]", flush=True)

# 5. neighbour grid (pre-declared in PREREG.md)
L0, R0 = MAIN["L"], MAIN["R"]
Ls = sorted({max(1, int(round(L0 * f))) for f in (0.5, 0.75, 1.25, 1.5)} - {L0})
Rs = [r for r in (1, 2, 4, 8, 12, 24, 48) if r != R0]
GRID = [("L", v) for v in Ls] + [("k", v) for v in (5, 8, 12, 15)] + [("R", v) for v in Rs] + \
       [("univ", v) for v in (20, 30, 50, 60)] + [("band", v) for v in ((1.5, 2.0) if MAIN["band"] == 1.0 else (1.0, 1.5, 2.5, 3.0))]
nb_rows = []
for name, v in GRID:
    d = cfg_run(**{name: v}, start=DEV_START, end=DEV_END); t = cfg_run(**{name: v}, start=TEST_START, end=TEST_END)
    nb_rows.append(dict(param=name, value=v, dev_net=d["net_pct"], dev_mean=d["mean_month"], dev_geo=d["geo_month"],
                        test_net=t["net_pct"], test_mean=t["mean_month"], test_geo=t["geo_month"],
                        both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nb_rows[-1], flush=True)
nbp = int(sum(r["both_positive"] for r in nb_rows)); frac = nbp / len(nb_rows)
res["checks"]["5"] = {"pass": bool(frac >= 0.70), "numbers": dict(n=len(nb_rows), n_both_positive=nbp, frac=round(frac, 3), grid=nb_rows)}

# 6. beats chance: 200 random-pick books with the same k, R, band, universe, costs
class RandScore:
    def __init__(self, seed, N):
        self.rng, self.N = np.random.default_rng(seed), N

    def __getitem__(self, idx):
        return self.rng.random((len(idx), self.N)).astype(np.float32)


NR = 200
rand_net = []
for s_ in range(NR):
    r = cfg_run(score=RandScore(1000 + s_, P.cf.shape[1]))
    rand_net.append(r["net_pct"])
    if s_ % 25 == 0:
        print("rand", s_, r["net_pct"], f"[{time.time()-t0:.0f}s]", flush=True)
rand_net = np.array(rand_net, float)
pct = float((rand_net < full["net_pct"]).mean() * 100)
c6 = dict(n_random=NR, strategy_net=full["net_pct"], random_median_net=round(float(np.median(rand_net)), 2),
          random_p95_net=round(float(np.percentile(rand_net, 95)), 2), random_p5_net=round(float(np.percentile(rand_net, 5)), 2),
          strategy_beats_pct=round(pct, 1))
res["checks"]["6"] = {"pass": bool(pct >= 95), "numbers": c6}
print("check6", c6, flush=True)

# 7. not a few coins or trades: drop the 5 most profitable coins from the universe (universe re-picked without them)
pos = full["_positions"]
coin_pnl = np.zeros(len(P.syms))
for j, side, oi, ci, pnl, eq0 in pos:
    coin_pnl[j] += pnl
top5 = [P.syms[j] for j in np.argsort(-coin_pnl)[:5]]
net_profit = full["_curve"][-1][1] - full["_curve"][0][1]
D = build_daily(); PD = E2.Panel(D)
keepd = np.array([j for j, s_ in enumerate(PD.syms) if s_ not in top5])
PD5 = E2.Panel(D, keepd)
m5 = E2.universe_mask(PD5, 40)
full_m = np.zeros((len(PD.t), len(PD.syms)), bool); full_m[:, keepd] = m5
dmap = {s_: j for j, s_ in enumerate(PD.syms)}
hcols = np.array([dmap[s_] for s_ in P.syms])
dayidx = ((P.t - PD.t[0]) // DAY).astype(int)
um5 = full_m[dayidx][:, hcols]
r7 = cfg_run(umask=um5)
pos_sorted = sorted(pos, key=lambda x: -x[4])
hr = lambda i: datetime.fromtimestamp(int(P.t[min(i, len(P.t) - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d %H:00")
max_share = float(pos_sorted[0][4] / net_profit * 100) if net_profit > 0 else None
c7 = dict(top5_coins={s_: round(float(coin_pnl[P.syms.index(s_)]), 2) for s_ in top5},
          net_without_top5_coins=r7["net_pct"], mean_month_without_top5=r7["mean_month"], full_net_profit_usdt=round(float(net_profit), 2),
          n_positions=len(pos), largest_position_share_pct=round(max_share, 2) if max_share is not None else None,
          top_positions=[dict(coin=P.syms[j], side="long" if s_ > 0 else "short", opened=hr(oi), closed=hr(ci), pnl_usdt=round(float(p_), 2))
                         for j, s_, oi, ci, p_, e_ in pos_sorted[:5]])
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and max_share is not None and max_share <= 10), "numbers": c7}
print("check7", c7, flush=True)
# 8. compounds
c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"])
res["checks"]["8"] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]), "numbers": c8}
res["robust"] = bool(all(c["pass"] for c in res["checks"].values()))
res["n_checks_passed"] = int(sum(c["pass"] for c in res["checks"].values()))

# leverage table (1x..10x; never above)
lt = []
for lev in (1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
    row = {"lev": lev}
    for nm, a, b in (("full", DEV_START, TEST_END), ("test", TEST_START, TEST_END)):
        r = cfg_run(lev=lev, start=a, end=b)
        row[nm] = short(r)
    lt.append(row); print("lev", lev, row["full"]["mean_month"], row["test"]["mean_month"], row["full"]["max_dd_pct"], flush=True)
res["leverage_table"] = lt
reach = [r["lev"] for r in lt if r["test"]["mean_month"] >= 20]
res["leverage_needed_for_20pct"] = reach[0] if reach else None
res["ytd_2026_1x"] = dict(months={k: v for k, v in months.items() if k.startswith("2026")},
                          compounded=round(compound([v for k, v in months.items() if k.startswith("2026")]), 2))
res["best_honest"] = res["attempts"][0]["name"]
res["verdict"] = "PASS" if (test["mean_month"] >= 20 and test2["mean_month"] > 0 and not test["wiped"]) else "FAIL"
ntoday = int(X["trading_today"].sum())
res["universe_note"] = (f"{len(P.syms)} crypto USDT perps that were ever in the daily top-60 since 2020-11 ({ntoday} trading "
                        f"today, {len(P.syms) - ntoday} delisted); TradFi/index contracts dropped (r2_rotation/data.py)")
np.save("random_net.npy", rand_net)
json.dump(res, open("results.json", "w"), indent=1, default=float)
print("ROBUST", res["robust"], {k: v["pass"] for k, v in res["checks"].items()}, "verdict", res["verdict"], f"[{time.time()-t0:.0f}s]")
