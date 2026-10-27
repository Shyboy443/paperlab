"""DEV-only grid for candidate 2 (turnover-volatility factor). Never touches TEST."""
import itertools, json, time
from tvol import *  # noqa

t0 = time.time()
rows = []
for L, k, rb in itertools.product([14, 30, 60], [3, 5, 10], [7, 14]):
    W = make(L, k, rb)()
    s = stats(simulate(W, DEV_START, DEV_END)); s2 = stats(simulate(W, DEV_START, DEV_END, cost_mult=2))
    rows.append(dict(L=L, k=k, rb=rb, **brief(s), net2x=s2["net_pct"]))
rows.sort(key=lambda r: -r["geo_month"])
for r in rows:
    print({a: (round(float(b), 2) if not isinstance(b, str) else b) for a, b in r.items()})
(HERE / "dev_tvol.json").write_text(json.dumps(rows, indent=1, default=float))
print("secs", round(time.time() - t0))
