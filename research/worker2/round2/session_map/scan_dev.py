"""DEV-only scan of the 864-config session map (PREREG.md). Writes scan_dev.json. Never touches TEST."""
import json, itertools, time
from core2 import *
T = time.time()
out = []
for E, H_, X, mode, days in itertools.product(range(0, 24, 2), (4, 8, 16, 24), (4, 8, 12), ("mom", "rev"), ("all", "weekday", "weekend")):
    p = dict(BASE, E=E, H=H_, X=X, mode=mode, days=days)
    c, tr, _, _ = sim2(p, d0, dT)
    s = stats(c, tr)
    c2, _, _, _ = sim2(p, d0, dT, cost_mult=2)
    out.append(dict(E=E, H=H_, X=X, mode=mode, days=days, t_daily=round(daily_t(c), 2), t_month=s["t_stat"],
                    median=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], dd=s["max_dd_pct"],
                    net=s["net_pct"], net2x=summary(c2)["net_pct"], trades=s["trades"], excluded=excluded(p)))
BAR = 3.85
for r in out:
    r["eligible"] = bool(r["t_daily"] >= BAR and (r["t_month"] or 0) >= 2 and r["median"] > 0 and r["net2x"] > 0 and not r["excluded"])
el = sorted([r for r in out if r["eligible"]], key=lambda r: (-r["geo"], -r["t_daily"]))
json.dump({"bar": BAR, "n": len(out), "eligible": el, "all": out}, open("scan_dev.json", "w"), indent=0)
print("time", round(time.time() - T), "n", len(out), "eligible", len(el))
for r in sorted(out, key=lambda r: -r["t_daily"])[:30]:
    print(r)
print("--- eligible")
for r in el[:15]:
    print(r)
