"""S15 Liquidation Cascade Fade - "buy the flush, sell the squeeze"

Idea:       Fade a one/two-candle liquidation-style flush on the 1m chart while the 15m chart still calls it
            range noise (latest 15m close inside the prior 4h high/low) rather than a breakout.
Timeframe:  1m trigger, 15m regime gate.
Symbols:    all configured symbols.
Entry:      move over the last <= 2 closed 1m bars >= 0.45% (or one bar's range/close >= 0.45%) on >= 1.8x the
            prior 20-bar volume average -> long a dump / short a rip, only if the latest closed 15m bar's close is
            inside [lowest low, highest high] of the previous 16 closed 15m bars (the latest bar excluded).
Stop:       0.35% beyond the cascade extreme (below the flush low for longs, above the spike high for shorts).
Targets:    one target at the 50% retrace of the cascade closes 100%; time exit after 30 minutes.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the 0.45% / 1.8x trigger is calibrated to the thin testnet tape (0.7% / 3x almost never
            printed), it trades the panic candle itself with a tight stop and a fat snapback target. The 4h-range
            regime gate is untouched - a genuine breakout is never faded - and a 120 s cooldown after every exit
            stops the strategy re-fading the same cascade into a string of scratches. The testnet liquidation tape
            is weak, so the cascade is a price/volume PROXY (badge), not liquidation data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.indicators import last
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    move_pct: float = P(0.45, min=0.2, max=5.0, step=0.05, label="cascade move %",
                        help="minimum move over <= move_bars closed bars, or a single bar's range / close")
    move_bars: int = P(2, min=1, max=5, step=1, label="cascade bars", help="most bars the move may span")
    volume_mult: float = P(1.8, min=1.0, max=10.0, step=0.25, label="volume mult",
                           help="cascade bar volume vs the volume SMA of the prior bars")
    vol_sma: int = P(20, min=5, max=100, step=1, label="volume SMA")
    range_bars_15m: int = P(16, min=4, max=64, step=1, label="15m range bars",
                            help="prior closed 15m bars whose high/low define the regime range (16 = 4h)")
    stop_pct: float = P(0.35, min=0.05, max=2.0, step=0.05, label="stop % beyond extreme")
    retrace: float = P(0.5, min=0.2, max=1.0, step=0.05, label="retrace target",
                       help="fraction of the cascade the target retraces")
    max_hold_s: int = P(1800, min=300, max=14400, step=60, label="max hold (s)")
    cooldown_after_loss_s: int = P(120, min=0, max=3600, step=10, label="cooldown after exit (s)",
                                   help="the engine refuses a re-entry on that symbol for this long after an exit")


class LiquidationCascadeFade(Strategy):
    id = "S15"
    name = "Liquidation Cascade Fade"
    Params = Params
    doc = StrategyDoc(
        idea="Fade a liquidation-style flush (price/volume proxy) while the 15m chart still calls it range noise.",
        timeframe="1m trigger, 15m regime gate",
        symbols="all configured",
        entry=">= 0.45% move over <= 2 closed 1m bars (or one bar's range) on >= 1.8x prior volume -> long a dump "
              "/ short a rip, only while the latest closed 15m close sits inside the prior 16-bar 15m high/low",
        stop="0.35% beyond the cascade extreme",
        targets="50% retrace of the cascade closes 100%; time exit after 30 min",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="0.45% / 1.8x trigger sized for the thin testnet tape; tight stop, fat snapback target, "
                       "120 s cooldown after each exit, 4h-range gate kept intact",
    )
    timeframes = ("1m", "15m")
    contributes_votes = False
    warmup_bars = 60
    # Geometry-driven target (50% retrace vs a 0.35% stop beyond the extreme): ~1R on a 0.45% cascade, 2-4R on
    # bigger ones. The floor only rejects fades where the close already retraced most of the move.
    min_rr = 0.5
    badges = ("PROXY",)

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_cascade: dict[str, dict[str, Any]] = {}
        self._regime_skips = 0

    def cooldown_ms(self) -> int:
        """The engine refuses a re-entry on that symbol for this long after ANY exit (win or scratch)."""
        return int(self.params.cooldown_after_loss_s) * 1000

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "1m")
        bars = max(1, int(p.move_bars))
        if len(candles) < max(int(p.vol_sma), bars) + 2:
            return []
        cascade = self._detect(c, candles, bars, p.move_pct / 100.0)
        if cascade is None:
            return []
        vol_avg = last(ctx.ind(c.symbol, "1m", "vol_sma", n=int(p.vol_sma)), 1)  # prior bars only
        if vol_avg is None or vol_avg <= 0 or c.volume < p.volume_mult * vol_avg:
            return []
        cascade["vol_mult"] = round(c.volume / vol_avg, 2)
        self._last_cascade[c.symbol] = cascade
        regime = self._inside_prior_range(c.symbol, ctx, int(p.range_bars_15m))
        if regime is None:
            cascade["skip"] = "15m warm-up"
            return []
        if not regime:
            self._regime_skips += 1
            cascade["skip"] = "15m close outside the prior range"
            return []
        price = ctx.last_price(c.symbol) or c.close
        lo, hi = cascade["extreme_low"], cascade["extreme_high"]
        if cascade["direction"] == "down":
            side, stop, tp = "long", lo * (1 - p.stop_pct / 100.0), lo + p.retrace * (hi - lo)
            ok = stop < price < tp
        else:
            side, stop, tp = "short", hi * (1 + p.stop_pct / 100.0), hi - p.retrace * (hi - lo)
            ok = tp < price < stop
        if not ok:
            cascade["skip"] = "price already past the retrace target"
            return []
        cascade["traded"] = True
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_price=[(tp, 1.0)], max_hold_s=int(p.max_hold_s), valid_bars=2,
            reason=f"fade {cascade['direction']} cascade {cascade['size_pct']:.2f}% on {cascade['vol_mult']:.1f}x vol",
            meta={"direction": cascade["direction"], "size_pct": cascade["size_pct"], "bars": cascade["bars"],
                  "extreme_low": lo, "extreme_high": hi, "vol_mult": cascade["vol_mult"]})]

    @staticmethod
    def _detect(c: Candle, candles: Sequence[Candle], bars: int, thr: float) -> dict[str, Any] | None:
        """Direction, size and extremes of a qualifying cascade ending at the newest closed bar (= `c`)."""
        ref = candles[-bars - 1].close
        move = (c.close - ref) / ref if ref > 0 else 0.0
        if abs(move) >= thr:
            direction, size = ("down" if move < 0 else "up"), abs(move)
            span = [candles[-i] for i in range(bars, 0, -1)]
        elif c.close > 0 and c.range / c.close >= thr and c.close != c.open:
            direction, size, span = ("down" if c.close < c.open else "up"), c.range / c.close, [c]
        else:
            return None
        return {"direction": direction, "size_pct": round(size * 100.0, 3), "bars": len(span), "ts": c.close_time,
                "extreme_low": min(x.low for x in span), "extreme_high": max(x.high for x in span), "traded": False}

    @staticmethod
    def _inside_prior_range(symbol: str, ctx: MarketContext, n: int) -> bool | None:
        """True when the latest closed 15m close lies inside the high/low of the previous n closed 15m bars."""
        c15 = ctx.candles(symbol, "15m")
        if len(c15) < n + 1:
            return None
        lo = last(ctx.ind(symbol, "15m", "lowest", n=n), 1)  # rolling windows INCLUDE the current bar -> offset 1
        hi = last(ctx.ind(symbol, "15m", "highest", n=n), 1)
        if lo is None or hi is None:
            return None
        return lo <= c15[-1].close <= hi

    def state(self) -> dict[str, Any]:
        return {"last_cascade": self._last_cascade, "regime_skips": self._regime_skips}

    def reset(self) -> None:
        self._last_cascade.clear()
        self._regime_skips = 0
