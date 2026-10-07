"""Server-side OpenRouter client for Jev's Decisions API.

Security rules this file exists to keep:

* The key comes from the server process environment (OPENROUTER_API_KEY) and lives only in a
  private attribute. It is not in any config object, repr, log line, stored row, error message or
  response. Error text from upstream is passed through `sanitize()` before it goes anywhere.
* Only this process talks to OpenRouter. Browsers and the local research runner reach Jev through
  the authenticated `/api/jev/decide` route, never with the key.
* JEV_ENABLED=false means no request is ever made; a missing key means NOT_CONFIGURED. Neither
  stops PaperLab from booting.

Failure handling is bounded: short timeouts, a few retries for transient errors (timeout, network,
429, 5xx) with exponential backoff that honours Retry-After up to a cap, and no retry for anything
that cannot succeed by repeating (400, 401, 402, 403, 404, 413, invalid or mismatched answers).
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from app.ai.jev.models import (QUESTIONS_V1, RETRYABLE, SMOKE_QUESTIONS, JevConfig, JevOutcome,
                               SchemaError, parse_decision)

log = logging.getLogger("paperlab.jev")

# (url, body, headers, timeout_s) -> (status, headers, body). Raises on network failure.
Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]

_TOKENISH = re.compile(r"(sk-or-[A-Za-z0-9_\-]{6,}|(?:Bearer|Basic)\s+[A-Za-z0-9._\-+/=]+|"
                       r"[Aa]uthorization[\"':\s=]+(?:(?:Bearer|Basic)\s+)?[^\s,}\"']+)")
STATUS_CODES = {400: "BAD_REQUEST", 401: "AUTH", 402: "CREDITS", 403: "AUTH",
                404: "MODEL_UNAVAILABLE", 413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMIT",
                500: "SERVER", 502: "SERVER", 503: "SERVER", 524: "SERVER", 529: "SERVER"}


def sanitize(text: Any, secret: str = "", limit: int = 200) -> str:
    """Anything that may leave this module as text goes through here first."""
    s = str(text or "")
    if secret and len(secret) >= 8:
        s = s.replace(secret, "***")
    s = _TOKENISH.sub("***", s)
    return s[:limit]


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str],
                     timeout_s: float) -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:            # a real HTTP status: let the caller classify it
        return e.code, dict(e.headers.items()) if e.headers else {}, e.read() or b""


class JevClient:
    def __init__(self, config: JevConfig, api_key: str | None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        # Held in a closure, not an attribute: vars(client), a debugger's object dump or an
        # accidental serialisation of __dict__ shows a function, never the key string.
        key = (api_key or "").strip()
        self._secret: Callable[[], str] = lambda: key
        self._transport = transport or urllib_transport
        self._sleep = sleep
        self._clock = clock

    def __repr__(self) -> str:                    # the key must never appear, even in a traceback
        return (f"JevClient(model={self.config.model!r}, enabled={self.config.enabled}, "
                f"configured={self.configured})")

    __str__ = __repr__

    @property
    def configured(self) -> bool:
        return bool(self._secret())

    @property
    def status(self) -> str:
        if not self.configured:
            return "NOT_CONFIGURED"
        if not self.config.enabled:
            return "DISABLED"
        return "READY"

    def _clean(self, text: Any) -> str:
        return sanitize(text, self._secret())

    # -- the one call --------------------------------------------------------------------------
    def decide(self, state: Any, questions: Mapping[str, Any] = QUESTIONS_V1,
               session_id: str | None = None, parse: bool = True) -> JevOutcome:
        """Ask the questions about `state`. Never raises; every failure is a coded outcome."""
        if not self.configured:
            return JevOutcome(False, error_code="NOT_CONFIGURED",
                              error_message="OPENROUTER_API_KEY is not set on the server")
        if not self.config.enabled:
            return JevOutcome(False, error_code="DISABLED", error_message="JEV_ENABLED is false")
        payload: dict[str, Any] = {"model": self.config.model, "state": state,
                                   "questions": dict(questions)}
        if session_id:
            payload["session_id"] = str(session_id)[:256]
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Authorization": "Bearer " + self._secret(), "Content-Type": "application/json",
                   "X-Title": "PaperLab"}
        t0 = self._clock()
        attempts, last = 0, JevOutcome(False, error_code="NETWORK")
        for attempt in range(1, self.config.max_retries + 2):
            attempts = attempt
            last = self._once(body, headers, questions, parse)
            if last.ok or last.error_code not in RETRYABLE or attempt > self.config.max_retries:
                break
            wait = self.config.backoff_ms / 1000.0 * (2 ** (attempt - 1))
            retry_after = last.extra.get("retry_after_s")
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                wait = max(wait, min(float(retry_after), 10.0))
            self._sleep(wait)
        last.attempts = attempts
        last.latency_ms = int((self._clock() - t0) * 1000)
        if not last.ok:
            log.warning("jev request failed: %s (http %s, %d attempts)", last.error_code,
                        last.http_status, attempts)
        return last

    def _once(self, body: bytes, headers: Mapping[str, str], questions: Mapping[str, Any],
              parse: bool) -> JevOutcome:
        timeout_s = self.config.timeout_ms / 1000.0
        try:
            status, rh, raw = self._transport(self.config.url, body, headers, timeout_s)
        except (socket.timeout, TimeoutError):
            return JevOutcome(False, error_code="TIMEOUT", error_message="request timed out")
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                return JevOutcome(False, error_code="TIMEOUT", error_message="request timed out")
            return JevOutcome(False, error_code="NETWORK", error_message=self._clean(e.reason))
        except OSError as e:
            return JevOutcome(False, error_code="NETWORK", error_message=self._clean(type(e).__name__))
        if status != 200:
            code = STATUS_CODES.get(status, "SERVER" if status >= 500 else "UPSTREAM")
            msg = ""
            try:
                msg = str((json.loads(raw or b"{}").get("error") or {}).get("message") or "")
            except (ValueError, AttributeError):
                msg = ""
            out = JevOutcome(False, error_code=code, http_status=status,
                             error_message=self._clean(msg or f"HTTP {status}"))
            ra = {k.lower(): v for k, v in (rh or {}).items()}.get("retry-after")
            try:
                out.extra["retry_after_s"] = float(ra) if ra is not None else None
            except (TypeError, ValueError):
                pass
            return out
        try:
            data = json.loads(raw)
        except ValueError:
            return JevOutcome(False, error_code="INVALID_JSON", http_status=status,
                              error_message="response was not JSON")
        if not isinstance(data, dict):
            return JevOutcome(False, error_code="INVALID_JSON", http_status=status,
                              error_message="response was not a JSON object")
        if not parse:
            return JevOutcome(True, http_status=status, extra={"raw": data})
        try:
            decision = parse_decision(data, questions)
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, http_status=status,
                              error_message=self._clean(str(e)))
        return JevOutcome(True, decision=decision, http_status=status)

    def smoke(self) -> dict[str, Any]:
        """One minimal authenticated request. Returns only non-secret facts about it."""
        out = self.decide({"text": "PaperLab connectivity check."}, SMOKE_QUESTIONS, parse=False)
        raw = out.extra.get("raw") or {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        answer = ((raw.get("answers") or {}).get("ok") or {}) if isinstance(raw, dict) else {}
        return {"ok": out.ok and answer.get("type") == "noul",
                "status": "OK" if out.ok else out.error_code,
                "model_requested": self.config.model,
                "model_resolved": str(raw.get("model") or ""), "provider": str(raw.get("provider") or ""),
                "latency_ms": out.latency_ms, "attempts": out.attempts, "http_status": out.http_status,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cost_usd": float(usage.get("cost") or 0.0),
                "answer_type": answer.get("type"), "error_code": out.error_code,
                "error_message": out.error_message, "checked_at": int(time.time() * 1000)}
