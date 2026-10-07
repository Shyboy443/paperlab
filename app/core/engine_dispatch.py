"""Event handlers for the Engine: candles -> strategies -> risk -> virtual fills -> router.

Every strategy always computes signals ("shadow" when disabled). Only enabled + supported strategies
route entries through RiskManager.approve(); every signal is recorded whatever its status.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from app.core import analytics
from app.core.portfolio import new_id
from app.core.types import (BookEvent, BookSnapshot, Candle, CandleClosed, CandleForming, ExitIntent, FeedStatus,
                            Fill, FundingInfo, MarkEvent, Signal, TradeEvent, UserEvent, VirtualPosition, tf_ms)

if TYPE_CHECKING:  # pragma: no cover
    from app.core.engine import Engine

log = logging.getLogger("paperlab.dispatch")
EXIT_KINDS = {"manual", "rotate", "vote_flip", "bias_flip", "funding_normalised", "regime", "time", "flatten"}


# ---- dispatch ---------------------------------------------------------------------------

async def handle(e: "Engine", ev: Any) -> None:
    if isinstance(ev, CandleClosed):
        await on_candle_closed(e, ev.candle)
    elif isinstance(ev, CandleForming):
        on_candle_forming(e, ev.candle)
        if ev.candle.tf == "1m":
            await apply_exit_checks(e, ev.candle.symbol)
    elif isinstance(ev, TradeEvent):
        await on_trade(e, ev)
    elif isinstance(ev, MarkEvent):
        await on_mark(e, ev)
    elif isinstance(ev, BookEvent):
        await on_book(e, ev.book)
    elif isinstance(ev, UserEvent):
        await on_user(e, ev)
    elif isinstance(ev, FeedStatus):
        await on_feed_status(e, ev)


def _record_error(e: "Engine", sid: str, exc: Exception, where: str) -> None:
    msg = f"{where}: {exc.__class__.__name__}: {str(exc)[:160]}"
    e.last_error[sid] = msg
    log.warning("strategy %s %s", sid, msg)


def _store_candle(e: "Engine", c: Candle) -> bool:
    """Append a closed candle; returns False for duplicates / out-of-order bars."""
    key = (c.symbol, c.tf)
    dq = e.candles.get(key)
    if dq is None:
        dq = e.candles[key] = deque(maxlen=e.settings.candle_window)
    if dq and c.open_time <= dq[-1].open_time:
        if c.open_time == dq[-1].open_time and dq[-1].source != "live" and c.source == "live":
            dq[-1] = c  # a live close replaces a backfilled copy of the same bar
            e.cache.invalidate(c.symbol, c.tf)
        return False
    dq.append(c)
    e.cache.invalidate(c.symbol, c.tf)
    if c.source != "synthetic":
        e.storage.insert_candles([c])
    if c.source == "live" and c.symbol not in e.last:
        e.last[c.symbol] = c.close
    return True


async def on_candle_closed(e: "Engine", c: Candle) -> None:
    if not _store_candle(e, c):
        return
    if c.source == "live" and e.forming.get((c.symbol, c.tf)) is not None:
        e.forming.pop((c.symbol, c.tf), None)
    last = e.last.get(c.symbol) or c.close
    # 1. position management on this (symbol, tf)
    for pos in [p for p in e.portfolio.positions.values() if p.symbol == c.symbol and p.tf == c.tf]:
        st = e.strategies.get(pos.strategy_id)
        if st is None:
            continue
        try:
            upd = st.manage(pos, c, e.ctx)
        except Exception as exc:
            _record_error(e, pos.strategy_id, exc, "manage")
            continue
        if upd is not None:
            intents = e.exit_engine.apply_update(pos, upd, last)
            await execute_exits(e, intents, simulated_reason=upd.reason)
            e.storage.upsert_position(pos, e.epoch) if pos.id in e.portfolio.positions else None
    await apply_exit_checks(e, c.symbol)
    # 2. strategies subscribed to this (symbol, tf)
    for sid in e.subs.get((c.symbol, c.tf), []):
        st = e.strategies[sid]
        try:
            sigs = st.on_candle(c, e.ctx)
        except Exception as exc:
            _record_error(e, sid, exc, f"on_candle {c.symbol} {c.tf}")
            continue
        if sigs:
            await process_signals(e, st, sigs, candle=c)
    await e.router.sync_dirty("candle")


def on_candle_forming(e: "Engine", c: Candle) -> None:
    e.forming[(c.symbol, c.tf)] = c
    if c.tf == "1m":
        e.last[c.symbol] = c.close


async def on_trade(e: "Engine", ev: TradeEvent) -> None:
    t = ev.tick
    e.last[t.symbol] = t.price
    dq = e.ticks.get(t.symbol)
    if dq is None:
        dq = e.ticks[t.symbol] = deque(maxlen=2000)
    dq.append(t)
    await apply_exit_checks(e, t.symbol)
    for sid, st in e.strategies.items():
        if type(st).needs_ticks and t.symbol in e.symbols:
            try:
                sigs = st.on_tick(t, e.ctx)
            except Exception as exc:
                _record_error(e, sid, exc, "on_tick")
                continue
            if sigs:
                await process_signals(e, st, sigs)
    await e.router.sync_dirty("tick")


async def on_mark(e: "Engine", ev: MarkEvent) -> None:
    m = ev.mark
    e.marks[m.symbol] = m
    if m.symbol not in e.fundings and m.funding_rate is not None:
        e.fundings[m.symbol] = FundingInfo(m.symbol, m.funding_rate, m.next_funding_ts, m.mark, m.index, m.ts)
    if m.symbol not in e.last:
        e.last[m.symbol] = m.mark
    await apply_exit_checks(e, m.symbol)
    await e.router.sync_dirty("mark")


async def on_book(e: "Engine", book: BookSnapshot) -> None:
    e.books[book.symbol] = book
    for sid, st in e.strategies.items():
        if type(st).needs_book:
            try:
                sigs = st.on_book(book, e.ctx)
            except Exception as exc:
                _record_error(e, sid, exc, "on_book")
                continue
            if sigs:
                await process_signals(e, st, sigs)
    await e.router.sync_dirty("book")


async def on_funding(e: "Engine", f: FundingInfo) -> None:
    prev = e.fundings.get(f.symbol)
    e.fundings[f.symbol] = f
    if prev is None or abs(prev.rate - f.rate) > 1e-9:
        e.storage.insert_funding(f)
    # paper funding settlement when the funding timestamp rolls over
    if prev is not None and prev.next_funding_ts and f.next_funding_ts > prev.next_funding_ts and e.portfolio.positions_on(f.symbol):
        fills = e.portfolio.apply_funding(f.symbol, prev.rate, f.mark, e.clock())
        for fill in fills:
            e.storage.insert_fill(fill)
    for sid, st in e.strategies.items():
        if type(st).needs_funding:
            try:
                sigs = st.on_funding(f, e.ctx)
            except Exception as exc:
                _record_error(e, sid, exc, "on_funding")
                continue
            if sigs:
                await process_signals(e, st, sigs)
    await e.router.sync_dirty("funding")


async def on_user(e: "Engine", ev: UserEvent) -> None:
    fired = e.router.on_user_event(ev)
    for sym in fired:
        await backstop_fired(e, sym, "backstop_fired")


async def on_feed_status(e: "Engine", ev: FeedStatus) -> None:
    e.feed_events.append({"ts": ev.ts, "stream": ev.stream, "connected": ev.connected, "detail": ev.detail})
    e.event("feed_status", {"stream": ev.stream, "connected": ev.connected, "detail": ev.detail})
    if ev.stream == "market" and ev.connected and e.client is not None and e.boot_report.get("backfilled"):
        try:
            from app.core import engine_boot
            await engine_boot.gap_fill(e)
        except Exception as exc:
            log.warning("gap fill after reconnect failed: %s", str(exc)[:160])


async def on_timer(e: "Engine") -> None:
    for sym in list(e.symbols):
        if sym in e.last:
            await apply_exit_checks(e, sym)
    await e.router.sync_dirty("timer")


async def on_position_poll(e: "Engine") -> None:
    await e.router.refresh_exchange(list(e.symbols), force=True)
    e.exchange_positions = dict(e.router.exchange_net)
    newly = e.risk.check_exchange_margin(e.exchange_positions, e.router.mmr_by_symbol)
    for sym in newly:
        e.event("margin_halt", {"symbol": sym, "liq_distance": e.exchange_positions[sym].liq_distance_pct()})
        await backstop_fired(e, sym, "margin_halt")
        await e.router.flatten_exchange([sym], reason="margin_halt")
    # exchange flat while the virtual book is not, with nothing in flight -> the exchange closed it (reset/backstop)
    for sym in list(e.symbols):
        ex = e.exchange_positions.get(sym)
        if ex is not None and abs(ex.qty) == 0 and abs(e.portfolio.net_qty(sym)) > 0 and sym not in e.router.desync \
                and sym not in e.router._dirty and not e.router._lock(sym).locked():
            await backstop_fired(e, sym, "exchange_flat")


# ---- signal processing ----------------------------------------------------------------------

async def process_signals(e: "Engine", st: Any, sigs: list[Signal], candle: Candle | None = None) -> None:
    sid = type(st).id
    m = e.meta.get(sid)
    if m is None:
        return
    now = e.clock()
    for sig in sigs:
        sig.id = sig.id or new_id()
        sig.ts = sig.ts or now
        if sig.kind == "exit":
            await _handle_exit_signal(e, sig)
            continue
        decision = None
        if e.warming:
            status, reason = "warmup", "boot warm-up"
        elif candle is not None and candle.source != "live":
            status, reason = "stale", f"{candle.source} bar"
        elif not m.supported:
            status, reason = "shadow", "venue unsupported"
        elif not m.enabled:
            status, reason = "shadow", "strategy off"
        elif now - sig.ts > max(1, sig.valid_bars) * tf_ms(sig.tf if sig.tf in ("1m", "5m", "15m") else "1m") + 5_000:
            status, reason = "stale", "signal older than valid_bars"
        else:
            decision = e.risk.approve(sig, m, e.prices(), e.settings.venue.caps.kind,
                                      e.router.exchange_wallet.get("available") if not e.router.dry_run else None,
                                      e.router.lev(sig.symbol), e.router.dry_run)
            if decision.approved:
                await open_from_signal(e, sig, decision)
                status, reason = "approved", ""
            else:
                status, reason = "rejected", decision.reason
                analytics.note_reject(e, sid, sig.symbol, reason)
        e.board.post(sig, status, reason, ts=sig.ts)
        e.storage.insert_signal(sig, status, reason, e.epoch, decision,
                                life=e.portfolio.wallets[sid].life if sid in e.portfolio.wallets else 1)


async def _handle_exit_signal(e: "Engine", sig: Signal) -> None:
    positions = [p for p in e.portfolio.positions_of(sig.strategy_id)
                 if p.symbol == sig.symbol and (sig.leg is None or p.leg == sig.leg)]
    kind = sig.reason if sig.reason in EXIT_KINDS else "manual"
    for p in positions:
        await close_position(e, p, 1.0, e.last.get(p.symbol) or p.entry_price, kind, sig.reason or "strategy exit")
    status = "exit" if positions else "exit_noop"
    e.board.post(sig, status, sig.reason, ts=sig.ts)
    e.storage.insert_signal(sig, status, sig.reason, e.epoch, None,
                            life=e.portfolio.wallets[sig.strategy_id].life
                            if sig.strategy_id in e.portfolio.wallets else 1)


def stamp_wallet(e: "Engine", fill: Fill) -> None:
    """Record the strategy's own book right after the fill (isolated wallets: never the lab total)."""
    upnl = e.portfolio.upnl(fill.strategy_id, e.prices())
    wallet = e.portfolio.wallets.get(fill.strategy_id)
    fill.wallet_upnl_after = upnl
    fill.wallet_equity_after = wallet.equity(upnl) if wallet else 0.0
    fill.life = wallet.life if wallet else 1


