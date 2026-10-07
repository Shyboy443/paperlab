"""Jev V5 for the V5 HOURLY / DAILY futures arena: JEV_PROMPT_V5, JEV_STATE_V5 and JEV_POLICY_V5.

V1-V4 are frozen evidence and untouched. Jev V5 is spent ONLY on a strategy family that already has a positive raw
edge on DEVELOPMENT (docs/V5_PROTOCOL.md §8), and asks one question about one setup of it:

    "Given that this setup already belongs to a strategy with positive historical raw edge, do current
     positioning, trend, volatility and funding conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT the setup?"

        CONTRADICT        -> SKIP     (no trade)
        SUPPORT           -> TAKE     (normal size, 1.0% risk)
        STRONGLY SUPPORT  -> ATTACK   (2.0% risk; needs a healthy bot and an order the RiskManager accepts,
                                       otherwise TAKE, recorded)

There is no DEFENSIVE size. A failed request is SKIP. The state holds the bot's trend ladder, the Bybit positioning
block (funding, its 90-day percentile and 24h change, open-interest changes, price / OI divergence, basis, the account
long ratio), the volatility band, relative volume, the thesis, the family's DEVELOPMENT raw edge, the transaction cost
and the funding expected over the hold, and the bot's drawdown -- all closed at the decision time. It never holds a
future return, a trade result, a holdout result or a leaderboard rank. Jev is judged by its SELECTION ALPHA against
the matched random action with the same SKIP / TAKE / ATTACK rates.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.gate import GateVerdict, ReproducibilityError, cache_key, outcome_from_row
from app.ai.jev.models import JevDecision, JevOutcome, SchemaError, fingerprint
from app.ai.jev.policy import PolicyResult
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import _context, _series, _u, health_features_v2
from app.ai.jev.v4 import ACTION_FOR, ACTIONS_V4, action_distribution_v4, parse_decision_v4
from app.competition.v5_config import health_state

PROMPT_VERSION_V5 = "JEV_PROMPT_V5"
STATE_VERSION_V5 = "JEV_STATE_V5"
ACTIONS_V5 = ACTIONS_V4

QUESTIONS_V5: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "A futures trading bot proposes the candidate trade in `setup` for one Bybit USDT perpetual. It decides on "
            "closed hourly (HOURLY class, holding 4-24 hours) or 4-hour candles (SWING class, holding 12-72 hours), "
            "with a structural stop and a time stop. The setup belongs to a strategy family whose entries showed a "
            "positive RAW edge on the development data (`economics.family_raw_edge_r`), and the order is legal. Given "
            "that this setup already belongs to a strategy with positive historical raw edge, do current positioning, "
            "trend, volatility and funding conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT the setup? Use the "
            "trend ladder, the positioning block (funding and its percentile and change, open-interest changes, "
            "price / OI divergence, basis, long/short ratio), volatility, relative volume, the transaction cost, the "
            "funding the position is expected to pay or receive over its hold, and the bot's drawdown. Judge the "
            "setup, not the chance of one loss: a strategy can lose more often than it wins and still have a positive "
            "expectancy."),
        "criteria": {
            "CONTRADICT": ("Current conditions materially contradict the setup: the higher timeframes point against "
                           "its direction, positioning says the move is already crowded or unwinding against it, the "
                           "funding over the hold eats most of the expected move, or volatility invalidates the "
                           "thesis. The bot should not trade it."),
            "SUPPORT": ("The setup remains valid: nothing in this state materially contradicts it. The bot should "
                        "trade it at normal size."),
            "STRONGLY_SUPPORT": ("Several independent conditions strongly reinforce the setup: the trend ladder aligned "
                                 "with its direction, positioning and funding confirming the thesis (for example "
                                 "open interest building with price, or a crowded side being forced out in the "
                                 "trade's favour), supportive volatility and a healthy bot. Exceptional: the bot "
                                 "should trade it above normal size."),
        },
    },
}
QUESTIONS_V5_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V5, "questions": QUESTIONS_V5})


def parse_decision_v5(body: Mapping[str, Any]) -> JevDecision:
    """Strict, as V4: CONTRADICT / SUPPORT / STRONGLY_SUPPORT -> SKIP / TAKE / ATTACK."""
    return parse_decision_v4(body, QUESTIONS_V5)


# ---- policy ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevPolicyV5Config:
    version: str = "JEV_POLICY_V5"
    multipliers: tuple[tuple[str, float], ...] = (("SKIP", 0.0), ("TAKE", 1.0), ("ATTACK", 2.0))
    mapping: tuple[tuple[str, str], ...] = tuple(ACTION_FOR.items())
    attack_rule: str = ("bot health OK under AGGRESSIVE_V5 (< 18% below peak, recent expectancy >= -0.25R) and the "
                        "2% ATTACK order legal; otherwise TAKE")
    on_error: str = "SKIP"

    def multiplier(self, level: str) -> float:
        return dict(self.multipliers)[level]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["multipliers"] = dict(self.multipliers)
        d["mapping"] = dict(self.mapping)
        return d

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V5 = JevPolicyV5Config()


def attack_or_take(health: str) -> tuple[str, str | None]:
    return ("ATTACK", None) if health == "OK" else ("TAKE", f"ATTACK -> TAKE: bot health {health}")


def decide_v5(decision: JevDecision | None, health: str, cfg: JevPolicyV5Config = POLICY_V5,
              error_code: str = "") -> PolicyResult:
    if decision is None:
        return PolicyResult("SKIP", "SKIP", cfg.multiplier("SKIP"), reason=f"JEV_ERROR {error_code or 'unknown'}")
    chosen = decision.risk_state if decision.risk_state in ACTIONS_V5 else "SKIP"
    level, why = (attack_or_take(health) if chosen == "ATTACK" else (chosen, None))
    return PolicyResult("SKIP" if level == "SKIP" else "TAKE", level, cfg.multiplier(level),
                        by_probability=f"{decision.take_probability:.3f}", by_risk_state=chosen,
                        reason=f"Jev: {chosen}" + (f"; {why}" if why else ""), extra={"downgrade": why})


# ---- state ----------------------------------------------------------------------------------------------

def _dir_vs(direction: Any, side: str) -> str | None:
    if direction not in ("up", "down", "flat"):
        return None
    if direction == "flat":
        return "FLAT"
    return "WITH" if direction == ("up" if side == "long" else "down") else "AGAINST"


def divergence(price_chg: float | None, oi_chg: float | None, eps: float = 0.005) -> str | None:
    """Price vs open interest over the same 24h: who is driving the move."""
    if price_chg is None or oi_chg is None:
        return None
    up, down = price_chg > eps, price_chg < -eps
    build, cut = oi_chg > eps, oi_chg < -eps
    if up and build:
        return "PRICE_UP_OI_UP (new longs)"
    if up and cut:
        return "PRICE_UP_OI_DOWN (short covering)"
    if down and build:
        return "PRICE_DOWN_OI_UP (new shorts)"
    if down and cut:
        return "PRICE_DOWN_OI_DOWN (long liquidation)"
    return "NO_CLEAR_DIVERGENCE"


class JevStateBuilderV5:
    """JEV_STATE_V5: bot, setup, trend ladder, positioning, volatility, economics, execution, bot health."""

    def build(self, *, ts: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], health: Mapping[str, Any], position: Mapping[str, Any]) -> DecisionSnapshot:
        for tf, cs in series.items():
            if any(int(c.close_time) > ts for c in cs):
                raise LookAheadError(f"{tf} candle closes after the decision time")
        meta = getattr(sig, "meta", None) or {}
        side = sig.side
        horizon = meta.get("horizon") or bot.get("horizon")
        ladder_tfs = [meta.get("signal_tf") or bot.get("signal_tf"), *(meta.get("context_tfs") or [])]
        ladder, agree, against = {}, 0, 0
        for tf in [t for t in ladder_tfs if t]:
            cs = list(series.get(tf) or [])[-120:]
            if len(cs) < 20:
                continue
            block = _context(cs)
            block["vs_trade"] = _dir_vs(block.get("direction"), side)
            agree += block["vs_trade"] == "WITH"
            against += block["vs_trade"] == "AGAINST"
            ladder[tf] = block
        sigs = list(series.get(ladder_tfs[0]) or []) if ladder_tfs and ladder_tfs[0] else []
        day_bars = 24 if horizon == "HOURLY" else 6
        price_chg = (sigs[-1].close / sigs[-1 - day_bars].close - 1.0) if len(sigs) > day_bars and sigs[-1 - day_bars].close else None
        prev_vol = [float(c.volume) for c in sigs[-21:-1]]
        rel_vol = (float(sigs[-1].volume) / (sum(prev_vol) / len(prev_vol))) if (sigs and len(prev_vol) >= 10 and sum(prev_vol) > 0) else None
        pos = dict(meta.get("positioning") or {})
        taker = float(execution.get("taker_fee") or 0.0)
        half = float(execution.get("half_spread_bps") or 0.0)
        slip = float(execution.get("expected_slippage_bps") or half)
        rt_bps = 2.0 * taker * 1e4 + 2.0 * slip
        stop = float(meta.get("stop_pct") or 0.0)
        fund = meta.get("expected_funding_pct")
        move = meta.get("expected_move_pct")
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "family": meta.get("family"), "horizon": horizon,
                    "signal_timeframe": ladder_tfs[0] if ladder_tfs else None,
                    "context_timeframes": ladder_tfs[1:], "execution_timeframe": "1m", "utc_hour": (ts // 3_600_000) % 24},
            "setup": {"side": side, "setup": meta.get("setup"), "thesis": meta.get("thesis") or bot.get("thesis"),
                      "fails_when": bot.get("fails_when"), "expected_hold": meta.get("expected_hold") or bot.get("expected_hold"),
                      "time_stop_hours": meta.get("time_stop_h"), "stop_distance_pct": _r(stop * 100, 3) if stop else None,
                      "target_r": bot.get("target_r"), "expected_move_pct": _r(move * 100, 3) if isinstance(move, (int, float)) else None,
                      "quality_factors": meta.get("quality_factors")},
            "trend": {"ladder": ladder, "aligned_with_trade": agree, "against_trade": against,
                      "regime_vs_trade": meta.get("regime")},
            "positioning": {**{k: _r(v, 6) if isinstance(v, float) else v for k, v in pos.items()},
                            "price_chg_24h": _r(price_chg, 5),
                            "price_vs_oi_24h": divergence(price_chg, pos.get("oi_chg_24h")),
                            "liquidations": "not available (Bybit publishes no liquidation history)"},
            "volatility": {"band": meta.get("vol_band"), "atr_pct_signal": (ladder.get(ladder_tfs[0]) or {}).get("atr_pct")
                           if ladder_tfs else None, "relative_volume_signal_bar": _r(rel_vol, 3)},
            "economics": {
                "family_raw_edge_r": bot.get("family_raw_edge_r"), "family_raw_edge_trades": bot.get("family_raw_edge_trades"),
                "family_net_edge_r": bot.get("family_net_edge_r"),
                "round_trip_cost_bps": _r(rt_bps, 3), "round_trip_cost_r": _r(rt_bps / 1e4 / stop, 4) if stop else None,
                "expected_funding_over_hold_pct": _r(fund * 100, 4) if isinstance(fund, (int, float)) else None,
                "expected_funding_over_hold_r": _r(fund / stop, 4) if (isinstance(fund, (int, float)) and stop) else None,
                "expected_move_vs_cost": _r((move * 1e4) / rt_bps, 3) if (isinstance(move, (int, float)) and rt_bps > 0) else None},
            "execution": {"taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                          "expected_slippage_bps": _r(slip, 3), "proposed_risk_pct": position.get("proposed_risk_pct"),
                          "proposed_leverage": position.get("proposed_leverage"),
                          "min_legal_notional_vs_equity": position.get("min_legal_notional_vs_equity"),
                          "available_margin_pct": position.get("available_margin_pct")},
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V5,
        }
        state = json.loads(json.dumps(state, default=str))
        return DecisionSnapshot(ts=int(ts), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V5,
                                meta={"ladder": list(ladder)})


# ---- deciders ---------------------------------------------------------------------------------------------

class ClientDeciderV5:
    def __init__(self, client: Any):
        self.client = client

    def decide(self, state: Any) -> JevOutcome:
        out = self.client.decide(state, QUESTIONS_V5, parse=False)
        if not out.ok:
            return out
        raw = out.extra.get("raw") or {}
        try:
            dec = parse_decision_v5(raw)
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts,
                          http_status=out.http_status)


class CachedDeciderV5:
    """The run's ledger first, then the shared cache, then Jev; V5 constants on every cache row."""

    def __init__(self, storage: Any, inner: Any, run_id: str, bot_key: str, model: str,
                 replay_only: bool = False, max_calls: int | None = None, max_cost_usd: float | None = None):
        self.storage, self.inner, self.run_id, self.bot_key, self.model = storage, inner, run_id, bot_key, model
        self.replay_only, self.max_calls, self.max_cost_usd = replay_only, max_calls, max_cost_usd

    def _spent(self) -> tuple[int, float]:
        row = self.storage.conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM jev_decisions "
                                        "WHERE run_id=? AND source='api'", (self.run_id,)).fetchone()
        return int(row[0]), float(row[1])

    def decide(self, key: str, snap: DecisionSnapshot) -> tuple[JevOutcome, str]:
        row = self.storage.jev_ledger_lookup(self.run_id, self.bot_key, key)
        if row is not None:
            return outcome_from_row(row), "ledger"
        cached = self.storage.jev_cache_get(key)
        if cached is not None:
            out = JevOutcome.from_dict(json.loads(cached["outcome_json"] or "{}"))
            out.cache_hit = True
            return out, "cache"
        if self.replay_only:
            raise ReproducibilityError(f"{self.bot_key}: no frozen decision for {key}")
        if self.inner is None:
            return JevOutcome(False, error_code="NOT_CONFIGURED", error_message="no decision source"), "none"
        if self.max_calls is not None or self.max_cost_usd is not None:
            n, cost = self._spent()
            if (self.max_calls is not None and n >= self.max_calls) or \
                    (self.max_cost_usd is not None and cost >= self.max_cost_usd):
                return JevOutcome(False, error_code="BUDGET", error_message="call/cost budget exhausted"), "budget"
        out = self.inner.decide(dict(snap.state))
        if out.ok and out.decision is not None:
            self.storage.jev_cache_put({"cache_key": key, "model_requested": self.model,
                                        "model_resolved": out.decision.model_resolved,
                                        "prompt_version": PROMPT_VERSION_V5,
                                        "questions_fingerprint": QUESTIONS_V5_FINGERPRINT,
                                        "state_fingerprint": snap.fingerprint, "outcome": out.to_dict()})
        return out, "api"


