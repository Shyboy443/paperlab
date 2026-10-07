"""The validation pipeline: walk-forward -> Monte Carlo -> stress -> qualification.

One competitor at a time, in that order, short-circuiting where later stages cannot change the
answer. A bot with 11 out-of-sample trades does not need 10,000 Monte Carlo paths to be told it has
an insufficient sample, and spending eight stress replays on it would be pure waste.

Three engineering constraints shaped this:

* **Memory.** Five years of 1m bars across three symbols is ~9M candles, ~2 GB materialised. The
  tape is streamed instead: `stream_tape` heap-merges per-symbol month files in timestamp order and
  `ReplayEngine.run` consumes the iterator, so peak memory stays flat regardless of the window.
* **Determinism.** Competitors are independent, so they may be evaluated in parallel -- but only
  because each one gets its own seeded RNG, its own isolated Portfolio, and the identical ordered
  tape. Results do not depend on worker count or completion order.
* **Checkpointing.** A five-year, 81-competitor run takes hours. Each finished competitor is
  persisted immediately and a resume skips what is already done -- but only when the config
  fingerprint matches, because resuming across a config change would blend two experiments.
"""
from __future__ import annotations

import dataclasses
import hashlib
import heapq
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

from app.backtest import archive
from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine, unsupported
from app.competition import metrics as mx
from app.competition import montecarlo as mc
from app.competition import stress as st_mod
from app.competition import walkforward as wf
from app.competition.config import (LEVERAGE_IDENTITIES, QualificationConfig, Season,
                                    competitor_key, competitor_version)
from app.competition.qualification import judge
from app.competition.score import score
from app.core.types import Candle
from app.execution.config import ExecutionConfig, FeeSchedule

log = logging.getLogger("paperlab.competition.validation")


# ---- streaming tape --------------------------------------------------------------------------

def stream_symbol(settings: Any, symbol: str, months: Sequence[str],
                  interval: str = "1m") -> Iterator[Candle]:
    """Candles for one symbol, month by month, without holding the whole history."""
    for month in months:
        for c in archive.load_klines(settings, symbol, [month], interval):
            yield c


def stream_tape(settings: Any, symbols: Sequence[str], months: Sequence[str],
                interval: str = "1m") -> Iterator[Candle]:
    """One merged tape in (close_time, symbol) order, streamed.

    Every competitor is driven from an identical stream built the same way, which is what keeps the
    market data fair across competitors without materialising it.
    """
    streams = [stream_symbol(settings, s, months, interval) for s in symbols]
    for _, _, c in heapq.merge(*[((c.close_time, c.symbol, c) for c in s) for s in streams]):
        yield c


def count_bars(settings: Any, symbols: Sequence[str], months: Sequence[str]) -> int:
    meta = archive.load_meta(settings) or {}
    rows = 0
    for f in meta.get("files", []):
        if f.get("kind") == "klines" and f.get("symbol") in symbols and f.get("month") in months:
            rows += int(f.get("rows") or 0)
    return rows


def load_funding(settings: Any, symbols: Sequence[str], months: Sequence[str]) -> FundingSchedule:
    return FundingSchedule({s: archive.load_funding(settings, s, months) for s in symbols})


