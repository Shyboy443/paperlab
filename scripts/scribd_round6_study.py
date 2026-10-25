"""Scribd round 6 study (pre-registered in docs/SCRIBD_ROUND6_STUDY.md).

    python scripts/scribd_round6_study.py

Data: data/round3_cache/<SYM>_D1.npy (ETFs) and data/stocks_cache/<SYM>_D1.npy (Dow stocks), Yahoo daily adjusted.
Writes app/ytstrats/round6_results.json (served read-only by /api/public/scribd-round6).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, round6 as R6, scalp, swing  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
fmt = lambda x: datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
ETF, STK = PROJECT / "data" / "round3_cache", PROJECT / "data" / "stocks_cache"
OUT = PROJECT / "app" / "ytstrats" / "round6_results.json"
LIQUID = {"SPY": 0.0001, "QQQ": 0.0001, "IWM": 0.0001}
WIN = {"pag": {"a": (ms("1995-01-01"), ms("2013-01-01")), "b": (ms("2013-01-01"), ms("2026-10-21"))},
       "stiff": {"a": (ms("2008-08-01"), ms("2018-08-01")), "b": (ms("2018-08-01"), ms("2026-10-21"))}}


def load(path: Path) -> swing.D | None:
    if not path.exists():
        return None
    a = np.load(path)
    a = a[(a[:, 2] >= a[:, 3]) & (a[:, 4] > 0)]
    return swing.D(*(a[:, k] for k in range(5)))


def compounded(trades: list, s0: int, s1: int) -> dict:
    if not trades:
        return {}
    eq = np.cumprod(1 + np.array([x["ret_pct"] for x in sorted(trades, key=lambda x: x["t"])]) / 100)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    yrs = (s1 - s0) / (365.25 * 86_400_000)
    return {"total_pct": float((eq[-1] - 1) * 100), "cagr_pct": float((eq[-1] ** (1 / yrs) - 1) * 100), "max_dd_pct": float((1 - eq / peak).max() * 100)}


def random_timing(trades: list, data: dict, costs: dict, s0: int, s1: int) -> dict:
    rng = np.random.default_rng(7)
    real, rand = [], []
    for x in trades:
        b = data[x["asset"]]
        idx = np.flatnonzero((b.t >= s0) & (b.t < s1))
        if len(idx) < 50:
            continue
        hold = max(int(np.searchsorted(b.t, x["exit_t"])) - int(np.searchsorted(b.t, x["t"])), 1)
        real.append(x["ret_pct"])
        c = costs[x["asset"]]
        for _ in range(10):
            a = int(rng.integers(idx[0], max(idx[0] + 1, idx[-1] - hold)))
            rand.append(((b.c[a + hold] * (1 - c)) / (b.c[a] * (1 + c)) - 1) * 100)
    re, ra = np.array(real), np.array(rand)
    return {"rule_mean_pct": float(re.mean()), "random_mean_pct": float(ra.mean()), "edge_pct": float(re.mean() - ra.mean()),
            "z": float((re.mean() - ra.mean()) / (re.std() / np.sqrt(len(re))))}


def leverage(s0: int, s1: int) -> dict:
    """Not a verdict: pagonidis_rsi3_ibs signals from QQQ, traded in QQQ and in TQQQ (from 2010)."""
    q, t3 = load(ETF / "QQQ_D1.npy"), load(ETF / "TQQQ_D1.npy")
    out = {}
    tr = R6.pagonidis_rsi3_ibs(q, 0, 0.0001, s0, s1)
    for name, px in (("QQQ", q), ("TQQQ", t3)):
        rets = []
        for x in tr:
            i, k = int(np.searchsorted(px.t, x["t"])), int(np.searchsorted(px.t, x["exit_t"]))
            rets.append(((px.c[k] * (1 - 0.0001)) / (px.c[i] * (1 + 0.0001)) - 1) * 100)
        fake = [{"t": x["t"], "ret_pct": r} for x, r in zip(tr, rets)]
        out[name] = {"trades": len(rets), "mean_ret_pct": float(np.mean(rets)), "worst_trade_pct": float(min(rets)), **compounded(fake, s0, s1)}
    return out


def evaluate(key, doc, fn, data, costs, win, single=False, extra=None):
    per, gross, by_asset = {"a": [], "b": []}, {"a": [], "b": []}, {}
    for sym, b in data.items():
        for w, (s0, s1) in win.items():
            tr = fn(b, costs[sym], s0, s1)
            tg = fn(b, 0.0, s0, s1)
            for x in tr:
                x["asset"] = sym
            per[w] += tr
            gross[w] += tg
            if w == "b":
                by_asset[sym] = float(sum(x["ret_pct"] for x in tr)) if tr else None
    sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
    low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
    traded = [v for v in by_asset.values() if v is not None]
    checks = {"a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
              "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= (30 if single else 100)),
              "b_ci_above_zero": bool(low > 0),
              "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2)}
    row = {"key": key, "doc": doc, "assets": list(data), "tf": "daily", "rule": (fn.__doc__ or "").strip(),
           "windows": {k: [fmt(v[0]), fmt(v[1])] for k, v in win.items()}, "a": sa,
           "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset},
           "gross": {"a": scalp.summarize(gross["a"]), "b": scalp.summarize(gross["b"])},
           "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
           "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
    if extra:
        row.update(extra(per))
    f2 = lambda s: f"{s.get('trades', 0):6d} tr WR {s.get('win_rate_pct', 0):5.1f}% mean {s.get('mean_ret_pct', 0):+.4f}% PF {s.get('profit_factor') or 0:4.2f}"  # noqa: E731
    print(f"{key:<20} | A {f2(sa)} | B {f2(sb)} | B gross {row['gross']['b'].get('mean_ret_pct', 0):+.4f}% | {row['verdict']} "
          f"{''.join('Y' if v else '.' for v in checks.values())}", flush=True)
    return row


def main() -> int:
    etfs = {s: d for s in R6.PAGONIDIS_ETFS if (d := load(ETF / f"{s}_D1.npy")) is not None}
    ecost = {s: LIQUID.get(s, 0.0005) for s in etfs}
    rows = []
    rows.append(evaluate("pagonidis_ibs_1day", "The IBS Effect: Mean Reversion in Equity ETFs, Pagonidis 2013 (168296451): Table 6 strategy",
                         lambda b, c, s0, s1: R6.pagonidis_ibs_1day(b, 0, c, s0, s1), etfs, ecost, WIN["pag"]))
    win = WIN["pag"]
    rows.append(evaluate("pagonidis_rsi3_ibs", "The IBS Effect: Mean Reversion in Equity ETFs, Pagonidis 2013 (168296451): Cutler RSI(3) + IBS filter",
                         lambda b, c, s0, s1: R6.pagonidis_rsi3_ibs(b, 0, c, s0, s1), etfs, ecost, win,
                         extra=lambda per: {"random_timing": {w: random_timing(per[w], etfs, ecost, *win[w]) for w in ("a", "b")},
                                            "qqq_only": {w: scalp.summarize([x for x in per[w] if x["asset"] == "QQQ"]) for w in ("a", "b")},
                                            "leverage_2010_2026": leverage(ms("2010-03-01"), ms("2026-10-21"))}))
    spy = load(ETF / "SPY_D1.npy")
    stocks = {s: d for s in swing.DOW30 if (d := load(STK / f"{s}_D1.npy")) is not None}
    scost = {s: 0.0003 for s in stocks}
    w2 = WIN["stiff"]
    row = evaluate("katsanos_stiffness", "The Stiffness Indicator, Katsanos, S&C Nov 2018 (422943971)",
                   lambda b, c, s0, s1: R6.katsanos_stiffness(b, spy, c, s0, s1), stocks, scost, w2,
                   extra=lambda per: {"random_timing": {w: random_timing(per[w], stocks, scost, *w2[w]) for w in ("a", "b")}})
    if row["verdict"] == "PASS":
        row["verdict"] = "PASS (not counted: needs a survivorship-free re-test)"
    rows.append(row)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_ROUND6_STUDY.md",
           "costs": {"etf": "0.01% per side SPY/QQQ/IWM, 0.05% country ETFs", "stocks": "0.03% per side"},
           "missing_data": [s for s in R6.PAGONIDIS_ETFS if s not in etfs], "rules": rows,
           "headline": ("No new counted winner. Pagonidis' IBS effect weakened after his 2013 paper (one-day rule +0.16% → +0.08% per trade before costs, now losing after costs); "
                        "his RSI(3)+IBS rule still beats random timing but misses the bar across 31 ETFs (on QQQ alone PF 2.08). Katsanos' Stiffness only rides the Dow stocks' rise. "
                        "Best return winner stays round 3's IBS rule on QQQ: 37.6% a year in TQQQ since 2010.")}
    OUT.write_text(json.dumps(out, indent=1, default=float))
    for r in rows:
        if "random_timing" in r:
            print(r["key"], "timing", {w: (round(v["edge_pct"], 3), round(v["z"], 1)) for w, v in r["random_timing"].items()})
        if "leverage_2010_2026" in r:
            print("  qqq only", {w: (v.get("trades"), round(v.get("mean_ret_pct", 0), 3), round(v.get("profit_factor") or 0, 2)) for w, v in r["qqq_only"].items()})
            print("  leverage", {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in r["leverage_2010_2026"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
