"""V3.1 AGGRESSIVE EDGE: identity, risk profile, sizing and the pre-registered settings.

Fixed by docs/V31_PROTOCOL.md before any V3.1 bot was replayed. V3 (run v3-e39e94890b, AGGRESSIVE_V3,
JEV_POLICY_V2) is frozen and untouched; V3.1 has its own profile, policy and tables.

Pipeline of every V3.1 bot:

    candidate -> EDGE GATE (expected net edge, deterministic) -> sizing (TAKE size) -> legality
              -> [Jev V3: SKIP / TAKE / ATTACK] -> ATTACK re-size through the RiskManager -> execution

Sizing (AGGRESSIVE_V31): TAKE 1.0%, ATTACK 1.5%, exceptional ATTACK 2.0% of equity at risk, 20x
leverage ceiling. There is no DEFENSIVE size any more: at 20 USDT a half-size order is often below
the exchange minimum (V3's DEFENSIVE acted as a hidden veto). A bot that is unhealthy simply cannot
ATTACK; a bot 30% below its peak stops trading.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from app.competition.bots import RiskProfile
from app.execution.config import BYBIT_LINEAR, ExecutionConfig, FeeSchedule

PROTOCOL_VERSION = "V31_AGGRESSIVE_EDGE_PROTOCOL_V1"
VENUE = "BYBIT_LINEAR"
PRICE_TAPE = "BINANCE_USDM_1M"
COINS: tuple[str, ...] = ("ZEC", "UNI", "HYPE", "SUI", "ARB", "TAO", "PENGU", "ENA", "AAVE", "ZRO")
TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m", "30m")
BENCHMARK_TIMEFRAMES: tuple[str, ...] = ("30m",)
DEV_MONTHS: tuple[str, ...] = ("2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04")
OBSERVE_FROM = "2025-10-11"         # RAW observation (edge-model evidence) starts after indicator warmup
DEV_FROM = "2025-11-01"
DEV_TO = "2026-04-30"
TEST_MONTHS: tuple[str, ...] = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08")   # April = warmup only
TEST_FROM = "2026-05-01"
TEST_TO = "2026-08-31"

# PARTICIPATION gate: executed trades per day (configurable, frozen before TEST)
PARTICIPATION_PER_DAY: dict[str, float] = {"3m": 2.0, "5m": 1.5, "15m": 0.5, "30m": 0.25}
MIN_SAMPLE = 30                      # adequate sample: never judge a bot on fewer closed trades


def min_trades(tf: str, days: float) -> int:
    return max(MIN_SAMPLE, int(math.ceil(PARTICIPATION_PER_DAY[tf] * days)))


# ---- risk profile -------------------------------------------------------------------------------------

AGGRESSIVE_V31 = RiskProfile(
    name="AGGRESSIVE_V31", starting_balance=20.0, max_leverage=20,  # type: ignore[arg-type]
    ordinary_risk_pct=0.010, strong_risk_pct=0.015, exceptional_risk_pct=0.020, max_risk_pct=0.020,
    attack_multiplier=1.5, defensive_multiplier=1.0, health_window=20, attack_min_trades=0,
    attack_min_expectancy_r=-0.25, attack_max_drawdown=0.18, defensive_expectancy_r=-9.0,
    defensive_drawdown=1.0, halt_drawdown=0.30, strong_quality=0.60, exceptional_quality=0.85,
    attack_min_edge_to_cost=2.0)

TIER_RISK = {"TAKE": 0.010, "ATTACK": 0.015, "STRONG_ATTACK": 0.020}


def health_state(health: dict[str, Any], p: RiskProfile = AGGRESSIVE_V31) -> tuple[str, dict[str, Any]]:
    """HALTED (>= 30% below peak) / NO_ATTACK (>= 18% below peak, or recent expectancy < -0.25R over
    the last 20 closed trades) / OK. Never a size cut: an order is either full TAKE size or none."""
    recent = list(health.get("r") or [])[-p.health_window:]
    exp = sum(recent) / len(recent) if recent else 0.0
    dd = float(health.get("drawdown") or 0.0)
    info = {"recent_expectancy_r": round(exp, 4), "recent_trades": len(recent), "drawdown_at_entry": round(dd, 4)}
    if dd >= p.halt_drawdown:
        return "HALTED", info
    if dd >= p.attack_max_drawdown or (len(recent) >= 10 and exp < p.attack_min_expectancy_r):
        return "NO_ATTACK", info
    return "OK", info


def attack_tier(edge: dict[str, Any] | None, state: str, want: str = "ATTACK") -> tuple[str, str]:
    """The deterministic ATTACK rule every mode shares (CONTROL, JEV, RANDOM): ATTACK needs the edge
    model's ATTACK eligibility (lower bound, headroom, quality) and a healthy bot; STRONG_ATTACK needs
    the edge model's EXCEPTIONAL flag too. Anything else is TAKE. Returns (tier, reason)."""
    if want == "TAKE":
        return "TAKE", ""
    e = edge or {}
    if state != "OK":
        return "TAKE", f"ATTACK -> TAKE: bot health {state}"
    if not e.get("attack_eligible"):
        return "TAKE", "ATTACK -> TAKE: edge model does not support ATTACK (" + str(e.get("attack_block") or "not eligible") + ")"
    if want == "STRONG_ATTACK" and e.get("exceptional"):
        return "STRONG_ATTACK", ""
    return "ATTACK", ""


