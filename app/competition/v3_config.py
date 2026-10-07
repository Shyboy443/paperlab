"""V3 AGGRESSIVE INTRADAY JEV ARENA: identity, risk profile, sizing and the pre-registered settings.

Everything here is fixed by docs/V3_PROTOCOL.md before any V3 result existed. V1/V2 profiles and
policies are untouched: AGGRESSIVE (V2) keeps its own tier table, V3 gets AGGRESSIVE_V3.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.competition.bots import AttackPolicy, RiskProfile, attack_state, quality_tier
from app.execution.config import BYBIT_LINEAR, ExecutionConfig, FeeSchedule

PROTOCOL_VERSION = "V3_AGGRESSIVE_PROTOCOL_V1"
VENUE = "BYBIT_LINEAR"
PRICE_TAPE = "BINANCE_USDM_1M"
COINS: tuple[str, ...] = ("ZEC", "UNI", "HYPE", "SUI", "ARB", "TAO", "PENGU", "ENA", "AAVE", "ZRO")
SYMBOLS: tuple[str, ...] = tuple(f"{c}USDT" for c in COINS)
DEV_MONTHS: tuple[str, ...] = ("2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04")
DEV_FROM = "2025-11-01"
DEV_TO = "2026-04-30"
TEST_FROM = "2026-05-01"
TEST_TO = "2026-08-31"
FIELD_TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m", "30m")
SCAN_TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m")
EXPERIMENTAL_TIMEFRAMES: tuple[str, ...] = ("1m",)

# Participation (docs/V3_PROTOCOL.md): minimum closed trades = max(absolute, per-30-days x days/30).
PARTICIPATION_ABS: dict[str, int] = {"1m": 150, "3m": 100, "5m": 60, "15m": 30, "30m": 20}
PARTICIPATION_PER_30D: dict[str, float] = {"1m": 75.0, "3m": 50.0, "5m": 30.0, "15m": 15.0, "30m": 10.0}


def min_trades(tf: str, days: float) -> int:
    import math
    return max(PARTICIPATION_ABS[tf], int(math.ceil(PARTICIPATION_PER_30D[tf] * days / 30.0)))


# ---- risk profile -------------------------------------------------------------------------------------

AGGRESSIVE_V3 = RiskProfile(
    name="AGGRESSIVE_V3", starting_balance=20.0, max_leverage=20,  # type: ignore[arg-type]
    ordinary_risk_pct=0.010, strong_risk_pct=0.015, exceptional_risk_pct=0.020, max_risk_pct=0.020,
    attack_multiplier=1.5, defensive_multiplier=0.5, health_window=20, attack_min_trades=10,
    attack_min_expectancy_r=0.10, attack_max_drawdown=0.10, defensive_expectancy_r=-0.25,
    defensive_drawdown=0.18, halt_drawdown=0.30, strong_quality=0.70, exceptional_quality=0.85,
    attack_min_edge_to_cost=3.0)


def risk_for_v3(profile: RiskProfile, state: str, quality: str) -> float:
    """AGGRESSIVE_V3 tier table: a STRONG setup gets 1.5% even without ATTACK health; 2.0% needs an
    EXCEPTIONAL setup AND a healthy bot. DEFENSIVE halves, HALTED is zero, nothing exceeds the cap."""
    if state == "HALTED":
        return 0.0
    base = profile.ordinary_risk_pct
    if state == "DEFENSIVE":
        risk = base * profile.defensive_multiplier
    elif state == "ATTACK" and quality == "EXCEPTIONAL":
        risk = profile.exceptional_risk_pct
    elif quality in ("STRONG", "EXCEPTIONAL"):
        risk = profile.strong_risk_pct
    else:
        risk = base
    return min(risk, profile.max_risk_pct)


class AttackPolicyV3(AttackPolicy):
    """ReplayEngine sizing hook for AGGRESSIVE_V3.

    CONTROL mode applies the tier table above. JEV mode applies ONLY the bot-health factor (DEFENSIVE
    0.5, HALTED 0): the +JEV twin's size decision belongs to Jev V2, whose multiplier the engine
    applies on top, under the same 2% hard cap and back through the RiskManager."""

    def __init__(self, profile: RiskProfile = AGGRESSIVE_V3, jev_mode: bool = False):
        super().__init__(profile)
        self.jev_mode = jev_mode

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        p = self.profile
        recent = list(health.get("r") or [])[-p.health_window:]
        exp = sum(recent) / len(recent) if recent else 0.0
        dd = float(health.get("drawdown") or 0.0)
        meta = getattr(sig, "meta", None) or {}
        e2c = meta.get("edge_to_cost")
        if self.jev_mode:
            state = attack_state(exp, dd, p, trades=len(recent), quality="ORDINARY")
            factor = 0.0 if state == "HALTED" else p.defensive_multiplier if state == "DEFENSIVE" else 1.0
            return factor, {"attack_state": state, "quality": "JEV", "target_risk_pct": p.ordinary_risk_pct * factor,
                            "signal_quality": meta.get("signal_quality"), "edge_to_cost": e2c,
                            "recent_expectancy_r": round(exp, 4), "recent_trades": len(recent),
                            "drawdown_at_entry": round(dd, 4), "health_factor": factor}
        quality = quality_tier(sig, p)
        cost_ok = isinstance(e2c, (int, float)) and e2c >= p.attack_min_edge_to_cost
        if quality != "ORDINARY" and not cost_ok:
            quality = "ORDINARY"              # no size-up without edge-to-cost >= 3
        state = attack_state(exp, dd, p, trades=len(recent), quality=quality)
        risk = risk_for_v3(p, state, quality)
        mult = risk / p.ordinary_risk_pct if p.ordinary_risk_pct > 0 else 0.0
        return mult, {"attack_state": state, "quality": quality, "target_risk_pct": risk,
                      "signal_quality": meta.get("signal_quality"), "edge_to_cost": e2c,
                      "recent_expectancy_r": round(exp, 4), "recent_trades": len(recent),
                      "drawdown_at_entry": round(dd, 4)}


# ---- identity -------------------------------------------------------------------------------------------

ROLE_TAG = {"CONTROL": "CONTROL", "JEV": "JEV2", "TAKE": "TAKE", "RANDOM": "RAND"}


@dataclass(frozen=True)
class V3Identity:
    """One V3 competitor. Everything that can change a result is part of it."""
    strategy_id: str
    coin: str
    timeframe: str
    role: str = "CONTROL"                 # CONTROL / JEV / TAKE / RANDOM
    seed: int = 0                          # RANDOM twins only
    max_leverage: int = 20
    profile: str = "AGGRESSIVE_V3"
    jev_policy: str = ""                   # "JEV_POLICY_V2" for JEV/TAKE/RANDOM twins
    strategy_version: str = "v3.0"

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"

    @property
    def context_tfs(self) -> tuple[str, str]:
        from app.strategies.v3.base import CONTEXT
        return CONTEXT[self.timeframe]

    @property
    def pair_id(self) -> str:
        return f"v3pair:{self.strategy_id}-{self.coin}-{self.timeframe}"

    @property
    def key(self) -> str:
        tag = ROLE_TAG[self.role] + (str(self.seed) if self.role == "RANDOM" else "")
        return f"{self.strategy_id}-{self.coin}-{self.timeframe}-{tag}@{self.max_leverage}x"

    def fingerprint(self, params: Any = None, sources: dict[str, str] | None = None) -> str:
        values: dict[str, Any] = {}
        if params is not None:
            try:
                values = {f.name: getattr(params, f.name) for f in dataclasses.fields(params)}
            except TypeError:
                values = {}
        blob = json.dumps({**dataclasses.asdict(self), "context": list(self.context_tfs), "execution_tf": "1m",
                           "params": values, "sources": sources or {}}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.update({"key": self.key, "symbol": self.symbol, "pair_id": self.pair_id,
                  "context_timeframes": list(self.context_tfs), "execution_timeframe": "1m"})
        return d


# ---- the run configuration ------------------------------------------------------------------------------

@dataclass(frozen=True)
class V3Gates:
    min_net_profit: float = 0.0
    min_expectancy_r: float = 0.0
    min_profit_factor: float = 1.10
    max_drawdown_pct: float = 0.30
    max_liquidations: int = 0
    positive_without_top3: bool = True
    max_skip_rate: float = 0.80
    random_percentile: float = 0.90
    min_auc_low: float = 0.50
    max_error_rate: float = 0.02
    max_latency_p95_ms: float = 2000.0
    catastrophic_drawdown: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class V3Config:
    protocol: str = PROTOCOL_VERSION
    dataset_role: str = "DEVELOPMENT"
    months: tuple[str, ...] = DEV_MONTHS
    trade_from: str = DEV_FROM              # entries only from here (the first month is warmup)
    trade_to: str = DEV_TO
    coins: tuple[str, ...] = COINS
    scan_timeframes: tuple[str, ...] = SCAN_TIMEFRAMES
    field_timeframes: tuple[str, ...] = FIELD_TIMEFRAMES
    starting_balance: float = 20.0
    leverage_ceiling: int = 20
    fees: FeeSchedule = BYBIT_LINEAR
    execution: ExecutionConfig = ExecutionConfig()
    cost_gate_min_ratio: float = 2.0
    attack_min_edge_to_cost: float = 3.0
    max_fee_share_of_r: float = 0.25
    min_notional_safety_multiplier: float = 1.0
    leverage_policy: str = "needed"
    seed: int = 7
    random_seeds: int = 20
    permutations: int = 2000
    min_active_jev_bots: int = 10
    gates: V3Gates = V3Gates()
    strategy_fingerprints: tuple[tuple[str, str], ...] = ()
    jev_fingerprints: tuple[tuple[str, str], ...] = ()
    venue: str = VENUE
    price_tape: str = PRICE_TAPE
    rules_source: str = ""
    dataset_fingerprint: str = ""

    @property
    def risk(self) -> RiskProfile:
        return dataclasses.replace(AGGRESSIVE_V3, starting_balance=float(self.starting_balance),
                                   max_leverage=int(self.leverage_ceiling),
                                   attack_min_edge_to_cost=float(self.attack_min_edge_to_cost))

    @property
    def days(self) -> float:
        import datetime as dt
        a = dt.date.fromisoformat(self.trade_from)
        b = dt.date.fromisoformat(self.trade_to)
        return float((b - a).days + 1)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["fees"] = self.fees.to_dict()
        d["strategy_fingerprints"] = dict(self.strategy_fingerprints)
        d["jev_fingerprints"] = dict(self.jev_fingerprints)
        d["risk_profile"] = self.risk.to_dict()
        d["participation"] = {tf: min_trades(tf, self.days) for tf in self.scan_timeframes}
        return d

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, default=str).encode()).hexdigest()[:16]
