"""exchange_events: the ONE pre-registered config (PREREG.md), DEV / TEST (run once) / full, the 8 checks, W1/W2 correlation."""
import json, time
import numpy as np
from engine import event_list, run, O, SYMS, _cn, _cq
from evlib import DEV_START, DEV_END, TEST_START, TEST_END
from stats import st, line
TYPES = {"UP_KRW_LIST", "UP_OTHER_LIST", "BN_SPOT_DELIST", "BN_MON_ADD", "UP_WARN"}
WIN = {"dev": (DEV_START, DEV_END), "test": (TEST_START, TEST_END), "full": (DEV_START, TEST_END)}
t_start = time.time()
OUT = {"agent": "exchange_events",
       "idea": "short Binance USDT perps for 48h after Binance delist / monitoring-tag and Upbit listing / warning announcements",
       "selected": {"types": sorted(TYPES), "H": 48, "S": 4, "delay_h": 0, "min_qv24": 1e6}}


def go(types=TYPES, H=48, S=4, delay=0, cm=1.0, win="full", exclude=()):
    t0, t1 = WIN[win]
    ev = event_list(types, delay=delay, t0=t0, t1=t1, exclude=exclude)
    c, tr = run(ev, H=H, S=S, cm=cm, t0=t0, t1=t1)
    return c, tr, st(c, tr)


R = {}
for w in WIN:
    for cm in (1.0, 2.0):
        c, tr, s = go(win=w, cm=cm); R[(w, cm)] = (c, tr, s)
        print(line(f"{w} cost x{cm}", s), flush=True)
for w in WIN:
    OUT[f"{w}_1x"] = R[(w, 1.0)][2]; OUT[f"{w}_2xcost_1x"] = R[(w, 2.0)][2]
fc, ftr, fs = R[("full", 1.0)]
months = fs["months"]; OUT["monthly_1x"] = months
mv = np.array(list(months.values())) / 100
chk = {}
# 1 both halves
d1, t1_, d2, t2 = (R[(w, c)][2]["net_pct"] for w, c in (("dev", 1.0), ("test", 1.0), ("dev", 2.0), ("test", 2.0)))
chk[1] = {"pass": min(d1, t1_, d2, t2) > 0, "numbers": dict(dev=d1, test=t1_, dev_2x=d2, test_2x=t2)}
# 2 years
yrs = {}
for k, v in months.items():
    yrs.setdefault(k[:4], 1.0); yrs[k[:4]] *= 1 + v / 100
