import numpy as np
from datetime import datetime, timezone
from evlib import measure
T = {"UP_KRW_LIST", "UP_OTHER_LIST", "BN_SPOT_DELIST", "BN_MON_ADD", "UP_WARN"}
for mq in (0, 1e6, 3e6):
    rows = measure(T, min_qv=mq)
    seen = set(); R = []
    for r in rows:
        k = (r["ts"] // 86400000, r["tk"])
        if k in seen: continue
        seen.add(k); R.append(r)
    print(f"\nmin_qv {mq:.0e}: n={len(R)}, zero-move 24h: {sum(abs(r['r24'])<1e-9 for r in R)}")
    for h in (12, 24, 48, 72, 168):
        v = -np.array([r[f"r{h}"] for r in R]) - 0.0014
        print(f"  short {h:>3}h net-of-fee mean {v.mean()*100:+.2f}% med {np.median(v)*100:+.2f}% t {v.mean()/v.std(ddof=1)*np.sqrt(len(v)):+.2f} win {np.mean(v>0)*100:.0f}% worst {v.min()*100:+.1f}%")
    yrs = {}
    for r in R:
        y = datetime.fromtimestamp(r["ts"]/1000, timezone.utc).year; yrs.setdefault(y, []).append(-r["r48"] - 0.0014)
    print("  48h by year:", {y: (len(v), round(np.mean(v)*100, 2)) for y, v in sorted(yrs.items())})
