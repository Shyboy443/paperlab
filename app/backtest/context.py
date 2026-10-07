"""MarketContext backed by a replayed candle tape and a real Portfolio.

Differs from tests/conftest.FakeCtx in the two ways that matter for a backtest:

* `positions_of` / `wallet_info` read the real Portfolio, so a strategy inspecting its own book
  during replay sees what it would see live (FakeCtx always answered "no positions, 500 USDT").
* candles for a timeframe are aggregated from the 1m tape by the engine and pushed here, so
  `ind()` runs the production IndicatorCache over the same bars the live feed would have built.

Book / funding / tick accessors return nothing: historical klines carry none of it. Strategies that
need them are refused up front by ReplayEngine rather than silently backtested on empty data.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Sequence

from app.backtest.rules import fallback_rules
from app.core.indicators import IndicatorCache, last as _last
from app.core.portfolio import Portfolio
from app.core.signals import SignalBoard
from app.core.types import BookSnapshot, Candle, FundingInfo, MarketRules, Tick, VirtualPosition

def default_rules(symbols: Sequence[str]) -> dict[str, MarketRules]:
    """Offline filters. Prefer `app.backtest.rules.load_rules(settings, symbols)`, which reads the
    venue's real exchangeInfo; this exists for callers that have no settings to hand."""
    return fallback_rules(symbols)


class ReplayContext:
    """Production MarketContext protocol over a historical tape."""

    def __init__(self, portfolio: Portfolio, rules: dict[str, MarketRules], symbols: Sequence[str],
                 venue: str = "futures", maxlen: int = 600):
        self.portfolio = portfolio
        self.rules_map = dict(rules)
        self.symbols = list(symbols)
        self.venue = venue
        self.cache = IndicatorCache()
        self.board = SignalBoard()
        self.prices: dict[str, float] = {}
        self._now = 0
        self._maxlen = maxlen
        self._candles: dict[tuple[str, str], deque[Candle]] = {}

    # -- tape ----------------------------------------------------------------------------
    def push(self, c: Candle) -> None:
        dq = self._candles.setdefault((c.symbol, c.tf), deque(maxlen=self._maxlen))
        dq.append(c)
        self.cache.invalidate(c.symbol, c.tf)

    def set_price(self, symbol: str, price: float, ts: int) -> None:
        self.prices[symbol] = price
        self._now = max(self._now, ts)

    def latest(self, symbol: str, tf: str, close_time: int) -> Candle | None:
        """The bar for `tf` that closed exactly at `close_time`, or None."""
        dq = self._candles.get((symbol, tf))
        if not dq:
            return None
        bar = dq[-1]
        return bar if bar.close_time == close_time else None

    # -- MarketContext -------------------------------------------------------------------
    def now_ms(self) -> int:
        return self._now

    def venue_kind(self) -> str:
        return self.venue

    def candles(self, symbol: str, tf: str) -> Sequence[Candle]:
        return self._candles.get((symbol, tf), ())

    def forming(self, symbol: str, tf: str) -> Candle | None:
        return None

    def last_price(self, symbol: str) -> float | None:
        return self.prices.get(symbol)

    def mark_price(self, symbol: str) -> float | None:
        # No mark stream in klines; last price is the honest stand-in. It makes paper liquidation
        # trigger off the traded price rather than a smoothed mark, which is the stricter of the two.
        return self.prices.get(symbol)

    def book(self, symbol: str) -> BookSnapshot | None:
        return None

    def funding(self, symbol: str) -> FundingInfo | None:
        return None

    def ind(self, symbol: str, tf: str, name: str, **params: Any) -> Any:
        return self.cache.get(symbol, tf, name, self._candles.get((symbol, tf), deque()), **params)

    def ind_last(self, symbol: str, tf: str, name: str, **params: Any) -> float | None:
        series = self.ind(symbol, tf, name, **params)
        if isinstance(series, tuple):
            series = series[0]
        return _last(series)

    def positions_of(self, strategy_id: str) -> list[VirtualPosition]:
        return self.portfolio.positions_of(strategy_id)

    def rules(self, symbol: str) -> MarketRules:
        r = self.rules_map.get(symbol)
        if r is None:
            r = default_rules([symbol])[symbol]
            self.rules_map[symbol] = r
        return r

    def recent_trades(self, symbol: str, window_ms: int) -> list[Tick]:
        return []

    def is_enabled(self, strategy_id: str) -> bool:
        return self.board.is_enabled(strategy_id)

    def wallet_info(self, strategy_id: str) -> dict[str, float]:
        equity = self.portfolio.wallet_equity(strategy_id, self.prices)
        used = self.portfolio.margin_used(strategy_id)
        w = self.portfolio.wallets.get(strategy_id)
        return {"allocation": w.allocation if w else 0.0, "equity": equity, "margin_used": used,
                "available": self.portfolio.available(strategy_id, self.prices)}
