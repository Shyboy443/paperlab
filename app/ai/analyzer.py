"""Cost analyzer: an uploaded backtest or trade history goes to an LLM, which explains where fees, spread and funding
eat the edge and proposes testable changes. Ported from the Lovable dashboard, where it ran on Lovable's AI gateway;
here it uses PaperLab's own OpenRouter key.

Security, the same rules as the Jev client (app/ai/jev/client.py):
- the key comes from the server environment (OPENROUTER_API_KEY) and is held in a closure, never in an attribute, a
  log line, a response or an error message; upstream error text goes through `sanitize()`;
- the route is private (Basic auth + X-PaperLab header, app/core/api_lab.py). The browser never talks to OpenRouter;
- one analysis at a time, so a stuck tab cannot run up a bill. The slot is a lease: if a closed tab leaves a
  stream unfinished, the slot frees itself after LEASE_S;
- the uploaded text is neither stored nor logged.

The answer streams back as plain text (Markdown). A failure after streaming has started is appended as
"\\n\\n[[ERROR]] <message>", the protocol the Lovable page already reads.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Mapping

from app.ai.jev.client import sanitize

log = logging.getLogger("paperlab.analyzer")

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
MAX_CHARS = 250_000
LEASE_S = 900.0
ERROR_MARK = "[[ERROR]]"

INSTRUCTIONS = """You are a quantitative trading-research reviewer for PaperLab, a lab that forward-tests pre-registered, frozen trading bots with realistic costs.
The user uploads a bot's backtest or trade history (CSV, JSON or text). Analyse ONLY what the data supports; never invent numbers. If a field is missing, say so.

Write the answer in Markdown with exactly these sections:
## Summary
3-5 bullets: trade count, period, gross vs net result, and the single biggest leak.
## Where the money goes
A table: Cost item | Total | % of gross profit | Per trade. Cover fees, spread, slippage, funding when present. Then state whether the bot is profitable BEFORE costs, and how much of the edge costs consume.
## Patterns
Short bullets on exit types, holding time, win/loss size, side, symbol or time-of-day effects that the data actually shows.
## Testable changes
3-6 numbered hypotheses. Each: the change, why the data suggests it, the expected effect on costs/edge, and how to test it as a NEW pre-registered experiment (fixed rules, minimum trade count, pass/fail metric). Prefer cost-reducing changes (fewer trades, larger targets, maker/limit entries, higher-timeframe filters, wider stops vs. noise).
## Caveats
Sample size, overfitting risk, and that results are not financial advice.

