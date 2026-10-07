"""Shared execution layer: one model, used by backtest, competition and paper trading.

`ExecutionModel.execute(order, state)` is the only place that decides a fill price. The level it
used is recorded on the result and on every partial fill, so no analysis has to guess whether a
number came from a real order book or from an OHLCV estimate.
"""
from app.execution.config import ExecutionConfig, FeeSchedule
from app.execution.model import ExecutionModel, ExecutionResult, MarketState
from app.execution.orders import LiquidityRole, Order, OrderState, PartialFill

__all__ = ["ExecutionConfig", "ExecutionModel", "FeeSchedule", "ExecutionResult", "LiquidityRole", "MarketState", "Order",
           "OrderState", "PartialFill"]
