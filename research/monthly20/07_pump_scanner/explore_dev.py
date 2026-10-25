"""DEV-only event study: after a coin pumps hard, does it keep going or fade? (2021-01..2024-10)"""
import json, sys
from lib import *

data = load_all()
print("symbols", len(data))
LIQ = 200_000  # mean 1h quote volume over the prior 30 days >= $200k (about $5M a day)
liq = lambda d: np.nan_to_num(d["basev"]) >= LIQ
defs = {
    "r24>=20%": lambda d: (d["r24"] >= 0.20) & liq(d),
    "r24>=30%": lambda d: (d["r24"] >= 0.30) & liq(d),
    "r24>=50%": lambda d: (d["r24"] >= 0.50) & liq(d),
    "r24>=30%&vol24x5": lambda d: (d["r24"] >= 0.30) & (d["vratio24"] >= 5) & liq(d),
    "r4>=15%": lambda d: (d["r4"] >= 0.15) & liq(d),
    "r4>=15%&newhigh30d": lambda d: (d["r4"] >= 0.15) & (d["c"] > d["prevhi"]) & liq(d),
    "r24>=20%&newhigh30d&vol3x": lambda d: (d["r24"] >= 0.20) & (d["c"] > d["prevhi"]) & (d["vratio24"] >= 3) & liq(d),
    "r24>=50%&close<hi24*0.9 (exhaustion)": lambda d: (d["r24"] >= 0.50) & (d["c"] < d["hi24"] * 0.90) & liq(d),
    "r24>=30%&close<hi24*0.85": lambda d: (d["r24"] >= 0.30) & (d["c"] < d["hi24"] * 0.85) & liq(d),
    "r24<=-20% (dump, for contrast)": lambda d: (d["r24"] <= -0.20) & liq(d),
}
out = {}
for name, f in defs.items():
    ev = events(data, f, DEV_START, DEV_END - 8 * DAY)
    out[name] = fwd_stats(data, ev)
    print(name, json.dumps(out[name]))
# per-year split for the main definition
for y in range(2021, 2025):
    ev = events(data, defs["r24>=30%"], utc(y, 1, 1), min(utc(y + 1, 1, 1), DEV_END - 8 * DAY))
    out[f"r24>=30% {y}"] = fwd_stats(data, ev)
    print(y, json.dumps(out[f"r24>=30% {y}"]))
json.dump(out, open("dev_event_study.json", "w"), indent=1)
