"""Typed Jev objects: configuration, the versioned question set, the parsed decision, errors.

Wire format verified against OpenRouter's reference on 2026-09-23
(docs/api/api-reference/alphadecisions): `POST https://openrouter.ai/api/alpha/decisions` with
`{model, state, questions}`; each question is `noul`, `choice` or `score` with `instructions` and
`criteria`; the response carries `model` (the RESOLVED version, e.g. typesafe/jev-1.13-20260917),
`answers` keyed like the questions, and `usage {input_tokens, output_tokens, cost}`.

The question text below IS the prompt. It is frozen under PROMPT_VERSION: changing a single word
makes a new version, because a result produced under one wording says nothing about another.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"          # pinned: never jev-latest mid-experiment
PROMPT_VERSION = "JEV_PROMPT_V1"
PRICE_PER_M_INPUT_USD = 0.042                # openrouter.ai/typesafe/jev-1.13, 2026-09-23; usage.cost wins

RISK_STATES = ("ATTACK", "NORMAL", "DEFENSIVE", "SKIP")
REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "ABNORMAL")
QUALITY_LEVELS = 5

# Every way a request can fail, as a stable code. The message beside it is sanitized separately.
ERROR_CODES = ("NOT_CONFIGURED", "DISABLED", "TIMEOUT", "NETWORK", "AUTH", "CREDITS", "RATE_LIMIT",
               "SERVER", "BAD_REQUEST", "MODEL_UNAVAILABLE", "PAYLOAD_TOO_LARGE", "INVALID_JSON",
               "MISSING_ANSWER", "SCHEMA_MISMATCH", "UPSTREAM")
RETRYABLE = frozenset({"TIMEOUT", "NETWORK", "RATE_LIMIT", "SERVER"})

QUESTIONS_V1: dict[str, dict[str, Any]] = {
    "take": {
        "type": "noul",
        "instructions": (
            "A rule-based trading strategy proposes the candidate trade in `signal` for the market in "
            "`bot`. Using only the market, execution-cost, bot-health and position data in this state, "
            "should this candidate trade be accepted?"),
        "criteria": {
            "true": ("Accept: the market context supports the signal's direction, the target is large "
                     "relative to fees, spread and slippage, and the bot's recent health does not argue "
                     "against trading."),
            "false": ("Reject: the context contradicts the signal, costs are large relative to the "
                      "expected move, conditions are abnormal, or recent bot health argues against it."),
        },
    },
    "risk_state": {
        "type": "choice",
        "instructions": "If this candidate is traded, how aggressively should it be sized?",
        "criteria": {
            "ATTACK": ("Unusually favourable: strong alignment between signal and context, costs small "
                       "relative to the target, healthy recent bot performance. Size above normal."),
            "NORMAL": "An ordinary acceptable setup. Standard size.",
            "DEFENSIVE": ("Tradeable but with clear risks: weak alignment, elevated volatility, high "
                          "costs relative to the target, or a recent drawdown. Reduced size."),
            "SKIP": "Should not be traded at all.",
        },
    },
    "setup_quality": {
        "type": "score",
        "instructions": "Rate the quality of this trade setup.",
        "criteria": [
            "Very poor: contradicted by the context or dominated by costs",
            "Weak",
            "Average",
            "Good",
            "Excellent: clean, well aligned, and a large expected move relative to costs",
        ],
    },
    "regime": {
        "type": "choice",
        "instructions": "Which regime best describes the recent price action in `market`?",
        "criteria": {
            "TREND_UP": "A sustained upward trend.",
            "TREND_DOWN": "A sustained downward trend.",
            "RANGE": "Sideways, mean-reverting movement without a clear trend.",
            "HIGH_VOL": "Unusually large or expanding volatility.",
            "ABNORMAL": "Disorderly or anomalous conditions: gaps, extreme moves, thin trading.",
        },
    },
}

SMOKE_QUESTIONS: dict[str, dict[str, Any]] = {
    "ok": {"type": "noul", "instructions": "Is this a connectivity check?",
           "criteria": {"true": "The text says it is a connectivity check.",
                        "false": "The text is about something else."}},
}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def fingerprint(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()[:n]


QUESTIONS_FINGERPRINT = fingerprint({"version": PROMPT_VERSION, "questions": QUESTIONS_V1})


def _flag(v: str | None, default: bool) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v: str | None, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(str(v)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class JevConfig:
    """Non-secret knobs. The key itself is never part of any config object."""
    enabled: bool = False
    model: str = DEFAULT_MODEL
    timeout_ms: int = 4000
    max_retries: int = 2
    backoff_ms: int = 400
    max_concurrency: int = 4
    url: str = DECISIONS_URL
    prompt_version: str = PROMPT_VERSION

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "JevConfig":
        e = os.environ if env is None else env
        return cls(enabled=_flag(e.get("JEV_ENABLED"), False),
                   model=(e.get("JEV_MODEL") or DEFAULT_MODEL).strip(),
                   timeout_ms=_int(e.get("JEV_TIMEOUT_MS"), 4000, 500, 30000),
                   max_retries=_int(e.get("JEV_MAX_RETRIES"), 2, 0, 5))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JevDecision:
    """One typed answer set. Probabilities are what Jev returned; nothing is re-derived."""
    take_probability: float
    risk_state: str
    risk_probabilities: dict[str, float]
    setup_quality: float                  # probability-weighted position scaled to 0..1
    quality_score: float                  # raw 0..4 position as returned
    quality_probabilities: dict[str, float]
    regime: str
    regime_probabilities: dict[str, float]
    model_resolved: str = ""
    provider: str = ""
    request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JevDecision":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[attr-defined]


@dataclass
class JevOutcome:
    """The result of asking, success or not. `decision` is None on any error."""
    ok: bool
    decision: JevDecision | None = None
    error_code: str = ""
    error_message: str = ""
    latency_ms: int = 0
    attempts: int = 0
    http_status: int | None = None
    cache_hit: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "decision": self.decision.to_dict() if self.decision else None,
                "error_code": self.error_code, "error_message": self.error_message,
                "latency_ms": self.latency_ms, "attempts": self.attempts,
                "http_status": self.http_status, "cache_hit": self.cache_hit}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JevOutcome":
        dec = d.get("decision")
        return cls(ok=bool(d.get("ok")), decision=JevDecision.from_dict(dec) if dec else None,
                   error_code=str(d.get("error_code") or ""),
                   error_message=str(d.get("error_message") or ""),
                   latency_ms=int(d.get("latency_ms") or 0), attempts=int(d.get("attempts") or 0),
                   http_status=d.get("http_status"), cache_hit=bool(d.get("cache_hit")))


class SchemaError(ValueError):
    """The response does not match the questions that were asked."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _prob(v: Any, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SchemaError("SCHEMA_MISMATCH", f"{where} is not a number")
    x = float(v)
    if not (0.0 <= x <= 1.0) or x != x:
        raise SchemaError("SCHEMA_MISMATCH", f"{where} outside [0, 1]")
    return x


