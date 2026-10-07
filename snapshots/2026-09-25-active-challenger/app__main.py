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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
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
                 exempt: tuple[str, ...] = ("/api/health", "/", "/favicon.ico"),
                 exempt_prefixes: tuple[str, ...] = ("/static/", "/public/", "/api/public/")):
        # The dashboard shell (HTML/JS/CSS) carries no data and is served openly; the page asks for the
        # password once and sends it as Basic auth on every /api call (no native browser prompt).
        #
        # `/public/` and `/api/public/` are the read-only inspection surface: no password, so an
        # outside reviewer can look at the real Competition UI. They are safe to exempt because
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
            await asyncio.to_thread(shadow.stop)
            await asyncio.to_thread(v6.stop)
            await realtime.stop()
            await engine.stop()
            await client.close()
            storage.close()

    app = FastAPI(title="PaperLab", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.realtime = Realtime()
    app.add_middleware(BasicAuthMiddleware, password=settings.dashboard_password, allow_no_auth=settings.allow_no_auth)
    app.include_router(api.router)
    app.include_router(api_competition.router)
    app.include_router(api_public.router)
    app.include_router(api_jev.router)
    app.include_router(api_stream.router)
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
