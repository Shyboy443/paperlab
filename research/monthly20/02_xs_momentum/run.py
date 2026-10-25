"""Agent 02 - cross-sectional rotation across Binance USDT perps. Reproduces every number in results.json / REPORT.md.

    python run.py            # DEV + TEST + TEST at doubled costs for every pre-registered attempt
Exploration scripts (DEV only): dev_sweep.py (1d grid), dev_stage2.py (leverage + BTC regime), dev_4h.py (4h grid).
Data: common.py cache (1d klines, funding); 4h bars resampled from agent 04's read-only 1h vision cache (panel4h.py).
"""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
from engine import *
from signals import *
from dev_sweep import build
from dev_stage2 import regime_gate

HERE = Path(__file__).resolve().parent

# ---- pre-registered attempts (see PREREG.md; frozen before the TEST run) ----
ATTEMPTS = [
    # name, interval, score builder, params
    ("P1_mom7_top3_btc50_2x", "1d", lambda P, b: regime_gate(P, ret_lb(P, 7), 50), dict(k_long=3, k_short=0, rebal=1, lev=2.0, mode="mom")),
    ("P2_mom7_top3_btc50_1x", "1d", lambda P, b: regime_gate(P, ret_lb(P, 7), 50), dict(k_long=3, k_short=0, rebal=1, lev=1.0, mode="mom")),
    ("P3_mom7_top1_btc50_1x", "1d", lambda P, b: regime_gate(P, ret_lb(P, 7), 50), dict(k_long=1, k_short=0, rebal=1, lev=1.0, mode="mom")),
    ("P4_mom14_LS10_weekly_2x", "1d", lambda P, b: ret_lb(P, 14), dict(k_long=10, k_short=10, rebal=7, lev=2.0, mode="mom")),
    ("P5_resid28_LS10_weekly_1x", "1d", lambda P, b: resid_btc(P, 28, 60, b), dict(k_long=10, k_short=10, rebal=7, lev=1.0, mode="mom")),
    ("P6_mom7_top3_nogate_1x", "1d", lambda P, b: ret_lb(P, 7), dict(k_long=3, k_short=0, rebal=1, lev=1.0, mode="mom")),
]
LEV_LADDER = [1, 1.5, 2, 3, 4, 5]   # diagnostic, for "leverage needed for 20%/month" on P2's rules


def clean(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def main():
    panels = {}
    out = []
    for name, iv, sb, p in ATTEMPTS:
        if iv not in panels:
            if iv == "1d":
                panels[iv] = build("1d")
            else:
                from dev_4h import build4h
                panels[iv] = build4h()
        P, um, keep = panels[iv]
        sc = sb(P, P.syms.index("BTCUSDT"))
        dev = run(P, um, sc, p, DEV_START, DEV_END)
        test = run(P, um, sc, p, TEST_START, TEST_END)
        test2 = run(P, um, sc, p, TEST_START, TEST_END, cost_mult=2.0)
        print(f"{name:28s} DEV mean {dev['mean_month']:7.2f} geo {dev['geo_month']:6.2f} dd {dev['max_dd_pct']:5.1f} | TEST mean {test['mean_month']:7.2f} "
              f"med {test['median_month']:6.2f} worst {test['worst_month']:7.2f} geo {test['geo_month']:6.2f} dd {test['max_dd_pct']:5.1f} "
              f"liq {test['liquidations']} wiped {test['wiped']} top3 {test['top3_share_of_profit']} | 2x mean {test2['mean_month']:7.2f}", flush=True)
        out.append(dict(name=name, prereg=True, interval=iv, params=p, dev=clean(dev), test=clean(test), test_double_cost=clean(test2)))
    # leverage ladder on P2's rules (diagnostic, pre-registered)
    P, um, keep = panels["1d"]
    sc = regime_gate(P, ret_lb(P, 7), 50)
    ladder = []
    for lev in LEV_LADDER:
        p = dict(k_long=3, k_short=0, rebal=1, lev=lev, mode="mom")
        d = run(P, um, sc, p, DEV_START, DEV_END); t = run(P, um, sc, p, TEST_START, TEST_END)
        ladder.append(dict(lev=lev, dev_mean=d["mean_month"], dev_geo=d["geo_month"], dev_dd=d["max_dd_pct"], dev_wiped=d["wiped"],
                           test_mean=t["mean_month"], test_median=t["median_month"], test_geo=t["geo_month"], test_dd=t["max_dd_pct"],
                           test_worst=t["worst_month"], test_wiped=t["wiped"], test_liq=t["liquidations"]))
        print("ladder", ladder[-1], flush=True)
    return out, ladder


if __name__ == "__main__":
    attempts, ladder = main()
    res = {"agent": "02_xs_momentum", "idea": "cross-sectional rotation across liquid Binance USDT perps (momentum/reversal, long-only top-k with BTC regime gate, long-short beta-neutral-ish)",
           "attempts": attempts, "leverage_ladder_P2_rules": ladder}
    passed = [a for a in attempts if a["test"]["mean_month"] >= 20 and a["test_double_cost"]["mean_month"] > 0 and not a["test"]["wiped"]
              and (a["test"]["top3_share_of_profit"] is not None and a["test"]["top3_share_of_profit"] <= 150)]
    best = max(attempts, key=lambda a: a["test"]["geo_month"])
    res["best_honest"] = best["name"]
    res["verdict"] = "PASS" if passed else "FAIL"
    res["leverage_needed_for_20pct"] = None
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
