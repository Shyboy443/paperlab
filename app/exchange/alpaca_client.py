"""Minimal Alpaca REST client (US stocks / ETFs): the account check behind the provider card and the live mirror.

Talks only to Alpaca's paper or live trading host (and its market-data host), named in app/config.py; any other host
is refused before a socket opens. Keys travel only in the APCA headers of the request and never appear in an error,
a log line or a return value.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Callable

from app.config import ALPACA_DATA_REST, ALPACA_LIVE_REST, ALPACA_PAPER_REST, host_of


class AlpacaError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"Alpaca {status or 'network'} error: {message}")
        self.status = status


class AlpacaClient:
    def __init__(self, network: str, key: str, secret: str, opener: Callable[..., Any] | None = None):
        if network not in ("testnet", "mainnet"):
            raise ValueError("network must be testnet (Alpaca paper) or mainnet (Alpaca live)")
        self.network = network
        self.base = ALPACA_PAPER_REST if network == "testnet" else ALPACA_LIVE_REST
        self.allowed_hosts = frozenset({host_of(self.base), host_of(ALPACA_DATA_REST)})
        self._key, self._secret = key, secret
        self._open = opener or urllib.request.urlopen

    def _call(self, method: str, url: str, body: dict[str, Any] | None) -> Any:
        if host_of(url) not in self.allowed_hosts:
            raise AlpacaError(None, f"refusing host {host_of(url)!r}")
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret,
                                              "Content-Type": "application/json", "User-Agent": "paperlab"})
        try:
            with self._open(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("message") or e.reason
            except ValueError:
                msg = e.reason
            raise AlpacaError(e.code, str(msg)[:160]) from None
        except urllib.error.URLError as e:
            raise AlpacaError(None, str(e.reason)[:160]) from None

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None, data: bool = False) -> Any:
        return await asyncio.to_thread(self._call, method, (ALPACA_DATA_REST if data else self.base) + path, body)

    async def fetch_account(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/account")

    async def fetch_balance(self) -> dict[str, Any]:
        a = await self.fetch_account()
        return {"wallet": float(a.get("equity") or 0), "available": float(a.get("buying_power") or 0),
                "cash": float(a.get("cash") or 0), "currency": a.get("currency") or "USD", "status": a.get("status")}

    def stream_auth(self) -> dict[str, str]:
        """The market-data stream's auth message (sent only over the TLS WebSocket, never logged)."""
        return {"action": "auth", "key": self._key, "secret": self._secret}

    async def close(self) -> None:
        return None
