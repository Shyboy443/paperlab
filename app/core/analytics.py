"""Per-strategy performance: trade grouping, live scoreboards, the daily rollup and the lab journal.

Every strategy runs its own isolated book, so all of this is computed per strategy_id and never mixes
wallets. Trades are reconstructed by grouping fills on position_id, which keeps the fill ledger the single
source of truth (it survives restarts and is what the CSV export contains).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from app.core.risk import reject_code
from app.core.types import Fill, VirtualPosition

if TYPE_CHECKING:  # pragma: no cover
    from app.core.engine import Engine

log = logging.getLogger("paperlab.analytics")

DAY_MS = 86_400_000
REJECT_NOTE_THROTTLE_MS = 3_600_000  # one journal line per (strategy, reason) per hour; signals table keeps them all


def day_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def day_bounds(day_utc: str) -> tuple[int, int]:
    start = datetime.strptime(day_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


# ---- trades ----------------------------------------------------------------------------------

def build_trades(fills: Sequence[Fill], open_positions: Iterable[VirtualPosition] = (),
                 now_ms: int | None = None) -> list[dict[str, Any]]:
    """Group a fill ledger into trades. One entry fill + its exit fills = one trade.

    `now_ms` makes a still-open trade report hold time up to now rather than up to its last partial exit.
    """
    groups: dict[str, dict[str, Any]] = {}
    for f in fills:
        if f.kind == "funding":
            continue
        g = groups.setdefault(f.position_id, {"entry": None, "exits": []})
        if f.is_open:
            g["entry"] = f
        else:
            g["exits"].append(f)
    still_open = {p.id: p for p in open_positions}
    out: list[dict[str, Any]] = []
    for pid, g in groups.items():
        entry: Fill | None = g["entry"]
        exits: list[Fill] = sorted(g["exits"], key=lambda x: x.ts)
        if entry is None:
            continue
        closed_qty = sum(x.qty for x in exits)
        exit_price = (sum(x.qty * x.price for x in exits) / closed_qty) if closed_qty else None
        pnl = sum(x.realized_pnl for x in exits)
        fees = entry.fee + sum(x.fee for x in exits)
        net = pnl - fees
        stop = float(entry.meta.get("stop") or 0.0)
        risk = entry.qty * abs(entry.price - stop) if stop else 0.0
        pos = still_open.get(pid)
        is_open = pos is not None and pos.qty > 0
        exit_ts = exits[-1].ts if exits else None
        end_ts = exit_ts if not is_open and exit_ts else None
        out.append({
            "position_id": pid, "strategy_id": entry.strategy_id, "symbol": entry.symbol,
            "side": entry.position_side, "qty": entry.qty, "qty_closed": closed_qty,
            "entry_price": entry.price, "entry_ts": entry.ts, "exit_price": exit_price, "exit_ts": end_ts,
            "pnl": pnl, "fees": fees, "net": net, "r": (net / risk) if risk > 0 else None,
            "risk_usd": risk, "exit_kind": (exits[-1].kind if exits else None), "open": is_open,
            "hold_s": _hold_s(entry.ts, end_ts, exits, is_open, now_ms),
            "leg": entry.meta.get("leg"), "simulated": entry.simulated, "leverage": entry.leverage,
            "stop": stop or None, "wallet_equity_after": (exits[-1].wallet_equity_after if exits
                                                          else entry.wallet_equity_after),
            "exits": [{"ts": x.ts, "qty": x.qty, "price": x.price, "kind": x.kind, "pnl": x.realized_pnl,
                       "fee": x.fee, "reason": x.meta.get("reason")} for x in exits],
        })
    out.sort(key=lambda t: (t["exit_ts"] or t["entry_ts"]))
    return out


def _hold_s(entry_ts: int, end_ts: int | None, exits: Sequence[Fill], is_open: bool,
            now_ms: int | None) -> int:
    """Seconds held: to the final exit when closed, otherwise to now (falling back to the last partial)."""
    if end_ts:
        return max(0, (end_ts - entry_ts) // 1000)
    if is_open and now_ms:
        return max(0, (now_ms - entry_ts) // 1000)
    return max(0, (exits[-1].ts - entry_ts) // 1000) if exits else 0


def summarise(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Win rate / profit factor / avg R / expectancy over CLOSED trades."""
    closed = [t for t in trades if not t["open"] and t["exit_ts"]]
    n = len(closed)
    wins = [t for t in closed if t["net"] > 0]
    losses = [t for t in closed if t["net"] <= 0]
    gross_profit = sum(t["net"] for t in wins)
    gross_loss = -sum(t["net"] for t in losses)
    rs = [t["r"] for t in closed if t["r"] is not None]
    if gross_loss > 0:
        pf: float | None = gross_profit / gross_loss
    else:
        pf = 999.0 if gross_profit > 0 else None
    return {
        "trades_total": n, "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / n) if n else None,
        "profit_factor": pf,
        "avg_r": (sum(rs) / len(rs)) if rs else None,
        "expectancy": (sum(t["net"] for t in closed) / n) if n else None,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "net": sum(t["net"] for t in closed),
        "time_in_market_s": sum(t["hold_s"] for t in closed),
    }


