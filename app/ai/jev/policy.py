"""Deterministic translation of a Jev decision into a sizing action.

Jev's typed output never reaches an order directly. This module turns it into one of

    SKIP       0.00 x   the candidate is not traded (a shadow copy is still simulated)
    DEFENSIVE  0.50 x   REDUCE
    NORMAL     1.00 x   TAKE
    ATTACK     1.50 x   TAKE, sized up -- still capped by the bot's hard risk ceiling

and the RiskManager then approves, reduces or rejects the resulting order as it would any other.

Two readings of the answer are combined, and the more cautious one wins:

* the take probability sets the most aggressive level allowed (thresholds below), and
* Jev's own risk_state can only LOWER that level, never raise it.

A request that failed (timeout, 401, 429, 5xx, bad schema, disabled, not configured) is SKIP. It is
never quietly replaced by control behaviour: a +JEV bot that trades like its control whenever the
API is down would contaminate the very comparison the experiment exists for.

Any change to thresholds or multipliers is a new POLICY version. V1 is never edited after results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.ai.jev.models import JevDecision, fingerprint

LEVEL_ORDER = {"SKIP": 0, "DEFENSIVE": 1, "NORMAL": 2, "ATTACK": 3}
ACTION_OF = {"SKIP": "SKIP", "DEFENSIVE": "REDUCE", "NORMAL": "TAKE", "ATTACK": "TAKE"}


@dataclass(frozen=True)
class JevPolicyConfig:
    version: str = "JEV_POLICY_V1"
    skip_below: float = 0.55          # take_probability <  0.55 -> SKIP
    defensive_below: float = 0.70     # 0.55 .. 0.70            -> DEFENSIVE
    normal_below: float = 0.85        # 0.70 .. 0.85            -> NORMAL; >= 0.85 ATTACK eligible
    multipliers: tuple[tuple[str, float], ...] = (("SKIP", 0.0), ("DEFENSIVE", 0.5),
                                                  ("NORMAL", 1.0), ("ATTACK", 1.5))
    on_error: str = "SKIP"

    def multiplier(self, level: str) -> float:
        return dict(self.multipliers)[level]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["multipliers"] = dict(self.multipliers)
        return d

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V1 = JevPolicyConfig()


@dataclass
class PolicyResult:
    action: str                 # TAKE / REDUCE / SKIP
    level: str                  # ATTACK / NORMAL / DEFENSIVE / SKIP
    multiplier: float
    by_probability: str = ""
    by_risk_state: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def level_for_probability(p: float, cfg: JevPolicyConfig = POLICY_V1) -> str:
    if p < cfg.skip_below:
        return "SKIP"
    if p < cfg.defensive_below:
        return "DEFENSIVE"
    if p < cfg.normal_below:
        return "NORMAL"
    return "ATTACK"


def decide(decision: JevDecision | None, cfg: JevPolicyConfig = POLICY_V1,
           error_code: str = "") -> PolicyResult:
    if decision is None:
        level = cfg.on_error
        return PolicyResult(ACTION_OF[level], level, cfg.multiplier(level),
                            reason=f"JEV_ERROR {error_code or 'unknown'}")
    by_p = level_for_probability(decision.take_probability, cfg)
    by_rs = decision.risk_state if decision.risk_state in LEVEL_ORDER else "SKIP"
    level = min(by_p, by_rs, key=lambda x: LEVEL_ORDER[x])
    why = (f"take_probability {decision.take_probability:.3f} -> {by_p}; "
           f"risk_state {by_rs}; final {level}")
    return PolicyResult(ACTION_OF[level], level, cfg.multiplier(level), by_p, by_rs, why)