Never promise profits. Never suggest changing a frozen experiment in place."""

# (url, body, headers, timeout_s) -> (status, raw lines). A non-200 status carries the error body as its lines.
Stream = Callable[[str, bytes, Mapping[str, str], float], tuple[int, Iterable[bytes]]]

STATUS_MESSAGES = {
    401: "OpenRouter rejected the server's key.",
    402: "Out of OpenRouter credits.",
    403: "OpenRouter refused this request.",
    404: "The analyzer model is not available on OpenRouter (check ANALYZER_MODEL).",
    408: "OpenRouter timed out.",
    413: "The file is too large for the model.",
    429: "The AI is busy right now. Try again in a minute.",
}


class AnalyzeInputError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyzeRequest:
    file_name: str
    content: str
    bot_context: str = ""
    notes: str = ""

    @classmethod
    def parse(cls, body: Any) -> "AnalyzeRequest":
        if not isinstance(body, dict):
            raise AnalyzeInputError("send a JSON object")

        def text(name: str, limit: int, required: bool = False) -> str:
            v = body.get(name)
            if v is None:
                v = ""
            if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
                raise AnalyzeInputError(f"Upload a non-empty file under {MAX_CHARS:,} characters." if name == "content"
                                        else f"'{name}' must be text under {limit:,} characters")
            return v
        return cls(text("fileName", 200, True), text("content", MAX_CHARS, True), text("botContext", 4000),
                   text("notes", 2000))

    def prompt(self) -> str:
        parts = [f"Live dashboard stats for this bot:\n{self.bot_context}" if self.bot_context else "",
                 f"Operator notes: {self.notes}" if self.notes else "",
                 f"File: {self.file_name}\n```\n{self.content}\n```"]
        return "\n\n".join(p for p in parts if p)


def urllib_stream(url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> tuple[int, Iterable[bytes]]:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
    except urllib.error.HTTPError as e:
        return e.code, [e.read() or b""]

    def lines() -> Iterator[bytes]:
        with resp:
            yield from resp
    return resp.status, lines()


class Analyzer:
    def __init__(self, api_key: str | None, model: str = DEFAULT_MODEL, max_tokens: int = 16000,
                 timeout_s: float = 180.0, stream: Stream | None = None, clock: Callable[[], float] = time.monotonic):
        key = (api_key or "").strip()
        self._secret: Callable[[], str] = lambda: key        # a closure: never in vars(), repr() or a traceback
        self.model = model
        self.max_tokens = int(max_tokens)
        self.timeout_s = float(timeout_s)
        self._stream = stream or urllib_stream
        self._clock = clock
        self._guard = threading.Lock()
        self._lease: float | None = None                  # clock() when the running analysis started

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Analyzer":
        env = os.environ if env is None else env
        return cls(env.get("OPENROUTER_API_KEY"), (env.get("ANALYZER_MODEL") or DEFAULT_MODEL).strip(),
                   int(env.get("ANALYZER_MAX_TOKENS") or 16000))

    def __repr__(self) -> str:
        return f"Analyzer(model={self.model!r}, configured={self.configured})"

    __str__ = __repr__

    @property
    def configured(self) -> bool:
        return bool(self._secret())

    @property
    def busy(self) -> bool:
        with self._guard:
            return self._held()

    def _held(self) -> bool:
        return self._lease is not None and self._clock() - self._lease < LEASE_S

    def status(self) -> dict[str, Any]:
        return {"configured": self.configured, "model": self.model, "busy": self.busy, "max_chars": MAX_CHARS}

    def _clean(self, text: Any) -> str:
        return sanitize(text, self._secret())

    def acquire(self) -> bool:
        """Claim the single analysis slot. The caller must hand the slot to run() or release it."""
        with self._guard:
            if self._held():
                return False
            self._lease = self._clock()
            return True

    def release(self) -> None:
        with self._guard:
            self._lease = None

    def run(self, req: AnalyzeRequest) -> Iterator[str]:
        """Yield the answer as text chunks. Never raises: a failure becomes a final "[[ERROR]] ..." chunk.
        Releases the slot taken by acquire() when the generator finishes or is closed."""
        try:
            yield from self._run(req)
        finally:
            self.release()

    def _run(self, req: AnalyzeRequest) -> Iterator[str]:
        body = json.dumps({
            "model": self.model, "stream": True, "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": req.prompt()}],
        }).encode()
        headers = {"Authorization": "Bearer " + self._secret(), "Content-Type": "application/json",
                   "Accept": "text/event-stream", "X-Title": "PaperLab cost analyzer"}
        wrote = False
        try:
            status, lines = self._stream(URL, body, headers, self.timeout_s)
            if status != 200:
                detail = self._clean(b"".join(lines).decode("utf-8", "replace"))
                log.warning("analyzer: OpenRouter http %s: %s", status, detail)
                yield f"\n\n{ERROR_MARK} " + STATUS_MESSAGES.get(status, f"The analysis failed (OpenRouter {status}).")
                return
            for raw in lines:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue                                    # SSE comments (": OPENROUTER PROCESSING") and blanks
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("error"):
                    err = event["error"]
                    log.warning("analyzer: stream error: %s", self._clean(err.get("message") if isinstance(err, dict)
                                                                          else err))
                    yield f"\n\n{ERROR_MARK} The model stopped with an error. Try again with a smaller file."
                    return
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        wrote = True
                        yield piece
            if not wrote:
                yield f"\n\n{ERROR_MARK} The AI returned no answer for this file."
        except (socket.timeout, TimeoutError):
            yield f"\n\n{ERROR_MARK} OpenRouter timed out. Try again with a smaller file."
        except Exception as exc:                          # network, broken stream: never leak details or the key
            log.warning("analyzer: %s: %s", type(exc).__name__, self._clean(exc))
            yield f"\n\n{ERROR_MARK} The analysis failed. Try again in a minute."
