"""V5.5 - Deleveraging reversal (open-interest PROXY for liquidations).

Hypothesis: a sharp open-interest drop together with a large same-direction price move is forced deleveraging
(longs liquidated into a fall, shorts squeezed into a rise); forced flow overshoots, and once it stops -- a signal bar
closing back through the previous bar -- price partially mean-reverts.
Bybit publishes no liquidation history (docs/V5_DATA_AUDIT.md), so this family uses a declared PROXY: OI down >= 6%
in 4 h or >= 12% in 24 h (SWING: >= 12% in 24 h or >= 20% in 3 days) with price down (up) >= 5% (SWING 10%) over the
last 12 (6) signal bars. No liquidation map is invented.
Expected hold 4-24 h; ~2-6 trades/month hourly, ~1-3 swing; seeks 3-10%.
Fails in genuine regime breaks where the cascade continues.
New vs V1-V4: V3 S15-like cascade fades read price only; none read open interest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, scale


@dataclass
class Params:
    oi_drop_fast: float = P(0.06, min=0.01, max=0.5, step=0.01, label="OI drop, fast window >=")
    oi_drop_slow: float = P(0.12, min=0.01, max=0.6, step=0.01, label="OI drop, slow window >=")
    price_move: float = P(0.05, min=0.01, max=0.5, step=0.01, label="price move with the cascade >= (hourly; swing x2)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (signal bars)")


class DeleveragingReversalV5(V5Strategy):
    id = "V5.5"
    name = "Deleveraging Reversal (OI proxy) v5"
    family = "DELEVERAGING_REVERSAL"
    hypothesis = "forced deleveraging (sharp OI drop + large same-direction move) overshoots and partially reverts"
    thesis = "OI drop proxy for liquidations + a large move + a reversal close; declared PROXY, no liquidation map"
    fails_when = "regime breaks where the cascade continues"
    expected_hold = "4-24 h"
    expected_frequency = "hourly ~2-6 / month, swing ~1-3 / month"
    why_new = "V1-V4 never read open interest; no liquidation history exists, so the proxy is declared"
    Params = Params
    doc = StrategyDoc(
        idea="After a cascade (OI collapsing while price falls hard), buy the first close back above the prior bar (mirror).",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="OI -6% in 4 h or -12% in 24 h (swing -12% 24 h or -20% 3 d); price -5% (swing -10%) over 12 (6) signal "
              "bars; the signal bar closes above the previous high (shorts: mirror after a squeeze)",
        stop="beyond the cascade extreme + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="forced flows create the largest short-horizon dislocations")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        fast, slow = (pos.get("oi_chg_4h"), pos.get("oi_chg_24h")) if self.horizon == "HOURLY" else \
            (pos.get("oi_chg_24h"), pos.get("oi_chg_3d"))
        if fast is None and slow is None:
            return None
        cascade = (fast is not None and fast <= -p.oi_drop_fast) or (slow is not None and slow <= -p.oi_drop_slow)
        if not cascade:
            return None
        n = int(self.lookback)
        move = self.ret(cs, n)
        need = p.price_move * (1.0 if self.horizon == "HOURLY" else 2.0)
        if move is None:
            return None
        prev = cs[-2]
        window = list(cs)[-n - 1:]
        drop = -min(v for v in (fast, slow) if v is not None)
        if move <= -need and c.close > prev.high:
            return Setup("long", min(x.low for x in window), {"oi_drop": scale(drop, p.oi_drop_fast, 0.4),
                                                               "move": scale(-move, need, 3 * need)}, "long liquidation cascade reversing")
        if move >= need and c.close < prev.low:
            return Setup("short", max(x.high for x in window), {"oi_drop": scale(drop, p.oi_drop_fast, 0.4),
                                                                "move": scale(move, need, 3 * need)}, "short squeeze reversing")
        return None
