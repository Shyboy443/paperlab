"""Boot / restart reconciliation for the Engine (see plan "Boot order").

1 storage state -> 2 strategy_state (first boot defaults) -> 3 replay fills + open snapshots -> 4 rules /
account mode -> 5 backfill + start feed -> 6 gap-check open positions -> 7 exchange sync -> 8 day roll ->
9 warm-up (signals recorded as `warmup`, never traded) -> 10 running / paused.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from app.core import analytics, engine_dispatch
from app.core.feed import MarketFeed
from app.core.risk import StrategyMeta
from app.core.types import Candle, tf_ms

if TYPE_CHECKING:  # pragma: no cover
    from app.core.engine import Engine

log = logging.getLogger("paperlab.boot")


def wallet_model_marker(balance: float) -> str:
    return f"isolated:{balance:g}"


def _load_strategy_states(e: "Engine") -> None:
    """One isolated book per strategy, every strategy armed. Wallets never share cash.

    A database written by an older capital model (a shared 10 000 pool, 500-per-strategy slices, or only
    S01+S06 enabled) is flagged for a rebase here and rebuilt once prices are live (see maybe_rebase).
    """
    rows = e.storage.strategy_states()
    first_boot = not rows
    balance = e.settings.strategy_starting_balance
    want_marker = wallet_model_marker(balance)
    needs_rebase = (not first_boot) and e.storage.get_meta("wallet_model") != want_marker
    venue_kind = e.settings.venue.caps.kind
    for sid, st in e.strategies.items():
        cls = type(st)
        row = rows.get(sid) or {}
        # Params: start from the CLASS defaults (so a new tuning pack lands on the next boot) and overlay
        # only the keys the operator actually edited through the API. Never silently keep a stale default.
        custom = set(row.get("params_custom") or [])
        stored = row.get("params") or {}
        overlay = {k: v for k, v in stored.items() if k in custom}
        e.params_custom[sid] = custom
        if overlay:
            from app.strategies.base import apply_params
            st.params, warnings = apply_params(cls.Params, st.params, overlay)
            for w in warnings:
                log.warning("%s params: %s", sid, w)
        fresh = first_boot or needs_rebase or not row
        stored_alloc = row.get("allocation")
        allocation = balance if (fresh or stored_alloc is None) else float(stored_alloc)
        wallet = e.portfolio.ensure_wallet(sid, allocation)
        wallet.allocation = allocation
        if not fresh:
            wallet.life = int(row.get("life") or 1)
            wallet.lives_today = int(row.get("lives_today") or 0)
            wallet.lives_day = str(row.get("lives_day") or "")
            wallet.realized_all_lives = float(row.get("realized_all_lives") or 0.0)
        supported = venue_kind in cls.supported_venues
        enabled = True if fresh else bool(row.get("enabled"))
        size_mult = 1.0 if fresh else float(row.get("size_mult") or 1.0)
        halted = False if fresh else bool(row.get("halted"))
        leverage = int(row.get("leverage") or cls.default_leverage or e.settings.default_leverage)
        if enabled and not supported:
            enabled = False  # e.g. S16 needs funding, which a spot venue does not have
        if not e.settings.bakeoff_enabled:
            enabled = False  # the V1 bake-off is stopped: no book takes a new entry (records untouched)
        e.meta[sid] = StrategyMeta(id=sid, enabled=enabled, size_mult=size_mult, leverage=leverage,
                                   max_positions=cls.max_positions, min_rr=cls.min_rr, supported=supported,
                                   halted=halted)
        floor = row.get("halt_floor")
        if fresh or floor is None:
            e.risk.set_halt_floor(sid, allocation)
        else:
            e.risk.state.halt_floors[sid] = float(floor)
        if halted:
            e.risk.state.halted_strategies.add(sid)
        e.board.set_enabled(sid, enabled)
        e.last_error[sid] = None
        if fresh:
            e.persist_meta(sid)
    e.boot_report["first_boot"] = first_boot
    e.boot_report["needs_rebase"] = needs_rebase
    e.boot_report["strategy_balance"] = balance
    if first_boot:
        e.storage.set_meta("wallet_model", want_marker)
        e.event("first_boot", {"enabled": sorted(sid for sid, m in e.meta.items() if m.enabled),
                               "balance_each": balance,
                               "paper_total": e.settings.paper_total(len(e.strategies))})


async def maybe_rebase(e: "Engine") -> bool:
    """Rebuild an older book as 20 isolated `STRATEGY_STARTING_BALANCE` wallets.

    Open positions are closed at the last price (flagged estimated) and recorded in the OLD epoch so the
    previous run stays auditable, then a new epoch starts with every wallet at the configured balance and
    every strategy armed. Old fills are never rewritten.
    """
    if not e.boot_report.get("needs_rebase"):
        return False
    balance = e.settings.strategy_starting_balance
    closed = 0
    for pos in list(e.portfolio.positions.values()):
        pos.meta["price_estimated"] = True
        price = e.last.get(pos.symbol) or pos.entry_price
        fill = e.portfolio.close_position(pos.id, 1.0, price, "wallet_rebase", simulated=e.router.dry_run,
                                          reason="wallet rebase to isolated books (estimated price)",
                                          ts=e.clock())
        e.storage.record_fill(fill, delete_position_id=pos.id)
        e.router.mark_dirty(pos.symbol)
        closed += 1
    await e.router.sync_dirty("wallet_rebase")
    old_epoch = e.epoch
    new_epoch = e.storage.bump_epoch()
    e.epoch = new_epoch
    e.portfolio.epoch = new_epoch
    e.portfolio.reset(new_epoch, {sid: balance for sid in e.strategies})
    e.storage.clear_positions()
    e.board.reset()
    for sid in e.strategies:
        meta = e.meta[sid]
        meta.enabled = meta.supported
        meta.size_mult = 1.0
        meta.halted = False
        meta.cooldown_until.clear()
        wallet = e.portfolio.wallets[sid]
        wallet.life, wallet.realized_all_lives, wallet.lives_today, wallet.lives_day = 1, 0.0, 0, ""
        e.risk.set_halt_floor(sid, balance)
        e.board.set_enabled(sid, meta.enabled)
        e.persist_meta(sid)
    e.risk.state.halted_strategies.clear()
    e.risk.state.margin_halted.clear()
    e.risk.state.symbol_cooldown_until.clear()
    e.risk.state.daily_halted = False
    e.storage.set_meta("wallet_model", wallet_model_marker(balance))
    e.boot_report["rebased"] = {"from_epoch": old_epoch, "to_epoch": new_epoch, "closed_positions": closed,
                                "balance_each": balance}
    e.event("wallet_rebased", {"from_epoch": old_epoch, "epoch": new_epoch, "closed_positions": closed,
                               "balance_each": balance,
                               "paper_total": e.settings.paper_total(len(e.strategies)),
                               "enabled": sorted(sid for sid, m in e.meta.items() if m.enabled)})
    log.warning("wallet rebase: closed %d position(s), epoch %d -> %d, every wallet reset to %.2f",
                closed, old_epoch, new_epoch, balance)
    return True


async def stop_bakeoff(e: "Engine") -> int:
    """BAKEOFF_ENABLED=false: the frozen V1 bake-off stops. Its books are already disarmed; here their open PAPER
    positions are closed at the live price so every stopped book ends with a final, recorded result. Only in dry
    run -- with a live router nothing is closed automatically (that stays an operator decision)."""
    if e.settings.bakeoff_enabled:
        return 0
    closed = 0
    if e.router.dry_run:
        for pos in list(e.portfolio.positions.values()):
            await engine_dispatch.close_position(e, pos, 1.0, e.last.get(pos.symbol) or pos.entry_price,
                                                 "halt", "bake-off stopped (BAKEOFF_ENABLED=false)")
            closed += 1
    e.boot_report["bakeoff"] = {"stopped": True, "paper_positions_closed": closed,
                                "books_disarmed": sum(1 for m in e.meta.values() if not m.enabled)}
    e.event("bakeoff_stopped", e.boot_report["bakeoff"])
    log.warning("V1 bake-off stopped: %d books disarmed, %d paper positions closed",
                e.boot_report["bakeoff"]["books_disarmed"], closed)
    return closed


async def respawn_halted(e: "Engine") -> list[str]:
    """Bring books that are sitting HALTED back into the bake-off on boot.

    A book halted by an earlier build (or before a restart) would otherwise sit out the whole soak. With
    AUTO_RESPAWN_HALTED it gets the same treatment as a fresh death: the dead life is recorded, then it
    respawns at the configured balance. The per-day life cap still applies.
    """
    if not e.settings.auto_respawn_halted or not e.settings.bakeoff_enabled:
        return []
    revived: list[str] = []
    for sid, meta in list(e.meta.items()):
        if not meta.halted or not meta.supported:
            continue
        for pos in [p for p in e.portfolio.positions.values() if p.strategy_id == sid]:
            await engine_dispatch.close_position(e, pos, 1.0, e.last.get(pos.symbol) or pos.entry_price,
                                                 "halt", "flattened before respawn on boot")
        equity = e.portfolio.wallet_equity(sid, e.prices())
        await engine_dispatch.end_life(e, sid, equity, e.risk.state.halt_floors.get(sid))
        if not e.meta[sid].halted:
            revived.append(sid)
    if revived:
        e.boot_report["respawned_on_boot"] = revived
        log.warning("respawned %d halted book(s) on boot: %s", len(revived), ", ".join(revived))
    return revived


def _build_subscriptions(e: "Engine") -> None:
    e.subs = {}
    for sid, st in e.strategies.items():
        for key in st.subscriptions(e.symbols):
            if key[1] in e.tfs and key[0] in e.symbols:
                e.subs.setdefault(key, []).append(sid)
    for key in list(e.subs):
        e.subs[key].sort()


def _replay(e: "Engine") -> None:
    allocations = {sid: w.allocation for sid, w in e.portfolio.wallets.items()}
    lives = {sid: w.life for sid, w in e.portfolio.wallets.items()}
    banked = {sid: w.realized_all_lives for sid, w in e.portfolio.wallets.items()}
    days = {sid: (w.lives_day, w.lives_today) for sid, w in e.portfolio.wallets.items()}
    fills = e.storage.fills_since(e.epoch)
    snapshots = e.storage.open_positions(e.epoch)
    e.portfolio.replay(fills, snapshots, allocations, lives)
    for sid, w in e.portfolio.wallets.items():  # replay rebuilds Wallets: restore the life bookkeeping
        w.realized_all_lives = banked.get(sid, 0.0)
        w.lives_day, w.lives_today = days.get(sid, ("", 0))
    e.boot_report["replayed_fills"] = len(fills)
    e.boot_report["restored_positions"] = len(snapshots)
    symbols_meta = e.storage.get_meta("symbols")
    if symbols_meta:
        syms = [s for s in symbols_meta.split(",") if s]
        if syms and syms != e.symbols:
            log.info("symbols overridden by dashboard setting: %s", syms)
            e.symbols = syms


async def _load_rules(e: "Engine") -> None:
    if e.client is None:
        raise RuntimeError("engine needs an exchange client for market rules")
    rules = await e.client.load_rules(e.symbols)
    e.rules.update(rules)
    for sym in list(e.rules):
        if sym not in e.symbols:
            e.rules.pop(sym, None)
    e.exit_engine.mmr_by_symbol = {}
    if not e.router.dry_run:
        report = await e.client.ensure_account_mode(e.symbols, e.settings.default_leverage)
        e.boot_report["account_mode"] = report
        brackets = await e.client.fetch_leverage_brackets(e.symbols)
        e.router.mmr_by_symbol = brackets
        e.exit_engine.mmr_by_symbol = dict(brackets)
        for sym, lev in report.get("leverage", {}).items():
            e.router.exchange_leverage[sym] = int(lev)


async def backfill_all(e: "Engine", max_bars: int | None = None) -> int:
    total = 0
    now = e.clock()
    window = max_bars or e.settings.candle_window
    for sym in e.symbols:
        for tf in e.tfs:
            key = (sym, tf)
            dq = e.candles.setdefault(key, deque(maxlen=e.settings.candle_window))
            stored = e.storage.candles(sym, tf, limit=window)
            for c in stored:
                if not dq or c.open_time > dq[-1].open_time:
                    dq.append(c)
            since = (dq[-1].open_time + tf_ms(tf)) if dq else None
            if since is not None and now - since > tf_ms(tf) * (window + 2):
                since = None  # stored data is too old; start over from the last `window` bars
            fresh = await e.client.backfill(sym, tf, since, now, max_bars=window)
            if fresh:
                if since is None and dq:
                    dq.clear()
                for c in fresh:
                    if not dq or c.open_time > dq[-1].open_time:
                        dq.append(c)
                e.storage.insert_candles(fresh)
                total += len(fresh)
                removed = e.storage.delete_synthetic(sym, tf)
                if removed:
                    log.info("dropped %d synthetic bars for %s %s after real backfill", removed, sym, tf)
            e.cache.invalidate(sym, tf)
            if dq and sym not in e.last:
                e.last[sym] = dq[-1].close
    e.boot_report["backfilled"] = True
    return total


async def gap_fill(e: "Engine") -> None:
    """After a reconnect: fetch closed bars missed while disconnected and push them through the pipeline."""
    now = e.clock()
    for sym in e.symbols:
        for tf in e.tfs:
            dq = e.candles.get((sym, tf))
            since = (dq[-1].open_time + tf_ms(tf)) if dq else None
            if since is not None and since + tf_ms(tf) > now:
                continue
            fresh = await e.client.backfill(sym, tf, since, now, max_bars=e.settings.candle_window)
            for c in fresh:
                await engine_dispatch.on_candle_closed(e, c)


def _gap_check_positions(e: "Engine") -> int:
    """Open virtual positions vs bars seen while we were down: close at the breached level (estimated)."""
    closed = 0
    for pos in list(e.portfolio.positions.values()):
        dq = e.candles.get((pos.symbol, "1m")) or e.candles.get((pos.symbol, pos.tf))
        if not dq:
            continue
        bars = [c for c in dq if c.open_time > pos.entry_ts]
        hit_price: float | None = None
        kind = "restart_gap"
        for c in bars:
            long = pos.side == "long"
            if pos.stop > 0 and ((long and c.low <= pos.stop) or (not long and c.high >= pos.stop)):
                hit_price = pos.stop
                break
            if pos.take_profits:
                tp = pos.take_profits[-1]
                if (long and c.high >= tp.price) or (not long and c.low <= tp.price):
                    hit_price = tp.price
                    break
        if hit_price is not None:
            pos.meta["price_estimated"] = True
            fill = e.portfolio.close_position(pos.id, 1.0, hit_price, kind, simulated=e.router.dry_run,
                                              reason="restart gap: level breached while offline (estimated)",
                                              ts=e.clock(), fill_price=hit_price)
            e.storage.record_fill(fill, delete_position_id=pos.id)
            e.router.mark_dirty(pos.symbol)
            closed += 1
    return closed


async def _warmup(e: "Engine") -> None:
    """Feed backfilled bars through on_candle so stateful strategies (S08, S18, S20 votes) are primed."""
    e.warming = True
    try:
        keys = sorted(e.candles.keys(), key=lambda k: (k[1], k[0]))
        merged: list[Candle] = []
        for key in keys:
            dq = e.candles[key]
            warm = list(dq)[-max(50, min(300, len(dq))):]
            merged.extend(warm)
        merged.sort(key=lambda c: (c.close_time, c.tf))
        # temporarily rebuild stores incrementally so indicators reflect "as of that bar"
        saved = {k: list(v) for k, v in e.candles.items()}
        for k in e.candles:
            e.candles[k].clear()
        for k, bars in saved.items():
            keep = len(bars) - len([c for c in merged if (c.symbol, c.tf) == k])
            for c in bars[:keep]:
                e.candles[k].append(c)
        e.cache.invalidate()
        n = 0
        for c in merged:
            dq = e.candles[(c.symbol, c.tf)]
            dq.append(c)
            e.cache.invalidate(c.symbol, c.tf)
            for sid in e.subs.get((c.symbol, c.tf), []):
                st = e.strategies[sid]
                try:
                    sigs = st.on_candle(c, e.ctx)
                except Exception as exc:
                    engine_dispatch._record_error(e, sid, exc, "warmup")
                    continue
                if sigs:
                    await engine_dispatch.process_signals(e, st, sigs, candle=c)
            n += 1
            if n % 200 == 0:
                await asyncio.sleep(0)
        e.boot_report["warmup_bars"] = n
    finally:
        e.warming = False


async def _start_feed(e: "Engine") -> None:
    e.feed = MarketFeed(e.settings.venue, e.symbols, e.tfs, e.queue, client=e.client,
                        user_stream=(not e.router.dry_run))
    await e.feed.start()
    if e.router.dry_run:
        e.feed.user["detail"] = "dry_run (no user stream)"


async def restart_feed(e: "Engine", symbols: list[str]) -> None:
    if e.feed is not None:
        await e.feed.stop()
    removed = [s for s in e.symbols if s not in symbols]
    e.symbols = list(symbols)
    for sym in removed:
        for tf in e.tfs:
            e.candles.pop((sym, tf), None)
        for store in (e.last, e.marks, e.books, e.fundings, e.ticks):
            store.pop(sym, None)
    for st in e.strategies.values():
        st.reset()
    _build_subscriptions(e)
    await backfill_all(e)
    await _start_feed(e)


async def _wait_for_prices(e: "Engine", timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(s in e.last for s in e.symbols) and e.feed is not None and e.feed.market["connected"]:
            return
        await asyncio.sleep(0.25)


# One-shot experiment journal entries. Key -> (note kind, params to read). The meta guard means a
# restart does not re-journal an experiment that is already recorded.
EXPERIMENTS: dict[str, tuple[str, dict[str, str]]] = {
    "hold_v1": ("hold_v1_applied", {"S01": "max_hold_s", "S05": "max_hold_s",
                                    "S17": "hold_s", "S19": "max_hold_s"}),
}


def journal_experiments(e: "Engine") -> None:
    """Write each experiment's note exactly once, with the holds as they actually loaded."""
    for key, (kind, wanted) in EXPERIMENTS.items():
        if e.storage.get_meta(f"experiment_{key}"):
            continue
        parts = []
        for sid, attr in wanted.items():
            strat = e.strategies.get(sid)
            val = getattr(strat.params, attr, None) if strat else None
            parts.append(f"{sid}.{attr}={val if val else 'none'}")
        analytics.note(e, kind, f"{kind}: " + ", ".join(parts) +
                       " (holds only; stops/TPs/trails/size unchanged)")
        e.storage.set_meta(f"experiment_{key}", str(e.clock()))


