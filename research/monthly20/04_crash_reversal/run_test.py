"""The single pre-registered TEST run (see PREREG.md). Called by `python run.py test`."""
import json
from run import *  # noqa


ATTEMPTS = [("A1_BREADTH_LIMIT_1x", "BREADTH_LIMIT", 1.0), ("A2_BREADTH_LIMIT_2.5x", "BREADTH_LIMIT", 2.5),
            ("A3_FLUSH_LIMIT_1x", "FLUSH_LIMIT", 1.0)]


def main():
    P, F = load_all()
    trades = {nm: make_trades(P, F, *v) for nm, v in CAND.items()}
    res = {"agent": "04_crash_reversal", "idea": "Buy the worst-hit liquid alt perps after a market-wide liquidation "
           "cascade (>=30% of the top-60 universe down >=5% in 4h) with trade-through limit bids 2% below, hold 24h; "
           "plus single-coin capitulation (z<=-3, volume spike, taker-sell dominance).", "attempts": []}
    for name, cand, lev in ATTEMPTS:
        tr = trades[cand]
        a = {"name": name, "prereg": True, "leverage": lev,
             "dev": run_one(P, tr, DEV_START, DEV_END, lev=lev),
             "test": run_one(P, tr, TEST_START, TEST_END, lev=lev),
             "test_double_cost": run_one(P, tr, TEST_START, TEST_END, lev=lev, cost=2)}
        print(name, {k: v for k, v in a["test"].items() if k != "months"}, flush=True)
        print(name, "x2 cost", {k: v for k, v in a["test_double_cost"].items() if k != "months"}, flush=True)
        res["attempts"].append(a)
    sweep = {}
    for lev in (1, 1.5, 2, 2.5, 3, 4, 5):
        s = run_one(P, trades["BREADTH_LIMIT"], TEST_START, TEST_END, lev=lev)
        sweep[str(lev)] = {k: s.get(k) for k in ("mean_month", "median_month", "geo_month", "worst_month",
                                                  "max_dd_pct", "net_pct", "liquidations")}
        print("sweep", lev, sweep[str(lev)], flush=True)
    res["test_leverage_sweep_info_only"] = sweep
    a2 = res["attempts"][1]
    t, d = a2["test"], a2["test_double_cost"]
    ok = (t["mean_month"] is not None and t["mean_month"] >= 20 and d["mean_month"] > 0 and t["net_pct"] > -99
          and (t.get("max_trade_share_of_profit") or 100) <= 50)
    res["best_honest"] = max(res["attempts"], key=lambda a: a["test"]["geo_month"])["name"]
    res["verdict"] = "PASS" if ok else "FAIL"
    need = [float(l) for l, s in sweep.items() if s["mean_month"] is not None and s["mean_month"] == s["mean_month"]
            and s["mean_month"] >= 20]
    res["leverage_needed_for_20pct"] = min(need) if need else None
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=lambda o: o.item()))
    print("verdict", res["verdict"], "lev needed", res["leverage_needed_for_20pct"])