# ---- config ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationConfig:
    walk_forward: wf.WalkForwardConfig = field(default_factory=wf.WalkForwardConfig)
    monte_carlo: mc.MonteCarloConfig = field(default_factory=mc.MonteCarloConfig)
    qualification: QualificationConfig = field(default_factory=QualificationConfig)
    leverages: tuple[int, ...] = LEVERAGE_IDENTITIES
    run_stress: bool = True
    run_monte_carlo: bool = True
    # Stages after walk-forward are expensive; skip them for a competitor that cannot qualify
    # regardless of their outcome.
    skip_later_stages_when_hopeless: bool = True

    def fingerprint(self) -> str:
        blob = json.dumps(dataclasses.asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class CompetitorValidation:
    """Everything the pipeline learned about one leverage identity."""
    strategy_id: str
    key: str
    version: str
    leverage: int
    name: str = ""
    skipped: str = ""
    walk_forward: wf.WalkForwardResult | None = None
    monte_carlo: mc.MonteCarloResult | None = None
    stress: st_mod.StressResult | None = None
    qualification: Any = None
    score: Any = None
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def state(self) -> str:
        return self.qualification.state if self.qualification else "COMPETING"

    @property
    def oos_metrics(self) -> mx.CompetitorMetrics | None:
        return self.walk_forward.metrics if self.walk_forward else None

    def to_dict(self) -> dict[str, Any]:
        m = self.oos_metrics
        return {
            "strategy_id": self.strategy_id, "key": self.key, "version": self.version,
            "leverage": self.leverage, "name": self.name, "skipped": self.skipped,
            "state": self.state, "elapsed_s": self.elapsed_s, "error": self.error,
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "monte_carlo": self.monte_carlo.to_dict() if self.monte_carlo else None,
            "stress": self.stress.to_dict() if self.stress else None,
            "qualification": self.qualification.to_dict() if self.qualification else None,
            "score": self.score.to_dict() if self.score else None,
            "oos_metrics": m.to_dict() if m else None,
        }


# ---- the pipeline ------------------------------------------------------------------------------

class Validator:
    def __init__(self, settings: Any, season: Season, cfg: ValidationConfig,
                 months: Sequence[str], rules: Any = None, brackets: Any = None):
        self.settings = settings
        self.season = season
        self.cfg = cfg
        self.months = list(months)
        self.rules = rules
        self.brackets = brackets
        self.funding = load_funding(settings, season.symbols, self.months)

    # -- one replay ------------------------------------------------------------------------
    def _replay(self, cls: Any, leverage: int, execution: ExecutionConfig,
                fees: FeeSchedule, reset_at: Sequence[int] | None = None) -> Any:
        eng = ReplayEngine(self._settings(), self.season.symbols, rules=self.rules,
                           seed=self.season.seed, funding=self.funding,
                           execution=execution, fees=fees, brackets=self.brackets)
        bars = stream_tape(self.settings, self.season.symbols, self.months)
        return eng.run(cls, bars, leverage=leverage, reset_at=reset_at)

    def _settings(self) -> Any:
        risk = self.season.risk
        return dataclasses.replace(self.settings,
                                   strategy_starting_balance=float(risk.starting_balance),
                                   risk_per_trade_pct=float(risk.risk_per_trade_pct))

    # -- one competitor ---------------------------------------------------------------------
    def validate(self, sid: str, cls: Any, leverage: int,
                 windows: Sequence[wf.Window]) -> CompetitorValidation:
        t0 = time.time()
        cv = CompetitorValidation(strategy_id=sid, key=competitor_key(sid, leverage),
                                  version=competitor_version(cls, leverage=leverage),
                                  leverage=leverage, name=getattr(cls, "name", sid))
        why = unsupported(sid, cls)
        if why:
            cv.skipped = why
            cv.elapsed_s = time.time() - t0
            return cv
        try:
            balance = float(self.season.risk.starting_balance)
            resets = [w.test_start for w in windows]
            result = self._replay(cls, leverage, self.season.execution, self.season.fees, resets)
            cv.walk_forward = wf.partition(result, windows, balance, sid, cv.version,
                                           leverage, self.cfg.walk_forward)
            cv.walk_forward.params_before = cv.walk_forward.params_after = \
                wf.params_fingerprint(cls())
            m = cv.walk_forward.metrics
            hopeless = self._hopeless(cv.walk_forward)

            if self.cfg.run_monte_carlo and not (hopeless and self.cfg.skip_later_stages_when_hopeless):
                cv.monte_carlo = mc.run(cv.walk_forward.oos_trades, balance, self.cfg.monte_carlo)
            if self.cfg.run_stress and not (hopeless and self.cfg.skip_later_stages_when_hopeless):
                cv.stress = self._stress(cls, leverage, windows, balance)

            cv.qualification = judge(
                m, self.cfg.qualification,
                oos_ratio=cv.walk_forward.profitable_ratio,
                active_ratio=cv.walk_forward.active_ratio,
                symbol_concentration=cv.walk_forward.symbol_concentration(),
                ruin_probability=cv.monte_carlo.ruin_probability if (cv.monte_carlo and cv.monte_carlo.ran) else None,
                stress_worst_net=cv.stress.worst_net if (cv.stress and cv.stress.ran) else None)
            cv.score = score(m, self.season.weights,
                             max_leverage_allowed=max(self.cfg.leverages),
                             oos_ratio=cv.walk_forward.profitable_ratio,
                             consistency=cv.walk_forward.active_ratio,
                             stress_ratio=cv.stress.degradation() if (cv.stress and cv.stress.ran) else None,
                             min_closed_trades=self.cfg.qualification.min_closed_trades)
        except Exception as exc:
            log.exception("%s failed", cv.key)
            cv.error = f"{type(exc).__name__}: {exc}"[:300]
        cv.elapsed_s = time.time() - t0
        return cv

    def _hopeless(self, w: wf.WalkForwardResult) -> bool:
        """True when no later stage could rescue this competitor.

        Monte Carlo and stress can only make a result worse, so a bot already failing a hard gate
        on the master OOS ledger cannot be saved by running them.
        """
        m = w.metrics
        if m is None:
            return True
        cfg = self.cfg.qualification
        return (m.trades < cfg.min_closed_trades
                or m.net_profit <= cfg.min_net_profit
                or m.expectancy_r <= cfg.min_expectancy_r
                or m.liquidation_count > cfg.max_liquidations)

    def _stress(self, cls: Any, leverage: int, windows: Sequence[wf.Window],
                balance: float) -> st_mod.StressResult:
        resets = [w.test_start for w in windows]

        def evaluate(sc: st_mod.Scenario, ex: ExecutionConfig,
                     fe: FeeSchedule) -> st_mod.ScenarioResult:
            result = self._replay(cls, leverage, ex, fe, resets)
            part = wf.partition(result, windows, balance, cls.id, "", leverage,
                                self.cfg.walk_forward)
            m = part.metrics
            net = m.net_profit
            if sc.funding_multiplier != 1.0:
                # Funding rates are historical fact, so the scenario scales the settled amount
                # rather than pretending a different rate was published.
                extra = m.funding_paid * (sc.funding_multiplier - 1.0)
                net += extra
            return st_mod.ScenarioResult(
                sc.name, sc.description, ran=True, net_profit=net,
                net_return_pct=net / balance if balance else 0.0,
                expectancy_r=m.expectancy_r, profit_factor=m.profit_factor,
                max_drawdown_pct=m.max_drawdown_pct, ending_equity=balance + net,
                trades=m.trades, liquidations=m.liquidation_count)

        return st_mod.run(evaluate, self.season.execution, self.season.fees)
