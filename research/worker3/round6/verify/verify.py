"""Worker 3 (built rule, unchanged): long-term stability, luck tests and a 200 USDT run. Nothing is chosen here.

- one continuous perp run 2021-01..2026-10 and the 2018-2020 spot holdout, at 10k and at 200 USDT with Binance minimums
- per year, rolling 12-month windows, longest time under water
- luck: block bootstrap of daily returns; deflated Sharpe for the number of configs tried across rounds 1-6
"""
import json, math, sys
from pathlib import Path
from statistics import NormalDist
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "execution"))
import lib6 as L  # noqa: E402
from lib6 import DEV_START, TEST_END, HO_START, HO_END, DAY  # noqa: E402

SEL = json.loads((HERE.parents[1] / "round5/high52_plus/dev.json").read_text())["selected"]["cfg"]
N01 = NormalDist()


def run(Pn, s, e, eq0=10_000.0, cm=1.0, **over):
    c = dict(SEL); c.update(over)
    return L.run5(Pn, c, s, e, cost_mult=cm, equity0=eq0)


def daily(curve):
    t = np.array([x[0] for x in curve], np.int64); e = np.array([x[1] for x in curve], float)
    return t, e


def months(t, e):
    import datetime as dt
    key = [dt.datetime.utcfromtimestamp((x - 1) / 1000).strftime("%Y-%m") for x in t]
    last = {}
    for k, v in zip(key, e):
        last[k] = v
    ks = sorted(last); prev = e[0]; out = {}
    for k in ks:
        out[k] = (last[k] / prev - 1) * 100; prev = last[k]
    return out


def stability(t, e):
    m = months(t, e); ks = list(m); v = np.array([m[k] for k in ks])
    eqm = np.cumprod(1 + v / 100)
    years = {}
    for k, x in m.items():
        years.setdefault(k[:4], []).append(x)
    yr = {y: round((np.prod([1 + a / 100 for a in xs]) - 1) * 100, 1) for y, xs in years.items()}
    r12 = [(np.prod(1 + v[i:i + 12] / 100) - 1) * 100 for i in range(len(v) - 11)]
    r6 = [(np.prod(1 + v[i:i + 6] / 100) - 1) * 100 for i in range(len(v) - 5)]
    pk = np.maximum.accumulate(e); under = e < pk * 0.999
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0; longest = max(longest, cur)
    return dict(months=len(v), median=round(float(np.median(v)), 2),
                geo=round(float((eqm[-1] ** (1 / len(v)) - 1) * 100), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                worst_month=round(float(v.min()), 1), max_dd_daily=round(float((1 - e / pk).max() * 100), 1),
                years=yr, rolling12_positive=f"{sum(x > 0 for x in r12)}/{len(r12)}", worst_12m=round(float(min(r12)), 1),
                rolling6_positive=f"{sum(x > 0 for x in r6)}/{len(r6)}", worst_6m=round(float(min(r6)), 1),
                longest_underwater_days=int(longest))


def luck(e, trials=(100, 500, 1000)):
    r = np.diff(np.log(e)); r = r[np.isfinite(r)]
    T = len(r); sr = r.mean() / r.std(ddof=1)
    g = np.random.default_rng(11); B = 30; nb = math.ceil(T / B)
    tot = []
    for _ in range(5000):
        st = g.integers(0, T - B, nb)
        tot.append(np.concatenate([r[s:s + B] for s in st])[:T].sum())
    tot = np.array(tot)
    sk = float(((r - r.mean()) ** 3).mean() / r.std() ** 3); ku = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)
    vsr = (1 + 0.5 * sr ** 2) / T                                     # variance of the SR estimate (iid approx.)
    em = 0.5772156649
    dsr = {}
    for n in trials:
        sr0 = math.sqrt(vsr) * ((1 - em) * N01.inv_cdf(1 - 1 / n) + em * N01.inv_cdf(1 - 1 / (n * math.e)))
        z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
        dsr[n] = round(N01.cdf(z), 4)
    return dict(days=T, sharpe_annual=round(float(sr * math.sqrt(365)), 2), skew=round(sk, 2), kurt=round(ku, 1),
                boot_p_loss=round(float((tot <= 0).mean()), 4),
                boot_p5_cagr=round(float((math.exp(np.percentile(tot, 5) * 365 / T) - 1) * 100), 1),
                deflated_sharpe_prob=dsr)


if __name__ == "__main__":
    D = L.L4.build(); P = L.L4.make_panel(D)
    H = L.L4.holdout_panel(); HP = L.L4.make_panel(H)
    res = {}
    for name, Pn, s, e in [("perp_2021_2026", P, DEV_START, TEST_END), ("spot_2018_2020", HP, HO_START, HO_END)]:
        for lbl, kw in [("10k", {}), ("200usdt_binance_min", dict(eq0=200.0, binance=True)),
                        ("200usdt_2x_costs", dict(eq0=200.0, binance=True, cm=2.0))]:
            r = run(Pn, s, e, **kw)
            t, eq = daily(r["_curve"])
            row = dict(net_pct=r["net_pct"], liquidations=r["liquidations"], orders_pm=r.get("orders_per_month"),
                       skipped=r.get("xstat", {}).get("skipped_entries"), **stability(t, eq))
            if lbl == "10k":
                row["luck"] = luck(eq)
            res[f"{name}/{lbl}"] = row
            print(name, lbl, json.dumps(row, default=str))
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