class SizingV31:
    """ReplayEngine sizing hook (runs AFTER the edge gate, BEFORE legality and Jev).

    CONTROL  deterministic: ATTACK (1.5%) when the edge model marks the setup ATTACK-eligible and the
             bot is healthy, STRONG_ATTACK (2.0%) when it is also EXCEPTIONAL; TAKE (1.0%) otherwise.
    GATED    (JEV / TAKE / RANDOM twins and the RAW observer): always TAKE size here, so legality is
             checked at the size every candidate would at least trade at. The gate may then ask for
             ATTACK, which goes through `attack_tier` and back through the RiskManager.
    """

    def __init__(self, profile: RiskProfile = AGGRESSIVE_V31, mode: str = "CONTROL"):
        self.profile = profile
        self.mode = mode

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        p = self.profile
        state, info = health_state(health, p)
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
        base = {**info, "health": state, "signal_quality": meta.get("signal_quality"),
                "edge_net_r": edge.get("net_r"), "edge_lower_r": edge.get("lower_r")}
        if state == "HALTED" and self.mode != "RAW":
            return 0.0, {**base, "tier": "HALTED", "target_risk_pct": 0.0}
        tier, why = ("TAKE", "")
        if self.mode == "CONTROL":
            tier, why = attack_tier(edge, state, "STRONG_ATTACK" if edge.get("exceptional") else "ATTACK")
            if why and edge.get("attack_eligible") is not True:
                tier, why = "TAKE", ""           # an ordinary setup is simply TAKE, not a downgrade
        risk = min(TIER_RISK[tier], p.max_risk_pct)
        return risk / p.ordinary_risk_pct, {**base, "tier": tier, "target_risk_pct": risk,
                                            "attack_downgrade": why or None}


# ---- identity -------------------------------------------------------------------------------------------

ROLE_TAG = {"CONTROL": "CONTROL", "JEV": "JEV3", "TAKE": "TAKE", "RANDOM": "RAND", "RAW": "RAW", "CAPACITY": "CAP"}


@dataclass(frozen=True)
class V31Identity:
    """One V3.1 competitor. Everything that can change a result is part of it."""
    strategy_id: str
    coin: str
    timeframe: str
    role: str = "CONTROL"                 # CONTROL / JEV / TAKE / RANDOM / RAW / CAPACITY
    seed: int = 0                          # RANDOM twins only
    balance: float = 20.0                  # CAPACITY diagnostics run 50 / 100 USDT
    max_leverage: int = 20
    profile: str = "AGGRESSIVE_V31"
    jev_policy: str = ""                   # "JEV_POLICY_V3" for JEV / TAKE / RANDOM twins
    strategy_version: str = "v3.1"

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"

    @property
    def context_tfs(self) -> tuple[str, str]:
        from app.strategies.v3.base import CONTEXT
        return CONTEXT[self.timeframe]

    @property
    def pair_id(self) -> str:
        return f"v31pair:{self.strategy_id}-{self.coin}-{self.timeframe}"

    @property
    def key(self) -> str:
        tag = ROLE_TAG[self.role]
        if self.role == "RANDOM":
            tag += str(self.seed)
        if self.role == "CAPACITY":
            tag += f"{self.balance:g}"
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


# ---- the expected-net-edge model and gate (docs/V31_PROTOCOL.md, "EDGE GATE") ---------------------------

