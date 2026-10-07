"""Jev V4 for the V4 intraday specialists: JEV_PROMPT_V4, JEV_STATE_V4 and JEV_POLICY_V4.

V1 / V2 / V3 are frozen evidence and untouched. What V4 learned from them (docs/V4_ROOT_CAUSE.md):
Jev's discrimination was never distinguishable from chance (AUC 0.51 V1, 0.51 V2, 0.50 / 0.55 V3), and its
apparent gains came from trading less on losing strategies, not from selection. So V4 asks ONE narrow
question, only about a setup that has already passed every deterministic check:

    "Given that this setup already passed legality, cost and expected-edge checks, do current market
     conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT the setup?"

        CONTRADICT        -> SKIP     (no trade)
        SUPPORT           -> TAKE     (normal size, 1.0% risk)
        STRONGLY SUPPORT  -> ATTACK   (2.0% risk; needs a healthy bot and an order the RiskManager accepts,
                                       otherwise TAKE, recorded)

Jev never shrinks an order and never creates an illegal one. A failed request is SKIP (as in V1-V3): a +JEV
bot that silently traded like its control whenever the API was down would contaminate the comparison. Jev
is judged ONLY by its selection alpha against the matched random action with the same SKIP / TAKE / ATTACK
distribution -- beating a losing control by trading less is not selection.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.gate import GateVerdict, ReproducibilityError, cache_key, outcome_from_row
from app.ai.jev.models import JevDecision, JevOutcome, SchemaError, _probs, fingerprint
from app.ai.jev.policy import PolicyResult
from app.ai.jev.state import DecisionSnapshot, _r
from app.ai.jev.v2 import _series, _u, health_features_v2
from app.ai.jev.v3 import JevStateBuilderV3, ObserverGate  # noqa: F401  (ObserverGate re-exported for V4 RAW bots)
from app.competition.v4_config import health_state

PROMPT_VERSION_V4 = "JEV_PROMPT_V4"
STATE_VERSION_V4 = "JEV_STATE_V4"
ANSWERS_V4: tuple[str, ...] = ("CONTRADICT", "SUPPORT", "STRONGLY_SUPPORT")
ACTIONS_V4: tuple[str, ...] = ("SKIP", "TAKE", "ATTACK")
ACTION_FOR: dict[str, str] = {"CONTRADICT": "SKIP", "SUPPORT": "TAKE", "STRONGLY_SUPPORT": "ATTACK"}

QUESTIONS_V4: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "An intraday trading bot proposes the candidate trade in `signal` for one crypto perpetual. Its strategy is "
            "a three-layer specialist: a 1h trend, a setup on the structure timeframe (`thesis`), and a trigger on the "
            "bot's own timeframe. The candidate has ALREADY passed deterministic checks: it is a legal exchange order, "
            "its round-trip cost is at most a quarter of its risk, and the strategy family's own recent record on this "
            "timeframe gives it a positive expected net edge (`expected_edge`). Given that this setup already passed "
            "legality, cost and expected-edge checks, do current market conditions CONTRADICT, SUPPORT or STRONGLY "
            "SUPPORT the setup? Use the multi-timeframe ladder, momentum, volume, taker flow, volatility, execution, "
            "derivatives and bot-health data in this state. Judge the setup, not the chance of one loss: a strategy "
            "with a trailing runner can lose more often than it wins and still have a positive expectancy."),
        "criteria": {
            "CONTRADICT": ("The evidence materially contradicts the setup: the higher timeframes point against its "
                           "direction, the move is exhausted, momentum or taker flow point the other way, or the "
                           "conditions invalidate the thesis. The bot should not trade it."),
            "SUPPORT": ("The setup remains valid: nothing in this state materially contradicts it. The bot should "
                        "trade it at normal size."),
            "STRONGLY_SUPPORT": ("Several independent conditions strongly reinforce the setup: the higher timeframes "
                                 "aligned with its direction, momentum and relative volume building, taker flow "
                                 "agreeing, volatility supportive, and a healthy bot. Exceptional: the bot should "
                                 "trade it above normal size."),
        },
    },
}
QUESTIONS_V4_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V4, "questions": QUESTIONS_V4})


def parse_decision_v4(body: Mapping[str, Any], questions: Mapping[str, Any] = QUESTIONS_V4) -> JevDecision:
    """Strict: any value outside the schema is an error. `risk_state` carries the ACTION the answer maps to,
    `risk_probabilities` P(SKIP / TAKE / ATTACK) = P(CONTRADICT / SUPPORT / STRONGLY_SUPPORT), and
    `take_probability` = 1 - P(CONTRADICT) (the probability the setup is at least supported)."""
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise SchemaError("MISSING_ANSWER", "response has no answers object")
    a = answers.get("conditions")
    if not isinstance(a, dict):
        raise SchemaError("MISSING_ANSWER", "no answer for conditions")
    if a.get("type") != questions["conditions"]["type"]:
        raise SchemaError("SCHEMA_MISMATCH", f"conditions answered as {a.get('type')!r}")
    if a.get("choice") not in ANSWERS_V4:
        raise SchemaError("SCHEMA_MISMATCH", "conditions.choice not an allowed option")
    probs = _probs(a.get("probabilities"), set(ANSWERS_V4), "conditions")
    aprobs = {ACTION_FOR[k]: v for k, v in probs.items()}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return JevDecision(take_probability=1.0 - float(probs.get("CONTRADICT") or 0.0), risk_state=ACTION_FOR[a["choice"]],
                       risk_probabilities=aprobs, setup_quality=0.0, quality_score=0.0, quality_probabilities={},
                       regime="", regime_probabilities={}, model_resolved=str(body.get("model") or ""),
                       provider=str(body.get("provider") or ""), request_id=str(body.get("id") or ""),
                       input_tokens=int(usage.get("input_tokens") or 0),
                       output_tokens=int(usage.get("output_tokens") or 0), cost_usd=float(usage.get("cost") or 0.0))


# ---- policy ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevPolicyV4Config:
    version: str = "JEV_POLICY_V4"
    multipliers: tuple[tuple[str, float], ...] = (("SKIP", 0.0), ("TAKE", 1.0), ("ATTACK", 2.0))
    mapping: tuple[tuple[str, str], ...] = tuple(ACTION_FOR.items())
    attack_rule: str = "bot health OK (< 18% below peak, recent expectancy >= -0.25R) and the ATTACK order legal"
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


POLICY_V4 = JevPolicyV4Config()


def attack_or_take(health: str) -> tuple[str, str | None]:
    """The ATTACK rule every mode shares (JEV, RANDOM): a bot that is not healthy trades TAKE instead."""
    return ("ATTACK", None) if health == "OK" else ("TAKE", f"ATTACK -> TAKE: bot health {health}")


def decide_v4(decision: JevDecision | None, health: str, cfg: JevPolicyV4Config = POLICY_V4,
              error_code: str = "") -> PolicyResult:
    if decision is None:
        return PolicyResult("SKIP", "SKIP", cfg.multiplier("SKIP"), reason=f"JEV_ERROR {error_code or 'unknown'}")
    chosen = decision.risk_state if decision.risk_state in ACTIONS_V4 else "SKIP"
    level, why = (attack_or_take(health) if chosen == "ATTACK" else (chosen, None))
    return PolicyResult("SKIP" if level == "SKIP" else "TAKE", level, cfg.multiplier(level),
                        by_probability=f"{decision.take_probability:.3f}", by_risk_state=chosen,
                        reason=f"Jev: {chosen}" + (f"; {why}" if why else ""), extra={"downgrade": why})


# ---- state ----------------------------------------------------------------------------------------------

class JevStateBuilderV4(JevStateBuilderV3):
    """JEV_STATE_V4 = JEV_STATE_V3 (signal, fast market, context, execution, derivatives, health, thesis,
    multi-timeframe ladder, volatility band) with the V4 expected-edge block (family x timeframe rolling
    expectancy). Everything is closed at `ts`: no outcome, holdout result or leaderboard exists in it."""

    def build(self, **k: Any) -> DecisionSnapshot:
        snap = super().build(**k)
        state = json.loads(json.dumps(snap.state, default=str))
        edge = (getattr(k["sig"], "meta", None) or {}).get("edge") or {}
        state["expected_edge"] = {
            "gate": "PASSED", "model": "family x timeframe expectancy of its most recent resolved setups (causal)",
            "predicted_net_r": edge.get("net_r"), "lower_bound_r": edge.get("lower_r"),
            "predicted_gross_r": edge.get("gross_r"), "execution_cost_r": edge.get("cost_r"),
            "expected_edge_bps": edge.get("edge_bps"), "gross_edge_vs_cost": edge.get("headroom_ratio"),
            "evidence_setups": (edge.get("n") or {}).get("family")}
        state["state_version"] = STATE_VERSION_V4
        return DecisionSnapshot(ts=snap.ts, state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V4,
                                meta=dict(snap.meta))


# ---- deciders ---------------------------------------------------------------------------------------------

class ClientDeciderV4:
    def __init__(self, client: Any):
        self.client = client

    def decide(self, state: Any) -> JevOutcome:
        out = self.client.decide(state, QUESTIONS_V4, parse=False)
        if not out.ok:
            return out
        raw = out.extra.get("raw") or {}
        try:
            dec = parse_decision_v4(raw)
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts,
                          http_status=out.http_status)


class CachedDeciderV4:
    """The run's ledger first, then the shared cache, then Jev; V4 constants on every cache row."""

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
                                        "prompt_version": PROMPT_VERSION_V4,
                                        "questions_fingerprint": QUESTIONS_V4_FINGERPRINT,
                                        "state_fingerprint": snap.fingerprint, "outcome": out.to_dict()})
        return out, "api"


