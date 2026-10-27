"""DEV only (2021-01..2024-10): selected config + neighbour grid. Never touches TEST."""
from engine import event_list, run, DEV_START
from evlib import DEV_END
from stats import st, line
TYPES = {"UP_KRW_LIST", "UP_OTHER_LIST", "BN_SPOT_DELIST", "BN_MON_ADD", "UP_WARN"}
def go(types=TYPES, H=48, S=4, delay=0, cm=1.0):
    ev = event_list(types, delay=delay, t1=DEV_END)
    c, tr = run(ev, H=H, S=S, cm=cm, t0=DEV_START, t1=DEV_END)
    return st(c, tr)
print(line("SELECTED H48 S4", go()))
print(line("SELECTED 2x cost", go(cm=2.0)))
for H in (24, 36, 60, 72): print(line(f"H{H}", go(H=H)))
for S in (2, 3, 5, 6): print(line(f"S{S}", go(S=S)))
for d in (1, 3): print(line(f"delay+{d}h", go(delay=d)))
for t in sorted(TYPES): print(line(f"drop {t}", go(types=TYPES - {t})))
print("-- not in rule (for info): other event types alone, short H48 S4")
for t in ("BN_FUT_DELIST", "BN_SPOT_LIST", "UP_CAUTION", "UP_DELIST", "BN_MON_REMOVE"): print(line(f"only {t}", go(types={t})))
