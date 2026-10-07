"""Order lifecycle.

An order is not an event, it is a state machine with a history. Modelling it as "signal -> fill"
is what lets a backtest quietly award fills that never happened -- most obviously to resting limit
orders, which in a naive simulator fill the instant price touches them and collect a maker rebate
for the privilege.

    CREATED -> SUBMITTED -> OPEN -> PARTIALLY_FILLED -> FILLED
                    |         |            |
                    |         +-> CANCELLED / EXPIRED
                    +-> REJECTED

Rules enforced here:

* A LIMIT order that rests is not a fill. It becomes MAKER only if and when it actually receives
  quantity; until then it has no liquidity role at all.
* A LIMIT order that crosses the spread at submission is a TAKER order and is priced as one.
* Nothing may execute before `execute_at` (signal timestamp + modelled latency).
* Partial fills accumulate; `filled_qty` and `remaining_qty` always sum to the original quantity.
* No minimum applies to a FILL. Binance checks MIN_NOTIONAL on the submitted order only, so a valid
  order may be filled in pieces far below it (the public USD-M tape shows it constantly: 0.01 SOL
  fills of a 5+ USDT order, 0.001 ETH fills of a 20+ USDT order).

What happens to an unfilled remainder is decided by the order type and time in force, never by its
size (Binance USD-M semantics, see `remainder_policy`):

    MARKET          remainder EXPIRES -- a market order never rests on the book
    LIMIT IOC       fills what it can immediately, remainder EXPIRES
    LIMIT FOK       all or nothing; if it cannot fill completely it EXPIRES with no fills
    LIMIT GTX       post-only; if it would take liquidity at submission it is REJECTED
    LIMIT GTC/GTD   remainder RESTS as PARTIALLY_FILLED until filled, cancelled or expired, even when
                    the remaining notional is below minNotional
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

OrderState = Literal["CREATED", "SUBMITTED", "OPEN", "PARTIALLY_FILLED", "FILLED",
                     "CANCELLED", "EXPIRED", "REJECTED"]
OrderType = Literal["MARKET", "LIMIT"]
TimeInForce = Literal["GTC", "IOC", "FOK", "GTX", "GTD"]
LiquidityRole = Literal["MAKER", "TAKER"]
RemainderPolicy = Literal["EXPIRE", "REST", "ALL_OR_NONE", "REJECT_IF_TAKING"]


def remainder_policy(order_type: str, time_in_force: str = "GTC") -> RemainderPolicy:
    """What Binance does with the part of an order that did not fill immediately."""
    if order_type == "MARKET":
        return "EXPIRE"
    tif = (time_in_force or "GTC").upper()
    if tif == "IOC":
        return "EXPIRE"
    if tif == "FOK":
        return "ALL_OR_NONE"
    if tif == "GTX":
        return "REJECT_IF_TAKING"
    return "REST"                       # GTC / GTD


TERMINAL: frozenset[str] = frozenset({"FILLED", "CANCELLED", "EXPIRED", "REJECTED"})


@dataclass
class PartialFill:
    """One execution against one price. A market order eating three book levels makes three."""
    price: float
    qty: float
    ts: int
    role: LiquidityRole
    level_index: int = 0            # which book level this came from (L1 only)
    execution_level: int = 3        # which ExecutionModel level produced it

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass
class Order:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    order_type: OrderType = "MARKET"
    limit_price: float | None = None
    decision_price: float = 0.0     # price the strategy saw when it decided
    signal_ts: int = 0
    submit_ts: int = 0
    execute_at: int = 0             # signal_ts + signal_latency + order_latency
    expire_at: int | None = None
    time_in_force: TimeInForce = "GTC"
    reduce_only: bool = False
    state: OrderState = "CREATED"
    fills: list[PartialFill] = field(default_factory=list)
    reject_reason: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    meta: dict[str, Any] = field(default_factory=dict)

    # -- derived ---------------------------------------------------------------------------
    @property
    def filled_qty(self) -> float:
        return sum(f.qty for f in self.fills)

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    @property
    def avg_price(self) -> float:
        n = self.filled_qty
        return sum(f.notional for f in self.fills) / n if n > 0 else 0.0

    @property
    def role(self) -> LiquidityRole | None:
        """A resting order that never filled has no liquidity role. Do not default it to MAKER."""
        if not self.fills:
            return None
        return "MAKER" if all(f.role == "MAKER" for f in self.fills) else "TAKER"

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def remainder_policy(self) -> RemainderPolicy:
        return remainder_policy(self.order_type, self.time_in_force)

    # -- transitions ------------------------------------------------------------------------
    def submit(self, ts: int) -> "Order":
        if self.state != "CREATED":
            raise ValueError(f"cannot submit from {self.state}")
        self.submit_ts = ts
        self.state = "SUBMITTED"
        return self

    def open(self) -> "Order":
        if self.state not in ("SUBMITTED", "OPEN"):
            raise ValueError(f"cannot rest from {self.state}")
        self.state = "OPEN"
        return self

    def reject(self, reason: str) -> "Order":
        self.reject_reason = reason
        self.state = "REJECTED"
        return self

    def cancel(self) -> "Order":
        if self.is_terminal:
            raise ValueError(f"cannot cancel from {self.state}")
        self.state = "CANCELLED"
        return self

    def expire(self) -> "Order":
        if self.is_terminal:
            raise ValueError(f"cannot expire from {self.state}")
        self.state = "EXPIRED"
        return self

    def add_fill(self, fill: PartialFill) -> "Order":
        if self.is_terminal:
            raise ValueError(f"cannot fill from {self.state}")
        if fill.ts < self.execute_at:
            raise ValueError(f"fill at {fill.ts} precedes execute_at {self.execute_at}")
        if fill.qty <= 0:
            return self
        self.fills.append(fill)
        self.state = "FILLED" if self.remaining_qty <= 1e-12 else "PARTIALLY_FILLED"
        return self

    def crosses(self, bid: float | None, ask: float | None) -> bool:
        """True when a LIMIT order would immediately take liquidity at submission."""
        if self.order_type != "LIMIT" or self.limit_price is None:
            return self.order_type == "MARKET"
        if self.side == "BUY":
            return ask is not None and self.limit_price >= ask
        return bid is not None and self.limit_price <= bid

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "symbol": self.symbol, "side": self.side, "type": self.order_type,
                "time_in_force": self.time_in_force, "reduce_only": self.reduce_only,
                "qty": self.qty, "limit_price": self.limit_price, "state": self.state,
                "decision_price": self.decision_price, "signal_ts": self.signal_ts,
                "submit_ts": self.submit_ts, "execute_at": self.execute_at,
                "filled_qty": self.filled_qty, "remaining_qty": self.remaining_qty,
                "avg_price": self.avg_price, "role": self.role,
                "reject_reason": self.reject_reason,
                "fills": [{"price": f.price, "qty": f.qty, "ts": f.ts, "role": f.role,
                           "level_index": f.level_index, "execution_level": f.execution_level}
                          for f in self.fills]}
