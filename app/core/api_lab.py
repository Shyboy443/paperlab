"""The AI cost analyzer behind the competition pages' "Cost analyzer" view (app/dashboard/lab.js), and the old /lab
address.

- GET /api/analyzer/status, POST /api/analyzer : operator only. They sit outside the auth exemptions, so
  BasicAuthMiddleware demands the dashboard password, and the POST also needs the X-PaperLab: 1 header. The OpenRouter
  key never leaves the server (app/ai/analyzer.py).
- GET /lab, /lab/... : the Lovable-style dashboard briefly lived there as a separate page; it is now part of the
  competition pages, so these addresses redirect to /public/competition.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.ai.analyzer import MAX_CHARS, Analyzer, AnalyzeInputError, AnalyzeRequest

MAX_BODY_BYTES = MAX_CHARS * 4 + 64_000           # worst case: every character escaped by JSON, plus the other fields

router = APIRouter()


def _analyzer(request: Request) -> Analyzer:
    a = getattr(request.app.state, "analyzer", None)
    if a is None:
        a = request.app.state.analyzer = Analyzer.from_env()
    return a


@router.get("/api/analyzer/status")
def analyze_status(request: Request) -> dict[str, Any]:
    return {"ok": True, **_analyzer(request).status()}


@router.post("/api/analyzer")
async def analyze(request: Request) -> Any:
    a = _analyzer(request)
    if not a.configured:
        return JSONResponse({"ok": False, "error": "The analyzer needs OPENROUTER_API_KEY on the server."},
                            status_code=503)
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse({"ok": False, "error": f"Upload a file under {MAX_CHARS:,} characters."}, status_code=413)
    try:
        req = AnalyzeRequest.parse(json.loads(await request.body()))
    except (ValueError, AnalyzeInputError) as exc:
        msg = str(exc) if isinstance(exc, AnalyzeInputError) else "send a JSON body"
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    if not a.acquire():
        return JSONResponse({"ok": False, "error": "Another analysis is still running. Try again when it finishes."},
                            status_code=429)
    return StreamingResponse(a.run(req), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                                      "X-Analyzer-Model": a.model})


@router.api_route("/lab", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/lab/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def lab(rest: str = "") -> Any:
    target = {"bots": "/public/competition/bots", "programs": "/public/competition/programs",
              "analyze": "/public/competition/analyzer"}.get(rest.split("/")[0], "/public/competition")
    return RedirectResponse(target, status_code=307)
