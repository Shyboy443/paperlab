"""Trading Desk LLM client: OpenRouter chat completions with optional tool calling.

Security, the same rules as the Jev client and the cost analyzer:
- the key comes from the server environment (OPENROUTER_API_KEY) and lives in a closure, never in an attribute, a log
  line, a stored message, a response or an error; upstream error text goes through `sanitize()`;
- only the server calls OpenRouter; the browser never sees the key or the endpoint;
- spending is capped: at most `max_calls_per_day` completions per UTC day (sessions + direct messages together).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.client import sanitize, urllib_transport

URL = "https://openrouter.ai/api/v1/chat/completions"
# Haiku 5.5: tool calling + structured output at ~1/40 of Opus 5.5's price (operator, 2026-10-09: "don't use opus for
# the desk, it costs a lot"). DESK_MODEL overrides it.
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
STATUS = {401: "OpenRouter rejected the server's key.", 402: "Out of OpenRouter credits.",
          404: "The desk model is not available on OpenRouter (check DESK_MODEL).", 429: "OpenRouter is rate-limiting; try later."}

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]
ToolRunner = Callable[[str, dict[str, Any]], Any]


class LLMError(RuntimeError):
    pass


class DeskLLM:
    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL, max_tokens: int = 1200,
                 max_calls_per_day: int = 120, timeout_s: float = 120.0, transport: Transport | None = None,
                 clock: Callable[[], float] = time.time):
        """The usage counters can be persisted with `attach_store` so a restart does not reset the daily cap."""
        key = (api_key or "").strip()
        self._secret: Callable[[], str] = lambda: key
        self.model = model
        self.max_tokens = int(max_tokens)
        self.max_calls_per_day = int(max_calls_per_day)
        self.timeout_s = timeout_s
        self._transport = transport or urllib_transport
        self._clock = clock
        self._lock = threading.Lock()
        self._day = -1
        self.calls_today = 0
        self.cost_today = 0.0
        self.cost_total = 0.0
        self._save: Callable[[dict[str, Any]], None] | None = None

    def attach_store(self, load: Callable[[], Any], save: Callable[[dict[str, Any]], None]) -> None:
        """Restore today's usage from `load()` and write it back through `save` after every call."""
        d = load() or {}
        with self._lock:
            self._day = int(d.get("day", -1))
            self.calls_today = int(d.get("calls_today", 0))
            self.cost_today = float(d.get("cost_today", 0.0))
            self.cost_total = float(d.get("cost_total", 0.0))
        self._save = save

    def _persist(self) -> None:
        if self._save:
            with self._lock:
                snap = {"day": self._day, "calls_today": self.calls_today, "cost_today": self.cost_today,
                        "cost_total": self.cost_total}
            try:
                self._save(snap)
            except Exception:
                pass

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeskLLM":
        env = os.environ if env is None else env
        return cls(env.get("OPENROUTER_API_KEY"), (env.get("DESK_MODEL") or DEFAULT_MODEL).strip(),
                   int(env.get("DESK_MAX_TOKENS") or 1200), int(env.get("DESK_MAX_CALLS_PER_DAY") or 120))

    def __repr__(self) -> str:
        return f"DeskLLM(model={self.model!r}, configured={self.configured})"

    __str__ = __repr__

    @property
    def configured(self) -> bool:
        return bool(self._secret())

    def health(self) -> dict[str, Any]:
        self._roll()
        return {"configured": self.configured, "model": self.model, "calls_today": self.calls_today,
                "max_calls_per_day": self.max_calls_per_day, "cost_today_usd": round(self.cost_today, 4),
                "cost_total_usd": round(self.cost_total, 4)}

    def _roll(self) -> None:
        day = int(self._clock() // 86400)
        with self._lock:
            if day != self._day:
                self._day, self.calls_today, self.cost_today = day, 0, 0.0

    def _reserve(self) -> None:
        self._roll()
        with self._lock:
            if self.calls_today >= self.max_calls_per_day:
                raise LLMError(f"daily AI budget used ({self.max_calls_per_day} calls); the desk resumes tomorrow (UTC)")
            self.calls_today += 1
        self._persist()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise LLMError("OPENROUTER_API_KEY is not set on the server")
        self._reserve()
        headers = {"Authorization": "Bearer " + self._secret(), "Content-Type": "application/json",
                   "HTTP-Referer": "https://paperlab.local/desk", "X-Title": "PaperLab Trading Desk"}
        try:
            status, _, raw = self._transport(URL, json.dumps(payload).encode(), headers, self.timeout_s)
        except Exception as exc:
            raise LLMError("OpenRouter unreachable: " + sanitize(exc, self._secret())) from None
        if status != 200:
            detail = ""
            try:
                detail = (json.loads(raw).get("error") or {}).get("message") or ""
            except Exception:
                pass
            raise LLMError(STATUS.get(status, f"OpenRouter HTTP {status}") + (" " + sanitize(detail, self._secret())
                                                                             if detail else ""))
        try:
            data = json.loads(raw)
        except ValueError:
            raise LLMError("OpenRouter sent an unreadable answer") from None
        if data.get("error"):
            raise LLMError("OpenRouter: " + sanitize((data["error"] or {}).get("message"), self._secret()))
        cost = float(((data.get("usage") or {}).get("cost")) or 0.0)
        with self._lock:
            self.cost_today += cost
            self.cost_total += cost
        self._persist()
        return data

    def chat(self, system: str, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None = None,
             run_tool: ToolRunner | None = None, max_rounds: int = 3, json_mode: bool = False,
             max_tokens: int | None = None) -> dict[str, Any]:
        """One agent turn. With tools, tool calls are executed by `run_tool` and fed back for up to `max_rounds`.
        Returns {"text", "tool_calls": [{name, args}], "cost_usd"}."""
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
        used, cost0 = [], self.cost_total
        for _ in range(max_rounds + 1):
            payload: dict[str, Any] = {"model": self.model, "messages": msgs, "max_tokens": max_tokens or self.max_tokens,
                                       "temperature": 0.3, "usage": {"include": True}}
            if tools:
                payload["tools"] = list(tools)
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            data = self._post(payload)
            choice = ((data.get("choices") or [{}])[0]).get("message") or {}
            calls = choice.get("tool_calls") or []
            if not calls or not run_tool:
                return {"text": (choice.get("content") or "").strip(), "tool_calls": used,
                        "cost_usd": round(self.cost_total - cost0, 5)}
            msgs.append({"role": "assistant", "content": choice.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                try:
                    result = run_tool(str(fn.get("name")), args if isinstance(args, dict) else {})
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                used.append({"name": fn.get("name"), "args": args})
                msgs.append({"role": "tool", "tool_call_id": call.get("id"),
                             "content": json.dumps(result, default=str)[:12000]})
        return {"text": "(stopped after too many tool calls)", "tool_calls": used,
                "cost_usd": round(self.cost_total - cost0, 5)}
