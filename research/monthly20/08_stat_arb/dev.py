"""Agent 08 - DEV-only exploration grid (2021-01..2024-10). Writes dev_grid.json. Never touches TEST."""
import json, time
import numpy as np
from engine import load, sim, DEV_START, DEV_END, summary
import os, engine
if os.environ.get("CACHED_ONLY"):
    have = {p.stem for p in (engine.CACHE / "perp" / "1h").glob("*.json")}
    engine.COINS[:] = [c for c in engine.COINS if c in have and (engine.CACHE / "funding" / f"{c}.json").exists()]
    print("coins", engine.COINS)
import strategies as S

t0 = time.time()
D = load()
r, E, B = S.residuals(D)
print("loaded", round(time.time() - t0), "s", flush=True)

# lead-lag diagnostics: does BTC's return in bar t predict alt residual return over bar t+1 (open->open, i.e. after fill)?
oo = (D["o"].shift(-2) / D["o"].shift(-1) - 1)  # return over bar t+1, tradable after close of t
m = (r.index >= DEV_START) & (r.index < DEV_END)
diag = {}
for a in E.columns:
    b1 = B[a]["BTCUSDT"]
    resid_next = oo[a] - b1 * oo["BTCUSDT"] - B[a]["ETHUSDT"] * oo["ETHUSDT"]
    x, y = r["BTCUSDT"][m], resid_next[m]
    ok = x.notna() & y.notna()
    diag[a] = round(float(np.corrcoef(x[ok], y[ok])[0, 1]), 4) if ok.sum() > 1000 else None
print("lead-lag corr(BTC r_t, alt resid r_{t+1}) DEV:", diag, flush=True)

grid = []


def run(name, W, rebal=1):
    c, pp, info = sim(D, W, DEV_START, DEV_END, rebal_every=rebal)
    s = summary(c, pp)
    s.update(info)
    c0, _, _ = sim(D, W, DEV_START, DEV_END, rebal_every=rebal, cost_mult=0.0)
    s["zero_cost_mean_month"] = summary(c0)["mean_month"]
    grid.append({"name": name, "dev": {k: v for k, v in s.items() if k != "months"}})
    print(f"{name:45s} mean {s['mean_month']:7.2f} med {s['median_month']:7.2f} worst {s['worst_month']:7.2f} "
          f"net {s['net_pct']:9.1f} dd {s['max_dd_pct']:6.1f} n {s.get('trades')} | nocost {s['zero_cost_mean_month']:6.2f}", flush=True)


for look in (4, 12, 24, 72):
    for rebal in (1, 4, 24):
        if rebal > look * 2:
            continue
        run(f"A_resid_cs_look{look}_rebal{rebal}", S.resid_reversal(D, r, E, B, look=look), rebal)
for look in (24, 72):
    for ze in (1.5, 2.5):
        run(f"A_resid_ts_look{look}_z{ze}_rebal4", S.resid_reversal(D, r, E, B, look=look, z_entry=ze, mode="ts"), 4)
for k in (2.0, 3.0):
    for sign in (1, -1):
        run(f"C_leadlag_k{k}_sign{sign}_hold1", S.lead_lag(D, r, E, B, k=k, sign=sign), 1)
for zw in (72, 168):
    for entry in (2.0, 2.5):
        for norm in ("all", "open"):
            run(f"B_pairs_zw{zw}_e{entry}_{norm}", S.pairs(D, z_win=zw, entry=entry, norm=norm), 1)

json.dump({"leadlag_corr_dev": diag, "grid": grid}, open("dev_grid.json", "w"), indent=1, default=float)
print("done", round(time.time() - t0), "s")