# ---- live scoreboard -------------------------------------------------------------------------

def scoreboard(e: "Engine", sid: str, trades: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything needed to judge one strategy at a glance, from its own isolated book."""
    prices = e.prices()
    wallet = e.portfolio.wallets.get(sid)
    positions = e.portfolio.positions_of(sid)
    upnl = e.portfolio.upnl(sid, prices)
    life = wallet.life if wallet else 1
    if trades is None:
        trades = build_trades(e.storage.fills_for(e.epoch, sid, life=life), positions, e.clock())
    stats = summarise(trades)
    day_start = e.day_start_ms()
    now = e.clock()
    open_s = sum(max(0, (now - p.entry_ts) // 1000) for p in positions)
    last_fill_ts = max((t["exit_ts"] or t["entry_ts"] for t in trades), default=None)
    rec = e.board.last(sid)
    last_signal = None
    if rec is not None:
        reason = rec.reason or rec.signal.reason
        last_signal = {"ts": rec.ts, "symbol": rec.signal.symbol, "side": rec.signal.side,
                       "kind": rec.signal.kind, "status": rec.status, "reason": reason,
                       "code": reject_code(reason) if rec.status == "rejected" else None}
    return {
        "allocation": wallet.allocation if wallet else 0.0,
        "equity": wallet.equity(upnl) if wallet else 0.0,
        "realized": wallet.realized if wallet else 0.0,
        "fees": wallet.fees if wallet else 0.0,
        "funding": wallet.funding if wallet else 0.0,
        "upnl": upnl,
        "trades_total": stats["trades_total"],
        "trades_open": len(positions),
        "trades_today": sum(1 for t in trades if not t["open"] and (t["exit_ts"] or 0) >= day_start),
        "wins": stats["wins"], "losses": stats["losses"], "win_rate": stats["win_rate"],
        "profit_factor": stats["profit_factor"], "avg_r": stats["avg_r"], "expectancy": stats["expectancy"],
        "max_dd_pct": e.storage.equity_max_drawdown(sid, e.epoch) * 100.0,
        "time_in_market_s": stats["time_in_market_s"] + open_s,
        "last_fill_ts": last_fill_ts,
        "last_signal": last_signal,
        "life": life,
        "lives_today": wallet.lives_today if wallet else 0,
        "realized_all_lives": wallet.net_all_lives() if wallet else 0.0,
        "rejects_today": e.storage.reject_reason_counts(e.epoch, sid, day_start),
        "margin_used": e.portfolio.margin_used(sid),
        "gross_notional": sum(p.notional(prices.get(p.symbol, p.entry_price)) for p in positions),
    }


# ---- daily rollup ----------------------------------------------------------------------------

def recompute_daily(e: "Engine", day_utc: str | None = None, strategy_ids: Iterable[str] | None = None) -> int:
    """Rebuild strategy_daily rows for a UTC day. Idempotent: running it twice writes the same values."""
    day_utc = day_utc or day_key(e.clock())
    start_ms, end_ms = day_bounds(day_utc)
    written = 0
    for sid in (strategy_ids if strategy_ids is not None else list(e.strategies)):
        wallet0 = e.portfolio.wallets.get(sid)
        life = wallet0.life if wallet0 else 1
        fills = e.storage.fills_for(e.epoch, sid, until_ts=end_ms, life=life)
        trades = build_trades(fills, e.portfolio.positions_of(sid), e.clock())
        today = [t for t in trades if not t["open"] and t["exit_ts"] and start_ms <= t["exit_ts"] < end_ms]
        stats = summarise(today)
        day_fills = [f for f in fills if start_ms <= f.ts < end_ms]
        realized = sum(f.realized_pnl for f in day_fills if f.kind != "funding")
        fees = sum(f.fee for f in day_fills)
        funding = sum(f.realized_pnl for f in day_fills if f.kind == "funding")
        counts = e.storage.signal_status_counts(e.epoch, sid, start_ms, end_ms)
        equity_pts = e.storage.equity_series(sid, e.epoch, start_ms, max_points=0) or []
        in_day = [p for p in equity_pts if p[0] < end_ms]
        wallet = e.portfolio.wallets.get(sid)
        end_equity = in_day[-1][1] if in_day else (wallet.equity(e.portfolio.upnl(sid, e.prices()))
                                                   if wallet else None)
        start_equity = in_day[0][1] if in_day else (wallet.allocation if wallet else None)
        peak = None
        worst = 0.0
        for _ts, eq in in_day:
            peak = eq if peak is None or eq > peak else peak
            if peak and peak > 0:
                worst = min(worst, (eq - peak) / peak)
        e.storage.upsert_strategy_daily({
            "day_utc": day_utc, "epoch": e.epoch, "strategy_id": sid,
            "start_equity": start_equity, "end_equity": end_equity, "realized": realized, "fees": fees,
            "funding": funding, "upnl_eod": e.portfolio.upnl(sid, e.prices()),
            "trades": stats["trades_total"], "wins": stats["wins"], "losses": stats["losses"],
            "win_rate": stats["win_rate"], "profit_factor": stats["profit_factor"], "avg_r": stats["avg_r"],
            "max_dd_pct": worst * 100.0, "expectancy": stats["expectancy"],
            "signals_total": sum(counts.values()),
            "signals_approved": counts.get("approved", 0), "signals_rejected": counts.get("rejected", 0),
            "signals_warmup": counts.get("warmup", 0), "signals_stale": counts.get("stale", 0),
            "time_in_market_s": stats["time_in_market_s"],
            "gross_notional_max": max((t["qty"] * t["entry_price"] for t in today), default=0.0),
            "reject_reasons_json": _dumps(e.storage.reject_reason_counts(e.epoch, sid, start_ms, end_ms)),
            "life": life,
        })
        written += 1
    return written


def _dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))


# ---- journal ---------------------------------------------------------------------------------

def note(e: "Engine", kind: str, text: str, strategy_id: str | None = None, symbol: str | None = None,
         author: str = "system") -> None:
    """Append one human-readable line to the lab journal (analysis_notes)."""
    try:
        e.storage.insert_note(e.epoch, kind, text, strategy_id, symbol, author, e.clock())
    except Exception as exc:  # the journal must never break trading
        log.warning("note failed (%s): %s", kind, str(exc)[:120])


def note_reject(e: "Engine", sid: str, symbol: str, reason: str) -> None:
    """Rejects are always persisted in `signals`; the journal keeps one line per reason per hour."""
    key = (sid, reason.split(":")[0])
    now = e.clock()
    if now - e._reject_note_ts.get(key, -REJECT_NOTE_THROTTLE_MS) < REJECT_NOTE_THROTTLE_MS:
        return
    e._reject_note_ts[key] = now
    note(e, "reject", f"{sid} reject {symbol} {reason}", sid, symbol)


def note_fill(e: "Engine", fill: Fill, opened: bool) -> None:
    sid = fill.strategy_id
    equity = fill.wallet_equity_after
    if opened:
        note(e, "open", f"{sid} open {fill.symbol} {fill.position_side} qty={fill.qty:g} @{fill.price:.6g} "
                        f"equity={equity:.2f}", sid, fill.symbol)
    else:
        r = fill.meta.get("r")
        r_txt = f"R={r:+.2f} " if isinstance(r, (int, float)) else ""
        note(e, "close", f"{sid} close {fill.symbol} kind={fill.kind} {r_txt}pnl={fill.realized_pnl:+.2f} "
                         f"equity={equity:.2f}", sid, fill.symbol)


def lives_timeline(e: "Engine", sid: str) -> list[dict[str, Any]]:
    """One row per life of this book: what it did before it died, newest last."""
    rows = e.storage.strategy_daily(e.epoch, sid)
    by_life: dict[int, dict[str, Any]] = {}
    for r in rows:
        life = int(r.get("life") or 1)
        acc = by_life.setdefault(life, {"life": life, "trades": 0, "realized": 0.0, "fees": 0.0, "days": []})
        acc["trades"] += int(r.get("trades") or 0)
        acc["realized"] += float(r.get("realized") or 0.0)
        acc["fees"] += float(r.get("fees") or 0.0)
        acc["days"].append(r.get("day_utc"))
    wallet = e.portfolio.wallets.get(sid)
    current = wallet.life if wallet else 1
    for life, acc in by_life.items():
        acc["current"] = life == current
        acc["net"] = acc["realized"] - acc["fees"]
    if wallet and current not in by_life:
        by_life[current] = {"life": current, "trades": 0, "realized": wallet.realized, "fees": wallet.fees,
                            "net": wallet.net(), "days": [], "current": True}
    if wallet:
        by_life[current]["equity"] = wallet.equity(e.portfolio.upnl(sid, e.prices()))
    return [by_life[k] for k in sorted(by_life)]