# ---- the gate -----------------------------------------------------------------------------------------------

ResizeProbe = Callable[[Any, float], tuple[bool, str]]


def _legal(level: str, mult: float, sig: Any, probe: ResizeProbe | None, cfg: JevPolicyV4Config) -> tuple[str, float, str | None]:
    """An ATTACK the RiskManager would refuse becomes TAKE (recorded ATTACK_NOT_LEGAL), never a veto."""
    if level == "ATTACK" and probe is not None:
        ok, why = probe(sig, mult)
        if not ok:
            return "TAKE", cfg.multiplier("TAKE"), f"ATTACK_NOT_LEGAL:{why}"
    return level, mult, None


class JevGateV4:
    """ReplayEngine gate for a +JEV4 bot. Keeps (TAKE), enlarges (ATTACK) or refuses (SKIP) a size the
    RiskManager already approved at TAKE."""

    def __init__(self, decider: Any, storage: Any, run_id: str, bot: Mapping[str, Any], pair_id: str,
                 model: str, policy: JevPolicyV4Config = POLICY_V4, builder: JevStateBuilderV4 | None = None,
                 clock: Callable[[], float] = time.time, persist: bool = True, probe: ResizeProbe | None = None):
        self.decider, self.storage, self.run_id = decider, storage, run_id
        self.bot, self.pair_id, self.model, self.policy = dict(bot), pair_id, model, policy
        self.builder = builder or JevStateBuilderV4()
        self.clock, self.persist, self.probe = clock, persist, probe
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def _context(self, g: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        eq = float(g["equity"])
        h = g["health"]
        d = g["decision"]
        health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]),
                                    list(g.get("trades") or []), int(g["ts"]))
        rules = g.get("rules")
        price = float(g.get("price") or 0.0)
        min_legal = rules.min_order_notional(price, 1.0) if (rules is not None and price > 0) else None
        position = {"open_positions": g["open_positions"],
                    "available_margin_pct": _r(g["available"] / eq, 4) if eq > 0 else None,
                    "leverage_ceiling": g["leverage_ceiling"],
                    "proposed_risk_pct": _r(d.risk_usd / eq, 5) if eq > 0 else None,
                    "proposed_notional_vs_equity": _r(d.notional / eq, 4) if eq > 0 else None,
                    "min_legal_notional_vs_equity": _r(min_legal / eq, 4) if (min_legal and eq > 0) else None,
                    "proposed_leverage": d.leverage}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": g["half_spread_bps"],
                     "expected_slippage_bps": g["expected_slippage_bps"]}
        return health, position, execution

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
        health, position, execution = self._context(g)
        state_h, _ = health_state(g["health"])
        tfs = [self.bot["timeframe"], *(meta.get("context_tfs") or []), "1h", "4h", "1m"]
        snap = self.builder.build(ts=int(g["ts"]), bot=self.bot, sig=sig, series=_series(g, self.bot["symbol"], tfs),
                                  execution=execution, health=health, position=position, funding=g["funding"])
        key = cache_key(model=self.model, bot_version=self.bot.get("control_version", ""),
                        strategy_id=self.bot["strategy_id"], params_version=self.bot["params_version"],
                        symbol=self.bot["symbol"], timeframe=self.bot["timeframe"], signal_ts=int(g["ts"]),
                        state_fingerprint=snap.fingerprint, prompt_version=PROMPT_VERSION_V4,
                        questions_fingerprint=QUESTIONS_V4_FINGERPRINT)
        t0 = self.clock()
        out, source = self.decider.decide(key, snap)
        roundtrip = int((self.clock() - t0) * 1000)
        dec = out.decision if out.ok else None
        pol = decide_v4(dec, state_h, self.policy, out.error_code)
        level, mult, legal_why = _legal(pol.level, pol.multiplier, sig, self.probe, self.policy)
        did = hashlib.sha256(f"{self.run_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        self.decisions += 1
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        if self.persist and source != "ledger" and self.storage is not None:
            self.storage.save_jev_decision({
                "id": did, "run_id": self.run_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
                "cache_key": key, "signal_ts": int(g["ts"]), "symbol": self.bot["symbol"],
                "timeframe": self.bot["timeframe"], "side": sig.side, "model_requested": self.model,
                "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V4,
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
        self.rows.append({"id": did, "ts": int(g["ts"]), "side": sig.side,
                          "p_support": dec.take_probability if dec else None,
                          "chosen": dec.risk_state if dec else None,
                          "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                          "level": level, "mult": mult, "downgrade": (pol.extra or {}).get("downgrade") or legal_why,
                          "health": state_h, "edge_net_r": edge.get("net_r"), "edge_lower_r": edge.get("lower_r"),
                          "attack_eligible": edge.get("attack_eligible"), "signal_quality": meta.get("signal_quality"),
                          "source": source, "latency_ms": out.latency_ms if source == "api" else None,
                          "error": out.error_code or None, "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0})
        info = {"decision_id": did, "jev_action": "SKIP" if level == "SKIP" else "TAKE", "jev_level": level,
                "jev_multiplier": mult, "jev_source": source, "take_probability": dec.take_probability if dec else None,
                "jev_error": out.error_code or None}
        return GateVerdict(mult, info, error=not out.ok)


# ---- baselines that stand in for Jev --------------------------------------------------------------------

class PolicyGateV4:
    """ALWAYS-TAKE and RANDOM MATCHED-ACTION (an action drawn with the +JEV4 bot's own SKIP / TAKE / ATTACK
    rates, deterministic seed), through the SAME health rule and legality probe. Never makes a request."""

    def __init__(self, kind: str, bot_key: str, seed: int = 0, distribution: Mapping[str, float] | None = None,
                 policy: JevPolicyV4Config = POLICY_V4, probe: ResizeProbe | None = None):
        self.kind, self.bot_key, self.seed, self.policy, self.probe = kind, bot_key, seed, policy, probe
        dist = dict(distribution or {"TAKE": 1.0})
        tot = sum(max(0.0, v) for v in dist.values()) or 1.0
        self.cum: list[tuple[str, float]] = []
        acc = 0.0
        for lvl in ACTIONS_V4:
            acc += max(0.0, dist.get(lvl, 0.0)) / tot
            self.cum.append((lvl, acc))
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
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
                          "downgrade": why or legal_why, "health": state_h, "edge_net_r": edge.get("net_r"),
                          "edge_lower_r": edge.get("lower_r"), "attack_eligible": edge.get("attack_eligible"),
                          "signal_quality": meta.get("signal_quality"), "source": self.kind.lower(),
                          "latency_ms": None, "error": None, "cost_usd": 0.0})
        return GateVerdict(mult, {"decision_id": did, "jev_action": "SKIP" if level == "SKIP" else "TAKE",
                                  "jev_level": level, "jev_multiplier": mult, "jev_source": self.kind.lower()})


def action_distribution_v4(decisions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """The +JEV4 bot's own CHOSEN actions (an error counts as the SKIP it became): the random twin draws
    from exactly this distribution."""
    n = len(decisions)
    if not n:
        return {"TAKE": 1.0}
    counts: dict[str, int] = {}
    for d in decisions:
        k = d.get("chosen") if d.get("chosen") in ACTIONS_V4 else "SKIP"
        counts[k] = counts.get(k, 0) + 1
    return {k: v / n for k, v in counts.items()}


def v4_fingerprints() -> dict[str, str]:
    return {"prompt": QUESTIONS_V4_FINGERPRINT, "policy": POLICY_V4.fingerprint(),
            "state_version": STATE_VERSION_V4, "prompt_version": PROMPT_VERSION_V4}
