"""Jev V3 for the V3.1 AGGRESSIVE EDGE arena: JEV_PROMPT_V3, JEV_STATE_V3 and JEV_POLICY_V3.

V1 (models.QUESTIONS_V1 / POLICY_V1) and V2 (v2.QUESTIONS_V2 / POLICY_V2) are frozen evidence and are
not touched. V3 is a new version with its own questions, state and policy, fingerprinted and cached
separately (the prompt version and question fingerprint are part of every cache key).

What V3 learned from V2 (V3 arena, DEVELOPMENT): Jev V2 chose DEFENSIVE almost always, its P(win) AUC
was ~0.51, DEFENSIVE's half size pushed 20 USDT orders below the exchange minimum, and its "gain" over
the controls was lower exposure, not selection. So in V3:

* Jev is only asked about candidates that ALREADY passed legality, the execution-cost model and the
  positive expected-net-edge gate -- and the question says so. It is asked whether current conditions
  SUPPORT or CONTRADICT the setup, not whether one trade will win (a long-hold continuation strategy
  can win under half its trades and still have positive expectancy);
* three actions: SKIP (evidence materially contradicts the setup), TAKE (the setup remains valid:
  the normal action), ATTACK (several independent conditions strongly reinforce it). No DEFENSIVE;
* Jev can never shrink an order and can never create an invalid one:

      candidate -> edge gate -> Jev action -> sizing -> exchange legality -> RiskManager

  ATTACK is re-sized through the RiskManager; an ATTACK that the exchange filters or the risk cap would
  refuse becomes TAKE (recorded ATTACK_NOT_LEGAL). TAKE is the size legality already approved, so
  MIN_NOTIONAL_AFTER_JEV cannot occur by construction -- it is still counted, and must stay 0;
* ATTACK needs the edge model's ATTACK eligibility and a healthy bot (the same deterministic rule the
  CONTROL uses); STRONG_ATTACK (2.0%) needs P(ATTACK) >= 0.60 and the edge model's EXCEPTIONAL flag.

A failed request is SKIP (as in V1/V2): a +JEV bot that silently trades like its control whenever the
API is down would contaminate the comparison. The error rate has its own gate (<= 2%).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.gate import GateVerdict, ReproducibilityError, cache_key, outcome_from_row
from app.ai.jev.models import JevDecision, JevOutcome, SchemaError, _prob, _probs, fingerprint
from app.ai.jev.policy import PolicyResult
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import JevStateBuilderV2, _context, _series, _u, health_features_v2
from app.competition.v31_config import attack_tier, health_state

PROMPT_VERSION_V3 = "JEV_PROMPT_V3"
STATE_VERSION_V3 = "JEV_STATE_V3"
ACTIONS_V3: tuple[str, ...] = ("SKIP", "TAKE", "ATTACK")
LEVELS_V3: tuple[str, ...] = ("SKIP", "TAKE", "ATTACK", "STRONG_ATTACK")
MTF_ORDER: tuple[str, ...] = ("4h", "1h", "30m", "15m", "5m", "3m", "1m")

QUESTIONS_V3: dict[str, dict[str, Any]] = {
    "support": {
        "type": "noul",
        "instructions": (
            "An intraday trading bot's rule-based strategy proposes the candidate trade in `signal` for one "
            "crypto perpetual. The candidate has ALREADY passed deterministic checks: it is a legal exchange "
            "order, its execution cost is known, and a model fitted on this strategy's own past candidates "
            "predicts a positive expected net edge after fees, spread and slippage (`expected_edge`). Given "
            "that this setup has already passed deterministic legality, execution-cost and positive-edge "
            "checks, determine whether current market conditions SUPPORT or CONTRADICT the strategy's setup "
            "(`thesis`, `signal`), using the multi-timeframe, momentum, volume, flow, volatility, execution, "
            "derivatives and bot-health data in this state."),
        "criteria": {
            "true": "Current conditions support the setup, or at least do not materially contradict its direction and thesis.",
            "false": ("Current conditions materially contradict the setup: the higher-timeframe structure is "
                      "against the direction, the move is exhausted, or momentum and flow point the other way."),
        },
    },
    "action": {
        "type": "choice",
        "instructions": (
            "Choose the action for this candidate. It already passed legality, execution-cost and positive-edge "
            "checks, so TAKE is the normal action for a setup that remains valid. A continuation strategy with a "
            "trailing runner can win fewer than half of its trades and still have a positive expectancy: judge "
            "the setup against the evidence, not the chance of a single loss. Distinguish an ordinary "
            "opportunity (TAKE) from an exceptional one (ATTACK) and from one the evidence contradicts (SKIP)."),
        "criteria": {
            "SKIP": ("The evidence in this state materially contradicts the setup: direction against the "
                     "higher-timeframe structure, an exhausted move, opposing momentum or taker flow, or "
                     "conditions that invalidate the thesis. Do not trade."),
            "TAKE": "The setup remains valid: nothing in this state materially contradicts it. Trade at normal size.",
            "ATTACK": ("Exceptional: multiple independent conditions strongly reinforce the setup -- higher "
                       "timeframes aligned with the direction, momentum and relative volume building, taker "
                       "flow agreeing, and an expected net edge well above the execution cost. Trade above normal size."),
        },
    },
}
QUESTIONS_V3_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V3, "questions": QUESTIONS_V3})


def parse_decision_v3(body: Mapping[str, Any], questions: Mapping[str, Any] = QUESTIONS_V3) -> JevDecision:
    """Strict: any value outside the schema is an error. `take_probability` carries P(support),
    `risk_state` the action choice and `risk_probabilities` P(SKIP/TAKE/ATTACK). V3 asks no quality
    or regime question (those fields stay empty)."""
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise SchemaError("MISSING_ANSWER", "response has no answers object")
    for key, q in questions.items():
        a = answers.get(key)
        if not isinstance(a, dict):
            raise SchemaError("MISSING_ANSWER", f"no answer for {key}")
        if a.get("type") != q["type"]:
            raise SchemaError("SCHEMA_MISMATCH", f"{key} answered as {a.get('type')!r}")
    support = _prob(answers["support"].get("noul"), "support.noul")
    act = answers["action"]
    if act.get("choice") not in ACTIONS_V3:
        raise SchemaError("SCHEMA_MISMATCH", "action.choice not an allowed option")
    aprobs = _probs(act.get("probabilities"), set(ACTIONS_V3), "action")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return JevDecision(take_probability=support, risk_state=str(act["choice"]), risk_probabilities=aprobs,
                       setup_quality=0.0, quality_score=0.0, quality_probabilities={}, regime="",
                       regime_probabilities={}, model_resolved=str(body.get("model") or ""),
                       provider=str(body.get("provider") or ""), request_id=str(body.get("id") or ""),
                       input_tokens=int(usage.get("input_tokens") or 0),
                       output_tokens=int(usage.get("output_tokens") or 0), cost_usd=float(usage.get("cost") or 0.0))


# ---- policy ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevPolicyV3Config:
    version: str = "JEV_POLICY_V3"
    multipliers: tuple[tuple[str, float], ...] = (("SKIP", 0.0), ("TAKE", 1.0), ("ATTACK", 1.5),
                                                  ("STRONG_ATTACK", 2.0))
    strong_attack_prob: float = 0.60        # STRONG_ATTACK needs Jev's own P(ATTACK) >= 0.60 ...
    strong_needs_exceptional_edge: bool = True   # ... and the edge model's EXCEPTIONAL flag
    attack_rule: str = "v31_config.attack_tier: edge ATTACK-eligible + bot health OK"
    on_error: str = "SKIP"

    def multiplier(self, level: str) -> float:
        return dict(self.multipliers)[level]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["multipliers"] = dict(self.multipliers)
        return d

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V3 = JevPolicyV3Config()
ACTION_OF_V3 = {"SKIP": "SKIP", "TAKE": "TAKE", "ATTACK": "TAKE", "STRONG_ATTACK": "TAKE"}


def decide_v3(decision: JevDecision | None, edge: Mapping[str, Any] | None, health: str,
              cfg: JevPolicyV3Config = POLICY_V3, error_code: str = "") -> PolicyResult:
    """Jev's action -> level -> multiplier. Only the shared deterministic ATTACK rule acts on it."""
    if decision is None:
        level = cfg.on_error
        return PolicyResult(ACTION_OF_V3[level], level, cfg.multiplier(level), reason=f"JEV_ERROR {error_code or 'unknown'}")
    chosen = decision.risk_state if decision.risk_state in ACTIONS_V3 else "SKIP"
    if chosen != "ATTACK":
        return PolicyResult(ACTION_OF_V3[chosen], chosen, cfg.multiplier(chosen), by_probability=f"{decision.take_probability:.3f}",
                            by_risk_state=chosen, reason=f"Jev chose {chosen}")
    p_attack = float((decision.risk_probabilities or {}).get("ATTACK") or 0.0)
    want = "STRONG_ATTACK" if p_attack >= cfg.strong_attack_prob else "ATTACK"
    tier, why = attack_tier(edge, health, want)
    reason = f"Jev chose ATTACK (P {p_attack:.2f})" + (f"; {why}" if why else f" -> {tier}")
    return PolicyResult(ACTION_OF_V3[tier], tier, cfg.multiplier(tier), by_probability=f"{decision.take_probability:.3f}",
                        by_risk_state=chosen, reason=reason, extra={"downgrade": why or None, "wanted": want})


