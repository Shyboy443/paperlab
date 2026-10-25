"""DEV-only grid (2021-01-01..2024-10-31) for round-4 non-momentum cross-sectional factors. Touches no TEST data
(the engine is started and stopped inside DEV; factor windows only look backwards).

Grid: factor in {lowvol, beta, ivol, max, skew, combo, volg+, volg-, size+, size-}
      x L in {14, 30, 60} (volg: {3, 7, 14})
      x k (coins per leg) in {5, 10}
      x neutral in {dollar, beta}  (beta-neutral uses the BTC beta over the same L; for volg L=30)
      x universe (top-n by 30d quote volume) in {40, 80}
      rebalance every 7 days, 1x gross, baseline costs.
Selection rule (fixed before running): among configs whose DEV net is > 0 at BOTH baseline and doubled costs,
pick the highest DEV t-stat of the monthly mean (46 DEV months). Ties -> higher DEV geometric mean month.
Output: dev_grid.json (every config, nothing dropped).
"""
import json, math, sys, time
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
import engine_r4 as E
from factors import Factors
from data import build
from common import DEV_START, DEV_END

FACTORS = ["lowvol", "beta", "ivol", "max", "skew", "combo", "volg+", "volg-", "size+", "size-"]


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))


def setup():
    D = build()
    P = E.Panel(D)
    btc = D["c"][:, list(D["syms"]).index("BTCUSDT")]
    return D, P, Factors(P, btc)


def one(P, F, UM, f, L, k, neu, univ, start, end, cost_mult=1.0, rebal=7, lev=1.0, record=False):
    beta = F.beta_raw(L if not f.startswith("volg") else 30) if neu == "beta" else None
    return E.run(P, UM[univ], F.score(f, L), dict(k_long=k, k_short=k, rebal=rebal, lev=lev), start, end,
                 cost_mult=cost_mult, beta=beta, record=record)


if __name__ == "__main__":
    t0 = time.time()
    D, P, F = setup()
    UM = {n: E.universe_mask(P, n) for n in (40, 80)}
    rows = []
    for f in FACTORS:
        Ls = (3, 7, 14) if f.startswith("volg") else (14, 30, 60)
        for L in Ls:
            for k in (5, 10):
                for neu in ("dollar", "beta"):
                    for univ in (40, 80):
                        r = one(P, F, UM, f, L, k, neu, univ, DEV_START, DEV_END)
                        r2 = one(P, F, UM, f, L, k, neu, univ, DEV_START, DEV_END, cost_mult=2)
                        row = dict(factor=f, L=L, k=k, neutral=neu, univ=univ, mean=r["mean_month"], median=r["median_month"],
                                   geo=r["geo_month"], net=r["net_pct"], dd=r["max_dd_pct"], net_2x=r2["net_pct"],
                                   t=round(tstat(r["months"]), 3), worst=r["worst_month"])
                        rows.append(row)
                        print(row, f"[{time.time()-t0:.0f}s]", flush=True)
    ok = [r for r in rows if r["net"] > 0 and r["net_2x"] > 0]
    sel = max(ok, key=lambda r: (r["t"], r["geo"])) if ok else None
    out = {"n_configs": len(rows), "n_dev_positive_both_costs": len(ok), "selected": sel, "rows": rows}
    (HERE / "dev_grid.json").write_text(json.dumps(out, indent=1))
    print("SELECTED", sel, "| positive both costs:", len(ok), "/", len(rows))
