"""Linked exchange accounts: one live strategy per account, so live strategies can never collide.

An operator links an account (typically a Binance sub-account's API key) under a label. The keys go into the
encrypted KeyVault under "acct:<id>"; the metadata kept here holds no secret (label, exchange, network, a short
fingerprint of the API key, who owns the account). Rules:
- An account is OWNED by at most one strategy at a time (a bot, a desk, or a trading-desk agent). claim() refuses
  any other owner; release() frees it only for that owner. Owning means: armed, or still holding a position.
- The same API key cannot be linked twice. The live mirror's shared key (Providers & Live) MAY be linked, but then the
  two paths exclude each other: a desk cannot claim that account while a mirrored bot is live on the shared key
  (mirror_busy), and the mirror refuses to arm while a desk owns it (shared_owner, wired into MirrorService.account_guard).
  Two DIFFERENT keys of the SAME Binance account cannot be told apart from here -- link each sub-account once.
- Keys are checked with a read-only balance call before they are stored; an account cannot be removed while owned.
Binance USD-M futures only for now (the only Binance live venue this app has).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

NETWORKS = ("testnet", "mainnet")
EXCHANGES = ("binance",)


class AccountError(ValueError):
    pass


def fingerprint(api_key: str) -> str:
    return hashlib.sha256((api_key or "").strip().encode()).hexdigest()[:10]


class LinkedAccounts:
    def __init__(self, path: Path, vault: Any, providers: Any, clock=time.time):
        self.path = Path(path)
        self.vault = vault
        self.providers = providers
        self.clock = clock
        self._lock = threading.RLock()
        self.mirror_busy = None                  # main sets it: (exchange, network) -> text when a mirrored bot is live there
        self._bal: dict[str, tuple[float, dict[str, Any]]] = {}          # account id -> (fetched at, balance)

    # -- storage (metadata only; keys live in the vault) -------------------------------------------------------------------
    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def vault_id(account_id: str) -> str:
        return "acct:" + account_id

    def get(self, account_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._load().get(account_id)
        if row is None:
            raise AccountError("unknown account")
        return row

    def keys(self, account_id: str) -> tuple[str, str]:
        self.get(account_id)
        keys = self.vault.get(self.vault_id(account_id)) if self.vault is not None else None
        if not keys:
            raise AccountError("this account's keys are missing from the vault (re-link it)")
        return keys

    def client(self, account_id: str) -> Any:
        row = self.get(account_id)
        return self.providers.client(row["exchange"], row["network"], self.keys(account_id))

    # -- link / unlink -------------------------------------------------------------------------------------------------------
    async def add(self, label: str, exchange: str, network: str, api_key: str, secret: str) -> dict[str, Any]:
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:40]
        api_key, secret = (api_key or "").strip(), (secret or "").strip()
        if not label:
            raise AccountError("give the account a label (e.g. 'ZEC sub-account')")
        if exchange not in EXCHANGES or network not in NETWORKS:
            raise AccountError("only Binance USD-M futures (testnet or mainnet) can be linked for now")
        if not api_key or not secret:
            raise AccountError("both the API key and the secret are required")
        if self.vault is None or not getattr(self.vault, "enabled", False) or getattr(self.vault, "locked", False):
            raise AccountError("the key vault is unavailable (DASHBOARD_PASSWORD not set or changed)")
        fp = fingerprint(api_key)
        with self._lock:
            data = self._load()
            if any(r["key_fp"] == fp for r in data.values()):
                raise AccountError("this API key is already linked")
            if any(r["label"].lower() == label.lower() for r in data.values()):
                raise AccountError("an account with this label already exists")
        c = self.providers.client(exchange, network, (api_key, secret))           # read-only check before storing
        try:
            bal = await c.fetch_balance()
        except Exception as exc:
            raise AccountError("the exchange rejected these keys: " + self.providers.redact(f"{type(exc).__name__}: {exc}")) from None
        finally:
            try:
                await c.close()
            except Exception:
                pass
        acct_id = "a" + uuid.uuid4().hex[:8]
        self.vault.set(self.vault_id(acct_id), api_key, secret)
        row = {"id": acct_id, "label": label, "exchange": exchange, "network": network, "key_fp": fp,
               "created_ts": int(self.clock() * 1000), "owner": None}
        with self._lock:
            data = self._load()
            data[acct_id] = row
            self._save(data)
        return {**{k: row[k] for k in ("id", "label", "exchange", "network", "key_fp")},
                "balance": {k: bal.get(k) for k in ("wallet", "available") if k in bal}}

    async def import_shared(self, exchange: str, network: str, label: str = "") -> dict[str, Any]:
        """Link the key already saved in Providers & Live (vault or Railway env) without the operator re-typing it.
        The key is read and re-checked on the server; it never travels to the browser."""
        if exchange not in EXCHANGES or network not in NETWORKS:
            raise AccountError("only Binance USD-M futures (testnet or mainnet) can be linked for now")
        keys = self.providers.keys(exchange, network)
        if not keys:
            raise AccountError(f"no {exchange} {network} key is saved in Providers & Live")
        return await self.add(label or f"Providers & Live {exchange} {network}", exchange, network, keys[0], keys[1])

    def shared_keys(self) -> list[dict[str, Any]]:
        """Which Providers & Live keys exist and whether they are already linked here (presence only)."""
        with self._lock:
            fps = {r["key_fp"] for r in self._load().values()}
        out = []
        for ex in EXCHANGES:
            for net in NETWORKS:
                k = self.providers.keys(ex, net)
                if k:
                    out.append({"exchange": ex, "network": net, "linked": fingerprint(k[0]) in fps})
        return out

    async def balances(self, max_age_s: float = 30.0) -> dict[str, dict[str, Any]]:
        """USDT balance of every linked account (read-only fetch_balance, cached max_age_s). Errors are reported, redacted."""
        import asyncio

        async def one(acct_id: str) -> tuple[str, dict[str, Any]]:
            hit = self._bal.get(acct_id)
            if hit and self.clock() - hit[0] < max_age_s:
                return acct_id, hit[1]
            c = None
            try:
                c = self.client(acct_id)
                b = await asyncio.wait_for(c.fetch_balance(), timeout=15)
                out = {"ok": True, "wallet": float(b.get("wallet") or 0), "available": float(b.get("available") or 0),
                       "upnl": float(b.get("upnl") or 0), "ts": int(self.clock() * 1000)}
            except Exception as exc:
                out = {"ok": False, "error": self.providers.redact(f"{type(exc).__name__}: {exc}")[:160], "ts": int(self.clock() * 1000)}
            finally:
                if c is not None:
                    try:
                        await c.close()
                    except Exception:
                        pass
            self._bal[acct_id] = (self.clock(), out)
            return acct_id, out

        with self._lock:
            ids = list(self._load())
        return dict(await asyncio.gather(*(one(i) for i in ids))) if ids else {}

    def remove(self, account_id: str) -> None:
        with self._lock:
            data = self._load()
            row = data.get(account_id)
            if row is None:
                raise AccountError("unknown account")
            if row.get("owner"):
                raise AccountError(f"in use by {row['owner']['ref']}: stop it first")
            data.pop(account_id)
            self._save(data)
        if self.vault is not None:
            self.vault.delete(self.vault_id(account_id))

    # -- ownership: one strategy per account ---------------------------------------------------------------------------------
    def claim(self, account_id: str, kind: str, ref: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            row = data.get(account_id)
            if row is None:
                raise AccountError("unknown account")
            owner = row.get("owner")
            if owner and (owner["kind"], owner["ref"]) != (kind, ref):
                raise AccountError(f"'{row['label']}' is already used by {owner['ref']}: one strategy per account")
            if not owner and self._is_shared(row) and self.mirror_busy:
                busy = self.mirror_busy(row["exchange"], row["network"])
                if busy:
                    raise AccountError(f"'{row['label']}' is the live mirror's shared key and {busy} is live on it: "
                                       "stop that bot's live copy first (one strategy per account)")
            row["owner"] = owner or {"kind": kind, "ref": ref, "since": int(self.clock() * 1000)}
            self._save(data)
            return row

    def release(self, account_id: str, kind: str, ref: str) -> None:
        with self._lock:
            data = self._load()
            row = data.get(account_id)
            if row and row.get("owner") and (row["owner"]["kind"], row["owner"]["ref"]) == (kind, ref):
                row["owner"] = None
                self._save(data)

    def _is_shared(self, row: dict[str, Any]) -> bool:
        shared = self.providers.keys(row["exchange"], row["network"])
        return bool(shared) and fingerprint(shared[0]) == row["key_fp"]

    def shared_owner(self, exchange: str, network: str) -> str | None:
        """Who owns the linked account whose key is the mirror's shared key for exchange/network (None: nobody)."""
        with self._lock:
            rows = list(self._load().values())
        for r in rows:
            if r["exchange"] == exchange and r["network"] == network and r.get("owner") and self._is_shared(r):
                return r["owner"]["ref"]
        return None

    def public(self) -> list[dict[str, Any]]:
        """Presence-only view: never a key or a secret."""
        with self._lock:
            rows = list(self._load().values())
        return [{**{k: r.get(k) for k in ("id", "label", "exchange", "network", "key_fp", "created_ts", "owner")},
                 "shared_with_mirror": self._is_shared(r)} for r in sorted(rows, key=lambda r: r.get("created_ts") or 0)]

    def owned_by(self, kind: str, ref: str) -> list[str]:
        with self._lock:
            return [r["id"] for r in self._load().values() if r.get("owner") and (r["owner"]["kind"], r["owner"]["ref"]) == (kind, ref)]
