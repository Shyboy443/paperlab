"""DEV-only grid for candidate 1 (stablecoin timer). Never touches TEST."""
import itertools, json, time
from stable import *  # noqa

t0 = time.time()
rows = []
for L, thr, K, mode in itertools.product([7, 14, 30, 60], [0.0, 0.005, 0.01], [1, 3, 10], ["LF", "LS"]):
    W = make(L, thr, K, mode)()
    s = stats(simulate(W, DEV_START, DEV_END))
    s2 = stats(simulate(W, DEV_START, DEV_END, cost_mult=2))
    rows.append(dict(L=L, thr=thr, K=K, mode=mode, on=round(on_fraction(L, thr, DEV_START, DEV_END), 2),
                     **brief(s), net2x=s2["net_pct"]))
# benchmark: always long top-K
for K in [1, 3, 10]:
    s = stats(simulate(make(1, -1.0, K, "LF")(), DEV_START, DEV_END))
    rows.append(dict(L=0, thr=-1, K=K, mode="ALWAYS", on=1.0, **brief(s)))
rows.sort(key=lambda r: -r["geo_month"])
for r in rows:
    print(r)
(HERE / "dev_stable.json").write_text(json.dumps(rows, indent=1, default=float))
print("secs", round(time.time() - t0))
