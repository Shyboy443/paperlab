"""V8 time-stop study (operator, 2026-10-01: "don't the hard-coded trade close timers make many trades lose or close
before the profits?").

Live, 44% of V8 trades end on the time stop (avg -0.11 R). Same entries and the live exits (1.5R target, structural stop),
the 60-day window of docs/V8_EXIT_STUDY.json (its cached Bybit bars), control bots only -- only the time stop changes:

    X1    the live hold (V8.1 30 min, V8.2 / V8.3 45 min)
    X2    twice as long        X4    four times as long        NONE    no time stop (stop / target / the daily reset)

The study resets every book at midnight UTC (as the exit study did); those exits COUNT here and are reported.

DECISION RULE (fixed before running, the exit study's): per family, a longer hold replaces X1 only if its total net R
beats X1's in BOTH halves AND its profit factor is at least X1's; if several do, the highest total net R wins.

    python scripts/v8_hold_study.py
"""
from __future__ import annotations

import dataclasses
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
START, END = 1785369600000, 1790553600000          # the cached window of docs/V8_EXIT_STUDY.json
HOLDS = {"X1": 1.0, "X2": 2.0, "X4": 4.0, "NONE": None}


def run_coin(args):
    coin, rules_raw = args
    from app.backtest.replay import ReplayEngine
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.bybit_market import kline_candle
    from app.strategies.v8.arena import load_v8_scalpers
    symbol = coin + "USDT"
    raw = json.loads((PROJECT / "data" / "v8_exit_study" / f"{symbol}-{START}-{END}.json").read_text())
    tape = [kline_candle(symbol, r, "historical") for r in raw if int(r[0]) + 60_000 <= END]
    rules = {s: MarketRules(**r) for s, r in rules_raw.items()}
    out = []
    for sid, base_cls in load_v8_scalpers().items():
        for name, mult in HOLDS.items():
            class Held(base_cls):
                def on_candle(self, c, ctx, _m=mult):
                    sigs = super().on_candle(c, ctx)
                    for s in sigs:
                        if s.max_hold_s:
                            s.max_hold_s = None if _m is None else int(s.max_hold_s * _m)
                    return sigs
            Held.__name__ = f"{base_cls.__name__}_{name}"
            settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
            eng = ReplayEngine(settings, [symbol], rules={symbol: rules[symbol]}, seed=7, execution=v8.EXECUTION_V8,
                               fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                               max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
            res = eng.run(Held.for_class("SCALP"), tape, since_ms=START, leverage=20, signal_tf="5m", only_symbol=symbol,
                          reset_at=[START + k * DAY for k in range(1, (END - START) // DAY)])
            trades = [t for t in res.trades if t.entry_ts >= START and t.exit_kind != "end_of_run"]
            out.append({"bot": f"{sid}-{coin}", "family": sid, "hold": name,
                        "trades": [{"ts": t.entry_ts, "net": t.net, "r": t.r_multiple, "exit": t.exit_kind,
                                    "hold_min": t.hold_s / 60} for t in trades]})
    return out


def summarize(rows: list[dict]) -> dict:
    mid = (START + END) // 2
    res = {}
    for name in HOLDS:
        tr = [t for r in rows if r["hold"] == name for t in r["trades"]]
        wins = [t["net"] for t in tr if t["net"] > 0]
        losses = [t["net"] for t in tr if t["net"] <= 0]
        exits: dict[str, int] = {}
        for t in tr:
            exits[t["exit"]] = exits.get(t["exit"], 0) + 1
        res[name] = {"trades": len(tr), "win_rate": round(len(wins) / len(tr), 3) if tr else None,
                     "avg_R": round(statistics.fmean(t["r"] for t in tr), 4) if tr else None,
                     "net_R_total": round(sum(t["r"] for t in tr), 2),
                     "net_R_half1": round(sum(t["r"] for t in tr if t["ts"] < mid), 2),
                     "net_R_half2": round(sum(t["r"] for t in tr if t["ts"] >= mid), 2),
                     "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
                     "median_hold_min": round(statistics.median(t["hold_min"] for t in tr), 1) if tr else None,
                     "exits": exits}
    base = res["X1"]
    ok = [n for n, r in res.items() if n != "X1" and r["net_R_half1"] > base["net_R_half1"]
          and r["net_R_half2"] > base["net_R_half2"] and (r["profit_factor"] or 0) >= (base["profit_factor"] or 0)]
    return {"holds": res, "passing": ok, "decision": max(ok, key=lambda n: res[n]["net_R_total"]) if ok else "X1"}


def main() -> None:
    from app.competition import v8_config as v8
    from v8_freeze import rules_and_intervals
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
        rows = [r for part in pool.map(run_coin, [(c, rules_raw) for c in v8.COINS]) for r in part]
    report = {"window_days": (END - START) // DAY, "all": summarize(rows),
              "per_family": {sid: summarize([r for r in rows if r["family"] == sid]) for sid in ("V8.1", "V8.2", "V8.3")}}
    (PROJECT / "docs" / "V8_HOLD_STUDY.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for scope, s in [("ALL", report["all"])] + list(report["per_family"].items()):
        for name, r in s["holds"].items():
            print(f"{scope:5s} {name:4s} trades {r['trades']:5d} win {r['win_rate']} avg R {r['avg_R']} | h1 {r['net_R_half1']} "
                  f"h2 {r['net_R_half2']} pf {r['profit_factor']} | median hold {r['median_hold_min']} min | {r['exits']}")
        print(f"{scope:5s} decision: {s['decision']}  (passing: {s['passing']})")


if __name__ == "__main__":
    main()
