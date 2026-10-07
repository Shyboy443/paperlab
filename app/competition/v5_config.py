"""V5 HOURLY / DAILY FUTURES ARENA: windows, identity, AGGRESSIVE_V5 sizing, exits and the pre-registered gates
(docs/V5_PROTOCOL.md). V1-V4 are frozen and untouched.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.competition.bots import RiskProfile
from app.execution.config import BYBIT_LINEAR, ExecutionConfig, FeeSchedule

PROTOCOL_VERSION = "V5_HOURLY_DAILY_PROTOCOL_V1"
VENUE = "BYBIT_LINEAR"
PRICE_TAPE = "BYBIT_LINEAR_1M"
HORIZONS: tuple[str, ...] = ("HOURLY", "SWING")
HTAG = {"HOURLY": "H", "SWING": "S"}
DOCS = Path(__file__).resolve().parents[2] / "docs"
EXIT_TIME_STOPS_H: tuple[int, ...] = (8, 12, 24, 48, 72)
EXIT_TIME_STOPS_BY_CLASS: tuple[tuple[str, tuple[int, ...]], ...] = (("HOURLY", (8, 12, 24)), ("SWING", (12, 24, 48, 72)))
EXIT_BASELINE = {"HOURLY": "X24", "SWING": "X72"}
EXIT_MIN_IMPROVEMENT_R = 0.02
EXIT_TARGETS_R: tuple[float | None, ...] = (None, 2.0, 3.0)
MFE_HORIZONS_H: tuple[int, ...] = (1, 2, 4, 8, 12, 24, 48, 72)


@dataclass(frozen=True)
class Window:
    role: str
    months: tuple[str, ...]
    trade_from: str
    trade_to: str
    universe_file: str
    subperiods: tuple[tuple[str, str], ...]

    @property
    def days(self) -> float:
        a, b = dt.date.fromisoformat(self.trade_from), dt.date.fromisoformat(self.trade_to)
        return float((b - a).days + 1)


def _months(first: str, last: str) -> tuple[str, ...]:
    from app.backtest.archive import months_between
    return tuple(months_between(first, last))


DEV = Window("DEVELOPMENT", _months("2024-12", "2026-08"), "2025-03-01", "2026-08-31", "V5_UNIVERSE_DEV.json",
             (("2025-03-01", "2025-08-31"), ("2025-09-01", "2026-02-28"), ("2026-03-01", "2026-08-31")))
TEST = Window("TEST", _months("2023-06", "2025-02"), "2023-09-01", "2025-02-28", "V5_UNIVERSE_TEST.json",
              (("2023-09-01", "2024-02-29"), ("2024-03-01", "2024-08-31"), ("2024-09-01", "2025-02-28")))
WINDOWS = {"DEVELOPMENT": DEV, "TEST": TEST}


def load_universe(window: Window, docs: Path = DOCS) -> dict[str, Any]:
    snap = json.loads((docs / window.universe_file).read_text(encoding="utf-8"))
    coins = [s[:-4] if s.endswith("USDT") else s for s in snap["universe"]]
    blob = json.dumps({"universe": snap["universe"], "rule": snap.get("rule_fingerprint"),
                       "scoring": snap.get("scoring_period")}, sort_keys=True)
    return {"coins": coins, "snapshot": snap, "fingerprint": hashlib.sha256(blob.encode()).hexdigest()[:12]}


# ---- risk -------------------------------------------------------------------------------------------------------

AGGRESSIVE_V5 = RiskProfile(
    name="AGGRESSIVE_V5", starting_balance=20.0, max_leverage=20,  # type: ignore[arg-type]
    ordinary_risk_pct=0.010, strong_risk_pct=0.015, exceptional_risk_pct=0.020, max_risk_pct=0.020,
    attack_multiplier=2.0, defensive_multiplier=1.0, health_window=20, attack_min_trades=0,
    attack_min_expectancy_r=-0.25, attack_max_drawdown=0.18, defensive_expectancy_r=-9.0,
    defensive_drawdown=1.0, halt_drawdown=0.30, strong_quality=0.60, exceptional_quality=0.85,
    attack_min_edge_to_cost=2.0)
TIER_RISK = {"TAKE": 0.010, "HIGH_CONVICTION": 0.015, "ATTACK": 0.020}


def health_state(health: dict[str, Any], p: RiskProfile = AGGRESSIVE_V5) -> tuple[str, dict[str, Any]]:
    recent = list(health.get("r") or [])[-p.health_window:]
    exp = sum(recent) / len(recent) if recent else 0.0
    dd = float(health.get("drawdown") or 0.0)
    info = {"recent_expectancy_r": round(exp, 4), "recent_trades": len(recent), "drawdown_at_entry": round(dd, 4)}
    if dd >= p.halt_drawdown:
        return "HALTED", info
    if dd >= p.attack_max_drawdown or (len(recent) >= 10 and exp < p.attack_min_expectancy_r):
        return "NO_ATTACK", info
    return "OK", info


class SizingV5:
    """Every CONTROL and every twin enters at TAKE (1%); only Jev's STRONGLY SUPPORT re-sizes to ATTACK, later,
    through the RiskManager. A halted book takes nothing. A risk-sized order below the exchange minimum is refused by
    the RiskManager (never enlarged) and counted as MIN_NOTIONAL_LIMITED."""

    def __init__(self, profile: RiskProfile = AGGRESSIVE_V5):
        self.profile = profile
        self.probes: Any = None

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        state, info = health_state(health, self.profile)
        base = {**info, "health": state, "tier": "TAKE", "target_risk_pct": TIER_RISK["TAKE"]}
        if state == "HALTED":
            return 0.0, {**base, "tier": "HALTED", "target_risk_pct": 0.0}
        return 1.0, base


# ---- identity ---------------------------------------------------------------------------------------------------

ROLE_TAG = {"CONTROL": "CONTROL", "JEV": "JEV5", "TAKE": "TAKE", "RANDOM": "RAND", "CAPACITY": "CAP"}


@dataclass(frozen=True)
class V5Identity:
    strategy_id: str
    coin: str
    horizon: str                          # HOURLY / SWING
    role: str = "CONTROL"
    seed: int = 0
    balance: float = 20.0
    time_stop_h: float = 0.0              # 0 = the class baseline (24 h / 72 h)
    target_r: float = 0.0                 # 0 = no target
    max_leverage: int = 20
    profile: str = "AGGRESSIVE_V5"
    jev_policy: str = ""
    strategy_version: str = "v5"

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"

    @property
    def exit_tag(self) -> str:
        if not self.time_stop_h and not self.target_r:
            return ""
        return f"-X{self.time_stop_h:g}" + (f"T{self.target_r:g}" if self.target_r else "")

    @property
    def pair_id(self) -> str:
        return f"v5pair:{self.strategy_id}-{self.coin}-{HTAG[self.horizon]}{self.exit_tag}"

    @property
    def key(self) -> str:
        tag = ROLE_TAG[self.role]
        if self.role == "RANDOM":
            tag += str(self.seed)
        if self.role == "CAPACITY":
            tag += f"{self.balance:g}"
        return f"{self.strategy_id}-{self.coin}-{HTAG[self.horizon]}{self.exit_tag}-{tag}@{self.max_leverage}x"

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.update({"key": self.key, "symbol": self.symbol, "pair_id": self.pair_id, "timeframe": HTAG[self.horizon],
                  "execution_timeframe": "1m"})
        return d


# ---- the gates ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RawEdgeGate:
    min_trades: tuple[tuple[str, int], ...] = (("HOURLY", 100), ("SWING", 40))
    max_p_mean_le_0: float = 0.05
    min_positive_subperiods: int = 2
    min_coin_share: float = 0.5
    coin_min_trades: int = 10
    drop_top_share: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["min_trades"] = dict(self.min_trades)
        return d


@dataclass(frozen=True)
class EconomicGate:
    max_p_mean_le_0: float = 0.10
    min_positive_subperiods: int = 2

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class V5Gates:
    min_trades: int = 30
    min_trades_per_day: tuple[tuple[str, float], ...] = (("HOURLY", 0.25), ("SWING", 1.0 / 7.0))
    min_profit_factor: float = 1.10
    max_p_mean_le_0: float = 0.10
    max_drawdown_pct: float = 0.30
    max_liquidations: int = 0
    max_top3_share: float = 0.60
    max_skip_rate: float = 0.80
    random_percentile: float = 0.90
    min_delta_vs_control: float = 0.50
    min_auc_low: float = 0.50
    max_error_rate: float = 0.02
    catastrophic_drawdown: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["min_trades_per_day"] = dict(self.min_trades_per_day)
        return d


@dataclass(frozen=True)
class V5Config:
    protocol: str = PROTOCOL_VERSION
    dataset_role: str = "DEVELOPMENT"
    months: tuple[str, ...] = DEV.months
    trade_from: str = DEV.trade_from
    trade_to: str = DEV.trade_to
    subperiods: tuple[tuple[str, str], ...] = DEV.subperiods
    coins: tuple[str, ...] = ()
    horizons: tuple[str, ...] = HORIZONS
    starting_balance: float = 20.0
    capacity_balances: tuple[float, ...] = (50.0, 100.0)
    raw_edge_balance: float = 100.0          # Amendment 1: the family raw-edge gate reads the 100 USDT twins
    leverage_ceiling: int = 20
    fees: FeeSchedule = BYBIT_LINEAR
    execution: ExecutionConfig = ExecutionConfig()
    max_fee_share_of_r: float = 0.25
    min_notional_safety_multiplier: float = 1.0
    leverage_policy: str = "needed"
    seed: int = 7
    random_seeds: int = 20
    raw_edge: RawEdgeGate = RawEdgeGate()
    economic: EconomicGate = EconomicGate()
    gates: V5Gates = V5Gates()
    exit_time_stops_h: tuple[int, ...] = EXIT_TIME_STOPS_H
    exit_time_stops_by_class: tuple[tuple[str, tuple[int, ...]], ...] = EXIT_TIME_STOPS_BY_CLASS
    exit_min_improvement_r: float = EXIT_MIN_IMPROVEMENT_R
    exit_targets_r: tuple[float | None, ...] = EXIT_TARGETS_R
    mfe_horizons_h: tuple[int, ...] = MFE_HORIZONS_H
    strategy_fingerprints: tuple[tuple[str, str], ...] = ()
    jev_fingerprints: tuple[tuple[str, str], ...] = ()
    universe_fingerprint: str = ""
    universe_rule: str = ""
    venue: str = VENUE
    price_tape: str = PRICE_TAPE
    rules_source: str = ""
    dataset_fingerprint: str = ""

    @property
    def risk(self) -> RiskProfile:
        return dataclasses.replace(AGGRESSIVE_V5, starting_balance=float(self.starting_balance),
                                   max_leverage=int(self.leverage_ceiling))

    @property
    def days(self) -> float:
        a, b = dt.date.fromisoformat(self.trade_from), dt.date.fromisoformat(self.trade_to)
        return float((b - a).days + 1)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["fees"] = self.fees.to_dict()
        d["strategy_fingerprints"] = dict(self.strategy_fingerprints)
        d["jev_fingerprints"] = dict(self.jev_fingerprints)
        d["risk_profile"] = self.risk.to_dict()
        d["tier_risk"] = dict(TIER_RISK)
        d["raw_edge"] = self.raw_edge.to_dict()
        d["gates"] = self.gates.to_dict()
        return d

    def fingerprint(self, ignore_dataset: bool = False) -> str:
        d = self.to_dict()
        if ignore_dataset:
            d.pop("dataset_fingerprint", None)
            d.pop("rules_source", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def config_for(window: Window, universe: dict[str, Any], **k: Any) -> V5Config:
    return V5Config(dataset_role=window.role, months=window.months, trade_from=window.trade_from,
                    trade_to=window.trade_to, subperiods=window.subperiods, coins=tuple(universe["coins"]),
                    universe_fingerprint=universe["fingerprint"],
                    universe_rule=str((universe["snapshot"] or {}).get("rule_fingerprint") or ""), **k)
