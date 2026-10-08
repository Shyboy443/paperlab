"""V18 TREND: the next round of long-hold crypto strategies after V17's swing study passed 4 of 5 families (operator,
2026-10-08: "needs better improved strategies, keep going"). Each family targets one weakness V17 showed:

- false breakouts in sideways markets (V17.1 bleeds in chop)        -> V18.1 slower channel, V18.3 multi-horizon vote
- every alt breaking down together with BTC (the -18% day)          -> V18.2 BTC regime gate
- giving back a large part of a trend before the channel exit fires -> V18.4 chandelier trail
- only one way to make money (breakouts)                            -> V18.5 pullback entries inside a trend

Same frame as V17 (app/strategies/v17/swing.py): closed 4h candles of one Bybit perpetual, rules on COMPLETED daily
bars, one position per bot, initial stop a multiple of the daily ATR(20), the family's own exit as an exit signal on a
4h close, V6 sizing at 1% risk of the bot's book.

    V18.1  Turtle 55/20       long a 4h close above the prior 55-day high, short below the 55-day low; exit on a 4h close
                              beyond the opposite 20-day extreme; stop 2 ATR (the Turtle "system 2" rule)
    V18.2  Donchian + BTC     V17.1's Donchian 20/10, but a long is taken only while BTC's last daily close is above its
                              50-day SMA and a short only while it is below (BTC itself gates on its own trend);
                              exits untouched
    V18.3  multi-horizon      at each 00:00 UTC the sign of the 10/20/40/80-day returns is averaged: long at >= +0.5
           momentum           (3 of 4 up), short at <= -0.5; exit when the average reaches 0 or the other side; stop 4 ATR
    V18.4  Donchian chandelier V17.1's 20-day breakout entry; exit on a 4h close more than 3 daily ATR below the highest
                              daily high since entry (mirror for shorts), or beyond the opposite 20-day extreme; stop 2 ATR
    V18.5  trend pullback     daily up-trend = close above a rising EMA50 (mirror for down); after yesterday closed at
                              the lowest close of the last 5 days, long a 4h close above yesterday's high; exit on a 4h
                              close below the 10-day low or after 10 days; stop 2.5 ATR

No edge is claimed: the pre-registered study scripts/v18_trend_study.py decides.
"""
from __future__ import annotations

from typing import Any, Callable

from app.strategies.v17.swing import DAY, SwingV17, ema


class TrendV18(SwingV17):
    version = "v18"
    family = "TREND"


class Turtle55V18(TrendV18):
    id = "V18.1"
    name = "Turtle 55/20 breakout"
    thesis = "A break of the 55-day range is rarer and truer than a 20-day one; fewer whipsaws pay for the later entry"
    stop_atr = 2.0

    def exit_reason(self, c, ctx, days, side):
        w = days[-20:]
        if side == "long" and c.close < min(d.low for d in w):
            return "4h close below the 20-day low"
        if side == "short" and c.close > max(d.high for d in w):
            return "4h close above the 20-day high"
        return None

    def decide(self, c, ctx, days, atr, pos):
        if len(days) < 56:
            return None
        w = days[-55:]
        if c.close > max(d.high for d in w):
            return self.enter(c, ctx, "long", atr, "4h close above the 55-day high")
        if c.close < min(d.low for d in w):
            return self.enter(c, ctx, "short", atr, "4h close below the 55-day low")
        return None


def btc_side(daily: dict[int, float], h: int, n: int = 50) -> str | None:
    """'long' while BTC's last COMPLETED daily close (before instant `h`) is above its n-day SMA, 'short' below, None
    without enough history. `daily` maps UTC day number -> close."""
    d = h // DAY - 1
    days = [d - i for i in range(n)]
    if any(x not in daily for x in days):
        return None
    sma = sum(daily[x] for x in days) / n
    return "long" if daily[d] > sma else "short" if daily[d] < sma else None


class DonchianBtcV18(TrendV18):
    id = "V18.2"
    name = "Donchian 20/10 with BTC regime gate"
    thesis = "Alts break down with BTC: a breakout against BTC's own trend is the one most likely to fail"
    stop_atr = 2.0
    btc_daily: dict[int, float] = {}            # UTC day number -> BTC close; the study / worker fills it
    regime: Callable[[int], str | None] | None = None

    def allowed(self, c, days) -> str | None:
        if c.symbol == "BTCUSDT":
            closes = [d.close for d in days[-50:]]
            if len(closes) < 50:
                return None
            sma = sum(closes) / 50
            return "long" if closes[-1] > sma else "short" if closes[-1] < sma else None
        fn = self.regime or (lambda h: btc_side(self.btc_daily, h))
        return fn(c.close_time + 1)

    def exit_reason(self, c, ctx, days, side):
        w = days[-10:]
        if side == "long" and c.close < min(d.low for d in w):
            return "4h close below the 10-day low"
        if side == "short" and c.close > max(d.high for d in w):
            return "4h close above the 10-day high"
        return None

    def decide(self, c, ctx, days, atr, pos):
        w = days[-20:]
        side = "long" if c.close > max(d.high for d in w) else "short" if c.close < min(d.low for d in w) else None
        if side is None or self.allowed(c, days) != side:
            return None
        return self.enter(c, ctx, side, atr, f"4h close beyond the 20-day {'high' if side == 'long' else 'low'}, "
                                             f"BTC trend agrees")