# ---- state ----------------------------------------------------------------------------------------------

def _dir_vs(direction: Any, side: str) -> str | None:
    if direction not in ("up", "down", "flat"):
        return None
    if direction == "flat":
        return "FLAT"
    return "WITH" if direction == ("up" if side == "long" else "down") else "AGAINST"


class JevStateBuilderV3(JevStateBuilderV2):
    """JEV_STATE_V3 = JEV_STATE_V2 (signal, fast market, context, execution, derivatives, health) plus:
    the strategy's thesis, the expected-net-edge verdict, the full multi-timeframe ladder
    (4h/1h direction -> 15m structure -> 5m setup -> 3m trigger -> 1m execution) and its alignment with
    the trade, and the volatility band. Everything is closed at `ts`: no outcome, holdout result or
    leaderboard exists in it."""

    def build(self, *, ts: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], health: Mapping[str, Any], position: Mapping[str, Any],
              funding: Sequence[tuple[int, float]] = ()) -> DecisionSnapshot:
        snap = super().build(ts=ts, bot=bot, sig=sig, series=series, execution=execution, health=health,
                             position=position, funding=funding)
        state = json.loads(json.dumps(snap.state, default=str))
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
        mtf: dict[str, Any] = {}
        agree = against = 0
        for tf in MTF_ORDER:
            cs = list(series.get(tf) or [])[-120:]
            if len(cs) < 20:
                continue
            if any(int(c.close_time) > ts for c in cs):
                raise LookAheadError(f"{tf} candle closes after the decision time")
            block = _context(cs)
            block["vs_trade"] = _dir_vs(block.get("direction"), sig.side)
            agree += block["vs_trade"] == "WITH"
            against += block["vs_trade"] == "AGAINST"
            mtf[tf] = block
        state["thesis"] = {"strategy": bot.get("strategy_name"), "family": meta.get("family"),
                           "hypothesis": bot.get("hypothesis"), "thesis": meta.get("thesis") or bot.get("thesis"),
                           "expected_hold": meta.get("expected_hold") or bot.get("expected_hold"),
                           "direction": sig.side, "runner": meta.get("runner")}
        state["expected_edge"] = {
            "gate": "PASSED", "predicted_net_r": edge.get("net_r"), "lower_bound_r": edge.get("lower_r"),
            "predicted_gross_r": edge.get("gross_r"), "execution_cost_r": edge.get("cost_r"),
            "expected_edge_bps": edge.get("edge_bps"), "gross_edge_vs_cost": edge.get("headroom_ratio"),
            "p_target": edge.get("p_target"), "p_stop": edge.get("p_stop"),
            "avg_winner_r": edge.get("avg_win_r"), "avg_loser_r": edge.get("avg_loss_r"),
            "evidence_trades": edge.get("n"), "attack_eligible": edge.get("attack_eligible")}
        state["multi_timeframe"] = {"ladder": mtf, "aligned_with_trade": agree, "against_trade": against,
                                    "regime_4h_vs_trade": meta.get("regime")}
        state["volatility"] = {"band_1h_atr": meta.get("vol_band"),
                               "realized_vol_1m_60": (state.get("fast_market") or {}).get("realized_vol_1m_60"),
                               "atr_pct_signal": (state.get("fast_market") or {}).get("atr_pct_signal")}
        state["state_version"] = STATE_VERSION_V3
        return DecisionSnapshot(ts=int(ts), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V3,
                                meta={**snap.meta, "mtf": list(mtf)})


