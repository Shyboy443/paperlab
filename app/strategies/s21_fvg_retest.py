"""S21 Fair Value Gap Retest - trade the retest of a 3-bar imbalance with the higher timeframe.

Idea:       A "fair value gap" (ICT / smart-money-concepts language, but the mechanic is just a 3-bar
            imbalance) is a range no trade passed through: on three consecutive 1m bars, bar[-3].high is
            below bar[-1].low (bullish) or bar[-3].low is above bar[-1].high (bearish). Price frequently
            returns to fill that range. We wait for the retest and join the higher-timeframe trend, we do
            NOT fade it.
Timeframe:  5m trigger, 15m trend gate.
Symbols:    all configured symbols.
Entry:      a fresh gap of at least `min_gap_pct` forms, price later trades back INTO the gap, and the
            latest closed 15m bar agrees (close above EMA`trend_ema` for longs, below for shorts) ->
            enter on the retest bar's close.
Stop:       `stop_buffer_pct` beyond the far edge of the gap (a filled gap invalidates the idea).
Targets:    2.0R takes 60%, the rest trails on ATR; time exit after `max_hold_s`.
Sizing:     standard per-trade risk, 10x virtual leverage.
Why aggressive: gaps are common on 5m crypto, so this trades often; the stop sits just past the gap so
            R is small relative to the target, and the trend gate keeps it on the momentum side.

The gap list is per symbol and bounded: a gap is dropped once fully filled or after `max_age_bars`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    min_gap_pct: float = P(0.12, min=0.02, max=2.0, step=0.01, label="min gap %",
                           help="gap height as a percentage of price; below this the retest is noise")
    stop_buffer_pct: float = P(0.10, min=0.02, max=1.0, step=0.01, label="stop buffer %",
                               help="distance beyond the far edge of the gap where the idea is wrong")
    trend_ema: int = P(50, min=10, max=200, step=5, label="15m trend EMA")
    max_age_bars: int = P(48, min=4, max=200, step=4, label="gap max age (bars)",
                          help="a gap that has not been retested in this many bars is forgotten")
    require_trend: bool = P(True, label="require the 15m trend",
                            help="off = take retests in both directions (much looser, more trades)")
    tp_r: float = P(2.0, min=0.5, max=10.0, step=0.1, label="first target (R)")
    tp_frac: float = P(0.6, min=0.1, max=1.0, step=0.05, label="fraction closed at the target")
    atr_trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(7200, min=300, max=28800, step=300, label="max hold (s)")
    cooldown_s: int = P(300, min=0, max=3600, step=30, label="cooldown after exit (s)")


class FairValueGapRetest(Strategy):
    id = "S21"
    name = "Fair Value Gap Retest"
    Params = Params
    doc = StrategyDoc(
        idea="Join the trend on a retest of a 3-bar imbalance (fair value gap) that price left behind.",
        timeframe="5m trigger, 15m trend gate",
        symbols="all configured",
        entry="3-bar gap >= 0.12% forms; price trades back into it; 15m close agrees with the direction",
        stop="0.10% beyond the far edge of the gap",
        targets="2.0R closes 60%, ATR trail on the rest, time exit at 2h",
        sizing="standard risk per trade at 10x virtual leverage",
        why_aggressive="gaps print often on 5m crypto so it trades frequently, and the stop sits just "
                       "past the gap edge which keeps R small next to a 2R target",
    )
    timeframes = ("5m", "15m")
    contributes_votes = False
    warmup_bars = 60
    max_positions = 1
    min_rr = 1.5
    default_leverage = 10

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._gaps: dict[str, list[dict[str, Any]]] = {}
        self._stats = {"formed": 0, "filled": 0, "expired": 0, "traded": 0, "trend_skips": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    # -- gap bookkeeping ------------------------------------------------------------------
    def _detect_gap(self, candles: Sequence[Candle], thr: float) -> dict[str, Any] | None:
        """A gap between bar[-3] and bar[-1], left by the middle bar's expansion."""
        if len(candles) < 3:
            return None
        a, mid, c = candles[-3], candles[-2], candles[-1]
        if c.close <= 0:
            return None
        if a.high < c.low:                                   # bullish imbalance
            lo, hi, side = a.high, c.low, "long"
        elif a.low > c.high:                                 # bearish imbalance
            lo, hi, side = c.high, a.low, "short"
        else:
            return None
        if (hi - lo) / c.close < thr or mid.range <= 0:
            return None
        return {"lo": lo, "hi": hi, "side": side, "ts": c.close_time, "age": 0}

    def _expire(self, symbol: str, c: Candle) -> None:
        keep: list[dict[str, Any]] = []
        for g in self._gaps.get(symbol, []):
            g["age"] += 1
            # fully traded through = the imbalance is gone, the level has no memory left
            if c.low <= g["lo"] and c.high >= g["hi"]:
                self._stats["filled"] += 1
                continue
            if g["age"] > int(self.params.max_age_bars):
                self._stats["expired"] += 1
                continue
            keep.append(g)
        self._gaps[symbol] = keep

    def _trend_ok(self, symbol: str, ctx: MarketContext, side: str) -> bool | None:
        c15 = ctx.candles(symbol, "15m")
        if len(c15) < int(self.params.trend_ema) + 2:
            return None
        e = last(ctx.ind(symbol, "15m", "ema", n=int(self.params.trend_ema)))
        if e is None:
            return None
        return c15[-1].close > e if side == "long" else c15[-1].close < e

    # -- hook ------------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(20, int(p.trend_ema)):
            return []
        self._expire(c.symbol, c)
        fresh = self._detect_gap(candles, p.min_gap_pct / 100.0)
        if fresh is not None:
            self._gaps.setdefault(c.symbol, []).append(fresh)
            self._stats["formed"] += 1
            return []                                        # never trade the bar that creates the gap

        # a retest = this bar reached into a gap that is still open
        hit = next((g for g in self._gaps.get(c.symbol, [])
                    if g["ts"] < c.close_time and c.low <= g["hi"] and c.high >= g["lo"]), None)
        if hit is None:
            return []
        side = hit["side"]
        if p.require_trend:
            ok = self._trend_ok(c.symbol, ctx, side)
            if ok is None:
                return []
            if not ok:
                self._stats["trend_skips"] += 1
                return []
        price = ctx.last_price(c.symbol) or c.close
        buf = p.stop_buffer_pct / 100.0
        if side == "long":
            stop = hit["lo"] * (1 - buf)
            if price <= stop:
                return []
        else:
            stop = hit["hi"] * (1 + buf)
            if price >= stop:
                return []
        hit["traded"] = True
        self._gaps[c.symbol] = [g for g in self._gaps[c.symbol] if g is not hit]
        self._stats["traded"] += 1
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp_r, p.tp_frac)],
            trail=TrailSpec("atr", "5m", p.atr_trail_mult, 14, True),
            max_hold_s=int(p.max_hold_s), valid_bars=2,
            reason=f"retest of a {(hit['hi'] - hit['lo']) / price * 100:.2f}% {side} gap",
            meta={"gap_lo": hit["lo"], "gap_hi": hit["hi"], "gap_age": hit["age"]})]

    def state(self) -> dict[str, Any]:
        return {"open_gaps": {s: len(g) for s, g in self._gaps.items() if g}, **self._stats}

    def reset(self) -> None:
        self._gaps.clear()
        for k in self._stats:
            self._stats[k] = 0
