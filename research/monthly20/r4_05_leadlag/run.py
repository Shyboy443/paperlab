"""Round-4 lead-lag agent: reproduces every number after the DEV grid (dev_grid.py).

    python panel.py ; python fetch15.py      # data (survivorship-free archive, cached under data/monthly20_r4_leadlag)
    python explore_dev.py 1h|15m ; python explore2_dev.py 1h|15m ; python dev_grid.py    # DEV only
    python run.py                           # pre-registered configs from prereg.json: TEST once, 8 checks, leverage
Writes results.json.
"""
import sys, json, math
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END, TEST_START, TEST_END, summary, utc
from engine import run, fill_months

FULL0, FULL1 = DEV_START, TEST_END
PR = json.load(open("prereg.json"))


def S(cfg, a, b, **kw):
    R = run(start=a, end=b, **cfg, **kw)
    cv = [(tt, max(e, 1e-9)) for tt, e in R["curve"]]  # a wiped account is floored at 1e-9 (= -100%)
    s = summary(fill_months(cv, a, b), [x[2] for x in R["trades"]])
    s["wiped"] = R["final"] <= 1e-6
    s["liquidations"] = R["liqs"]
    return s, R


def tstat(v):
    v = np.array(v); return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))


def boot5(v, n=5000, blk=3, seed=1):
    v = np.array(v); rng = np.random.default_rng(seed); k = math.ceil(len(v) / blk); out = []
    for _ in range(n):
        st = rng.integers(0, len(v) - blk + 1, k)
        out.append(np.concatenate([v[s:s + blk] for s in st])[:len(v)].mean())
    return float(np.percentile(out, 5))


def comp(vals):
    p = 1.0
    for x in vals: p *= 1 + x / 100
    return (p - 1) * 100


def neighbours(cfg):
    out = []
    for key, mults in (("L", (0.5, 0.75, 1.25, 1.5)), ("z", (0.5, 0.75, 1.25, 1.5)), ("H", (0.5, 0.75, 1.25, 1.5)),
                       ("K", (0.5, 0.75, 1.25, 1.5)), ("rmax", (0.5, 0.75, 1.25, 1.5))):
        seen = {cfg[key]}
        for m in mults:
            v = cfg[key] * m
            v = round(v, 2) if key == "z" else max(1, int(round(v)))
            if v in seen: continue
            seen.add(v); out.append({**cfg, key: v})
    # other "rebalance frequency": the other bar size (same clock-time L and H), and one bar of extra latency
    f = 4 if cfg["tf"] == "1h" else 0.25
    other = {**cfg, "tf": "15m" if cfg["tf"] == "1h" else "1h", "L": max(1, int(round(cfg["L"] * f))), "H": max(1, int(round(cfg["H"] * f)))}
    if other["tf"] == "15m": other["rmax"] = min(cfg["rmax"], 20)  # 15m data only for the top-12 union
    out += [other, {**cfg, "delay": 1}]
    return out


