"""DEV-only grid (2021-01..2024-10). Writes dev_grid.json. TEST is never touched here."""
import json, itertools, numpy as np, core
D = core.load()
grid = []
for zn, th, sh in itertools.product((60, 90, 180), (0.0, 0.5, 1.0), (True, False)):
    grid.append(dict(fam="vrp", zn=zn, th=th, short=sh, rv_n=30))
for k, th, sh, ct in itertools.product((1, 3, 7, 14), (0.0, 0.05, 0.10), (True, False), (0, 1)):
    grid.append(dict(fam="ivchg", k=k, th=th, short=sh, contra=ct))
for zn, th, sh, ct in itertools.product((90, 180, 365), (0.0, 0.5, 1.0), (True, False), (0, 1)):
    grid.append(dict(fam="ivlvl", zn=zn, th=th, short=sh, contra=ct))
for n, zn, g, go, sh in itertools.product((20, 50, 100), (90, 180), (-0.5, 0.0, 0.5), ("vrp", "iv"), (True, False)):
    grid.append(dict(fam="trend_iv", n=n, zn=zn, g=g, gate_on=go, short=sh, rv_n=30))
grid.append(dict(fam="trend_iv", n=50, zn=90, g=-99, gate_on="vrp", short=True))   # plain trend, no IV gate (reference)
grid.append(dict(fam="trend_iv", n=50, zn=90, g=-99, gate_on="vrp", short=False))
grid.append(dict(fam="trend_iv", n=100, zn=90, g=-99, gate_on="vrp", short=False))
res = []
for p in grid:
    b = core.brief(core.simulate(D, p, core.DEV_START, core.DEV_END))
    b2 = core.brief(core.simulate(D, p, core.DEV_START, core.DEV_END, cost_mult=2))
    row = dict(p=p, **{k: (float(v) if v is not None else None) for k, v in b.items() if k != "months"}, geo_2x=float(b2["geo_month"]))
    res.append(row)
res.sort(key=lambda r: -r["t_stat"])
json.dump(res, open("dev_grid.json", "w"), indent=1)
for r in res[:30]:
    print(r["p"], "| med", r["median_month"], "geo", r["geo_month"], "mean", r["mean_month"], "up", r["months_up"], "dd", r["max_dd_pct"], "t", r["t_stat"], "tr/mo", r["trades_per_month"], "geo2x", r["geo_2x"])
print("n configs", len(res), "positive geo", sum(r["geo_month"] > 0 for r in res))
for fam in ("vrp", "ivchg", "ivlvl", "trend_iv"):
    f = [r for r in res if r["p"]["fam"] == fam]
    print(fam, len(f), "pos", sum(r["geo_month"] > 0 for r in f), "best t", max(r["t_stat"] for r in f), "median t", float(np.median([r["t_stat"] for r in f])))