# ---- deciders ---------------------------------------------------------------------------------------------

class ClientDeciderV3:
    def __init__(self, client: Any):
        self.client = client

    def decide(self, state: Any) -> JevOutcome:
        out = self.client.decide(state, QUESTIONS_V3, parse=False)
        if not out.ok:
            return out
        raw = out.extra.get("raw") or {}
        try:
            dec = parse_decision_v3(raw)
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts,
                          http_status=out.http_status)


class CachedDeciderV3:
    """The run's ledger first, then the shared cache, then Jev; V3 constants on every cache row."""

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
                                        "prompt_version": PROMPT_VERSION_V3,
                                        "questions_fingerprint": QUESTIONS_V3_FINGERPRINT,
                                        "state_fingerprint": snap.fingerprint, "outcome": out.to_dict()})
        return out, "api"


# ---- the gate -----------------------------------------------------------------------------------------------

ResizeProbe = Callable[[Any, float], tuple[bool, str]]


def _legal_level(level: str, mult: float, sig: Any, probe: ResizeProbe | None,
                 cfg: JevPolicyV3Config) -> tuple[str, float, str | None]:
    """An ATTACK the RiskManager would refuse becomes TAKE (never a veto). TAKE is the size legality
    already approved; if even that were refused the candidate is SKIP, recorded MIN_NOTIONAL_AFTER_JEV."""
    if level in ("ATTACK", "STRONG_ATTACK") and probe is not None:
        ok, why = probe(sig, mult)
        if not ok:
            return "TAKE", cfg.multiplier("TAKE"), f"ATTACK_NOT_LEGAL:{why}"
    return level, mult, None


