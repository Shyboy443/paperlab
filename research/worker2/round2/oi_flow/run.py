"""oi_flow: the ONE TEST run of the PREREG.md config + 8 checks + Worker 1 correlation + 500 USDT feasibility.
    python run.py   -> results.json, run.log
"""
import json
from datetime import datetime, timezone
import numpy as np
import core as C

P = C.P
SEL = dict(k=7, q=0.2, h=3, signs={"UU": 1, "DU": -1})
NEIGH = [dict(SEL, k=k) for k in (4, 5, 9, 10)] + [dict(SEL, q=q) for q in (0.1, 0.15, 0.25, 0.3)] + \
        [dict(SEL, h=h) for h in (1, 2, 4, 5)]
ALL_MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]
DEV, TEST, FULL = (C.DEV_START, C.DEV_END), (C.TEST_START, C.TEST_END), (C.DEV_START, C.TEST_END)


def W_of(cfg, rand=None):
    return C.weights(cfg["k"], cfg["q"], cfg["h"], cfg["signs"], rand=rand)


def tstat(v):
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def boot5(v, n=5000, b=3, seed=7):
    rng = np.random.default_rng(seed); L = len(v); out = []
    for _ in range(n):
        st = rng.integers(0, L - b + 1, size=L // b + 1)
        out.append(np.concatenate([v[x:x + b] for x in st])[:L].mean())
    return float(np.percentile(out, 5))


def stats(r, months=None, tpm_months=None):
    """median / geometric / mean month first. months: restrict to these keys (else all months of r)."""
    mk = months or list(r["months"])
    v = np.array([r["months"].get(m, 0.0) for m in mk])
    g = np.prod(1 + v / 100)
    return {"median_month": round(float(np.median(v)), 2), "geo_month": round(float((g ** (1 / len(v)) - 1) * 100), 2),
            "mean_month": round(float(v.mean()), 2), "max_dd_pct": r["max_dd_pct"], "net_pct": round(float((g - 1) * 100), 2),
            "t_stat": round(tstat(v), 2), "n_months": len(v), "months_up": int((v > 0).sum()),
            "worst_month": round(float(v.min()), 2), "best_month": round(float(v.max()), 2),
            "positions": r.get("trades"), "positions_per_month": round(r.get("trades", 0) / (tpm_months or len(v)), 1)}


def months_between(a, b):
    return [m for m in ALL_MONTHS if a <= m <= b]


log = open("run.log", "w")


def say(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); log.write(s + "\n"); log.flush()


Wsel = W_of(SEL)
dev = C.run(Wsel, *DEV); dev2 = C.run(Wsel, *DEV, cost_mult=2)
test = C.run(Wsel, *TEST); test2 = C.run(Wsel, *TEST, cost_mult=2)
full = C.run(Wsel, *FULL, record=True); full2 = C.run(Wsel, *FULL, cost_mult=2)
DEVM, TESTM = months_between("2021-12", "2024-10"), months_between("2024-11", "2026-10")
table = {"DEV_2021-12..2024-10": stats(dev, DEVM), "TEST": stats(test, TESTM), "FULL_2021-01..2026-10(70m, 11 flat)": stats(full, ALL_MONTHS),
         "FULL_active_2021-12..2026-10": stats(full, months_between("2021-12", "2026-10")),
         "DEV_2x_cost": stats(dev2, DEVM), "TEST_2x_cost": stats(test2, TESTM), "FULL_2x_cost": stats(full2, ALL_MONTHS)}
full2_2025 = stats(full2, months_between("2025-01", "2026-10"))
ytd = stats(full, months_between("2026-01", "2026-10"))
for k_, v_ in table.items():
    say(k_, v_)
say("2025-01..2026-10 at 2x cost", full2_2025); say("2026 YTD", ytd, "Oct 2026", full["months"].get("2026-10"))

v = np.array([full["months"].get(m, 0.0) for m in ALL_MONTHS])
chk = {}
chk[1] = dict(pass_=bool(dev["net_pct"] > 0 and test["net_pct"] > 0 and dev2["net_pct"] > 0 and test2["net_pct"] > 0),
              dev=dev["net_pct"], test=test["net_pct"], dev_2x=dev2["net_pct"], test_2x=test2["net_pct"])
yrs = {y: round((np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m.startswith(str(y))]) - 1) * 100, 2)
       for y in range(2021, 2027)}
ex21 = (np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m >= "2022-01"]) - 1) * 100
chk[2] = dict(pass_=bool(sum(x > 0 for x in yrs.values()) >= 4 and ex21 > 0), years=yrs, from_2022=round(ex21, 2),
              note="2021 = December only (metrics start 2021-12-01)")
t = tstat(v); b5 = boot5(v)
chk[3] = dict(pass_=bool(t >= 2 and b5 > 0), t_stat_70m=round(t, 2), boot5_mean_month=round(b5, 3))
srt = np.argsort(-v); net3 = (np.prod(1 + np.delete(v, srt[:3]) / 100) - 1) * 100
chk[4] = dict(pass_=bool(net3 > 0), net_without_best3=round(net3, 2), best3={ALL_MONTHS[i]: round(float(v[i]), 2) for i in srt[:3]})
nb = []
for cfg in NEIGH:
    Wn = W_of(cfg)
    d = C.run(Wn, *DEV)["net_pct"]; te = C.run(Wn, *TEST)["net_pct"]
    nb.append(dict(k=cfg["k"], q=cfg["q"], h=cfg["h"], dev=d, test=te, ok=bool(d > 0 and te > 0))); say("neighbour", nb[-1])
