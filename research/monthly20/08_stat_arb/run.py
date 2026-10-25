"""Agent 08 - stat arb. Reproduces every number.

    python run.py          # pre-registered attempts: DEV, TEST, TEST at 2x costs, + post-hoc leverage sweep
    python run.py --dev    # also re-run the DEV exploration grids (dev.py, dev2.py, dev3.py; CACHED_ONLY universes)
"""
import json, subprocess, sys
import numpy as np
import engine
from engine import sim, summary, DEV_START, DEV_END, TEST_START, TEST_END
import strategies as S

if "--dev" in sys.argv:
    for f in ("dev.py", "dev2.py", "dev3.py"):
        subprocess.run([sys.executable, f], check=True, env={**__import__("os").environ, "CACHED_ONLY": "1"})

D = engine.load()
r, E, B = S.residuals(D)

ATTEMPTS = [
    ("D_shock_both_h24_s5_L1", lambda: S.shock(D, r, E, B, hold=24, slots=5), 1, 1.0),
    ("D_shock_both_h24_s5_L5", lambda: S.shock(D, r, E, B, hold=24, slots=5), 1, 5.0),
    ("A_resid_cs_look72_rebal24", lambda: S.resid_reversal(D, r, E, B, look=72), 24, 1.0),
    ("B_pairs_zw168_e2.5_all", lambda: S.pairs(D, z_win=168, entry=2.5, norm="all"), 1, 1.0),
    ("D2_basket_both_h12_s5", lambda: S.shock_vs_basket(D, r, E, B, hold=12, slots=5), 1, 1.0),
]


def summ(W, a, b, rebal, lev, cm=1.0):
    c, pp, info = sim(D, W, a, b, lev=lev, rebal_every=rebal, cost_mult=cm)
    s = summary(c, pp)
    s.update(info)
    tot = sum(pp)
    s["top1_share_of_profit"] = round(max(pp) / tot * 100, 1) if tot > 0 and pp else None
    return {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in s.items()}


attempts, cache = [], {}
for name, fn, rebal, lev in ATTEMPTS:
    key = name.rsplit("_L", 1)[0] if name.startswith("D_shock") else name
    W = cache.setdefault(key, fn())
    a = {"name": name, "prereg": True, "leverage": lev,
         "dev": summ(W, DEV_START, DEV_END, rebal, lev),
         "test": summ(W, TEST_START, TEST_END, rebal, lev),
         "test_double_cost": summ(W, TEST_START, TEST_END, rebal, lev, 2.0)}
    attempts.append(a)
    t = a["test"]
    print(f"{name:30s} DEV mean {a['dev']['mean_month']:6.2f} | TEST mean {t['mean_month']:6.2f} med {t['median_month']:6.2f} "
          f"worst {t['worst_month']:7.2f} dd {t['max_dd_pct']:5.1f} net {t['net_pct']:8.1f} wiped {t['wiped']} "
          f"liq {t['liq_legs']} | 2x mean {a['test_double_cost']['mean_month']:6.2f}", flush=True)

# post-hoc diagnostic (NOT an attempt that can pass): leverage sweep of the main candidate on TEST
sweep = {}
W = cache["D_shock_both_h24_s5"]
for lev in (2.0, 10.0, 20.0):
    s = summ(W, TEST_START, TEST_END, 1, lev)
    sweep[str(lev)] = {k: s[k] for k in ("mean_month", "median_month", "worst_month", "max_dd_pct", "net_pct", "wiped", "liq_legs")}
    print("sweep L", lev, sweep[str(lev)], flush=True)

main1 = attempts[0]["test"]["mean_month"]
lev_needed = round(20 / main1, 1) if main1 and main1 > 0 else None
m5, m5x2 = attempts[1]["test"], attempts[1]["test_double_cost"]
passed = (m5["mean_month"] >= 20 and m5x2["mean_month"] > 0 and not m5["wiped"]
          and (m5["top1_share_of_profit"] or 100) <= 50)

dev_grids = {}
for f in ("dev_grid.json", "dev_grid2.json", "dev_grid3.json"):
    try:
        dev_grids[f] = json.load(open(f))
    except FileNotFoundError:
        pass

out = {"agent": "08_stat_arb",
       "idea": "Stat arb on Binance USDT perps: BTC/ETH factor-residual reversal, sector pairs, BTC->alt lead-lag, "
               "and residual-shock events; both legs pay costs and funding; hedge ratios rolling, past data only.",
       "universe": engine.COINS,
       "attempts": attempts,
       "posthoc_leverage_sweep_D_shock_test": sweep,
       "dev_exploration": {"note": "dev_grid.json ran on 8 coins, dev_grid2.json on 11, dev_grid3.json on 13 "
                                   "(coins cached at the time); none of these touched TEST.", **dev_grids},
       "best_honest": "D_shock_both_h24_s5_L1",
       "verdict": "PASS" if passed else "FAIL",
       "leverage_needed_for_20pct": lev_needed}
json.dump(out, open("results.json", "w"), indent=1, default=float)
print("verdict", out["verdict"], "leverage needed", lev_needed)
