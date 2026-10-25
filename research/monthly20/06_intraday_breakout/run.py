"""Agent 06 - intraday volatility-expansion breakouts. Reproduces every number in REPORT.md / results.json.

    python run.py dev     # DEV scan + selection + leverage sweep on DEV (writes dev_select.json)
    python run.py test    # the single pre-registered TEST run (needs PREREG.md); writes results.json
"""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import *  # noqa
import dev_scan

HERE = Path(__file__).resolve().parent
LEVS = [1, 2, 3, 5, 10, 15, 20, 25, 30]


def select(rows):
    """Pre-declared DEV rule: >= 300 trades, both DEV halves net > 0 bp per trade at baseline cost,
    highest DEV net bp. If nothing qualifies, take the highest DEV net bp anyway (flagged)."""
    ok = [r for r in rows if r["all"]["n"] >= 300 and (r["h1"]["mean_net_bp"] or -1) > 0
          and (r["h2"]["mean_net_bp"] or -1) > 0]
    pool = ok or [r for r in rows if r["all"]["n"] >= 300]
    best = max(pool, key=lambda r: r["all"]["mean_net_bp"])
    return best, bool(ok), len(ok)


def ruin_mc(trades, L, months=24, sims=2000, seed=6):
    """Bootstrap DEV trades (with their real per-month count) at leverage L: share of 24-month paths that
    lose 90%+ of equity, and the median end multiple."""
    rnd = random.Random(seed)
    s, _, pnls = portfolio(trades, L, DEV_START, DEV_END)
    per_month = len(trades) / s["n_months"]
    n = int(per_month * months)
    side = FEE + SLIP
    rets = []
    liq = 1 / L - MAINT_MARGIN
    for t in trades:
        r = -1.0 if t["mae"] >= liq else max(L * (t["gross"] + t["fund"] - 2 * side), -1.0)
        rets.append(r / len(COINS))  # each trade uses 1/n_slots of equity as margin
    ruined, ends = 0, []
    for _ in range(sims):
        eq, low = 1.0, 1.0
        for _ in range(n):
            eq *= 1 + rnd.choice(rets)
            low = min(low, eq)
        ruined += low < 0.1
        ends.append(eq)
    ends.sort()
    return {"L": L, "p_lose_90pct_in_24m": round(ruined / sims, 3), "median_end_multiple": round(ends[sims // 2], 3)}


def dev():
    path = HERE / "dev_scan.json"
    if not path.exists():
        rows = []
        for p in dev_scan.grid():
            tr = all_trades(p, DEV_START, DEV_END)
            a = [x for x in tr if x["et"] < dev_scan.H1_END]
            b = [x for x in tr if x["et"] >= dev_scan.H1_END]
            rows.append({"p": p, "all": unlev_stats(tr), "h1": unlev_stats(a), "h2": unlev_stats(b),
                         "gross_bp": unlev_stats(tr, 0.0)["mean_net_bp"]})
        path.write_text(json.dumps(rows))
    rows = json.loads(path.read_text())
    best, qualified, n_ok = select(rows)
    tr = all_trades(best["p"], DEV_START, DEV_END)
    sweep = {}
    for L in LEVS:
        s, _, _ = portfolio(tr, L, DEV_START, DEV_END)
        sweep[L] = {k: s[k] for k in ("mean_month", "median_month", "worst_month", "max_dd_pct", "net_pct", "liquidations")}
    need = next((L for L in LEVS if sweep[L]["mean_month"] >= 20 and sweep[L]["net_pct"] > -90), None)
    gross = sorted(rows, key=lambda r: -(r["gross_bp"] or -999))
    out = {"n_variants": len(rows), "n_qualified": n_ok, "qualified": qualified, "selected": best,
           "dev_leverage_sweep": sweep, "dev_leverage_for_20pct": need,
           "variants_net_positive": sum((r["all"]["mean_net_bp"] or -1) > 0 for r in rows),
           "variants_gross_positive": sum((r["gross_bp"] or -1) > 0 for r in rows),
           "top10_by_net": [(r["p"], r["all"], r["h1"]["mean_net_bp"], r["h2"]["mean_net_bp"]) for r in
                            sorted(rows, key=lambda r: -(r["all"]["mean_net_bp"] or -999))[:10]],
           "top5_by_gross": [(r["p"], r["gross_bp"], r["all"]["n"]) for r in gross[:5]],
           "ruin_mc": [ruin_mc(tr, L) for L in (1, 3, 5, 10, 20)]}
    (HERE / "dev_select.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "top10_by_net"}, indent=1))
    for r in out["top10_by_net"]:
        print(r)


def test():
    assert (HERE / "PREREG.md").exists(), "write PREREG.md first"
    pre = json.loads((HERE / "prereg_params.json").read_text())
    dsel = json.loads((HERE / "dev_select.json").read_text())
    attempts = []
    for a in pre["attempts"]:
        p, L = a["params"], a["leverage"]
        dtr = all_trades(p, DEV_START, DEV_END)
        ttr = all_trades(p, TEST_START, TEST_END)
        d, _, _ = portfolio(dtr, L, DEV_START, DEV_END)
        t, _, _ = portfolio(ttr, L, TEST_START, TEST_END)
        t2, _, _ = portfolio(ttr, L, TEST_START, TEST_END, cost_mult=2.0)
        attempts.append({"name": a["name"], "prereg": True, "leverage": L, "params": p,
                         "dev": d, "test": t, "test_double_cost": t2,
                         "test_unlevered_trade_stats": unlev_stats(ttr), "dev_unlevered_trade_stats": unlev_stats(dtr)})
        print(a["name"], "L", L, "TEST mean", t["mean_month"], "median", t["median_month"], "worst", t["worst_month"],
              "dd", t["max_dd_pct"], "net", t["net_pct"], "trades", t["trades"], "liq", t["liquidations"],
              "| x2 cost mean", t2["mean_month"], "| unlev", unlev_stats(ttr))
    # descriptive TEST leverage sweep of the primary attempt (pre-registered as descriptive only)
    p0 = pre["attempts"][0]["params"]
    ttr = all_trades(p0, TEST_START, TEST_END)
    sweep = {}
    for L in LEVS:
        s, _, _ = portfolio(ttr, L, TEST_START, TEST_END)
        sweep[L] = {k: s[k] for k in ("mean_month", "median_month", "worst_month", "max_dd_pct", "net_pct", "liquidations")}
    prim = attempts[0]
    t = prim["test"]
    passed = (t["mean_month"] >= 20 and prim["test_double_cost"]["mean_month"] > 0 and t["net_pct"] > -99
              and (t["top1_share_of_profit"] is not None and t["top1_share_of_profit"] <= 50))
    res = {"agent": "06_intraday_breakout",
           "idea": "Intraday volatility-expansion breakouts (session opening ranges, BB/KC squeezes, prior-day high/low) "
                   "on 6 liquid 15m perps, taker entries, trailing ATR stop + time exit, leveraged isolated margin.",
           "attempts": attempts, "best_honest": prim["name"], "verdict": "PASS" if passed else "FAIL",
           "leverage_needed_for_20pct": dsel["dev_leverage_for_20pct"],
           "dev_selection": {k: dsel[k] for k in ("n_variants", "n_qualified", "variants_net_positive",
                                                   "variants_gross_positive", "dev_leverage_sweep", "ruin_mc")},
           "test_leverage_sweep_descriptive": sweep}
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(sweep, indent=1))
    print("VERDICT", res["verdict"])


if __name__ == "__main__":
    {"dev": dev, "test": test}[sys.argv[1]]()
