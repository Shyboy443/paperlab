"""r4_09_session: conditional intraday momentum in alts (strong 00-16 UTC movers keep going into the Asia open).

    python run.py dev    # DEV-only numbers used to pick the config (never touches TEST)
    python run.py        # the one pre-registered TEST run + all 8 robustness checks + leverage table -> results.json

Rule IM(H=16, k=2, X=26, K=5, W=480) on the survivorship-free top-40 perps (BTC/ETH excluded for the ALT version):
each UTC day d, z = log(close of hour H-1 / open of hour 0) / (std of hourly log returns over the previous W hours * sqrt(H)).
If |z| >= k: enter at the open of hour H in the direction of the move, exit at the open of hour X (X=26 = 02:00 next day).
Each position gets equity/max(n, K) (1x, isolated). Costs 0.05% + 0.02% per side, funding stamps inside the hold.
A trade is booked on its signal day d (it covers 16:00 d .. 02:00 d+1)."""
import json, sys
import numpy as np
from base import *
from engine import run
from common import summary, TEST_START, TEST_END, DEV_START, DEV_END

HERE = __import__("pathlib").Path(__file__).resolve().parent
DEV_ONLY = len(sys.argv) > 1 and sys.argv[1] == "dev"
SEL = dict(H=16, k=2.0, X=26, K=5, W=480, uni="ALT", mode="mom")
d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
if DEV_ONLY:
    d1 = dT
ALT_ELIG = [np.array([j for j in np.where(U40[d])[0] if j not in (BTC, ETH)], dtype=int) for d in range(len(td))]
_hv = {}


def hv(W):
    if W not in _hv:
        _hv[W] = hvol(np.arange(d0 * 24, min(d1 * 24, len(th)), 24), W)  # vol known at each day's 00:00 open
    return _hv[W]


def signals(p, d_from, d_to, exclude=()):
    H_, k, X, W = p["H"], p["k"], p["X"], p["W"]
    V = hv(W)
    rows = []
    for d in range(d_from, d_to):
        b = d * 24
        if b + X >= len(th):
            break
        js = np.array([BTC, ETH]) if p["uni"] == "BE" else ALT_ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        if len(js) == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            z = np.log(C[b + H_ - 1, js] / O[b, js]) / (V[d - d0, js] * np.sqrt(H_))
        ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[b + H_, js])  # no filter on the exit bar (no look-ahead)
        for j, zz in zip(js[ok], z[ok]):
            rows.append((d, b + H_, b + X, int(j), np.sign(zz) * (1 if p["mode"] == "mom" else -1)))
    return rows


def sim(p, a, b, lev=1.0, cost_mult=1.0, exclude=()):
    rows = signals(p, a, b, exclude)
    c, tr, liq = run(rows, a, b, p["K"], lev=lev, cost_mult=cost_mult)
    return c, tr, liq, rows


def stats(c, tr):
    s = summary(c, [t[3] for t in tr])
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))), 2) if len(v) > 2 else None
    return s


def yearly(months):
    out = {}
    for k_, v in months.items():
        out.setdefault(k_[:4], 1.0)
        out[k_[:4]] *= 1 + v / 100
    return {y: round((g - 1) * 100, 2) for y, g in out.items()}


def neighbours():
    g = []
    for name, vals in (("H", (8, 12, 20, 24)), ("k", (1.0, 1.5, 2.5, 3.0)), ("hold", (5, 8, 12, 15)),
                       ("K", (3, 4, 6, 8)), ("W", (240, 360, 600, 720))):
        for v in vals:
            p = dict(SEL)
            if name == "hold":
                p["X"] = p["H"] + v
            elif name == "H":
                p["H"] = v; p["X"] = v + (SEL["X"] - SEL["H"])  # keep the 10 h hold
            else:
                p[name] = v
            g.append((f"{name}={v}", p))
    return g


