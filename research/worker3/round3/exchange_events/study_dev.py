"""DEV-only event study (2021-01..2024-10). Prints mean / median / t / hit of raw and BTC-relative returns per type & horizon."""
import numpy as np
from evlib import measure, DEV_START, DEV_END
H = (1, 4, 12, 24, 48, 72, 168, 336)
TYPES = ["BN_SPOT_DELIST", "BN_MON_ADD", "BN_MON_REMOVE", "BN_FUT_DELIST", "BN_SPOT_LIST", "UP_KRW_LIST", "UP_OTHER_LIST", "UP_WARN", "UP_CAUTION", "UP_DELIST"]
for typ in TYPES:
    rows = measure({typ})
    print(f"\n== {typ}: n={len(rows)}  (events with perp at entry, DEV)")
    if len(rows) < 3:
        continue
    pre = np.array([r["pre24"] for r in rows]); print(f"   pre-24h mean {pre.mean()*100:+.2f}% med {np.median(pre)*100:+.2f}%")
    for h in H:
        for k in ("r", "x"):
            v = np.array([r[f"{k}{h}"] for r in rows])
            t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan
            print(f"   {k}{h:>3}h  mean {v.mean()*100:+7.2f}%  med {np.median(v)*100:+7.2f}%  t {t:+5.2f}  up {np.mean(v>0)*100:4.0f}%", end="" if k == "r" else "\n")
