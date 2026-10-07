"""The one object in PaperLab that holds the OpenRouter key, and the only caller of OpenRouter.

It runs inside the Railway service. The browser never talks to OpenRouter, and the research runner
on the workstation (where the market archive lives) asks for decisions through the authenticated
`POST /api/jev/decide` route -- so the key never leaves the server process.

Blocking HTTP runs in a worker thread under a small semaphore, so a slow or failing upstream can
never stall the event loop that drives the live paper engine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Any, Mapping

from app.ai.jev.client import JevClient
from app.ai.jev.models import PROMPT_VERSION, QUESTIONS_FINGERPRINT, QUESTIONS_V1, JevConfig, JevOutcome
from app.ai.jev.policy import POLICY_V1

log = logging.getLogger("paperlab.jev")

MAX_STATE_BYTES = 16_000
HEALTH_META_KEY = "jev_health"


class JevService:
    def __init__(self, config: JevConfig, api_key: str | None, storage: Any = None,
                 client: JevClient | None = None):
        self.config = config
        self.client = client or JevClient(config, api_key)
        self.storage = storage
        self._sem = asyncio.Semaphore(max(1, config.max_concurrency))
        self.calls = 0
        self.errors = 0
        self.error_codes: dict[str, int] = {}
        self.latencies: deque[int] = deque(maxlen=1000)
        self.last_error: dict[str, Any] | None = None
        self.last_check: dict[str, Any] | None = self._load_check()

    def __repr__(self) -> str:
        return f"JevService({self.client!r})"

    @classmethod
    def from_env(cls, storage: Any = None, env: Mapping[str, str] | None = None) -> "JevService":
        e = os.environ if env is None else env
        return cls(JevConfig.from_env(e), e.get("OPENROUTER_API_KEY"), storage)

    # -- health ------------------------------------------------------------------------------
    def _load_check(self) -> dict[str, Any] | None:
        if self.storage is None:
            return None
        try:
            raw = self.storage.get_meta(HEALTH_META_KEY)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def _save_check(self, check: dict[str, Any]) -> None:
        self.last_check = check
        if self.storage is not None:
            try:
                self.storage.set_meta(HEALTH_META_KEY, json.dumps(check))
            except Exception:
                pass

    def health(self) -> dict[str, Any]:
        """JEV API HEALTH. Non-secret by construction: nothing here can carry the key."""
        base = self.client.status
        check = self.last_check or {}
        if base != "READY":
            status = base
        elif not check:
            status = "UNTESTED"
        elif check.get("ok"):
            rate = self.errors / self.calls if self.calls else 0.0
            status = "OK" if rate < 0.2 else "DEGRADED"
        else:
            status = "ERROR"
        lat = sorted(self.latencies)
        return {
            "status": status, "configured": self.client.configured, "enabled": self.config.enabled,
            "model_requested": self.config.model,
            "model_resolved": check.get("model_resolved") or None,
            "prompt_version": PROMPT_VERSION, "questions_fingerprint": QUESTIONS_FINGERPRINT,
            "policy_version": POLICY_V1.version, "timeout_ms": self.config.timeout_ms,
            "max_retries": self.config.max_retries,
            "last_check": {k: check.get(k) for k in ("ok", "status", "latency_ms", "checked_at",
                                                     "error_code", "http_status", "input_tokens",
                                                     "cost_usd", "model_resolved")} if check else None,
            "calls_since_boot": self.calls, "errors_since_boot": self.errors,
            "error_codes": dict(self.error_codes),
            "latency_p50_ms": lat[len(lat) // 2] if lat else None,
            "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
            "last_error": self.last_error,
        }

    # -- calls ------------------------------------------------------------------------------
    async def smoke(self) -> dict[str, Any]:
        async with self._sem:
            check = await asyncio.to_thread(self.client.smoke)
        self._save_check(check)
        log.info("jev smoke test: %s in %s ms", check.get("status"), check.get("latency_ms"))
        return check

    async def decide(self, state: Any, prompt_version: str, session_id: str | None = None) -> JevOutcome:
        if prompt_version != PROMPT_VERSION:
            return JevOutcome(False, error_code="BAD_REQUEST",
                              error_message=f"server runs {PROMPT_VERSION}, not {prompt_version}")
        if len(json.dumps(state, separators=(",", ":"))) > MAX_STATE_BYTES:
            return JevOutcome(False, error_code="PAYLOAD_TOO_LARGE", error_message="state too large")
        async with self._sem:
            out = await asyncio.to_thread(self.client.decide, state, QUESTIONS_V1, session_id)
        if out.error_code not in ("NOT_CONFIGURED", "DISABLED"):
            self.calls += 1
            self.latencies.append(out.latency_ms)
            if not out.ok:
                self.errors += 1
                self.error_codes[out.error_code] = self.error_codes.get(out.error_code, 0) + 1
                self.last_error = {"code": out.error_code, "at": int(time.time() * 1000),
                                   "http_status": out.http_status}
        return out
