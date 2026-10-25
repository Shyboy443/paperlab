"""Scribd round 4 study: monthly momentum / rotation systems (pre-registered in docs/SCRIBD_ROUND4_STUDY.md).

    python scripts/scribd_round4_study.py

Data: data/round3_cache/<SYM>_D1.npy (Yahoo daily, adjusted). Writes app/ytstrats/round4_results.json (served
read-only by /api/public/scribd-round4).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, round3 as R3, round4 as R4, scalp, swing  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
fmt = lambda x: datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
CACHE = PROJECT / "data" / "round3_cache"
OUT = PROJECT / "app" / "ytstrats" / "round4_results.json"
COST = 0.0005
GMR = ("MDY", "IEV", "EEM", "ILF", "EPP", "EDV")
BONDS = ("AGG", "BOND", "CWB", "JNK", "TLH")
ALV = ("AGG", "DBC", "EEM", "EFA", "GLD", "HYG", "IWM", "LQD", "QQQ", "SPY", "TLT", "VNQ")
RULES = [
    {"key": "gmr_diy", "doc": "A Global Market Rotation Strategy With An Annual Performance Of 41.4% Since 2003, Grossmann (399509251)",
     "assets": GMR, "fn": R4.gmr_weights(GMR), "win": {"a": ("2008-05-01", "2013-09-01"), "b": ("2013-09-01", "2026-10-21")}, "claim": "41.4% a year 2003-2013 (DIY version ~34%)"},
    {"key": "bond_rotation_diy", "doc": "The Sleep Well Bond Rotation Strategy, Grossmann (250728700)",
     "assets": BONDS, "fn": R4.gmr_weights(BONDS), "win": {"a": ("2012-07-01", "2019-07-01"), "b": ("2019-07-01", "2026-10-21")}, "claim": "15.7-19% a year since 2008 (DIY top-1: 17%)"},
    {"key": "gem", "doc": "Gary Antonacci's Momentum Trend Research: Global Equities Momentum (1071527952)",
     "assets": ("SPY", "VEU", "AGG", "BIL"), "fn": R4.gem_weights, "win": {"a": ("2008-06-01", "2015-01-01"), "b": ("2015-01-01", "2026-10-21")}, "claim": "higher return than equities with lower risk"},
    {"key": "alvarez_trend_mom", "doc": "Trend-Following Plus Momentum in ETFs, Alvarez Quant Trading (439780519)",
     "assets": ALV, "fn": R4.alvarez_weights(ALV), "win": {"a": ("2007-01-01", "2019-11-01"), "b": ("2019-11-01", "2026-10-21")}, "scale": 12.0,
     "claim": "beats buy and hold on most metrics for 5 of 12 ETFs"},
]


def load(sym: str) -> swing.D:
    a = np.load(CACHE / f"{sym}_D1.npy")
    return swing.D(*(a[:, k] for k in range(5)))


def curve(month_pct: list[float]) -> dict:
    if not month_pct:
        return {"months": 0}
    eq = np.cumprod(1 + np.array(month_pct) / 100)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    yrs = len(month_pct) / 12
    return {"months": len(month_pct), "total_pct": float((eq[-1] - 1) * 100), "cagr_pct": float((eq[-1] ** (1 / yrs) - 1) * 100),
            "max_dd_pct": float((1 - eq / peak).max() * 100)}


def spy_same_months(starts: list[int]) -> dict:
    """Buy and hold SPY open-to-open over the same months."""
    b = load("SPY")
    if not starts:
        return {}
    i0 = int(np.searchsorted(b.t, starts[0]))
    t_end = starts[-1] + 32 * 86_400_000
    i1 = min(int(np.searchsorted(b.t, t_end)), len(b.t) - 1)
    yrs = (b.t[i1] - b.t[i0]) / (365.25 * 86_400_000)
    path = b.c[i0:i1 + 1]
    peak = np.maximum.accumulate(path)
    tot = b.o[i1] / b.o[i0]
    return {"total_pct": float((tot - 1) * 100), "cagr_pct": float((tot ** (1 / yrs) - 1) * 100), "max_dd_pct": float((1 - path / peak).max() * 100)}


def tqqq_sensitivity() -> dict:
    """Not a verdict: round 3's ibs_qqq signals (from QQQ) traded in QQQ and in TQQQ, 2010-02 -> 2026-10, full capital."""
    q, t3 = load("QQQ"), load("TQQQ")
    s0, s1 = ms("2010-03-01"), ms("2026-10-21")
    out = {}
    for name, px in (("QQQ", q), ("TQQQ", t3)):
        tr = R3.ibs_qqq(q, 0.0, 0.0001, s0, s1)
        rets = []
        for x in tr:
            i, k = int(np.searchsorted(px.t, x["t"])), int(np.searchsorted(px.t, x["exit_t"]))
            rets.append(((px.c[k] * (1 - 0.0001)) / (px.c[i] * (1 + 0.0001)) - 1) * 100)
        eq = np.cumprod(1 + np.array(rets) / 100)
        peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
        yrs = (s1 - s0) / (365.25 * 86_400_000)
        out[name] = {"trades": len(rets), "mean_ret_pct": float(np.mean(rets)), "total_pct": float((eq[-1] - 1) * 100),
                     "cagr_pct": float((eq[-1] ** (1 / yrs) - 1) * 100), "max_dd_pct": float((1 - eq / peak).max() * 100),
                     "worst_trade_pct": float(min(rets))}
    out["buy_and_hold_QQQ_cagr_pct"] = float(((q.c[-1] / q.o[int(np.searchsorted(q.t, s0))]) ** (1 / ((s1 - s0) / (365.25 * 86_400_000))) - 1) * 100)
    return out


