"""Execution-layer configuration: fees and the execution model's knobs.

These live in the execution layer rather than in the competition package because backtests and
paper trading need them just as much as a tournament does. `app.competition.config` re-exports both
names, so existing imports keep working.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FeeSchedule:
    """Venue fee rates. Centralised so no module invents its own constant.

    Defaults are Binance USD-M regular-user rates. `rate()` is keyed on the liquidity role that was
    ACTUALLY achieved, never on the order type that was requested.
    """
    maker_rate: float = 0.0002
    taker_rate: float = 0.0005
    source: str = "binance_usdm"
    updated_at: str = "2026-09-23"

    def rate(self, maker: bool) -> float:
        return self.maker_rate if maker else self.taker_rate

    def fee(self, notional: float, maker: bool = False) -> float:
        return abs(notional) * self.rate(maker)

    def to_dict(self) -> dict[str, object]:
        return {"maker_rate": self.maker_rate, "taker_rate": self.taker_rate, "source": self.source,
                "updated_at": self.updated_at}


# THE fee schedules. Every engine -- historical replay, competition, arena, shadow, Jev and the live
# paper engine -- takes its rates from here, by venue. Regular (non-VIP) rates, no BNB/VIP discount.
BINANCE_USDM = FeeSchedule(0.0002, 0.0005, "binance_usdm", "2026-09-23")    # binance.com FAQ 360033544231
BINANCE_SPOT = FeeSchedule(0.0010, 0.0010, "binance_spot", "2026-09-23")    # regular spot 0.10% / 0.10%
BYBIT_LINEAR = FeeSchedule(0.0002, 0.00055, "bybit_linear", "2026-09-23")   # bybit help centre: perpetuals
# Frozen copy of what every replay charged before 2026-09-23 (the Settings default of 0.04% taker,
# while declaring 0.05%). ONLY for reproducing stored experiments such as validation bdddc67f6315.
LEGACY_PRE_2026_09_23 = FeeSchedule(0.0002, 0.0004, "legacy_settings_default", "before 2026-09-23")


def fee_schedule_for(exchange: str, kind: str) -> FeeSchedule:
    """The schedule for a venue: `exchange` binance|bybit, `kind` futures|spot."""
    if exchange == "bybit":
        return BYBIT_LINEAR
    return BINANCE_SPOT if kind == "spot" else BINANCE_USDM


@dataclass(frozen=True)
class ExecutionConfig:
    """Which execution model to use, and how hard to press it.

    `level` is the BEST model to use when the data supports it; ExecutionModel degrades to a higher
    (worse) number when it does not, and records the reason on every result:
      1  order book walk (needs BookSnapshot depth)
      2  bid/ask (needs a quote)
      3  spread + volatility + size estimate over OHLCV

    `slippage_mult` and `force_taker` are the stress-test knobs. `size_component` defaults to 0
    because a market-impact coefficient is a guess unless it is calibrated against real depth --
    set it deliberately, with `depth_notional_1pct` supplied, rather than inheriting a number.

    CALIBRATION (production bookTicker, 2026-09-22). On liquid USD-M majors the top of book is one
    tick wide, so the half-spread is tick/2 and nothing else:

        BTCUSDT  spread 0.012 bps   half 0.006   tick 0.1   / 85966  = 0.0116 bps
        ETHUSDT  spread 0.036 bps   half 0.018   tick 0.01  /  2750  = 0.036  bps
        SOLUSDT  spread 0.852 bps   half 0.426   tick 0.01  /   117  = 0.852  bps

    So L3 derives the half-spread from the symbol's tick size, floors it at `base_slippage_bps`,
    and adds a small volatility term for the widening that happens in fast markets. An earlier
    revision used `vol_component = 0.5 * ATR/price`, which produced ~23 bps on these symbols --
    roughly 50x too wide for BTC and enough on its own to turn a profitable strategy into a losing
    one. Volatility is not a spread; it only modulates one.
    """
    level: Literal[1, 2, 3] = 1
    base_slippage_bps: float = 0.1      # floor on the half-spread
    vol_component: float = 0.02         # multiplied by (ATR / price), in bps
    size_component: float = 0.0         # multiplied by (order notional / available liquidity), in bps
    slippage_mult: float = 1.0          # stress knob
    force_taker: bool = False           # stress knob: treat every fill as taker
    signal_latency_ms: int = 250
    order_latency_ms: int = 150

    @property
    def total_latency_ms(self) -> int:
        return self.signal_latency_ms + self.order_latency_ms
