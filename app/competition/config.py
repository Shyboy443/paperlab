"""Competition, season and qualification configuration.

Everything a result depends on lives in one of these dataclasses so a season can be reproduced
exactly: same data, same fees, same execution model, same gates, same seed. `Season.fingerprint()`
is what gets stored beside results; if it changes, the season is a different season.

Nothing here is a promise of profitability. The defaults are research starting points.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

# Fees and execution knobs belong to the execution layer; re-exported here because a
# Season embeds them and every existing import expects to find them on this module.
from app.execution.config import ExecutionConfig, FeeSchedule

# Competitor lifecycle. The pipeline runs left to right; DISQUALIFIED and FAILED are terminal for
# the season. Nothing beyond QUALIFIED is reachable without an explicit operator action.
State = Literal[
    "COMPETING",             # trading the season, not yet judged
    "INSUFFICIENT_SAMPLE",   # on the leaderboard, too few closed trades to judge
    "FAILED",                # judged and failed one or more hard gates
    "WATCHLIST",             # passed most gates, failed a soft one; worth watching
    "QUALIFIED",             # passed every hard gate on out-of-sample evidence
    "SHADOW_LIVE",           # running forward on live data, simulated execution
    "LIVE_CANDIDATE",        # forward edge held; awaiting operator approval for real capital
    "DISQUALIFIED",          # liquidated or crossed the catastrophic-equity floor
]

TERMINAL: frozenset[str] = frozenset({"FAILED", "DISQUALIFIED"})


@dataclass(frozen=True)
class RiskConfig:
    """Competition-level boundaries. The strategy proposes; this decides."""
    starting_balance: float = 20.0
    max_leverage: int = 20
    risk_per_trade_pct: float = 0.04
    max_margin_fraction_per_position: float = 0.50
    max_total_margin_fraction: float = 0.90
    max_daily_loss_pct: float = 0.12
    max_drawdown_pct: float = 0.30
    catastrophic_equity: float = 10.0   # season over for this bot at or below this equity


@dataclass(frozen=True)
class QualificationConfig:
    """Hard gates. A bot can rank #1 and still fail every one of these.

    The trade and window gates are written for the MASTER OOS LEDGER, not per window: requiring
    100 trades inside every 30-day out-of-sample window would exclude every lower-frequency
    strategy by construction rather than on merit.
    """
    min_closed_trades: int = 100
    min_net_profit: float = 0.0
    min_expectancy_r: float = 0.0
    min_profit_factor: float = 1.15
    max_drawdown_pct: float = 0.30
    min_profitable_oos_ratio: float = 0.55
    min_active_oos_ratio: float = 0.60
    max_symbol_concentration: float = 0.80   # share of OOS profit from a single symbol
    max_liquidations: int = 0
    max_ruin_probability: float = 0.05          # Monte Carlo P(losing 50%)
    max_stress_degradation: float = 1.0         # net profit may not go negative under stress
    max_profit_concentration: float = 0.40      # largest single trade's share of gross profit
    min_forward_trades: int = 30                # before LIVE_CANDIDATE

    # Soft gates: failing one of these lands on WATCHLIST rather than FAILED.
    soft: frozenset[str] = frozenset({"max_profit_concentration", "min_profitable_oos_ratio"})


@dataclass(frozen=True)
class ScoreWeights:
    """Transparent leaderboard score. Every component is displayed; none of them is a gate."""
    after_cost_return: float = 0.25
    expectancy: float = 0.20
    profit_factor: float = 0.15
    drawdown_efficiency: float = 0.15
    consistency: float = 0.10
    oos_performance: float = 0.10
    stress_performance: float = 0.05
    # Penalties, subtracted after the weighted sum.
    penalty_drawdown: float = 0.30
    penalty_liquidation: float = 1.00
    penalty_leverage: float = 0.20
    penalty_concentration: float = 0.20
    penalty_fee_drag: float = 0.10
    penalty_slippage_drag: float = 0.10
    penalty_insufficient_sample: float = 0.25


@dataclass(frozen=True)
class Season:
    """One reproducible tournament over a fixed window."""
    season_id: str
    competition_id: str
    start_ms: int
    end_ms: int
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    timeframe: str = "1m"
    venue: str = "BINANCE_USDM"
    label: str = ""                      # e.g. "2024 Q1 bullish"
    regime_label: str = ""
    dataset_version: str = ""
    code_version: str = ""
    seed: int = 7
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    qualification: QualificationConfig = field(default_factory=QualificationConfig)
    weights: ScoreWeights = field(default_factory=ScoreWeights)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["qualification"]["soft"] = sorted(self.qualification.soft)
        return d

    def fingerprint(self) -> str:
        """Stable hash of everything that can change a result. Store it with the results."""
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# Diagnostic leverage ceilings. Each one is a SEPARATE competitor, never merged: leverage changes
# which signals clear Binance's minimum notional, so S02@5x and S02@20x can trade a genuinely
# different population of setups. Merging them would hide whether an edge is real or an artefact of
# eligibility. Higher is NOT assumed better -- the score penalises leverage explicitly.
LEVERAGE_IDENTITIES: tuple[int, ...] = (5, 10, 20)


def competitor_version(cls: Any, params: Any = None, leverage: int | None = None) -> str:
    """`strategy + parameters + leverage ceiling` is the competitor identity.

    Changing a slider after seeing out-of-sample results creates a NEW competitor whose evaluation
    restarts; it must not silently inherit the old one's record. The hash covers the parameter
    values so `S21@10x:a1b2c3d4` and `S21@10x:9f8e7d6c` are different entrants, and the leverage
    ceiling is in the label so the three diagnostic variants never collide.
    """
    p = params if params is not None else cls.Params()
    try:
        values = {f.name: getattr(p, f.name) for f in dataclasses.fields(p)}
    except TypeError:
        values = {}
    blob = json.dumps({"id": cls.id, "params": values, "leverage": leverage},
                      sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:8]
    suffix = f"@{int(leverage)}x" if leverage else ""
    return f"{cls.id}{suffix}:{digest}"


def competitor_key(strategy_id: str, leverage: int | None) -> str:
    """Stable display/lookup key for one identity, e.g. "S02@10x"."""
    return f"{strategy_id}@{int(leverage)}x" if leverage else strategy_id


def season_windows(start_ms: int, end_ms: int, n: int, labels: Sequence[str] = ()) -> list[tuple[int, int, str]]:
    """Split a span into `n` equal seasons. Qualification must never rest on a single window."""
    if n < 1:
        raise ValueError("n must be >= 1")
    step = (end_ms - start_ms) // n
    out = []
    for i in range(n):
        lo = start_ms + i * step
        hi = end_ms if i == n - 1 else lo + step
        out.append((lo, hi, labels[i] if i < len(labels) else f"S{i + 1}"))
    return out
