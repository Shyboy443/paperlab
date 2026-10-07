"""Providers for the live mirror: Bybit and Binance (USDT perpetuals) on TESTNET or MAINNET, and Alpaca (US stocks /
ETFs) on PAPER (network "testnet") or LIVE (network "mainnet").

Keys are never returned, logged or stored in the database. They come from, in this order:

    1. the private dashboard (System -> Providers & Live), saved ENCRYPTED on the data volume (app/live/key_vault.py);
    2. the server environment (Railway variables):
         Bybit testnet     BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET
         Bybit mainnet     BYBIT_API_KEY / BYBIT_API_SECRET
         Binance testnet   BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET    (USD-M futures testnet)
         Binance mainnet   BINANCE_API_KEY / BINANCE_API_SECRET
         Alpaca paper      ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET
         Alpaca live       ALPACA_API_KEY / ALPACA_API_SECRET
         Reddit (data)     REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET   (the V10 scout's read-only app key)

Keys typed into the dashboard are checked against the provider (a read-only account / balance call) before they are
saved; rejected keys are not kept. (The hosts themselves are defined only in app/config.py.)

MAINNET stays locked unless LIVE_MIRROR_MAINNET_ENABLED=true is set by the operator AND that provider has passed a
recorded testnet (paper) round trip (open, exchange-side stop, close, flat).
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Mapping

PROVIDERS: dict[tuple[str, str], dict[str, Any]] = {
    ("bybit", "testnet"): {"mode": "BYBIT_TESTNET", "exchange": "bybit", "prefix": "BYBIT", "market": "crypto",
                           "env": ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"), "label": "Bybit testnet"},
    ("bybit", "mainnet"): {"mode": "LIVE_OVERRIDE_I_UNDERSTAND", "exchange": "bybit", "prefix": "BYBIT", "market": "crypto",
                           "env": ("BYBIT_API_KEY", "BYBIT_API_SECRET"), "label": "Bybit (real money)"},
    ("binance", "testnet"): {"mode": "FUTURES_TESTNET", "exchange": "binance", "prefix": "BINANCE", "market": "crypto",
                             "env": ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"), "label": "Binance futures testnet"},
    ("binance", "mainnet"): {"mode": "LIVE_OVERRIDE_I_UNDERSTAND", "exchange": "binance", "prefix": "BINANCE", "market": "crypto",
                             "env": ("BINANCE_API_KEY", "BINANCE_API_SECRET"), "label": "Binance USD-M (real money)"},
    ("alpaca", "testnet"): {"mode": None, "exchange": "alpaca", "prefix": "ALPACA_PAPER", "market": "stocks",
                            "env": ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET"), "label": "Alpaca paper"},
    ("alpaca", "mainnet"): {"mode": None, "exchange": "alpaca", "prefix": "ALPACA", "market": "stocks",
                            "env": ("ALPACA_API_KEY", "ALPACA_API_SECRET"), "label": "Alpaca (real money)"},
    # a DATA source for the V10 scout (read-only; never a trading venue)
    ("reddit", "data"): {"mode": None, "exchange": "reddit", "prefix": "REDDIT", "market": "data",
                         "env": ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"), "label": "Reddit API (read-only data)"},
    # NOTIFICATIONS (app/live/telegram.py): the bot token, and optionally the chat id (else /start links the chat)
    ("telegram", "data"): {"mode": None, "exchange": "telegram", "prefix": "TELEGRAM", "market": "data",
                           "env": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), "label": "Telegram bot (trade updates)",
                           "optional_secret": True},
}
MAINNET_SWITCH = "LIVE_MIRROR_MAINNET_ENABLED"
MAX_AMOUNT_ENV = "LIVE_MIRROR_MAX_AMOUNT_USDT"       # hard ceiling per mirror (default 200 USDT)


def provider_id(exchange: str, network: str) -> str:
    return f"{exchange}:{network}"


class Providers:
    def __init__(self, env: Mapping[str, str] | None = None, client_factory: Any = None, vault: Any = None):
        self.env = os.environ if env is None else env
        self._factory = client_factory
        self.vault = vault
        self._balances: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    def source(self, exchange: str, network: str) -> str | None:
        """Where the keys in use come from: 'dashboard' (encrypted vault), 'railway' (environment) or None."""
        p = PROVIDERS.get((exchange, network))
        if p is None:
            return None
        if self.vault is not None and self.vault.get(provider_id(exchange, network)):
            return "dashboard"
        k, s = (self.env.get(p["env"][0]) or "").strip(), (self.env.get(p["env"][1]) or "").strip()
        return "railway" if k and (s or p.get("optional_secret")) else None

    def keys(self, exchange: str, network: str) -> tuple[str, str] | None:
        p = PROVIDERS.get((exchange, network))
        if p is None:
            return None
        if self.vault is not None:
            saved = self.vault.get(provider_id(exchange, network))
            if saved:
                return saved
        k, s = (self.env.get(p["env"][0]) or "").strip(), (self.env.get(p["env"][1]) or "").strip()
        return (k, s) if k and (s or p.get("optional_secret")) else None

    def mainnet_switch(self) -> bool:
        return str(self.env.get(MAINNET_SWITCH, "")).strip().lower() in ("1", "true", "yes", "on")

    def max_amount(self) -> float:
        try:
            return max(0.0, float(self.env.get(MAX_AMOUNT_ENV) or 200.0))
        except ValueError:
            return 200.0

    def settings(self, exchange: str, network: str, keys: tuple[str, str] | None = None) -> Any:
        from app.config import load_settings
        p = PROVIDERS[(exchange, network)]
        if p["mode"] is None:
            raise ValueError(f"{p['label']} does not use exchange settings")
        keys = keys or self.keys(exchange, network)
        if keys is None:
            raise ValueError(f"{p['label']}: keys are not configured ({p['env'][0]} / {p['env'][1]})")
        env = {"MODE": p["mode"], "EXCHANGE": p["exchange"], "DASHBOARD_PASSWORD": "live-mirror", "DRY_RUN": "false",
               "SYMBOLS": "ETHUSDT", f"{p['prefix']}_API_KEY": keys[0], f"{p['prefix']}_API_SECRET": keys[1]}
        return load_settings(env, env_path=None)

    def client(self, exchange: str, network: str, keys: tuple[str, str] | None = None) -> Any:
        if self._factory is not None:
            return self._factory(exchange, network)
        if exchange == "telegram":
            keys = keys or self.keys(exchange, network)
            if keys is None:
                raise ValueError("Telegram: the bot token is not configured")
            return TelegramCheck(keys[0])
        if exchange == "reddit":
            from app.scout.sources import RedditClient
            keys = keys or self.keys(exchange, network)
            if keys is None:
                raise ValueError("Reddit: keys are not configured")
            return RedditClient(*keys)
        if exchange == "alpaca":
            from app.exchange.alpaca_client import AlpacaClient
            keys = keys or self.keys(exchange, network)
            if keys is None:
                raise ValueError(f"{PROVIDERS[(exchange, network)]['label']}: keys are not configured")
            return AlpacaClient(network, *keys)
        from app.exchange.client import make_client
        return make_client(self.settings(exchange, network, keys))

    def redact(self, text: Any) -> str:
        s = str(text)
        values = [v for (ex, net) in PROVIDERS for v in (self.keys(ex, net) or ())]
        if self.vault is not None:
            values += self.vault.values()
        for v in values:
            if v:
                s = s.replace(v, "***")
        return s[:200]

    async def _balance_with(self, exchange: str, network: str, keys: tuple[str, str] | None) -> dict[str, Any]:
        client = None
        try:
            client = self.client(exchange, network, keys)
            b = await client.fetch_balance()
            out = {"ok": True, "wallet": round(float(b.get("wallet") or 0), 4), "available": round(float(b.get("available") or 0), 4),
                   "currency": b.get("currency") if b.get("currency") is not None else ("USD" if exchange == "alpaca" else "USDT")}
            if b.get("access"):
                out["access"] = b["access"]
            return out
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            for v in keys or ():
                msg = msg.replace(v, "***")
            return {"ok": False, "error": self.redact(msg)}
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass

    async def balance(self, exchange: str, network: str, max_age_s: float = 60.0) -> dict[str, Any]:
        hit = self._balances.get((exchange, network))
        if hit and time.time() - hit[0] < max_age_s:
            return hit[1]
        out = await self._balance_with(exchange, network, None)
        self._balances[(exchange, network)] = (time.time(), out)
        return out

    async def save_keys(self, exchange: str, network: str, key: str, secret: str) -> dict[str, Any]:
        """Check the keys against the provider (read-only account call); keep them, encrypted, only if accepted."""
        if (exchange, network) not in PROVIDERS:
            return {"ok": False, "error": "unknown provider"}
        if self.vault is None or not self.vault.enabled:
            return {"ok": False, "error": "saving keys needs DASHBOARD_PASSWORD on the server (the vault is off)"}
        key, secret = (key or "").strip(), (secret or "").strip()
        if not key or (not secret and not PROVIDERS[(exchange, network)].get("optional_secret")):
            return {"ok": False, "error": "both the API key and the secret are required"}
        if exchange == "telegram" and secret and not re.fullmatch(r"-?\d{1,20}", secret):
            return {"ok": False, "error": "the chat id must be a number -- leave it empty and send /start to the bot"}
        bal = await self._balance_with(exchange, network, (key, secret))
        if not bal.get("ok"):
            return {"ok": False, "error": "the provider rejected these keys: " + str(bal.get("error") or "unknown error")}
        self.vault.set(provider_id(exchange, network), key, secret,
                       optional_secret=bool(PROVIDERS[(exchange, network)].get("optional_secret")))
        self._balances[(exchange, network)] = (time.time(), bal)
        return {"ok": True, "balance": bal}

    def forget_keys(self, exchange: str, network: str) -> bool:
        self._balances.pop((exchange, network), None)
        return bool(self.vault is not None and self.vault.enabled and self.vault.delete(provider_id(exchange, network)))

    async def status(self, storage: Any, with_balance: bool = True) -> list[dict[str, Any]]:
        checks = storage.provider_checks(limit=50) if storage is not None else []
        vault = self.vault.status() if self.vault is not None else {"enabled": False, "locked": False, "saved": {}}
        out = []
        for (ex, net), p in PROVIDERS.items():
            configured = self.keys(ex, net) is not None
            verified = next((c for c in checks if c["exchange"] == ex and c["network"] == "testnet" and c["ok"]), None)
            saved = vault["saved"].get(provider_id(ex, net)) or {}
            row = {"exchange": ex, "network": net, "label": p["label"], "market": p["market"], "keys_configured": configured,
                   "key_source": self.source(ex, net), "key_saved_ts": saved.get("set_ts"), "key_env": list(p["env"]),
                   "can_save_keys": bool(vault["enabled"]), "vault_locked": bool(vault["locked"]),
                   "testnet_verified_ts": verified["ts"] if verified else None}
            if net == "mainnet":
                row["mainnet_switch"] = self.mainnet_switch()
                row["mainnet_allowed"] = bool(configured and self.mainnet_switch() and verified)
            if configured and with_balance:
                row["balance"] = await self.balance(ex, net)
            out.append(row)
        return out


class TelegramCheck:
    """Verifies a Telegram bot token (getMe) for the key form; the token never leaves the request or reaches a log."""

    def __init__(self, token: str):
        self.token = token

    async def fetch_balance(self) -> dict[str, Any]:
        import asyncio
        import json as _json
        import urllib.error
        import urllib.request

        def get() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(f"https://api.telegram.org/bot{self.token}/getMe", timeout=10) as r:
                    return _json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return {"ok": False, "description": f"HTTP {e.code}: the token was rejected"}
            except Exception as e:                       # a URL error would carry the token: report the type only
                return {"ok": False, "description": f"{type(e).__name__}: Telegram unreachable"}
        res = await asyncio.to_thread(get)
        if not res.get("ok"):
            raise ValueError(str(res.get("description") or "the token was rejected"))
        user = (res.get("result") or {}).get("username") or "bot"
        return {"wallet": 0.0, "available": 0.0, "currency": "", "access": f"@{user} OK"}

    async def close(self) -> None:
        return None