R = {"agent": "r4_09_session", "idea": "Alts (point-in-time top-40 perps incl. delisted, ex BTC/ETH) that moved >= 2 vol-units "
     "between 00:00 and 16:00 UTC keep moving: ride the move from 16:00 to 02:00 UTC next day (US afternoon + Asia open).",
     "selected": SEL, "attempts": []}

if DEV_ONLY:
    out = {}
    for lev in (1, 2, 3):
        c, tr, liq, _ = sim(SEL, d0, dT, lev=lev); s = stats(c, tr); s["liquidations"] = liq; out[f"dev_{lev}x"] = s
    c, tr, _, _ = sim(SEL, d0, dT, cost_mult=2); out["dev_2xcost"] = stats(c, tr)
    pb = dict(SEL, uni="BE", K=2); c, tr, _, _ = sim(pb, d0, dT); out["dev_BE"] = stats(c, tr)
    nb = []
    for n, p in neighbours():
        c, tr, _, _ = sim(p, d0, dT); s = stats(c, tr); nb.append((n, s["net_pct"], s["mean_month"], s["geo_month"]))
    out["dev_neighbours"] = nb
    for k_, v in out.items():
        if isinstance(v, dict):
            print(k_, {x: v[x] for x in ("mean_month", "median_month", "geo_month", "max_dd_pct", "worst_month", "net_pct", "t_stat", "trades") if x in v}, v.get("liquidations", ""))
        else:
            for row in v: print(k_, row)
    json.dump(out, open(HERE / "dev_selected.json", "w"), indent=1, default=float)
    sys.exit()


# ---------------------------------------------------------------- the one TEST run + checks
def attempt(name, p, prereg=True):
    a = {"name": name, "prereg": prereg, "params": p}
    for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1))):
        for cm, sfx in ((1, ""), (2, "_double_cost")):
            c, tr, liq, _ = sim(p, x, y, cost_mult=cm)
            a[tag + sfx] = stats(c, tr)
    return a


A = attempt("IM_ALT_H16_k2_X26 (selected)", SEL)
B = attempt("IM_BE_H16_k2_X26 (BTC/ETH version)", dict(SEL, uni="BE", K=2))
R["attempts"] = [A, B]
full = A["full"]; months = full["months"]; v = np.array(list(months.values()))
chk = {}
# 1 both halves
c1 = {k_: A[k_]["net_pct"] for k_ in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
# 2 most years
yr = yearly(months); m22 = np.prod([1 + x / 100 for k_, x in months.items() if k_ >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022-01..2026-10_pct": round(m22 * 100, 2)}}
# 3 statistics
rng = np.random.default_rng(7)
nb_ = int(np.ceil(len(v) / 3)); boots = []
for _ in range(5000):
    st = rng.integers(0, len(v) - 2, nb_)
    boots.append(np.concatenate([v[s:s + 3] for s in st])[:len(v)].mean())
p5 = float(np.percentile(boots, 5)); tstat = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
chk[3] = {"pass": tstat >= 2 and p5 > 0, "numbers": {"t_stat": round(tstat, 2), "boot_p5_mean_month": round(p5, 2), "n_months": len(v)}}
# 4 drop 3 best months
rest = sorted(v)[:-3]; g4 = np.prod([1 + x / 100 for x in rest]) - 1
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2), "best3": [float(x) for x in sorted(v)[-3:]]}}
# 5 neighbours
nbr = []
for n, p in neighbours():
    cd, _, _, _ = sim(p, d0, dT); ct, _, _, _ = sim(p, dT, d1)
    sd, st_ = summary(cd), summary(ct)
    nbr.append({"name": n, "dev_net": sd["net_pct"], "test_net": st_["net_pct"], "dev_mean": sd["mean_month"], "test_mean": st_["mean_month"]})
share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": share >= 0.7, "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}
# 6 random coin picks: same days, same number of longs/shorts per day, same windows/costs, random alts from that day's universe
c, tr, _, rows = sim(SEL, d0, d1)
strat_net = summary(c)["net_pct"]
by = {}
for r in rows:
    by.setdefault(r[0], []).append(r)
