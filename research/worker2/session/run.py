"""Worker 2 / session: pre-registered rule P (vol-scaled sizes, PREREG.md) through the 8 ROBUSTNESS checks,
neighbours N1/N2, year-by-year, Worker-1 correlation and 50/50 combo. Writes run_out.json (merged into results.json)."""
import json
import numpy as np
from datetime import datetime, timezone
from core import *

SIG_P, SIG_N1, SIG_N2 = 0.01154, 0.00834, 0.01551
P = dict(SEL, sig_ref=SIG_P)
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "months_up", "n_months", "net_pct", "trades", "t_stat")


def pick(s):
    return {k: s.get(k) for k in KEYS}


d25 = dayidx(int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
OUT = {}


def windows(p):
    a = {}
    for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1))):
        for cm, sfx in ((1, ""), (2, "_double_cost")):
            c, tr, _, _ = sim(p, x, y, cost_mult=cm)
            a[tag + sfx] = stats(c, tr)
    return a


def check7(p, c, tr):
    pc = {}
    for t in tr:
        pc[t[1]] = pc.get(t[1], 0) + t[3]
    top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
    c7, _, _, _ = sim(p, d0, d1, exclude=set(top5))
    net7 = summary(c7)["net_pct"]
    total = c[-1][1] - c[0][1]
    i = int(np.argmax([t[3] for t in tr]))
    share = tr[i][3] / total
    lg = np.array([np.log1p(t[4]) for t in tr])
    lshare = lg.max() / lg.sum()
    return {"pass": bool(net7 > 0 and share <= 0.10), "numbers": {
        "top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7,
        "max_single_position_share_of_profit": round(float(share), 4),
        "max_position": {"coin": syms[tr[i][1]], "day": datetime.fromtimestamp(td[tr[i][0]] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                         "pct_of_equity": round(tr[i][4] * 100, 2), "size_pct_of_equity": round(tr[i][5] * 100, 2),
                         "ret_pct": round(tr[i][6] * 100, 2)},
        "max_single_position_pct_of_equity": round(max(t[4] for t in tr) * 100, 2),
        "info_max_share_of_log_growth": round(float(lshare), 4)}}


def all_checks(p, A):
    full = A["full"]
    months = full["months"]
    v = np.array(list(months.values()))
    chk = {}
    c1 = {k: A[k]["net_pct"] for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
    chk[1] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
    yr = yearly(months)
    m22 = np.prod([1 + x / 100 for k, x in months.items() if k >= "2022-01"]) - 1
    chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0),
              "numbers": {"years": yr, "2022-01..2026-10_pct": round(m22 * 100, 2)}}
    rng = np.random.default_rng(7)
    nb_ = int(np.ceil(len(v) / 3))
    boots = []
    for _ in range(5000):
        st = rng.integers(0, len(v) - 2, nb_)
        boots.append(np.concatenate([v[s:s + 3] for s in st])[:len(v)].mean())
    p5 = float(np.percentile(boots, 5))
    tstat = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
    chk[3] = {"pass": tstat >= 2 and p5 > 0, "numbers": {"t_stat": round(tstat, 2), "boot_p5_mean_month": round(p5, 2), "n_months": len(v)}}
    rest = sorted(v)[:-3]
    g4 = np.prod([1 + x / 100 for x in rest]) - 1
    chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2), "best3": [float(x) for x in sorted(v)[-3:]]}}
    nbr = []
    for n, q in neighbours(p):
        cd, _, _, _ = sim(q, d0, dT)
        ct, _, _, _ = sim(q, dT, d1)
        sd, st_ = summary(cd), summary(ct)
        nbr.append({"name": n, "dev_net": sd["net_pct"], "test_net": st_["net_pct"],
                    "dev_median": sd["median_month"], "test_median": st_["median_month"]})
    share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
    chk[5] = {"pass": bool(share >= 0.7), "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}
    c, tr, _, rows = sim(p, d0, d1)
    strat_net = summary(c)["net_pct"]
    V = hv(p["W"])
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    rand = []
    for rep in range(200):
        rr = []
        for d, rs in by.items():
            pool = [j for j in ALT_ELIG[d] if np.isfinite(O[rs[0][1], j])]
            pk = rng.choice(pool, size=min(len(rs), len(pool)), replace=False)
            rr += [(d, r[1], r[2], int(j), r[4], size_mult(p, V[d - d0, j])) for r, j in zip(rs, pk)]
        cr, _, _ = run(rr, d0, d1, p["K"])
        rand.append(summary(cr)["net_pct"])
    pct = float(np.mean(np.array(rand) < strat_net))
    chk[6] = {"pass": pct >= 0.95, "numbers": {"strategy_net_pct": strat_net, "beats_share": round(pct, 3),
              "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
    chk[7] = check7(p, c, tr)
    chk[8] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
              "numbers": {"geo_month": full["geo_month"], "mean_month": full["mean_month"]}}
    return {str(k): x for k, x in chk.items()}


A = windows(P)
chk = all_checks(P, A)
OUT["primary"] = {"name": "P vol-scaled sig_ref=1.154%", "params": P, "windows": {k: pick(v) for k, v in A.items()},
                  "years_1x": yearly(A["full"]["months"]), "years_2x_cost": yearly(A["full_double_cost"]["months"]),
                  "monthly_1x": A["full"]["months"], "checks": chk, "checks_passed": int(sum(x["pass"] for x in chk.values()))}
OUT["primary"]["robust"] = OUT["primary"]["checks_passed"] == 8
# no 1%-of-volume cap: a 500 USDT account never hits it; the 10k -> huge compounding curve does
c_unc, tr_unc, _ = run(signals(P, d0, d1), d0, d1, 5, liq_cap=False)
OUT["primary"]["full_no_volume_cap"] = pick(stats(c_unc, tr_unc))
for nm, sg in (("N1 sig_ref=0.834%", SIG_N1), ("N2 sig_ref=1.551%", SIG_N2)):
    q = dict(SEL, sig_ref=sg)
    B = windows(q)
    c, tr, _, _ = sim(q, d0, d1)
    OUT[nm] = {"windows": {k: pick(v) for k, v in B.items()}, "years_1x": yearly(B["full"]["months"]), "check7": check7(q, c, tr)}
B = windows(SEL)
OUT["r4_09_original"] = {"windows": {k: pick(v) for k, v in B.items()}, "years_1x": yearly(B["full"]["months"]),
                         "years_2x_cost": yearly(B["full_double_cost"]["months"])}

# ---------------- Worker 1 correlation + 50/50
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")
w1day = z["day"].astype(np.int64)
mk = np.array([datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in w1day])
w1m_from_daily = {m: round((np.prod(1 + np.nan_to_num(rb[mk == m])) - 1) * 100, 2) for m in w1m}
OUT["w1_daily_vs_monthly_maxabsdiff"] = round(max(abs(w1m_from_daily[m] - w1m[m]) for m in w1m), 3)
eq = np.array([e for _, e in c_unc])
rP = eq[1:] / eq[:-1] - 1
dP = {int(td[d]): rP[i] for i, d in enumerate(range(d0, d0 + len(rP)))}
w1d = {int(t): r for t, r in zip(w1day, rb) if np.isfinite(r)}
common_days = sorted(set(dP) & set(w1d))
a = np.array([dP[t] for t in common_days])
b = np.array([w1d[t] for t in common_days])
mkey = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in common_days]
m22 = np.array([m >= "2022-01" for m in mkey])
tm = np.array([m >= "2024-11" for m in mkey])


def mstats(mon):
    v = np.array(list(mon.values()))
    g = np.cumprod(1 + v / 100)
    dd = (1 - g / np.maximum.accumulate(np.r_[1, g])[1:]).max()
    return {"mean_month": round(float(v.mean()), 2), "median_month": round(float(np.median(v)), 2),
            "geo_month": round(float(g[-1] ** (1 / len(v)) - 1) * 100, 2), "worst_month": round(float(v.min()), 2),
            "months_up": int((v > 0).sum()), "n_months": len(v), "net_pct": round(float(g[-1] - 1) * 100, 1),
            "max_dd_monthly_pct": round(float(dd) * 100, 2)}


def daily_combo(x, y, keys):
    r = 0.5 * x + 0.5 * y
    g = np.cumprod(1 + r)
    dd = (1 - g / np.maximum.accumulate(g)).max()
    mon = {}
    for k, ri in zip(keys, r):
        mon[k] = mon.get(k, 1.0) * (1 + ri)
    s = mstats({k: (v - 1) * 100 for k, v in mon.items()})
    s["max_dd_daily_pct"] = round(float(dd) * 100, 2)
    return s


pm = {}
for k, ri in zip(mkey, a):
    pm[k] = pm.get(k, 1.0) * (1 + ri)
pm = {k: (v - 1) * 100 for k, v in pm.items()}
ms = sorted(set(pm) & set(w1m))
corr = {}
for lab, lo in (("full_2021_2026", "2021-01"), ("2022_2026", "2022-01"), ("test", "2024-11")):
    mm = [m for m in ms if m >= lo]
    corr["monthly_" + lab] = round(float(np.corrcoef([pm[m] for m in mm], [w1m[m] for m in mm])[0, 1]), 3)
corr["daily_full"] = round(float(np.corrcoef(a, b)[0, 1]), 3)
corr["daily_2022_2026"] = round(float(np.corrcoef(a[m22], b[m22])[0, 1]), 3)
corr["daily_test"] = round(float(np.corrcoef(a[tm], b[tm])[0, 1]), 3)
OUT["worker1"] = {"corr": corr, "n_days": len(common_days), "note": "P daily book without the 1% volume cap (small account)"}
combo = {}
for lab, sel in (("full", np.ones(len(a), bool)), ("2022_2026", m22), ("test", tm)):
    keys = [k for k, s in zip(mkey, sel) if s]
    mon_c = {m: 0.5 * pm[m] + 0.5 * w1m[m] for m in sorted(set(keys))}
    combo[lab] = {"P_alone": daily_combo(a[sel], a[sel], keys), "W1_alone": daily_combo(b[sel], b[sel], keys),
                  "combo_50_50_daily_rebal": daily_combo(a[sel], b[sel], keys),
                  "combo_50_50_monthly_rebal": mstats(mon_c)}
OUT["worker1"]["combo"] = combo
json.dump(OUT, open("run_out.json", "w"), indent=1, default=float)

pr = OUT["primary"]
for k, v in pr["windows"].items():
    print(k, v)
print("years", pr["years_1x"], "2x", pr["years_2x_cost"])
for k, x in pr["checks"].items():
    print("check", k, x["pass"], {a_: b_ for a_, b_ in x["numbers"].items() if a_ != "grid"})
for x in pr["checks"]["5"]["numbers"]["grid"]:
    print("  nb", x)
print("nocap", pr["full_no_volume_cap"])
for nm in ("N1 sig_ref=0.834%", "N2 sig_ref=1.551%"):
    print(nm, OUT[nm]["windows"]["full"], OUT[nm]["windows"]["test"], OUT[nm]["check7"])
print("orig years", OUT["r4_09_original"]["years_1x"], OUT["r4_09_original"]["years_2x_cost"], OUT["r4_09_original"]["windows"]["y2025_26_double_cost"])
print("w1 check", OUT["w1_daily_vs_monthly_maxabsdiff"])
print(json.dumps(OUT["worker1"], indent=0))
