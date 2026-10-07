"""S17 Order Book Imbalance Scalp - "lean on the wall"

Idea:       When the top-10 bid stack is 2x the ask stack (or vice versa) on several consecutive book updates and
            the spread is tight, price tends to lean into the thin side over the next minute: scalp it.
Timeframe:  order book (~10 updates/s per symbol); the 1m subscription only keeps candles flowing.
Symbols:    all configured symbols, one position at a time.
Entry:      long when bidQty/askQty (10 levels) >= 2.0, short when askQty/bidQty >= 2.0, spread <= 2 ticks and the
            condition holding on 5 consecutive updates; skipped (and counted in thin_book_skips) when the top-10
            depth is below 15k USDT, or when the last 3 trades all hit against the trade (absorption).
Stop:       0.8 x the risk unit (12 bps of price) = 9.6 bps from the entry, floored at the risk gate's 5 bps
            minimum (which the 12 bps unit clears, so the floor never bites).
Targets:    one target at 2.2 units = 2.75R closes 100%; time exit after 45 s.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: sub-minute, book-only. This book previously hit the -25% floor by overtrading a thin tape, so the
            looser 2.0 imbalance is paired with a HARD 15 s per-symbol cooldown (cooldown_ms) and a wider 12 bps
            risk unit: fewer, bigger scalps instead of a stream of sub-tick scratches. The depth floor is lowered,
            never removed - a book thinner than 15k USDT is still skipped and counted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import BookSnapshot, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc

_MIN_STOP_BPS = 5.01  # the risk gate rejects stops closer than 5 bps of price; keep a hair of margin
_LEVELS = 10
_TRADE_WINDOW_MS = 10_000


@dataclass
class Params:
    ratio_min: float = P(2.0, min=1.5, max=10.0, step=0.1, label="imbalance ratio",
                         help="bidQty/askQty (long) or askQty/bidQty (short) over the top 10 levels")
    max_spread_ticks: int = P(2, min=1, max=10, step=1, label="max spread (ticks)")
    confirm_updates: int = P(5, min=1, max=30, step=1, label="confirm updates",
                             help="consecutive book updates the condition must hold")
    min_notional_depth: float = P(15_000.0, min=1_000.0, max=2_000_000.0, step=1_000.0, label="min depth (USDT)",
                                  help="top-10 bid + ask notional; thinner books are still skipped and counted")
    hold_s: int = P(180, min=15, max=600, step=5, label="max hold (s)",
                    help="hold_v1: 45 -> 180s. The 45s clock closed 133 of the lab's 137 time exits "
                         "before the 2.2-unit target could be reached.")
    risk_unit_bps: float = P(12.0, min=1.0, max=30.0, step=0.5, label="risk unit (bps)")
    cooldown_s: int = P(15, min=0, max=300, step=1, label="re-entry cooldown (s)",
                        help="the engine refuses a re-entry on that symbol for this long after an exit")
    stop_units: float = P(0.8, min=0.2, max=3.0, step=0.1, label="stop (units)")
    tp_units: float = P(2.2, min=0.5, max=8.0, step=0.1, label="target (units)")


class OrderBookImbalanceScalp(Strategy):
    id = "S17"
    name = "Order Book Imbalance Scalp"
    Params = Params
    doc = StrategyDoc(
        idea="Scalp a persistent 2:1 top-of-book imbalance with a tight spread, on a 15 s leash.",
        timeframe="order book updates (~10/s); 1m candles only for context",
        symbols="all configured, one position at a time",
        entry="bidQty/askQty >= 2 -> long, askQty/bidQty >= 2 -> short over 10 levels, spread <= 2 ticks, held on "
              "5 consecutive updates; skipped on absorption (last 3 trades against) or depth < 15k USDT",
        stop="0.8 x 12 bps risk unit = 9.6 bps (floored at the 5 bps gate minimum)",
        targets="2.2 units = 2.75R closes 100%; time exit after 45 s",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="sub-minute and book-only, but on a 15 s per-symbol cooldown and a 12 bps risk unit so the "
                       "looser 2:1 trigger buys fewer, bigger scalps instead of a stream of scratches",
    )
    timeframes = ("1m",)
    needs_book = True
    max_positions = 1
    min_rr = 2.5
    contributes_votes = False
    warmup_bars = 20
    badges = ("BOOK",)

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._sym: dict[str, dict[str, Any]] = {}
        self._last_emit: dict[str, int] = {}
        self._thin_book_skips = 0
        self._absorption_skips = 0

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def on_book(self, b: BookSnapshot, ctx: MarketContext) -> list[Signal]:
        p = self.params
        sym = b.symbol
        st = self._sym.setdefault(sym, {"ratio": None, "spread_ticks": None, "depth": 0.0, "confirm": 0, "side": None})
        depth = b.bid_notional(_LEVELS) + b.ask_notional(_LEVELS)
        ratio = b.ratio(_LEVELS)
        tick = ctx.rules(sym).tick
        spread = b.spread
        spread_ticks = None if spread is None or tick <= 0 else spread / tick
        st["ratio"] = None if ratio is None else round(ratio, 2)
        st["spread_ticks"] = None if spread_ticks is None else round(spread_ticks, 2)
        st["depth"] = round(depth, 0)
        if depth < p.min_notional_depth:
            self._thin_book_skips += 1
            self._reset_streak(st)
            return []
        side: str | None = None
        if ratio is not None and ratio >= p.ratio_min:
            side = "long"
        elif ratio is not None and ratio > 0 and 1.0 / ratio >= p.ratio_min:
            side = "short"
        if side is None or spread_ticks is None or spread_ticks > p.max_spread_ticks + 1e-6:
            self._reset_streak(st)
            return []
        st["confirm"] = st["confirm"] + 1 if st["side"] == side else 1
        st["side"] = side
        if st["confirm"] < int(p.confirm_updates):
            return []
        now = ctx.now_ms()
        if ctx.positions_of(self.id) or now - self._last_emit.get(sym, -1) < self.cooldown_ms():
            return []  # one position at a time, no re-entry storm
        trades = ctx.recent_trades(sym, _TRADE_WINDOW_MS)[-3:]
        if len(trades) == 3:
            against = all(t.buyer_is_maker for t in trades) if side == "long" else all(not t.buyer_is_maker for t in trades)
            if against:
                self._absorption_skips += 1
                self._reset_streak(st)
                return []
        price = ctx.last_price(sym) or b.mid
        if not price or price <= 0:
            return []
        unit = price * p.risk_unit_bps / 1e4
        stop_dist = max(p.stop_units * unit, price * _MIN_STOP_BPS / 1e4)
        stop = price - stop_dist if side == "long" else price + stop_dist
        self._reset_streak(st)
        self._last_emit[sym] = now
        return [self.make_entry(
            symbol=sym, side=side, ts=now, tf="1m", price=price, stop=stop,
            tps_r=[(p.tp_units / p.stop_units, 1.0)], max_hold_s=int(p.hold_s), valid_bars=1,
            reason=f"book imbalance {ratio:.2f} bid/ask, spread {spread_ticks:.1f} ticks -> {side}",
            meta={"ratio": ratio, "spread_ticks": spread_ticks, "depth": depth, "unit": unit,
                  "stop_floored": stop_dist > p.stop_units * unit + 1e-12})]

    @staticmethod
    def _reset_streak(st: dict[str, Any]) -> None:
        st["confirm"] = 0
        st["side"] = None

    def state(self) -> dict[str, Any]:
        return {"symbols": self._sym, "thin_book_skips": self._thin_book_skips,
                "absorption_skips": self._absorption_skips}

    def reset(self) -> None:
        self._sym.clear()
        self._last_emit.clear()
        self._thin_book_skips = 0
        self._absorption_skips = 0