def checks(name, cfg):
    res = {}
    dev, _ = S(cfg, DEV_START, DEV_END); dev2, _ = S(cfg, DEV_START, DEV_END, cost_mult=2)
    test, _ = S(cfg, TEST_START, TEST_END); test2, _ = S(cfg, TEST_START, TEST_END, cost_mult=2)
    full, RF = S(cfg, FULL0, FULL1); full2, _ = S(cfg, FULL0, FULL1, cost_mult=2)
    months = full["months"]; vals = list(months.values())
    c1 = all(x["net_pct"] > 0 for x in (dev, dev2, test, test2))
    res[1] = dict(pass_=c1, numbers={k: x["net_pct"] for k, x in (("dev", dev), ("dev_2x", dev2), ("test", test), ("test_2x", test2))})
    yrs = {y: round(comp([v for k, v in months.items() if k.startswith(str(y))]), 1) for y in range(2021, 2027)}
    no21 = comp([v for k, v in months.items() if k >= "2022-01"])
    res[2] = dict(pass_=sum(v > 0 for v in yrs.values()) >= 4 and no21 > 0, numbers=dict(years=yrs, from_2022=round(no21, 1)))
    ts, b5 = tstat(vals), boot5(vals)
    res[3] = dict(pass_=ts >= 2 and b5 > 0, numbers=dict(t=round(ts, 2), boot5=round(b5, 3), n_months=len(vals)))
    srt = sorted(vals)
    res[4] = dict(pass_=comp(srt[:-3]) > 0, numbers=dict(net_without_3_best=round(comp(srt[:-3]), 1)))
    nb = []
    for nc in neighbours(cfg):
        a, _ = S(nc, DEV_START, DEV_END); b, _ = S(nc, TEST_START, TEST_END)
        nb.append(dict(cfg={k: nc[k] for k in nc if nc[k] != cfg.get(k)}, dev=a["net_pct"], test=b["net_pct"]))
    share = sum(x["dev"] > 0 and x["test"] > 0 for x in nb) / len(nb)
    res[5] = dict(pass_=share >= 0.7, numbers=dict(share=round(share, 3), neighbours=nb))
    rnd = []
    for sd in range(200):
        R = run(start=FULL0, end=FULL1, random_seed=sd, **cfg)
        rnd.append((R["final"] - 1) * 100)
    pct = float(np.mean(np.array(rnd) < full["net_pct"]))
    res[6] = dict(pass_=pct >= 0.95, numbers=dict(beats_share=round(pct, 3), random_median=round(float(np.median(rnd)), 1),
                                                  random_p95=round(float(np.percentile(rnd, 95)), 1), strategy=full["net_pct"]))
    by = {}
    for _, sym, p in RF["trades"]: by[sym] = by.get(sym, 0) + p
    top5 = [s for s, _ in sorted(by.items(), key=lambda x: -x[1])[:5]]
    ex, _ = S({**cfg, "exclude": tuple(top5)}, FULL0, FULL1)
    tot = sum(p for _, _, p in RF["trades"])
    mx = max(p for _, _, p in RF["trades"])
    share1 = mx / tot if tot > 0 else float("inf")
    res[7] = dict(pass_=ex["net_pct"] > 0 and tot > 0 and share1 <= 0.10,
                  numbers=dict(top5=top5, net_without_top5=ex["net_pct"], max_position_share=round(share1, 3) if tot > 0 else None))
    res[8] = dict(pass_=full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"],
                  numbers=dict(geo=full["geo_month"], mean=full["mean_month"]))
    for k in res:
        res[k]["pass"] = bool(res[k].pop("pass_"))
    ytd = comp([v for k, v in months.items() if k.startswith("2026")])
    lev = []
    for L in (1, 1.5, 2, 3, 4, 5, 6, 8, 10):
        a, _ = S(cfg, TEST_START, TEST_END, lev=L); b, _ = S(cfg, FULL0, FULL1, lev=L)
        lev.append(dict(lev=L, test={k: a[k] for k in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct", "liquidations", "wiped")},
                        full={k: b[k] for k in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct", "liquidations", "wiped")}))
    need = next((x["lev"] for x in lev if x["test"]["mean_month"] >= 20), None)
    tstat_dev = tstat(list(dev["months"].values())); tstat_test = tstat(list(test["months"].values()))
    return dict(name=name, cfg=cfg, prereg=True, dev=dev, dev_double_cost=dev2, test=test, test_double_cost=test2, full=full,
                full_double_cost=full2, t_dev=round(tstat_dev, 2), t_test=round(tstat_test, 2), t_full=round(ts, 2),
                checks=res, n_pass=sum(r["pass"] for r in res.values()), robust=all(r["pass"] for r in res.values()),
                ytd_2026=round(ytd, 2), monthly_1x=months, leverage_table=lev, leverage_needed_for_20pct=need)


if __name__ == "__main__":
    out = []
    for a in PR["attempts"]:
        r = checks(a["name"], a["cfg"])
        print(a["name"], "n_pass", r["n_pass"], "test", {k: r["test"][k] for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct")}, flush=True)
        out.append(r)
    best = max(out, key=lambda r: r["test"]["geo_month"])
    verdict = "PASS" if any(r["test"]["mean_month"] >= 20 and r["test_double_cost"]["mean_month"] > 0 for r in out) else "FAIL"
    res = {"agent": "r4_05_leadlag", "idea": PR["idea"],
           "attempts": [{**r, "test_double_cost": r["test_double_cost"]} for r in out],
           "best_honest": PR["attempts"][0]["name"], "verdict": verdict,
           "robust": out[0]["robust"], "leverage_needed_for_20pct": out[0]["leverage_needed_for_20pct"]}
    json.dump(res, open("results.json", "w"), indent=1, default=float)
    print("written results.json", verdict)
