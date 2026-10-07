"""S23 RSI Divergence - price makes a new extreme, momentum does not.

Idea:       Regular divergence. On the 5m chart, price prints a lower low than the previous swing low
            while RSI prints a HIGHER low (bullish), or a higher high while RSI prints a lower high
            (bearish). The second leg is running out of participation, so we take the reversal. This is
            different from S02 (RSI at an absolute extreme) - the RSI level does not matter here, only
            the direction it moved between two swings.
Timeframe:  5m, self-contained.
Symbols:    all configured symbols.
Entry:      two confirmed swing pivots within `lookback` bars, separated by at least `min_sep` bars,
            with price and RSI disagreeing by at least `min_rsi_gap` RSI points, and the trigger bar
            closing back through the newer pivot (confirmation, not a falling knife).
Stop:       `stop_buffer_atr` ATRs beyond the newer pivot extreme.
Targets:    the older pivot (the level the divergence points back to) closes 60%, the rest trails.
Sizing:     standard per-trade risk, 10x virtual leverage.
Why aggressive: divergence is a reversal trade against the immediate move, and the stop sits just past
            the pivot, so R is small - the trade is wrong quickly and cheaply when it is wrong.

Pivots are confirmed with `pivot_k` bars either side, so the newest usable pivot is always `pivot_k`
bars old. That lag is deliberate: an unconfirmed pivot is just the current bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    rsi_n: int = P(14, min=5, max=50, step=1, label="RSI length")
    pivot_k: int = P(2, min=1, max=6, step=1, label="pivot confirmation bars",
                     help="bars required either side of a swing before it counts as a pivot")
    lookback: int = P(60, min=10, max=200, step=5, label="lookback (bars)")
    min_sep: int = P(5, min=2, max=60, step=1, label="min bars between the two pivots")
    min_rsi_gap: float = P(4.0, min=0.5, max=30.0, step=0.5, label="min RSI divergence (points)")
    stop_buffer_atr: float = P(0.6, min=0.1, max=3.0, step=0.1, label="stop beyond the pivot (ATR)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    tp_frac: float = P(0.6, min=0.1, max=1.0, step=0.05, label="fraction closed at the older pivot")
    atr_trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(7200, min=300, max=28800, step=300, label="max hold (s)")
    cooldown_s: int = P(600, min=0, max=3600, step=30, label="cooldown after exit (s)")


def _pivots(values: Sequence[float], k: int, low: bool) -> list[int]:
    """Indices of confirmed swing points: an extreme with k strictly worse bars on both sides."""
    out: list[int] = []
    for i in range(k, len(values) - k):
        window = values[i - k:i + k + 1]
        v = values[i]
        if low and v == min(window) and all(v <= x for x in window):
            out.append(i)
        elif not low and v == max(window) and all(v >= x for x in window):
            out.append(i)
    return out


class RsiDivergence(Strategy):
    id = "S23"
    name = "RSI Divergence"
    Params = Params
    doc = StrategyDoc(
        idea="Take the reversal when price makes a new swing extreme and RSI refuses to confirm it.",
        timeframe="5m",
        symbols="all configured",
        entry="two confirmed pivots; price lower-low with RSI higher-low (or the mirror), >= 4 RSI points "
              "apart, trigger bar closing back through the newer pivot",
        stop="0.6 ATR beyond the newer pivot extreme",
        targets="the older pivot closes 60%, ATR trail on the rest, time exit at 2h",
        sizing="standard risk per trade at 10x virtual leverage",
        why_aggressive="a reversal against the live move with a stop just past the pivot, so it is wrong "
                       "cheaply and the target is the whole prior leg",
    )
    timeframes = ("5m",)
    contributes_votes = False
    warmup_bars = 120
    max_positions = 1
    min_rr = 1.2
    default_leverage = 10

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._stats = {"found": 0, "traded": 0, "no_confirm": 0, "rr_skips": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = list(ctx.candles(c.symbol, "5m"))[-int(p.lookback):]
        k, need = int(p.pivot_k), max(int(p.rsi_n), int(p.atr_n)) + 2 * int(p.pivot_k) + 5
        if len(candles) < max(need, int(p.min_sep) + 2 * k + 2):
            return []
        rsi_series = list(ctx.ind(c.symbol, "5m", "rsi", n=int(p.rsi_n)))[-len(candles):]
        atr = last(ctx.ind(c.symbol, "5m", "atr", n=int(p.atr_n)))
        if atr is None or atr <= 0 or len(rsi_series) != len(candles):
            return []
        if any(r is None for r in rsi_series[-(2 * k + int(p.min_sep) + 2):]):
            return []

        for low in (True, False):
            vals = [x.low for x in candles] if low else [x.high for x in candles]
            piv = [i for i in _pivots(vals, k, low) if rsi_series[i] is not None]
            if len(piv) < 2:
                continue
            new_i, old_i = piv[-1], piv[-2]
            if new_i - old_i < int(p.min_sep):
                continue
            pr_new, pr_old = vals[new_i], vals[old_i]
            r_new, r_old = float(rsi_series[new_i]), float(rsi_series[old_i])
            if low:
                diverges = pr_new < pr_old and r_new > r_old + p.min_rsi_gap
                side, stop = "long", pr_new - p.stop_buffer_atr * atr
                confirmed = c.close > pr_new
            else:
                diverges = pr_new > pr_old and r_new < r_old - p.min_rsi_gap
                side, stop = "short", pr_new + p.stop_buffer_atr * atr
                confirmed = c.close < pr_new
            if not diverges:
                continue
            self._stats["found"] += 1
            self._last[c.symbol] = {"side": side, "rsi_gap": round(abs(r_new - r_old), 1),
                                    "bars_apart": new_i - old_i, "ts": c.close_time}
            if not confirmed:
                self._stats["no_confirm"] += 1
                self._last[c.symbol]["skip"] = "trigger bar has not closed back through the pivot"
                continue
            price = ctx.last_price(c.symbol) or c.close
            target = pr_old
            risk = abs(price - stop)
            if risk <= 0 or abs(target - price) / risk < self.min_rr:
                self._stats["rr_skips"] += 1
                self._last[c.symbol]["skip"] = "older pivot is too close to pay for the stop"
                continue
            if side == "long" and not (stop < price < target):
                continue
            if side == "short" and not (target < price < stop):
                continue
            self._stats["traded"] += 1
            self._last[c.symbol]["traded"] = True
            return [self.make_entry(
                symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
                tps_price=[(target, p.tp_frac)],
                trail=TrailSpec("atr", "5m", p.atr_trail_mult, int(p.atr_n), True),
                max_hold_s=int(p.max_hold_s), valid_bars=2,
                reason=f"{'bullish' if low else 'bearish'} divergence, {abs(r_new - r_old):.1f} RSI points "
                       f"over {new_i - old_i} bars",
                meta={"rsi_new": round(r_new, 1), "rsi_old": round(r_old, 1),
                      "pivot_new": pr_new, "pivot_old": pr_old})]
        return []

    def state(self) -> dict[str, Any]:
        return {"last_divergence": self._last, **self._stats}

    def reset(self) -> None:
        self._last.clear()
        for k in self._stats:
            self._stats[k] = 0
