"""Agent 09 — time and event effects. Reproduces every number in results.json / REPORT.md.

    python research/monthly20/09_seasonality_events/run.py     (from D:/Projects/BinanceBOt/paperlab)

Design was done on DEV only (explore.py, dev_scan.py, lev_scan.py; outputs *.txt). PREREG.md froze the attempts."""
import json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from strategies import *
from engine import backtest
from events import FOMC, CPI

frames, idx = load(SYMS)
gap = gap_momentum(frames, idx, ["BTCUSDT"], 0.01, 24)
fomc = event_long(idx, SYMS, FOMC, 14, 0, 4, 1)
cpi = event_long(idx, SYMS, CPI, 8, 30, 24, 0)
st = stack(gap, fomc, cpi)
stf = stack_fixed(gap, fomc, cpi)
ATTEMPTS = [("STACK_1X", st, 1), ("STACK_3X", st, 3), ("FOMC_1X", fomc, 1), ("FOMC_12X", fomc, 12), ("GAP_1X", gap, 1)]


def line(n, s):
    return (f"{n:22s} mean {s['mean_month']:8.2f} med {s['median_month']:7.2f} worst {s['worst_month']:7.2f} "
            f"dd {s['max_dd_pct']:6.2f} net {s['net_pct']:9.1f} tr {s.get('trades')} liq {s['liquidations']} "
            f"top3 {s.get('top3_share_of_profit')}")


attempts = []
# exploratory DEV variants (never run on TEST)
dev_scan = json.load(open("dev_scan.json"))
for n, s in dev_scan.items():
    attempts.append({"name": "DEV-scan: " + n, "prereg": False, "dev": s, "test": None, "test_double_cost": None})
for name, W, L in ATTEMPTS:
    d = backtest(W, frames, L=L, start=DEV_START, end=DEV_END)
    t = backtest(W, frames, L=L, start=TEST_START, end=TEST_END)
    t2 = backtest(W, frames, L=L, cost_mult=2.0, start=TEST_START, end=TEST_END)
    for k, s in (("DEV", d), ("TEST", t), ("TEST2x", t2)):
        print(line(f"{name} {k}", s), flush=True)
    attempts.append({"name": name, "prereg": True, "leverage": L, "dev": d, "test": t, "test_double_cost": t2})
# NEW attempts after seeing TEST: the pre-registered stack had a column-alignment bug (only BTC was traded,
# see stack() docstring). The fixed stack is what PREREG.md describes in words, but it is a post-TEST attempt.
for name, W, L in [("STACK_FIXED_1X", stf, 1), ("STACK_FIXED_3X", stf, 3)]:
    d = backtest(W, frames, L=L, start=DEV_START, end=DEV_END)
    t = backtest(W, frames, L=L, start=TEST_START, end=TEST_END)
    t2 = backtest(W, frames, L=L, cost_mult=2.0, start=TEST_START, end=TEST_END)
    for k, s in (("DEV", d), ("TEST", t), ("TEST2x", t2)):
        print(line(f"{name} {k}", s), flush=True)
    attempts.append({"name": name, "prereg": False, "note": "post-TEST bug fix of STACK", "leverage": L,
                     "dev": d, "test": t, "test_double_cost": t2})

# post-TEST diagnostic: leverage sweep (labelled, not an attempt)
sweep = {}
for name, W in (("stack", st), ("stack_fixed", stf), ("fomc", fomc), ("gap", gap), ("cpi", cpi)):
    for L in (1, 2, 3, 5, 8, 12, 20, 30):
        s = backtest(W, frames, L=L, start=TEST_START, end=TEST_END)
        sweep[f"{name}_L{L}"] = {k: s[k] for k in ("mean_month", "median_month", "worst_month", "max_dd_pct",
                                                    "net_pct", "liquidations")}
        print(line(f"sweep {name} L{L}", s), flush=True)

base = max(a["test"]["mean_month"] for a in attempts if a["test"] and a.get("leverage") == 1)
lev_needed = round(20 / base, 1) if base and base >= 0.5 else None  # TEST edge ~0: no leverage reaches 20%


def passes(a):
    t, t2 = a["test"], a["test_double_cost"]
    return (t["mean_month"] >= 20 and t2["mean_month"] > 0 and t["net_pct"] > -100 and
            (t.get("top3_share_of_profit") or 0) <= 150 and t["liquidations"] == 0)


verdict = "PASS" if any(passes(a) for a in attempts if a["prereg"]) else "FAIL"
json.dump({"agent": "09_seasonality_events",
           "idea": "Time and event effects: hour/day/weekend/turn-of-month/funding hours/US session/options expiry "
                   "(all too small for costs), plus CME weekend-gap momentum, pre-FOMC and pre-CPI longs, stacked.",
           "attempts": attempts, "best_honest": "STACK_1X", "verdict": verdict,
           "leverage_needed_for_20pct": lev_needed,
           "leverage_needed_note": "null: best 1x TEST mean is +0.04%/month (500x by linear scaling, meaningless). On DEV the "
                                   "fixed stack (+3.14%/month) would need ~6.4x; on TEST every leg is liquidated at 12x",
           "post_test_leverage_sweep": sweep}, open("results.json", "w"), indent=1, default=float)
print("verdict", verdict, "leverage needed (linear)", lev_needed)
