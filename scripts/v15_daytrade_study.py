"""V15 day traders: do any day-trading families make money on US stocks over two years, after costs? (operator,
2026-10-08: "improve the bots ... also add some day trading bots"; chosen market: US stocks.)

    python scripts/v15_daytrade_study.py --data-dir /data/bt2y --workers 8     (on the server: the two-year archive)

PRE-REGISTERED (2026-10-08, before any family was run; one shot -- no parameter is changed after the results)

Families (app/strategies/v15/daytrade.py, exactly as written there):
- V15.1 opening-candle trend, V15.2 stocks in play, V15.3 30-minute breakout, V15.4 noise-band momentum,
  V15.5 last-half-hour momentum. Each at most one trade per symbol per session, flat by the close.

Data
- The Alpaca IEX 1m tape of scripts/backtest_2y.py (fetch9), regular sessions, the 10 V9 symbols:
  SPY, QQQ, AAPL, NVDA, TSLA, AMD, MSFT, META, AMZN, GOOGL.
- Window: 2024-10-01 .. 2026-10-01 with 30 days of warm-up. Year 1 ends at 2025-10-01.

Book
- 1000 USD, reset every day. Halts off. The live V9 sizing (1% risk), fees (0.2 bp a side), execution (60 s decision
  latency, the IEX half-spread capped at 1 bp, stops at the crossed level plus friction, opening gaps at the open) and
  3x cap, through the same StockReplayEngine the live stock bots use. Nothing held overnight.

Verdict, per family over its 10 bots (summed):
- PASS only if net USD > 0 AND mean net R per trade > 0 in BOTH years, profit factor >= 1.10 over the two years,
  at least 6 of the 10 symbols net positive over the two years, and at least 100 trades in total.
- A passing family goes live (paper) on all 10 symbols. A failing family does not go live; nothing is re-tuned.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.backtest_2y import DAY, END, MID, START, stock_tape  # noqa: E402

OUT = PROJECT / "docs" / "V15_DAYTRADE_STUDY.json"
FAMILIES = ("V15.1", "V15.2", "V15.3", "V15.4", "V15.5")


def run(job: tuple[str, str], data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    sid, sym = job
    from app.live.stock_engine import StockReplayEngine as ReplayEngine
    from app.competition import v9_config as v9
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v15.daytrade import load_v15_day_traders
    rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
    bars, sessions = stock_tape(Path(data_dir), sym)
    cls = load_v15_day_traders()[sid].for_class("DAY", session_at=sessions.at)
    eng = ReplayEngine(dataclasses.replace(v9.settings_v9(), strategy_halt_pct=1.0, daily_halt_pct=1.0), [sym],
                       rules={sym: rules[sym]}, seed=7, funding=None, execution=v9.EXECUTION_V9, fees=v9.FEES_V9,
                       fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bars, since_ms=START, leverage=v9.LEVERAGE_CAP, signal_tf="5m", only_symbol=sym,
                  reset_at=list(range(START + DAY, END, DAY)))
    eng.portfolio.closed_trades = deque()
    trades = [(t.entry_ts, t.net, t.r_multiple, t.fees, t.exit_kind, t.side) for t in res.trades if t.entry_ts >= START]
    n_sessions = sum(1 for a, b in sessions.iv if START <= a < END)
    return {"sid": sid, "symbol": sym, "trades": trades, "sessions": n_sessions}


def summarize(trades: list[tuple]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, part in (("all", trades), ("year1", [t for t in trades if t[0] < MID]),
                        ("year2", [t for t in trades if t[0] >= MID])):
        n = len(part)
        if not n:
            out[label] = {"trades": 0, "net_usd": 0.0, "net_r": 0.0}
            continue
        wins = sum(t[1] for t in part if t[1] > 0)
        losses = -sum(t[1] for t in part if t[1] < 0)
        out[label] = {"trades": n, "net_usd": round(sum(t[1] for t in part), 2),
                      "net_r": round(sum(t[2] for t in part) / n, 4), "win": round(sum(t[1] > 0 for t in part) / n, 3),
                      "pf": round(wins / losses, 3) if losses else None, "fees": round(sum(t[3] for t in part), 2),
                      "exits": {k: sum(1 for t in part if t[4] == k) for k in sorted({t[4] for t in part})},
                      "long": sum(1 for t in part if t[5] == "long")}
    return out


def main() -> int:
    from app.competition import v9_config as v9
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    jobs = [(sid, sym) for sid in FAMILIES for sym in v9.SYMBOLS]
    fam: dict[str, list] = {sid: [] for sid in FAMILIES}
    bots: dict[str, Any] = {}
    sessions = 0
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(run, j, args.data_dir) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            fam[r["sid"]].extend(r["trades"])
            sessions = max(sessions, r["sessions"])
            key = r["sid"] + "-" + r["symbol"]
            bots[key] = summarize(r["trades"])
            print(f"{i}/{len(jobs)} {key}: {len(r['trades'])} trades net {bots[key]['all']['net_usd']} "
                  f"({time.time() - t0:.0f}s)", flush=True)
    results = {sid: summarize(tr) for sid, tr in fam.items()}
    verdict = {}
    for sid, s in results.items():
        positive = sum(1 for k, b in bots.items() if k.startswith(sid + "-") and b["all"]["net_usd"] > 0)
        checks = {
            "both_years_net_usd": all(s[y]["trades"] and s[y]["net_usd"] > 0 for y in ("year1", "year2")),
            "both_years_net_r": all(s[y]["trades"] and s[y]["net_r"] > 0 for y in ("year1", "year2")),
            "profit_factor_1_10": (s["all"].get("pf") or 0) >= 1.10,
            "six_symbols_positive": positive >= 6,
            "100_trades": s["all"]["trades"] >= 100}
        verdict[sid] = {"pass": all(checks.values()), "checks": checks, "symbols_positive": positive,
                        "trades_per_symbol_session": round(s["all"]["trades"] / (10 * max(1, sessions)), 3)}
    for sid in FAMILIES:
        s = results[sid]
        print(f"{sid} {'PASS' if verdict[sid]['pass'] else 'FAIL'}  "
              + "  ".join(f"{y}: {s[y].get('trades')} tr net {s[y].get('net_usd')} R {s[y].get('net_r')} pf {s[y].get('pf')}"
                          for y in ("year1", "year2")) + f"  symbols+ {verdict[sid]['symbols_positive']}/10")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                               "sessions": sessions, "results": results, "verdict": verdict, "bots": bots}, indent=1),
                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
