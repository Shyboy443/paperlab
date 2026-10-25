"""DEV-only grid (2021-01..2024-10). Never runs TEST. Writes dev_grid.json.
Selection rule (fixed before running): among configs with >= 150 DEV trades and DEV net > 0 at doubled costs,
pick the highest t-stat of the DEV monthly mean (46 months, 2021 months without OI data count as 0)."""
import itertools, json, sys
from multiprocessing import Pool
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")

GRID = []
for mode in ("revert",):
    for W, Z, V, D, conf, H, S in itertools.product((6, 12, 24), (3.5, 5.0, 7.0), (3.0, 5.0), (0.02, 0.04), (0, 1), (36, 72, 144), (2.0, 4.0)):
        GRID.append(dict(mode=mode, W=W, Z=Z, V=V, D=D, confirm=conf, H=H, S=S, T=0, K=5, cool=0))
for W, Z, V, D, H, S in itertools.product((6, 12), (3.5, 5.0), (3.0,), (0.02, 0.04), (36, 144), (2.0, 4.0)):
    GRID.append(dict(mode="cont", W=W, Z=Z, V=V, D=D, confirm=0, H=H, S=S, T=0, K=5, cool=0))

_coins = None


def _init():
    global _coins, C
    import checks as C_
    import engine as E
    C = C_
    _coins = E.load_coins()


def work(p):
    from common import DEV_START, DEV_END
    _, acc, s = C.run(_coins, p, start=DEV_START, end=DEV_END)
    _, _, s2 = C.run(_coins, p, cost_mult=2, start=DEV_START, end=DEV_END)
    vals = list(s["months"].values())
    s.pop("months")
    return {"p": p, "dev": s, "dev_2x_net": s2["net_pct"], "t": round(C.tstat(vals), 3)}


if __name__ == "__main__":
    with Pool(5, initializer=_init) as pool:
        res = []
        for i, r in enumerate(pool.imap_unordered(work, GRID, chunksize=4)):
            res.append(r)
            if i % 25 == 0:
                print(i, len(GRID), r["p"], r["dev"]["net_pct"], r["t"], flush=True)
    json.dump(res, open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade/dev_grid.json", "w"), indent=0, default=float)
    ok = [r for r in res if r["dev"].get("trades", 0) >= 150 and r["dev_2x_net"] > 0]
    ok.sort(key=lambda r: -r["t"])
    for r in ok[:15]:
        print(r["t"], r["p"], {k: r["dev"][k] for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct", "trades", "win_rate")}, r["dev_2x_net"])
