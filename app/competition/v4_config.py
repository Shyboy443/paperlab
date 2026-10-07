"""V4 INTRADAY SPECIALISTS: identity, windows, universe, sizing and the pre-registered gates.

Fixed by docs/V4_PROTOCOL.md before any V4 bot was replayed. V1, V2, V3 and V3.1 are frozen and untouched;
V4 has its own strategies (app/strategies/v4), expected-edge model (v4_edge), Jev policy (JEV_POLICY_V4),
tables (v4_runs / v4_bots) and documents.

Pipeline of every V4 bot:

    setup -> legality + cost (RiskManager; the fee gate needs round trip <= 25% of R)
          -> EXPECTED-EDGE gate (causal family x timeframe expectancy, v4_edge)
          -> sizing (TAKE 1.0% / CONTROL's deterministic ATTACK 2.0%)
          -> [Jev V4: CONTRADICT -> SKIP, SUPPORT -> TAKE, STRONGLY SUPPORT -> ATTACK] -> execution

Timeframes and the research allocation (a share of bots, never a qualification bias): 3m 15%, 5m 20%,
15m 35%, 30m 30% -- 7 families x (3 + 4 + 7 + 6) coins = 140 CONTROL bots, faster timeframes on the best
tradeability scores (they pay the costs more often). The coin list is NOT fixed: the frozen tradeability
rule picks it per window from the 30 days before the window (docs/V4_UNIVERSE_*.json).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.competition.bots import RiskProfile
from app.execution.config import BYBIT_LINEAR, ExecutionConfig, FeeSchedule

PROTOCOL_VERSION = "V4_INTRADAY_SPECIALIST_PROTOCOL_V1"
VENUE = "BYBIT_LINEAR"
PRICE_TAPE = "BINANCE_USDM_1M"
TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m", "30m")
COINS_PER_TF: dict[str, int] = {"3m": 3, "5m": 4, "15m": 7, "30m": 6}     # 15% / 20% / 35% / 30% of 140
DOCS = Path(__file__).resolve().parents[2] / "docs"


@dataclass(frozen=True)
class Window:
    role: str                   # DEVELOPMENT / TEST
    months: tuple[str, ...]     # tape months, the first is warm-up only
    observe_from: str           # RAW observation (edge-model evidence) starts after indicator warm-up
    trade_from: str
    trade_to: str
    universe_file: str

    @property
    def days(self) -> float:
        a, b = dt.date.fromisoformat(self.trade_from), dt.date.fromisoformat(self.trade_to)
        return float((b - a).days + 1)


DEV = Window("DEVELOPMENT", ("2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"),
             "2025-10-11", "2025-11-01", "2026-04-30", "V4_UNIVERSE_DEV.json")
# The holdout: six months no V3 / V3.1 / V4 design ever looked at (2026-05..08 was consumed by the V3.1 TEST,
# 2026-09 is not complete). Pre-registered in docs/V4_TEST_PREREGISTRATION.json BEFORE any month is downloaded.
TEST = Window("TEST", ("2025-03", "2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09"),
              "2025-03-11", "2025-04-01", "2025-09-30", "V4_UNIVERSE_TEST.json")
WINDOWS = {"DEVELOPMENT": DEV, "TEST": TEST}

PARTICIPATION_PER_DAY: dict[str, float] = {"3m": 0.5, "5m": 0.5, "15m": 0.4, "30m": 0.25}
MIN_SAMPLE = 30


def min_trades(tf: str, days: float) -> int:
    return max(MIN_SAMPLE, int(math.ceil(PARTICIPATION_PER_DAY[tf] * days)))


def load_universe(window: Window, docs: Path = DOCS) -> dict[str, Any]:
    """The window's tradeability snapshot and the coins each timeframe trades (a prefix of the ranking)."""
    snap = json.loads((docs / window.universe_file).read_text(encoding="utf-8"))
    coins = [s[:-4] if s.endswith("USDT") else s for s in snap["universe"]]
    per_tf = {tf: coins[:n] for tf, n in COINS_PER_TF.items()}
    blob = json.dumps({"universe": snap["universe"], "rule": snap.get("rule_fingerprint"),
                       "scoring": snap.get("scoring_period")}, sort_keys=True)
    return {"coins": coins, "per_tf": per_tf, "snapshot": snap,
            "fingerprint": hashlib.sha256(blob.encode()).hexdigest()[:12]}


# ---- risk ----------------------------------------------------------------------------------------------

