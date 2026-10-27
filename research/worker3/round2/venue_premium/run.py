"""The ONE pre-registered config (PREREG.md): CBPz3/30 BTC+ETH long/flat 1x. DEV/TEST/full, 8 checks, W1/W2, 500 USDT.
TEST is touched once here."""
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/venue_premium")
from lib import *  # noqa

HERE = Path(__file__).resolve().parent
R = Path(r"D:/Projects/BinanceBOt/paperlab/research")
t0 = time.time()
SG = build_signals()
CBP = SG["CBP"]
MAIN = dict(S=3, L=30, thr=0.0, f=1, assets="BTCETH")
AS = {"BTCETH": [BTC, ETH], "BTC": [BTC], "ETH": [ETH]}
FULL = DEV_START


def make_pos(S=3, L=30, thr=0.0, f=1, start=FULL, **_):
    s = rmean(CBP, S)
    z = (s - rmean(CBP, L)) / rstd(CBP, L)
    pos = shift(np.where(np.isnan(z), 0.0, (z > thr).astype(float)))
    if f > 1:
        i0 = int(np.searchsorted(P.t, start))
        idx = np.arange(T); anchor = i0 + ((idx - i0) // f) * f
        anchor = np.clip(anchor, 0, T - 1)
        pos = pos[anchor]
    return pos


def cfg(start=FULL, end=TEST_END, cost_mult=1.0, record=False, pos=None, **over):
    kw = dict(MAIN); kw.update(over)
    p = make_pos(start=start, **kw) if pos is None else pos
    return run_timing(p, AS[kw["assets"]], start, end, cost_mult=cost_mult, record=record)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def stats(r):
    m = r["months"]; nm = len(m)
    return dict(median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"], up=f'{r["months_up"]}/{nm}', max_dd=r["max_dd_pct"],
                net=r["net_pct"], t=round(tstat(m), 2), trades_per_month=round(r["trades"] / nm, 1), worst=r["worst_month"], best=r["best_month"])


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    return dict(median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2), mean=round(float(v.mean()), 2),
                up=f"{int((v > 0).sum())}/{len(v)}", max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2))


res = dict(agent="w3r2_venue_premium", config=MAIN, checks={})
full = cfg(record=True)
dev, test = cfg(start=DEV_START, end=DEV_END), cfg(start=TEST_START, end=TEST_END, record=True)
dev2, test2, full2 = cfg(start=DEV_START, end=DEV_END, cost_mult=2), cfg(start=TEST_START, end=TEST_END, cost_mult=2), cfg(cost_mult=2)
s2501 = cfg(start=int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000), cost_mult=2)
bh_test = run_timing(np.ones(T), [BTC, ETH], TEST_START, TEST_END)
months = full["months"]; assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res.update(dev_1x=stats(dev), test_1x=stats(test), full_1x=stats(full), dev_2x=stats(dev2), test_2x=stats(test2), full_2x=stats(full2),
           net_2025_01_2026_10_2xcost=s2501["net_pct"], monthly_1x=months, test_months=test["months"],
           buyhold_btceth_test=dict(geo=bh_test["geo_month"], median=bh_test["median_month"], dd=bh_test["max_dd_pct"], net=bh_test["net_pct"]))
res["ytd_2026"] = round(compound([v for k, v in test["months"].items() if k.startswith("2026")]), 2)
res["oct_2026"] = test["months"].get("2026-10")
print("dev", res["dev_1x"], "\ntest", res["test_1x"], "\nfull", res["full_1x"], "\nBH test", res["buyhold_btceth_test"], flush=True)

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
GRID = [("S", v) for v in (2, 4, 5)] + [("L", v) for v in (15, 22, 38, 45)] + [("f", v) for v in (2, 3)] + \
       [("thr", v) for v in (-0.25, 0.25)] + [("assets", "BTC")]
nbr = []
for name, v in GRID:
    d = cfg(start=DEV_START, end=DEV_END, **{name: v}); t = cfg(start=TEST_START, end=TEST_END, **{name: v})
    nbr.append(dict(param=name, value=v, dev_net=d["net_pct"], dev_geo=d["geo_month"], test_net=t["net_pct"], test_geo=t["geo_month"],
                    test_median=t["median_month"], test_dd=t["max_dd_pct"], both=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nbr[-1], flush=True)
fr = sum(x["both"] for x in nbr) / len(nbr)
res["checks"]["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac=round(fr, 3), n=len(nbr), grid=nbr))
# 6: random circular shifts of the in-sample position series
p0 = make_pos()
i0, i1 = int(np.searchsorted(P.t, FULL)), int(np.searchsorted(P.t, TEST_END))
seg = p0[i0:i1].copy(); rn = []
rr = np.random.default_rng(11)
for s in range(500):
    k = int(rr.integers(30, len(seg) - 30))
    pp = p0.copy(); pp[i0:i1] = np.roll(seg, k)
    rn.append(cfg(pos=pp)["net_pct"])
rn = np.array(rn); pct = float((rn < full["net_pct"]).mean() * 100)
res["checks"]["6"] = dict(pass_=pct >= 95, numbers=dict(beats_pct=pct, rand_median=round(float(np.median(rn)), 2),
                                                        rand_p95=round(float(np.percentile(rn, 95)), 2), strat=full["net_pct"]))
# 7: single-coin versions + largest holding spell share (log-return)
rb, re_ = cfg(assets="BTC"), cfg(assets="ETH")
eq = np.array([e for _, e in full["_curve"]]); lr = np.log(eq[1:] / eq[:-1]); ps = p0[i0:i1]
spells, cur = [], 0.0
for x, inm in zip(lr, ps):
    if inm > 0:
        cur += x
    elif cur != 0.0:
        spells.append(cur); cur = 0.0
if cur:
    spells.append(cur)
tot = lr.sum(); mx = max(spells) / tot * 100
res["checks"]["7"] = dict(pass_=bool(rb["net_pct"] > 0 and re_["net_pct"] > 0 and mx <= 10),
                          numbers=dict(btc_only_net=rb["net_pct"], eth_only_net=re_["net_pct"], n_spells=len(spells),
                                       largest_spell_share_pct=round(float(mx), 2)))
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
res["portfolio_test_monthly"] = dict(X=mstats([X[k] for k in tk]), W1=mstats([W1m[k] for k in tk]),
                                     W1W2_5050=mstats([(W1m[k] + W2m[k]) / 2 for k in tk]),
                                     W1W2X_thirds=mstats([(W1m[k] + W2m[k] + X[k]) / 3 for k in tk]))
res["usdt500"] = dict(per_leg_usdt=250, note="2 legs of 250 USDT: above BTCUSDT (100) and ETHUSDT (20) min notional; ~1 switch per 10 days.")
print(json.dumps({k: res[k] for k in ("corr", "portfolio_test_monthly", "ytd_2026", "oct_2026", "n_pass")}, default=float))
print({k: (v["pass"], v["numbers"] if k != "5" else v["numbers"]["frac"]) for k, v in res["checks"].items()})
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
