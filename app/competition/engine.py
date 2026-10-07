"""CompetitionEngine: run every competitor over one identical tape, in isolation.

Isolation is structural, not a convention. Each competitor gets its own `ReplayEngine`, which means
its own `Portfolio`, `RiskManager` and wallet. There is no code path by which one bot's position can
move another's balance, because they do not share an object.

That also fixes a subtlety in reusing the live engine directly: `RiskManager.approve` enforces
portfolio-wide gross and net notional caps computed over `total_equity`. In the live lab that is
correct -- twenty books share one real account. In a competition it would let a busy bot crowd out a
quiet one's entries, which is not an edge difference. One engine per competitor keeps those caps
per-book, where a competition needs them.

Fairness of the market stream is likewise structural: every competitor is handed the same immutable
`bars` sequence in the same order, so no bot can see an event another did not.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from app.backtest.brackets import BracketTable
from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine, ReplayResult, unsupported
from app.competition import metrics as mx
from app.competition.config import LEVERAGE_IDENTITIES, Season, competitor_key, competitor_version
from app.competition.qualification import QualificationResult, judge
from app.competition.score import ScoreBreakdown, score
from app.core.types import Candle, MarketRules

log = logging.getLogger("paperlab.competition")


@dataclass
class Competitor:
    """One entrant: a strategy at a specific parameter version AND leverage ceiling."""
    strategy_id: str
    version: str
    name: str = ""
    leverage: int | None = None
    skipped: str = ""                         # why it could not compete at all
    result: ReplayResult | None = None
    metrics: mx.CompetitorMetrics | None = None
    qualification: QualificationResult | None = None
    breakdown: ScoreBreakdown | None = None

    @property
    def score(self) -> float:
        return self.breakdown.total if self.breakdown else float("-inf")

    @property
    def key(self) -> str:
        """Display identity, e.g. "S02@10x". Distinct per leverage variant."""
        return competitor_key(self.strategy_id, self.leverage)

    @property
    def state(self) -> str:
        if self.skipped:
            return "COMPETING"
        return self.qualification.state if self.qualification else "COMPETING"

    def row(self) -> dict[str, Any]:
        """One leaderboard row. Gross and net are both present, deliberately."""
        m = self.metrics
        if m is None:
            return {"strategy_id": self.strategy_id, "key": self.key, "leverage": self.leverage,
                    "version": self.version, "name": self.name,
                    "skipped": self.skipped, "state": self.state}
        return {
            "strategy_id": self.strategy_id, "key": self.key, "leverage": self.leverage,
            "version": self.version, "name": self.name,
            "equity": m.ending_equity, "gross_pnl": m.gross_pnl, "net_pnl": m.net_profit,
            "return_pct": m.net_return_pct, "max_dd_pct": m.max_drawdown_pct, "trades": m.trades,
            "win_rate": m.win_rate, "expectancy_r": m.expectancy_r, "profit_factor": m.profit_factor,
            "fees": m.fees_paid, "slippage": m.slippage_cost, "funding": m.funding_paid,
            "max_leverage": m.max_leverage_used, "liquidations": m.liquidation_count,
            "score": self.score, "state": self.state,
            "reasons": list(self.qualification.reasons) if self.qualification else [],
        }


@dataclass
class CompetitionRun:
    season: Season
    fingerprint: str
    competitors: list[Competitor] = field(default_factory=list)
    bars: int = 0

    def leaderboard(self, by: str = "score") -> list[Competitor]:
        """Ranked competitors. Rank is NOT qualification: #1 here can still be FAILED."""
        keys: dict[str, Callable[[Competitor], float]] = {
            "score": lambda c: c.score,
            "return": lambda c: c.metrics.net_return_pct if c.metrics else float("-inf"),
            "expectancy": lambda c: c.metrics.expectancy_r if c.metrics else float("-inf"),
            "profit_factor": lambda c: (c.metrics.profit_factor if c.metrics else float("-inf")),
            "drawdown": lambda c: -(c.metrics.max_drawdown_pct if c.metrics else 1e9),
            "trades": lambda c: c.metrics.trades if c.metrics else -1,
        }
        key = keys.get(by, keys["score"])
        ran = [c for c in self.competitors if c.metrics is not None]
        return sorted(ran, key=key, reverse=True)

    def qualified(self) -> list[Competitor]:
        return [c for c in self.competitors if c.qualification and c.qualification.passed]

    def summary(self) -> dict[str, Any]:
        ran = [c for c in self.competitors if c.metrics is not None]
        states: dict[str, int] = {}
        for c in ran:
            states[c.state] = states.get(c.state, 0) + 1
        return {"season": self.season.season_id, "label": self.season.label,
                "fingerprint": self.fingerprint, "competitors": len(ran),
                "skipped": len(self.competitors) - len(ran), "bars": self.bars,
                "starting_balance": self.season.risk.starting_balance, "states": states}


