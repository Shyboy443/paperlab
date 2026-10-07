"""The +JEV decision layer as a replay gate, with its frozen ledger and shared cache.

    candidate (already approved by the RiskManager at the control's size)
        -> DecisionSnapshot (timestamp-safe, compact)
        -> ledger of THIS experiment?  reuse it       (a replay makes zero calls)
        -> shared cache?               reuse it       (an identical question is never paid twice)
        -> Jev                                        (only for genuinely new questions)
        -> JevPolicy -> multiplier -> back through the RiskManager

The cache key covers everything that can change Jev's ANSWER: the requested model, the prompt and
question fingerprint, the bot's identity (strategy, version, coin, timeframe), the signal time and
the state fingerprint. The policy version is deliberately NOT in it -- thresholds act after the
answer, so V2 of the policy may reuse V1's answers to identical questions. It IS on every ledger row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.ai.jev.models import (PROMPT_VERSION, QUESTIONS_FINGERPRINT, JevDecision, JevOutcome,
                               fingerprint)
from app.ai.jev.policy import POLICY_V1, JevPolicyConfig, decide as policy_decide
from app.ai.jev.state import DecisionSnapshot, JevStateBuilder, health_features

log = logging.getLogger("paperlab.jev.gate")


class ReproducibilityError(RuntimeError):
    """A replay-only run met a question its frozen ledger does not answer."""


@dataclass
class GateVerdict:
    multiplier: float
    info: dict[str, Any] = field(default_factory=dict)
    error: bool = False


def cache_key(*, model: str, bot_version: str, strategy_id: str, params_version: str, symbol: str,
              timeframe: str, signal_ts: int, state_fingerprint: str,
              prompt_version: str = PROMPT_VERSION,
              questions_fingerprint: str = QUESTIONS_FINGERPRINT) -> str:
    return fingerprint({"model": model, "prompt": prompt_version, "questions": questions_fingerprint,
                        "bot": bot_version, "strategy": strategy_id, "params": params_version,
                        "symbol": symbol, "tf": timeframe, "ts": int(signal_ts),
                        "state": state_fingerprint}, 24)


class Decider:
    """Anything that turns a state into a JevOutcome: the real client, or a test double."""

    def decide(self, state: Any) -> JevOutcome:          # pragma: no cover - interface
        raise NotImplementedError


class ClientDecider(Decider):
    def __init__(self, client: Any):
        self.client = client

    def decide(self, state: Any) -> JevOutcome:
        return self.client.decide(state)


class CachedDecider:
    """Ledger first, then the shared cache, then Jev -- and a hard budget on real calls."""

    def __init__(self, storage: Any, inner: Decider | None, run_id: str, bot_key: str,
                 model: str, replay_only: bool = False, max_calls: int | None = None,
                 max_cost_usd: float | None = None):
        self.storage = storage
        self.inner = inner
        self.run_id = run_id
        self.bot_key = bot_key
        self.model = model
        self.replay_only = replay_only
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd

    def _spent(self) -> tuple[int, float]:
        row = self.storage.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM jev_decisions "
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
            return JevOutcome(False, error_code="NOT_CONFIGURED",
                              error_message="no decision source configured"), "none"
        if self.max_calls is not None or self.max_cost_usd is not None:
            n, cost = self._spent()
            if (self.max_calls is not None and n >= self.max_calls) or \
                    (self.max_cost_usd is not None and cost >= self.max_cost_usd):
                return JevOutcome(False, error_code="BUDGET",
                                  error_message="experiment call/cost budget exhausted"), "budget"
        out = self.inner.decide(dict(snap.state))
        if out.ok and out.decision is not None:
            self.storage.jev_cache_put({
                "cache_key": key, "model_requested": self.model,
                "model_resolved": out.decision.model_resolved, "prompt_version": PROMPT_VERSION,
                "questions_fingerprint": QUESTIONS_FINGERPRINT,
                "state_fingerprint": snap.fingerprint, "outcome": out.to_dict()})
        return out, "api"


def outcome_from_row(row: Mapping[str, Any]) -> JevOutcome:
    answers = json.loads(row.get("answers_json") or "null")
    decision = JevDecision.from_dict(answers) if answers else None
    return JevOutcome(ok=decision is not None and not row.get("error_code"), decision=decision,
                      error_code=row.get("error_code") or "", error_message=row.get("error_message") or "",
                      latency_ms=int(row.get("request_latency_ms") or 0),
                      attempts=int(row.get("attempts") or 0), cache_hit=True)


class JevGate:
    """ReplayEngine gate. Only ever scales a size the RiskManager already approved."""

    def __init__(self, decider: CachedDecider, storage: Any, run_id: str, bot: Mapping[str, Any],
                 pair_id: str, model: str, policy: JevPolicyConfig = POLICY_V1,
                 builder: JevStateBuilder | None = None,
                 clock: Callable[[], float] = time.time):
        self.decider = decider
        self.storage = storage
        self.run_id = run_id
        self.bot = dict(bot)                  # key, strategy_id, strategy_name, params_version, ...
        self.pair_id = pair_id
        self.model = model
        self.policy = policy
        self.builder = builder or JevStateBuilder()
        self.clock = clock
        self.decisions = 0

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        eq = float(g["equity"])
        h = g["health"]
        d = g["decision"]
        health = health_features(eq, float(h.get("peak") or eq), float(g["start_equity"]), h.get("r") or [])
        position = {"open_positions": g["open_positions"],
                    "available_margin_pct": round(g["available"] / eq, 4) if eq > 0 else None,
                    "leverage_ceiling": g["leverage_ceiling"],
                    "proposed_risk_pct": round(d.risk_usd / eq, 5) if eq > 0 else None,
                    "proposed_notional_vs_equity": round(d.notional / eq, 4) if eq > 0 else None,
                    "proposed_leverage": d.leverage}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": g["half_spread_bps"],
                     "expected_slippage_bps": g["expected_slippage_bps"]}
        snap = self.builder.build(ts=int(g["ts"]), bot=self.bot, sig=sig, candles=g["candles"],
                                  execution=execution, health=health, position=position,
                                  funding=g["funding"])
        key = cache_key(model=self.model, bot_version=self.bot.get("control_version", ""),
                        strategy_id=self.bot["strategy_id"], params_version=self.bot["params_version"],
                        symbol=self.bot["symbol"], timeframe=self.bot["timeframe"],
                        signal_ts=int(g["ts"]), state_fingerprint=snap.fingerprint)
        t0 = self.clock()
        out, source = self.decider.decide(key, snap)
        roundtrip = int((self.clock() - t0) * 1000)
        pol = policy_decide(out.decision if out.ok else None, self.policy, out.error_code)
        did = hashlib.sha256(f"{self.run_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        self.decisions += 1
        if source != "ledger":
            dec = out.decision
            self.storage.save_jev_decision({
                "id": did, "run_id": self.run_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
                "cache_key": key, "signal_ts": int(g["ts"]), "symbol": self.bot["symbol"],
                "timeframe": self.bot["timeframe"], "side": sig.side,
                "model_requested": self.model, "model_resolved": dec.model_resolved if dec else "",
                "prompt_version": PROMPT_VERSION, "policy_version": self.policy.version,
                "state_fingerprint": snap.fingerprint, "state_json": json.dumps(dict(snap.state)),
                "take_probability": dec.take_probability if dec else None,
                "setup_quality": dec.setup_quality if dec else None,
                "risk_state": dec.risk_state if dec else None, "regime": dec.regime if dec else None,
                "answers_json": json.dumps(dec.to_dict()) if dec else None,
                "final_action": pol.action, "final_level": pol.level,
                "risk_multiplier": pol.multiplier,
                "request_latency_ms": out.latency_ms if source == "api" else 0,
                "roundtrip_ms": roundtrip if source == "api" else 0,
                "input_tokens": dec.input_tokens if (dec and source == "api") else 0,
                "output_tokens": dec.output_tokens if (dec and source == "api") else 0,
                "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0,
                "cache_hit": int(source == "cache"), "source": source, "attempts": out.attempts,
                "error_code": out.error_code or None, "error_message": out.error_message or None,
                "created_ts": int(self.clock() * 1000)})
        info = {"decision_id": did, "jev_action": pol.action, "jev_level": pol.level,
                "jev_multiplier": pol.multiplier, "jev_source": source,
                "take_probability": out.decision.take_probability if out.decision else None,
                "jev_error": out.error_code or None}
        return GateVerdict(pol.multiplier, info, error=not out.ok)