AGGRESSIVE_V4 = RiskProfile(
    name="AGGRESSIVE_V4", starting_balance=20.0, max_leverage=20,  # type: ignore[arg-type]
    ordinary_risk_pct=0.010, strong_risk_pct=0.020, exceptional_risk_pct=0.020, max_risk_pct=0.020,
    attack_multiplier=2.0, defensive_multiplier=1.0, health_window=20, attack_min_trades=0,
    attack_min_expectancy_r=-0.25, attack_max_drawdown=0.18, defensive_expectancy_r=-9.0,
    defensive_drawdown=1.0, halt_drawdown=0.30, strong_quality=0.60, exceptional_quality=0.85,
    attack_min_edge_to_cost=2.0)

TIER_RISK = {"TAKE": 0.010, "ATTACK": 0.020}


def health_state(health: dict[str, Any], p: RiskProfile = AGGRESSIVE_V4) -> tuple[str, dict[str, Any]]:
    """HALTED (>= 30% below peak) / NO_ATTACK (>= 18% below peak, or the last 20 trades' expectancy
    < -0.25R) / OK. A size is never cut: an order is TAKE, ATTACK or nothing."""
    recent = list(health.get("r") or [])[-p.health_window:]
    exp = sum(recent) / len(recent) if recent else 0.0
    dd = float(health.get("drawdown") or 0.0)
    info = {"recent_expectancy_r": round(exp, 4), "recent_trades": len(recent), "drawdown_at_entry": round(dd, 4)}
    if dd >= p.halt_drawdown:
        return "HALTED", info
    if dd >= p.attack_max_drawdown or (len(recent) >= 10 and exp < p.attack_min_expectancy_r):
        return "NO_ATTACK", info
    return "OK", info


class SizingV4:
    """ReplayEngine sizing hook (after the edge gate, before legality and Jev).

    CONTROL  deterministic: ATTACK (2.0%) when the edge model marks the setup ATTACK-eligible (its lower
             bound >= +0.05R) and the bot is healthy; TAKE (1.0%) otherwise.
    GATED    (JEV / TAKE / RANDOM twins, RAW observer): TAKE size here, so legality is checked at the size
             every candidate would at least trade at; an ATTACK is re-sized later through the RiskManager.
    """

    def __init__(self, profile: RiskProfile = AGGRESSIVE_V4, mode: str = "CONTROL"):
        self.profile = profile
        self.mode = mode
        self.probes: Any = None

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        p = self.profile
        state, info = health_state(health, p)
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
        base = {**info, "health": state, "signal_quality": meta.get("signal_quality"),
                "edge_net_r": edge.get("net_r"), "edge_lower_r": edge.get("lower_r"),
                "edge_candidate_id": meta.get("edge_candidate_id"), "regime": meta.get("regime"),
                "vol_band": meta.get("vol_band")}
        if state == "HALTED" and self.mode != "RAW":
            return 0.0, {**base, "tier": "HALTED", "target_risk_pct": 0.0}
        tier, why = "TAKE", None
        if self.mode == "CONTROL" and edge.get("attack_eligible") and state == "OK":
            tier = "ATTACK"
        mult = TIER_RISK[tier] / p.ordinary_risk_pct
        if mult > 1.0 and self.probes is not None:
            ok, reason = self.probes.resize(sig, mult)
            if not ok:
                tier, mult, why = "TAKE", 1.0, f"ATTACK_NOT_LEGAL:{reason}"
        return mult, {**base, "tier": tier, "target_risk_pct": TIER_RISK[tier], "attack_downgrade": why}


# ---- identity -------------------------------------------------------------------------------------------

ROLE_TAG = {"CONTROL": "CONTROL", "JEV": "JEV4", "TAKE": "TAKE", "RANDOM": "RAND", "RAW": "RAW", "CAPACITY": "CAP"}


@dataclass(frozen=True)
class V4Identity:
    strategy_id: str
    coin: str
    timeframe: str
    role: str = "CONTROL"                 # CONTROL / JEV / TAKE / RANDOM / RAW / CAPACITY
    seed: int = 0
    balance: float = 20.0                  # CAPACITY diagnostics: 50 / 100 USDT
    max_leverage: int = 20
    profile: str = "AGGRESSIVE_V4"
    jev_policy: str = ""
    strategy_version: str = "v4"

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"

    @property
    def context_tfs(self) -> tuple[str, str]:
        from app.strategies.v4.base import STRUCTURE, TREND_TF
        return STRUCTURE[self.timeframe], TREND_TF

    @property
    def pair_id(self) -> str:
        return f"v4pair:{self.strategy_id}-{self.coin}-{self.timeframe}"

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


# ---- the expected-edge model (v4_edge) --------------------------------------------------------------------