async def open_from_signal(e: "Engine", sig: Signal, decision: Any) -> None:
    ref = e.last.get(sig.symbol) or sig.entry_price
    pos, fill = e.portfolio.open_position(sig, decision, ref, simulated=e.router.dry_run, ts=e.clock())
    stamp_wallet(e, fill)
    e.storage.record_fill(fill, snapshot=pos)
    e.router.mark_dirty(sig.symbol)
    analytics.note_fill(e, fill, opened=True)
    log.info("OPEN %s %s %s qty=%g @%.6g stop=%.6g", sig.strategy_id, sig.side, sig.symbol, pos.qty, pos.entry_price, pos.stop)
    await check_halts(e, sig.strategy_id)


async def close_position(e: "Engine", pos: VirtualPosition, fraction: float, ref_price: float, kind: str,
                         reason: str = "") -> None:
    if pos.id not in e.portfolio.positions:
        return
    fill = e.portfolio.close_position(pos.id, fraction, ref_price, kind, simulated=e.router.dry_run, reason=reason,
                                      ts=e.clock())
    if pos.initial_risk_usd > 0:
        fill.meta["r"] = (pos.realized - pos.fees) / pos.initial_risk_usd
    stamp_wallet(e, fill)
    still_open = pos.id in e.portfolio.positions
    e.storage.record_fill(fill, snapshot=pos if still_open else None, delete_position_id=None if still_open else pos.id)
    e.router.mark_dirty(pos.symbol)
    analytics.note_fill(e, fill, opened=False)
    log.info("CLOSE %s %s %s qty=%g @%.6g kind=%s pnl=%.4f", pos.strategy_id, pos.side, pos.symbol, fill.qty, fill.price,
             kind, fill.realized_pnl)
    if not still_open:
        analytics.recompute_daily(e, strategy_ids=[pos.strategy_id])
    await check_halts(e, pos.strategy_id)


