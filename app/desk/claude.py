"""Trading Desk on Anthropic's Claude API directly (the operator's own key), with the same interface, budget and
persistence as the OpenRouter client in app/desk/llm.py.

- Key: ANTHROPIC_API_KEY from the server environment only, held in a closure; it never reaches a log line, a stored
  message, a response or the browser, and error text goes through `sanitize()`.
- Models: DESK_MODEL (default claude-haiku-5-5) for Scout / News / Sentiment / the Chief's summary, DESK_MODEL_DEEP
  (default claude-sonnet-5-5) for Charts, the Skeptic and operator chats.
- Spend: the daily call cap and a daily USD cap (DESK_MAX_USD_PER_DAY, default 4) -- cost is computed from the token
  usage Anthropic reports, at the published per-million-token prices below.
- Tool loops append the assistant's content blocks unchanged (thinking blocks included), as the API requires.

`make_llm(env)` picks this client when ANTHROPIC_API_KEY is set, otherwise the OpenRouter one.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.client import sanitize
from app.desk.llm import DeskLLM, LLMError, ToolRunner, complete_sentences

# USD per million tokens: (input, output). Thinking tokens are billed as output.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
DEFAULT_FAST = "claude-haiku-5-5"
DEFAULT_DEEP = "claude-sonnet-5-5"


def to_claude_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-style function tools -> Anthropic tool definitions."""
    out = []
    for t in tools:
        f = t.get("function") or t
        out.append({"name": f["name"], "description": f.get("description", ""),
                    "input_schema": f.get("parameters") or {"type": "object", "properties": {}}})
    return out


def to_claude_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Plain {role, content: str} turns -> Claude turns: starts with the user, roles alternate (same-role text merged)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role and isinstance(out[-1]["content"], str):
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out or [{"role": "user", "content": "(no message)"}]


class ClaudeLLM(DeskLLM):
    provider = "anthropic"

    def __init__(self, api_key: str | None, model: str = DEFAULT_FAST, model_deep: str = DEFAULT_DEEP, **kw: Any):
        self._client_factory: Callable[[str], Any] | None = kw.pop("client_factory", None)
        super().__init__(api_key, model=model, model_deep=model_deep, **kw)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClaudeLLM":
        env = os.environ if env is None else env
        return cls(env.get("ANTHROPIC_API_KEY"), (env.get("DESK_MODEL") or DEFAULT_FAST).strip(),
                   (env.get("DESK_MODEL_DEEP") or DEFAULT_DEEP).strip(), max_tokens=int(env.get("DESK_MAX_TOKENS") or 2000),
                   max_calls_per_day=int(env.get("DESK_MAX_CALLS_PER_DAY") or 400),
                   max_usd_per_day=float(env.get("DESK_MAX_USD_PER_DAY") or 4.0))

    def __repr__(self) -> str:
        return f"ClaudeLLM(model={self.model!r}, deep={self.model_deep!r}, configured={self.configured})"

    __str__ = __repr__

    def _client(self) -> Any:
        if self._client_factory:
            return self._client_factory(self._secret())
        import anthropic
        return anthropic.Anthropic(api_key=self._secret(), max_retries=2, timeout=self.timeout_s)

    def _create(self, **params: Any) -> Any:
        if not self.configured:
            raise LLMError("ANTHROPIC_API_KEY is not set on the server")
        self._reserve()
        try:
            resp = self._client().messages.create(**params)
        except Exception as exc:                       # never let the key or a raw trace escape
            status = getattr(exc, "status_code", None)
            msg = {401: "Anthropic rejected the server's key.", 403: "Anthropic refused this request.",
                   404: "That Claude model is not available to this key.", 429: "Anthropic is rate-limiting; try later.",
                   529: "Anthropic is overloaded; try later."}.get(status)
            raise LLMError(msg or ("Anthropic error: " + sanitize(exc, self._secret()))) from None
        u = getattr(resp, "usage", None)
        pin, pout = PRICES.get(params["model"], PRICES[DEFAULT_DEEP])
        if u is not None:
            tokens_in = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) * 1.25 \
                + (getattr(u, "cache_read_input_tokens", 0) or 0) * 0.1
            cost = (tokens_in * pin + (getattr(u, "output_tokens", 0) or 0) * pout) / 1e6
            with self._lock:
                self.cost_today += cost
                self.cost_total += cost
            self._persist()
        return resp

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()

    def chat(self, system: str, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None = None,
             run_tool: ToolRunner | None = None, max_rounds: int = 3, json_mode: bool = False,
             max_tokens: int | None = None, deep: bool = False) -> dict[str, Any]:
        model = self.model_deep if deep else self.model
        # thinking (on by default for these models) counts toward max_tokens, so leave room for it
        params: dict[str, Any] = {"model": model, "system": system, "max_tokens": max(8000, max_tokens or self.max_tokens),
                                  "messages": to_claude_messages(messages)}
        if tools:
            params["tools"] = to_claude_tools(tools)
        if json_mode:
            params["system"] = system + "\n\nAnswer with the JSON object only."
        used, cost0, continued = [], self.cost_total, 0
        for _ in range(max_rounds + 1):
            resp = self._create(**params)
            stop = getattr(resp, "stop_reason", None)
            if stop == "refusal":
                return {"text": "(the model declined to answer this)", "tool_calls": used,
                        "cost_usd": round(self.cost_total - cost0, 5)}
            calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if stop == "tool_use" and calls and run_tool:
                params["messages"] = params["messages"] + [{"role": "assistant", "content": resp.content}]
                results = []
                for b in calls:
                    try:
                        out = run_tool(b.name, dict(b.input or {}))
                    except Exception as exc:
                        out = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                    used.append({"name": b.name, "args": dict(b.input or {})})
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)[:12000]})
                params["messages"] = params["messages"] + [{"role": "user", "content": results}]
                continue
            text = self._text(resp)
            if stop == "max_tokens" and continued < 1:
                continued += 1
                params["messages"] = params["messages"] + [{"role": "assistant", "content": resp.content},
                                                           {"role": "user", "content": "Continue exactly where you stopped."}]
                more = self._create(**params)
                text = (text + " " + self._text(more)).strip()
                if getattr(more, "stop_reason", None) == "max_tokens":
                    text = complete_sentences(text)
            return {"text": text, "tool_calls": used, "cost_usd": round(self.cost_total - cost0, 5)}
        return {"text": "(stopped after too many tool calls)", "tool_calls": used, "cost_usd": round(self.cost_total - cost0, 5)}


def make_llm(env: Mapping[str, str] | None = None) -> DeskLLM:
    """Claude directly when the operator's ANTHROPIC_API_KEY is set, otherwise OpenRouter."""
    env = os.environ if env is None else env
    if (env.get("ANTHROPIC_API_KEY") or "").strip():
        return ClaudeLLM.from_env(env)
    return DeskLLM.from_env(env)
