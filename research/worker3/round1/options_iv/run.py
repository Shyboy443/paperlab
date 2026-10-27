"""Runs the pre-registered config once on DEV / TEST / full + the 8 checks. Writes results.json."""
import json, collections, numpy as np, core
from core import DEV_START, DEV_END, TEST_START, TEST_END
from datetime import datetime, timezone
FULL0 = DEV_START
P = dict(fam="ivchg", k=7, th=0.10, short=True, contra=1)
D = core.load()
B = lambda p, a, b, cm=1.0, **kw: core.brief(core.simulate(D, p, a, b, cost_mult=cm, **kw))
strip = lambda s: {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v) for k, v in s.items()}
res = dict(agent="options_iv", config=P)
for nm, a, b in (("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END), ("full", FULL0, TEST_END),
                 ("y2025_26", core.DEV_START * 0 + 1735689600000, TEST_END)):
    res[nm] = strip(B(P, a, b)); res[nm + "_2x"] = strip(B(P, a, b, 2.0))
full_r = core.simulate(D, P, FULL0, TEST_END, record=True)
M = res["full"]["months"]; vals = np.array(list(M.values()))
yrs = collections.defaultdict(lambda: 1.0)
for k, v in M.items(): yrs[k[:4]] *= 1 + v / 100
yrs = {k: round((v - 1) * 100, 2) for k, v in yrs.items()}
res["years"] = yrs
chk = {}
# 1 both halves
c1 = [res[x]["net_pct"] for x in ("dev", "dev_2x", "test", "test_2x")]
chk[1] = dict(pass_=all(x > 0 for x in c1), numbers=dict(zip(("dev", "dev_2x", "test", "test_2x"), c1)))
# 2 most years
r22 = strip(B(P, 1640995200000, TEST_END))["net_pct"]
chk[2] = dict(pass_=sum(v > 0 for v in yrs.values()) >= 4 and r22 > 0, numbers=dict(years=yrs, from2022=r22))
# 3 t-stat + bootstrap
t = vals.mean() / (vals.std(ddof=1) / np.sqrt(len(vals)))
rng = np.random.default_rng(7); n = len(vals); bs = []
for _ in range(5000):
    st = rng.integers(0, n - 2, size=(n + 2) // 3)
    idx = np.concatenate([np.arange(s, s + 3) for s in st])[:n]; bs.append(vals[idx].mean())
p5 = float(np.percentile(bs, 5))
chk[3] = dict(pass_=t >= 2 and p5 > 0, numbers=dict(t=round(float(t), 2), boot_p5=round(p5, 3), n_months=n))
# 4 minus best 3 months
rest = np.sort(vals)[:-3]; net_wo = (np.prod(1 + rest / 100) - 1) * 100
chk[4] = dict(pass_=net_wo > 0, numbers=dict(net_without_best3=round(float(net_wo), 2)))
# 5 neighbours
nb = []
for kk in (4, 5, 9, 11): nb.append(dict(P, k=kk))
for th in (0.05, 0.075, 0.125, 0.15): nb.append(dict(P, th=th))
for h in (2, 3): nb.append(dict(P, hold=h))
nbr = []
for q in nb:
    d_, t_ = B(q, DEV_START, DEV_END), B(q, TEST_START, TEST_END)
    nbr.append(dict(p=q, dev_net=float(d_["net_pct"]), test_net=float(t_["net_pct"]), test_geo=float(t_["geo_month"]),
                    test_median=float(t_["median_month"])))
npos = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr)
chk[5] = dict(pass_=npos >= 0.7 * len(nbr), numbers=dict(pos_both=npos, of=len(nbr), rows=nbr))
# 6 random timing
sig = {c: core.signal(D, c, P) for c in core.COINS}
full_net = res["full"]["net_pct"]; rnd = []
rng = np.random.default_rng(11)
valid0 = int(np.searchsorted(D["t"], 1616544000000 + 8 * 86400000))
for _ in range(500):
    ss = {}
    for c in core.COINS:
        s = sig[c].copy(); seg = s[valid0:]; s[valid0:] = np.roll(seg, rng.integers(30, len(seg) - 30)); ss[c] = s
    rnd.append(core.brief(core.simulate(D, P, FULL0, TEST_END, sig=ss))["net_pct"])
pct = float(np.mean(np.array(rnd) < full_net) * 100)
chk[6] = dict(pass_=pct >= 95, numbers=dict(beats_pct=round(pct, 1), random_median_net=round(float(np.median(rnd)), 1), n=500))
# 7 coins / positions
alone = {c: float(B(P, FULL0, TEST_END, coins=(c,))["net_pct"]) for c in core.COINS}
pp = np.array([x[3] for x in full_r["pos"]]); tot = pp.sum(); top = float(pp.max() / tot * 100) if tot > 0 else None
chk[7] = dict(pass_=all(v > 0 for v in alone.values()) and tot > 0 and top <= 10,
              numbers=dict(each_coin_alone_net=alone, n_positions=len(pp), top_position_share_pct=round(top, 2) if top else None))
# 8 compounds
g, m = res["full"]["geo_month"], res["full"]["mean_month"]
chk[8] = dict(pass_=g > 0 and g >= 0.5 * m, numbers=dict(geo=g, mean=m))
res["checks"] = {k: dict(pass_=bool(v["pass_"]), numbers=v["numbers"]) for k, v in chk.items()}
res["n_pass"] = sum(v["pass_"] for v in res["checks"].values())
# Worker 1 / Worker 2 correlations (monthly, TEST and full)
w1 = json.load(open(core.PROJECT / "research/monthly20/r4_07_ensemble/results.json"))["monthly_1x"]
w2 = json.load(open(core.PROJECT / "research/worker2/session/results.json"))["primary"]["monthly_1x"]
def corr(a, b, keys):
    k = [x for x in keys if x in a and x in b]
    return round(float(np.corrcoef([a[x] for x in k], [b[x] for x in k])[0, 1]), 3), len(k)
tk = list(res["test"]["months"].keys())
res["corr"] = dict(w1_test=corr(M, w1, tk), w2_test=corr(M, w2, tk), w1_full=corr(M, w1, list(M)), w2_full=corr(M, w2, list(M)))
# portfolio W1+W2+X equal monthly mix vs W1+W2 on TEST
def stats(vs):
    vs = np.array(vs); eq = np.cumprod(1 + vs / 100); dd = float(np.max(1 - eq / np.maximum.accumulate(np.r_[1, eq])[1:]) * 100)
    return dict(median=round(float(np.median(vs)), 2), geo=round(float((eq[-1] ** (1 / len(vs)) - 1) * 100), 2),
                mean=round(float(vs.mean()), 2), up=int((vs > 0).sum()), n=len(vs), month_end_dd=round(dd, 2))
k = [x for x in tk if x in w1 and x in w2]
res["portfolio_test"] = dict(w1w2=stats([(w1[x] + w2[x]) / 2 for x in k]), w1w2x=stats([(w1[x] + w2[x] + M[x]) / 3 for x in k]),
                             x_alone=stats([M[x] for x in k]))
res["ytd_2026"] = strip(B(P, 1767225600000, TEST_END)); res["ytd_2026"].pop("months", None)
res["oct_2026"] = M.get("2026-10")
res["size_500usdt"] = ("250 USDT per coin leg at 1x; BTCUSDT min notional 100 USDT and qty step 0.001 BTC (~85 USDT) -> "
                       "legs round to 0.003 BTC (~±15% size error); ETHUSDT step 0.001 ETH fine. Market orders, ~5 trades/month.")
json.dump(res, open("results.json", "w"), indent=1, default=float)
show = lambda s: {k: s[k] for k in ("median_month", "geo_month", "mean_month", "months_up", "n_months", "max_dd_pct", "t_stat", "trades_per_month", "net_pct")}
for nm in ("dev", "test", "full", "dev_2x", "test_2x", "y2025_26_2x"): print(nm, show(res[nm]))
print("years", yrs)
for k, v in res["checks"].items(): print("check", k, v["pass_"], {a: b for a, b in v["numbers"].items() if a != "rows"})
print("n_pass", res["n_pass"]); print("corr", res["corr"]); print("portfolio", res["portfolio_test"])
print("ytd", show(res["ytd_2026"]) if "t_stat" in res["ytd_2026"] else res["ytd_2026"], "oct", res["oct_2026"])
print("test months", res["test"]["months"])
for r in nbr: print(r)
