"""Engine: state machine, background tasks and the control API used by app/core/api.py.

Event handling lives in engine_dispatch.py, boot/reconciliation in engine_boot.py. Everything runs on the
single asyncio loop; the only event consumer is `_consume()`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import RedactFilter, Settings
from app.core import analytics, engine_boot, engine_dispatch
from app.core.context import MarketCtx
from app.core.indicators import IndicatorCache, last as _last
from app.core.portfolio import Portfolio
from app.core.positions import ExitEngine
from app.core.risk import RiskManager, StrategyMeta
from app.core.signals import SignalBoard
from app.core.storage import Storage
from app.core.types import (BookSnapshot, Candle, ExchangePosition, FundingInfo, MarketRules, MarkPrice, Tick)
from app.exchange.client import make_client
from app.exchange.paper_router import PaperRouter
from app.strategies.base import Strategy, apply_params, param_specs, params_to_dict

log = logging.getLogger("paperlab.engine")


class EngineError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Engine:
    def __init__(self, settings: Settings, storage: Storage, client: Any | None,
                 strategy_classes: dict[str, type[Strategy]], clock: Callable[[], int] | None = None):
        self.settings = settings
        self.storage = storage
        self.client = client
        # set by main.configure_logging so dashboard-entered secrets are masked in every log line
        self.redactor: RedactFilter | None = None
        self._key_source: str = "env" if settings.has_keys else "none"
        self.classes = dict(strategy_classes)
        self.strategies: dict[str, Strategy] = {sid: cls() for sid, cls in self.classes.items()}
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.symbols: list[str] = list(settings.symbols)
        self.tfs: tuple[str, ...] = tuple(settings.timeframes)
        self.rules: dict[str, MarketRules] = {}
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=100_000)
        self.board = SignalBoard()
        self.cache = IndicatorCache()
        self.candles: dict[tuple[str, str], deque[Candle]] = {}
        self.forming: dict[tuple[str, str], Candle] = {}
        self.last: dict[str, float] = {}
        self.marks: dict[str, MarkPrice] = {}
        self.books: dict[str, BookSnapshot] = {}
        self.fundings: dict[str, FundingInfo] = {}
        self.ticks: dict[str, deque[Tick]] = {}
        self.epoch: int = storage.epoch()
        self.portfolio = Portfolio(settings, self.rules, self.clock)
        self.portfolio.epoch = self.epoch
        self.risk = RiskManager(settings, self.portfolio, self.rules, self.clock)
        self.exit_engine = ExitEngine(self.rules, atr_provider=self._atr_provider, line_provider=self._line_provider)
        self.router = PaperRouter(settings, client, self.portfolio, storage, self.rules, self.clock,
                                  lambda s: self.last.get(s))
        self.feed: Any | None = None
        self.meta: dict[str, StrategyMeta] = {}
        self.subs: dict[tuple[str, str], list[str]] = {}
        self.state: str = "booting"
        self.paused_reason: str | None = None
        self.started_at: float = time.time()
        self.boot_ms: int = self.clock()  # engine-clock boot stamp (wall time is not the clock in tests)
        self.last_error: dict[str, str | None] = {}
        self.ctx = MarketCtx(self)
        self.tasks: list[asyncio.Task] = []
        self.warming: bool = False
        self.feed_events: deque[dict[str, Any]] = deque(maxlen=50)
        self.exchange_positions: dict[str, ExchangePosition] = {}
        self.boot_report: dict[str, Any] = {}
        self._reject_note_ts: dict[tuple[str, str], int] = {}  # journal throttle, see analytics.note_reject
        self.params_custom: dict[str, set[str]] = {}  # param keys the operator edited: survive a defaults change
        self._starved_noted: dict[str, int] = {}
        self.lock = asyncio.Lock()
        self._stopping = False
        self._last_rollover_check = 0.0

    # -- providers for the exit engine ----------------------------------------------------
    def _atr_provider(self, symbol: str, tf: str, period: int) -> float | None:
        try:
            return self.ctx.ind_last(symbol, tf, "atr", n=period)
        except Exception:
            return None

    def _line_provider(self, symbol: str, tf: str) -> float | None:
        try:
            st = self.strategies.get("S06")
            n = int(getattr(getattr(st, "params", None), "atr_period", 10))
            mult = float(getattr(getattr(st, "params", None), "multiplier", 2.2))
            line, _ = self.ctx.ind(symbol, tf, "supertrend", n=n, mult=mult)
            return _last(line)
        except Exception:
            return None

    # -- lifecycle -----------------------------------------------------------------------
    async def boot(self) -> None:
        await engine_boot.boot(self)

    def spawn_tasks(self) -> None:
        loop_tasks = [
            ("consume", self._consume()), ("timer1s", self._timer_1s()), ("funding", self._funding_poll()),
            ("positions", self._position_poll()), ("router-retry", self._router_retry()),
            ("equity", self._equity_snapshot()), ("rollover", self._utc_rollover()),
        ]
        for name, coro in loop_tasks:
            self.tasks.append(asyncio.create_task(coro, name=f"engine-{name}"))

    async def stop(self) -> None:
        self._stopping = True
        if self.feed is not None:
            await self.feed.stop()
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()
        self.state = "stopped"

    # -- background tasks -------------------------------------------------------------------
    async def _consume(self) -> None:
        while not self._stopping:
            ev = await self.queue.get()
            try:
                await engine_dispatch.handle(self, ev)
            except Exception:
                log.exception("event handling failed for %s", type(ev).__name__)

    async def _timer_1s(self) -> None:
        while not self._stopping:
            await asyncio.sleep(1.0)
            try:
                await engine_dispatch.on_timer(self)
            except Exception:
                log.exception("timer tick failed")

    async def _funding_poll(self) -> None:
        if self.client is None or not self.settings.venue.caps.funding:
            return
        while not self._stopping:
            try:
                for sym in list(self.symbols):
                    f = await self.client.fetch_premium_index(sym)
                    await engine_dispatch.on_funding(self, f)
            except Exception as exc:
                log.warning("funding poll failed: %s", str(exc)[:160])
            await asyncio.sleep(60.0)

    async def _position_poll(self) -> None:
        if self.router.dry_run:
            return
        while not self._stopping:
            await asyncio.sleep(10.0)
            try:
                await engine_dispatch.on_position_poll(self)
            except Exception as exc:
                log.warning("position poll failed: %s", str(exc)[:160])

    async def _router_retry(self) -> None:
        if self.router.dry_run:
            return
        while not self._stopping:
            await asyncio.sleep(10.0)
            try:
                await self.router.retry_desync(list(self.symbols))
            except Exception as exc:
                log.warning("router retry failed: %s", str(exc)[:160])

    async def _equity_snapshot(self) -> None:
        ticks = 0
        while not self._stopping:
            await asyncio.sleep(30.0)
            ticks += 1
            if ticks % 10 == 0:  # every 5 min: keep strategy_daily fresh while positions are still open
                try:
                    analytics.recompute_daily(self)
                    engine_dispatch.check_starved(self)
                except Exception:
                    log.exception("periodic daily rollup failed")
            try:
                engine_dispatch.snapshot_equity(self)
            except Exception:
                log.exception("equity snapshot failed")

    async def _utc_rollover(self) -> None:
        while not self._stopping:
            await asyncio.sleep(30.0)
            try:
                await engine_dispatch.check_rollover(self)
            except Exception:
                log.exception("rollover check failed")

    # -- helpers ------------------------------------------------------------------------------
    def today(self) -> str:
        return datetime.fromtimestamp(self.clock() / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    def prices(self) -> dict[str, float]:
        return {s: p for s, p in self.last.items() if p}

    def is_running(self) -> bool:
        return self.state == "running"

    def set_state(self, state: str, reason: str | None = None) -> None:
        self.state = state
        self.paused_reason = reason if state == "paused" else None
        self.risk.state.engine_running = state == "running"

    # kinds that get an automatic journal line; halts write their own richer text
    AUTO_NOTE_KINDS = {"kill", "unkill", "strategy_halt_cleared", "halt_cleared_manual", "halt_cleared_auto",
                       "margin_halt", "backstop_fired", "exchange_flat", "wallet_rebased", "reset",
                       "params_changed", "strategy_enabled", "strategy_disabled", "allocation_changed",
                       "engine_start", "engine_stop", "first_boot", "symbols_changed", "restart_gap"}

    def event(self, kind: str, payload: dict[str, Any] | None = None, strategy_id: str | None = None) -> None:
        payload = payload or {}
        self.storage.insert_event(kind, payload, self.epoch, strategy_id, self.clock())
        log.info("event %s %s %s", kind, strategy_id or "", payload)
        if kind in self.AUTO_NOTE_KINDS:
            bits = ", ".join(f"{k}={v}" for k, v in list(payload.items())[:4] if not isinstance(v, (dict, list)))
            analytics.note(self, kind, f"{strategy_id + ' ' if strategy_id else ''}{kind}"
                                       f"{': ' + bits if bits else ''}", strategy_id)

    def persist_meta(self, sid: str) -> None:
        m = self.meta[sid]
        st = self.strategies[sid]
        w = self.portfolio.wallets[sid]
        self.storage.upsert_strategy_state(
            sid, enabled=int(m.enabled), allocation=w.allocation, leverage=m.leverage,
            size_mult=m.size_mult, params=params_to_dict(st.params), halted=int(m.halted),
            halt_floor=self.risk.state.halt_floors.get(sid),
            params_custom=sorted(self.params_custom.get(sid, set())),
            life=w.life, lives_today=w.lives_today, lives_day=w.lives_day,
            realized_all_lives=w.realized_all_lives)

    # -- control API (called from api.py) ---------------------------------------------------
    async def kill(self) -> dict[str, Any]:
        async with self.lock:
            self.risk.kill()
            self.set_state("paused", "kill")
            closed = await engine_dispatch.flatten_all(self, kind="kill", reason="kill switch")
            await self.router.flatten_exchange(list(self.symbols), reason="kill")
            for sid, m in self.meta.items():
                if m.enabled:
                    m.enabled = False
                    self.board.set_enabled(sid, False)
                    self.persist_meta(sid)
            self.event("kill", {"closed_positions": closed})
            return {"ok": True, "closed_positions": closed}

    async def resume(self, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise EngineError("confirm=true required", 400)
        async with self.lock:
            total = self.portfolio.total_equity(self.prices())
            if self.risk.state.daily_halted:
                self.risk.clear_daily_halt(total)
                self.storage.set_meta("sod_equity", str(total))
                self.event("halt_cleared_manual", {"rebased_start_of_day_equity": total})
            if self.risk.state.killed:
                self.risk.unkill()
                self.event("unkill", {})
            if self.settings.engine_enabled:
                self.set_state("running")
            else:
                self.set_state("paused", "engine_disabled")
            return {"ok": True, "state": self.state}

    ARM_PHRASE = "GO LIVE"

    # -- winners -------------------------------------------------------------------------------
    # A "winner" is a book PROMOTED out of the bake-off. That is a durable label stored in meta, kept
    # separate from `router.live_strategies` (which orders are actually being routed right now). They
    # differ constantly: every restart clears the in-memory keys and disarms, but the promotion stands.
    def winners(self) -> list[str]:
        raw = self.storage.get_meta("winners") or ""
        return [s for s in (x.strip().upper() for x in raw.split(",")) if s and s in self.meta]

    def _set_winners(self, ids: list[str]) -> None:
        self.storage.set_meta("winners", ",".join(ids))

    async def promote(self, sid: str) -> dict[str, Any]:
        self._meta(sid)
        current = self.winners()
        if sid in current:
            return {"ok": True, "winners": current, "detail": "already promoted"}
        current.append(sid)              # order is the ranking: first promoted is WINNER 1
        self._set_winners(current)
        self.event("promoted", {"rank": len(current)}, sid)
        analytics.note(self, "promoted", f"{sid} promoted to WINNER {len(current)}")
        return {"ok": True, "winners": current}

    async def demote(self, sid: str) -> dict[str, Any]:
        current = [x for x in self.winners() if x != sid.upper()]
        self._set_winners(current)
        self.event("demoted", {}, sid.upper())
        analytics.note(self, "demoted", f"{sid.upper()} returned to the bake-off")
        return {"ok": True, "winners": current}

    # -- exchange credentials entered from the dashboard --------------------------------------
    # Held in memory for the life of the process ONLY. Never written to the database, never to disk,
    # never logged (they are pushed into the redact filter) and never readable back through the API -
    # /api/live reports a masked fingerprint and nothing else. A restart clears them.
    def credentials_status(self) -> dict[str, Any]:
        key = self.settings.api_key or ""
        return {"connected": bool(self.settings.has_keys),
                "fingerprint": (key[:4] + "…" + key[-4:]) if len(key) >= 8 else ("set" if key else ""),
                "source": self._key_source}

    async def set_credentials(self, key: str, secret: str) -> dict[str, Any]:
        """Swap in an API key pair and prove it works before accepting it. Refused while armed."""
        import dataclasses
        key, secret = str(key or "").strip(), str(secret or "").strip()
        if not key or not secret:
            raise EngineError("both the API key and secret are required", 400)
        if not self.router.dry_run:
            raise EngineError("disarm live trading before changing credentials", 409)
        async with self.lock:
            probe_settings = dataclasses.replace(self.settings, api_key=key, api_secret=secret)
            probe = make_client(probe_settings)
            try:
                balance = await probe.fetch_balance()
            except Exception as exc:
                await probe.close()
                # the message can echo request params, so redact before it reaches the browser
                raise EngineError(f"those credentials were rejected by {self.settings.venue.label}: "
                                  f"{RedactFilter([key, secret]).redact(str(exc))[:200]}", 400) from exc
            if self.redactor is not None:
                self.redactor.add(key)
                self.redactor.add(secret)
            old = self.client
            self.settings = probe_settings
            self.client = probe
            self.router.client = probe
            self._key_source = "session"
            if old is not None and old is not probe:
                try:
                    await old.close()
                except Exception:  # pragma: no cover - best effort
                    pass
            self.event("credentials_set", {"venue": self.settings.venue.label,
                                           "wallet": balance.get("wallet")})
            log.info("exchange credentials accepted for %s (wallet %.2f USDT)",
                     self.settings.venue.label, float(balance.get("wallet") or 0.0))
            return {"ok": True, "wallet": balance, **self.credentials_status()}

    async def clear_credentials(self) -> dict[str, Any]:
        import dataclasses
        if not self.router.dry_run:
            raise EngineError("disarm live trading before clearing credentials", 409)
        async with self.lock:
            self.settings = dataclasses.replace(self.settings, api_key="", api_secret="")
            self._key_source = "none"
            self.event("credentials_cleared", {})
            return {"ok": True, **self.credentials_status()}

    def live_preflight(self, balance: dict[str, float] | None = None,
                       candidates: set[str] | None = None) -> list[dict[str, Any]]:
        """Every condition that must hold before real orders may be sent. The GUI shows this list so the
        person pressing the button can see exactly what they are accepting.

        `balance` is the real exchange wallet when it could be read; the paper-vs-real size check is
        skipped (and reported as such) when it could not.
        """
        s = self.settings
        n_pos = len(self.portfolio.positions)
        live = sorted(candidates if candidates is not None else (self.router.live_strategies or set()))
        prices = self.prices()
        paper = sum(self.portfolio.wallet_equity(sid, prices) for sid in live)
        live_pos = sum(1 for p in self.portfolio.positions.values() if p.strategy_id in set(live))
        wallet = float((balance or {}).get("wallet") or 0.0)
        checks = [
            {"id": "live_books", "ok": 1 <= len(live) <= max(1, s.max_live_strategies),
             "detail": f"{len(live)} selected: {', '.join(live) or 'none'}",
             "help": f"pick between 1 and {max(1, s.max_live_strategies)} strategy to trade live; "
                     f"every other book keeps paper trading and never reaches the exchange"},
            {"id": "enabled", "ok": all(self.meta[sid].enabled for sid in live) if live else False,
             "detail": ", ".join(f"{sid}={'on' if self.meta[sid].enabled else 'OFF'}" for sid in live) or "none",
             "help": "the live strategy must be enabled in the Strategies tab"},
            {"id": "not_halted", "ok": all(not self.meta[sid].halted for sid in live) if live else False,
             "detail": ", ".join(sid for sid in live if self.meta[sid].halted) or "ok",
             "help": "clear the strategy halt first"},
            # THE dangerous one: orders are sized from the PAPER wallet. If the paper book is bigger than
            # the real balance, every order is oversized by exactly that ratio.
            {"id": "paper_matches_wallet",
             "ok": wallet >= 1.0 and paper <= wallet * 1.05,
             "detail": f"paper {paper:.2f} USDT vs exchange {wallet:.2f} USDT",
             "help": (
                 # A ratio against a ~0 balance is meaningless ("1869158.9x"), and the real problem is
                 # not the paper size at all - there is nothing in the trading account to trade with.
                 f"the exchange trading account holds {wallet:.2f} USDT. On Bybit, derivatives trade "
                 f"from the UNIFIED TRADING account: move funds there (Assets -> Funding -> Transfer -> "
                 f"Unified Trading) and check the API key belongs to the same (sub)account you funded."
                 if wallet < 1.0 else
                 f"orders are sized from the paper wallet, so a {paper:.2f} USDT book on a "
                 f"{wallet:.2f} USDT account sends orders {paper / wallet:.1f}x too large. "
                 f"Set STRATEGY_STARTING_BALANCE to {wallet:.2f} and reset the epoch.")},
            {"id": "venue", "ok": bool(s.venue.is_live),
             "detail": f"{s.venue.label} ({s.venue.rest_base})",
             "help": "only a venue built through the explicit live override may send real orders"},
            {"id": "api_keys", "ok": bool(s.has_keys),
             "detail": "present" if s.has_keys else "missing",
             "help": f"set the API key/secret for {s.venue.exchange} in paperlab/.env"},
            {"id": "client", "ok": self.client is not None,
             "detail": type(self.client).__name__ if self.client else "none",
             "help": "no exchange client is attached"},
            # Only the LIVE books need to be flat: their virtual net is what the exchange is converged to,
            # so an open one would be netted onto the real account the instant we arm. Paper books may
            # hold positions freely - they never reach the exchange.
            {"id": "live_books_flat", "ok": live_pos == 0,
             "detail": f"{live_pos} open position(s) on the live book(s), {n_pos} in the lab overall",
             "help": "flatten the live strategy first: arming would immediately push its open position "
                     "onto the real account at whatever price the market is now"},
            {"id": "not_killed", "ok": not self.risk.state.killed, "detail": "killed" if self.risk.state.killed else "ok",
             "help": "resume (unkill) before arming"},
            {"id": "no_daily_halt", "ok": not self.risk.state.daily_halted,
             "detail": "halted" if self.risk.state.daily_halted else "ok", "help": "clear the daily halt first"},
        ]
        return checks

    async def arm_live(self, phrase: str, strategies: list[str] | None = None) -> dict[str, Any]:
        """Flip the router from paper fills to real orders. Refuses unless EVERY preflight check passes
        and the exact phrase was typed. Never called automatically - only from the dashboard button."""
        if str(phrase).strip().upper() != self.ARM_PHRASE:
            raise EngineError(f"type {self.ARM_PHRASE!r} exactly to arm live trading", 400)
        async with self.lock:
            if not self.router.dry_run:
                return {"ok": True, "live": True, "detail": "already armed"}
            # Resolve WHICH books go live before anything else: it is the cheapest check and the one
            # most likely to be wrong. Never fall back to "whatever is enabled" - with the bake-off
            # running that is 27 books, and with one enabled it would silently arm a strategy nobody
            # chose (that is how S01 once went live instead of S15).
            want = {s.strip().upper() for s in (strategies or []) if s and s.strip()}
            if not want:
                raise EngineError("name the strategy to trade live - arming will not guess", 400)
            unknown = sorted(want - set(self.meta))
            if unknown:
                raise EngineError(f"unknown strategies: {', '.join(unknown)}", 404)
            # Read the REAL balance first: the paper-vs-wallet size check is worthless against a stale or
            # missing number, so a balance we cannot read must block arming rather than be skipped.
            balance: dict[str, float] = {}
            if self.client is not None and self.settings.has_keys:
                try:
                    balance = await self.client.fetch_balance()
                except Exception as exc:
                    raise EngineError(f"cannot read the exchange balance, refusing to arm: {str(exc)[:160]}", 502)
            if not float(balance.get("wallet") or 0.0) > 0:
                raise EngineError("exchange wallet reads 0 USDT - fund the account or check the API key "
                                  "permissions before arming", 409)
            failed = [c for c in self.live_preflight(balance, candidates=want) if not c["ok"]]
            if failed:
                raise EngineError("live preflight failed: "
                                  + "; ".join(f"{c['id']} ({c['detail']}) - {c['help']}" for c in failed), 409)
            # Converge the exchange to the (flat) virtual book before anything can fire.
            await self.client.ensure_account_mode(list(self.symbols), self.settings.default_leverage)
            await self.router.cancel_orphans(list(self.symbols))
            self.router.live_strategies = set(want)
            self.storage.set_meta("live_strategies", ",".join(sorted(want)))
            for sid in sorted(want):          # arming a book is what promotes it out of the bake-off
                if sid not in self.winners():
                    await self.promote(sid)
            self.router.dry_run = False
            await self.router.refresh_exchange(list(self.symbols), force=True)
            self.event("live_armed", {"venue": self.settings.venue.label, "wallet": balance.get("wallet"),
                                      "available": balance.get("available"), "strategies": sorted(want)})
            log.critical("!!! LIVE ARMED on %s for %s - real orders will now be sent !!!",
                         self.settings.venue.label, ", ".join(sorted(want)))
            analytics.note(self, "live_armed",
                           f"LIVE ARMED on {self.settings.venue.label} for {', '.join(sorted(want))}; "
                           f"wallet {balance.get('wallet')} USDT. Every other book stays on paper.")
            return {"ok": True, "live": True, "wallet": balance, "strategies": sorted(want)}

    async def disarm_live(self) -> dict[str, Any]:
        """Back to paper fills. Flattens the exchange first so nothing is left running unmanaged."""
        async with self.lock:
            if self.router.dry_run:
                return {"ok": True, "live": False, "detail": "already paper"}
            await self.router.flatten_exchange(list(self.symbols), reason="disarm")
            await self.router.cancel_orphans(list(self.symbols))
            self.router.dry_run = True
            was = sorted(self.router.live_strategies or ())
            self.router.live_strategies = None
            self.storage.set_meta("live_strategies", "")
            self.event("live_disarmed", {"was": was})
            analytics.note(self, "live_disarmed", "back to paper fills; exchange flattened")
            return {"ok": True, "live": False}

    async def start_engine(self) -> dict[str, Any]:
        async with self.lock:
            if self.risk.state.killed:
                raise EngineError("killed: use resume", 409)
            if self.risk.state.daily_halted:
                raise EngineError("daily halt active: use resume", 409)
            self.set_state("running")
            self.event("engine_start", {})
            return {"ok": True, "state": self.state}

    async def stop_engine(self) -> dict[str, Any]:
        async with self.lock:
            self.set_state("paused", "manual")
            self.event("engine_stop", {})
            return {"ok": True, "state": self.state}

    async def set_enabled(self, sid: str, enabled: bool) -> dict[str, Any]:
        m = self._meta(sid)
        if enabled:
            if self.risk.state.killed:
                raise EngineError("killed: resume first", 409)
            if self.risk.state.daily_halted:
                raise EngineError("daily halt active: resume first", 409)
            if m.halted:
                raise EngineError("strategy halted: clear the halt first", 409)
            if not m.supported:
                raise EngineError("venue unsupported for this strategy", 409)
        m.enabled = enabled
        self.board.set_enabled(sid, enabled)
        self.persist_meta(sid)
        self.event("strategy_enabled" if enabled else "strategy_disabled", {}, sid)
        # Wallets are isolated: arming or disarming never moves capital between books.
        return {"ok": True, "enabled": enabled, "allocation": self.portfolio.wallets[sid].allocation}

    async def solo_strategy(self, sid: str) -> dict[str, Any]:
        """Enable exactly one strategy and disable every other. One click instead of 19 toggles, which is
        the difference between arming one book against a real account and accidentally arming twenty."""
        m = self._meta(sid)
        if self.risk.state.killed:
            raise EngineError("killed: resume first", 409)
        if self.risk.state.daily_halted:
            raise EngineError("daily halt active: resume first", 409)
        if m.halted:
            raise EngineError(f"{sid} is halted: clear the halt first", 409)
        if not m.supported:
            raise EngineError(f"{sid} is unsupported on this venue", 409)
        held = sorted({p.strategy_id for p in self.portfolio.positions.values()} - {sid})
        if held:
            raise EngineError(f"flatten first: {', '.join(held)} still hold open positions", 409)
        disabled = []
        for other, om in self.meta.items():
            want = other == sid
            if om.enabled != want:
                om.enabled = want
                self.board.set_enabled(other, want)
                self.persist_meta(other)
                if not want:
                    disabled.append(other)
        self.event("strategy_solo", {"enabled": sid, "disabled": disabled}, sid)
        analytics.note(self, "solo", f"{sid} is now the only enabled strategy ({len(disabled)} disabled)")
        return {"ok": True, "enabled": sid, "disabled": disabled}

    async def arm_all(self, enabled: bool = True) -> dict[str, Any]:
        """Enable (or disable) every strategy at once - the bake-off switch.

        Safe beside a live book: only the strategies in `router.live_strategies` are netted onto the
        exchange, so arming the whole lab adds paper competitors and nothing else. Halted books are
        skipped rather than silently re-armed.
        """
        changed, skipped = [], []
        for sid, m in self.meta.items():
            if enabled and (m.halted or not m.supported):
                skipped.append(sid)
                continue
            if m.enabled != enabled:
                m.enabled = enabled
                self.board.set_enabled(sid, enabled)
                self.persist_meta(sid)
                changed.append(sid)
        self.event("arm_all" if enabled else "disarm_all", {"changed": changed, "skipped": skipped})
        analytics.note(self, "bake_off",
                       f"{'armed' if enabled else 'disabled'} {len(changed)} strategies"
                       + (f"; skipped {', '.join(skipped)} (halted/unsupported)" if skipped else ""))
        return {"ok": True, "changed": changed, "skipped": skipped,
                "enabled": sum(1 for m in self.meta.values() if m.enabled)}

    async def set_params(self, sid: str, updates: dict[str, Any]) -> dict[str, Any]:
        st = self.strategies[sid] if sid in self.strategies else None
        if st is None:
            raise EngineError(f"unknown strategy {sid}", 404)
        new_params, warnings = apply_params(type(st).Params, st.params, updates)
        st.params = new_params
        # remember which keys the operator set by hand so a future defaults change does not overwrite them
        known = {s.name for s in param_specs(type(st).Params)}
        self.params_custom.setdefault(sid, set()).update(k for k in updates if k in known)
        self.persist_meta(sid)
        self.event("params_changed", {"updates": updates, "warnings": warnings}, sid)
        return {"ok": True, "values": params_to_dict(new_params), "warnings": warnings}

    async def set_allocation(self, sid: str, allocation: float | None = None, leverage: int | None = None,
                             size_mult: float | None = None) -> dict[str, Any]:
        m = self._meta(sid)
        if (allocation is not None or leverage is not None) and self.portfolio.positions_of(sid):
            raise EngineError("close the strategy's position before changing allocation or leverage", 409)
        if allocation is not None:
            if not 10.0 <= float(allocation) <= 10_000.0:
                raise EngineError("allocation must be between 10 and 10000", 400)
            self.portfolio.set_allocation(sid, float(allocation))
            self.risk.set_halt_floor(sid, float(allocation))
        if leverage is not None:
            if not 1 <= int(leverage) <= 25:
                raise EngineError("leverage must be 1..25", 400)
            m.leverage = int(leverage)
        if size_mult is not None:
            if not 0.05 <= float(size_mult) <= 3.0:
                raise EngineError("size_mult must be 0.05..3", 400)
            m.size_mult = float(size_mult)
        self.persist_meta(sid)
        self.event("allocation_changed", {"allocation": allocation, "leverage": leverage, "size_mult": size_mult}, sid)
        return {"ok": True}

    async def flatten(self, sid: str | None, kind: str = "manual", reason: str = "manual flatten") -> dict[str, Any]:
        async with self.lock:
            n = await engine_dispatch.flatten_all(self, kind=kind, reason=reason, strategy_id=sid)
            return {"ok": True, "closed_positions": n}

    async def clear_strategy_halt(self, sid: str) -> dict[str, Any]:
        m = self._meta(sid)
        equity = self.portfolio.wallet_equity(sid, self.prices())
        floor = self.risk.clear_strategy_halt(sid, equity)
        m.halted = False
        self.persist_meta(sid)
        self.event("strategy_halt_cleared", {"new_floor": floor, "equity": equity}, sid)
        return {"ok": True, "halt_floor": floor}

    async def reset_paper(self, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise EngineError("confirm=true required", 400)
        async with self.lock:
            if self.portfolio.positions:
                raise EngineError("close all positions before resetting", 409)
            if self.risk.state.killed:
                raise EngineError("killed: resume (unkill) before resetting", 409)
            new_epoch = self.storage.bump_epoch()
            self.epoch = new_epoch
            balance = self.settings.strategy_starting_balance
            allocations = {sid: balance for sid in self.strategies}
            self.portfolio.reset(new_epoch, allocations)
            self.storage.clear_positions()
            self.board.reset()
            for sid, st in self.strategies.items():
                st.reset()
                self.risk.set_halt_floor(sid, allocations[sid])
                meta = self.meta[sid]
                meta.halted = False
                meta.size_mult = 1.0
                meta.enabled = meta.supported  # a reset re-arms the whole bake-off
                meta.cooldown_until.clear()
                self.board.set_enabled(sid, meta.enabled)
                self.persist_meta(sid)
            self.risk.state.halted_strategies.clear()
            self.risk.state.margin_halted.clear()
            self.risk.state.symbol_cooldown_until.clear()
            total = self.portfolio.total_equity(self.prices())
            self.risk.roll_day(total, "")
            self.risk.roll_day(total, self.today())
            self.storage.set_meta("sod_date", self.today())
            self.storage.set_meta("sod_equity", str(total))
            # a fresh book cannot still be in a drawdown halt from the old one
            self.risk.clear_daily_halt(total)
            if self.state == "paused" and self.paused_reason == "daily_halt":
                self.set_state("running" if self.settings.engine_enabled else "paused", "engine_disabled")
            self.storage.set_meta("wallet_model", engine_boot.wallet_model_marker(balance))
            self.event("reset", {"epoch": new_epoch, "balance_each": balance,
                                 "paper_total": self.settings.paper_total(len(self.strategies)),
                                 "enabled": sorted(sid for sid, m in self.meta.items() if m.enabled),
                                 "state": self.state})
            return {"ok": True, "epoch": new_epoch, "state": self.state,
                    "balance_each": balance, "enabled": sum(1 for m in self.meta.values() if m.enabled)}

    async def change_symbols(self, symbols: list[str]) -> dict[str, Any]:
        async with self.lock:
            symbols = [s.strip().upper() for s in symbols if s.strip()]
            if not symbols:
                raise EngineError("symbols list is empty", 400)
            removed = [s for s in self.symbols if s not in symbols]
            if any(p.symbol in removed for p in self.portfolio.positions.values()):
                raise EngineError("close positions on removed symbols first", 409)
            if self.client is not None:
                new_rules = await self.client.load_rules(symbols)
                self.rules.update(new_rules)
            await engine_boot.restart_feed(self, symbols)
            self.storage.set_meta("symbols", ",".join(symbols))
            self.event("symbols_changed", {"symbols": symbols, "removed": removed})
            return {"ok": True, "symbols": symbols}

    def _meta(self, sid: str) -> StrategyMeta:
        m = self.meta.get(sid)
        if m is None:
            raise EngineError(f"unknown strategy {sid}", 404)
        return m

    # -- payloads ---------------------------------------------------------------------------
    def strategy_row(self, sid: str, snap: dict[str, Any], prices: dict[str, float],
                     board: dict[str, Any] | None = None) -> dict[str, Any]:
        m = self.meta[sid]
        st = self.strategies[sid]
        cls = type(st)
        positions = self.portfolio.positions_of(sid)
        summary = ", ".join(f"{p.symbol} {p.side} {p.qty:g} @{p.entry_price:g}" for p in positions[:3])
        sb = board if board is not None else analytics.scoreboard(self, sid)
        badges = list(cls.badges)
        if not m.supported:
            badges.append("venue unsupported")
        if m.halted:
            badges.append("HALTED")
        return {
            "id": sid, "name": cls.name, "enabled": m.enabled, "supported": m.supported, "halted": m.halted,
            "leverage": m.leverage, "size_mult": m.size_mult,
            "position_summary": summary, "open_positions": len(positions),
            "last_error": self.last_error.get(sid), "badges": badges,
            "votes_contributor": cls.contributes_votes, "timeframes": list(cls.timeframes),
            "min_rr": cls.min_rr, "max_positions": cls.max_positions,
            "halt_floor": self.risk.state.halt_floors.get(sid),
            **sb,
        }

    def day_start_ms(self) -> int:
        now = datetime.fromtimestamp(self.clock() / 1000, tz=timezone.utc)
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    _day_start_ms = day_start_ms  # legacy alias

    def state_payload(self, curves: bool = False) -> dict[str, Any]:
        return engine_dispatch.state_payload(self, curves)

    def strategy_detail(self, sid: str) -> dict[str, Any]:
        st = self.strategies.get(sid)
        if st is None:
            raise EngineError(f"unknown strategy {sid}", 404)
        m = self.meta[sid]
        prices = self.prices()
        positions = self.portfolio.positions_of(sid)
        life = self.portfolio.wallets[sid].life if sid in self.portfolio.wallets else 1
        trades = analytics.build_trades(self.storage.fills_for(self.epoch, sid, life=life), positions, self.clock())
        board = analytics.scoreboard(self, sid, trades)
        return {
            **type(st).describe(),
            "row": self.strategy_row(sid, {}, prices, board),
            "scoreboard": board,
            "params": {"specs": [s.to_dict() for s in param_specs(type(st).Params)],
                       "values": params_to_dict(st.params)},
            "state": _safe_state(st),
            "trades": list(reversed(trades)),
            "open_positions": [engine_dispatch.position_row(self, p, prices) for p in positions],
            "signals": [r.to_dict() for r in self.board.recent(100, sid)],
            "rejects_today": self.storage.reject_reason_counts(self.epoch, sid, self.day_start_ms()),
            "daily": self.storage.strategy_daily(self.epoch, sid),
            "notes": self.storage.notes(self.epoch, sid, 20),
            "lives": analytics.lives_timeline(self, sid),
            "meta": {"enabled": m.enabled, "leverage": m.leverage, "size_mult": m.size_mult, "halted": m.halted,
                     "halt_floor": self.risk.state.halt_floors.get(sid), "supported": m.supported},
        }

    def trades_page(self, sid: str, skip: int = 0, limit: int = 100) -> dict[str, Any]:
        if sid not in self.strategies:
            raise EngineError(f"unknown strategy {sid}", 404)
        life = self.portfolio.wallets[sid].life if sid in self.portfolio.wallets else 1
        trades = analytics.build_trades(self.storage.fills_for(self.epoch, sid, life=life),
                                        self.portfolio.positions_of(sid), self.clock())
        trades.reverse()  # newest first
        page = trades[skip:skip + max(1, min(limit, 1000))]
        return {"strategy_id": sid, "epoch": self.epoch, "total": len(trades), "skip": skip, "limit": limit,
                "trades": page, "summary": analytics.summarise(trades)}


def _safe_state(st: Strategy) -> dict[str, Any]:
    try:
        return st.state() or {}
    except Exception as exc:
        return {"error": str(exc)[:160]}
