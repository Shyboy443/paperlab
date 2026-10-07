"""V6.5 - Deleveraging reversal.

Hypothesis: a violent move during which open interest COLLAPSES is a forced unwind (liquidations and stop cascades),
not new information; once the forced flow is spent, price retraces part of the flush. Bybit publishes no liquidation
history, so the unwind is read from open interest (a >= 5% drop within hours) together with the price move -- a proxy,
labelled as one.
Expected hold 4-24 h hourly / 12-72 h swing; ~1-5 setups / month hourly, rarer swing; seeks 3R.
Fails when the flush is the start of a regime change (fundamental news, a trend that keeps going).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, close_location, scale


@dataclass
class Params:
    window_h: int = P(4, min=1, max=24, step=1, label="flush window (hours; swing x3)")
    min_oi_drop: float = P(0.05, min=0.01, max=0.3, step=0.005, label="OI drop over the window >=")
    min_move_atr: float = P(2.5, min=1.0, max=8.0, step=0.1, label="price move over the window >= (signal ATR)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (signal bars)")


class DeleveragingV6(V6Strategy):
    id = "V6.5"
    name = "Deleveraging Reversal v6"
    family = "DELEVERAGING_REVERSAL"
    hypothesis = "a flush that collapses open interest is forced flow; price retraces once it is spent"
    thesis = "OI down >= 5% within 4 h (swing 12 h) with a >= 2.5 ATR move; the bar closes back against the flush"
    fails_when = "regime change: the flush starts a new trend"
    expected_hold = "4-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~1-5 / month, swing rarer"
    source = "V5.5 DELEVERAGING_REVERSAL idea (too few V5 trades to judge); open interest as the liquidation proxy"
    Params = Params
    doc = StrategyDoc(
        idea="Fade a liquidation-style flush (price move with collapsing open interest) on the first reversal bar.",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot",
        entry="over the last 4 h (swing 12 h) OI fell >= 5% and price moved >= 2.5 signal ATR; the signal bar closes "
              "beyond the previous bar's extreme against the flush",
        stop="beyond the flush extreme + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6", why_aggressive="forced flow overshoots")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        if self.feed is None:
            return None
        hours = int(p.window_h) * (1 if self.horizon == "HOURLY" else 3)
        t = int(c.close_time) + 1
        oi_now = self.oi_at(t, t)
        oi_then = self.oi_at(t - hours * 3_600_000, t)
        if not oi_now or not oi_then:
            return None
        drop = 1.0 - oi_now / oi_then
        if drop < p.min_oi_drop:
            return None
        bars = list(cs)
        k = max(1, hours // (1 if self.horizon == "HOURLY" else 4))
        seg = bars[-k - 1:]
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not atr or len(seg) < 2:
            return None
        move = (seg[-2].close - seg[0].open) / atr          # the flush, up to the bar before the signal bar
        prev = bars[-2]
        if move <= -p.min_move_atr and c.close > prev.high:
            side, extreme, cl = "long", min(x.low for x in seg), close_location(c)
        elif move >= p.min_move_atr and c.close < prev.low:
            side, extreme, cl = "short", max(x.high for x in seg), 1.0 - close_location(c)
        else:
            return None
        return Setup(side, extreme, {"oi_drop": scale(drop, p.min_oi_drop, 0.20),
                                     "flush": scale(abs(move), p.min_move_atr, 2.0 * p.min_move_atr),
                                     "close": scale(cl, 0.5, 1.0)}, "OI flush reversed")