async def boot(e: "Engine") -> None:
    t0 = time.monotonic()
    e.set_state("booting")
    _load_strategy_states(e)
    _replay(e)
    _build_subscriptions(e)
    await _load_rules(e)
    e.portfolio.rules = e.rules
    e.boot_report["symbols"] = list(e.symbols)
    bars = await backfill_all(e)
    e.boot_report["backfill_bars"] = bars
    # start consuming events before the feed connects so nothing queues up unhandled
    e.spawn_tasks()
    await _start_feed(e)
    await _wait_for_prices(e)
    gap_closed = _gap_check_positions(e)
    e.boot_report["restart_gap_closed"] = gap_closed
    for sym in e.symbols:
        await engine_dispatch.apply_exit_checks(e, sym)
    rebased = await maybe_rebase(e)  # needs live prices to close the old book honestly
    await respawn_halted(e)          # nobody sits out the bake-off because of an earlier death
    await stop_bakeoff(e)            # BAKEOFF_ENABLED=false: close the stopped books' paper positions
    journal_experiments(e)
    if not e.router.dry_run:
        await e.router.cancel_orphans(e.symbols)
        await e.router.refresh_exchange(e.symbols, force=True)
        e.exchange_positions = dict(e.router.exchange_net)
        for sym in e.symbols:
            e.router.mark_dirty(sym)
        await e.router.sync_dirty("boot")
    # day roll / start-of-day equity
    total = e.portfolio.total_equity(e.prices())
    sod_date = e.storage.get_meta("sod_date") or ""
    sod_equity = float(e.storage.get_meta("sod_equity") or 0.0)
    # A rebased book, or a changed per-strategy balance, makes the stored start-of-day equity meaningless:
    # left alone it would trip the daily halt on a phantom drawdown the moment we boot.
    paper_total = e.settings.paper_total(len(e.strategies))
    configured = float(e.storage.get_meta("config_paper_total") or 0.0)
    if rebased or (configured and abs(configured - paper_total) > 1e-9):
        e.event("start_of_day_rebased", {"from": configured or None, "to": paper_total,
                                         "equity": total, "after_wallet_rebase": rebased})
        sod_date, sod_equity = "", 0.0
        e.risk.state.daily_halted = False
    e.storage.set_meta("config_paper_total", str(paper_total))
    if sod_date == e.today() and sod_equity > 0:
        e.risk.state.start_of_day_date = sod_date
        e.risk.state.start_of_day_equity = sod_equity
    else:
        e.risk.roll_day(total, e.today())
        e.storage.set_meta("sod_date", e.today())
        e.storage.set_meta("sod_equity", str(total))
    await _warmup(e)
    engine_dispatch.snapshot_equity(e)
    if e.settings.engine_enabled:
        e.set_state("running")
    else:
        e.set_state("paused", "engine_disabled")
    e.boot_report["boot_s"] = round(time.monotonic() - t0, 1)
    e.event("boot", {k: v for k, v in e.boot_report.items() if k != "account_mode"})
    log.info("boot complete in %.1fs: %s", time.monotonic() - t0, e.boot_report)
