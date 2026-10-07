"""The /lab dashboard (the UI ported from the Lovable export, built from lab_ui/ into app/dashboard/lab/) and its one
private API: the cost analyzer.

- GET /lab, /lab/... : the single-page app shell. It carries no data and is served without a password, like the
  public inspection page; every number it shows comes from the read-only /api/public/* feeds.
- GET /api/lab/analyze/status, POST /api/lab/analyze : operator only. They sit outside the auth exemptions, so
  BasicAuthMiddleware demands the dashboard password, and the POST also needs the X-PaperLab: 1 header. The
  OpenRouter key never leaves the server (app/ai/analyzer.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.ai.analyzer import MAX_CHARS, Analyzer, AnalyzeInputError, AnalyzeRequest

LAB_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "lab"
MAX_BODY_BYTES = MAX_CHARS * 4 + 64_000           # worst case: every character escaped by JSON, plus the other fields

router = APIRouter()


def _analyzer(request: Request) -> Analyzer:
    a = getattr(request.app.state, "analyzer", None)
    if a is None:
        a = request.app.state.analyzer = Analyzer.from_env()
    return a


@router.get("/api/lab/analyze/status")
def analyze_status(request: Request) -> dict[str, Any]:
    return {"ok": True, **_analyzer(request).status()}


@router.post("/api/lab/analyze")
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


def lab_page() -> Any:
    page = LAB_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>PaperLab</h1><p>The lab dashboard is not built. Run <code>npm run build</code> in "
                            "lab_ui/.</p>", status_code=404)
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.api_route("/lab", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/lab/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def lab(rest: str = "") -> Any:
    """Every client-side route (/lab/bots, /lab/bots/v6--KEY, /lab/programs, /lab/analyze) serves the same shell."""
    return lab_page()
