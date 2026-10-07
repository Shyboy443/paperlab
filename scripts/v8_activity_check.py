"""V8 activity check: how many trades per bot per day would the frozen-to-be V8 scalpers take on the last week?

    python scripts/v8_activity_check.py

Counts EXECUTED trades (one position per bot, the 45-minute time stop, the cooldown, legal sizing and the fee guard
all apply) from a replay of recent Bybit 1m bars through the same engine the live bots use. Reports trade counts
only: this is an activity target check, not a profitability test, and it tunes nothing.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402
from app.backtest.replay import ReplayEngine  # noqa: E402
from app.competition import v8_config as v8  # noqa: E402
from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6  # noqa: E402
from app.core.types import MarketRules  # noqa: E402
from app.live.bybit_market import kline_candle  # noqa: E402
from app.strategies.v8.arena import load_v8_scalpers  # noqa: E402
sys.path.insert(0, str(PROJECT / "scripts"))
from v8_freeze import rules_and_intervals  # noqa: E402

DAY = 86_400_000
TEST_DAYS = 7


def bars(symbol: str, start: int, end: int, cache: Path) -> list:
    f = cache / f"{symbol}-{start}-{end}.json"
    if f.exists():
        rows = json.loads(f.read_text())
    else:
        rows = bb.klines(symbol, "1", start, end)
        f.write_text(json.dumps(rows))
    return [kline_candle(symbol, r, "historical") for r in rows if int(r[0]) + 60_000 <= end]


def run_coin(coin: str, start: int, end: int, rules: dict, cache: Path) -> list[dict]:
    symbol = coin + "USDT"
    tape = bars(symbol, start - v8.WARMUP_DAYS * DAY, end, cache)
    out = []
    for sid, base_cls in load_v8_scalpers().items():
        eng = ReplayEngine(settings_v6(), [symbol], rules={symbol: rules[symbol]}, seed=7, execution=v8.EXECUTION_V8,
                           fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                           leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
        res = eng.run(base_cls.for_class("SCALP"), tape, since_ms=start, leverage=20, signal_tf="5m", only_symbol=symbol)
        trades = [t for t in res.trades if t.entry_ts >= start and t.exit_kind != "end_of_run"]
        out.append({"bot": f"{sid}-{coin}-5M", "signals": res.signals, "trades": len(trades),
                    "trades_per_day": round(len(trades) / TEST_DAYS, 1),
                    "refused": {k: v for k, v in sorted(res.rejects.items()) if v}})
    return out


def main() -> None:
    end = int(time.time() * 1000) // DAY * DAY
    start = end - TEST_DAYS * DAY
    cache = PROJECT / "data" / "v8_activity"
    cache.mkdir(parents=True, exist_ok=True)
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    rules = {s: MarketRules(**r) for s, r in rules_raw.items()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [r for part in pool.map(lambda c: run_coin(c, start, end, rules, cache), v8.COINS) for r in part]
    report = {"window": {"from": time.strftime("%Y-%m-%d", time.gmtime(start / 1000)),
                         "to": time.strftime("%Y-%m-%d", time.gmtime(end / 1000)), "days": TEST_DAYS},
              "target_trades_per_bot_per_day": 10, "bots": rows,
              "median_trades_per_day": sorted(r["trades_per_day"] for r in rows)[len(rows) // 2],
              "note": "executed-trade COUNTS only (not returns); recent Bybit 1m bars through the live engine"}
    (PROJECT / "docs" / "V8_ACTIVITY_CHECK.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['bot']:14s} {r['trades_per_day']:5.1f}/day  trades {r['trades']:4d}  signals {r['signals']:4d}  refused {r['refused']}")
    print("median trades/day per bot:", report["median_trades_per_day"])


if __name__ == "__main__":
    main()
