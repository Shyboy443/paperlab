"""Bot Trading Competition: identical market conditions, isolated books, cost-honest results.

The purpose is not to find the bot with the highest PnL. It is to find bots whose edge survives
realistic costs, several market regimes, out-of-sample testing and bad luck -- and to say exactly
why the others did not.

Layout:
    config.py         Season / FeeSchedule / ExecutionConfig / RiskConfig / QualificationConfig
    engine.py         CompetitionEngine -- one isolated ReplayEngine per competitor, one shared tape
    metrics.py        gross vs net, costs off the fill ledger, robustness statistics
    qualification.py  hard gates and the competitor state machine
    score.py          transparent leaderboard ordering (never a gate)

Execution, fills, fees, sizing and risk are NOT implemented here: they come from app/backtest's
ReplayEngine, which drives the live app/core components. There is one execution model.
"""
from app.competition.config import (ExecutionConfig, FeeSchedule, QualificationConfig, RiskConfig,
                                    ScoreWeights, Season, competitor_version, season_windows)
from app.competition.engine import Competitor, CompetitionEngine, CompetitionRun
from app.competition.metrics import CompetitorMetrics, compute
from app.competition.qualification import Gate, QualificationResult, judge
from app.competition.score import ScoreBreakdown, score

__all__ = ["CompetitionEngine", "CompetitionRun", "Competitor", "CompetitorMetrics",
           "ExecutionConfig", "FeeSchedule", "Gate", "QualificationConfig", "QualificationResult",
           "RiskConfig", "ScoreBreakdown", "ScoreWeights", "Season", "competitor_version",
           "compute", "judge", "score", "season_windows"]
