"""Settings, venues and the live-endpoint guard.

This is the ONLY module under app/ that may contain live Binance hostnames
(tests/test_guard.py enforces that). Everything else asks `Settings.venue`.

Rules enforced here:
* the .env file is loaded ONLY from <project>/.env by explicit path (never a parent directory);
* any live production host is refused unless MODE == LIVE_OVERRIDE_I_UNDERSTAND;
* DRY_RUN=false requires both API keys;
* ALLOW_NO_AUTH is a local-only escape hatch (refused on Railway or non-loopback binds).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

# Live production hosts. Referenced only to REFUSE them.
LIVE_HOSTS: frozenset[str] = frozenset({
    "api.binance.com", "api1.binance.com", "api2.binance.com", "api3.binance.com", "api4.binance.com",
    "api-gcp.binance.com", "fapi.binance.com", "dapi.binance.com", "papi.binance.com", "eapi.binance.com",
    "fstream.binance.com", "dstream.binance.com", "stream.binance.com", "ws-api.binance.com",
    "ws-fapi.binance.com", "fstream-auth.binance.com",
})
# Bybit production hosts. Named here ONLY so they can be refused: api.bybit.com is real money and has
# no demo twin, so a REST_BASE_OVERRIDE pointing at it must fail the same way a live Binance host does.
LIVE_HOSTS_BYBIT: frozenset[str] = frozenset({
    "api.bybit.com", "api.bytick.com", "api.bybit.nl", "api.byhkbit.com",
    "stream.bybit.com", "stream.bytick.com",
})
# Production METADATA sources. These are read-only, unauthenticated, and must never carry an order.
#
# A competition that means to model real trading has to size and liquidate against PRODUCTION
# constraints even though every order is simulated: testnet publishes different lot sizes and a
# different liquidation fee, and its `maintMarginPercent` is the generic 20x-tier boilerplate rather
# than the symbol's real risk brackets. They live here because app/config.py is the only module
# allowed to name a live host (tests/test_guard.py enforces that); callers import these constants
# instead of spelling a hostname out.
#
# The trading client NEVER uses these. Its host allowlist is `Venue.allowed_hosts`, unchanged.
METADATA_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
METADATA_LEVERAGE_BRACKETS = "https://www.binance.com/bapi/futures/v1/friendly/future/common/brackets"
METADATA_BOOK_TICKER = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
METADATA_TICKER_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
# Live MARKET DATA for the live shadow (app/live): public, unauthenticated, read-only. Same rule as
# above -- nothing that uses these holds a key or can place an order. Binance split its USD-M
# websocket streams: klines are served on /market, bookTicker on /public; the legacy /stream path
# still answers but delivers bookTicker and NO klines (verified 2026-09-23 from Railway and locally).
METADATA_KLINES = "https://fapi.binance.com/fapi/v1/klines"
METADATA_PREMIUM_INDEX = "https://fapi.binance.com/fapi/v1/premiumIndex"
METADATA_FUNDING_RATE = "https://fapi.binance.com/fapi/v1/fundingRate"
MARKETDATA_WS_KLINES = "wss://fstream.binance.com/market/stream"
MARKETDATA_WS_BOOK = "wss://fstream.binance.com/public/stream"

LIVE_OVERRIDE = "LIVE_OVERRIDE_I_UNDERSTAND"
_LIVE_FUTURES_REST = "https://fapi.binance.com"
_LIVE_FUTURES_WS = "wss://fstream.binance.com"
_LIVE_BYBIT_REST = "https://api.bybit.com"
_LIVE_BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
# Alpaca (US stocks / ETFs; app/exchange/alpaca_client.py). The PAPER trading host simulates every fill; the live
# host is real money and is used only by the operator-armed live mirror behind its own gates. Market data (REST
# bars and the free IEX real-time stream) is read-only but still needs the account's keys.
ALPACA_PAPER_REST = "https://paper-api.alpaca.markets"
ALPACA_LIVE_REST = "https://api.alpaca.markets"
ALPACA_DATA_REST = "https://data.alpaca.markets"
ALPACA_DATA_WS_IEX = "wss://stream.data.alpaca.markets/v2/iex"
# Reddit's official API for the V10 scout (read-only, application-only OAuth with the operator's own free app key).
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
# CoinGecko's public trending list (no key, no login): the coins crypto users search for most right now.
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
# Autonomous research uses public GET market data only, never an order/account client.
AUTORESEARCH_SPOT_REST = "https://data-api.binance.vision"
AUTORESEARCH_PERP_REST = _LIVE_FUTURES_REST


class ConfigError(RuntimeError):
    """Raised for any configuration that must stop the process before it touches a venue."""


@dataclass(frozen=True)
class VenueCaps:
    kind: Literal["futures", "spot"]
    shorts: bool
    leverage: bool
    funding: bool
    mark_price: bool
    backstop: bool
    user_stream: bool


@dataclass(frozen=True)
class Venue:
    mode: str
    rest_base: str
    ws_base: str
    caps: VenueCaps
    allowed_hosts: frozenset[str]
    label: str
    exchange: Literal["binance", "bybit"] = "binance"

    @property
    def is_live(self) -> bool:
        return self.mode == LIVE_OVERRIDE


FUTURES_CAPS = VenueCaps("futures", shorts=True, leverage=True, funding=True, mark_price=True,
                         backstop=True, user_stream=True)
SPOT_CAPS = VenueCaps("spot", shorts=False, leverage=False, funding=False, mark_price=False,
                      backstop=False, user_stream=True)

VENUES: dict[str, Venue] = {
    "FUTURES_TESTNET": Venue(
        "FUTURES_TESTNET", "https://testnet.binancefuture.com", "wss://fstream.binancefuture.com", FUTURES_CAPS,
        frozenset({"testnet.binancefuture.com", "fstream.binancefuture.com", "stream.binancefuture.com"}),
        "TESTNET"),
    "FUTURES_DEMO": Venue(
        "FUTURES_DEMO", "https://demo-fapi.binance.com", "wss://demo-fstream.binance.com", FUTURES_CAPS,
        frozenset({"demo-fapi.binance.com", "demo-fstream.binance.com"}), "DEMO"),
    "SPOT_TESTNET": Venue(
        "SPOT_TESTNET", "https://testnet.binance.vision", "wss://stream.testnet.binance.vision", SPOT_CAPS,
        frozenset({"testnet.binance.vision", "stream.testnet.binance.vision"}), "SPOT TESTNET"),
    "BYBIT_TESTNET": Venue(
        "BYBIT_TESTNET", "https://api-testnet.bybit.com", "wss://stream-testnet.bybit.com/v5/public/linear",
        FUTURES_CAPS, frozenset({"api-testnet.bybit.com", "stream-testnet.bybit.com"}), "BYBIT TESTNET",
        exchange="bybit"),
}
MODE_ALIASES = {
    "BINANCE_FUTURES_TESTNET": "FUTURES_TESTNET", "BINANCE_FUTURES_DEMO": "FUTURES_DEMO",
    "BINANCE_SPOT_TESTNET": "SPOT_TESTNET", "TESTNET": "FUTURES_TESTNET", "DEMO": "FUTURES_DEMO",
    "SPOT": "SPOT_TESTNET", "BYBIT": "BYBIT_TESTNET", "BYBIT_DEMO": "BYBIT_TESTNET",
}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


@dataclass(frozen=True)
class Settings:
    mode: str
    venue: Venue
    api_key: str
    api_secret: str
    dashboard_password: str
    allow_no_auth: bool
    symbols: tuple[str, ...]
    engine_enabled: bool
    dry_run: bool
    strategy_starting_balance: float
    default_leverage: int
    data_dir: Path
    port: int
    bind_host: str
    auto_resume_after_halt: bool
    log_level: str
    # Defaults are BINANCE_USDM's; load_settings() replaces them with the configured venue's own
    # schedule (app.execution.config.fee_schedule_for). TAKER_FEE / MAKER_FEE override explicitly.
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    fee_source: str = "binance_usdm"
    slippage_bps_min: float = 1.0
    slippage_bps_max: float = 3.0
    backstop_factor: float = 0.70
    backstop_min_pct: float = 0.004
    daily_halt_pct: float = 0.12
    strategy_halt_pct: float = 0.25
    max_total_notional_mult: float = 8.0
    max_net_notional_mult: float = 4.0
    exchange_margin_use_max: float = 0.80
    risk_per_trade_pct: float = 0.04
    min_rr: float = 2.5
    min_stop_bps: float = 8.0
    max_fee_share_of_r: float = 0.25
    # PaperLab preference, NOT a Binance rule. Binance USD-M checks MIN_NOTIONAL against the
    # submitted order (price x qty; mark price for MARKET) and exempts reduce-only orders, so an
    # entry only has to clear 1x. 2.0 restores the old conservative rule that sized every entry so
    # a 50% exit would clear minNotional on its own. See docs/MIN_NOTIONAL_AUDIT.md.
    min_notional_safety_multiplier: float = 1.0
    max_live_strategies: int = 1        # how many books may be armed against ONE real account
    seed_results: bool = True          # import seeds/results.json.gz on boot
    auto_respawn_halted: bool = True
    max_lives_per_day: int = 8
    # The V1 paper bake-off (one isolated book per registered strategy). False stops it: every book boots
    # disarmed, nothing respawns, open PAPER positions are closed on boot (dry run only). The engine itself --
    # market feed, RiskManager, kill switch, router -- keeps running. docs/V5_RUNTIME_AUDIT.md
    bakeoff_enabled: bool = True
    timeframes: tuple[str, ...] = ("1m", "5m", "15m")
    candle_window: int = 600

    def paper_total(self, n_strategies: int) -> float:
        """Total paper capital = one isolated book per strategy. There is no shared pool."""
        return self.strategy_starting_balance * max(0, n_strategies)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperlab.db"

    @property
    def is_live(self) -> bool:
        return self.venue.is_live

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_live_host(url_or_host: str) -> bool:
    host = host_of(url_or_host) if "://" in url_or_host else url_or_host.lower()
    if host in LIVE_HOSTS or host in LIVE_HOSTS_BYBIT:
        return True
    if host.endswith(".bybit.com") or host.endswith(".bytick.com"):
        return "testnet" not in host and "demo" not in host
    return host.endswith(".binance.com") and "testnet" not in host and "demo" not in host


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    val = env.get(key)
    return default if val is None else str(val).strip()


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key, "")
    if raw == "":
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} must be true/false, got {raw!r}")


def _num(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = _get(env, key, "")
    try:
        val = float(raw) if raw else float(default)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not (lo <= val <= hi):
        raise ConfigError(f"{key}={val} outside allowed range [{lo}, {hi}]")
    return val


def _resolve_venue(env: Mapping[str, str], mode: str) -> Venue:
    rest_override = _get(env, "REST_BASE_OVERRIDE")
    ws_override = _get(env, "WS_BASE_OVERRIDE")
    if mode == LIVE_OVERRIDE:
        # The one door to real money, for either exchange. EXCHANGE picks which.
        exchange = _get(env, "EXCHANGE", "binance").lower()
        if exchange not in ("binance", "bybit"):
            raise ConfigError(f"EXCHANGE must be 'binance' or 'bybit', got {exchange!r}")
        bybit = exchange == "bybit"
        rest = rest_override or (_LIVE_BYBIT_REST if bybit else _LIVE_FUTURES_REST)
        ws = ws_override or (_LIVE_BYBIT_WS if bybit else _LIVE_FUTURES_WS)
        hosts = frozenset(h for h in (host_of(rest), host_of(ws)) if h)
        return Venue(LIVE_OVERRIDE, rest, ws, FUTURES_CAPS, hosts, f"LIVE {exchange.upper()}",
                     exchange=exchange)
    base = VENUES[mode]
    rest = rest_override or base.rest_base
    ws = ws_override or base.ws_base
    for url in (rest, ws):
        if is_live_host(url):
            raise ConfigError(
                f"{url} looks like LIVE production. Refusing: set MODE={LIVE_OVERRIDE} only if you really mean it.")
    hosts = set(base.allowed_hosts) | {host_of(rest), host_of(ws)}
    # `exchange` must be carried over: dropping it silently makes every venue look like Binance, which
    # picks the wrong client, the wrong key pair and the wrong websocket protocol.
    return Venue(base.mode, rest, ws, base.caps, frozenset(h for h in hosts if h), base.label,
                 exchange=base.exchange)


def load_settings(env: Mapping[str, str] | None = None, env_path: Path | None = ENV_PATH) -> Settings:
    """Build Settings. `env` overrides the process environment (tests); .env is read ONLY from env_path."""
    if env is None:
        if env_path is not None and env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
        env = dict(os.environ)

    raw_mode = _get(env, "MODE", "FUTURES_TESTNET").upper()
    mode = MODE_ALIASES.get(raw_mode, raw_mode)
    if mode not in VENUES and mode != LIVE_OVERRIDE:
        raise ConfigError(f"Unknown MODE={raw_mode!r}. Use one of: {', '.join(VENUES)}.")
    venue = _resolve_venue(env, mode)

    symbols = tuple(s.strip().upper() for s in _get(env, "SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
                    if s.strip())
    if not symbols:
        raise ConfigError("SYMBOLS is empty")
    for sym in symbols:
        if not _SYMBOL_RE.match(sym):
            raise ConfigError(f"SYMBOLS entry {sym!r} is not a Binance symbol")
    if len(set(symbols)) != len(symbols):
        raise ConfigError("SYMBOLS contains duplicates")

    password = _get(env, "DASHBOARD_PASSWORD")
    allow_no_auth = _bool(env, "ALLOW_NO_AUTH", False)
    bind_host = _get(env, "BIND_HOST", "127.0.0.1") or "127.0.0.1"
    on_railway = any(_get(env, k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID",
                                            "RAILWAY_VOLUME_MOUNT_PATH"))
    loopback = bind_host in ("127.0.0.1", "localhost", "::1")
    if allow_no_auth and (on_railway or (_get(env, "PORT") and not loopback)):
        raise ConfigError("ALLOW_NO_AUTH=true is only allowed for a local loopback run; set DASHBOARD_PASSWORD.")
    if not password and not allow_no_auth:
        raise ConfigError("DASHBOARD_PASSWORD is empty. Set it (or ALLOW_NO_AUTH=true for a local loopback run).")

    # Each venue reads its OWN key pair, so a Binance key can never be handed to Bybit (or the reverse)
    # just because it happens to be exported in the environment.
    prefix = "BYBIT" if venue.exchange == "bybit" else "BINANCE"
    api_key = _get(env, f"{prefix}_API_KEY")
    api_secret = _get(env, f"{prefix}_API_SECRET")
    dry_run = _bool(env, "DRY_RUN", True)
    if not dry_run and not (api_key and api_secret):
        raise ConfigError(f"DRY_RUN=false needs {prefix}_API_KEY and {prefix}_API_SECRET (testnet keys).")

    from app.execution.config import fee_schedule_for
    sched = fee_schedule_for(venue.exchange, venue.caps.kind)

    data_dir_raw = _get(env, "DATA_DIR") or _get(env, "RAILWAY_VOLUME_MOUNT_PATH")
    data_dir = Path(data_dir_raw) if data_dir_raw else PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        mode=mode, venue=venue, api_key=api_key, api_secret=api_secret,
        dashboard_password=password, allow_no_auth=allow_no_auth, symbols=symbols,
        engine_enabled=_bool(env, "ENGINE_ENABLED", True), dry_run=dry_run,
        strategy_starting_balance=_num(env, "STRATEGY_STARTING_BALANCE", 100, 10, 1e7),
        default_leverage=int(_num(env, "DEFAULT_LEVERAGE", 20, 1, 20)),
        data_dir=data_dir, port=int(_num(env, "PORT", 8540, 1, 65535)), bind_host=bind_host,
        auto_resume_after_halt=_bool(env, "AUTO_RESUME_AFTER_HALT", False),
        log_level=_get(env, "LOG_LEVEL", "INFO").upper() or "INFO",
        taker_fee=_num(env, "TAKER_FEE", sched.taker_rate, 0, 0.01),
        maker_fee=_num(env, "MAKER_FEE", sched.maker_rate, 0, 0.01),
        fee_source=("env override" if (_get(env, "TAKER_FEE") or _get(env, "MAKER_FEE")) else sched.source),
        slippage_bps_min=_num(env, "SLIPPAGE_BPS_MIN", 1.0, 0, 50),
        slippage_bps_max=_num(env, "SLIPPAGE_BPS_MAX", 3.0, 0, 50),
        daily_halt_pct=_num(env, "DAILY_HALT_PCT", 0.12, 0.01, 0.9),
        strategy_halt_pct=_num(env, "STRATEGY_HALT_PCT", 0.25, 0.01, 0.9),
        max_total_notional_mult=_num(env, "MAX_TOTAL_NOTIONAL_MULT", 8.0, 0.1, 100),
        max_net_notional_mult=_num(env, "MAX_NET_NOTIONAL_MULT", 4.0, 0.1, 100),
        risk_per_trade_pct=_num(env, "RISK_PER_TRADE_PCT", 0.04, 0.001, 0.2),
        min_stop_bps=_num(env, "MIN_STOP_BPS", 8.0, 1.0, 200.0),
        max_fee_share_of_r=_num(env, "MAX_FEE_SHARE_OF_R", 0.25, 0.01, 1.0),
        min_notional_safety_multiplier=_num(env, "MIN_NOTIONAL_SAFETY_MULTIPLIER", 1.0, 1.0, 5.0),
        max_live_strategies=int(_num(env, "MAX_LIVE_STRATEGIES", 1, 1, 20)),
        seed_results=_bool(env, "SEED_RESULTS", True),
        auto_respawn_halted=_bool(env, "AUTO_RESPAWN_HALTED", True),
        max_lives_per_day=int(_num(env, "MAX_LIVES_PER_DAY", 8, 1, 100)),
        bakeoff_enabled=_bool(env, "BAKEOFF_ENABLED", True),
    )


class RedactFilter(logging.Filter):
    """Masks API key / secret / listenKey values in every log record."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 8]

    def add(self, secret: str) -> None:
        if secret and len(secret) >= 8 and secret not in self._secrets:
            self._secrets.append(secret)

    def redact(self, text: str) -> str:
        for s in self._secrets:
            if s in text:
                text = text.replace(s, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        masked = self.redact(msg)
        if masked != msg:
            record.msg = masked
            record.args = ()
        return True


def redact(text: str, settings: Settings) -> str:
    return RedactFilter([settings.api_key, settings.api_secret]).redact(text)