async def execute_exits(e: "Engine", intents: list[ExitIntent], simulated_reason: str = "") -> None:
    for it in intents:
        pos = e.portfolio.positions.get(it.position_id)
        if pos is None:
            continue
        await close_position(e, pos, it.fraction, it.ref_price, it.kind, it.reason or simulated_reason)


async def apply_exit_checks(e: "Engine", symbol: str) -> None:
    last = e.last.get(symbol)
    if not last:
        return
    mark = e.marks[symbol].mark if symbol in e.marks else None
    now = e.clock()
    for pos in [p for p in e.portfolio.positions.values() if p.symbol == symbol]:
        before = (pos.stop, len(pos.take_profits), pos.be_done)
        intents = e.exit_engine.on_price(pos, last, mark, now)
        if intents:
            await execute_exits(e, intents)
        if pos.id in e.portfolio.positions and (pos.stop, len(pos.take_profits), pos.be_done) != before:
            e.storage.upsert_position(pos, e.epoch)


async def flatten_all(e: "Engine", kind: str, reason: str, strategy_id: str | None = None) -> int:
    n = 0
    for pos in list(e.portfolio.positions.values()):
        if strategy_id is not None and pos.strategy_id != strategy_id:
            continue
        await close_position(e, pos, 1.0, e.last.get(pos.symbol) or pos.entry_price, kind, reason)
        n += 1
    await e.router.sync_dirty(kind)
    return n


