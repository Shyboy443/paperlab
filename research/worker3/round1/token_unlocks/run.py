"""Single TEST run of the pre-registered config (PREREG.md) + the 8 checks + W1/W2 correlation + 500 USDT check.
Writes results.json. Run once."""
import json, numpy as np
from collections import defaultdict
from lib import *

CFG = dict(mn=0.05, X=30, Y=0, cap=0.33, hedge=True)
FULL0, FULL1 = DEV_START, TEST_END
rng = np.random.default_rng(7)


def trades_for(mn, X, Y):
    return candidates(mn, X=X, Y=Y)


def run(mn, X, Y, cap, hedge, cost_mult=1.0, **kw):
    tr = trades_for(mn, X, Y)
    r, c = book(tr, cap=cap, hedge=hedge, cost_mult=cost_mult, **kw)
    return tr, r, c


tr, r1, contrib = run(**CFG)
_, r2, _ = run(**CFG, cost_mult=2)
_, rs, _ = run(**CFG, scale=0.5)
M = monthly(r1, FULL0, FULL1); M2 = monthly(r2, FULL0, FULL1); MS = monthly(rs, FULL0, FULL1)
S = lambda mo, a, b: stats({k: v for k, v in mo.items() if a <= k <= b})
res = dict(agent="token_unlocks", config=CFG,
           dev=S(M, "2021-01", "2024-10"), test=S(M, "2024-11", "2026-10"), full=S(M, "2021-01", "2026-10"),
           dev_2x=S(M2, "2021-01", "2024-10"), test_2x=S(M2, "2024-11", "2026-10"), full_2x=S(M2, "2021-01", "2026-10"),
           strict1x_test=S(MS, "2024-11", "2026-10"), strict1x_full=S(MS, "2021-01", "2026-10"),
           test_daily_maxdd=daily_dd(r1, TEST_START, TEST_END), full_daily_maxdd=daily_dd(r1, FULL0, FULL1),
           ytd_2026=S(M, "2026-01", "2026-10"), oct_2026=M.get("2026-10"),
           monthly_1x={k: round(v, 3) for k, v in M.items()}, monthly_2xcost={k: round(v, 3) for k, v in M2.items()})
ntr = defaultdict(int)
for a, b, j, e in tr:
    if FULL0 <= T[a] < FULL1:
        ntr["dev" if T[a] < DEV_END else "test"] += 1
res["trades"] = dict(ntr); res["trades_per_month_test"] = round(ntr["test"] / 24, 2); res["trades_per_month_dev"] = round(ntr["dev"] / 46, 2)
res["test_trades"] = [dict(sym=SYMS[j], entry=__import__("datetime").datetime.utcfromtimestamp(int(T[a]) / 1000).date().isoformat(), unlock_day=int(e["day"]), size=round(e["size"], 3))
                      for a, b, j, e in tr if T[a] >= TEST_START]

checks = {}
v = np.array([M[k] for k in sorted(M)])
# 1 both halves, both costs
c1 = [res["dev"]["total"], res["test"]["total"], res["dev_2x"]["total"], res["test_2x"]["total"]]
checks[1] = dict(passed=all(x > 0 for x in c1), numbers=dict(dev=c1[0], test=c1[1], dev_2x=c1[2], test_2x=c1[3]))
# 2 years
yrs = {y: S(M, f"{y}-01", f"{y}-12")["total"] for y in range(2021, 2027)}
x22 = S(M, "2022-01", "2026-10")["total"]
checks[2] = dict(passed=sum(t > 0 for t in yrs.values()) >= 4 and x22 > 0, numbers=dict(years=yrs, from_2022=x22))
# 3 t-stat and block bootstrap
t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
nb = int(np.ceil(len(v) / 3)); means = []
for _ in range(5000):
    st = rng.integers(0, len(v) - 2, nb); s = np.concatenate([v[i:i + 3] for i in st])[:len(v)]; means.append(s.mean())
p5 = float(np.percentile(means, 5))
checks[3] = dict(passed=t >= 2 and p5 > 0, numbers=dict(t=round(float(t), 2), boot_p5=round(p5, 3), months=len(v)))
# 4 drop 3 best months
vv = np.sort(v)[:-3]; tot = float((np.prod(1 + vv / 100) - 1) * 100)
checks[4] = dict(passed=tot > 0, numbers=dict(total_without_best3=round(tot, 2)))
# 5 neighbours
nbs = []
for k, vals in dict(mn=(0.025, 0.0375, 0.0625, 0.075), X=(15, 22, 38, 45), Y=(1, 3, 7), cap=(0.165, 0.25, 0.41, 0.5), hedge=(False,)).items():
    for val in vals:
        p = dict(CFG); p[k] = val
        _, rr, _ = run(**p); mm = monthly(rr, FULL0, FULL1)
        d, te = S(mm, "2021-01", "2024-10"), S(mm, "2024-11", "2026-10")
        nbs.append(dict(param=k, value=val, dev_total=d["total"], test_total=te["total"], test_geo=te["geo"], test_median=te["median"],
                        ok=d["total"] > 0 and te["total"] > 0))