@dataclass(frozen=True)
class EdgeConfigV4:
    shrinkage_k: float = 30.0         # pseudo-observations of ZERO edge added to every estimate
    window: int = 300                 # the most recent resolved observations of the family x timeframe
    min_n: int = 30                   # fewer = INSUFFICIENT_EVIDENCE (refused)
    take_margin_r: float = 0.02       # TAKE needs shrunk expected net R >= +0.02
    attack_lower_r: float = 0.05      # CONTROL's ATTACK needs (expected net R - 1 se) >= +0.05
    evidence: str = "RAW observation ledger of the SAME window, causal (only outcomes that exited before t)"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---- qualification ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class V4Gates:
    min_net_profit: float = 0.0
    min_expectancy_r: float = 0.0
    min_gross_expectancy_r: float = 0.0          # raw edge BEFORE costs must be positive (root cause #1)
    min_profit_factor: float = 1.10
    max_drawdown_pct: float = 0.30
    max_liquidations: int = 0
    positive_without_top3: bool = True
    max_top3_share: float = 0.60
    max_p_mean_le_0: float = 0.10                # bootstrap P(mean net R <= 0) must be <= 10%
    max_skip_rate: float = 0.80
    random_percentile: float = 0.90
    min_delta_vs_control: float = 0.50
    min_auc_low: float = 0.50
    max_error_rate: float = 0.02
    catastrophic_drawdown: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class V4Config:
    protocol: str = PROTOCOL_VERSION
    dataset_role: str = "DEVELOPMENT"
    months: tuple[str, ...] = DEV.months
    observe_from: str = DEV.observe_from
    trade_from: str = DEV.trade_from
    trade_to: str = DEV.trade_to
    coins_per_tf: tuple[tuple[str, tuple[str, ...]], ...] = ()
    timeframes: tuple[str, ...] = TIMEFRAMES
    starting_balance: float = 20.0
    capacity_balances: tuple[float, ...] = (50.0, 100.0)
    leverage_ceiling: int = 20
    fees: FeeSchedule = BYBIT_LINEAR
    execution: ExecutionConfig = ExecutionConfig()
    max_fee_share_of_r: float = 0.25
    min_notional_safety_multiplier: float = 1.0
    leverage_policy: str = "needed"
    edge: EdgeConfigV4 = EdgeConfigV4()
    field_coins_per_slot: int = 2
    seed: int = 7
    random_seeds: int = 20
    gates: V4Gates = V4Gates()
    strategy_fingerprints: tuple[tuple[str, str], ...] = ()
    jev_fingerprints: tuple[tuple[str, str], ...] = ()
    universe_fingerprint: str = ""
    tradeability_rule: str = ""
    venue: str = VENUE
    price_tape: str = PRICE_TAPE
    rules_source: str = ""
    dataset_fingerprint: str = ""

    @property
    def coins(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c for _, cs in self.coins_per_tf for c in cs))

    def coins_for(self, tf: str) -> tuple[str, ...]:
        return dict(self.coins_per_tf).get(tf, ())

    @property
    def risk(self) -> RiskProfile:
        return dataclasses.replace(AGGRESSIVE_V4, starting_balance=float(self.starting_balance),
                                   max_leverage=int(self.leverage_ceiling))

    @property
    def days(self) -> float:
        a, b = dt.date.fromisoformat(self.trade_from), dt.date.fromisoformat(self.trade_to)
        return float((b - a).days + 1)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["fees"] = self.fees.to_dict()
        d["coins_per_tf"] = {tf: list(cs) for tf, cs in self.coins_per_tf}
        d["strategy_fingerprints"] = dict(self.strategy_fingerprints)
        d["jev_fingerprints"] = dict(self.jev_fingerprints)
        d["risk_profile"] = self.risk.to_dict()
        d["tier_risk"] = dict(TIER_RISK)
        d["participation_per_day"] = dict(PARTICIPATION_PER_DAY)
        d["participation_min_trades"] = {tf: min_trades(tf, self.days) for tf in self.timeframes}
        return d

    def fingerprint(self, ignore_dataset: bool = False) -> str:
        d = self.to_dict()
        if ignore_dataset:
            d.pop("dataset_fingerprint", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def config_for(window: Window, universe: dict[str, Any], **k: Any) -> V4Config:
    return V4Config(dataset_role=window.role, months=window.months, observe_from=window.observe_from,
                    trade_from=window.trade_from, trade_to=window.trade_to,
                    coins_per_tf=tuple((tf, tuple(universe["per_tf"][tf])) for tf in TIMEFRAMES),
                    universe_fingerprint=universe["fingerprint"],
                    tradeability_rule=str((universe["snapshot"] or {}).get("rule_fingerprint") or ""), **k)
