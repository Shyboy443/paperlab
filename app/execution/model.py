"""ExecutionModel: one place that turns an order plus a market state into fills.

Three levels, best-available-first. The level actually used is recorded on every fill and every
result; the model never degrades silently.

    LEVEL 1  order book      walk the far side level by level, VWAP the result, allow partial fills
    LEVEL 2  bid/ask         BUY fills at or above ask, SELL at or below bid, plus modelled impact
    LEVEL 3  OHLCV           half-spread + market impact estimated from volatility and order size

Level 3 is what a multi-year historical tournament actually runs on, because the Binance archive
publishes no historical order books (see docs: bookDepth is aggregated ±1%..±5% notional, not price
levels) and best bid/ask only for a limited window. It is therefore written to lean conservative:
every term is a cost, none of them can be favourable, and the size term grows with the fraction of
available liquidity the order consumes.

Two invariants the tests pin down:

* A fill price is never better than the decision price. Slippage is a cost in every level.
* `spread_cost` and `impact_cost` are a DECOMPOSITION of the total slippage, not extra charges.
  They sum to the same number `metrics.slippage_usdt` derives from (fill - decision) * qty, so
  exposing them cannot double-count. There is a test for exactly this.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.execution.config import ExecutionConfig, FeeSchedule
from app.core.types import BookSnapshot
from app.execution.orders import LiquidityRole, Order, PartialFill, remainder_policy

log = logging.getLogger("paperlab.execution")


@dataclass(frozen=True)
class MarketState:
    """Everything the model is allowed to see at execution time, and nothing later.

    The caller is responsible for only ever constructing this from information that existed at or
    before `ts`. ReplayEngine builds it from the bar an order executes against, never from the bar
    the signal was generated on.
    """
    symbol: str
    ts: int
    last: float
    bid: float | None = None
    ask: float | None = None
    book: BookSnapshot | None = None
    atr: float | None = None                    # volatility, in price units
    bar_range: float | None = None              # high - low of the executing bar
    quote_volume: float | None = None           # liquidity proxy for the executing bar
    depth_notional_1pct: float | None = None    # from the bookDepth archive, when loaded
    tick: float | None = None                   # symbol tick size; the top of book is one tick wide

    def mid(self) -> float:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return self.last


@dataclass
class ExecutionResult:
    order_id: str
    execution_level_used: int
    level_reason: str                       # why this level and not a better one
    role: LiquidityRole | None
    fills: list[PartialFill] = field(default_factory=list)
    decision_price: float = 0.0
    best_bid: float | None = None
    best_ask: float | None = None
    book_depth_consumed: float = 0.0        # notional taken out of the book (L1)
    latency_cost: float = 0.0               # decision price -> market price when the order arrived
    spread_cost: float = 0.0                # market price -> the touch we had to pay
    impact_cost: float = 0.0                # the touch -> the average price actually achieved
    rejected: str = ""
    requested_qty: float = 0.0
    # What happened to the part that did not fill: "" (nothing left), "EXPIRED" (MARKET / IOC /
    # FOK remainder, gone) or "RESTING" (GTC / GTD remainder, still on the book). Never decided by
    # the remainder's size: a resting remainder below minNotional is legal on Binance.
    remainder: str = ""

    @property
    def filled_qty(self) -> float:
        return sum(f.qty for f in self.fills)

    @property
    def avg_price(self) -> float:
        q = self.filled_qty
        return sum(f.price * f.qty for f in self.fills) / q if q > 0 else 0.0

    @property
    def slippage_usdt(self) -> float:
        """Total execution cost against the decision price.

        `latency_cost + spread_cost + impact_cost` is a decomposition of this same number, never an
        addition to it. Whoever charges the wallet charges exactly one of the two forms.

        The three terms answer different questions. Latency is how far the market moved while the
        order was in flight -- nothing to do with liquidity. Spread is what it cost to cross to the
        far side. Impact is what it cost to be big. A strategy can be killed by any one of them and
        the fix is different in each case.
        """
        if not self.fills or not self.decision_price:
            return 0.0
        return abs(self.avg_price - self.decision_price) * self.filled_qty

    def to_dict(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "execution_level_used": self.execution_level_used,
                "level_reason": self.level_reason, "role": self.role,
                "decision_price": self.decision_price, "best_bid": self.best_bid,
                "best_ask": self.best_ask, "filled_qty": self.filled_qty,
                "avg_price": self.avg_price, "book_depth_consumed": self.book_depth_consumed,
                "latency_cost": self.latency_cost, "spread_cost": self.spread_cost,
                "impact_cost": self.impact_cost,
                "rejected": self.rejected, "requested_qty": self.requested_qty,
                "remainder": self.remainder}

    @property
    def partial(self) -> bool:
        return bool(self.fills) and self.filled_qty < self.requested_qty - 1e-12


class ExecutionModel:
    def __init__(self, config: ExecutionConfig | None = None, fees: FeeSchedule | None = None):
        self.config = config or ExecutionConfig()
        self.fees = fees or FeeSchedule()

    # -- level selection -------------------------------------------------------------------
    def available_level(self, state: MarketState) -> tuple[int, str]:
        """The best level this market state can actually support, with the reason."""
        want = self.config.level
        # A one-sided book still selects L1: the missing side is *insufficient depth*, which
        # _fill_book reports honestly. Degrading to L2 there would fill the whole order at the
        # touch and quietly pretend the liquidity existed.
        if want <= 1 and state.book and (state.book.asks or state.book.bids):
            return 1, "order book available"
        if want <= 2 and state.bid and state.ask:
            return 2, "bid/ask available" if want == 2 else "requested L1, no book: bid/ask"
        reason = "OHLCV only"
        if want == 1:
            reason = "requested L1, no book and no quote: OHLCV"
        elif want == 2:
            reason = "requested L2, no quote: OHLCV"
        return 3, reason

    # -- entry point -----------------------------------------------------------------------
    def execute(self, order: Order, state: MarketState) -> ExecutionResult:
        """Price and fill `order`, then settle any unfilled remainder the way Binance would."""
        res = self._execute(order, state)
        return self._settle_remainder(order, res)

    def _settle_remainder(self, order: Order, res: ExecutionResult) -> ExecutionResult:
        res.requested_qty = order.qty
        if res.rejected:
            return res
        left = order.qty - res.filled_qty
        if left <= 1e-12:
            res.remainder = ""
            return res
        policy = remainder_policy(order.order_type, order.time_in_force)
        if policy == "ALL_OR_NONE":
            # FOK: a partial is not allowed to exist at all.
            res.fills.clear()
            res.role = None
            res.latency_cost = res.spread_cost = res.impact_cost = 0.0
            res.book_depth_consumed = 0.0
            res.rejected = "fok_not_fully_fillable"
            res.remainder = "EXPIRED"
            return res
        res.remainder = "RESTING" if policy == "REST" else "EXPIRED"
        return res

    def _execute(self, order: Order, state: MarketState) -> ExecutionResult:
        level, reason = self.available_level(state)
        res = ExecutionResult(order.id, level, reason, None,
                              decision_price=order.decision_price or state.last,
                              best_bid=state.bid, best_ask=state.ask)
        if order.qty <= 0:
            res.rejected = "zero_quantity"
            return res
        if state.ts < order.execute_at:
            res.rejected = "before_execute_at"
            return res

        crosses = order.crosses(state.bid, state.ask)
        if order.order_type == "LIMIT" and crosses and order.time_in_force == "GTX":
            res.rejected = "post_only_would_take"     # Binance rejects a GTX order that would match
            return res
        if order.order_type == "LIMIT" and not crosses:
            # It rests. A resting order is only MAKER once it receives quantity, and it can only
            # receive quantity when the market actually trades through its price.
            if not self._limit_reachable(order, state):
                res.role = None
                res.rejected = "resting"
                return res
            return self._fill_maker(order, state, res)

        if level == 1:
            return self._fill_book(order, state, res)
        if level == 2:
            return self._fill_quote(order, state, res)
        return self._fill_ohlcv(order, state, res)

    # -- LEVEL 1: walk the book -------------------------------------------------------------
    def _fill_book(self, order: Order, state: MarketState, res: ExecutionResult) -> ExecutionResult:
        book = state.book
        levels: Sequence[tuple[float, float]] = book.asks if order.side == "BUY" else book.bids
        remaining = order.qty
        touch = levels[0][0] if levels else state.last
        for i, (price, size) in enumerate(levels):
            if remaining <= 1e-12:
                break
            take = min(remaining, size)
            if take <= 0:
                continue
            res.fills.append(PartialFill(price, take, state.ts, "TAKER", i, 1))
            res.book_depth_consumed += price * take
            remaining -= take
        if not res.fills:
            res.rejected = "no_depth"
            return res
        res.role = "TAKER"
        q = res.filled_qty
        res.latency_cost = abs(state.last - res.decision_price) * q
        res.spread_cost = abs(touch - state.last) * q
        res.impact_cost = abs(res.avg_price - touch) * q
        if remaining > 1e-12:
            log.debug("order %s only partially filled: %g of %g", order.id, q, order.qty)
        return res

    # -- LEVEL 2: bid/ask ---------------------------------------------------------------------
    def _fill_quote(self, order: Order, state: MarketState, res: ExecutionResult) -> ExecutionResult:
        touch = state.ask if order.side == "BUY" else state.bid
        impact_bps = self._impact_bps(order, state) * self.config.slippage_mult
        sign = 1.0 if order.side == "BUY" else -1.0
        price = touch * (1.0 + sign * impact_bps / 1e4)
        res.fills.append(PartialFill(price, order.qty, state.ts, "TAKER", 0, 2))
        res.role = "TAKER"
        res.latency_cost = abs(state.last - res.decision_price) * order.qty
        res.spread_cost = abs(touch - state.last) * order.qty
        res.impact_cost = abs(price - touch) * order.qty
        return res

    # -- LEVEL 3: OHLCV fallback ---------------------------------------------------------------
    def _fill_ohlcv(self, order: Order, state: MarketState, res: ExecutionResult) -> ExecutionResult:
        """half spread + market impact, both estimated, both always a cost."""
        half_spread_bps = self._half_spread_bps(state)
        impact_bps = self._impact_bps(order, state)
        total_bps = (half_spread_bps + impact_bps) * self.config.slippage_mult
        sign = 1.0 if order.side == "BUY" else -1.0
        ref = state.last
        touch = ref * (1.0 + sign * half_spread_bps * self.config.slippage_mult / 1e4)
        price = ref * (1.0 + sign * total_bps / 1e4)
        res.fills.append(PartialFill(price, order.qty, state.ts, "TAKER", 0, 3))
        res.role = "TAKER"
        res.latency_cost = abs(ref - res.decision_price) * order.qty
        res.spread_cost = abs(touch - ref) * order.qty
        res.impact_cost = abs(price - touch) * order.qty
        return res

    # -- resting limit orders -------------------------------------------------------------------
    def _limit_reachable(self, order: Order, state: MarketState) -> bool:
        """Did the market actually trade through the limit price during this state?

        With only OHLCV this is the bar's range. Touching the limit exactly is NOT treated as a
        fill: at the touch the order is at the back of the queue, and assuming otherwise is the
        single most common way a backtest invents maker fills.
        """
        if order.limit_price is None:
            return False
        if state.bar_range is None:
            ref = state.bid if order.side == "BUY" else state.ask
            if ref is None:
                return False
            return ref < order.limit_price if order.side == "BUY" else ref > order.limit_price
        half = state.bar_range / 2.0
        low, high = state.last - half, state.last + half
        return low < order.limit_price if order.side == "BUY" else high > order.limit_price

    def _fill_maker(self, order: Order, state: MarketState, res: ExecutionResult) -> ExecutionResult:
        """A resting order that was genuinely traded through fills AT its limit, as maker."""
        price = float(order.limit_price)
        res.fills.append(PartialFill(price, order.qty, state.ts, "MAKER", 0, res.execution_level_used))
        res.role = "MAKER"
        res.latency_cost = 0.0
        res.spread_cost = 0.0
        res.impact_cost = 0.0
        return res

    # -- cost components --------------------------------------------------------------------------
    def _half_spread_bps(self, state: MarketState) -> float:
        """Half of a one-tick spread, floored, plus a small volatility widening term.

        Measured against production quotes, the top of book on these symbols is exactly one tick,
        so tick/2 IS the half-spread. The floor covers symbols whose tick is unrealistically fine,
        and the volatility term covers fast markets where the book thins out.
        """
        tick_half_bps = 0.0
        if state.tick and state.last > 0:
            tick_half_bps = (state.tick / 2.0) / state.last * 1e4
        base = max(self.config.base_slippage_bps, tick_half_bps)
        vol_bps = 0.0
        if state.atr and state.last > 0:
            vol_bps = self.config.vol_component * (state.atr / state.last) * 1e4
        return base + vol_bps

    def _impact_bps(self, order: Order, state: MarketState) -> float:
        """Market impact from how much of the available liquidity the order eats.

        Liquidity is taken from the best proxy present: real ±1% book depth when the bookDepth
        archive is loaded, otherwise the executing bar's quote volume. With neither, the size term
        is zero and only the volatility term applies -- which is why `size_component` defaults to 0
        and must be configured deliberately rather than guessed.
        """
        if self.config.size_component <= 0:
            return 0.0
        notional = order.qty * (state.last or 1.0)
        liquidity = state.depth_notional_1pct or state.quote_volume
        if not liquidity or liquidity <= 0:
            return 0.0
        return self.config.size_component * (notional / liquidity) * 1e4

    # -- fees -------------------------------------------------------------------------------------
    def fee_for(self, res: ExecutionResult) -> float:
        """Commission on the executed notional at the rate for the role actually achieved."""
        if not res.fills:
            return 0.0
        role = res.role or "TAKER"
        if self.config.force_taker:
            role = "TAKER"
        return sum(self.fees.fee(f.price * f.qty, maker=(role == "MAKER")) for f in res.fills)
