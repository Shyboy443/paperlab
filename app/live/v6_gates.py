"""V6 live gates (docs/V6_PROTOCOL.md §6-§7): the CONTROL's gate and the +JEV twin's Jev V6 gate.

Both bots of a pair see the same candidate (same strategy, data, equity, execution and risk). Before anything else
BOTH apply the same LIVE-ACTIONABILITY rules, so neither side of the CONTROL vs +JEV comparison moves on a candidate
that could not really have been traded:

    MISSED_DOWNTIME   (re-derivation only) no session of the experiment was running at the decision instant
    LATE_DECISION     the decision would land after the decision window (e.g. a backlog after a reconnect)
    DATA_STALE        the coin's live data was stale at the decision (shared verdict per coin and instant)

The CONTROL then TAKEs every candidate. The +JEV twin asks Jev V6 (SKIP / TAKE / ATTACK) within the window; a failure
or a late answer is SKIP. Every decision -- the control's too -- is recorded, and a restarted process replays the
recorded decision instead of deciding again: Jev is never asked about the past. A candidate a session observed but
never recorded (a crash between deciding and writing) follows the control, flagged NOT_RECORDED.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.gate import GateVerdict
from app.ai.jev.models import JevOutcome
from app.ai.jev.v6 import (POLICY_V6, PROMPT_VERSION_V6, JevPolicyV6Config, JevStateBuilderV6, decide_v6)
from app.competition.v6_config import (DECISION_WINDOW_MS, JEV_DEADLINE_MARGIN_MS, LATE_AFTER_MS, health_state)

ResizeProbe = Callable[[Any, float], tuple[bool, str]]


def decision_id(experiment_id: str, bot_key: str, signal_ts: int, side: str) -> str:
    return hashlib.sha256(f"{experiment_id}|{bot_key}|{int(signal_ts)}|{side}".encode()).hexdigest()[:20]


def latency_move_bps(side: str, mid_then: float | None, mid_now: float | None) -> float | None:
    """Adverse market move while waiting, in bps: positive = the price ran away from the trade."""
    if not mid_then or not mid_now:
        return None
    move = (mid_now - mid_then) / mid_then * 1e4
    return move if side == "long" else -move


class Coverage:
    """The instants some session of the experiment was LIVE (between its go-live and its last heartbeat)."""

    def __init__(self, intervals: Sequence[tuple[int, int]] = ()):
        self.iv = sorted((int(a), int(b)) for a, b in intervals if a and b and int(b) >= int(a))

    def observed(self, t: int) -> bool:
        return any(a <= int(t) <= b for a, b in self.iv)


class DecisionBoard:
    """One live market-health verdict per (symbol, decision instant), shared by every bot on that coin, so a CONTROL
    and its +JEV twin never disagree about whether the data was fresh."""

    def __init__(self, check: Callable[[str, int], tuple[bool, str | None]]):
        self.check = check
        self._v: dict[tuple[str, int], tuple[bool, str | None]] = {}
        self._lock = threading.Lock()

    def verdict(self, symbol: str, t: int) -> tuple[bool, str | None]:
        key = (symbol, int(t))
        with self._lock:
            hit = self._v.get(key)
            if hit is None:
                hit = self.check(symbol, int(t))
                self._v[key] = hit
                if len(self._v) > 5000:
                    for k in list(self._v)[:2500]:
                        self._v.pop(k, None)
            return hit


class _Gate:
    role = "CONTROL"

    def __init__(self, *, experiment_id: str, session_id: str, bot: Mapping[str, Any],
                 emit: Callable[[str, dict[str, Any]], None], board: DecisionBoard, coverage: Coverage,
                 recorded: Mapping[tuple, Mapping[str, Any]] | None, live_from_ms: int,
                 wall: Callable[[], float] = time.time):
        self.experiment_id = experiment_id
        self.session_id = session_id
        self.bot = dict(bot)
        self.emit = emit
        self.board = board
        self.coverage = coverage
        self.recorded = dict(recorded or {})
        self.live_from_ms = int(live_from_ms)
        self.wall = wall
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []
        self.probe: ResizeProbe | None = None

    def _now(self) -> int:
        return int(self.wall() * 1000)

    def _base(self, sig: Any, g: Mapping[str, Any], t: int) -> dict[str, Any]:
        sz = dict(g.get("sizing") or {})
        return {"id": decision_id(self.experiment_id, self.bot["key"], int(g["ts"]), sig.side),
                "experiment_id": self.experiment_id, "session_id": self.session_id, "bot_key": self.bot["key"],
                "role": self.role, "pair_id": self.bot.get("pair_id"), "symbol": self.bot["symbol"],
                "horizon": self.bot.get("horizon"), "strategy_id": self.bot.get("strategy_id"), "side": sig.side,
                "signal_ts": int(g["ts"]), "decision_instant_ms": t, "tier": sz.get("tier"),
                "legal_min_risk_pct": sz.get("legal_min_risk_pct"), "quality": sz.get("quality"),
                "attack_only": bool(sz.get("attack_only"))}

    def _verdict(self, row: dict[str, Any], mult: float, error: bool = False, emit: bool = True) -> GateVerdict:
        self.decisions += 1
        row["risk_multiplier"] = float(mult)
        self.rows.append(row)
        if emit:
            self.emit("decision", row)
        info = {"decision_id": row["id"], "gate_action": row.get("final_action"), "jev_level": row.get("final_level"),
                "jev_multiplier": float(mult), "gate_reason": row.get("reason"), "jev_source": row.get("source")}
        return GateVerdict(float(mult), info, error=error)

    def _replay(self, sig: Any, g: Mapping[str, Any], t: int) -> GateVerdict | None:
        """A candidate before this session's go-live: the recorded decision, or MISSED when nobody was running."""
        if int(g["ts"]) >= self.live_from_ms:
            return None
        rec = self.recorded.get((self.bot["key"], int(g["ts"]), sig.side))
        row = self._base(sig, g, t)
        if rec is not None:
            row.update({k: rec.get(k) for k in ("final_action", "final_level", "reason", "error_code")},
                       source="recorded", rederived=True)
            return self._verdict(row, float(rec.get("risk_multiplier") or 0.0), error=bool(rec.get("error_code")),
                                 emit=False)
        if not self.coverage.observed(t):
            row.update(final_action="SKIP", final_level="SKIP", reason="MISSED_DOWNTIME", source="rederived",
                       rederived=True)
            return self._verdict(row, 0.0)
        return None

    def _live_block(self, sig: Any, g: Mapping[str, Any], t: int) -> str | None:
        if self._now() - t > LATE_AFTER_MS:
            return "LATE_DECISION"
        ok, why = self.board.verdict(self.bot["symbol"], t)
        return None if ok else f"DATA_STALE:{why}"


