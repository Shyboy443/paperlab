"""FastAPI entrypoint: `uvicorn app.main:app`.

* Basic auth (user admin / DASHBOARD_PASSWORD) on everything except /api/health.
* POST /api/* additionally requires the `X-PaperLab: 1` header (CSRF guard).
* The engine boots in a background task so /api/health answers immediately (Railway healthcheck).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import ConfigError, RedactFilter, Settings, load_settings
from app.ai.jev.service import JevService
from app.core import api, api_competition, api_jev, api_public, api_stream
from app.core.auth import basic_ok
from app.core.engine import Engine, EngineError
from app.core.realtime import Realtime
from app.competition.service import CompetitionService
from app.core.seed import apply_seed
from app.core.storage import Storage
from app.exchange.client import ExchangeClient, make_client
from app.strategies.registry import load_all

DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
log = logging.getLogger("paperlab")


class BasicAuthMiddleware:
    """Pure ASGI middleware: Basic auth + CSRF header on POST."""

    def __init__(self, app: Any, password: str, allow_no_auth: bool,
                 exempt: tuple[str, ...] = ("/api/health", "/", "/favicon.ico", "/lab"),
                 exempt_prefixes: tuple[str, ...] = ("/static/", "/public/", "/api/public/", "/lab/")):
        # The dashboard shell (HTML/JS/CSS) carries no data and is served openly; the page asks for the
        # password once and sends it as Basic auth on every /api call (no native browser prompt).
        #
        # `/public/` and `/api/public/` are the read-only inspection surface: no password, so an
        # outside reviewer can look at the real Competition UI. `/lab` only redirects there now (it briefly held a
        # separate dashboard). They are safe to exempt because
        # api_public defines GET routes only and rebuilds every payload from an allow-list. The
        # POST rule below still applies to them, and every mutating route lives elsewhere and stays
        # behind this check.
        self.app = app
        self.password = password
        self.allow_no_auth = allow_no_auth
        self.exempt = exempt
        self.exempt_prefixes = exempt_prefixes

    def _authorized(self, headers: dict[bytes, bytes]) -> bool:
        return basic_ok(headers.get(b"authorization", b""), self.password, self.allow_no_auth)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        exempt = path in self.exempt or any(path.startswith(p) for p in self.exempt_prefixes)
        if not exempt and not self._authorized(headers):
            await self._respond(send, 401, b'{"ok":false,"error":"unauthorized"}',
                               [(b"www-authenticate", b'Basic realm="paperlab"')])
            return
        if scope.get("method") == "POST" and path.startswith("/api/") and headers.get(b"x-paperlab") != b"1":
            await self._respond(send, 403, b'{"ok":false,"error":"missing X-PaperLab header"}')
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send: Any, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None) -> None:
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        headers += extra or []
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def asset_version() -> str:
    """Short hash of the dashboard assets: changes exactly when one of them changes."""
    digest = hashlib.sha256()
    for name in sorted(p.name for p in DASHBOARD_DIR.glob("*") if p.is_file() and p.name != "index.html"):
        try:
            digest.update(name.encode())
            digest.update((DASHBOARD_DIR / name).read_bytes())
        except OSError:  # pragma: no cover - unreadable asset should not break the page
            continue
    return digest.hexdigest()[:12]


def _index_html(index_file: Path) -> str:
    """index.html with ?v=<hash> stamped onto every local asset reference."""
    v = asset_version()
    html = index_file.read_text(encoding="utf-8")
    return re.sub(r'((?:src|href)=")(/static/[^"?]+)"', rf'\1\2?v={v}"', html)


class NoCacheStatic(StaticFiles):
    """Dashboard assets must revalidate on every load.

    index.html is served by a route (never cached) while app.js/charts.js/styles.css were cached by the
    browser, so a deploy could leave NEW markup being driven by OLD javascript: the new controls render
    as empty, inert elements and nothing in the UI says why. Revalidating costs one 304 per file.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:  # type: ignore[override]
        response_headers["cache-control"] = "no-cache, must-revalidate"
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "no-cache, must-revalidate"
        return response


def configure_logging(settings: Settings) -> RedactFilter:
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    flt = RedactFilter([settings.api_key, settings.api_secret])
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return flt