class JevGateV3:
    """ReplayEngine gate for a +JEV3 bot. Keeps or enlarges a size the RiskManager already approved."""

    def __init__(self, decider: Any, storage: Any, run_id: str, bot: Mapping[str, Any], pair_id: str,
                 model: str, policy: JevPolicyV3Config = POLICY_V3, builder: JevStateBuilderV3 | None = None,
                 clock: Callable[[], float] = time.time, persist: bool = True, probe: ResizeProbe | None = None):
        self.decider, self.storage, self.run_id = decider, storage, run_id
        self.bot, self.pair_id, self.model, self.policy = dict(bot), pair_id, model, policy
        self.builder = builder or JevStateBuilderV3()
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
                        state_fingerprint=snap.fingerprint, prompt_version=PROMPT_VERSION_V3,
                        questions_fingerprint=QUESTIONS_V3_FINGERPRINT)
        t0 = self.clock()
        out, source = self.decider.decide(key, snap)
        roundtrip = int((self.clock() - t0) * 1000)
        dec = out.decision if out.ok else None
        pol = decide_v3(dec, edge, state_h, self.policy, out.error_code)
        level, mult, legal_why = _legal_level(pol.level, pol.multiplier, sig, self.probe, self.policy)
        did = hashlib.sha256(f"{self.run_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        self.decisions += 1
        latency = out.latency_ms if source == "api" else None
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        if self.persist and source != "ledger" and self.storage is not None:
            self.storage.save_jev_decision({
                "id": did, "run_id": self.run_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
                "cache_key": key, "signal_ts": int(g["ts"]), "symbol": self.bot["symbol"],
                "timeframe": self.bot["timeframe"], "side": sig.side, "model_requested": self.model,
                "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V3,
                "policy_version": self.policy.version, "state_fingerprint": snap.fingerprint,
                "state_json": json.dumps(dict(snap.state)),
                "take_probability": dec.take_probability if dec else None, "setup_quality": None,
                "risk_state": dec.risk_state if dec else None, "regime": None,
                "answers_json": json.dumps(dec.to_dict()) if dec else None,
                "final_action": ACTION_OF_V3[level], "final_level": level, "risk_multiplier": mult,
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
                          "source": source, "latency_ms": latency, "error": out.error_code or None,
                          "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0})
        info = {"decision_id": did, "jev_action": ACTION_OF_V3[level], "jev_level": level, "jev_multiplier": mult,
                "jev_source": source, "take_probability": dec.take_probability if dec else None,
                "jev_error": out.error_code or None}
        return GateVerdict(mult, info, error=not out.ok)


# ---- baselines that stand in for Jev --------------------------------------------------------------------

class PolicyGateV3:
    """ALWAYS-TAKE (constant TAKE) and RANDOM MATCHED-ACTION: an action drawn with the +JEV3 bot's own
    SKIP / TAKE / ATTACK rates (deterministic seed), through the SAME ATTACK rule and legality probe.
    No request is ever made."""

    def __init__(self, kind: str, bot_key: str, seed: int = 0, distribution: Mapping[str, float] | None = None,
                 strong_share: float = 0.0, policy: JevPolicyV3Config = POLICY_V3, probe: ResizeProbe | None = None):
        self.kind, self.bot_key, self.seed, self.policy, self.probe = kind, bot_key, seed, policy, probe
        dist = dict(distribution or {"TAKE": 1.0})
        tot = sum(max(0.0, v) for v in dist.values()) or 1.0
        self.cum: list[tuple[str, float]] = []
        acc = 0.0
        for lvl in ACTIONS_V3:
            acc += max(0.0, dist.get(lvl, 0.0)) / tot
            self.cum.append((lvl, acc))
        self.strong_share = strong_share
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
        why = None
        if chosen == "ATTACK":
            want = "STRONG_ATTACK" if _u("strong", self.seed, self.bot_key, ts) < self.strong_share else "ATTACK"
            level, why = attack_tier(edge, state_h, want)
            why = why or None
        else:
            level = chosen
        mult = self.policy.multiplier(level)
        level, mult, legal_why = _legal_level(level, mult, sig, self.probe, self.policy)
        did = f"{self.kind.lower()}{self.seed}:{self.bot_key}:{ts}:{sig.side}"
        self.decisions += 1
        self.rows.append({"id": did, "ts": ts, "side": sig.side, "p_support": None, "chosen": chosen,
                          "p_skip": None, "p_take": None, "p_attack": None, "level": level, "mult": mult,
                          "downgrade": why or legal_why, "health": state_h, "edge_net_r": edge.get("net_r"),
                          "edge_lower_r": edge.get("lower_r"), "attack_eligible": edge.get("attack_eligible"),
                          "signal_quality": meta.get("signal_quality"), "source": self.kind.lower(),
                          "latency_ms": None, "error": None, "cost_usd": 0.0})
        return GateVerdict(mult, {"decision_id": did, "jev_action": ACTION_OF_V3[level], "jev_level": level,
                                  "jev_multiplier": mult, "jev_source": self.kind.lower()})


class ObserverGate:
    """RAW observation: refuses every candidate so the engine simulates it in the shadow book at TAKE
    size, and records its features. Never trades, never asks anyone anything."""

    def __init__(self):
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        from app.competition.v31_edge import cost_r_of, qband
        meta = getattr(sig, "meta", None) or {}
        ts = int(g["ts"])
        did = f"obs:{ts}:{sig.side}:{self.decisions}"
        self.decisions += 1
        stop_pct = meta.get("stop_pct")
        self.rows.append({"id": did, "ts": ts, "side": sig.side, "quality": meta.get("signal_quality"),
                          "qband": qband(meta.get("signal_quality")), "regime": meta.get("regime") or "UNKNOWN",
                          "vol_band": meta.get("vol_band") or "UNKNOWN", "stop_pct": stop_pct,
                          "cost_r": _r(cost_r_of(float(g["taker_fee"]), float(g["half_spread_bps"]), stop_pct or 0.0), 5),
                          "expected_move_pct": meta.get("expected_move_pct")})
        return GateVerdict(0.0, {"decision_id": did, "jev_source": "observer"})


def action_distribution_v3(decisions: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], float]:
    """The +JEV3 bot's own CHOSEN actions (an error counts as the SKIP it became) and the share of its
    ATTACK choices that asked for STRONG_ATTACK (P(ATTACK) >= 0.60) -- the random twin draws from this."""
    n = len(decisions)
    if not n:
        return {"TAKE": 1.0}, 0.0
    counts: dict[str, int] = {}
    for d in decisions:
        k = d.get("chosen") if d.get("chosen") in ACTIONS_V3 else "SKIP"
        counts[k] = counts.get(k, 0) + 1
    attacks = [d for d in decisions if d.get("chosen") == "ATTACK"]
    strong = sum(1 for d in attacks if (d.get("p_attack") or 0.0) >= POLICY_V3.strong_attack_prob)
    return {k: v / n for k, v in counts.items()}, (strong / len(attacks)) if attacks else 0.0


def v3_fingerprints() -> dict[str, str]:
    return {"prompt": QUESTIONS_V3_FINGERPRINT, "policy": POLICY_V3.fingerprint(),
            "state_version": STATE_VERSION_V3, "prompt_version": PROMPT_VERSION_V3}
