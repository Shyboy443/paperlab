"""Compare the saved pre-fix engine with the corrected engine on cached V8 bars.

No strategy selection or parameter tuning. Six coins, the existing active V8.3
strategy and fees; five warm-up days, then two chronological reporting halves.
Daily resets match the earlier cost studies. This is an engineering comparison
on previously studied data, not an independent out-of-sample profitability test.

Run: .venv/Scripts/python.exe scripts/execution_guard_study.py --workers 3
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import statistics
import sys
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
BACKUP = PROJECT / "snapshots/2026-10-04-execution-audit"
START, END = 1785369600000, 1790553600000
DAY = 86_400_000
SINCE = START + 5 * DAY
MID = SINCE + (END - SINCE) // 2
COINS = ("ETH", "SOL", "XRP", "DOGE", "ARB", "ENA")


def legacy_engine():
    """Load the preserved engine sources under independent module names."""
    replacements = {
        "from app.backtest.replay import": "from execution_legacy_replay import",
        "from app.live.scan_engine import": "from execution_legacy_scan import",
    }
    for name, rel in (("execution_legacy_replay", "app/backtest/replay.py"),
                      ("execution_legacy_scan", "app/live/scan_engine.py"),
                      ("execution_legacy_v8", "app/live/v8_engine.py")):
        path = BACKUP / rel
        source = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            source = source.replace(old, new)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
    return sys.modules["execution_legacy_v8"].LevelMakerEngineV8


def run(coin):
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.bybit_market import kline_candle
    from app.live.v8_engine import LevelMakerEngineV8
    from app.strategies.v8.arena import load_v8_scalpers

    symbol = coin + "USDT"
    raw = json.loads((PROJECT / f"data/v8_exit_study/{symbol}-{START}-{END}.json").read_text())
    bars = [kline_candle(symbol, r, "historical") for r in raw if int(r[0]) + 60_000 <= END]
    rules = {s: MarketRules(**r) for s, r in v8.load_freeze()["rules"].items()}
    result = {}
    for label, engine in (("BEFORE", legacy_engine()), ("AFTER", LevelMakerEngineV8)):
        settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
        eng = engine(settings, [symbol], rules={symbol: rules[symbol]}, seed=7,
                     execution=v8.EXECUTION_V8, fees=FEES_V6, fee_source="schedule",
                     sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                     max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True)
        eng.portfolio.closed_trades = deque(maxlen=None)
        cls = load_v8_scalpers()["V8.3"].for_class("SCALP")
        res = eng.run(cls, bars, since_ms=SINCE, leverage=20, signal_tf="5m", only_symbol=symbol,
                      reset_at=list(range(SINCE + DAY, END, DAY)))
        trades = [t for t in res.trades if t.entry_ts >= SINCE and t.exit_kind not in ("reset", "end_of_run")
                  and t.exit_ts < bars[-1].close_time]
        result[label] = {"rejects": res.rejects,
                         "trades": [{"ts": t.entry_ts, "r": t.r_multiple, "net": t.net, "fees": t.fees,
                                     "exit": t.exit_kind} for t in trades]}
    return {"coin": coin, **result}


def summarize(rows, label, lo=SINCE, hi=END):
    tr = [t for r in rows for t in r[label]["trades"] if lo <= t["ts"] < hi]
    gains = sum(t["net"] for t in tr if t["net"] > 0)
    losses = -sum(t["net"] for t in tr if t["net"] < 0)
    return {"trades": len(tr), "net_usdt": round(sum(t["net"] for t in tr), 4),
            "fees_usdt": round(sum(t["fees"] for t in tr), 4),
            "net_r_per_trade": round(statistics.mean(t["r"] for t in tr), 6) if tr else None,
            "profit_factor": round(gains / losses, 4) if losses else None,
            "win_rate": round(sum(t["net"] > 0 for t in tr) / len(tr), 4) if tr else None,
            "exits": dict(Counter(t["exit"] for t in tr))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = []
        for row in pool.map(run, COINS):
            rows.append(row)
            print(f"completed {row['coin']}", flush=True)
    summary = {label: {"all": summarize(rows, label), "first_half": summarize(rows, label, SINCE, MID),
                       "second_half": summarize(rows, label, MID, END)} for label in ("BEFORE", "AFTER")}
    out = {"protocol": __doc__, "start_ms": SINCE, "end_ms": END, "mid_ms": MID,
           "summary": summary, "by_coin": {r["coin"]: {label: summarize([r], label) for label in ("BEFORE", "AFTER")}
                                             for r in rows},
           "rejects": {label: dict(sum((Counter(r[label]["rejects"]) for r in rows), Counter()))
                       for label in ("BEFORE", "AFTER")}}
    modules = ("app/backtest/replay.py", "app/live/scan_engine.py", "app/live/v8_engine.py")
    out["source_sha256"] = {label: {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in modules}
                            for label, root in (("BEFORE", BACKUP), ("AFTER", PROJECT))}
    dest = PROJECT / "docs/EXECUTION_GUARD_STUDY.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