async def backstop_fired(e: "Engine", symbol: str, kind: str) -> None:
    closed = 0
    for pos in [p for p in e.portfolio.positions.values() if p.symbol == symbol]:
        pos.meta["price_estimated"] = True
        await close_position(e, pos, 1.0, e.last.get(symbol) or pos.entry_price, kind,
                             f"{kind}: exchange net position closed; virtual book closed at last price (estimated)")
        closed += 1
    e.risk.set_symbol_cooldown(symbol, 300_000)
    e.event(kind, {"symbol": symbol, "closed_positions": closed})
    e.router.mark_dirty(symbol)
    await e.router.sync_dirty(kind)


# ---- halts -----------------------------------------------------------------------------------

async def check_halts(e: "Engine", strategy_id: str | None = None) -> None:
    prices = e.prices()
    if strategy_id is not None:
        m = e.meta.get(strategy_id)
        equity = e.portfolio.wallet_equity(strategy_id, prices)
        if m is not None and not m.halted and e.risk.check_strategy_halt(strategy_id, equity):
            m.halted = True
            m.enabled = False
            e.board.set_enabled(strategy_id, False)
            e.persist_meta(strategy_id)
            floor = e.risk.state.halt_floors.get(strategy_id)
            e.event("strategy_halt", {"equity": equity, "floor": floor}, strategy_id)
            await flatten_all(e, "halt", "strategy halt -25%", strategy_id)
            await end_life(e, strategy_id, equity, floor)
    total = e.portfolio.total_equity(prices)
    if e.risk.check_daily_halt(total):
        e.event("daily_halt", {"equity": total, "start_of_day": e.risk.state.start_of_day_equity})
        analytics.note(e, "halt", f"LAB DAILY HALT: equity {total:.2f} vs start-of-day "
                                  f"{e.risk.state.start_of_day_equity:.2f}; everything flattened and paused")
        e.set_state("paused", "daily_halt")
        await flatten_all(e, "halt", "daily halt -12%")


async def check_rollover(e: "Engine") -> None:
    today = e.today()
    if e.risk.state.start_of_day_date == today:
        return
    # close the books on the day that just ended before re-basing
    yesterday = e.risk.state.start_of_day_date
    if yesterday:
        analytics.recompute_daily(e, yesterday)
        analytics.note(e, "rollover", f"day {yesterday} closed; strategy_daily written for "
                                      f"{len(e.strategies)} books")
    total = e.portfolio.total_equity(e.prices())
    e.risk.roll_day(total, today)
    e.storage.set_meta("sod_date", today)
    e.storage.set_meta("sod_equity", str(total))
    e.event("day_rollover", {"start_of_day_equity": total})
    if e.risk.state.daily_halted and e.settings.auto_resume_after_halt:
        e.risk.clear_daily_halt(total)
        if e.settings.engine_enabled and not e.risk.state.killed:
            e.set_state("running")
        e.event("halt_cleared_auto", {})


