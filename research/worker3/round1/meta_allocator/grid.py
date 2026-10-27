"""DEV grid (Part A of PREREG.md). Prints and saves DEV stats only; TEST masked."""
import json
import numpy as np
from alloc import *

G = [dict(name="bench_5050", kind="fixed", w=[0.5, 0.5], freq=1)]
for L in (30, 60, 120):
    for f in (1, 7):
        G.append(dict(name=f"invvol_L{L}_f{f}", kind="invvol", L=L, freq=f))
for L in (60, 120):
    G.append(dict(name=f"minvar_L{L}", kind="minvar", L=L, shrink=0.5, freq=7))
for L in (30, 60):
    for tg in (0.2, 0.3, 0.4):
        G.append(dict(name=f"voltgt_L{L}_t{tg}", kind="voltarget", L=L, tgt=tg, freq=1))
for L in (30, 60, 120):
    for ti in (0.15, 0.25):
        G.append(dict(name=f"relmom_L{L}_t{ti}", kind="relmom", L=L, tilt=ti, freq=7))
for L in (30, 60, 120):
    for c in (0.0, 0.5):
        G.append(dict(name=f"absmom_L{L}_c{c}", kind="absmom", L=L, cut=c, freq=1))
for X in (0.10, 0.15):
    for c in (0.0, 0.5):
        G.append(dict(name=f"ddcut_X{X}_c{c}", kind="ddcut", X=X, cut=c, freq=1))
for M in (30, 60):
    for c in (0.0, 0.5):
        G.append(dict(name=f"ivmom_M{M}_c{c}", kind="ivmom", L=60, M=M, cut=c, freq=1))
for N in (50, 100, 200):
    G.append(dict(name=f"btc{N}_eq3", kind="fixed", w=[1/3, 1/3, 1/3], btcN=N, freq=1))
    G.append(dict(name=f"btc{N}_iv3", kind="invvol", L=60, btcN=N, freq=7))
if __name__ == "__main__":
    rows = []
    for cfg in G:
        p, turn, gross = run_cfg(cfg)
        s = mstats(p, DEV); s.pop("months")
        s["turn_per_month"] = round(float(turn[DEV].sum() / s["n_months"]), 3)
        s["avg_gross"] = round(float(gross[DEV].mean()), 3)
        rows.append({"cfg": cfg, "dev": s})
        print(f'{cfg["name"]:22s}', {k: s[k] for k in ("median_month", "geo_month", "mean_month", "months_up", "max_dd_pct", "turn_per_month", "avg_gross")})
    b = rows[0]["dev"]
    ok = [r for r in rows[1:] if r["dev"]["months_up"] >= b["months_up"] and r["dev"]["max_dd_pct"] <= b["max_dd_pct"] + 5]
    ok.sort(key=lambda r: -r["dev"]["geo_month"])
    sel = ok[0] if ok else rows[0]
    print("qualifying:", [(r["cfg"]["name"], r["dev"]["geo_month"]) for r in ok[:8]])
    print("SELECTED:", sel["cfg"])
    json.dump({"rows": rows, "selected": sel["cfg"]}, open("dev_grid.json", "w"), indent=1)
