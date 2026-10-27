"""The ONE TEST run and ONE HOLDOUT run of the selected config (PREREG.md addendum), the 8 checks on both windows,
correlations, portfolios, 500 USDT notes. Writes results.json."""
import json, math, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib6 as L
import sig
KEEP = []
from lib6 import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
t0 = time.time()
SEL = json.loads((HERE.parents[1] / "round5/high52_plus/dev.json").read_text())["selected"]["cfg"]
RN = 7
D = L.L4.build(); P = L.L4.make_panel(D)
H = L.L4.holdout_panel(); HP = L.L4.make_panel(H)
NR = 300
NB = [("lscale", v) for v in (0.5, 0.75, 1.25, 1.5)] + [("k", v) for v in (3, 4, 6, 8)] + \
     [("every", v) for v in (2, 3, 5, 7)] + [("univ", v) for v in (10, 15, 25, 30)] + [("rn", v) for v in (3, 5, 10, 14)]


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if v.std() > 0 else float("nan")
    return dict(n=len(v), median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2), t=round(t, 2), net=round(float((eq[-1] - 1) * 100), 2))


def boot_p5(vals):
    g = np.random.default_rng(7); nb = math.ceil(len(vals) / 3)
    st = g.integers(0, len(vals) - 2, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def vk(Pn, lscale=1.0, rn=RN, S=None):
    S = L.score(Pn, "30", lscale) if S is None else S
    return dict(score_override=S, short_score=sig.mask(S, sig.ret(Pn, rn) < 0))


def run(Pn, s, e, cm=1.0, rec=False, **over):
    KEEP.append(Pn)
    rn = over.pop("rn", RN)
    c = dict(SEL); c.update(over)
    return L.run5(Pn, c, s, e, cost_mult=cm, record=rec, **vk(Pn, c.get("lscale", 1.0), rn))


def c6(Pn, s, e, strat_net):
    c = dict(SEL); blk = np.arange(Pn.c.shape[0]) // 30
    rn, rb = [], []
    for k in range(NR):
        rn.append(L.run5(Pn, c, s, e, **vk(Pn, S=np.random.default_rng(1000 + k).random(Pn.c.shape)))["net_pct"])
        rb.append(L.run5(Pn, c, s, e, **vk(Pn, S=np.random.default_rng(5000 + k).random((blk.max() + 1, Pn.c.shape[1]))[blk]))["net_pct"])
    rn, rb = np.array(rn), np.array(rb)
    pa, pb = float((rn < strat_net).mean() * 100), float((rb < strat_net).mean() * 100)
    return dict(pass_=pa >= 95 and pb >= 95, numbers=dict(daily_beats_pct=pa, block30_beats_pct=pb, rand_daily_median=round(float(np.median(rn)), 2),
                rand_daily_p95=round(float(np.percentile(rn, 95)), 2), rand_block_median=round(float(np.median(rb)), 2),
                rand_block_p95=round(float(np.percentile(rb, 95)), 2), strat=strat_net))


def c7(Pn, DD, r, s, e):
    pos = r["_positions"]; netp = r["_curve"][-1][1] - r["_curve"][0][1]
    cp = np.zeros(len(Pn.syms))
    for p in pos:
        cp[p[0]] += p[4]
    top5 = np.argsort(-cp)[:5]
    P5 = L.L4.make_panel(DD, np.setdiff1d(np.arange(len(Pn.syms)), top5))
    r7 = run(P5, s, e)
    mx = L.max_pos_share(r)
    return dict(pass_=bool(r7["net_pct"] > 0 and mx <= 10), numbers=dict(
        top5_coins={Pn.syms[j]: round(float(cp[j] / netp * 100), 2) for j in top5}, net_without_top5=r7["net_pct"],
        largest_position_share_pct=mx,
        top5_positions=[(Pn.syms[p[0]], "L" if p[1] > 0 else "S", int(p[3] - p[2]), round(p[4] / netp * 100, 2))
                        for p in sorted(pos, key=lambda p: -p[4])[:5]]))


def fin(c):
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    return c


res = dict(agent="w3r6_signal", selected=dict(SEL, short_filter="ret7<0"))
# ---------------- DEV / TEST / full 2021-26 ----------------
dev, dev2 = run(P, DEV_START, DEV_END, rec=True), run(P, DEV_START, DEV_END, 2)
test, test2 = run(P, TEST_START, TEST_END, rec=True), run(P, TEST_START, TEST_END, 2)
res["liq"] = dict(dev=dev["liquidations"], test=test["liquidations"])
full, full2 = run(P, DEV_START, TEST_END, rec=True), run(P, DEV_START, TEST_END, 2)
res.update(dev_1x=L.stats(dev), dev_2x=L.stats(dev2), test_1x=L.stats(test), test_2x=L.stats(test2), full_1x=L.stats(full),
           full_2x=L.stats(full2), monthly_full_1x=full["months"], test_max_pos_share=L.max_pos_share(test))
print("DEV", res["dev_1x"], "\nTEST", res["test_1x"], "\nTEST2x", res["test_2x"]["net"], f"[{time.time()-t0:.0f}s]", flush=True)
m = full["months"]; vals = np.array(list(m.values()))
c = {}
c["1"] = dict(pass_=all(x > 0 for x in (dev["net_pct"], test["net_pct"], dev2["net_pct"], test2["net_pct"])),
              numbers=dict(dev=dev["net_pct"], test=test["net_pct"], dev_2x=dev2["net_pct"], test_2x=test2["net_pct"]))
yrs = {str(y): round(compound([v for k, v in m.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex21 = round(compound([v for k, v in m.items() if k >= "2022-01"]), 2)
c["2"] = dict(pass_=sum(v > 0 for v in yrs.values()) >= 4 and ex21 > 0, numbers=dict(years=yrs, net_ex2021=ex21))
ts = L.tstat(m); p5 = boot_p5(vals)
c["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(ts, 3), boot_p5=round(p5, 3)))
b3 = sorted(m, key=m.get, reverse=True)[:3]
c["4"] = dict(pass_=compound([v for k, v in m.items() if k not in b3]) > 0, numbers=dict(best3={k: m[k] for k in b3},
              net_without=round(compound([v for k, v in m.items() if k not in b3]), 2)))
nb = []
for name, v in NB:
    d_, t_ = run(P, DEV_START, DEV_END, **{name: v}), run(P, TEST_START, TEST_END, **{name: v})
    nb.append(dict(param=name, value=v, dev_net=d_["net_pct"], dev_geo=d_["geo_month"], test_net=t_["net_pct"], test_geo=t_["geo_month"],
                   both=bool(d_["net_pct"] > 0 and t_["net_pct"] > 0)))
fr = sum(x["both"] for x in nb) / len(nb)
c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), grid=nb))
print("c5", fr, f"[{time.time()-t0:.0f}s]", flush=True)
c["6"] = c6(P, DEV_START, TEST_END, full["net_pct"])
print("c6", c["6"]["numbers"], f"[{time.time()-t0:.0f}s]", flush=True)
c["7"] = c7(P, D, full, DEV_START, TEST_END)
c["8"] = dict(pass_=bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
              numbers=dict(geo=full["geo_month"], mean=full["mean_month"]))
res["checks_test"] = fin(c); res["n_pass_test"] = sum(x["pass"] for x in c.values())
print("TEST checks", {k: x["pass"] for k, x in c.items()}, flush=True)

# ---------------- HOLDOUT ----------------
ho, ho2 = run(HP, HO_START, HO_END, rec=True), run(HP, HO_START, HO_END, 2)
res["liq"]["holdout"] = ho["liquidations"]
hm = ho["months"]; hv = np.array(list(hm.values()))
res.update(holdout_1x=L.stats(ho), holdout_2x=L.stats(ho2), monthly_holdout_1x=hm)
act = [v for v in hm.values() if v != 0.0]
res["holdout_active_months"] = mstats(act)
print("HOLDOUT", res["holdout_1x"], "2x", ho2["net_pct"], flush=True)
c = {}
c["1"] = dict(pass_=ho["net_pct"] > 0 and ho2["net_pct"] > 0, numbers=dict(net_1x=ho["net_pct"], net_2x=ho2["net_pct"]))
hy = {str(y): round(compound([v for k, v in hm.items() if k.startswith(str(y))]), 2) for y in (2018, 2019, 2020)}
e18 = round(compound([v for k, v in hm.items() if k >= "2019-01"]), 2)
c["2"] = dict(pass_=sum(v > 0 for v in hy.values()) >= 2 and e18 > 0, numbers=dict(years=hy, net_2019_2020=e18))
ts = L.tstat(hm); p5 = boot_p5(hv)
c["3"] = dict(pass_=bool(ts >= 2 and p5 > 0), numbers=dict(t=round(ts, 3), boot_p5=round(p5, 3)))
b3 = sorted(hm, key=hm.get, reverse=True)[:3]
c["4"] = dict(pass_=compound([v for k, v in hm.items() if k not in b3]) > 0, numbers=dict(best3={k: hm[k] for k in b3},
              net_without=round(compound([v for k, v in hm.items() if k not in b3]), 2)))
nb = []
for name, v in NB:
    x = run(HP, HO_START, HO_END, **{name: v})
    nb.append(dict(param=name, value=v, net=x["net_pct"], geo=x["geo_month"]))
fr = sum(x["net"] > 0 for x in nb) / len(nb)
c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), grid=nb))
c["6"] = c6(HP, HO_START, HO_END, ho["net_pct"])
c["7"] = c7(HP, H, ho, HO_START, HO_END)
c["8"] = dict(pass_=bool(ho["geo_month"] > 0 and ho["geo_month"] >= 0.5 * ho["mean_month"]), numbers=dict(geo=ho["geo_month"], mean=ho["mean_month"]))
res["checks_holdout"] = fin(c); res["n_pass_holdout"] = sum(x["pass"] for x in c.values())
print("HOLDOUT checks", {k: x["pass"] for k, x in c.items()}, f"[{time.time()-t0:.0f}s]", flush=True)

