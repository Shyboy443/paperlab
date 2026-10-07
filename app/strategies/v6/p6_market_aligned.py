"""V6.6 - Market-aligned alt trend.

Hypothesis: altcoins trend hardest when the whole market is trending the same way -- BTC's 4h and 1D trends agree, ETH
does not disagree, most of a liquid breadth set is on the same side of its daily average and aggregate open interest
is not unwinding. An alt that is ALSO outperforming BTC and breaks to a new 24-bar extreme in that direction is the
market's leader and continues.
Expected hold 6-24 h hourly / 12-72 h swing; ~4-12 setups / month hourly in trending markets, ~0 in chop; seeks 3R.
Fails at market turning points and in rotation (leadership changing hands).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, scale


@dataclass
class Params:
    min_breadth: float = P(0.60, min=0.5, max=0.9, step=0.05, label="breadth above the 1D EMA20 >= (longs; shorts <= 1 - x)")
    high_bars: int = P(24, min=6, max=72, step=1, label="new extreme over (signal bars)")
    min_rel_strength: float = P(0.0, min=-0.05, max=0.1, step=0.005, label="24h return minus BTC's >= (in the trade's favour)")
    min_agg_oi: float = P(-0.03, min=-0.2, max=0.0, step=0.005, label="aggregate OI change 24h >= (no market unwind)")
    stop_bars: int = P(6, min=2, max=24, step=1, label="stop anchor: extreme of the last (bars)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (signal bars)")


class MarketAlignedV6(V6Strategy):
    id = "V6.6"
    name = "Market-Aligned Alt Trend v6"
    family = "MARKET_ALIGNED_ALT_TREND"
    hypothesis = "an outperforming alt breaking out while BTC, ETH, breadth and aggregate OI all agree keeps trending"
    thesis = "BTC 4h and 1D agree, ETH 1D not against, breadth >= 60%, aggregate OI not unwinding; alt 4h trend agrees, " \
             "outperforms BTC over 24 h, closes at a new 24-bar extreme"
    fails_when = "market turning points; leadership rotation"
    expected_hold = "6-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~4-12 / month in trending markets, ~0 in chop"
    source = "new in V6: the first family that reads BTC, ETH, breadth and aggregate open interest"
    Params = Params
    doc = StrategyDoc(
        idea="Ride the market's leaders when the whole market trends the same way.",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot (observes the market)",
        entry="BTC 4h and 1D trends agree, ETH 1D not opposite, breadth >= 0.60 (shorts <= 0.40), aggregate OI 24h "
              ">= -3%; the coin's 4h trend agrees, its 24h return beats BTC's, and it closes at a new 24-bar high (low)",
        stop="beyond the extreme of the last 6 bars + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6", why_aggressive="market-wide trends carry leaders furthest")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        btc, eth = mkt.get("btc") or {}, mkt.get("eth") or {}
        b1d, b4h, e1d = btc.get("trend_1d"), btc.get("trend_4h"), eth.get("trend_1d")
        breadth, agg_oi = mkt.get("breadth_1d"), mkt.get("agg_oi_chg_24h")
        if b1d not in ("up", "down") or b4h != b1d or breadth is None or agg_oi is None:
            return None
        side = "long" if b1d == "up" else "short"
        against = "down" if side == "long" else "up"
        if e1d == against or agg_oi < p.min_agg_oi:
            return None
        width = breadth if side == "long" else 1.0 - breadth
        if width < p.min_breadth:
            return None
        own = self.ctx_trend(ctx, c.symbol, "4h" if self.horizon == "HOURLY" else "1d")
        if own != b1d:
            return None
        bars = list(cs)
        n = int(p.high_bars)
        prior = bars[-n - 1:-1]
        if len(prior) < n:
            return None
        day = self.day_bars
        mine = self.ret(bars, day)
        bret = btc.get("ret_24h")
        if mine is None or bret is None:
            return None
        rs = (mine - bret) if side == "long" else (bret - mine)
        if rs < p.min_rel_strength:
            return None
        recent = bars[-int(p.stop_bars):]
        if side == "long":
            if not c.close > max(x.high for x in prior):
                return None
            extreme = min(x.low for x in recent)
        else:
            if not c.close < min(x.low for x in prior):
                return None
            extreme = max(x.high for x in recent)
        return Setup(side, extreme, {"breadth": scale(width, p.min_breadth, 1.0), "rel_strength": scale(rs, 0.0, 0.10),
                                     "market_oi": scale(agg_oi, p.min_agg_oi, 0.05), "market": self.alignment(mkt, side)},
                     "market leader broke out")