def _probs(raw: Any, allowed: set[str], where: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise SchemaError("SCHEMA_MISMATCH", f"{where}.probabilities missing")
    out = {}
    for k, v in raw.items():
        if str(k) not in allowed:
            raise SchemaError("SCHEMA_MISMATCH", f"{where}.probabilities has unknown key")
        out[str(k)] = _prob(v, f"{where}.probabilities")
    return out


def parse_decision(body: Mapping[str, Any], questions: Mapping[str, Any] = QUESTIONS_V1) -> JevDecision:
    """Validate a Decisions API response against the questions asked. Strict on purpose: a value
    outside the schema is an error, never something to coerce."""
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise SchemaError("MISSING_ANSWER", "response has no answers object")
    for key, q in questions.items():
        a = answers.get(key)
        if not isinstance(a, dict):
            raise SchemaError("MISSING_ANSWER", f"no answer for {key}")
        if a.get("type") != q["type"]:
            raise SchemaError("SCHEMA_MISMATCH", f"{key} answered as {a.get('type')!r}")
    take = _prob(answers["take"].get("noul"), "take.noul")
    rs = answers["risk_state"]
    if rs.get("choice") not in RISK_STATES:
        raise SchemaError("SCHEMA_MISMATCH", "risk_state.choice not an allowed option")
    rprobs = _probs(rs.get("probabilities"), set(RISK_STATES), "risk_state")
    sq = answers["setup_quality"]
    score = sq.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not (0 <= score <= QUALITY_LEVELS - 1):
        raise SchemaError("SCHEMA_MISMATCH", "setup_quality.score outside the scale")
    qprobs = _probs(sq.get("probabilities"), {str(i) for i in range(QUALITY_LEVELS)}, "setup_quality")
    rg = answers["regime"]
    if rg.get("choice") not in REGIMES:
        raise SchemaError("SCHEMA_MISMATCH", "regime.choice not an allowed option")
    gprobs = _probs(rg.get("probabilities"), set(REGIMES), "regime")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return JevDecision(
        take_probability=take, risk_state=str(rs["choice"]), risk_probabilities=rprobs,
        setup_quality=float(score) / (QUALITY_LEVELS - 1), quality_score=float(score),
        quality_probabilities=qprobs, regime=str(rg["choice"]), regime_probabilities=gprobs,
        model_resolved=str(body.get("model") or ""), provider=str(body.get("provider") or ""),
        request_id=str(body.get("id") or ""),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cost_usd=float(usage.get("cost") or 0.0))
