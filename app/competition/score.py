"""Transparent leaderboard score.

The score exists to order the leaderboard, nothing else. It never gates promotion -- that is
`qualification.judge`. Every component and every penalty is returned alongside the total so the
dashboard can show the arithmetic rather than a magic number.

Components are squashed into 0..1 before weighting so no single unbounded quantity (a 40x profit
factor, say) can dominate. The squash is documented per component rather than hidden in a helper.

Leverage carries a real penalty here. PaperLab's competition books are small enough that reaching
some symbols' minimum notional requires leverage, so leverage is permitted -- but a bot that gets
the same edge at 3x must out-rank one that needed 20x, or the leaderboard just ranks risk appetite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.competition.config import ScoreWeights
from app.competition.metrics import CompetitorMetrics


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _saturating(x: float, half: float) -> float:
    """0 at x<=0, 0.5 at x==half, approaching 1. Keeps outliers from running away with the score."""
    if x <= 0:
        return 0.0
    return x / (x + half)


@dataclass
class ScoreBreakdown:
    total: float = 0.0
    components: dict[str, float] = field(default_factory=dict)   # weighted contributions
    raw: dict[str, float] = field(default_factory=dict)          # pre-weight 0..1 values
    penalties: dict[str, float] = field(default_factory=dict)    # subtracted amounts

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "components": dict(self.components),
                "raw": dict(self.raw), "penalties": dict(self.penalties)}

    def explain(self) -> str:
        parts = [f"{k}={v:+.3f}" for k, v in self.components.items()]
        parts += [f"-{k}={v:.3f}" for k, v in self.penalties.items() if v]
        return f"{self.total:.3f} = " + " ".join(parts)


def score(m: CompetitorMetrics, w: ScoreWeights, *, max_leverage_allowed: int = 20,
          oos_ratio: float | None = None, consistency: float | None = None,
          stress_ratio: float | None = None, min_closed_trades: int = 100) -> ScoreBreakdown:
    """Rank-ordering score in roughly -1..1. Higher is better; negative means the penalties won.

    `consistency` is profitable_seasons / total_seasons, `oos_ratio` is profitable OOS windows /
    total, `stress_ratio` is stressed net profit / standard net profit. Each is skipped (scored 0,
    not assumed good) when its stage has not run.
    """
    b = ScoreBreakdown()

    # -- components ------------------------------------------------------------------------
    # after-cost return: 20% of the book is a strong season, so that sits at 0.5
    b.raw["after_cost_return"] = _saturating(m.net_return_pct, 0.20)
    # expectancy in R: +0.2R/trade is good
    b.raw["expectancy"] = _saturating(m.expectancy_r, 0.20)
    # profit factor: 1.0 is break-even, so measure the excess; PF 1.5 -> 0.5
    pf = 999.0 if m.profit_factor == float("inf") else m.profit_factor
    b.raw["profit_factor"] = _saturating(pf - 1.0, 0.50)
    # drawdown efficiency: net return per unit of drawdown suffered
    dd = max(m.max_drawdown_pct, 1e-9)
    b.raw["drawdown_efficiency"] = _saturating(m.net_return_pct / dd, 1.0)
    b.raw["consistency"] = _clamp(consistency) if consistency is not None else 0.0
    b.raw["oos_performance"] = _clamp(oos_ratio) if oos_ratio is not None else 0.0
    b.raw["stress_performance"] = _clamp(stress_ratio) if stress_ratio is not None else 0.0

    for name in ("after_cost_return", "expectancy", "profit_factor", "drawdown_efficiency",
                 "consistency", "oos_performance", "stress_performance"):
        b.components[name] = b.raw[name] * getattr(w, name)

    # -- penalties -------------------------------------------------------------------------
    # drawdown beyond 20% of the book starts costing
    b.penalties["drawdown"] = w.penalty_drawdown * _clamp((m.max_drawdown_pct - 0.20) / 0.30)
    b.penalties["liquidation"] = w.penalty_liquidation * (1.0 if m.liquidation_count else 0.0)
    lev_head = max(1, max_leverage_allowed - 1)
    b.penalties["leverage"] = w.penalty_leverage * _clamp((max(1, m.max_leverage_used) - 1) / lev_head)
    b.penalties["concentration"] = w.penalty_concentration * _clamp(
        (m.largest_trade_profit_contribution_pct - 0.30) / 0.50)
    b.penalties["fee_drag"] = w.penalty_fee_drag * _clamp(m.fee_to_gross_profit_ratio)
    b.penalties["slippage_drag"] = w.penalty_slippage_drag * _clamp(m.slippage_to_gross_profit_ratio)
    b.penalties["insufficient_sample"] = w.penalty_insufficient_sample * _clamp(
        1.0 - m.trades / max(1, min_closed_trades))

    b.total = sum(b.components.values()) - sum(b.penalties.values())
    return b
