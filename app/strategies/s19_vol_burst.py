"""S19 Volatility Burst News Proxy - "trade the event candle"

Idea:       A 1m bar whose ATR(14) is 2x the 2h median on 2x average volume is a news/liquidation event in
            progress: go with the burst close, not against it.
Timeframe:  1m.
Symbols:    all configured symbols.
Entry:      ATR(14) of the closed bar >= 2 x the median ATR(14) of the prior 120 bars and volume >= 2 x the
            prior 20-bar volume SMA; bullish bar -> long, bearish -> short; dojis (body < 20% of range) skipped.
Stop:       the opposite side of the burst candle (its low for longs, its high for shorts).
Targets:    one target at 3R closes 100%, NO runner and no break-even move (bursts die, so the trade is either
            paid in full or stopped); time exit after 4 hours.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the 2x ATR / 2x volume trigger fires on the thin testnet tape (3x / 4x almost never did), it
            enters on the event candle itself with the whole bar as risk, and takes a 3R target flat out. A 60 s
            cooldown after each exit keeps it from re-entering the same decaying burst. There is no news feed on
            testnet, so the burst is a volatility/volume PROXY (badge). Contributes votes to the S20 ensemble.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")
    median_window: int = P(120, min=30, max=400, step=10, label="ATR median window (bars)", help="120 x 1m = 2h")
    atr_mult: float = P(2.0, min=1.5, max=8.0, step=0.1, label="ATR burst mult", help="bar ATR vs the prior median ATR")
    volume_mult: float = P(2.0, min=1.0, max=10.0, step=0.25, label="volume mult",
                           help="burst bar volume vs the volume SMA of the prior bars")
    vol_sma: int = P(20, min=5, max=100, step=1, label="volume SMA")
    tp_r: float = P(3.0, min=1.0, max=6.0, step=0.1, label="target (R)")
    max_hold_s: int = P(14400, min=300, max=28800, step=60, label="max hold (s)",
                        help="hold_v1: 3600 -> 14400s, so the burst target is not cut by the clock.")
    min_body_frac: float = P(0.2, min=0.0, max=0.8, step=0.05, label="min body / range", help="skips dojis")
    cooldown_s: int = P(60, min=0, max=600, step=5, label="re-entry cooldown (s)",
                        help="the engine refuses a re-entry on that symbol for this long after an exit")


class VolatilityBurstNewsProxy(Strategy):
    id = "S19"
    name = "Volatility Burst News Proxy"
    Params = Params
    doc = StrategyDoc(
        idea="Trade in the direction of a 2x-ATR / 2x-volume burst bar (news proxy), flat out to 3R.",
        timeframe="1m",
        symbols="all configured",
        entry="ATR(14) >= 2 x the median ATR(14) of the prior 120 bars and volume >= 2 x the prior 20-bar SMA; "
              "bullish burst -> long, bearish -> short, dojis (body < 20% of range) skipped",
        stop="the opposite side of the burst candle",
        targets="3R closes 100%, no runner and no break-even move; time exit after 60 min",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="2x / 2x trigger fires on this tape, trades the event candle itself to a flat 3R, 60 s "
                       "cooldown after each exit",
    )
    timeframes = ("1m",)
    min_rr = 2.5
    contributes_votes = True
    warmup_bars = 140
    badges = ("PROXY",)

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_burst: dict[str, dict[str, Any]] = {}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "1m")
        if len(candles) < int(p.atr_period) + int(p.median_window) + 1:
            return []
        atr_now = ctx.ind_last(c.symbol, "1m", "atr", n=int(p.atr_period))
        atr_med = last(ctx.ind(c.symbol, "1m", "atr_median", n=int(p.atr_period), window=int(p.median_window)), 1)
        vol_avg = last(ctx.ind(c.symbol, "1m", "vol_sma", n=int(p.vol_sma)), 1)  # prior bars only
        if atr_now is None or atr_med is None or vol_avg is None or atr_med <= 0 or vol_avg <= 0:
            return []
        if atr_now < p.atr_mult * atr_med or c.volume < p.volume_mult * vol_avg:
            return []
        rng = c.range
        body_frac = c.body / rng if rng > 0 else 0.0
        burst: dict[str, Any] = {"ts": c.close_time, "side": None, "atr_ratio": round(atr_now / atr_med, 2),
                                 "vol_mult": round(c.volume / vol_avg, 2), "body_frac": round(body_frac, 2),
                                 "traded": False}
        self._last_burst[c.symbol] = burst
        if rng <= 0 or c.close == c.open or body_frac < p.min_body_frac:
            burst["skip"] = "doji"
            return []
        side = "long" if c.is_bull else "short"
        burst["side"] = side
        price = ctx.last_price(c.symbol) or c.close
        stop = c.low if side == "long" else c.high
        if (side == "long" and stop >= price) or (side == "short" and stop <= price):
            burst["skip"] = "price already through the burst extreme"
            return []
        burst["traded"] = True
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop, tps_r=[(p.tp_r, 1.0)],
            trail=None, be_at_r=None, max_hold_s=int(p.max_hold_s), valid_bars=2,
            reason=f"vol burst {burst['atr_ratio']:.1f}x ATR on {burst['vol_mult']:.1f}x vol -> {side}",
            meta={"atr": atr_now, "atr_median": atr_med, "atr_ratio": burst["atr_ratio"],
                  "vol_mult": burst["vol_mult"], "body_frac": burst["body_frac"]})]

    def state(self) -> dict[str, Any]:
        return {"last_burst": self._last_burst}

    def reset(self) -> None:
        self._last_burst.clear()