# ---- the gate -----------------------------------------------------------------------------------------------

ResizeProbe = Callable[[Any, float], tuple[bool, str]]


def _legal(level: str, mult: float, sig: Any, probe: ResizeProbe | None, cfg: JevPolicyV5Config) -> tuple[str, float, str | None]:
    """An ATTACK the RiskManager would refuse becomes TAKE (recorded ATTACK_NOT_LEGAL), never a veto."""
    if level == "ATTACK" and probe is not None:
        ok, why = probe(sig, mult)
        if not ok:
            return "TAKE", cfg.multiplier("TAKE"), f"ATTACK_NOT_LEGAL:{why}"
    return level, mult, None


def gate_context(g: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    eq = float(g["equity"])
    h = g["health"]
    d = g["decision"]
    health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]), list(g.get("trades") or []),
                                int(g["ts"]))
    rules = g.get("rules")
    price = float(g.get("price") or 0.0)
    min_legal = rules.min_order_notional(price, 1.0) if (rules is not None and price > 0) else None
    position = {"open_positions": g["open_positions"],
                "available_margin_pct": _r(g["available"] / eq, 4) if eq > 0 else None,
                "leverage_ceiling": g["leverage_ceiling"],
                "proposed_risk_pct": _r(d.risk_usd / eq, 5) if eq > 0 else None,
                "min_legal_notional_vs_equity": _r(min_legal / eq, 4) if (min_legal and eq > 0) else None,
                "proposed_leverage": d.leverage}
    execution = {"taker_fee": g["taker_fee"], "half_spread_bps": g["half_spread_bps"],
                 "expected_slippage_bps": g["expected_slippage_bps"]}
    return health, position, execution


