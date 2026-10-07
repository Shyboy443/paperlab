"""Live-endpoint guard, config refusals, secret redaction, account-mode abort and the auth middleware."""
from __future__ import annotations

import base64
import logging
import re

import ccxt.async_support as ccxt_async
import pytest

import app.config as config
from app.config import ConfigError, RedactFilter, VENUES, host_of, load_settings
from app.core.types import ExchangePosition
from app.exchange.client import ExchangeClient
from app.exchange.guard import AccountModeError, GuardedBinanceSpot, GuardedBinanceUSDM, LiveEndpointBlocked, build_exchange
from app.main import BasicAuthMiddleware
from tests.conftest import settings_factory

LIVE_HOSTS = ("api.binance.com", "fapi.binance.com")
LIVE_RE = re.compile(r"(?<![\w.-])(api|fapi|fstream|stream)\.binance\.com")


def _urls(obj) -> list[str]:
    """Flatten every string in a (possibly nested) ccxt urls map."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [u for v in obj.values() for u in _urls(v)]
    if isinstance(obj, (list, tuple)):
        return [u for v in obj for u in _urls(v)]
    return []


class _ParentFetchCalled(AssertionError):
    pass


async def _boom(self, url, method="GET", headers=None, body=None):
    raise _ParentFetchCalled(f"parent fetch reached for {method} {url}")


# ---- build_exchange -----------------------------------------------------------------------

class TestBuildExchange:
    @pytest.mark.parametrize("mode", ["FUTURES_TESTNET", "FUTURES_DEMO", "SPOT_TESTNET"])
    async def test_urls_point_at_the_venue_and_never_at_live(self, mode, tmp_path):
        s = settings_factory(mode=mode, data_dir=str(tmp_path / "d"))
        ex = build_exchange(s)
        try:
            venue = VENUES[mode]
            rest = venue.rest_base.rstrip("/")
            futures = venue.caps.kind == "futures"
            assert isinstance(ex, GuardedBinanceUSDM if futures else GuardedBinanceSpot)
            assert ex.allowed_hosts == venue.allowed_hosts
            assert "test" not in ex.urls
            api = ex.urls["api"]
            for url in _urls(api):
                assert host_of(url) not in LIVE_HOSTS, url
                assert not config.is_live_host(url), url
            if futures:
                assert api["fapiPublic"] == f"{rest}/fapi/v1"
                assert api["fapiPrivate"] == f"{rest}/fapi/v1"
                assert api["fapiPrivateV2"] == f"{rest}/fapi/v2"
                assert api["fapiData"] == f"{rest}/futures/data"
                assert api["sapi"] == "https://blocked.invalid"
                assert api["public"] == "https://blocked.invalid"
            else:
                assert api["public"] == f"{rest}/api/v3"
                assert api["private"] == f"{rest}/api/v3"
                assert api["fapiPublic"] == "https://blocked.invalid"
                assert api["sapi"] == "https://blocked.invalid"
            for url in _urls(api):
                assert host_of(url) in venue.allowed_hosts or host_of(url) == "blocked.invalid", url
        finally:
            await ex.close()

    @pytest.mark.parametrize("mode", ["FUTURES_TESTNET", "FUTURES_DEMO", "SPOT_TESTNET"])
    async def test_fetch_refuses_live_hosts_before_any_socket(self, mode, monkeypatch, tmp_path):
        monkeypatch.setattr(ccxt_async.binanceusdm, "fetch", _boom)
        monkeypatch.setattr(ccxt_async.binance, "fetch", _boom)
        s = settings_factory(mode=mode, data_dir=str(tmp_path / "d"))
        ex = build_exchange(s)
        try:
            for url in ("https://api.binance.com/api/v3/ping", "https://fapi.binance.com/fapi/v1/ping",
                        "https://blocked.invalid/sapi/v1/capital/config/getall", "wss://fstream.binance.com/ws"):
                with pytest.raises(LiveEndpointBlocked):
                    await ex.fetch(url)
            # an allowed venue host is handed to the (patched) parent, proving the guard is the only gate
            with pytest.raises(_ParentFetchCalled):
                await ex.fetch(f"{s.venue.rest_base}/fapi/v1/ping")
        finally:
            await ex.close()

    async def test_guard_message_names_the_allowed_hosts(self, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"))
        ex = build_exchange(s)
        try:
            with pytest.raises(LiveEndpointBlocked, match="testnet.binancefuture.com"):
                await ex.fetch("https://api.binance.com/api/v3/time")
        finally:
            await ex.close()


# ---- source scan --------------------------------------------------------------------------

def test_only_config_py_names_live_hosts():
    app_dir = config.PROJECT_ROOT / "app"
    offenders = []
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(config.PROJECT_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LIVE_RE.search(line) and rel != "app/config.py":
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == []
    assert LIVE_RE.search((app_dir / "config.py").read_text(encoding="utf-8")), "config.py holds the refusal list"


def test_live_host_regex_matches_precisely():
    assert LIVE_RE.search("https://api.binance.com/api/v3/ping")
    assert LIVE_RE.search("wss://fstream.binance.com/stream")
    assert LIVE_RE.search("stream.binance.com:9443")
    assert not LIVE_RE.search("https://testnet.binancefuture.com/fapi/v1")
    assert not LIVE_RE.search("https://demo-fapi.binance.com/fapi/v1")
    assert not LIVE_RE.search("wss://demo-fstream.binance.com/ws")
    assert not LIVE_RE.search("https://testnet.binance.vision/api/v3")


# ---- load_settings ------------------------------------------------------------------------

class TestLoadSettings:
    def test_live_rest_override_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="LIVE"):
            settings_factory(data_dir=str(tmp_path / "d"), REST_BASE_OVERRIDE="https://fapi.binance.com")

    def test_live_ws_override_is_refused(self, tmp_path):
        with pytest.raises(ConfigError):
            settings_factory(data_dir=str(tmp_path / "d"), WS_BASE_OVERRIDE="wss://fstream.binance.com")

    def test_empty_dashboard_password_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="DASHBOARD_PASSWORD"):
            settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="")

    def test_allow_no_auth_is_refused_on_railway(self, tmp_path):
        with pytest.raises(ConfigError, match="ALLOW_NO_AUTH"):
            settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="", ALLOW_NO_AUTH="true",
                             RAILWAY_ENVIRONMENT="x")

    def test_allow_no_auth_is_refused_on_a_public_bind_with_port(self, tmp_path):
        with pytest.raises(ConfigError, match="ALLOW_NO_AUTH"):
            settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="", ALLOW_NO_AUTH="true",
                             BIND_HOST="0.0.0.0", PORT="8540")

    def test_allow_no_auth_works_on_loopback(self, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="", ALLOW_NO_AUTH="true")
        assert s.allow_no_auth is True and s.dashboard_password == ""

    def test_dry_run_false_needs_keys(self, tmp_path):
        env = {"MODE": "FUTURES_TESTNET", "DASHBOARD_PASSWORD": "pw", "DRY_RUN": "false", "DATA_DIR": str(tmp_path / "d")}
        with pytest.raises(ConfigError, match="BINANCE_API_KEY"):
            load_settings(env)
        s = load_settings({**env, "BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20})
        assert s.dry_run is False and s.has_keys

    def test_live_override_builds_a_live_venue(self, tmp_path):
        s = settings_factory(mode="LIVE_OVERRIDE_I_UNDERSTAND", data_dir=str(tmp_path / "d"))
        # the label now names the exchange ("LIVE BINANCE" / "LIVE BYBIT"); the dashboard keys its red
        # real-money badge off the LIVE prefix, so that prefix is the contract worth asserting
        assert s.venue.label.startswith("LIVE") and s.is_live and s.venue.is_live
        assert host_of(s.venue.rest_base) in config.LIVE_HOSTS

    def test_unknown_mode_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="Unknown MODE"):
            settings_factory(mode="MAINNET", data_dir=str(tmp_path / "d"))

    def test_env_path_is_the_project_dotenv(self):
        assert config.ENV_PATH == config.PROJECT_ROOT / ".env"
        assert config.PROJECT_ROOT.name == "paperlab"
        assert config.ENV_PATH.name == ".env"

    def test_venues_are_never_live(self):
        for venue in VENUES.values():
            for url in (venue.rest_base, venue.ws_base):
                assert not config.is_live_host(url), url
            assert not venue.is_live


# ---- RedactFilter --------------------------------------------------------------------------

def test_redact_filter_masks_key_and_secret():
    key, secret = "AKIA" + "k" * 20, "sec" + "s" * 20
    flt = RedactFilter([key, secret])
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "auth %s / %s failed", (key, secret), None)
    assert flt.filter(record) is True
    msg = record.getMessage()
    assert key not in msg and secret not in msg
    assert msg == "auth *** / *** failed"
    assert flt.redact(f"listenKey={key}") == "listenKey=***"
    short = RedactFilter(["abc"])  # too short to be a secret: never masked (would shred ordinary words)
    assert short.redact("abcdef") == "abcdef"


# ---- hedge mode abort -----------------------------------------------------------------------

class TestAccountMode:
    async def test_hedge_mode_with_open_positions_aborts(self, monkeypatch, tmp_path):
        client = ExchangeClient(settings_factory(dry_run=False, data_dir=str(tmp_path / "d")))
        try:
            async def dual(params=None):
                return {"dualSidePosition": "true"}

            async def positions(symbols, all_symbols=False):
                return {"BTCUSDT": ExchangePosition("BTCUSDT", 0.5, 65000.0, 60000.0, 100.0, 0.0, 15, 65000.0)}

            monkeypatch.setattr(client.ex, "fapiPrivateGetPositionSideDual", dual)
            monkeypatch.setattr(client, "fetch_positions", positions)
            with pytest.raises(AccountModeError, match="hedge"):
                await client.ensure_account_mode(["BTCUSDT"], 15)
        finally:
            await client.close()

    async def test_hedge_mode_without_positions_switches_and_reports_leverage(self, monkeypatch, tmp_path):
        client = ExchangeClient(settings_factory(dry_run=False, data_dir=str(tmp_path / "d")))
        try:
            calls: list[tuple[str, dict]] = []

            async def dual(params=None):
                return {"dualSidePosition": "true"}

            async def positions(symbols, all_symbols=False):
                return {}

            async def post_dual(params=None):
                calls.append(("dual", dict(params or {})))
                return {"code": 200, "msg": "success"}

            async def margin_type(params=None):
                calls.append(("margin", dict(params or {})))
                return {"code": 200, "msg": "success"}

            async def leverage(params=None):
                calls.append(("leverage", dict(params or {})))
                return {"symbol": params["symbol"], "leverage": str(params["leverage"]), "maxNotionalValue": "1000000"}

            monkeypatch.setattr(client.ex, "fapiPrivateGetPositionSideDual", dual)
            monkeypatch.setattr(client, "fetch_positions", positions)
            monkeypatch.setattr(client.ex, "fapiPrivatePostPositionSideDual", post_dual)
            monkeypatch.setattr(client.ex, "fapiPrivatePostMarginType", margin_type)
            monkeypatch.setattr(client.ex, "fapiPrivatePostLeverage", leverage)
            report = await client.ensure_account_mode(["BTCUSDT", "ETHUSDT"], 15)
            assert report["dual"] == "switched_to_one_way"
            assert report["isolated"] == ["BTCUSDT", "ETHUSDT"]
            assert report["leverage"] == {"BTCUSDT": 15, "ETHUSDT": 15}
            assert ("dual", {"dualSidePosition": "false"}) in calls
            assert ("margin", {"symbol": "BTCUSDT", "marginType": "ISOLATED"}) in calls
            assert ("leverage", {"symbol": "ETHUSDT", "leverage": 15}) in calls
        finally:
            await client.close()

    async def test_one_way_mode_skips_the_switch(self, monkeypatch, tmp_path):
        client = ExchangeClient(settings_factory(dry_run=False, data_dir=str(tmp_path / "d")))
        try:
            async def dual(params=None):
                return {"dualSidePosition": "false"}

            async def margin_type(params=None):
                return {}

            async def leverage(params=None):
                return {"leverage": 20}

            monkeypatch.setattr(client.ex, "fapiPrivateGetPositionSideDual", dual)
            monkeypatch.setattr(client.ex, "fapiPrivatePostMarginType", margin_type)
            monkeypatch.setattr(client.ex, "fapiPrivatePostLeverage", leverage)
            report = await client.ensure_account_mode(["BTCUSDT"], 20)
            assert report["dual"] is False and report["leverage"] == {"BTCUSDT": 20}
        finally:
            await client.close()

    async def test_spot_client_has_no_account_mode(self, tmp_path):
        client = ExchangeClient(settings_factory(mode="SPOT_TESTNET", dry_run=False, data_dir=str(tmp_path / "d")))
        try:
            assert await client.ensure_account_mode(["BTCUSDT"], 15) == {"mode": "spot"}
            assert await client.fetch_positions(["BTCUSDT"]) == {}
        finally:
            await client.close()


# ---- BasicAuthMiddleware -------------------------------------------------------------------

async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b'{"ok":true}'})


async def _call(app, method: str, path: str, headers: dict[str, str] | None = None):
    scope = {"type": "http", "method": method, "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


def _basic(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


AUTH = {"Authorization": _basic("admin", "test-pw")}


class TestBasicAuthMiddleware:
    @pytest.fixture
    def app(self):
        return BasicAuthMiddleware(_inner, password="test-pw", allow_no_auth=False)

    async def test_health_is_open(self, app):
        status, _, body = await _call(app, "GET", "/api/health")
        assert status == 200 and body == b'{"ok":true}'

    async def test_state_without_auth_is_401_with_challenge(self, app):
        status, headers, body = await _call(app, "GET", "/api/state")
        assert status == 401
        assert headers["www-authenticate"].startswith("Basic")
        assert b"unauthorized" in body

    async def test_state_with_admin_password_passes(self, app):
        status, _, body = await _call(app, "GET", "/api/state", AUTH)
        assert status == 200 and body == b'{"ok":true}'

    async def test_wrong_user_or_password_is_401(self, app):
        assert (await _call(app, "GET", "/api/state", {"Authorization": _basic("admin", "nope")}))[0] == 401
        assert (await _call(app, "GET", "/api/state", {"Authorization": _basic("root", "test-pw")}))[0] == 401
        assert (await _call(app, "GET", "/api/state", {"Authorization": "Bearer abc"}))[0] == 401
        assert (await _call(app, "GET", "/api/state", {"Authorization": "Basic !!!not-base64"}))[0] == 401

    async def test_post_without_csrf_header_is_403(self, app):
        status, _, body = await _call(app, "POST", "/api/kill", AUTH)
        assert status == 403 and b"X-PaperLab" in body

    async def test_post_with_csrf_header_passes(self, app):
        status, _, body = await _call(app, "POST", "/api/kill", {**AUTH, "X-PaperLab": "1"})
        assert status == 200 and body == b'{"ok":true}'

    async def test_post_needs_auth_before_csrf(self, app):
        status, _, _ = await _call(app, "POST", "/api/kill", {"X-PaperLab": "1"})
        assert status == 401

    async def test_dashboard_shell_and_static_are_open_but_api_is_not(self, app):
        assert (await _call(app, "GET", "/"))[0] == 200
        assert (await _call(app, "GET", "/static/app.js"))[0] == 200
        assert (await _call(app, "GET", "/api/strategies"))[0] == 401

    async def test_allow_no_auth_skips_basic_but_keeps_csrf(self):
        app = BasicAuthMiddleware(_inner, password="", allow_no_auth=True)
        assert (await _call(app, "GET", "/api/state"))[0] == 200
        assert (await _call(app, "POST", "/api/kill"))[0] == 403
        assert (await _call(app, "POST", "/api/kill", {"X-PaperLab": "1"}))[0] == 200

    async def test_non_http_scopes_pass_through(self, app):
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["type"])

        mw = BasicAuthMiddleware(inner, password="pw", allow_no_auth=False)
        await mw({"type": "lifespan"}, None, None)
        assert seen == ["lifespan"]


# ---- analysis surface --------------------------------------------------------------------

from app.core.storage import EXPORTABLE  # noqa: E402
from tests.test_analytics import SCOREBOARD_KEYS  # noqa: E402

DETAIL_KEYS = {"row", "scoreboard", "params", "state", "trades", "open_positions", "signals",
               "rejects_today", "daily", "notes", "meta", "lives"}


def test_exportable_covers_the_analysis_tables():
    assert "strategy_daily" in EXPORTABLE and "analysis_notes" in EXPORTABLE
    for table in ("fills", "signals", "orders", "equity", "events", "candles", "funding"):
        assert table in EXPORTABLE


async def test_every_exportable_table_actually_exports(engine):
    for table in EXPORTABLE:
        header = next(iter(engine.storage.export_csv(table)))
        assert header.strip(), table
    with pytest.raises(ValueError):
        next(iter(engine.storage.export_csv("meta")))


class TestStrategyDetail:
    async def test_it_exposes_trades_and_the_scoreboard(self, engine):
        detail = engine.strategy_detail("S01")
        assert DETAIL_KEYS <= set(detail)
        assert isinstance(detail["trades"], list)
        assert SCOREBOARD_KEYS <= set(detail["scoreboard"])
        assert SCOREBOARD_KEYS <= set(detail["row"]), "the grid row carries the scoreboard too"
        assert detail["scoreboard"]["allocation"] == 100.0
        assert detail["meta"]["halt_floor"] == 75.0
        assert detail["rejects_today"] == {}
        assert detail["daily"] == [] and detail["notes"] == []

    async def test_trades_page_is_paged_and_summarised(self, engine):
        page = engine.trades_page("S01", 0, 10)
        assert page["strategy_id"] == "S01" and page["epoch"] == engine.epoch
        assert page["trades"] == [] and page["total"] == 0
        assert page["summary"]["trades_total"] == 0

    async def test_unknown_strategy_is_404(self, engine):
        from app.core.engine import EngineError

        with pytest.raises(EngineError) as exc:
            engine.strategy_detail("S99")
        assert exc.value.status == 404
        with pytest.raises(EngineError):
            engine.trades_page("S99")


# ---- schema 3: the `life` columns the respawn bookkeeping needs -------------------------------

LIFE_COLUMNS = {"fills": {"life"}, "signals": {"life"}, "strategy_daily": {"life"},
                "strategy_state": {"life", "lives_today", "lives_day", "realized_all_lives",
                                   "params_custom_json"}}


def _columns(storage, table: str) -> set[str]:
    return {r[1] for r in storage.conn.execute(f"PRAGMA table_info({table})").fetchall()}


async def test_schema_carries_the_life_columns(engine):
    from app.core.storage import SCHEMA_VERSION

    # schema 5 competition_*, 6 validation_*, 7 arena_*, 8 jev_*, 9 shadow_*, 10 candidate_*,
    # 11 shadow_trades.rederived (continuous forward books), 12 v3_* (V3 aggressive arena),
    # 13 v31_* (V3.1 aggressive edge), 14 v4_* (V4 intraday specialists), 15 v5_* (V5 hourly / daily)
    assert SCHEMA_VERSION == 17
    for table in ("candidate_runs", "candidate_results"):
        assert _columns(engine.storage, table), table
    assert "rederived" in _columns(engine.storage, "shadow_trades")
    for table in ("v3_runs", "v3_bots", "v31_runs", "v31_bots", "v4_runs", "v4_bots", "v5_runs", "v5_bots"):
        assert _columns(engine.storage, table), table
    for table in ("shadow_sessions", "shadow_bots", "shadow_trades", "shadow_events", "shadow_decisions"):
        assert _columns(engine.storage, table), table
    assert engine.storage.get_meta("schema_version") == str(SCHEMA_VERSION)
    for table, wanted in LIFE_COLUMNS.items():
        assert wanted <= _columns(engine.storage, table), table


async def test_a_schema_2_database_is_migrated_in_place(tmp_path):
    """The life columns are additive: an older DB keeps its rows and gains life=1 defaults."""
    import sqlite3

    from app.core.storage import Storage

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE fills(id TEXT PRIMARY KEY, ts INTEGER, epoch INTEGER, strategy_id TEXT)")
    con.execute("INSERT INTO fills(id,ts,epoch,strategy_id) VALUES('f1',1,1,'S01')")
    con.commit()
    con.close()
    st = Storage(str(path))
    try:
        assert "life" in _columns(st, "fills")
        row = st.conn.execute("SELECT life FROM fills WHERE id='f1'").fetchone()
        assert row["life"] == 1, "existing rows belong to life 1"
    finally:
        st.close()


async def test_the_life_columns_are_exported(engine):
    for table in ("fills", "signals", "strategy_daily"):
        header = next(iter(engine.storage.export_csv(table)))
        assert "life" in [c.strip() for c in header.split(",")], table
