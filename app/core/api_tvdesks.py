"""T3-CCI and CHI desks: public, GET-only, read-only routes (summary, historical results, CSV downloads). Paper only."""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api/public/tv")
DESKS = {"t3cci": "tv_t3cci", "chi": "tv_chi"}


def _svc(request: Request, desk: str):
    svc = getattr(request.app.state, DESKS.get(desk, ""), None)
    if svc is None:
        raise HTTPException(404 if desk not in DESKS else 503, f"no desk '{desk}'")
    return svc


@router.get("/{desk}")
async def summary(request: Request, desk: str) -> dict[str, Any]:
    svc = _svc(request, desk)
    out = await asyncio.to_thread(svc.summary)
    out["forward_monthly"] = await asyncio.to_thread(svc.forward_monthly, "execution")
    return out


@router.get("/{desk}/history")
async def history(request: Request, desk: str) -> dict[str, Any]:
    h = _svc(request, desk).history
    if not h:
        return {"status": "MISSING", "historical_only": True}
    books = [{k: v for k, v in b.items() if k != "execution_simulation"} | {
        "execution_simulation": {k: v for k, v in (b.get("execution_simulation") or {}).items() if k != "skips"}
        | {"skips": len((b.get("execution_simulation") or {}).get("skips") or [])}} for b in h["books"]]
    return {"summary": h["summary"], "books": books, "portfolio_monthly": h["portfolio_monthly"], "book_monthly": h.get("book_monthly", {}),
            "historical_only": True}


def _iso(v: Any) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 1_000_000_000_000 < v < 10_000_000_000_000:
        return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, (dict, list)):
        return str(v)
    return v


def _csv(rows: list[dict[str, Any]], name: str) -> Response:
    buf = io.StringIO()
    keys: list[str] = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    w = csv.DictWriter(buf, fieldnames=keys or ["empty"])
    w.writeheader()
    for r in rows:
        w.writerow({k: _iso(v) if k in ("time", "t", "entry_time", "exit_time", "signal_t", "fill_t") or isinstance(v, (dict, list)) else v
                    for k, v in r.items()})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"', "Cache-Control": "no-cache"})


@router.get("/{desk}/csv")
async def download(request: Request, desk: str, section: str = Query("forward", pattern="^(forward|history)$"),
                   kind: str = Query("trades", pattern="^(trades|equity|monthly|skips|divergences|books)$"),
                   book: str = Query("portfolio", max_length=16), mode: str = Query("execution", pattern="^(execution|research)$"),
                   cost: int = Query(1, ge=1, le=2)) -> Response:
    svc = _svc(request, desk)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    if section == "forward":
        try:
            rows = await asyncio.to_thread(svc.forward_rows, kind, mode, book)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return _csv(rows, f"{desk}-forward-{mode}-{kind}-{book}-{stamp}")
    h = svc.history
    if not h:
        raise HTTPException(503, "historical results are not available")
    if kind == "trades":
        rows = [t for t in h["trades"] if book in ("portfolio", "", t["asset"])]
        if cost != 1:
            raise HTTPException(400, "historical trade lists are stored for base costs (cost=1)")
    elif kind == "monthly":
        rows = h["portfolio_monthly"][str(cost)] if book in ("portfolio", "") else (h.get("book_monthly") or {}).get(book, [])
    elif kind == "books":
        rows = [{"asset": b["asset"], "venue": b["venue"], "pair": b["pair"], **{f"{k}_cost{c}": b["metrics"][c][k]
                 for c in ("1", "2") for k in ("net_return_pct", "max_drawdown_pct", "ohlc_drawdown_bound_pct", "trades", "ending_capital", "fees")},
                 **{f"split_{k}": v for k, v in b["splits"].items()},
                 "exec_sim_return_pct": (b.get("execution_simulation") or {}).get("metrics", {}).get("net_return_pct")} for b in h["books"]]
    elif kind == "equity":
        if book in ("portfolio", ""):
            e = h["portfolio_equity"]
            rows = [{"time": t, "equity": a, "high_equity": b, "low_equity": c} for t, a, b, c in zip(e["times"], e["equity"], e["high"], e["low"])]
        else:
            e = (h.get("book_equity") or {}).get(book)
            if e is None:
                raise HTTPException(404, "unknown book")
            rows = ([{"time": t, "equity": a, "high_equity": b, "low_equity": c} for t, a, b, c in zip(e["times"], e["equity"], e["high"], e["low"])]
                    if isinstance(e, dict) else [{"time": t, "equity_daily_close": v} for t, v in e])
    else:
        raise HTTPException(400, "not available for the historical section")
    return _csv(rows, f"{desk}-historical-{kind}-{book}-cost{cost}-{stamp}")
