"""Hard gates and the competitor state machine.

Ranking and qualification are deliberately separate. `score.py` produces a number for sorting the
leaderboard; this module decides whether a bot may progress, and it can fail the bot that is ranked
first. Every gate records the value it saw and the threshold it was held to, so a dashboard can
always answer "why did this fail" without re-running anything.

State machine:

    COMPETING -> INSUFFICIENT_SAMPLE | FAILED | WATCHLIST | QUALIFIED
    QUALIFIED -> SHADOW_LIVE -> LIVE_CANDIDATE        (each step is an explicit call)
    any       -> DISQUALIFIED                          (liquidation / catastrophic equity)

Nothing here promotes to live capital. LIVE_CANDIDATE means "an operator may now consider it",
which is the same bar PaperLab's existing `Engine.promote` + "GO LIVE" arm phrase already enforce.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.competition.config import QualificationConfig, State
from app.competition.metrics import CompetitorMetrics


@dataclass(frozen=True)
class Gate:
    name: str
    ok: bool
    actual: float
    threshold: float
    comparison: str              # ">=", "<=", "=="
    soft: bool = False
    detail: str = ""

    def describe(self) -> str:
        return f"{self.name} {self.actual:.4g} {self.comparison} {self.threshold:.4g}"


@dataclass
class QualificationResult:
    strategy_id: str
    state: State
    gates: list[Gate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)      # why it is not QUALIFIED
    evaluated_stages: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.state in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")

    def failed_gates(self) -> list[Gate]:
        return [g for g in self.gates if not g.ok]

    def to_dict(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "state": self.state, "reasons": list(self.reasons),
                "evaluated_stages": list(self.evaluated_stages),
                "gates": [{"name": g.name, "ok": g.ok, "actual": g.actual, "threshold": g.threshold,
                           "comparison": g.comparison, "soft": g.soft, "detail": g.detail}
                          for g in self.gates]}


def _gate(name: str, actual: float, threshold: float, comparison: str, cfg: QualificationConfig,
          detail: str = "") -> Gate:
    if comparison == ">=":
        ok = actual >= threshold
    elif comparison == "<=":
        ok = actual <= threshold
    else:
        ok = actual == threshold
    return Gate(name, ok, actual, threshold, comparison, name in cfg.soft, detail)


def judge(m: CompetitorMetrics, cfg: QualificationConfig, *,
          oos_ratio: float | None = None,
          active_ratio: float | None = None,
          symbol_concentration: float | None = None,
          ruin_probability: float | None = None,
          stress_worst_net: float | None = None) -> QualificationResult:
    """Apply every gate the available evidence supports.

    `oos_ratio`, `ruin_probability` and `stress_worst_net` come from walk-forward, Monte Carlo and
    stress testing. When a stage has not been run its gate is not evaluated and the bot cannot
    reach QUALIFIED -- absence of evidence is not a pass. The stages that did run are listed in
    `evaluated_stages` so the dashboard can show how far a bot actually got.
    """
    res = QualificationResult(m.strategy_id, "COMPETING")
    stages = ["season"]

    # Terminal conditions first: these end the season regardless of everything else.
    if m.liquidation_count > cfg.max_liquidations:
        res.state = "DISQUALIFIED"
        res.gates.append(_gate("max_liquidations", m.liquidation_count, cfg.max_liquidations, "<=", cfg))
        res.reasons.append(f"liquidated {m.liquidation_count}x")
        res.evaluated_stages = stages
        return res

    g = [
        _gate("min_net_profit", m.net_profit, cfg.min_net_profit, ">=", cfg, "after fees, slippage and funding"),
        _gate("min_expectancy_r", m.expectancy_r, cfg.min_expectancy_r, ">=", cfg),
        _gate("min_profit_factor", m.profit_factor if m.profit_factor != float("inf") else 999.0,
              cfg.min_profit_factor, ">=", cfg),
        _gate("max_drawdown_pct", m.max_drawdown_pct, cfg.max_drawdown_pct, "<=", cfg),
        _gate("max_profit_concentration", m.largest_trade_profit_contribution_pct,
              cfg.max_profit_concentration, "<=", cfg, "largest winner's share of gross profit"),
    ]
    if oos_ratio is not None:
        stages.append("walk_forward")
        g.append(_gate("min_profitable_oos_ratio", oos_ratio, cfg.min_profitable_oos_ratio, ">=", cfg))
        if active_ratio is not None:
            g.append(_gate("min_active_oos_ratio", active_ratio, cfg.min_active_oos_ratio, ">=", cfg,
                           "share of out-of-sample windows in which it traded at all"))
        if symbol_concentration is not None:
            g.append(_gate("max_symbol_concentration", symbol_concentration,
                           cfg.max_symbol_concentration, "<=", cfg,
                           "share of out-of-sample profit from one symbol"))
    if ruin_probability is not None:
        stages.append("monte_carlo")
        g.append(_gate("max_ruin_probability", ruin_probability, cfg.max_ruin_probability, "<=", cfg))
    if stress_worst_net is not None:
        stages.append("stress")
        g.append(_gate("stress_survives", stress_worst_net, 0.0, ">=", cfg,
                       "worst-case net profit under stressed execution"))
    res.gates = g
    res.evaluated_stages = stages

    # Sample size is judged last so the leaderboard still shows every other gate's value.
    sample = _gate("min_closed_trades", m.trades, cfg.min_closed_trades, ">=", cfg)
    res.gates.append(sample)
    if not sample.ok:
        res.state = "INSUFFICIENT_SAMPLE"
        res.reasons.append(f"{m.trades} closed trades < {cfg.min_closed_trades}")
        return res

    hard_failures = [x for x in g if not x.ok and not x.soft]
    soft_failures = [x for x in g if not x.ok and x.soft]
    if hard_failures:
        res.state = "FAILED"
        res.reasons = [x.describe() for x in hard_failures]
        return res

    missing = [s for s in ("walk_forward", "monte_carlo", "stress") if s not in stages]
    if missing:
        res.state = "WATCHLIST"
        res.reasons.append("not yet evaluated: " + ", ".join(missing))
        return res
    if soft_failures:
        res.state = "WATCHLIST"
        res.reasons = [x.describe() for x in soft_failures]
        return res

    res.state = "QUALIFIED"
    return res


def to_shadow_live(res: QualificationResult) -> QualificationResult:
    """QUALIFIED -> SHADOW_LIVE. Forward testing on live data, simulated execution."""
    if res.state != "QUALIFIED":
        raise ValueError(f"cannot enter shadow-live from {res.state}")
    res.state = "SHADOW_LIVE"
    return res


def to_live_candidate(res: QualificationResult, forward_trades: int,
                      cfg: QualificationConfig) -> QualificationResult:
    """SHADOW_LIVE -> LIVE_CANDIDATE, once forward evidence is deep enough.

    LIVE_CANDIDATE still sends nothing. Real capital needs a human going through the existing
    Engine.promote + "GO LIVE" arm path, which is unchanged by this module.
    """
    if res.state != "SHADOW_LIVE":
        raise ValueError(f"cannot become a live candidate from {res.state}")
    gate = _gate("min_forward_trades", forward_trades, cfg.min_forward_trades, ">=", cfg)
    res.gates.append(gate)
    if not gate.ok:
        res.reasons.append(gate.describe())
        return res
    res.state = "LIVE_CANDIDATE"
    return res


def disqualify(res: QualificationResult, reason: str) -> QualificationResult:
    res.state = "DISQUALIFIED"
    res.reasons.append(reason)
    return res