# ---------------- portfolio ----------------
W1h = json.loads((R / "worker1_backfill/results.json").read_text())["backfill_1x"]["monthly"]
W2h = json.loads((R / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]["1x"]["monthly"]
W1p = json.loads((R / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2p = json.loads((R / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
X = full["months"]
wins = {"DEV": (W1p, W2p, X, lambda k: k < "2024-11"), "TEST": (W1p, W2p, X, lambda k: k >= "2024-11"),
        "HOLDOUT": (W1h, W2h, hm, lambda k: True)}
A, B, C_, f = wins["DEV"]; kd = sorted(k for k in A if k in B and k in C_ and f(k))
dd5050 = mstats([(A[k] + B[k]) / 2 for k in kd])["max_dd_monthly"]
best = None
for a in range(1, 9):
    for b in range(1, 10 - a):
        x = 10 - a - b
        if x < 1:
            continue
        s_ = mstats([(a * A[k] + b * B[k] + x * C_[k]) / 10 for k in kd])
        if s_["max_dd_monthly"] <= dd5050 and (best is None or s_["geo"] > best[1]["geo"]):
            best = ((a / 10, b / 10, x / 10), s_)
res["dev_mix"] = dict(weights_W1_W2_X=best[0] if best else None, dev_5050_dd=dd5050)
port = {}
for w, (A, B, C_, f) in wins.items():
    ks = sorted(k for k in A if k in B and k in C_ and f(k))
    d = dict(months=f"{ks[0]}..{ks[-1]}", W1=mstats([A[k] for k in ks]), W2=mstats([B[k] for k in ks]),
             X=mstats([C_[k] for k in ks]), W1W2_5050=mstats([(A[k] + B[k]) / 2 for k in ks]),
             W1W2X_thirds=mstats([(A[k] + B[k] + C_[k]) / 3 for k in ks]),
             corr_X=dict(W1=round(float(np.corrcoef([A[k] for k in ks], [C_[k] for k in ks])[0, 1]), 3),
                         W2=round(float(np.corrcoef([B[k] for k in ks], [C_[k] for k in ks])[0, 1]), 3)))
    if best:
        a, b, x = best[0]
        d["W1W2X_devmix"] = mstats([a * A[k] + b * B[k] + x * C_[k] for k in ks])
    port[w] = d
res["portfolio"] = port
print(json.dumps(port, indent=1), json.dumps(res["dev_mix"]), flush=True)
tpos = test["_positions"]; syms = [P.syms[p[0]] for p in tpos]
res["usdt500"] = dict(per_position_usdt=500 / (2 * SEL.get("k", 10)), positions=2 * SEL.get("k", 10),
                      trades_per_month_test=res["test_1x"]["trades_per_month"],
                      share_btc_positions_test=round(syms.count("BTCUSDT") / len(tpos) * 100, 1),
                      share_eth_positions_test=round(syms.count("ETHUSDT") / len(tpos) * 100, 1))
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