class ForwardGateV6(_Gate):
    """The CONTROL: TAKE every live-actionable candidate."""
    role = "CONTROL"

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        t = int(g["ts"]) + 1
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        row = self._base(sig, g, t)
        if int(g["ts"]) < self.live_from_ms:            # observed by a session, never recorded
            row.update(final_action="TAKE", final_level="TAKE", reason="NOT_RECORDED", source="rederived", rederived=True)
            return self._verdict(row, 1.0)
        block = self._live_block(sig, g, t)
        row.update(source="rule", decision_ms=self._now())
        if block:
            row.update(final_action="SKIP", final_level="SKIP", reason=block)
            return self._verdict(row, 0.0)
        row.update(final_action="TAKE", final_level="TAKE", reason="CONTROL")
        return self._verdict(row, 1.0)


class JevGateV6(_Gate):
    """The +JEV twin: the same actionability rules, then Jev V6 within the decision window."""
    role = "JEV"

    def __init__(self, *, decide: Callable[[Mapping[str, Any]], JevOutcome], model: str,
                 mid: Callable[[str], float | None], spread: Callable[[str], tuple[float | None, str]],
                 policy: JevPolicyV6Config = POLICY_V6, builder: JevStateBuilderV6 | None = None, **k: Any):
        super().__init__(**k)
        self.decide_fn = decide
        self.model = model
        self.mid = mid
        self.spread = spread
        self.policy = policy
        self.builder = builder or JevStateBuilderV6()

    def _attack_mult(self, g: Mapping[str, Any]) -> float:
        d = g["decision"]
        eq = float(g["equity"])
        if eq <= 0 or not getattr(d, "risk_usd", 0):
            return 1.0
        return max(1.0, self.policy.attack_risk_pct * eq / float(d.risk_usd))

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        t = int(g["ts"]) + 1
        attack_only = bool((g.get("sizing") or {}).get("attack_only"))
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        row = self._base(sig, g, t)
        if int(g["ts"]) < self.live_from_ms:            # observed, never recorded: follow the control
            act = "SKIP" if attack_only else "TAKE"
            row.update(final_action=act, final_level=act, reason="NOT_RECORDED", source="rederived", rederived=True)
            return self._verdict(row, 0.0 if attack_only else 1.0)
        block = self._live_block(sig, g, t)
        if block:
            row.update(final_action="SKIP", final_level="SKIP", reason=block, source="rule", decision_ms=self._now())
            return self._verdict(row, 0.0)
        deadline = t + DECISION_WINDOW_MS - JEV_DEADLINE_MARGIN_MS
        state_h, _ = health_state(g["health"])
        eq = float(g["equity"])
        d = g["decision"]
        from app.ai.jev.v2 import _series, health_features_v2
        h = g["health"]
        health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]),
                                    list(g.get("trades") or []), int(g["ts"]))
        half, source = self.spread(self.bot["symbol"])
        position = {"proposed_risk_pct": round(d.risk_usd / eq, 5) if eq > 0 else None,
                    "proposed_leverage": d.leverage,
                    "available_margin_pct": round(g["available"] / eq, 4) if eq > 0 else None}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": half if half is not None else g["half_spread_bps"],
                     "spread_source": source}
        meta = getattr(sig, "meta", None) or {}
        tfs = [meta.get("signal_tf") or self.bot["timeframe"], *(meta.get("context_tfs") or [])]
        snap = self.builder.build(t=t, bot=self.bot, sig=sig, series=_series(g, self.bot["symbol"], tfs),
                                  execution=execution, sizing=g.get("sizing") or {}, health=health, position=position)
        t_candidate = self._now()
        mid_then = self.mid(self.bot["symbol"])
        if t_candidate > deadline - 1000:
            out = JevOutcome(False, error_code="LATE", error_message="no time left in the decision window")
            t_request = t_response = t_candidate
        else:
            t_request = self._now()
            try:
                out = self.decide_fn(dict(snap.state))
            except Exception as exc:                  # a failed call is SKIP, never control behaviour
                out = JevOutcome(False, error_code="CLIENT_ERROR", error_message=type(exc).__name__)
            t_response = self._now()
            if out.ok and t_response > deadline:
                out = JevOutcome(False, error_code="DEADLINE", error_message="answered after the decision window",
                                 latency_ms=out.latency_ms)
        mid_now = self.mid(self.bot["symbol"])
        dec = out.decision if out.ok else None
        pol = decide_v6(dec, state_h, attack_only, self.policy, out.error_code)
        level, mult, why = pol.level, pol.multiplier, (pol.extra or {}).get("downgrade")
        if mult < 0:                                  # ATTACK: 2% risk if the RiskManager accepts it
            mult = self._attack_mult(g)
            if self.probe is not None:
                ok, reason = self.probe(sig, mult)
                if not ok:
                    level, why = ("SKIP", f"ATTACK_NOT_LEGAL:{reason}") if attack_only else ("TAKE", f"ATTACK_NOT_LEGAL:{reason}")
                    mult = 0.0 if attack_only else 1.0
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        row.update({"source": "api", "candidate_wall_ms": t_candidate, "request_start_ms": t_request,
                    "response_ms": t_response, "decision_ms": self._now(),
                    "latency_ms": (t_response - t_request) if out.ok else (out.latency_ms or (t_response - t_request)),
                    "mid_at_candidate": mid_then, "mid_at_response": mid_now,
                    "move_bps": latency_move_bps(sig.side, mid_then, mid_now), "model_requested": self.model,
                    "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V6,
                    "policy_version": self.policy.version, "state_fingerprint": snap.fingerprint,
                    "state_json": json.dumps(dict(snap.state)), "p_support": dec.take_probability if dec else None,
                    "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                    "choice": dec.risk_state if dec else None,
                    "final_action": "SKIP" if level == "SKIP" else "TAKE", "final_level": level,
                    "reason": why or pol.reason, "error_code": out.error_code or None,
                    "timed_out": int(out.error_code in ("TIMEOUT", "DEADLINE", "LATE")),
                    "input_tokens": dec.input_tokens if dec else 0, "output_tokens": dec.output_tokens if dec else 0,
                    "cost_usd": dec.cost_usd if dec else 0.0, "notional": float(getattr(d, "notional", 0.0) or 0.0),
                    "health": state_h})
        return self._verdict(row, 0.0 if level == "SKIP" else float(mult), error=not out.ok)