class JevGateV5:
    """ReplayEngine gate for a +JEV5 bot: keeps (TAKE), enlarges (ATTACK) or refuses (SKIP) a size the RiskManager
    already approved at TAKE."""

    def __init__(self, decider: Any, storage: Any, run_id: str, bot: Mapping[str, Any], pair_id: str,
                 model: str, policy: JevPolicyV5Config = POLICY_V5, builder: JevStateBuilderV5 | None = None,
                 clock: Callable[[], float] = time.time, persist: bool = True, probe: ResizeProbe | None = None):
        self.decider, self.storage, self.run_id = decider, storage, run_id
        self.bot, self.pair_id, self.model, self.policy = dict(bot), pair_id, model, policy
        self.builder = builder or JevStateBuilderV5()
        self.clock, self.persist, self.probe = clock, persist, probe
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        health, position, execution = gate_context(g)
        state_h, _ = health_state(g["health"])
        tfs = [meta.get("signal_tf") or self.bot["signal_tf"], *(meta.get("context_tfs") or [])]
        snap = self.builder.build(ts=int(g["ts"]), bot=self.bot, sig=sig, series=_series(g, self.bot["symbol"], tfs),
                                  execution=execution, health=health, position=position)
        key = cache_key(model=self.model, bot_version=self.bot.get("control_version", ""),
                        strategy_id=self.bot["strategy_id"], params_version=self.bot["params_version"],
                        symbol=self.bot["symbol"], timeframe=self.bot["timeframe"], signal_ts=int(g["ts"]),
                        state_fingerprint=snap.fingerprint, prompt_version=PROMPT_VERSION_V5,
                        questions_fingerprint=QUESTIONS_V5_FINGERPRINT)
        t0 = self.clock()
        out, source = self.decider.decide(key, snap)
        roundtrip = int((self.clock() - t0) * 1000)
        dec = out.decision if out.ok else None
        pol = decide_v5(dec, state_h, self.policy, out.error_code)
        level, mult, legal_why = _legal(pol.level, pol.multiplier, sig, self.probe, self.policy)
        did = hashlib.sha256(f"{self.run_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        self.decisions += 1
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        if self.persist and source != "ledger" and self.storage is not None:
            self.storage.save_jev_decision({
                "id": did, "run_id": self.run_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
                "cache_key": key, "signal_ts": int(g["ts"]), "symbol": self.bot["symbol"],
                "timeframe": self.bot["timeframe"], "side": sig.side, "model_requested": self.model,
                "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V5,
                "policy_version": self.policy.version, "state_fingerprint": snap.fingerprint,
                "state_json": json.dumps(dict(snap.state)),
                "take_probability": dec.take_probability if dec else None, "setup_quality": None,
                "risk_state": dec.risk_state if dec else None, "regime": None,
                "answers_json": json.dumps(dec.to_dict()) if dec else None,
                "final_action": "SKIP" if level == "SKIP" else "TAKE", "final_level": level, "risk_multiplier": mult,
                "request_latency_ms": out.latency_ms if source == "api" else 0,
                "roundtrip_ms": roundtrip if source == "api" else 0,
                "input_tokens": dec.input_tokens if (dec and source == "api") else 0,
                "output_tokens": dec.output_tokens if (dec and source == "api") else 0,
                "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0,
                "cache_hit": int(source == "cache"), "source": source, "attempts": out.attempts,
                "error_code": out.error_code or None, "error_message": out.error_message or None,
                "created_ts": int(self.clock() * 1000)})
        pos = meta.get("positioning") or {}
        self.rows.append({"id": did, "ts": int(g["ts"]), "side": sig.side,
                          "p_support": dec.take_probability if dec else None, "chosen": dec.risk_state if dec else None,
                          "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                          "level": level, "mult": mult, "downgrade": (pol.extra or {}).get("downgrade") or legal_why,
                          "health": state_h, "funding_pct_90d": pos.get("funding_pct_90d"),
                          "oi_chg_24h": pos.get("oi_chg_24h"), "regime": meta.get("regime"),
                          "source": source, "latency_ms": out.latency_ms if source == "api" else None,
                          "error": out.error_code or None, "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0})
        info = {"decision_id": did, "jev_action": "SKIP" if level == "SKIP" else "TAKE", "jev_level": level,
                "jev_multiplier": mult, "jev_source": source, "take_probability": dec.take_probability if dec else None,
                "jev_error": out.error_code or None}
        return GateVerdict(mult, info, error=not out.ok)


# ---- baselines that stand in for Jev --------------------------------------------------------------------

class PolicyGateV5:
    """ALWAYS-TAKE and RANDOM MATCHED-ACTION (drawn with the +JEV5 bot's own SKIP / TAKE / ATTACK rates, deterministic
    seed), through the SAME health rule and legality probe. Never makes a request."""

    def __init__(self, kind: str, bot_key: str, seed: int = 0, distribution: Mapping[str, float] | None = None,
                 policy: JevPolicyV5Config = POLICY_V5, probe: ResizeProbe | None = None):
        self.kind, self.bot_key, self.seed, self.policy, self.probe = kind, bot_key, seed, policy, probe
        dist = dict(distribution or {"TAKE": 1.0})
        tot = sum(max(0.0, v) for v in dist.values()) or 1.0
        self.cum: list[tuple[str, float]] = []
        acc = 0.0
        for lvl in ACTIONS_V5:
            acc += max(0.0, dist.get(lvl, 0.0)) / tot
            self.cum.append((lvl, acc))
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        ts = int(g["ts"])
        state_h, _ = health_state(g["health"])
        if self.kind == "TAKE":
            chosen = "TAKE"
        else:
            u = _u(self.seed, self.bot_key, ts, sig.side)
            chosen = next((lvl for lvl, c in self.cum if u < c), "TAKE")
        level, why = attack_or_take(state_h) if chosen == "ATTACK" else (chosen, None)
        mult = self.policy.multiplier(level)
        level, mult, legal_why = _legal(level, mult, sig, self.probe, self.policy)
        did = f"{self.kind.lower()}{self.seed}:{self.bot_key}:{ts}:{sig.side}"
        self.decisions += 1
        self.rows.append({"id": did, "ts": ts, "side": sig.side, "p_support": None, "chosen": chosen,
                          "p_skip": None, "p_take": None, "p_attack": None, "level": level, "mult": mult,
                          "downgrade": why or legal_why, "health": state_h, "regime": meta.get("regime"),
                          "source": self.kind.lower(), "latency_ms": None, "error": None, "cost_usd": 0.0})
        return GateVerdict(mult, {"decision_id": did, "jev_action": "SKIP" if level == "SKIP" else "TAKE",
                                  "jev_level": level, "jev_multiplier": mult, "jev_source": self.kind.lower()})


def action_distribution_v5(decisions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """The +JEV5 bot's own CHOSEN actions (an error counts as the SKIP it became)."""
    return action_distribution_v4(decisions)


def v5_fingerprints() -> dict[str, str]:
    import inspect
    src = inspect.getsource(JevStateBuilderV5).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V5_FINGERPRINT, "policy": POLICY_V5.fingerprint(),
            "state_version": STATE_VERSION_V5, "prompt_version": PROMPT_VERSION_V5,
            "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12]}