def snapshot_equity(e: "Engine") -> None:
    prices = e.prices()
    ts = e.clock()
    rows = []
    totals = e.portfolio.totals(prices)
    rows.append((ts, e.epoch, "TOTAL", totals["equity"], totals["upnl"], totals["realized"]))
    for sid, snap in e.portfolio.snapshot(prices).items():
        rows.append((ts, e.epoch, sid, snap["equity"], snap["upnl"], snap["realized"]))
    e.storage.insert_equity_many(rows)



from app.core.payloads import fill_row, position_row, state_payload  # noqa: E402,F401  (re-exported)
_fill_row = fill_row  # legacy alias


# ---- lives: a dead book is recorded, then respawned so the bake-off keeps 20 runners -------------

async def end_life(e: "Engine", sid: str, equity: float, floor: float | None) -> None:
    """A book hit its -25% floor. Bank the life, then respawn it unless respawn is off or the daily cap is hit.

    The dead life stays on disk: its fills, signals and strategy_daily rows keep their `life` number, and
    `realized_all_lives` carries its result forward so the graveyard is visible on the leaderboard.
    """
    wallet = e.portfolio.wallets.get(sid)
    meta = e.meta.get(sid)
    if wallet is None or meta is None:
        return
    trades = sum(1 for t in e.portfolio.closed_trades if t.strategy_id == sid)
    analytics.recompute_daily(e, strategy_ids=[sid])
    analytics.note(e, "life_ended",
                   f"{sid} life_ended equity={equity:.2f} realized={wallet.net():+.2f} trades={trades} "
                   f"floor={floor:.2f}" if floor is not None else
                   f"{sid} life_ended equity={equity:.2f} realized={wallet.net():+.2f} trades={trades}", sid)
    day = e.today()
    lives_today = wallet.lives_today if wallet.lives_day == day else 0
    cap = int(e.settings.max_lives_per_day)
    if not e.settings.auto_respawn_halted or not e.settings.bakeoff_enabled:
        why = "AUTO_RESPAWN_HALTED=false" if e.settings.bakeoff_enabled else "BAKEOFF_ENABLED=false"
        analytics.note(e, "halt", f"{sid} stays halted ({why}) - clear it by hand", sid)
        return
    if lives_today >= cap:
        analytics.note(e, "halt", f"{sid} stays halted: {lives_today} lives today reaches the cap of {cap}", sid)
        e.event("respawn_capped", {"lives_today": lives_today, "cap": cap}, sid)
        return
    balance = e.settings.strategy_starting_balance
    wallet = e.portfolio.respawn(sid, balance, day)
    meta.halted = False
    meta.enabled = meta.supported          # size_mult is deliberately left as the operator set it
    meta.cooldown_until.clear()
    e.risk.state.halted_strategies.discard(sid)
    e.risk.set_halt_floor(sid, balance)
    e.board.set_enabled(sid, meta.enabled)
    e.persist_meta(sid)
    e.event("life_respawned", {"life": wallet.life, "lives_today": wallet.lives_today, "balance": balance,
                               "realized_all_lives": wallet.realized_all_lives}, sid)
    analytics.note(e, "life_respawned",
                   f"{sid} life_respawned life={wallet.life} balance={balance:.2f} "
                   f"all_lives={wallet.realized_all_lives:+.2f}", sid)
    log.warning("%s respawned as life %d at %.2f (lives today %d/%d)", sid, wallet.life, balance,
                wallet.lives_today, cap)


STARVED_WINDOW_MS = 3 * 3_600_000
STARVED_MIN_IDLE_SIGNALS = 20


def check_starved(e: "Engine") -> None:
    """Flag a book that has seen plenty of bars but produced no approved entry for three hours.

    The cure is the tuning pack (looser thresholds), not a weaker risk gate, so this only records the
    finding: a `starved` journal line per strategy per window.
    """
    now = e.clock()
    if now - getattr(e, "boot_ms", now) < STARVED_WINDOW_MS:
        return
    since = now - STARVED_WINDOW_MS
    for sid in e.strategies:
        if now - e._starved_noted.get(sid, 0) < STARVED_WINDOW_MS:
            continue
        counts = e.storage.signal_status_counts(e.epoch, sid, since, now)
        idle = counts.get("warmup", 0) + counts.get("stale", 0) + counts.get("shadow", 0)
        if counts.get("approved", 0) == 0 and idle > STARVED_MIN_IDLE_SIGNALS:
            e._starved_noted[sid] = now
            analytics.note(e, "starved", f"{sid} starved: 0 approved entries in 3h, {idle} warmup/stale/shadow "
                                         f"signals - its entry filter is not firing on this tape", sid)
            e.event("starved", {"window_h": 3, "idle_signals": idle}, sid)