@dataclass(frozen=True)
class EdgeConfig:
    shrinkage_k: float = 30.0            # pseudo-observations pulling each bucket toward its parent
    min_family_n: int = 30               # fewer resolved observations for the family = INSUFFICIENT_EVIDENCE
    take_margin_r: float = 0.05          # TAKE needs predicted net expectancy >= +0.05 R
    attack_lower_r: float = 0.10         # ATTACK needs (predicted net - 1 se) >= +0.10 R ...
    attack_headroom_ratio: float = 2.0   # ... expected gross edge >= 2x its own execution cost ...
    attack_min_quality: float = 0.60     # ... and signal quality >= 0.60
    exceptional_lower_r: float = 0.20    # STRONG_ATTACK: lower bound >= +0.20 R and quality >= 0.85
    exceptional_quality: float = 0.85
    quality_bands: tuple[float, float] = (0.40, 0.60)   # LOW < 0.40 <= MID < 0.60 <= HIGH
    evidence: str = "RAW observation ledger (every legal candidate, simulated at TAKE size, non-overlapping)"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---- the maker-first experiment -------------------------------------------------------------------------

@dataclass(frozen=True)
class MakerConfig:
    wait_minutes: tuple[int, ...] = (5, 15)
    convert_within_stop_frac: float = 0.25       # unfilled -> taker only if price is within 0.25 x stop of the limit
    conservative_through_ticks: int = 1          # conservative: the tape must trade THROUGH the limit
    conservative_through_bps: float = 1.0        # ... by at least max(1 tick, 1 bp)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---- qualification ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class V31Gates:
    min_net_profit: float = 0.0
    min_expectancy_r: float = 0.0
    min_profit_factor: float = 1.10
    max_drawdown_pct: float = 0.30
    max_liquidations: int = 0
    positive_without_top3: bool = True
    max_top3_share: float = 0.60                 # top 3 trades may not be more than 60% of gross profit
    max_skip_rate: float = 0.80
    random_percentile: float = 0.90
    min_delta_vs_control: float = 0.50           # "meaningfully better": +0.50 USDT (2.5% of the book)
    min_auc_low: float = 0.50
    max_error_rate: float = 0.02
    max_latency_p95_ms: float = 2000.0
    catastrophic_drawdown: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class V31Config:
    protocol: str = PROTOCOL_VERSION
    dataset_role: str = "DEVELOPMENT"
    months: tuple[str, ...] = DEV_MONTHS
    observe_from: str = OBSERVE_FROM
    trade_from: str = DEV_FROM               # entries only from here
    trade_to: str = DEV_TO
    coins: tuple[str, ...] = COINS
    timeframes: tuple[str, ...] = TIMEFRAMES
    starting_balance: float = 20.0
    capacity_balances: tuple[float, ...] = (50.0, 100.0)
    leverage_ceiling: int = 20
    fees: FeeSchedule = BYBIT_LINEAR
    execution: ExecutionConfig = ExecutionConfig()
    max_fee_share_of_r: float = 0.25
    min_notional_safety_multiplier: float = 1.0
    leverage_policy: str = "needed"
    edge: EdgeConfig = EdgeConfig()
    maker: MakerConfig = MakerConfig()
    field_min_pairs: int = 20
    field_target_pairs: int = 30
    field_coins_per_slot: int = 3
    seed: int = 7
    random_seeds: int = 20
    permutations: int = 2000
    gates: V31Gates = V31Gates()
    strategy_fingerprints: tuple[tuple[str, str], ...] = ()
    jev_fingerprints: tuple[tuple[str, str], ...] = ()
    edge_model_fingerprint: str = ""         # TEST: the frozen DEVELOPMENT evidence the gate uses
    venue: str = VENUE
    price_tape: str = PRICE_TAPE
    rules_source: str = ""
    dataset_fingerprint: str = ""

    @property
    def risk(self) -> RiskProfile:
        return dataclasses.replace(AGGRESSIVE_V31, starting_balance=float(self.starting_balance),
                                   max_leverage=int(self.leverage_ceiling))

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
        d["tier_risk"] = dict(TIER_RISK)
        d["participation_per_day"] = dict(PARTICIPATION_PER_DAY)
        d["participation_min_trades"] = {tf: min_trades(tf, self.days) for tf in self.timeframes}
        return d

    def fingerprint(self, ignore_dataset: bool = False) -> str:
        """`ignore_dataset`: the TEST configuration is pre-registered BEFORE its months are downloaded,
        so its registered fingerprint cannot contain the dataset's (which the run then records)."""
        d = self.to_dict()
        if ignore_dataset:
            d.pop("dataset_fingerprint", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]