class MultiMomentumV18(TrendV18):
    id = "V18.3"
    name = "Multi-horizon momentum vote"
    thesis = "Trends that show on several horizons at once persist; one horizon alone flips on noise"
    stop_atr = 4.0
    flip_on_exit = True
    horizons = (10, 20, 40, 80)

    def score(self, days) -> float | None:
        if len(days) < max(self.horizons) + 1:
            return None
        last = days[-1].close
        signs = [(1 if last > days[-1 - n].close else -1 if last < days[-1 - n].close else 0) for n in self.horizons]
        return sum(signs) / len(signs)

    @staticmethod
    def midnight(c) -> bool:
        return (c.close_time + 1) % DAY == 0

    def exit_reason(self, c, ctx, days, side):
        if not self.midnight(c):
            return None
        s = self.score(days)
        if s is None:
            return None
        if side == "long" and s <= 0:
            return f"momentum vote fell to {s:+.2f}"
        if side == "short" and s >= 0:
            return f"momentum vote rose to {s:+.2f}"
        return None

    def decide(self, c, ctx, days, atr, pos):
        if not self.midnight(c):
            return None
        s = self.score(days)
        if s is None:
            return None
        want = "long" if s >= 0.5 else "short" if s <= -0.5 else None
        if want is None or (pos is not None and pos.side == want):
            return None
        return self.enter(c, ctx, want, atr, f"momentum vote {s:+.2f}")


class ChandelierV18(TrendV18):
    id = "V18.4"
    name = "Donchian 20 with chandelier exit"
    thesis = "Keep V17.1's entries but trail the exit 3 ATR behind the best price, so less of each trend is given back"
    stop_atr = 2.0
    trail_atr = 3.0

    def exit_reason(self, c, ctx, days, side):
        pos = self.position(ctx, c.symbol)
        atr = self.daily_atr(days, self.params.atr_n)
        if pos is None or not atr:
            return None
        since = [d for d in days if d.close_time >= pos.entry_ts]
        w = days[-20:]
        if side == "long":
            best = max([d.high for d in since] + [pos.entry_price])
            if c.close < best - self.trail_atr * atr:
                return "4h close 3 ATR below the best high since entry"
            if c.close < min(d.low for d in w):
                return "4h close below the 20-day low"
        else:
            best = min([d.low for d in since] + [pos.entry_price])
            if c.close > best + self.trail_atr * atr:
                return "4h close 3 ATR above the best low since entry"
            if c.close > max(d.high for d in w):
                return "4h close above the 20-day high"
        return None

    def decide(self, c, ctx, days, atr, pos):
        w = days[-20:]
        if c.close > max(d.high for d in w):
            return self.enter(c, ctx, "long", atr, "4h close above the 20-day high")
        if c.close < min(d.low for d in w):
            return self.enter(c, ctx, "short", atr, "4h close below the 20-day low")
        return None


class PullbackV18(TrendV18):
    id = "V18.5"
    name = "Trend pullback"
    thesis = "Inside a daily trend, a 5-day low close is a dip that resumes; buying the reclaim earns the trend cheaper"
    stop_atr = 2.5
    hold_s = 10 * DAY

    def trend(self, days) -> str | None:
        if len(days) < 61:
            return None
        e = ema([d.close for d in days], 50)
        if days[-1].close > e[-1] and e[-1] > e[-6]:
            return "up"
        if days[-1].close < e[-1] and e[-1] < e[-6]:
            return "down"
        return None

    def exit_reason(self, c, ctx, days, side):
        w = days[-10:]
        if side == "long" and c.close < min(d.low for d in w):
            return "4h close below the 10-day low"
        if side == "short" and c.close > max(d.high for d in w):
            return "4h close above the 10-day high"
        return None

    def decide(self, c, ctx, days, atr, pos):
        t = self.trend(days)
        if t is None:
            return None
        y, last5 = days[-1], [d.close for d in days[-5:]]
        if t == "up" and y.close <= min(last5) and c.close > y.high:
            return self.enter(c, ctx, "long", atr, "reclaimed yesterday's high after a 5-day low close in an up-trend")
        if t == "down" and y.close >= max(last5) and c.close < y.low:
            return self.enter(c, ctx, "short", atr, "lost yesterday's low after a 5-day high close in a down-trend")
        return None


def load_v18() -> dict[str, type[TrendV18]]:
    return {cls.id: cls for cls in (Turtle55V18, DonchianBtcV18, MultiMomentumV18, ChandelierV18, PullbackV18)}
