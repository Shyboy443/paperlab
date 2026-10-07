"""Payload builders for the HTTP API: one position row, and the whole /api/state document."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.core import analytics
from app.core.types import VirtualPosition

if TYPE_CHECKING:  # pragma: no cover
    from app.core.engine import Engine


def position_row(e: "Engine", p: VirtualPosition, prices: dict[str, float]) -> dict[str, Any]:
    price = prices.get(p.symbol, p.entry_price)
    net = abs(e.portfolio.net_qty(p.symbol))
    gross = sum(abs(x.qty) for x in e.portfolio.positions_on(p.symbol))
    netted = gross > 0 and net < 0.5 * gross
    return {
        "id": p.id, "strategy_id": p.strategy_id, "symbol": p.symbol, "side": p.side, "qty": p.qty,
        "qty_initial": p.qty_initial, "entry_price": p.entry_price, "entry_ts": p.entry_ts, "stop": p.stop,
        "stop_kind": p.meta.get("stop_kind", "stop"), "take_profits": [[tp.price, tp.fraction] for tp in p.take_profits],
        "lev_virtual": p.leverage, "lev_exchange": e.router.lev(p.symbol), "margin": p.margin,
        "upnl": p.upnl(price), "r": p.r_multiple(price), "liq_paper": p.liq_price(e.exit_engine.mmr(p.symbol)),
        "liq_label": "NETTED" if netted else "PAPER", "age_s": max(0, (e.clock() - p.entry_ts) // 1000),
        "leg": p.leg, "tf": p.tf, "trail": p.trail.kind if p.trail else None, "tp1_done": p.tp1_done,
        "be_done": p.be_done, "max_hold_deadline": p.max_hold_deadline, "simulated": bool(p.meta.get("simulated", True)),
        "notional": p.notional(price),
    }


def state_payload(e: "Engine", curves: bool = False) -> dict[str, Any]:
    prices = e.prices()
    totals = e.portfolio.totals(prices)
    starting = e.settings.paper_total(len(e.strategies))
    risk = e.risk.snapshot(prices)
    day_start = e.day_start_ms()
    boards = {sid: analytics.scoreboard(e, sid) for sid in e.strategies}
    stats = e.portfolio.stats(None, day_start)
    stats["max_dd_pct"] = e.storage.equity_max_drawdown("TOTAL", e.epoch) * 100.0
    stats["wins"] = sum(b["wins"] for b in boards.values())
    stats["losses"] = sum(b["losses"] for b in boards.values())
    stats["trades_open"] = len(e.portfolio.positions)
    ensemble: dict[str, Any] = {}
    s20 = e.strategies.get("S20")
    if s20 is not None:
        try:
            ensemble = s20.state().get("symbols", {}) if isinstance(s20.state(), dict) else {}
        except Exception as exc:
            ensemble = {"error": str(exc)[:120]}
    payload: dict[str, Any] = {
        "ts": e.clock(), "mode": e.settings.mode, "venue_label": e.settings.venue.label,
        "venue_kind": e.settings.venue.caps.kind, "dry_run": e.router.dry_run, "epoch": e.epoch,
        "engine": {"state": e.state, "paused_reason": e.paused_reason, "killed": e.risk.state.killed,
                   "uptime_s": int(time.time() - e.started_at), "symbols": list(e.symbols),
                   "strategies_loaded": len(e.strategies), "warming": e.warming, "boot": e.boot_report},
        "feed": e.feed.status() if e.feed is not None else {"market": {"connected": False, "detail": "no feed"},
                                                             "user": {"connected": False, "detail": "no feed"}},
        "risk": risk,
        "equity": {"total": totals["equity"], "starting": starting, "upnl": totals["upnl"],
                   "realized": totals["realized"], "fees": totals["fees"], "funding": totals["funding"],
                   "pnl": totals["equity"] - starting,
                   "pnl_pct": ((totals["equity"] - starting) / starting * 100.0) if starting else 0.0,
                   "gross_notional": totals["gross_notional"]},
        "exchange_wallet": (e.router.exchange_wallet or None) if not e.router.dry_run else None,
        "stats": stats,
        "prices": {s: {"last": e.last.get(s), "mark": e.marks[s].mark if s in e.marks else None,
                       "funding": e.fundings[s].rate if s in e.fundings else None} for s in e.symbols},
        "strategies": [e.strategy_row(sid, {}, prices, boards[sid]) for sid in sorted(e.strategies)],
        "journal": e.storage.notes(e.epoch, limit=15),
        # Winners = books trading real money; everything else is still in the paper bake-off.
        "live_strategies": sorted(e.router.live_strategies or ()),
        "winners": e.winners(),        # promoted books, in promotion order (WINNER 1, 2, ...)
        "exit_kinds": _exit_mix(e),
        "wallets": {"balance_each": e.settings.strategy_starting_balance, "count": len(e.strategies),
                    "paper_total": starting,
                    "armed": sum(1 for m in e.meta.values() if m.enabled)},
        "positions": {
            "virtual": [position_row(e, p, prices) for p in sorted(e.portfolio.positions.values(), key=lambda p: p.entry_ts)],
            "exchange": [{"symbol": p.symbol, "qty": p.qty, "entry_price": p.entry_price, "liq_price": p.liq_price,
                          "margin": p.margin, "upnl": p.upnl, "lev_exchange": p.leverage, "mark": p.mark,
                          "liq_distance_pct": p.liq_distance_pct()} for p in e.exchange_positions.values() if p.qty],
            "router": e.router.status(list(e.symbols)),
        },
        "ensemble": ensemble,
        "tape": {"fills": [fill_row(f) for f in e.storage.fills_recent(50, epoch=e.epoch)],
                 "signals": [r.to_dict() for r in e.board.recent(50)]},
        "feed_events": list(e.feed_events)[-10:],
        "desync": {k: v for k, v in e.router.desync.items()},
    }
    if curves:
        since = e.clock() - 7 * 86_400_000
        curves_out: dict[str, Any] = {"TOTAL": e.storage.equity_series("TOTAL", e.epoch, since, 400)}
        for sid in e.strategies:
            pts = e.storage.equity_series(sid, e.epoch, since, 300)
            if pts:
                curves_out[sid] = pts
        payload["equity_curves"] = curves_out
    return payload


def fill_row(f: Any) -> dict[str, Any]:
    return {"id": f.id, "ts": f.ts, "strategy_id": f.strategy_id, "symbol": f.symbol, "side": f.side, "qty": f.qty,
            "price": f.price, "fee": f.fee, "slippage_bps": f.slippage_bps, "kind": f.kind, "pnl": f.realized_pnl,
            "simulated": f.simulated, "position_side": f.position_side, "is_open": f.is_open,
            "reason": f.meta.get("reason"), "leg": f.meta.get("leg"), "exchange_order_id": f.exchange_order_id,
            "life": getattr(f, "life", 1)}


_fill_row = fill_row  # legacy alias


def _exit_mix(e: "Engine") -> dict[str, Any]:
    """Exit-kind counts for the epoch plus today's reject codes, so the hold experiment can be read off
    the Overview instead of the tape."""
    kinds = e.storage.exit_kind_counts(e.epoch)
    total = sum(v["n"] for v in kinds.values()) or 1
    rejects = e.storage.reject_reason_counts(e.epoch, None, e.day_start_ms())
    good = kinds.get("tp", {}).get("n", 0) + kinds.get("trail", {}).get("n", 0)
    return {
        "kinds": kinds,
        "total": sum(v["n"] for v in kinds.values()),
        "time_share": kinds.get("time", {}).get("n", 0) / total,
        "target_share": good / total,          # tp + trail: the share we want to rise
        "rejects_today": rejects,
        "fee_gt_r_today": rejects.get("fee_gt_r", 0),
    }
