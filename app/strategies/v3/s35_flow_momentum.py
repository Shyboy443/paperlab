"""S35 v3 - Flow Momentum (liquidity / microstructure momentum).

Hypothesis: a burst of one-sided AGGRESSIVE flow -- takers buying (or selling) far more than usual,
on a volume and trade-count spike -- that breaks a local extreme is informed flow and continues for a
short while.

Taker flow is real data: Binance klines report the base volume bought by takers (archive field 9,
websocket "V"). A bar without it (0 taker volume reported) is never traded -- nothing is inferred.

Entry:   taker-buy share of the signal bar >= `flow_long` (<= `flow_short` for shorts); volume >=
         `min_volume_ratio` x and trade count >= `min_trades_ratio` x their 20-bar means; close beyond
         the prior `break_n`-bar extreme; slow context not opposite.
Stop:    `stop_atr` x ATR, clamped to 0.5%..2.0%.
Exits:   60% at 1.2R, break-even at 0.8R, 1.5 ATR trail, 20 bars max (flow bursts fade quickly).
Expected move: min(first target, 1 ATR of the fast context).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, scale


@dataclass
class Params:
    flow_long: float = P(0.60, min=0.5, max=0.9, step=0.01, label="min taker-buy share (longs)")
    flow_short: float = P(0.40, min=0.1, max=0.5, step=0.01, label="max taker-buy share (shorts)")
    min_volume_ratio: float = P(2.5, min=1.0, max=10.0, step=0.1, label="min volume vs 20-bar mean")
    min_trades_ratio: float = P(2.0, min=1.0, max=10.0, step=0.1, label="min trade count vs 20-bar mean")
    break_n: int = P(10, min=3, max=60, step=1, label="local extreme (bars)")
    stop_atr: float = P(1.2, min=0.5, max=4.0, step=0.1, label="stop (ATR)")
    tp1_r: float = P(1.2, min=0.8, max=4.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.6, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(1.5, min=0.5, max=5.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (bars)")


class FlowMomentumV3(V3Strategy):
    id = "S35"
    name = "Flow Momentum v3"
    family = "FLOW_MOMENTUM"
    hypothesis = "a one-sided taker-flow burst on a volume and trade-count spike that breaks a local extreme continues"
    Params = Params
    max_hold_bars = 20
    doc = StrategyDoc(
        idea="Follow a burst of one-sided taker flow that breaks a local extreme.",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="taker-buy share >= 60% (<= 40%); volume >= 2.5x and trades >= 2x their means; close "
              "beyond the prior 10-bar extreme; slow context not opposite",
        stop="1.2 ATR, 0.5%-2.0%",
        targets="60% at 1.2R, break-even at 0.8R, 1.5 ATR trail, 20 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="reacts to every aggressive flow burst on a fast timeframe",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        if c.volume <= 0 or c.taker_buy_volume <= 0:          # no reported taker flow: never inferred
            return []
        p, tf = self.params, self.signal_tf
        n = int(p.break_n)
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < max(n, 20) + 5:
            return []
        flow = c.taker_buy_volume / c.volume
        side = "long" if flow >= p.flow_long else "short" if flow <= p.flow_short else None
        if side is None:
            return []
        prior = cs[-n - 1:-1]
        if side == "long" and c.close <= max(x.high for x in prior):
            return []
        if side == "short" and c.close >= min(x.low for x in prior):
            return []
        vr, tr = self.volume_ratio(cs), self.trades_ratio(cs)
        if vr is None or tr is None or vr < p.min_volume_ratio or tr < p.min_trades_ratio:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        want = "up" if side == "long" else "down"
        if slow is None or slow["direction"] == ("down" if side == "long" else "up"):
            return []
        atr = self.atr(ctx, c.symbol, tf)
        if not atr:
            return []
        price = ctx.last_price(c.symbol) or c.close
        sp = self.stop_pct(price, p.stop_atr * atr)
        if sp is None:
            return []
        level = max(x.high for x in prior) if side == "long" else min(x.low for x in prior)
        ctx_atr = self.atr(ctx, c.symbol, self.ctx_fast)
        first = p.tp1_r * sp
        factors = {"flow": scale(abs(flow - 0.5), 0.10, 0.25), "volume": scale(vr, p.min_volume_ratio, 6.0),
                   "trades": scale(tr, p.min_trades_ratio, 5.0), "breakout": scale(abs(c.close - level) / atr, 0.0, 1.0),
                   "context": 1.0 if slow["direction"] == want else 0.5}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp,
                           tps_r=[(p.tp1_r, p.tp1_frac)], trail_atr=p.trail_atr, be_at_r=p.be_at_r,
                           expected_move_pct=min(first, ctx_atr / price) if ctx_atr else first,
                           expected_move_source=f"strategy: min(1.2R first target, 1 ATR of {self.ctx_fast})",
                           factors=factors,
                           reason=f"{tf} taker flow {flow:.0%} buy on {vr:.1f}x volume through the {n}-bar extreme",
                           extra={"context": {"slow": slow["direction"]}, "taker_buy_share": round(flow, 4),
                                  "volume_ratio": round(vr, 3), "trades_ratio": round(tr, 3)})]