rand = []
for rep in range(200):
    rr = []
    for d, rs in by.items():
        pool = [j for j in ALT_ELIG[d] if np.isfinite(O[rs[0][1], j])]
        pick = rng.choice(pool, size=min(len(rs), len(pool)), replace=False)
        rr += [(d, r[1], r[2], int(j), r[4]) for r, j in zip(rs, pick)]
    cr, _, _ = run(rr, d0, d1, SEL["K"]); rand.append(summary(cr)["net_pct"])
pct = float(np.mean(np.array(rand) < strat_net))
chk[6] = {"pass": pct >= 0.95, "numbers": {"strategy_net_pct": strat_net, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
# 7 top-5 coins removed; single position share
pc = {}
for t in tr:
    pc[t[1]] = pc.get(t[1], 0) + t[3]
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
c7, tr7, _, _ = sim(SEL, d0, d1, exclude=set(top5))
net7 = summary(c7)["net_pct"]; total = c[-1][1] - c[0][1]
maxpos = max(t[3] for t in tr) / total if total > 0 else None
maxpos_eqpct = max(t[4] for t in tr) * 100
chk[7] = {"pass": bool(net7 > 0 and maxpos is not None and maxpos <= 0.10),
          "numbers": {"top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7,
                      "max_single_position_share_of_profit": round(maxpos, 4) if maxpos else None,
                      "max_single_position_pct_of_equity": round(maxpos_eqpct, 2)}}
# 8 compounding
chk[8] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
          "numbers": {"geo_month": full["geo_month"], "mean_month": full["mean_month"]}}
R["checks"] = {str(k_): x for k_, x in chk.items()}
R["checks_passed"] = int(sum(x["pass"] for x in chk.values()))
R["robust"] = R["checks_passed"] == 8
R["monthly_1x"] = months
# leverage table (TEST and full), liquidation + 1% volume cap modelled
lt = []
for lev in (1, 1.5, 2, 3, 4, 5, 6, 8, 10):
    row = {"lev": lev}
    for tag, (x, y) in (("test", (dT, d1)), ("full", (d0, d1))):
        c, tr, liq, _ = sim(SEL, x, y, lev=lev); s = summary(c)
        row[tag] = {k_: s[k_] for k_ in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct")}
        row[tag]["liquidations"] = liq
    lt.append(row)
R["leverage_table"] = lt
need = [r["lev"] for r in lt if r["test"]["mean_month"] >= 20]
R["leverage_needed_for_20pct"] = need[0] if need else None
R["ytd_2026_1x"] = {k_: x for k_, x in months.items() if k_.startswith("2026")}
R["ytd_2026_1x_total_pct"] = round((np.prod([1 + x / 100 for x in R["ytd_2026_1x"].values()]) - 1) * 100, 2)
R["best_honest"] = A["name"]
R["verdict"] = "PASS" if A["test"]["mean_month"] >= 20 and A["test_double_cost"]["mean_month"] > 0 else "FAIL"
json.dump(R, open(HERE / "results.json", "w"), indent=1, default=float)
for at in R["attempts"]:
    for tag in ("dev", "test", "full", "test_double_cost"):
        s = at[tag]; print(at["name"], tag, {x: s[x] for x in ("mean_month", "median_month", "geo_month", "max_dd_pct", "worst_month", "net_pct", "t_stat", "trades")})
for k_, x in chk.items():
    print("check", k_, x["pass"], {a: b for a, b in x["numbers"].items() if a != "grid"})
for x in chk[5]["numbers"]["grid"]:
    print("  nb", x)
for r in lt:
    print(r)
print("lev for 20%:", R["leverage_needed_for_20pct"], "YTD", R["ytd_2026_1x"], R["ytd_2026_1x_total_pct"])
print("TEST months", A["test"]["months"])
