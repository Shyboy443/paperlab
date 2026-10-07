"""V6 FORWARD ARENA configuration (docs/V6_PROTOCOL.md): the field, execution, AGGRESSIVE_V6 dynamic legal risk,
evidence maturity and the FREEZE MANIFEST that fixes a forward experiment's identity.

V1-V5 are frozen research history and untouched. V6 is evaluated on LIVE forward data only (Bybit), with simulated
fills: nothing here can place an order.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.competition.bots import RiskProfile
from app.execution.config import BYBIT_LINEAR, ExecutionConfig, FeeSchedule

PROTOCOL_VERSION = "V6_FORWARD_PROTOCOL_V1"
VENUE = "BYBIT_LINEAR"
DOCS = Path(__file__).resolve().parents[2] / "docs"
FREEZE_FILE = DOCS / "V6_FREEZE.json"

HOUR = 3_600_000
MINUTE = 60_000
DAY = 24 * HOUR

# ---- the field --------------------------------------------------------------------------------------------------

HORIZON_TAG = {"HOURLY": "1H", "SWING": "4H"}
SIGNAL_TF = {"HOURLY": "1h", "SWING": "4h"}
HOURLY_FAMILIES: tuple[str, ...] = ("V6.1", "V6.2", "V6.3", "V6.4", "V6.5", "V6.6")
SWING_FAMILIES: tuple[str, ...] = ("V6.1",)            # the V5 lead (a SWING replication) is also run as SWING
TRADED_COINS = 4                                        # the top of the frozen universe ranking
BREADTH_SET = 16                                        # BTC + ETH + the top liquid eligible coins, observed only


@dataclass(frozen=True)
class BotSpecV6:
    strategy_id: str
    coin: str
    horizon: str                                        # HOURLY / SWING
    role: str = "CONTROL"                               # CONTROL / JEV

    @property
    def symbol(self) -> str:
        return f"{self.coin}USDT"

    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-{self.coin}-{HORIZON_TAG[self.horizon]}"

    @property
    def key(self) -> str:
        return self.control_key + ("+JEV" if self.role == "JEV" else "")

    @property
    def pair_id(self) -> str:
        return "v6pair:" + self.control_key

    @property
    def timeframe(self) -> str:
        return SIGNAL_TF[self.horizon]

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "key": self.key, "control_key": self.control_key, "pair_id": self.pair_id,
                "symbol": self.symbol, "timeframe": self.timeframe}


def field_plan(coins: Sequence[str], jev: bool = True) -> list[BotSpecV6]:
    """Every CONTROL of the V6 field and, when `jev`, its matched +JEV twin (same candidate, data, equity, execution
    and risk; Jev only filters or enlarges)."""
    out: list[BotSpecV6] = []
    for horizon, fams in (("HOURLY", HOURLY_FAMILIES), ("SWING", SWING_FAMILIES)):
        for sid in fams:
            for coin in coins:
                out.append(BotSpecV6(sid, coin, horizon, "CONTROL"))
                if jev:
                    out.append(BotSpecV6(sid, coin, horizon, "JEV"))
    return out


# ---- execution and data -----------------------------------------------------------------------------------------

# Every decision is taken at the instant H that closes its signal candle. The live market holds the hour-closing bar
# at the HOUR BARRIER until the hour's positioning is fetched (<= BARRIER_TIMEOUT_MS); Jev then has until
# H + DECISION_WINDOW_MS - JEV_DEADLINE_MARGIN_MS; the order fills at the open of the first 1m bar at or after
# H + DECISION_WINDOW_MS -- always AFTER the decision, CONTROL and +JEV alike.
DECISION_WINDOW_MS = 120_000
BARRIER_TIMEOUT_MS = 45_000
JEV_TIMEOUT_MS = 25_000
JEV_RETRIES = 1
JEV_DEADLINE_MARGIN_MS = 5_000
LATE_AFTER_MS = DECISION_WINDOW_MS - JEV_DEADLINE_MARGIN_MS     # a decision later than this could not be acted on
STALE_STREAM_S = 90.0                                           # no kline / ticker message for this long = stale
STALE_POSITIONING_H = 3.0                                       # newest OI / funding older than this = stale

# The engine stamps a signal at its candle's close_time (H - 1 ms) and executes an order at the open of the 1m bar that
# contains signal + latency. Latency = the window + 1 ms puts that instant exactly at H + DECISION_WINDOW_MS, so every
# fill is priced at the OPEN of the first 1m bar starting at or after H + 2 min -- strictly after the latest possible
# Jev answer (H + 115 s), never at a price from inside the window.
EXECUTION_V6 = ExecutionConfig(level=2, base_slippage_bps=0.1, vol_component=0.02, size_component=0.0,
                               slippage_mult=1.0, force_taker=False,
                               signal_latency_ms=DECISION_WINDOW_MS - 400 + 1, order_latency_ms=400)
FEES_V6: FeeSchedule = BYBIT_LINEAR

WARMUP_DAYS = 45                    # 1m bars: >= 40 closed daily candles (a SWING bot's 1D structure), the 1D EMA
                                    # 10/30 trend, 4h EMA 20/50, 60 signal bars -- every bot can trade from the start
FUNDING_HISTORY_DAYS = 95           # the 90-day funding percentile
POSITIONING_HISTORY_DAYS = 35       # OI / basis / ratio: 30-day percentiles, 3-day changes
CONTEXT_HISTORY_DAYS = 45           # market-context 1h klines: BTC / ETH 1D trends, breadth vs the 1D EMA20

# ---- risk: AGGRESSIVE_V6 ----------------------------------------------------------------------------------------

AGGRESSIVE_V6 = RiskProfile(
    name="AGGRESSIVE_V6", starting_balance=20.0, max_leverage=20,  # type: ignore[arg-type]
    ordinary_risk_pct=0.010, strong_risk_pct=0.015, exceptional_risk_pct=0.020, max_risk_pct=0.020,
    attack_multiplier=2.0, defensive_multiplier=1.0, health_window=20, attack_min_trades=0,
    attack_min_expectancy_r=-0.25, attack_max_drawdown=0.18, defensive_expectancy_r=-9.0,
    defensive_drawdown=1.0, halt_drawdown=0.30, strong_quality=0.60, exceptional_quality=0.85,
    attack_min_edge_to_cost=2.0)
STARTING_BALANCE = 20.0
LEVERAGE_CEILING = 20
MAX_FEE_SHARE_OF_R = 0.25
TIERS = ("TAKE", "LEGAL_STRONG", "LEGAL_CONVICTION", "ATTACK_ONLY", "ATTACK")
# The engine's own guards, pinned here (never read from the environment): no new entry for the rest of a UTC day after
# a 12% intraday loss, a permanent halt at 75% of the starting balance, notional caps, the minimum stop.
ENGINE_SETTINGS = {"daily_halt_pct": 0.12, "strategy_halt_pct": 0.25, "max_total_notional_mult": 8.0,
                   "max_net_notional_mult": 4.0, "min_stop_bps": 8.0}


def settings_v6() -> Any:
    """The engine settings every V6 book runs with (paper only: nothing here can reach an exchange)."""
    from app.config import load_settings
    base = load_settings({"MODE": "FUTURES_TESTNET", "DASHBOARD_PASSWORD": "v6-forward-engine", "DRY_RUN": "true",
                          "SYMBOLS": "BTCUSDT", "STRATEGY_STARTING_BALANCE": str(STARTING_BALANCE)}, env_path=None)
    return dataclasses.replace(base, strategy_starting_balance=STARTING_BALANCE,
                               risk_per_trade_pct=AGGRESSIVE_V6.ordinary_risk_pct, max_fee_share_of_r=MAX_FEE_SHARE_OF_R,
                               min_notional_safety_multiplier=1.0, **ENGINE_SETTINGS)


def health_state(health: Mapping[str, Any], p: RiskProfile = AGGRESSIVE_V6) -> tuple[str, dict[str, Any]]:
    recent = list(health.get("r") or [])[-p.health_window:]
    exp = sum(recent) / len(recent) if recent else 0.0
    dd = float(health.get("drawdown") or 0.0)
    info = {"recent_expectancy_r": round(exp, 4), "recent_trades": len(recent), "drawdown_at_entry": round(dd, 4)}
    if dd >= p.halt_drawdown:
        return "HALTED", info
    if dd >= p.attack_max_drawdown or (len(recent) >= 10 and exp < p.attack_min_expectancy_r):
        return "NO_ATTACK", info
    return "OK", info


class SizingV6:
    """AGGRESSIVE_V6 dynamic legal risk (docs/V6_PROTOCOL.md §5). The base is 1% of the bot's equity. When the
    exchange's minimum order needs more:

        needs <= 1.0%                 TAKE at 1%
        1.0% < needs <= 1.5%          LEGAL_STRONG at exactly the legal minimum, only for a strong setup (quality >= 0.60)
        1.5% < needs <= 2.0%          LEGAL_CONVICTION for a very-high-conviction setup (quality >= 0.85); on a +JEV
                                      bot otherwise ATTACK_ONLY: sized at the minimum, traded only if Jev says ATTACK
        needs > 2.0%                  SKIP (MIN_NOTIONAL_LIMITED)

    Risk is never raised merely to reach the minimum beyond these tiers, and never above 2%. A halted bot (30% below
    its peak) takes nothing. Every refusal carries its reason."""

    def __init__(self, rules: Mapping[str, Any], jev: bool, profile: RiskProfile = AGGRESSIVE_V6):
        self.rules = rules
        self.jev = bool(jev)
        self.profile = profile
        self.probes: Any = None

    def legal_min_risk_pct(self, sig: Any, equity: float) -> float | None:
        r = self.rules.get(sig.symbol)
        dist = abs(float(sig.entry_price) - float(sig.stop))
        if r is None or equity <= 0 or dist <= 0 or sig.entry_price <= 0:
            return None
        return float(r.min_order_qty(float(sig.entry_price), 1.0)) * dist / equity

    def __call__(self, sig: Any, health: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
        p = self.profile
        state, info = health_state(health, p)
        q = float((getattr(sig, "meta", None) or {}).get("signal_quality") or 0.0)
        base = {**info, "health": state, "quality": round(q, 4)}
        if state == "HALTED":
            return 0.0, {**base, "tier": "HALTED", "target_risk_pct": 0.0, "reject_reason": "bot_halted"}
        need = self.legal_min_risk_pct(sig, float(health.get("equity") or 0.0))
        if need is None:
            return 0.0, {**base, "tier": "SKIP", "reject_reason": "no_market_rules"}
        base["legal_min_risk_pct"] = round(need, 5)
        take = p.ordinary_risk_pct
        bump = lambda r: (r / take) * (1.0 + 1e-6)  # noqa: E731  (clears the qty rounding, never more)
        if need <= take:
            return 1.0, {**base, "tier": "TAKE", "target_risk_pct": take}
        if need <= p.strong_risk_pct:
            if q >= p.strong_quality:
                return bump(need), {**base, "tier": "LEGAL_STRONG", "target_risk_pct": round(need, 5)}
            return 0.0, {**base, "tier": "SKIP", "reject_reason": "min_notional_needs_strong_setup"}
        if need <= p.max_risk_pct:
            if q >= p.exceptional_quality:
                return bump(need), {**base, "tier": "LEGAL_CONVICTION", "target_risk_pct": round(need, 5)}
            if self.jev:
                return bump(need), {**base, "tier": "ATTACK_ONLY", "target_risk_pct": round(need, 5), "attack_only": True}
            return 0.0, {**base, "tier": "SKIP", "reject_reason": "min_notional_needs_attack"}
        return 0.0, {**base, "tier": "SKIP", "reject_reason": "min_notional_above_max_risk"}


# ---- evidence maturity -------------------------------------------------------------------------------------------

MATURITY = (("MATURE SAMPLE", 30 * DAY, 30), ("EARLY SIGNAL", 7 * DAY, 15), ("COLLECTING", DAY, 3))


def maturity(age_ms: int | float, trades: int) -> str:
    """TOO EARLY / COLLECTING / EARLY SIGNAL / MATURE SAMPLE -- never "WINNER". A MATURE SAMPLE needs >= 30 calendar
    days AND >= 30 closed trades; an evaluation is not serious before that."""
    for name, age, n in MATURITY:
        if age_ms >= age and trades >= n:
            return name
    return "TOO EARLY"


# ---- the freeze manifest (docs/V6_FREEZE.json) -----------------------------------------------------------------

def _src(obj: Any) -> str:
    return hashlib.sha256(inspect.getsource(obj).replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]


TRADING_MODULES = ("app.competition.v6_config", "app.ai.jev.v6", "app.live.v6_gates", "app.live.bybit_market",
                   "app.backtest.replay", "app.execution.model", "app.execution.config", "app.core.risk",
                   "app.core.portfolio", "app.core.positions")


def code_fingerprints() -> dict[str, str]:
    """Everything whose change would change what a V6 bot does or is charged: the strategies and features, this
    module (field, sizing, execution), Jev V6, the gates, the live market's data semantics (hour barrier,
    watermarks, observed spread, funding charged) and the shared engine (replay, execution model, risk, portfolio,
    exits). Reporting code (runner, worker, views) is not part of the identity."""
    import importlib
    from app.strategies.registry import v6_fingerprints
    out = {f"strategy:{k}": v for k, v in v6_fingerprints().items()}
    for name in TRADING_MODULES:
        out[f"source:{name}"] = _src(importlib.import_module(name))
    return out


def params_snapshot() -> dict[str, Any]:
    from app.strategies.registry import load_v6
    return {sid: dataclasses.asdict(cls.Params()) for sid, cls in sorted(load_v6().items())}


def config_snapshot() -> dict[str, Any]:
    return {"protocol": PROTOCOL_VERSION, "venue": VENUE, "fees": FEES_V6.to_dict(),
            "execution": dataclasses.asdict(EXECUTION_V6), "decision_window_ms": DECISION_WINDOW_MS,
            "barrier_timeout_ms": BARRIER_TIMEOUT_MS, "jev_timeout_ms": JEV_TIMEOUT_MS, "jev_retries": JEV_RETRIES,
            "jev_deadline_margin_ms": JEV_DEADLINE_MARGIN_MS, "stale_stream_s": STALE_STREAM_S,
            "stale_positioning_h": STALE_POSITIONING_H, "risk_profile": AGGRESSIVE_V6.to_dict(),
            "starting_balance": STARTING_BALANCE, "leverage_ceiling": LEVERAGE_CEILING,
            "max_fee_share_of_r": MAX_FEE_SHARE_OF_R, "engine_settings": dict(ENGINE_SETTINGS),
            "hourly_families": list(HOURLY_FAMILIES),
            "swing_families": list(SWING_FAMILIES), "traded_coins": TRADED_COINS, "breadth_set": BREADTH_SET,
            "warmup_days": WARMUP_DAYS, "funding_history_days": FUNDING_HISTORY_DAYS,
            "positioning_history_days": POSITIONING_HISTORY_DAYS, "context_history_days": CONTEXT_HISTORY_DAYS}


def manifest_fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "code", "params", "config", "universe", "breadth_set", "rules", "jev")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_freeze(path: Path = FREEZE_FILE) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    """What differs between the running code and the frozen manifest (empty = identical)."""
    if not man:
        return ["docs/V6_FREEZE.json is missing: V6 is not frozen"]
    diffs = []
    code = code_fingerprints()
    for k, v in sorted(code.items()):
        if (man.get("code") or {}).get(k) != v:
            diffs.append(f"{k}: frozen {(man.get('code') or {}).get(k)} != running {v}")
    for k in sorted(set(man.get("code") or {}) - set(code)):
        diffs.append(f"{k}: frozen but no longer in the code")
    if (man.get("params") or {}) != json.loads(json.dumps(params_snapshot())):
        diffs.append("strategy parameters differ from the freeze")
    if (man.get("config") or {}) != json.loads(json.dumps(config_snapshot(), default=str)):
        diffs.append("V6 configuration differs from the freeze")
    if man.get("fingerprint") != manifest_fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: str) -> tuple[str, dict[str, Any]]:
    """A FORWARD EXPERIMENT continues only while its identity is unchanged: the frozen manifest (strategy sources and
    parameters, features, universe, instrument rules, risk profile, execution model, fee schedule, venue, Jev prompt /
    state / policy) and the Jev model actually requested. Anything else starts a NEW experiment."""
    ident = {"protocol": man.get("protocol"), "manifest": man.get("fingerprint"), "jev_model": jev_model,
             "venue": (man.get("config") or {}).get("venue")}
    return "v6x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident
