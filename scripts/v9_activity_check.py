"""V9 activity check: how many trades per bot per SESSION would the V9 stock scalpers take on the last sessions?

    python scripts/v9_activity_check.py            (on the server: it needs the Alpaca paper keys for market data)

Replays recent Alpaca IEX 1m bars (regular sessions, silent minutes filled flat exactly as the live feed does) through
the same engine the live bots use: one position per bot, the session entry window, the 45-minute time stop, the
cooldown, legal sizing and 4x buying power all apply. Reports executed-trade COUNTS (and that nothing was held
overnight) -- an activity check, not a profitability test; it tunes nothing.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.live.stock_engine import StockReplayEngine as ReplayEngine  # noqa: E402
from app.competition import v9_config as v9  # noqa: E402
from app.competition.v6_config import AGGRESSIVE_V6, SizingV6  # noqa: E402
from app.core.types import MarketRules  # noqa: E402
from app.exchange.alpaca_client import AlpacaClient  # noqa: E402
from app.live.alpaca_market import AlpacaLiveMarket, session_tape  # noqa: E402
from app.live.v9_worker import alpaca_keys  # noqa: E402
from app.strategies.v9.stocks import load_v9_stock_scalpers  # noqa: E402

DAY = 86_400_000
TEST_SESSIONS = 10


def main() -> None:
    keys = alpaca_keys()
    if keys is None:
        raise SystemExit("no Alpaca paper keys (dashboard vault or ALPACA_PAPER_API_KEY / _SECRET)")
    now = int(time.time() * 1000)
    feed = AlpacaLiveMarket(v9.SYMBOLS, AlpacaClient("testnet", *keys), lambda *a: None,
                            warmup_from_ms=now - (v9.WARMUP_DAYS + 25) * DAY)
    asyncio.run(feed._refresh_calendar())
    sessions = feed.sessions
    done = [s for s in sessions.iv if s[1] <= now]
    test = done[-TEST_SESSIONS:]
    start, end = test[0][0], test[-1][1]
    raw = asyncio.run(feed.fetch_bars(list(v9.SYMBOLS), start - v9.WARMUP_DAYS * DAY, end))
    rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
    rows, overnight = [], 0
    for sym in v9.SYMBOLS:
        tape = session_tape(sym, raw.get(sym, []), sessions)
        filled = sum(1 for b in tape if b.source == "filled")
        for sid, base_cls in load_v9_stock_scalpers().items():
            eng = ReplayEngine(v9.settings_v9(), [sym], rules={sym: rules[sym]}, seed=7, execution=v9.EXECUTION_V9,
                               fees=v9.FEES_V9, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                               leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
            res = eng.run(base_cls.for_class("SCALP", session_at=sessions.at), tape, since_ms=start, leverage=v9.LEVERAGE_CAP,
                          signal_tf="5m", only_symbol=sym)
            trades = [t for t in res.trades if t.entry_ts >= start and t.exit_kind != "end_of_run"]
            held = sum(1 for t in trades if sessions.at(t.entry_ts) != sessions.at(max(t.entry_ts, t.exit_ts - 1)))
            overnight += held
            per = [sum(1 for t in trades if s[0] <= t.entry_ts < s[1]) for s in test]
            rows.append({"bot": f"{sid}-{sym}-5M", "signals": res.signals, "trades": len(trades),
                         "per_session": round(len(trades) / len(test), 2), "min_session": min(per), "max_session": max(per),
                         "held_overnight": held, "bars": len(tape), "flat_filled_pct": round(100 * filled / max(1, len(tape)), 2),
                         "refused": {k: v for k, v in sorted(res.rejects.items()) if v}})
    report = {"window": {"sessions": len(test), "from": time.strftime("%Y-%m-%d", time.gmtime(start / 1000)),
                         "to": time.strftime("%Y-%m-%d", time.gmtime(end / 1000))},
              "bots": rows, "median_per_session": sorted(r["per_session"] for r in rows)[len(rows) // 2],
              "held_overnight": overnight,
              "note": "executed-trade COUNTS only (not returns); recent Alpaca IEX 1m bars through the live engine"}
    (PROJECT / "docs" / "V9_ACTIVITY_CHECK.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['bot']:14s} {r['per_session']:5.2f}/session (min {r['min_session']}, max {r['max_session']})  "
              f"trades {r['trades']:3d}  signals {r['signals']:3d}  flat {r['flat_filled_pct']:5.2f}%  refused {r['refused']}")
    print("sessions:", report["window"], " median trades/session per bot:", report["median_per_session"],
          " held overnight:", overnight)


if __name__ == "__main__":
    main()