share = np.mean([n["ok"] for n in nbs])
checks[5] = dict(passed=share >= 0.7, numbers=dict(share=round(float(share), 3), neighbours=nbs))
# 6 random coin picks, same windows/weights/hedge/costs
elig = {}
for k, (a, b, j, e) in enumerate(tr):
    ok = np.where((QV30[a] >= 2e6) & np.isfinite(O[a]) & np.isfinite(O[max(a - 30, 0)]) & np.isfinite(O[min(b, ND - 1)]))[0]
    elig[k] = ok[(ok != BTC) & (ok != j)]
full_strat = res["full"]["total"]; rnd = []
for i in range(300):
    sw = [int(rng.choice(elig[k])) if len(elig[k]) else tr[k][2] for k in range(len(tr))]
    rr, _ = book(tr, cap=CFG["cap"], hedge=True, swap=sw)
    rnd.append(S(monthly(rr, FULL0, FULL1), "2021-01", "2026-10")["total"])
pct = float(np.mean(np.array(rnd) < full_strat))
checks[6] = dict(passed=pct >= 0.95, numbers=dict(beats_share=round(pct, 3), strat_total=full_strat,
                                                   random_median=round(float(np.median(rnd)), 2), random_p95=round(float(np.percentile(rnd, 95)), 2)))
# 7 coins / trades
cc = [x for x in contrib if FULL0 <= T[x[1]] < FULL1]
bycoin = defaultdict(float)
for s, a, val in cc:
    bycoin[s] += val
top5 = [s for s, _ in sorted(bycoin.items(), key=lambda x: -x[1])[:5]]
_, r7, _ = run(**CFG, skip_coins=set(top5)); tot7 = S(monthly(r7, FULL0, FULL1), "2021-01", "2026-10")["total"]
totc = sum(x[2] for x in cc); mx = max(x[2] for x in cc)
checks[7] = dict(passed=tot7 > 0 and mx / totc <= 0.10, numbers=dict(top5=top5, total_without_top5=tot7,
                                                                    max_trade_share=round(mx / totc, 3), n_trades=len(cc)))
# 8 compounding
f = res["full"]
checks[8] = dict(passed=f["geo"] > 0 and f["geo"] >= 0.5 * f["mean"], numbers=dict(geo=f["geo"], mean=f["mean"]))
res["checks"] = {k: {"pass": bool(x["passed"]), "numbers": x["numbers"]} for k, x in checks.items()}
res["n_pass"] = int(sum(x["passed"] for x in checks.values()))

# W1 / W2 monthly correlation (TEST and full)
w1 = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/results.json"))["monthly_1x"]
w2 = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/worker2/session/results.json"))["primary"]["monthly_1x"]
corr = {}
for nm, w in (("w1", w1), ("w2", w2)):
    for per, (a, b) in (("test", ("2024-11", "2026-10")), ("full", ("2021-01", "2026-10"))):
        ks = [k for k in sorted(M) if a <= k <= b and k in w]
        corr[f"{nm}_{per}"] = round(float(np.corrcoef([M[k] for k in ks], [w[k] for k in ks])[0, 1]), 3)
res["corr"] = corr
# portfolio W1+W2+X (equal thirds, monthly rebalanced) vs W1+W2 halves, TEST
ks = [k for k in sorted(M) if "2024-11" <= k <= "2026-10"]
p2 = {k: (w1[k] + w2[k]) / 2 for k in ks}; p3 = {k: (w1[k] + w2[k] + M[k]) / 3 for k in ks}
res["portfolio_test"] = dict(w1w2=stats(p2), w1w2x=stats(p3))
# 500 USDT: smallest order = one event slot x 500 x weight; min notional ~5 USDT
res["usdt500"] = dict(min_slot_notional=round(500 * min(CFG["cap"], 1 / 3), 1),
                      note="weights >= 1/n_active; max concurrent events reported", max_active=None)
act = defaultdict(int)
for a, b, j, e in tr:
    for d in range(a, b):
        act[d] += 1
mxa = max(act.values()); res["usdt500"]["max_active"] = mxa
res["usdt500"]["min_order_at_max_active"] = round(500 * min(CFG["cap"], 1 / mxa), 1)
json.dump(res, open("results.json", "w"), indent=1, default=str)
for k in ("dev", "test", "full", "dev_2x", "test_2x", "strict1x_test", "ytd_2026", "oct_2026", "trades", "trades_per_month_test",
          "test_daily_maxdd", "corr", "portfolio_test", "usdt500", "n_pass"):
    print(k, res[k])
for k, x in res["checks"].items():
    print(k, x["pass"], {a: b for a, b in x["numbers"].items() if a != "neighbours"})
for n in nbs:
    print(n)
