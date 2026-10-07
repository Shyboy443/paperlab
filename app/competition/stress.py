"""Stress scenarios: re-run a finalist under deliberately worse execution.

An edge that only exists at the modelled fee and spread is not an edge, it is a calibration. Each
scenario is a real re-replay through the same ExecutionModel with a harsher config -- not a haircut
applied to the result afterwards, because worse fills change WHICH trades happen, not merely what
they earn. A wider spread can push a setup under the fee gate and remove it entirely.

Scenarios deteriorate one dimension at a time so a failure is attributable. `FUNDING_ADVERSE` is the
exception and is applied as a multiplier on settled funding, since the funding rate is historical
fact rather than a model parameter.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.execution.config import ExecutionConfig, FeeSchedule


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    execution: Callable[[ExecutionConfig], ExecutionConfig] = lambda c: c
    fees: Callable[[FeeSchedule], FeeSchedule] = lambda f: f
    funding_multiplier: float = 1.0


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("NORMAL", "the season's own configuration"),
    Scenario("SLIPPAGE_1_5X", "execution costs 50% more",
             lambda c: dataclasses.replace(c, slippage_mult=c.slippage_mult * 1.5)),
    Scenario("SLIPPAGE_2X", "execution costs double",
             lambda c: dataclasses.replace(c, slippage_mult=c.slippage_mult * 2.0)),
    Scenario("SPREAD_1_5X", "the book is half again as wide",
             lambda c: dataclasses.replace(c, base_slippage_bps=c.base_slippage_bps * 1.5,
                                           vol_component=c.vol_component * 1.5)),
    Scenario("HIGHER_FEES", "a worse fee tier: both rates +50% (Binance USD-M: taker 0.075%, maker 0.03%)",
             fees=lambda f: dataclasses.replace(f, taker_rate=f.taker_rate * 1.5,
                                                maker_rate=f.maker_rate * 1.5,
                                                source=f.source + "+stress")),
    Scenario("ALL_TAKER", "no fill ever earns the maker rate",
             lambda c: dataclasses.replace(c, force_taker=True)),
    Scenario("LATENCY_2X", "orders take twice as long to reach the venue",
             lambda c: dataclasses.replace(c, signal_latency_ms=c.signal_latency_ms * 2,
                                           order_latency_ms=c.order_latency_ms * 2)),
    Scenario("FUNDING_ADVERSE", "every funding settlement costs double what it did",
             funding_multiplier=2.0),
)


@dataclass
class ScenarioResult:
    name: str
    description: str = ""
    ran: bool = False
    net_profit: float = 0.0
    net_return_pct: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    ending_equity: float = 0.0
    trades: int = 0
    liquidations: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class StressResult:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    ran: bool = False

    def by_name(self, name: str) -> ScenarioResult | None:
        return next((s for s in self.scenarios if s.name == name), None)

    @property
    def worst_net(self) -> float:
        runs = [s for s in self.scenarios if s.ran]
        return min((s.net_profit for s in runs), default=0.0)

    @property
    def worst_scenario(self) -> str:
        runs = [s for s in self.scenarios if s.ran]
        if not runs:
            return ""
        return min(runs, key=lambda s: s.net_profit).name

    def survives(self) -> bool:
        """Every scenario that ran must still end profitable after costs."""
        runs = [s for s in self.scenarios if s.ran]
        return bool(runs) and all(s.net_profit > 0 for s in runs)

    def degradation(self) -> float:
        """Worst net as a fraction of the NORMAL net. 1.0 = unchanged, <0 = sign flip."""
        base = self.by_name("NORMAL")
        if not base or not base.ran or base.net_profit == 0:
            return 0.0
        return self.worst_net / base.net_profit

    def to_dict(self) -> dict[str, Any]:
        return {"ran": self.ran, "scenarios": [s.to_dict() for s in self.scenarios],
                "worst_net": self.worst_net, "worst_scenario": self.worst_scenario,
                "survives": self.survives(), "degradation": self.degradation()}


def apply(scenario: Scenario, execution: ExecutionConfig,
          fees: FeeSchedule) -> tuple[ExecutionConfig, FeeSchedule]:
    return scenario.execution(execution), scenario.fees(fees)


def run(evaluate: Callable[[Scenario, ExecutionConfig, FeeSchedule], ScenarioResult],
        execution: ExecutionConfig, fees: FeeSchedule,
        scenarios: Sequence[Scenario] = SCENARIOS) -> StressResult:
    """`evaluate` re-runs the competitor under one scenario and returns its ScenarioResult."""
    out = StressResult()
    for sc in scenarios:
        ex, fe = apply(sc, execution, fees)
        try:
            r = evaluate(sc, ex, fe)
        except Exception as exc:                       # one bad scenario must not void the rest
            r = ScenarioResult(sc.name, sc.description, reason=f"{type(exc).__name__}: {exc}"[:200])
        r.description = r.description or sc.description
        out.scenarios.append(r)
    out.ran = any(s.ran for s in out.scenarios)
    return out