frac = sum(x["ok"] for x in nb) / len(nb)
chk[5] = dict(pass_=bool(frac >= 0.7), share_ok=round(frac, 3), neighbours=nb)
rng = np.random.default_rng(42); rnd = [C.run(W_of(SEL, rand=rng), *FULL)["net_pct"] for _ in range(300)]
beat = float(np.mean(np.array(rnd) < full["net_pct"]))
chk[6] = dict(pass_=bool(beat >= 0.95), beats_share=round(beat, 3), random_median=round(float(np.median(rnd)), 2),
              random_p95=round(float(np.percentile(rnd, 95)), 2), n_random=len(rnd))
pos = full["_positions"]; by_coin = {}
for j, side, o, c, pnl in pos:
    by_coin[j] = by_coin.get(j, 0) + pnl
top5 = sorted(by_coin, key=lambda j: -by_coin[j])[:5]
ex = C.run(Wsel, *FULL, exclude=top5)
tot = sum(p[4] for p in pos); maxpos = max(p[4] for p in pos)
chk[7] = dict(pass_=bool(ex["net_pct"] > 0 and tot > 0 and maxpos / tot <= 0.10), top5=[P.syms[j] for j in top5],
              net_without_top5=ex["net_pct"], max_single_position_share=round(maxpos / tot, 4) if tot > 0 else None)
arith = float(v.mean()); geo = float((np.prod(1 + v / 100) ** (1 / len(v)) - 1) * 100)
chk[8] = dict(pass_=bool(geo > 0 and geo >= 0.5 * arith), geo=round(geo, 3), arith=round(arith, 3))
npass = sum(c["pass_"] for c in chk.values())
for k_, c in chk.items():
    say("check", k_, "PASS" if c["pass_"] else "FAIL", {a: b for a, b in c.items() if a != "neighbours"})

# ---- Worker 1 correlation
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
w1day = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")["day"].astype(np.int64)
cv = full["_curve"]; eq = np.array([e for _, e in cv]); dr = eq[1:] / eq[:-1] - 1
my_day = {int(cv[i + 1][0] - C.G.STEP + 1): dr[i] for i in range(len(dr))}
w1d = {int(t_): r for t_, r in zip(w1day, rb) if np.isfinite(r)}
cd = sorted(set(my_day) & set(w1d))
a = np.array([my_day[x] for x in cd]); b = np.array([w1d[x] for x in cd])
mk = np.array([datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m") for x in cd])
corr = {}
for lab, lo in (("2022_2026", "2022-01"), ("test", "2024-11")):
    mm = [m for m in ALL_MONTHS if m >= lo and m in w1m]
    corr["monthly_" + lab] = round(float(np.corrcoef([full["months"].get(m, 0) for m in mm], [w1m[m] for m in mm])[0, 1]), 3)
    s = mk >= lo
    corr["daily_" + lab] = round(float(np.corrcoef(a[s], b[s])[0, 1]), 3)
corr["n_common_days"] = len(cd)
say("worker1 corr", corr)

# ---- 500 USDT feasibility: every non-zero weight change x 500 (equity drift ignored)
i0, i1 = np.searchsorted(P.t, C.DEV_START), np.searchsorted(P.t, C.TEST_END)
dW = np.abs(np.diff(Wsel[i0 - 1:i1], axis=0)); orders = dW[dW > 1e-12] * 500
feas = dict(min_order_usdt=round(float(orders.min()), 2), p1_order_usdt=round(float(np.percentile(orders, 1)), 2),
            median_order_usdt=round(float(np.median(orders)), 2), share_below_5usdt=round(float((orders < 5).mean()), 4),
            orders_per_month=round(len(orders) / 59, 1),
            max_coins_held=int((np.abs(Wsel[i0:i1]) > 0).sum(1).max()), mean_coins_held=round(float((np.abs(Wsel[i0:i1]) > 0).sum(1).mean()), 1))
say("500 USDT", feas)

bar = dict(a_8of8=npass == 8, b_test_median=table["TEST"]["median_month"] >= 2.22, b_test_geo=table["TEST"]["geo_month"] >= 3.02,
           c_corr=abs(corr["monthly_2022_2026"]) <= 0.3, d_500usdt=feas["share_below_5usdt"] < 0.01)
verdict = "REPLACE" if all(bar.values()) else "NO"
say("bar", bar, verdict)
out = {"agent": "w2r2_oi_flow", "selected": SEL, "prereg": "PREREG.md", "table_1x": table, "full_2x_cost_2025_2026": full2_2025,
       "ytd_2026": ytd, "oct_2026_pct": full["months"].get("2026-10"), "years_1x": yrs,
       "checks": {str(k_): {("pass" if a_ == "pass_" else a_): b_ for a_, b_ in c.items()} for k_, c in chk.items()},
       "checks_passed": npass, "robust": npass == 8, "monthly_1x": {m: round(float(x), 2) for m, x in zip(ALL_MONTHS, v)},
       "worker1_corr": corr, "feasibility_500usdt": feas, "replacement_bar": bar, "verdict": verdict,
       "random_nets": [round(x, 2) for x in rnd]}
json.dump(out, open("results.json", "w"), indent=1, default=float)
