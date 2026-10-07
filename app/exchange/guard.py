"""GuardedBinance: a ccxt subclass that can only talk to the configured venue hosts.

Why: with sandbox mode ccxt has routed some unified calls (fetch_balance/fetch_positions) to the LIVE
spot sapi endpoints (ccxt issue #26487). We never call set_sandbox_mode(); instead the whole
urls['api'] map is replaced (unused keys -> https://blocked.invalid) and fetch() - ccxt's single
HTTP choke point - refuses any host outside `allowed_hosts` BEFORE a socket is opened.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, TypeVar

import ccxt.async_support as ccxt_async
from ccxt.base.errors import DDoSProtection, ExchangeError, NetworkError, RateLimitExceeded

from app.config import Settings, host_of

log = logging.getLogger("paperlab.exchange")
T = TypeVar("T")

# Binance error codes that retrying cannot fix.
NON_RETRYABLE = {-2010, -2013, -2014, -2015, -2019, -2022, -4003, -4028, -4046, -4061, -4120, -1111, -1121, -1102,
                 -1013, -2018}
_CODE_RE = re.compile(r'"code"\s*:\s*(-?\d+)')


class LiveEndpointBlocked(RuntimeError):
    """Raised before any network I/O when a request would leave the allowed venue hosts."""


class AccountModeError(RuntimeError):
    """The account is in a mode the lab refuses to trade (hedge/dual-side)."""


def binance_code(exc: BaseException) -> int | None:
    m = _CODE_RE.search(str(exc))
    return int(m.group(1)) if m else None


class _GuardMixin:
    allowed_hosts: frozenset[str] = frozenset()

    async def fetch(self, url: str, method: str = "GET", headers: Any = None, body: Any = None):  # type: ignore[override]
        host = host_of(url)
        if host not in self.allowed_hosts:
            raise LiveEndpointBlocked(f"refusing {method} {url}: host {host!r} is not an allowed venue host "
                                      f"{sorted(self.allowed_hosts)}")
        return await super().fetch(url, method, headers, body)  # type: ignore[misc]


class GuardedBinanceUSDM(_GuardMixin, ccxt_async.binanceusdm):
    async def fetch_time(self, params: dict | None = None) -> int:  # type: ignore[override]
        r = await self.fapiPublicGetTime(params or {})
        return int(r["serverTime"])


class GuardedBinanceSpot(_GuardMixin, ccxt_async.binance):
    async def fetch_time(self, params: dict | None = None) -> int:  # type: ignore[override]
        r = await self.publicGetTime(params or {})
        return int(r["serverTime"])


class GuardedBybit(_GuardMixin, ccxt_async.bybit):
    """Bybit v5. Every endpoint hangs off one host, so the whole urls['api'] map points at the venue."""

    async def fetch_time(self, params: dict | None = None) -> int:  # type: ignore[override]
        r = await self.publicGetV5MarketTime(params or {})
        return int(float(r["result"]["timeSecond"]) * 1000)


def _build_bybit(settings: Settings) -> GuardedBybit:
    ex = GuardedBybit({
        "apiKey": settings.api_key or None,
        "secret": settings.api_secret or None,
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {"defaultType": "swap", "adjustForTimeDifference": True, "recvWindow": 10_000,
                    "fetchCurrencies": False},
    })
    ex.allowed_hosts = frozenset(settings.venue.allowed_hosts)
    rest = settings.venue.rest_base.rstrip("/")
    # Unlike Binance there is no per-family base: v5 is one host. Replacing every key keeps the guard
    # meaningful even if ccxt adds a family we do not use.
    ex.urls["api"] = {key: rest for key in ex.urls["api"]}
    ex.urls.pop("test", None)
    return ex


def build_exchange(settings: Settings) -> GuardedBinanceUSDM | GuardedBinanceSpot | GuardedBybit:
    if settings.venue.exchange == "bybit":
        return _build_bybit(settings)
    futures = settings.venue.caps.kind == "futures"
    cls = GuardedBinanceUSDM if futures else GuardedBinanceSpot
    ex = cls({
        "apiKey": settings.api_key or None,
        "secret": settings.api_secret or None,
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {
            "defaultType": "future" if futures else "spot",
            "adjustForTimeDifference": True,
            "recvWindow": 10_000,
            "warnOnFetchOpenOrdersWithoutSymbol": False,
            "fetchCurrencies": False,
        },
    })
    ex.allowed_hosts = frozenset(settings.venue.allowed_hosts)
    rest = settings.venue.rest_base.rstrip("/")
    api: dict[str, Any] = {key: "https://blocked.invalid" for key in ex.urls["api"]}
    if futures:
        api.update({
            "fapiPublic": f"{rest}/fapi/v1", "fapiPublicV2": f"{rest}/fapi/v2", "fapiPublicV3": f"{rest}/fapi/v3",
            "fapiPrivate": f"{rest}/fapi/v1", "fapiPrivateV2": f"{rest}/fapi/v2", "fapiPrivateV3": f"{rest}/fapi/v3",
            "fapiData": f"{rest}/futures/data",
        })
    else:
        api.update({"public": f"{rest}/api/v3", "private": f"{rest}/api/v3", "v1": f"{rest}/api/v1",
                    "v3": f"{rest}/api/v3"})
    ex.urls["api"] = api
    ex.urls.pop("test", None)
    return ex


async def with_retry(factory: Callable[[], Awaitable[T]], *, label: str = "", attempts: int = 3,
                     exchange: Any = None) -> T:
    """Run an exchange call with bounded retries; never retries a blocked-host or non-retryable error."""
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await factory()
        except LiveEndpointBlocked:
            raise
        except (RateLimitExceeded, DDoSProtection) as exc:
            last = exc
            wait = 4.0 ** (attempt + 1)
            log.warning("%s rate limited (%s); sleeping %.0fs", label, exc.__class__.__name__, wait)
        except ExchangeError as exc:
            last = exc
            code = binance_code(exc)
            if code in NON_RETRYABLE:
                raise
            if code == -1021 and exchange is not None:
                try:
                    await exchange.load_time_difference()
                except Exception:  # pragma: no cover - best effort
                    pass
            wait = 2.0 ** attempt
            log.warning("%s exchange error code=%s attempt %d: %s", label, code, attempt + 1, str(exc)[:160])
        except (NetworkError, asyncio.TimeoutError, OSError) as exc:
            last = exc
            wait = 2.0 ** attempt
            log.warning("%s network error attempt %d: %s", label, attempt + 1, str(exc)[:160])
        if attempt < attempts - 1:
            await asyncio.sleep(wait)
    assert last is not None
    raise last
