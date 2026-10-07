"""Frozen Jev V1 as a gate on LIVE candidates, with the latency it really costs.

Same question as the replay experiment (JEV_PROMPT_V1, the same timestamp-safe state builder) and
the same deterministic policy (JEV_POLICY_V1). What is new is the clock:

    candidate    the bot emitted a genuine candidate (RiskManager-approved at the control's size)
    request      the call to Jev started
    response     Jev answered (or failed)
    decision     the policy turned the answer into SKIP / REDUCE / TAKE
    submission   when a real order would have been sent: decision + the execution model's order latency

The mid-price is read at the candidate and at the response instant from the live book; the move
between them, signed against the trade's side, is the AI LATENCY SLIPPAGE a real +JEV bot would pay
on top of its control. It is recorded per decision and reported next to the economics -- the
simulated fill itself stays on the replay execution model so CONTROL and +JEV remain comparable.

There is no ledger or cache here: a live question has never been asked before. Retries are off
(a retried live decision is a stale one); a failure or timeout is SKIP, never control behaviour.

Re-deriving the past (a resumed FORWARD EXPERIMENT, app/live/continuity.py) never asks Jev: a
candidate before `live_from_ms` reuses the decision recorded for it, and one no session observed
(the server was down) follows the control, flagged NOT_OBSERVED -- neither side of the CONTROL vs
+JEV comparison moves on a candidate Jev never saw.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Mapping

from app.ai.jev.gate import GateVerdict, cache_key
from app.ai.jev.models import PROMPT_VERSION, JevOutcome
from app.ai.jev.policy import POLICY_V1, JevPolicyConfig, decide as policy_decide
from app.ai.jev.state import JevStateBuilder, health_features


def latency_slippage_bps(side: str, mid_then: float | None, mid_now: float | None) -> float | None:
    """Adverse move while waiting, in bps: positive = the price ran away from the entry."""
    if not mid_then or not mid_now:
        return None
    move = (mid_now - mid_then) / mid_then * 1e4
    return move if side == "long" else -move


class LiveJevGate:
    """ReplayEngine gate for a live +JEV bot. Only ever scales a size the RiskManager approved."""

    def __init__(self, decide: Callable[[Mapping[str, Any]], JevOutcome], bot: Mapping[str, Any],
                 pair_id: str, session_id: str, model: str, emit: Callable[[str, dict[str, Any]], None],
                 mid: Callable[[str], float | None], order_latency_ms: int,
                 policy: JevPolicyConfig = POLICY_V1, builder: JevStateBuilder | None = None,
                 wall: Callable[[], float] = time.time, live_from_ms: int = 0,
                 recorded: Mapping[tuple, Mapping[str, Any]] | None = None):
        self.decide_fn = decide
        self.bot = dict(bot)
        self.pair_id = pair_id
        self.session_id = session_id
        self.model = model
        self.emit = emit
        self.mid = mid
        self.order_latency_ms = int(order_latency_ms)
        self.policy = policy
        self.builder = builder or JevStateBuilder()
        self.wall = wall
        self.decisions = 0
        self.last: dict[str, Any] = {}
        self.live_from_ms = int(live_from_ms)
        self.recorded = dict(recorded or {})

    def past(self, sig: Any, ts: int) -> GateVerdict:
        rec = self.recorded.get((self.bot["key"], ts, sig.side))
        if rec is not None:
            m = float(rec.get("risk_multiplier") or 0.0)
            return GateVerdict(m, {"decision_id": rec.get("id") or "", "jev_action": rec.get("final_action"),
                                   "jev_level": rec.get("final_level"), "jev_multiplier": m,
                                   "jev_source": "recorded", "take_probability": rec.get("take_probability"),
                                   "jev_error": rec.get("error_code")}, error=bool(rec.get("error_code")))
        return GateVerdict(1.0, {"decision_id": "", "jev_action": "NOT_OBSERVED", "jev_multiplier": 1.0,
                                 "jev_source": "not_observed"})

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        if int(g["ts"]) < self.live_from_ms:
            return self.past(sig, int(g["ts"]))
        t_candidate = int(self.wall() * 1000)
        mid_then = self.mid(self.bot["symbol"])
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
        t_request = int(self.wall() * 1000)
        try:
            out = self.decide_fn(dict(snap.state))
        except Exception as exc:                     # a failed call is SKIP, never control behaviour
            out = JevOutcome(False, error_code="CLIENT_ERROR", error_message=type(exc).__name__)
        t_response = int(self.wall() * 1000)
        mid_now = self.mid(self.bot["symbol"])
        pol = policy_decide(out.decision if out.ok else None, self.policy, out.error_code)
        t_decision = int(self.wall() * 1000)
        did = hashlib.sha256(f"{self.session_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        dec = out.decision
        slip = latency_slippage_bps(sig.side, mid_then, mid_now)
        row = {
            "id": did, "session_id": self.session_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
            "symbol": self.bot["symbol"], "timeframe": self.bot["timeframe"], "side": sig.side,
            "signal_ts": int(g["ts"]), "candidate_wall_ms": t_candidate, "request_start_ms": t_request,
            "response_ms": t_response, "decision_ms": t_decision,
            "submit_ms": t_decision + self.order_latency_ms, "latency_ms": t_response - t_request,
            "mid_at_candidate": mid_then, "mid_at_response": mid_now, "latency_slippage_bps": slip,
            "model_requested": self.model, "model_resolved": dec.model_resolved if dec else "",
            "prompt_version": PROMPT_VERSION, "policy_version": self.policy.version,
            "state_fingerprint": snap.fingerprint, "state_json": json.dumps(dict(snap.state)),
            "take_probability": dec.take_probability if dec else None,
            "setup_quality": dec.setup_quality if dec else None,
            "risk_state": dec.risk_state if dec else None, "regime": dec.regime if dec else None,
            "final_action": pol.action, "final_level": pol.level, "risk_multiplier": pol.multiplier,
            "error_code": out.error_code or None, "timed_out": int(out.error_code == "TIMEOUT"),
            "input_tokens": dec.input_tokens if dec else 0, "output_tokens": dec.output_tokens if dec else 0,
            "cost_usd": dec.cost_usd if dec else 0.0,
            "notional": float(getattr(d, "notional", 0.0) or 0.0),
        }
        self.decisions += 1
        self.last = row
        self.emit("decision", row)
        info = {"decision_id": did, "jev_action": pol.action, "jev_level": pol.level,
                "jev_multiplier": pol.multiplier, "jev_source": "live",
                "take_probability": dec.take_probability if dec else None,
                "jev_error": out.error_code or None}
        return GateVerdict(pol.multiplier, info, error=not out.ok)
