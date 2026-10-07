"""MarketCtx: the MarketContext implementation handed to strategies by the Engine."""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Sequence

from app.core.indicators import last as _last
from app.core.signals import SignalBoard
from app.core.types import BookSnapshot, Candle, FundingInfo, MarketRules, Tick, VirtualPosition

if TYPE_CHECKING:  # pragma: no cover
    from app.core.engine import Engine


class MarketCtx:
    def __init__(self, e: "Engine"):
        self.e = e

    @property
    def symbols(self) -> list[str]:
        return self.e.symbols

    @property
    def board(self) -> SignalBoard:
        return self.e.board

    def now_ms(self) -> int:
        return self.e.clock()

    def venue_kind(self) -> str:
        return self.e.settings.venue.caps.kind

    def candles(self, symbol: str, tf: str) -> Sequence[Candle]:
        return self.e.candles.get((symbol, tf), ())

    def forming(self, symbol: str, tf: str) -> Candle | None:
        return self.e.forming.get((symbol, tf))

    def last_price(self, symbol: str) -> float | None:
        return self.e.last.get(symbol)

    def mark_price(self, symbol: str) -> float | None:
        m = self.e.marks.get(symbol)
        return m.mark if m else self.e.last.get(symbol)

    def book(self, symbol: str) -> BookSnapshot | None:
        return self.e.books.get(symbol)

    def funding(self, symbol: str) -> FundingInfo | None:
        return self.e.fundings.get(symbol)

    def ind(self, symbol: str, tf: str, name: str, **params: Any) -> Any:
        return self.e.cache.get(symbol, tf, name, self.e.candles.get((symbol, tf), deque()), **params)

    def ind_last(self, symbol: str, tf: str, name: str, **params: Any) -> float | None:
        series = self.ind(symbol, tf, name, **params)
        if isinstance(series, tuple):
            series = series[0]
        return _last(series)

    def positions_of(self, strategy_id: str) -> list[VirtualPosition]:
        return self.e.portfolio.positions_of(strategy_id)

    def rules(self, symbol: str) -> MarketRules:
        return self.e.rules[symbol]

    def recent_trades(self, symbol: str, window_ms: int) -> list[Tick]:
        cutoff = self.e.clock() - window_ms
        return [t for t in self.e.ticks.get(symbol, ()) if t.ts >= cutoff]

    def is_enabled(self, strategy_id: str) -> bool:
        m = self.e.meta.get(strategy_id)
        return bool(m and m.enabled)

    def wallet_info(self, strategy_id: str) -> dict[str, float]:
        pf = self.e.portfolio
        w = pf.wallets.get(strategy_id)
        if w is None:
            return {"allocation": 0.0, "equity": 0.0, "margin_used": 0.0, "available": 0.0}
        prices = self.e.prices()
        equity = pf.wallet_equity(strategy_id, prices)
        used = pf.margin_used(strategy_id)
        return {"allocation": w.allocation, "equity": equity, "margin_used": used, "available": equity - used}
