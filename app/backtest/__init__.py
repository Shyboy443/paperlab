"""Historical replay over the LIVE engine components.

The point of this package is that it owns no trading logic. Sizing, risk gates, fills, fees,
stops, take-profit ladders, trailing and liquidation all come from app.core.{portfolio,risk,positions}
exactly as the running lab uses them. The only thing added here is a price path: live gets ticks,
history gets OHLC, so `replay` synthesises an intrabar path (see ReplayEngine.PATH).
"""
from app.backtest.context import ReplayContext, default_rules
from app.backtest.replay import ReplayEngine, ReplayResult
from app.backtest.rules import load_rules

__all__ = ["ReplayContext", "ReplayEngine", "ReplayResult", "default_rules", "load_rules"]