yrs = {y: round((v - 1) * 100, 2) for y, v in yrs.items()}
ex21 = np.prod([1 + v / 100 for k, v in months.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": sum(v > 0 for v in yrs.values()) >= 4 and ex21 > 0, "numbers": dict(years=yrs, from_2022=round(ex21 * 100, 2))}
# 3 t-stat + block bootstrap (3-month blocks, 5000)
tstat = mv.mean() / (mv.std(ddof=1) / np.sqrt(len(mv)))
rng = np.random.default_rng(0); n = len(mv); bm = []
for _ in range(5000):
    s0 = rng.integers(0, n - 2, size=(n + 2) // 3)
    bm.append(np.concatenate([mv[s:s + 3] for s in s0])[:n].mean())
p5 = np.percentile(bm, 5)
chk[3] = {"pass": tstat >= 2 and p5 > 0, "numbers": dict(t=round(float(tstat), 2), boot_p5_mean_month_pct=round(float(p5) * 100, 3), n_months=n)}
# 4 drop 3 best months
rest = np.sort(mv)[:-3]
chk[4] = {"pass": np.prod(1 + rest) > 1, "numbers": dict(net_without_3_best_pct=round(float(np.prod(1 + rest) - 1) * 100, 2))}
# 5 neighbours
NB = {f"H{h}": dict(H=h) for h in (24, 36, 60, 72)}
NB.update({f"S{s}": dict(S=s) for s in (2, 3, 5, 6)})
NB.update({f"delay+{d}h": dict(delay=d) for d in (1, 3)})
NB.update({f"drop {t}": dict(types=TYPES - {t}) for t in sorted(TYPES)})
nbres = {}
for k, kw in NB.items():
    a = go(win="dev", **kw)[2]; b = go(win="test", **kw)[2]
    nbres[k] = dict(dev_net=a["net_pct"], test_net=b["net_pct"], test_geo=b["geo_month"], test_med=b["median_month"],
                    test_up=b["months_up"], test_dd=b["max_dd_pct"])
    print("nb", k, nbres[k], flush=True)
share = float(np.mean([v["dev_net"] > 0 and v["test_net"] > 0 for v in nbres.values()]))
chk[5] = {"pass": share >= 0.7, "numbers": dict(share_positive_both=round(share, 3), grid=nbres)}
# 6 random coins at the same hours
evs = event_list(TYPES, t0=DEV_START, t1=TEST_END)
cnt = {}
for e in evs:
    cnt[e[0]] = cnt.get(e[0], 0) + 1
hours = sorted(cnt)
pool = {}
for h in hours:
    q24 = _cq[h] - _cq[h - 24]; n24 = _cn[h] - _cn[h - 24]
    pool[h] = np.where((~np.isnan(O[h])) & (n24 >= 20) & (_cn[h] >= 24) & (q24 >= 1e6))[0]
rnd = []; rng = np.random.default_rng(1)
for r_ in range(200):
    ev = []
    for h in hours:
        js = rng.choice(pool[h], size=min(cnt[h], len(pool[h])), replace=False)
        ev += [(h, int(j), SYMS[j], "rnd") for j in js]
    rnd.append(run(ev, H=48, S=4, t0=DEV_START, t1=TEST_END)[0][-1][1] - 1)
    if r_ % 50 == 0:
        print("random", r_, round(rnd[-1] * 100, 1), flush=True)
rnd = np.array(rnd); real = fc[-1][1] - 1; beat = float(np.mean(real > rnd))
chk[6] = {"pass": beat >= 0.95, "numbers": dict(beat_share=round(beat, 3), real_net_pct=round(real * 100, 1),
          random_median_net_pct=round(float(np.median(rnd)) * 100, 1), random_p95_net_pct=round(float(np.percentile(rnd, 95)) * 100, 1))}
# 7 top-5 coins + single position
by = {}
for t in ftr:
    by[t["tk"]] = by.get(t["tk"], 0) + t["pnl"]
top5 = [k for k, _ in sorted(by.items(), key=lambda x: -x[1])[:5]]
c7 = go(exclude=tuple(top5))[2]
tot = sum(t["pnl"] for t in ftr); mx = max(t["pnl"] for t in ftr)
chk[7] = {"pass": c7["net_pct"] > 0 and mx / tot <= 0.10, "numbers": dict(top5=top5, net_without_top5=c7["net_pct"],
          max_single_trade_share=round(mx / tot, 3), n_trades=len(ftr))}
# 8 compounding
chk[8] = {"pass": fs["geo_month"] > 0 and fs["geo_month"] >= 0.5 * fs["mean_month"], "numbers": dict(geo=fs["geo_month"], mean=fs["mean_month"])}
for k in chk:
    chk[k]["pass"] = bool(chk[k]["pass"])
    print("check", k, chk[k]["pass"], {a: b for a, b in chk[k]["numbers"].items() if a != "grid"}, flush=True)
OUT["checks"] = chk; OUT["n_pass"] = sum(c["pass"] for c in chk.values())
# W1 / W2 monthly correlation and portfolio
W1 = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/results.json"))["monthly_1x"]
W2 = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/worker2/session/results.json"))["primary"]["monthly_1x"]
tm = [k for k in months if k >= "2024-11"]


def corr(a, b, ks):
    ks = [k for k in ks if k in a and k in b]
    return round(float(np.corrcoef([a[k] for k in ks], [b[k] for k in ks])[0, 1]), 3), len(ks)


OUT["corr"] = {"w1_test": corr(months, W1, tm), "w2_test": corr(months, W2, tm),
               "w1_full": corr(months, W1, list(months)), "w2_full": corr(months, W2, list(months))}


def mstats(vals):
    v = np.array(vals); g = (np.prod(1 + v / 100) ** (1 / len(v)) - 1) * 100
    eq = np.r_[1, np.cumprod(1 + v / 100)]; dd = np.max(1 - eq / np.maximum.accumulate(eq))
    return dict(median=round(float(np.median(v)), 2), geo=round(float(g), 2), up=int((v > 0).sum()), n=len(v), monthly_dd=round(float(dd) * 100, 1))


ks = [k for k in tm if k in W1 and k in W2]
OUT["portfolio_test_monthly"] = {"W1+W2 50/50": mstats([(W1[k] + W2[k]) / 2 for k in ks]),
                                 "W1+W2+X thirds": mstats([(W1[k] + W2[k] + months[k]) / 3 for k in ks]),
                                 "X alone": mstats([months[k] for k in ks])}
OUT["ytd_2026_pct"] = round(float(np.prod([1 + v / 100 for k, v in months.items() if k >= "2026-01"]) - 1) * 100, 2)
OUT["oct_2026_pct"] = months.get("2026-10")
# 500 USDT: order notional = weight of equity at entry x 500
c_t, tr_t = R[("test", 1.0)][0], R[("test", 1.0)][1]
ts_ = np.array([k for k, _ in c_t]); eqs = np.array([e for _, e in c_t])
tw = np.array([t["cap"] / eqs[max(0, np.searchsorted(ts_, t["t"], "right") - 1)] for t in tr_t])
OUT["usdt500"] = {"weight_min": round(float(tw.min()), 4), "weight_median": round(float(np.median(tw)), 4),
                  "orders_below_5usdt_at_500": int((tw * 500 < 5).sum()), "orders_below_20usdt_at_500": int((tw * 500 < 20).sum()),
                  "n_orders": len(tw)}
tt = {}
for t in tr_t:
    d = tt.setdefault(t["typ"], [0, 0.0]); d[0] += 1; d[1] += t["ret"]
OUT["test_types"] = {k: dict(n=v[0], mean_trade_ret_pct=round(v[1] / v[0] * 100, 2)) for k, v in tt.items()}
for k in ("corr", "portfolio_test_monthly", "ytd_2026_pct", "oct_2026_pct", "usdt500", "test_types"):
    print(k, OUT[k])
print("test months", {k: v for k, v in months.items() if k >= "2024-11"})
OUT["runtime_s"] = round(time.time() - t_start)
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