class CompetitionEngine:
    def __init__(self, settings: Any, season: Season, rules: dict[str, MarketRules] | None = None,
                 funding: FundingSchedule | None = None, brackets: BracketTable | None = None):
        self.settings = settings
        self.season = season
        self.rules = rules
        self.funding = funding
        self.brackets = brackets or BracketTable.fallback()

    def run(self, classes: dict[str, Any], bars: Sequence[Candle], *,
            only: Iterable[str] | None = None,
            leverages: Sequence[int] | None = None) -> CompetitionRun:
        """Run every eligible strategy over `bars`, once per leverage identity.

        `bars` must already be sorted and immutable: every competitor is handed the same object.
        `leverages` defaults to the season's single ceiling; passing LEVERAGE_IDENTITIES enters
        S02@5x, S02@10x and S02@20x as three SEPARATE competitors with their own versions, ledgers
        and qualification results. They are never merged, because the leverage ceiling changes which
        signals clear Binance's minimum notional -- so the variants can trade a different population
        of setups, and collapsing them would hide whether an edge is real or an eligibility artefact.
        """
        season = self.season
        run = CompetitionRun(season=season, fingerprint=season.fingerprint(), bars=len(bars))
        wanted = sorted(only) if only is not None else sorted(classes)
        levs = list(leverages) if leverages else [season.risk.max_leverage]
        for sid in wanted:
            cls = classes.get(sid)
            if cls is None:
                continue
            why = unsupported(sid, cls)
            for lev in levs:
                version = competitor_version(cls, leverage=lev)
                comp = Competitor(strategy_id=sid, version=version,
                                  name=getattr(cls, "name", sid), leverage=lev)
                if why:
                    comp.skipped = why
                    run.competitors.append(comp)
                    continue
                comp.result = self._run_one(cls, bars, lev)
                comp.metrics = mx.compute(sid, comp.result.trades, comp.result.fills,
                                          comp.result.equity, comp.result.starting_equity,
                                          rejects=comp.result.rejects, halted=comp.result.halted,
                                          version=version, leverage=comp.result)
                comp.metrics.configured_max_leverage = lev
                comp.qualification = judge(comp.metrics, season.qualification)
                comp.breakdown = score(comp.metrics, season.weights,
                                       max_leverage_allowed=max(levs),
                                       min_closed_trades=season.qualification.min_closed_trades)
                run.competitors.append(comp)
                log.info("%s: %d trades, net %+.2f, effLev %.1fx, state %s", comp.key,
                         comp.metrics.trades, comp.metrics.net_profit,
                         comp.metrics.avg_effective_leverage, comp.state)
        return run

    def _season_settings(self) -> Any:
        """Settings with the SEASON's risk config applied.

        `ReplayEngine` sizes from `settings.strategy_starting_balance` and
        `settings.risk_per_trade_pct`, so a Season whose RiskConfig says 20 USDT would otherwise be
        replayed at whatever the server's environment happens to hold -- the header would read
        "20.00 USDT" while every book actually started somewhere else. Overriding here keeps the
        Season the single source of truth for what it claims to be.
        """
        risk = self.season.risk
        return dataclasses.replace(self.settings,
                                   strategy_starting_balance=float(risk.starting_balance),
                                   risk_per_trade_pct=float(risk.risk_per_trade_pct))

    def _run_one(self, cls: Any, bars: Sequence[Candle], leverage: int | None = None) -> ReplayResult:
        eng = ReplayEngine(self._season_settings(), self.season.symbols, rules=self.rules,
                           seed=self.season.seed, funding=self.funding,
                           execution=self.season.execution, fees=self.season.fees,
                           brackets=self.brackets)
        return eng.run(cls, bars, since_ms=self.season.start_ms,
                       leverage=leverage or self.season.risk.max_leverage)
