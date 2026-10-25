"""r4_10_cascade: the ONE pre-registered TEST run + 8 luck checks + leverage table + 2026 YTD.
Reads the selected config and neighbour grid from PREREG.md's JSON block (prereg.json). Writes results.json.
    python research/monthly20/r4_10_cascade/run.py
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")
import engine as E
import checks as C
from common import DEV_START, DEV_END, TEST_START, TEST_END

HERE = Path(__file__).resolve().parent


def clean(s):
    return {k: v for k, v in s.items() if k != "months"}


def main():
    pr = json.loads((HERE / "prereg.json").read_text())
    p = pr["selected"]
    neighbours = [(n["name"], n["p"]) for n in pr["neighbours"]]
    coins = E.load_coins()
    checks, R = C.eight_checks(coins, p, neighbours)
    full = R["full"]
    # leverage table (trades regenerated per leverage: liquidation depends on it)
    lev_rows, need = [], None
    for lev in (1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        _, _, sf = C.run(coins, p, lev=lev)
        _, _, st = C.run(coins, p, lev=lev, start=TEST_START, end=TEST_END)
        row = {"lev": lev, "full": clean(sf), "test": clean(st)}
        lev_rows.append(row)
        if need is None and st["mean_month"] >= 20:
            need = lev
        print("lev", lev, sf["mean_month"], sf["geo_month"], sf["max_dd_pct"], sf["liquidations"], "| test", st["mean_month"], st["max_dd_pct"], flush=True)
    ytd = {k: v for k, v in full["months"].items() if k >= "2026-01"}
    ytd_net = C.comp(list(ytd.values()))
    acc = R["acc"]
    out = {
        "agent": "r4_10_cascade",
        "idea": "Fade liquidation cascades: after a big fast 5m-bar move with a volume spike AND a sharp open-interest drop, "
                "trade against the move at the next bar open (ATR stop, time stop). OI-rising continuation was tried on DEV and dropped.",
        "selected": p,
        "attempts": [{"name": "A1_flush_revert_selected", "prereg": True,
                      "dev": clean(R["dev"]), "test": R["test"], "test_double_cost": clean(R["test_2x"]),
                      "dev_double_cost": clean(R["dev_2x"])}],
        "full_2021_2026": full, "full_2021_2026_double_cost": clean(R["full_2x"]), "full_t": R["full_ts"],
        "checks": checks, "robust": all(v["pass"] for v in checks.values()),
        "n_pass": sum(v["pass"] for v in checks.values()),
        "monthly_1x": full["months"],
        "leverage_table": lev_rows, "leverage_needed_for_20pct": need,
        "ytd_2026": {"months": ytd, "net_pct": round(ytd_net, 2)},
        "trades_by_coin": {},
    }
    by = {}
    for t in acc:
        by[t["sym"]] = by.get(t["sym"], 0) + t["pnl"]
    out["trades_by_coin"] = {k: round(v, 2) for k, v in sorted(by.items(), key=lambda x: -x[1])}
    out["n_long"] = sum(t["d"] > 0 for t in acc); out["n_short"] = sum(t["d"] < 0 for t in acc)
    out["best_honest"] = "A1_flush_revert_selected"
    out["verdict"] = "PASS" if (R["test"]["mean_month"] >= 20 and R["test_2x"]["mean_month"] > 0) else "FAIL"
    (HERE / "results.json").write_text(json.dumps(out, indent=1, default=float))
    print({k: bool(checks[k]["pass"]) for k in checks}, out["n_pass"])
    for k in ("dev", "test", "dev_2x", "test_2x", "full", "full_2x"):
        s = R[k]
        print(k, {x: s[x] for x in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct", "trades")})
    print("YTD", ytd, round(ytd_net, 2), "need", need)


if __name__ == "__main__":
    main()