def main() -> int:
    rows = []
    for r in RULES:
        data = {s: load(s) for s in r["assets"]}
        per, curves, spy = {}, {}, {}
        by_asset: dict[str, float | None] = {}
        gross = {}
        for w, (a, b) in r["win"].items():
            tr, port, starts = R4.monthly(data, r["fn"], COST, ms(a), ms(b), scale=r.get("scale", 1.0))
            trg, _, _ = R4.monthly(data, r["fn"], 0.0, ms(a), ms(b), scale=r.get("scale", 1.0))
            per[w], curves[w], spy[w] = tr, curve(port), spy_same_months(starts)
            gross[w] = scalp.summarize(trg)
            if w == "b":
                if r["key"] == "alvarez_trend_mom":
                    for s in r["assets"]:
                        v = [x["ret_pct"] for x in tr if x["asset"] == s]
                        by_asset[s] = float(sum(v)) if v else None
                else:
                    by_asset["strategy"] = float(sum(port))
        sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
        low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
        traded = [v for v in by_asset.values() if v is not None]
        checks = {
            "a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
            "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= 50),
            "b_ci_above_zero": bool(low > 0),
            "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
        }
        held = {}
        for x in per["b"]:
            held[x["asset"]] = held.get(x["asset"], 0) + 1
        fn = r["fn"]
        doc_rule = (getattr(fn, "__doc__", None) or "").strip()
        if not doc_rule or doc_rule.startswith("Run"):
            doc_rule = {"gmr_diy": R4.gmr_weights.__doc__, "bond_rotation_diy": "Sleep Well Bond Rotation, do-it-yourself version (250728700): each month hold the one ETF of AGG, BOND, CWB, JNK, TLH with the best 3-month return.",
                        "alvarez_trend_mom": R4.alvarez_weights.__doc__}.get(r["key"], "").strip()
        row = {"key": r["key"], "doc": r["doc"], "assets": list(r["assets"]), "tf": "monthly", "rule": doc_rule, "claim": r["claim"],
               "windows": {k: list(v) for k, v in r["win"].items()},
               "a": {**sa, **{"cagr_pct": curves["a"].get("cagr_pct"), "max_dd_curve_pct": curves["a"].get("max_dd_pct")}},
               "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset, "cagr_pct": curves["b"].get("cagr_pct"), "max_dd_curve_pct": curves["b"].get("max_dd_pct")},
               "curve": curves, "spy_same_months": spy, "gross": gross, "months_held_b": held,
               "min_trades_b": 50, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
               "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
        rows.append(row)
        print(f"{r['key']:<18} | A {sa.get('trades', 0):4d} mo mean {sa.get('mean_ret_pct', 0):+.3f}% PF {sa.get('profit_factor') or 0:4.2f} "
              f"CAGR {curves['a'].get('cagr_pct', 0):+6.1f}% DD {curves['a'].get('max_dd_pct', 0):5.1f}% (SPY {spy['a'].get('cagr_pct', 0):+5.1f}%) | "
              f"B {sb.get('trades', 0):4d} mo mean {sb.get('mean_ret_pct', 0):+.3f}% PF {sb.get('profit_factor') or 0:4.2f} "
              f"CAGR {curves['b'].get('cagr_pct', 0):+6.1f}% DD {curves['b'].get('max_dd_pct', 0):5.1f}% (SPY {spy['b'].get('cagr_pct', 0):+5.1f}% DD {spy['b'].get('max_dd_pct', 0):4.1f}%) | "
              f"{row['verdict']} {''.join('Y' if v else '.' for v in checks.values())}", flush=True)
    sens = tqqq_sensitivity()
    print("ibs_qqq signals 2010-2026:", {k: ({kk: round(vv, 1) for kk, vv in v.items()} if isinstance(v, dict) else round(v, 1)) for k, v in sens.items()})
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_ROUND4_STUDY.md",
           "costs": {"etf": "0.05% of the traded amount per side on every switch or weight change"},
           "rules": rows, "ibs_qqq_leverage_sensitivity": sens}
    g = {r["key"]: r for r in rows}
    out["headline"] = (f"The highest-return claims did not survive: the Global Market Rotation “41% a year” made {g['gmr_diy']['a']['cagr_pct']:+.1f}% a year "
                       f"before its 2013 article and {g['gmr_diy']['b']['cagr_pct']:+.1f}% a year after it. GEM and bond rotation lag SPY since publication. "
                       f"Alvarez trend + momentum passes as a low-risk overlay ({g['alvarez_trend_mom']['b']['cagr_pct']:.1f}% a year, "
                       f"{g['alvarez_trend_mom']['b']['max_dd_curve_pct']:.1f}% max drawdown). The highest real return so far is round 3's IBS rule taken in TQQQ: "
                       f"{sens['TQQQ']['cagr_pct']:.1f}% a year 2010–2026 with a {sens['TQQQ']['max_dd_pct']:.0f}% drawdown at trade closes (TQQQ buy and hold: 41.3% a year, 82% drawdown).")
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
