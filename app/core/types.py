"""Shared dataclass contracts. Pure data: no imports from other app modules.

Conventions: timestamps are int milliseconds UTC; symbols are raw Binance ids (BTCUSDT);
timeframes are "1m" | "5m" | "15m"; position/signal sides are "long" | "short";
exchange order sides are "BUY" | "SELL".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Union

TF_MS: dict[str, int] = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
                         "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
Side = Literal["long", "short"]
OrderSide = Literal["BUY", "SELL"]


def tf_ms(tf: str) -> int:
    try:
        return TF_MS[tf]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {tf!r}") from exc


def opposite(side: str) -> str:
    return "short" if side == "long" else "long"


def sign_of(side: str) -> int:
    return 1 if side == "long" else -1


@dataclass(slots=True)
class Candle:
    symbol: str
    tf: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    closed: bool = True
    quote_volume: float = 0.0
    trades: int = 0
    source: str = "live"  # live | backfill | synthetic
    # Base-asset volume bought by takers (Binance kline field 9 / websocket "V"). 0.0 when the
    # source does not report it; only V3 flow features read it.
    taker_buy_volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_historical(self) -> bool:
        return self.source != "live"


@dataclass(slots=True)
class Tick:
    symbol: str
    price: float
    qty: float
    ts: int
    buyer_is_maker: bool  # True => an aggressive SELL hit the bid


@dataclass(slots=True)
class MarkPrice:
    symbol: str
    mark: float
    index: float
    funding_rate: float
    next_funding_ts: int
    ts: int


@dataclass(slots=True)
class BookSnapshot:
    symbol: str
    bids: list[tuple[float, float]]  # best first, (price, qty)
    asks: list[tuple[float, float]]
    ts: int

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def spread_bps(self) -> float | None:
        mid = self.mid
        if not mid or self.spread is None:
            return None
        return self.spread / mid * 1e4

    def bid_qty(self, levels: int = 10) -> float:
        return sum(q for _, q in self.bids[:levels])

    def ask_qty(self, levels: int = 10) -> float:
        return sum(q for _, q in self.asks[:levels])

    def bid_notional(self, levels: int = 10) -> float:
        return sum(p * q for p, q in self.bids[:levels])

    def ask_notional(self, levels: int = 10) -> float:
        return sum(p * q for p, q in self.asks[:levels])

    def imbalance(self, levels: int = 10) -> float | None:
        """Bid share of top-N notional in [0, 1]; None when the book is empty."""
        b, a = self.bid_notional(levels), self.ask_notional(levels)
        return None if b + a <= 0 else b / (b + a)

    def ratio(self, levels: int = 10) -> float | None:
        """bidQty / askQty over the top-N levels (S17 uses this)."""
        a = self.ask_qty(levels)
        return None if a <= 0 else self.bid_qty(levels) / a


@dataclass(slots=True)
class FundingInfo:
    symbol: str
    rate: float
    next_funding_ts: int
    mark: float
    index: float
    ts: int

    def basis_bps(self) -> float:
        return 0.0 if not self.index else (self.mark - self.index) / self.index * 1e4


@dataclass(frozen=True)
class MarketRules:
    symbol: str
    tick: float
    step: float
    min_qty: float
    min_notional: float
    maint_margin_rate: float = 0.025
    price_precision: int = 8
    qty_precision: int = 8

    def round_price(self, price: float, mode: str = "nearest") -> float:
        if self.tick <= 0:
            return price
        units = price / self.tick
        if mode == "down":
            n = math.floor(units + 1e-9)
        elif mode == "up":
            n = math.ceil(units - 1e-9)
        else:
            n = round(units)
        return round(n * self.tick, self.price_precision)

    def round_qty_down(self, qty: float) -> float:
        if self.step <= 0:
            return qty
        n = math.floor(qty / self.step + 1e-9)
        return round(max(n, 0) * self.step, self.qty_precision)

    def round_qty_nearest(self, qty: float) -> float:
        if self.step <= 0:
            return qty
        return round(round(qty / self.step) * self.step, self.qty_precision)

    def qty_ok(self, qty: float, price: float, reduce_only: bool = False) -> bool:
        """Would Binance accept an order of `qty` at `price`?

        LOT_SIZE (minQty, step) applies to every order. MIN_NOTIONAL applies to the submitted
        order and exempts reduce-only orders (error -4164: "Order's notional must be no smaller
        than 5.0 (unless you choose reduce only)"). Nothing here applies to individual fills.
        """
        if qty < self.min_qty - 1e-12:
            return False
        return reduce_only or qty * price >= self.min_notional - 1e-9

    def min_order_qty(self, price: float, safety: float = 1.0) -> float:
        """Smallest step-aligned quantity an opening order may carry at `price`.

        `safety` scales the notional floor only; it is a PaperLab preference, not an exchange rule.
        """
        if price <= 0:
            return float("inf")
        need = max(self.min_qty, self.min_notional * max(1.0, safety) / price)
        if self.step <= 0:
            return need
        n = math.ceil(need / self.step - 1e-9)
        return round(n * self.step, self.qty_precision)

    def min_order_notional(self, price: float, safety: float = 1.0) -> float:
        """USDT value of `min_order_qty` -- what the exchange actually needs for an entry."""
        return self.min_order_qty(price, safety) * price


@dataclass(slots=True)
class TakeProfit:
    price: float
    fraction: float  # fraction of the ORIGINAL position qty


@dataclass(slots=True)
class TrailSpec:
    kind: Literal["atr", "supertrend", "pct", "none"]
    tf: str
    mult: float = 1.5
    atr_period: int = 14
    activate_after_tp1: bool = True


@dataclass(slots=True)
class Signal:
    strategy_id: str
    symbol: str
    kind: Literal["entry", "exit"]
    side: Side
    ts: int
    tf: str
    entry_price: float
    stop: float = 0.0
    take_profits: list[TakeProfit] = field(default_factory=list)
    trail: TrailSpec | None = None
    be_at_r: float | None = None
    max_hold_s: int | None = None
    valid_bars: int = 1
    size_mult: float = 1.0
    leg: str | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    @property
    def is_entry(self) -> bool:
        return self.kind == "entry"

    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop)

    def rr(self) -> float | None:
        """Reward:risk of the LAST take-profit; None when no TP or stop is undefined."""
        dist = self.stop_distance()
        if not self.take_profits or dist <= 0:
            return None
        last = self.take_profits[-1].price
        reward = (last - self.entry_price) if self.side == "long" else (self.entry_price - last)
        return reward / dist

    def stop_on_correct_side(self) -> bool:
        if self.side == "long":
            return 0 < self.stop < self.entry_price
        return self.stop > self.entry_price > 0


@dataclass(slots=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    qty: float
    reduce_only: bool
    purpose: Literal["net_delta", "backstop", "flatten"]
    client_id: str
    ref_price: float
    stop_price: float | None = None
    close_position: bool = False


@dataclass(slots=True)
class OrderResult:
    client_id: str
    exchange_id: str
    status: str
    avg_price: float | None
    executed_qty: float
    raw: dict[str, Any]
    error: str | None = None


@dataclass(slots=True)
class Fill:
    id: str
    ts: int
    epoch: int
    strategy_id: str
    symbol: str
    position_id: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    slippage_bps: float
    kind: str  # entry|tp|stop|trail|time|manual|kill|halt|rotate|liq|restart_gap|funding|exchange_flat|backstop_fired|margin_halt|reset|vote_flip|bias_flip
    realized_pnl: float
    simulated: bool
    signal_id: str | None = None
    exchange_order_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    ref_price: float = 0.0
    leverage: int = 1
    position_side: str = "long"
    is_open: bool = False  # True for entry fills
    wallet_equity_after: float = 0.0  # this strategy's isolated book right after the fill
    wallet_upnl_after: float = 0.0
    life: int = 1  # which life of that strategy's book this fill belongs to


@dataclass(slots=True)
class VirtualPosition:
    id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: float
    qty_initial: float
    entry_price: float
    entry_ts: int
    leverage: int
    margin: float
    stop: float
    take_profits: list[TakeProfit]
    trail: TrailSpec | None
    be_at_r: float | None
    max_hold_deadline: int | None
    initial_risk_usd: float
    extreme_price: float
    tp1_done: bool = False
    be_done: bool = False
    realized: float = 0.0
    fees: float = 0.0
    signal_id: str = ""
    leg: str | None = None
    tf: str = "1m"
    meta: dict[str, Any] = field(default_factory=dict)
    exit_fill_ids: list[str] = field(default_factory=list)
    exit_notional: float = 0.0
    exit_qty: float = 0.0

    @property
    def sign(self) -> int:
        return sign_of(self.side)

    @property
    def signed_qty(self) -> float:
        return self.qty * self.sign

    def notional(self, price: float) -> float:
        return abs(self.qty * price)

    def upnl(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * self.sign

    def r_multiple(self, price: float) -> float:
        if self.initial_risk_usd <= 0 or self.qty_initial <= 0:
            return 0.0
        per_unit_risk = self.initial_risk_usd / self.qty_initial
        return (price - self.entry_price) * self.sign / per_unit_risk

    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop)

    def liq_price(self, mmr: float) -> float | None:
        """Paper liquidation at THIS position's (virtual) leverage; the exchange only liquidates the net."""
        if self.leverage <= 0:
            return None
        dist = 1.0 / self.leverage - mmr
        if dist <= 0:
            return self.entry_price
        return self.entry_price * (1 - dist) if self.side == "long" else self.entry_price * (1 + dist)


@dataclass(slots=True)
class Wallet:
    strategy_id: str
    allocation: float
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    life: int = 1                    # incremented on every respawn after a -25% death; fills are stamped with it
    realized_all_lives: float = 0.0  # net result of every DEAD life, so the graveyard stays visible
    lives_today: int = 0             # deaths in lives_day, capped to stop a death loop
    lives_day: str = ""

    def equity(self, upnl: float = 0.0) -> float:
        return self.allocation + self.realized - self.fees + self.funding + upnl

    def net(self) -> float:
        """Result of the CURRENT life only (what the leaderboard's realized column shows)."""
        return self.realized - self.fees + self.funding

    def net_all_lives(self) -> float:
        return self.realized_all_lives + self.net()


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    qty: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    leverage: int = 1
    risk_usd: float = 0.0
    rr: float | None = None
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved, "reason": self.reason, "qty": self.qty, "notional": self.notional,
            "margin": self.margin, "leverage": self.leverage, "risk_usd": self.risk_usd, "rr": self.rr,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


@dataclass(slots=True)
class ExchangePosition:
    symbol: str
    qty: float  # signed: +long / -short
    entry_price: float
    liq_price: float | None
    margin: float
    upnl: float
    leverage: int
    mark: float = 0.0
    ts: int = 0

    @property
    def notional(self) -> float:
        return abs(self.qty) * (self.mark or self.entry_price)

    def liq_distance_pct(self) -> float | None:
        if not self.liq_price or not self.mark or self.qty == 0:
            return None
        return abs(self.mark - self.liq_price) / self.mark


@dataclass(slots=True)
class ExitIntent:
    position_id: str
    kind: str
    fraction: float  # fraction of CURRENT qty to close
    ref_price: float
    reason: str = ""


@dataclass(slots=True)
class ExitUpdate:
    stop: float | None = None
    take_profits: list[TakeProfit] | None = None
    close: bool = False
    reason: str = ""
    allow_worse: bool = False


# ---- feed events -----------------------------------------------------------

@dataclass(slots=True)
class CandleClosed:
    candle: Candle


@dataclass(slots=True)
class CandleForming:
    candle: Candle


@dataclass(slots=True)
class TradeEvent:
    tick: Tick


@dataclass(slots=True)
class MarkEvent:
    mark: MarkPrice


@dataclass(slots=True)
class BookEvent:
    book: BookSnapshot


@dataclass(slots=True)
class UserEvent:
    kind: str
    payload: dict[str, Any]


@dataclass(slots=True)
class FeedStatus:
    stream: str
    connected: bool
    detail: str
    ts: int


Event = Union[CandleClosed, CandleForming, TradeEvent, MarkEvent, BookEvent, UserEvent, FeedStatus]
