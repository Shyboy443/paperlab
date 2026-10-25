"""Legacy /lab addresses redirect to the competition dashboard."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.api_route("/lab", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/lab/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def lab(rest: str = "") -> RedirectResponse:
    target = {"bots": "/public/competition/bots"}.get(
        rest.split("/")[0], "/public/competition")
    return RedirectResponse(target, status_code=307)
