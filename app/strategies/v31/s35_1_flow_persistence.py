"""S35.1 - Flow Persistence Momentum.

Hypothesis: taker flow that stays one-sided for several bars -- unusually so for THIS coin on THIS
timeframe -- on unusually high volume, breaking the recent range in the direction of the
higher-timeframe trend, is informed and keeps moving for hours.

Why it should overcome the V3 failure: V3's S35 was the family with the most consistent positive
drift after entry on 15m/30m (DEVELOPMENT: +15/+20/+24/+33 bps at 15m/1h/4h/12h on 30m; +17/+20 bps at
4h/12h on 15m) -- and the family V3 exited fastest (60% at 1.2R, 1.5 ATR trail on the signal
timeframe, 20 bars max: median hold 33-62 min, verdict HOLD TOO SHORT). V3.1 demands PERSISTENCE (the
3-bar flow and 3-bar volume, not one bar), measures both against the coin's own last 100 bars (a z-score
and a percentile, so a 30m bar -- whose flow averages out towards 50% -- is judged on its own scale, not
on a 3m threshold), requires the slow context to trend with the trade, and holds the runner on the 1h ATR.

Expected holding period: 1-10 hours. Expected move: 2R+ on the runner. Expected frequency (raw setups per
coin-day, DEVELOPMENT counts): 3m ~6, 5m ~3.5, 15m ~0.8, 30m ~0.25; executed trades fewer (one position
at a time, the edge gate).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v31.base import V31Strategy, scale

HISTORY = 100


@dataclass
class Params:
    persist_bars: int = P(3, min=2, max=8, step=1, label="persistence window (bars)")
    flow_z: float = P(1.0, min=0.5, max=4.0, step=0.1, label="window taker-flow z-score vs the last 100 windows")
    volume_pct: float = P(0.70, min=0.5, max=0.99, step=0.01, label="window volume percentile vs the last 100 windows")
    break_n: int = P(12, min=5, max=100, step=1, label="range broken (bars)")
    stop_atr: float = P(1.5, min=0.5, max=4.0, step=0.1, label="stop (ATR)")
    tp_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="partial target (R)")
    tp_frac: float = P(0.3, min=0.0, max=1.0, step=0.05, label="partial size")
    be_at_r: float = P(1.5, min=0.0, max=4.0, step=0.1, label="break-even at (R)")
    trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="runner trail (x ATR 1h)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (bars)")


def windows(cs: list[Candle], k: int, n: int) -> tuple[list[float], list[float]]:
    """Taker-buy share and total volume of every k-bar window ending in the last n+1 bars (oldest first)."""
    shares, vols = [], []
    for end in range(len(cs) - n, len(cs) + 1):
        w = cs[end - k:end]
        v = sum(x.volume for x in w)
        tb = sum(x.taker_buy_volume for x in w)
        shares.append(tb / v if v > 0 else 0.5)
        vols.append(v)
    return shares, vols


class FlowPersistenceV31(V31Strategy):
    id = "S35.1"
    name = "Flow Persistence Momentum v3.1"
    family = "FLOW_PERSISTENCE_MOMENTUM"
    hypothesis = "unusually persistent one-sided taker flow on unusually high volume, breaking the range with the higher-timeframe trend, continues for hours"
    thesis = "multi-bar taker-flow persistence (z-score vs own history) + volume persistence + range break, with the slow trend, long hold"
    why_v31 = "V3 S35 had positive 15m-12h drift on 15m/30m but held 33-62 min; adds persistence judged on the coin's own scale, trend and a long-hold runner"
    expected_hold = "1-10 hours"
    expected_frequency = "raw per coin-day: 3m ~6, 5m ~3.5, 15m ~0.8, 30m ~0.25"
    Params = Params
    doc = StrategyDoc(
        idea="Follow multi-bar one-sided taker flow that breaks the range with the higher-timeframe trend.",
        timeframe="3m / 5m / 15m / 30m trigger with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="3-bar taker-buy share z-score >= +1.0 (<= -1.0) against the last 100 windows and on the trade's "
              "side of 50%; 3-bar volume in the top 30% of the last 100 windows; close beyond the prior 12-bar "
              "extreme; slow context trends with the trade, fast context not against it",
        stop="1.5 ATR or the 3-bar extreme, 0.6%-2.5%",
        targets="break-even at 1.5R, 30% at 2R, runner trailed 2 x ATR(1h), 12 h max",
        sizing="AGGRESSIVE_V31 behind the expected-net-edge gate; Jev V3 on the +JEV twin",
        why_aggressive="trades every unusually persistent flow break with the trend and holds it",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        if c.volume <= 0 or c.taker_buy_volume <= 0:           # no reported taker flow: never inferred
            return []
        p, tf = self.params, self.signal_tf
        k, n = int(p.persist_bars), int(p.break_n)
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < HISTORY + k + 2 or len(cs) < n + 2:
            return []
        shares, vols = windows(cs, k, HISTORY)
        hist_s, flow = shares[:-1], shares[-1]
        hist_v, vol = vols[:-1], vols[-1]
        sd = statistics.pstdev(hist_s)
        if sd <= 0 or vol <= 0:
            return []
        z = (flow - statistics.fmean(hist_s)) / sd
        vpct = sum(1 for v in hist_v if v < vol) / len(hist_v)
        if z >= p.flow_z and flow > 0.5:
            side = "long"
        elif z <= -p.flow_z and flow < 0.5:
            side = "short"
        else:
            return []
        if vpct < p.volume_pct:
            return []
        prior = cs[-n - 1:-1]
        level = max(x.high for x in prior) if side == "long" else min(x.low for x in prior)
        if (side == "long" and c.close <= level) or (side == "short" and c.close >= level):
            return []
        want = "up" if side == "long" else "down"
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        fast = self.trend(ctx, c.symbol, self.ctx_fast)
        if slow is None or fast is None or slow["direction"] != want or fast["direction"] not in (want, "flat"):
            return []
        atr = self.atr(ctx, c.symbol, tf)
        if not atr:
            return []
        price = ctx.last_price(c.symbol) or c.close
        win = cs[-k:]
        extreme = min(x.low for x in win) if side == "long" else max(x.high for x in win)
        sp = self.stop_pct(price, max(p.stop_atr * atr, abs(price - extreme)))
        if sp is None:
            return []
        factors = {"flow": scale(abs(z), p.flow_z, 3.5), "volume": scale(vpct, p.volume_pct, 1.0),
                   "breakout": scale(abs(c.close - level) / atr, 0.0, 1.0), "trend": scale(abs(slow["slope"]), 0.0, 0.01),
                   "fast_context": 1.0 if fast["direction"] == want else 0.5}
        return [self.runner_entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp, tp_r=p.tp_r, tp_frac=p.tp_frac,
                                  be_at_r=p.be_at_r, trail_mult=p.trail_mult, factors=factors,
                                  reason=f"{tf} {k}-bar flow {flow:.0%} buy (z {z:+.1f}) on top-{1 - vpct:.0%} volume through the {n}-bar range",
                                  extra={"context": {"slow": slow["direction"], "fast": fast["direction"]},
                                         "taker_buy_share": round(flow, 4), "flow_z": round(z, 3), "volume_pct": round(vpct, 3)})]
