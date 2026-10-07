"""One fixed feasibility replay for V8; retrospective, never a profitability claim.

This uses the already cached September 18–25 Bybit tape and positioning.
Historical bid/ask arrival times are unavailable, so the live spread gate
cannot be reconstructed and these results can overstate executable activity.
"""
import datetime as dt
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine
from app.competition import v6_config as base
from app.competition.v6_features import PositioningFeedV6
from app.core.types import MarketRules
from app.live.bybit_market import kline_candle
from app.strategies.v8.scalp import load_v8

DAY = 86_400_000
HOUR = 3_600_000


def main():
    end = int(dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
    start = end - 7 * DAY
    rows = []
    frozen = base.load_freeze()
    for coin in frozen["universe"]["traded"]:
        symbol = coin + "USDT"
        source = Path("data/v7_activity")
        data = json.loads((source / f"{coin}-{end}.json").read_text())
        tape = json.loads((source / f"{coin}-{end}-1m.json").read_text())
        bars = [kline_candle(symbol, row, "historical") for row in tape]
        assert len(bars) == 35 * 1440 and all(b.open_time - a.open_time == 60_000
                                             for a, b in zip(bars, bars[1:]))
        rules = {symbol: MarketRules(**frozen["rules"][symbol])}
        caps = {t: {"oi": t, "funding": t} for t in range(start, end + HOUR, HOUR)}
        feed = PositioningFeedV6(symbol, funding=data["funding"], oi=data["oi"], caps=caps,
                                 funding_interval_h=8)
        for sid, cls in load_v8().items():
            eng = ReplayEngine(base.settings_v6(), [symbol], rules=rules, seed=7,
                               funding=FundingSchedule({symbol: [tuple(r) for r in data["funding"]]}),
                               execution=replace(base.EXECUTION_V6, signal_latency_ms=60_000 - 400 + 1),
                               fees=base.FEES_V6, fee_source="schedule",
                               sizing=base.SizingV6(rules, jev=False), leverage_policy="needed",
                               max_risk_pct=0.02)
            result = eng.run(cls.for_class("SCALP", feed), bars, since_ms=start, leverage=20,
                             signal_tf=cls.signal_tf, only_symbol=symbol)
            equity = [v for ts, v in result.equity if ts >= start]
            peak, dd = 20.0, 0.0
            for value in equity:
                peak = max(peak, value)
                dd = max(dd, 1 - value / peak)
            row = {"bot": f"{sid}-{coin}", "trades": len(result.trades), "signals": result.signals,
                   "net_usdt": round(equity[-1] - 20, 6), "max_drawdown": round(dd, 6),
                   "fees": round(sum(f.fee for f in result.fills), 6), "halted": result.halted,
                   "rejects": result.rejects}
            rows.append(row)
            print(json.dumps(row), flush=True)
    report = {"window": "2026-09-18..2026-09-25 UTC", "bots": rows,
              "total_net_usdt": round(sum(r["net_usdt"] for r in rows), 6),
              "total_trades": sum(r["trades"] for r in rows), "starting_usdt": 160,
              "limitations": "Retrospective week, no holdout; modelled spreads, no historical bid/ask timestamps or "
                             "REST arrival times; live spread gate not reconstructed; end-of-run exits included. "
                             "No proven edge."}
    Path("docs/V8_REPLAY_CHECK.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