def _note_fee_schedule(storage: Storage, settings: Settings) -> None:
    """Journal the paper engine's fee schedule whenever it differs from the last boot, so a strategy's
    history shows exactly where its fees changed (0.04% taker before 2026-09-23, the venue's rate after)."""
    now = f"{settings.fee_source} maker {settings.maker_fee:.4%} taker {settings.taker_fee:.4%}"
    try:
        before = storage.get_meta("fee_schedule")
        if before != now:
            storage.insert_note(int(storage.get_meta("epoch") or 1), "config",
                                f"Fee schedule is now {now} (was {before or 'the pre-2026-09-23 default, taker 0.0400%'}).")
            storage.set_meta("fee_schedule", now)
    except Exception as exc:                     # the journal must never stop the service booting
        log.warning("could not journal the fee schedule: %s", str(exc)[:160])


def create_app(settings: Settings | None = None, strict: bool | None = None) -> FastAPI:
    settings = settings or load_settings()
    if strict is None:
        strict = os.environ.get("STRATEGIES_STRICT", "true").lower() not in ("0", "false", "no")
    redact = configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage = Storage(settings.db_path)
        # Tournament results are produced off-box and shipped with the image; this imports any the
        # volume database does not already have. Additive and idempotent, so a redeploy is a no-op.
        if settings.seed_results:
            try:
                apply_seed(storage)
            except Exception as exc:             # a bad seed must never stop the service booting
                log.warning("seed import failed: %s", str(exc)[:200])
        client = make_client(settings)
        classes = load_all(strict=strict)
        engine = Engine(settings, storage, client, classes)
        engine.redactor = redact      # so credentials typed into the dashboard are masked in logs too
        app.state.engine = engine
        _note_fee_schedule(storage, settings)
        # The competition runs seasons off the SAME storage and strategy registry the engine uses.
        # It owns no client and no router, so it cannot place an order however it is driven.
        app.state.competition = CompetitionService(settings, storage, classes)
        # Jev reads OPENROUTER_API_KEY from the process environment and keeps it to itself. Missing
        # key -> NOT_CONFIGURED, JEV_ENABLED=false -> DISABLED; neither affects anything else.
        redact.add(os.environ.get("OPENROUTER_API_KEY", ""))
        app.state.jev = JevService.from_env(storage)
        log.info("jev: %s (model %s)", app.state.jev.health()["status"], app.state.jev.config.model)
        # Realtime: server-sent events replace dashboard polling. Producers only run while a
        # client is connected where they cost anything (the state deltas).
        realtime: Realtime = app.state.realtime
        realtime.attach(engine=engine, competition=app.state.competition, jev=app.state.jev)
        realtime.start(asyncio.get_running_loop())
        # Live shadow: frozen v2 bots (and +JEV twins of eligible controls) on live market data, in a
        # separate process. Off unless LIVE_SHADOW_ENABLED=true; it can never place an order.
        from app.live.service import LiveShadowService
        shadow = LiveShadowService(str(settings.db_path), bus=realtime.bus)
        app.state.shadow = shadow
        realtime.shadow = shadow
        shadow.start()
        log.info("live shadow: %s", shadow.health()["status"])
        # V6 FORWARD ARENA: the frozen V6 bots (and +JEV twins) on live Bybit data, paper fills, started automatically
        # at every boot when V6_FORWARD_ENABLED=true. It resumes its forward experiment; it never backtests.
        from app.live.v6_service import V6ForwardService
        v6 = V6ForwardService(str(settings.db_path), bus=realtime.bus)
        app.state.v6 = v6
        realtime.v6 = v6
        v6.start()
        log.info("V6 forward: %s", v6.health()["status"])
        # The active challenger owns a separate database: no shared wallets or market watermarks.
        v7_storage = Storage(str(settings.db_path.with_name("v7-forward.db")))
        v7 = V6ForwardService(str(settings.db_path.with_name("v7-forward.db")), bus=realtime.bus, program="V7")
        app.state.v7 = v7
        app.state.v7_storage = v7_storage
        realtime.v7 = v7
        v7.start()
        log.info("V7 active paper: %s", v7.health()["status"])
        # V8 SCALP: aggressive 5m scalpers on their own database and freeze (V8_FORWARD_ENABLED).
        v8_storage = Storage(str(settings.db_path.with_name("v8-forward.db")))
        v8 = V6ForwardService(str(settings.db_path.with_name("v8-forward.db")), bus=realtime.bus, program="V8")
        app.state.v8 = v8
        app.state.v8_storage = v8_storage
        realtime.v8 = v8
        # LIVE MIRROR: operator-armed copies of QUALIFIED V8 bots on a real exchange account (testnet first).
        from app.core.arena_live_view import v8_enrich
        from app.core.v6_view import v6_payload
        from app.live.mirror import MirrorService
        from app.live.providers import Providers

        def _bot_view(program: str, key: str) -> dict | None:
            if program == "v6":       # V6.2 / V6.6 bots with a profitable two-year backtest (app/live/v6_golive.py)
                from app.live.v6_golive import mirror_row
                return mirror_row(storage, v6, key)       # V6 lives in the main paperlab.db (V6ForwardService has no .storage)
            if program != "v8":
                return None
            rows = v8_enrich(v6_payload(v8_storage, v8), v8_storage).get("leaderboard") or []
            return next((r for r in rows if r.get("key") == key), None)
        from app.live.key_vault import VAULT_FILE, KeyVault
        vault = KeyVault(settings.data_dir / VAULT_FILE, settings.dashboard_password)   # dashboard-entered keys, encrypted
        mirror = MirrorService(storage, Providers(vault=vault), _bot_view)
        app.state.mirror = mirror
        await mirror.start()
        v8.listeners.append(mirror.on_event)
        v6.listeners.append(mirror.on_event)
        v8.start()
        log.info("V8 scalp paper: %s", v8.health()["status"])
        # V9 STOCKS: paper scalpers on US stocks / ETFs (Alpaca IEX data, regular sessions), its own database.
        v9_storage = Storage(str(settings.db_path.with_name("v9-forward.db")))
        v9 = V6ForwardService(str(settings.db_path.with_name("v9-forward.db")), bus=realtime.bus, program="V9")
        app.state.v9 = v9
        app.state.v9_storage = v9_storage
        realtime.v9 = v9
        v9.start()
        log.info("V9 stock paper: %s", v9.health()["status"])
        # V11 SCAN: multi-coin scanner bots on one ordered Bybit tape (public data), its own database.
        v11_storage = Storage(str(settings.db_path.with_name("v11-forward.db")))
        v11 = V6ForwardService(str(settings.db_path.with_name("v11-forward.db")), bus=realtime.bus, program="V11")
        app.state.v11 = v11
        app.state.v11_storage = v11_storage
        realtime.v11 = v11
        v11.start()
        log.info("V11 scan paper: %s", v11.health()["status"])
        # V12 BIZZY: beebots' Bizzy Bee day breakout and her Jev twin, the same ordered Bybit tape, its own database.
        v12_storage = Storage(str(settings.db_path.with_name("v12-forward.db")))
        v12 = V6ForwardService(str(settings.db_path.with_name("v12-forward.db")), bus=realtime.bus, program="V12")
        app.state.v12 = v12
        app.state.v12_storage = v12_storage
        realtime.v12 = v12
        v12.start()
        log.info("V12 Bizzy paper: %s", v12.health()["status"])
        # V13 SNAPBACK: limit-order (maker) mean reversion over the V11 coins, the same ordered Bybit tape, its own db.
        v13_storage = Storage(str(settings.db_path.with_name("v13-forward.db")))
        v13 = V6ForwardService(str(settings.db_path.with_name("v13-forward.db")), bus=realtime.bus, program="V13")
        app.state.v13 = v13
        app.state.v13_storage = v13_storage
        realtime.v13 = v13
        v13.start()
        log.info("V13 Snapback paper: %s", v13.health()["status"])
        # V14 HTF: copies of the best strategies that check the higher timeframes first, the same tape, its own db.
        v14_storage = Storage(str(settings.db_path.with_name("v14-forward.db")))
        v14 = V6ForwardService(str(settings.db_path.with_name("v14-forward.db")), bus=realtime.bus, program="V14")
        app.state.v14 = v14
        app.state.v14_storage = v14_storage
        realtime.v14 = v14
        v14.start()
        log.info("V14 HTF paper: %s", v14.health()["status"])
        # TELEGRAM: every live paper trade, TP hit and close to the operator's bot (token from the encrypted vault).
        from app.core.roster_view import roster_payload
        from app.live.telegram import TelegramNotifier
        from app.live.trade_chart import chart_for_open
        program_svc = {"v6": v6, "v7": v7, "v8": v8, "v9": v9, "v11": v11, "v12": v12, "v13": v13, "v14": v14}
        program_db = {p: str(settings.db_path if p == "v6" else settings.db_path.with_name(f"{p}-forward.db"))
                      for p in program_svc}

        from app.live.trade_stats import bot_record, setup_confidence

        def _experiment(program: str) -> str | None:
            svc = program_svc.get(program)
            return (svc.health() or {}).get("experiment_id") if svc is not None else None

        def _bot_stats(program: str, key: str) -> tuple[int, int] | None:
            """(closed trades, wins) of one bot in its program's current experiment, read-only from its database.
            The worker saves a closed trade BEFORE publishing its event, so a close message counts its own trade."""
            eid, path = _experiment(program), program_db.get(program)
            return bot_record(path, eid, key) if eid and path else None

        def _confidence(program: str, ev: dict) -> tuple[int, int, str] | None:
            """The setup's measured win chance (app/live/trade_stats.setup_confidence)."""
            path = program_db.get(program)
            if not path or not ev.get("strategy_id"):
                return None
            return setup_confidence(path, _experiment(program), str(ev["strategy_id"]), str(ev.get("role") or "CONTROL"),
                                    int(time.time() * 1000))
        telegram = TelegramNotifier(mirror.providers, str(settings.data_dir),
                                    names=lambda: roster_payload(app.state).get("names") or {}, stats=_bot_stats,
                                    confidence=_confidence,
                                    chart=lambda program, ev, name: chart_for_open(program_db[program], program, ev, name)
                                    if program in program_db else None)
        app.state.telegram = telegram
        for svc in (v6, v7, v8, v9, v11, v12, v13, v14):
            svc.listeners.append(telegram.on_event)
        telegram.start()
        log.info("telegram notifications: %s", {k: v for k, v in telegram.health().items() if k in ("configured", "linked")})
        mirror.alert_fn = telegram.alert          # live-mirror incidents, safe mode, kill switch -> the operator
        # WATCHDOG: program health -> Telegram alerts, the live mirror's SAFE MODE and /public/health/deep.
        from app.live.watchdog import Watchdog
        watchdog = Watchdog(program_svc, mirror=mirror,
                            notifier=telegram, names=lambda: roster_payload(app.state).get("names") or {})
        app.state.watchdog = watchdog
        watchdog.start()
        # V10 SCOUT: news + Reddit attention research (no trading); keys from the same encrypted vault.
        from app.scout.service import ScoutService
        scout = ScoutService(str(settings.db_path.with_name("scout.db")), providers=mirror.providers, bus=realtime.bus)
        app.state.scout = scout
        scout.start()
        log.info("V10 scout: %s", scout.health()["state"])
        from app.autoresearch.service import ResearchService
        research = ResearchService(settings.db_path.with_name("autonomous-research.db"))
        app.state.research = research
        research.start()
        log.info("Autonomous market research enabled=%s", research.enabled)
        from app.stock_trend.service import StockTrendService
        stock_trend = StockTrendService(settings.db_path.with_name('stock-trend.db'), mirror.providers,
                                       secure=bool(settings.dashboard_password) and not settings.allow_no_auth)
        app.state.stock_trend = stock_trend
        stock_trend.start()
        from app.video_breakout.service import VideoBreakoutService
        video_breakout = VideoBreakoutService(settings.data_dir / 'video-breakout.json',
                                             can_enter=lambda: not engine.risk.state.killed)
        app.state.video_breakout = video_breakout
        video_breakout.start()
        if v6.enabled or v7.enabled or v8.enabled:
            from app.core.display_prices import DisplayPrices
            from app.competition.v6_config import load_freeze
            from app.competition.v8_config import COINS as V8_COINS
            coins = list(dict.fromkeys(list((load_freeze() or {}).get("universe", {}).get("traded", [])) + list(V8_COINS)))
            realtime.display_prices = DisplayPrices(realtime.bus, [c + "USDT" for c in coins] + ["BTCUSDT", "ETHUSDT"])
            realtime.display_prices.start()
        app.state.boot_error = None
        log.info("PaperLab %s dry_run=%s symbols=%s strategies=%d db=%s", settings.venue.label, settings.dry_run,
                 ",".join(engine.symbols), len(classes), settings.db_path)
        if settings.is_live:
            log.critical("!!! LIVE OVERRIDE ACTIVE: %s - real money !!!", settings.venue.rest_base)

        async def _boot() -> None:
            try:
                await engine.boot()
            except Exception as exc:
                app.state.boot_error = f"{exc.__class__.__name__}: {redact.redact(str(exc))[:300]}"
                engine.set_state("error")
                log.exception("engine boot failed")

        boot_task = asyncio.create_task(_boot(), name="engine-boot")
        try:
            yield
        finally:
            boot_task.cancel()
            await video_breakout.close()
            await stock_trend.close()
            await asyncio.to_thread(research.stop)
            if realtime.display_prices is not None:
                await realtime.display_prices.stop()
            await asyncio.to_thread(shadow.stop)
            await asyncio.to_thread(v6.stop)
            await asyncio.to_thread(v7.stop)
            v7_storage.close()
            await mirror.stop()
            await asyncio.to_thread(v8.stop)
            v8_storage.close()
            await asyncio.to_thread(v9.stop)
            v9_storage.close()
            await asyncio.to_thread(v11.stop)
            v11_storage.close()
            telegram.stop()
            watchdog.stop()
            await asyncio.to_thread(v12.stop)
            v12_storage.close()
            await asyncio.to_thread(v13.stop)
            v13_storage.close()
            await asyncio.to_thread(v14.stop)
            v14_storage.close()
            await asyncio.to_thread(scout.stop)
            await realtime.stop()
            await engine.stop()
            await client.close()
            storage.close()

    app = FastAPI(title="PaperLab", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.realtime = Realtime()
    app.add_middleware(BasicAuthMiddleware, password=settings.dashboard_password, allow_no_auth=settings.allow_no_auth)
    # Read-only cross-origin GETs on /api/public/* and /public/* only (external front ends, e.g. Lovable); added after
    # the auth middleware so it wraps it and answers preflights. Private routes stay same-origin (app/core/cors.py).
    from app.core.cors import PublicCORS
    from app.core.public_cache import PublicCache
    app.add_middleware(PublicCache)        # shared 10 s cache of the public program feeds (inside CORS: per-origin headers)
    app.add_middleware(PublicCORS)
    app.include_router(api.router)
    app.include_router(api_competition.router)
    app.include_router(api_public.router)
    app.include_router(api_jev.router)
    app.include_router(api_stream.router)
    from app.core import api_live_mirror, api_scout
    app.include_router(api_live_mirror.router)
    app.include_router(api_scout.public_router)
    app.include_router(api_scout.router)
    from app.core import api_autoresearch
    app.include_router(api_autoresearch.router)
    from app.core import api_stock_trend
    app.include_router(api_stock_trend.router)
    from app.core import api_video_breakout
    app.include_router(api_video_breakout.router)
    from app.core import api_lab                    # the private cost analyzer (+ /lab -> /public/competition)
    from app.ai.analyzer import Analyzer
    app.state.analyzer = Analyzer.from_env()
    app.include_router(api_lab.router)

    @app.get('/public/video-breakout', include_in_schema=False)
    def video_breakout_page():
        return RedirectResponse('/?p=video&bot=BTC-4H-BREAKOUT#bots/bots', status_code=307)

    @app.get('/public/stock-trend', include_in_schema=False)
    def stock_trend_page():
        return FileResponse(DASHBOARD_DIR / 'stock-trend.html', headers={'Cache-Control':'no-cache'})

    @app.get("/public/research", include_in_schema=False)
    def research_page():
        return FileResponse(DASHBOARD_DIR / "research.html", headers={"Cache-Control": "no-cache"})
    app.add_exception_handler(EngineError, api.engine_error_handler)  # type: ignore[arg-type]

    if DASHBOARD_DIR.exists():
        app.mount("/static", NoCacheStatic(directory=str(DASHBOARD_DIR)), name="static")

    def _competition_service() -> Any:
        svc = getattr(app.state, "competition", None)
        if svc is None:
            raise RuntimeError("competition service not started")
        return svc

    # ---- PUBLIC INSPECTION (app/core/inspection.py): JSON for readers that do not run JavaScript ----
    # GET/HEAD only, no auth, no cookies, no redirects, no custom headers needed. Allow-listed fields.
    @app.api_route("/public/inspection.json", methods=["GET", "HEAD"], include_in_schema=False)
    async def public_inspection() -> Any:
        from fastapi.responses import JSONResponse
        from app.core import inspection
        try:
            _competition_service()
        except RuntimeError:
            return JSONResponse({"ok": False, "error": "starting"}, status_code=503)
        live = inspection.live_paper(app.state)          # the engine lives on the event loop
        data = await asyncio.to_thread(inspection.build, app.state, live)
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=30"})

    # For an external uptime monitor (Uptime Kuma, UptimeRobot, ...): 200 when every program is healthy, 503 when one is
    # down or a live mirror is in SAFE MODE. Statuses and problems only: no keys, ids, balances or positions.
    @app.api_route("/public/health/deep", methods=["GET", "HEAD"], include_in_schema=False)
    async def public_health_deep() -> Any:
        wd = getattr(app.state, "watchdog", None)
        if wd is None:
            return JSONResponse({"ok": False, "error": "starting"}, status_code=503)
        data = wd.deep_health()
        return JSONResponse(data, status_code=200 if data["ok"] else 503, headers={"Cache-Control": "no-store"})

    @app.api_route("/public/inspection/bot/{bot_id:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def public_inspection_bot(bot_id: str) -> Any:
        from fastapi.responses import JSONResponse
        from app.core import inspection
        try:
            _competition_service()
        except RuntimeError:
            return JSONResponse({"ok": False, "error": "starting"}, status_code=503)
        bid = bot_id[:-5] if bot_id.endswith(".json") else bot_id
        live = inspection.live_paper(app.state) if bid.startswith("live:") else []
        data = await asyncio.to_thread(inspection.bot_detail, app.state, bid, live)
        if data is None:
            return JSONResponse({"ok": False, "error": "unknown bot_id", "bot_id": bid[:120]}, status_code=404)
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=60"})

    # Registered BEFORE the /public/competition/{rest} catch-all below, which would otherwise
    # swallow "report" and "report.txt" and serve the JavaScript shell instead.
    @app.api_route("/public/competition/report", methods=["GET", "HEAD"], include_in_schema=False)
    async def public_report() -> Any:
        """Server-rendered inspection report: the real values are in the HTML, no JS required."""
        from app.core import report as report_mod
        try:
            svc = _competition_service()
        except RuntimeError:
            return HTMLResponse("<h1>PaperLab</h1><p>Competition service not started.</p>",
                                status_code=503)
        data = report_mod.collect(svc, jev=getattr(app.state, "jev", None), shadow=getattr(app.state, "shadow", None), v6=getattr(app.state, "v6", None))
        return HTMLResponse(report_mod.render_html(data), headers={"Cache-Control": "no-store"})

    @app.api_route("/public/competition/report.txt", methods=["GET", "HEAD"],
                   include_in_schema=False)
    async def public_report_text() -> Any:
        from app.core import report as report_mod
        try:
            svc = _competition_service()
        except RuntimeError:
            return PlainTextResponse("competition service not started", status_code=503)
        data = report_mod.collect(svc, jev=getattr(app.state, "jev", None), shadow=getattr(app.state, "shadow", None), v6=getattr(app.state, "v6", None))
        return PlainTextResponse(report_mod.render_text(data),
                                 headers={"Cache-Control": "no-store"})

    @app.api_route("/public/competition/report/jev/pair/{pair:path}", methods=["GET", "HEAD"],
                   include_in_schema=False)
    async def public_report_jev_pair(pair: str) -> Any:
        """CONTROL vs +JEV for one pair, server-rendered, with the decision inspector."""
        from app.core import report as report_mod
        try:
            svc = _competition_service()
        except RuntimeError:
            return HTMLResponse("<h1>PaperLab</h1><p>Competition service not started.</p>",
                                status_code=503)
        d = report_mod.collect_jev_pair(svc, pair)
        if d is None:
            return HTMLResponse(
                f"<!doctype html><html><head><meta charset=utf-8><title>not found</title></head>"
                f"<body><h1>Unknown pair</h1><p>No pair <code>{__import__('html').escape(pair)}</code> in "
                f"the latest CONTROL vs +JEV experiment.</p>"
                f"<p><a href='/public/competition/report#jev'>back to report</a></p></body></html>",
                status_code=404)
        return HTMLResponse(report_mod.render_jev_pair_html(svc, d), headers={"Cache-Control": "no-store"})

    @app.api_route("/public/competition/report/arena/bot/{key:path}", methods=["GET", "HEAD"],
                   include_in_schema=False)
    async def public_report_arena_bot(key: str) -> Any:
        """One arena bot, server-rendered: identity, gates, costs, refusals and its trade ledger."""
        from app.core import report as report_mod
        try:
            svc = _competition_service()
        except RuntimeError:
            return HTMLResponse("<h1>PaperLab</h1><p>Competition service not started.</p>",
                                status_code=503)
        b = report_mod.collect_arena_bot(svc, key)
        if b is None:
            return HTMLResponse(
                f"<!doctype html><html><head><meta charset=utf-8><title>not found</title></head>"
                f"<body><h1>Unknown arena bot</h1><p>No record for "
                f"<code>{__import__('html').escape(key)}</code> in the latest arena run.</p>"
                f"<p><a href='/public/competition/report#arena'>back to report</a></p></body></html>",
                status_code=404)
        return HTMLResponse(report_mod.render_arena_bot_html(svc, b),
                            headers={"Cache-Control": "no-store"})

    @app.api_route("/public/competition/report/bot/{key:path}", methods=["GET", "HEAD"],
                   include_in_schema=False)
    async def public_report_bot(key: str) -> Any:
        from app.core import report as report_mod
        try:
            svc = _competition_service()
        except RuntimeError:
            return HTMLResponse("<h1>PaperLab</h1><p>Competition service not started.</p>",
                                status_code=503)
        payload = report_mod.collect_bot(svc, key)
        if payload is None:
            return HTMLResponse(
                f"<!doctype html><html><head><meta charset=utf-8><title>not found</title></head>"
                f"<body><h1>Unknown competitor</h1><p>No record for "
                f"<code>{__import__('html').escape(key)}</code> in the current runs.</p>"
                f"<p><a href='/public/competition/report'>back to report</a></p></body></html>",
                status_code=404)
        return HTMLResponse(report_mod.render_bot_html(svc, payload),
                            headers={"Cache-Control": "no-store"})

    @app.api_route("/public/competition", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/public/competition/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def public_competition(rest: str = "") -> Any:
        """Read-only inspection shell. Every deep link serves the same page; the path after
        /public/competition is routed client-side, so /public/competition/validation and
        /public/competition/bot/<key> are directly addressable and reloadable."""
        page = DASHBOARD_DIR / "public.html"
        if not page.exists():
            return HTMLResponse("<h1>PaperLab</h1><p>Public inspection page not built.</p>",
                                status_code=404)
        return HTMLResponse(_index_html(page), headers={"Cache-Control": "no-store"})

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index() -> Any:
        index_file = DASHBOARD_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>PaperLab</h1><p>Dashboard not built yet. API is live at /api/state.</p>")
        # Cache-busting by content: asset URLs carry a hash of the asset files, so a deploy changes the
        # URL itself. no-cache headers alone are not enough - a browser holding an already-cached copy
        # keeps using it until a hard refresh, which is how new markup ends up driven by old javascript.
        return HTMLResponse(_index_html(index_file), headers={"Cache-Control": "no-store"})

    return app


try:
    app = create_app()
except ConfigError as exc:  # make misconfiguration obvious in the process log
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("paperlab").critical("CONFIG ERROR: %s", exc)
    raise


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    s = load_settings()
    uvicorn.run("app.main:app", host=s.bind_host, port=s.port, reload=False)
