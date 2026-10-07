"""Encrypted store for the exchange / broker API keys the operator types into the PRIVATE dashboard
(System -> Providers & Live).

    where     one file on the data volume (DATA_DIR/provider_keys.vault) -- never the SQLite database
    how       Fernet (AES-128-CBC + HMAC-SHA256) under a key derived with PBKDF2-SHA256 (390k rounds, random salt)
              from the server's DASHBOARD_PASSWORD, which already lives only in the server environment
    exposure  values never leave the server: status() reports presence and when each was set, nothing else; nothing
              here logs a value. The file alone is useless without the password.

If DASHBOARD_PASSWORD changes, the old file can no longer be opened: the vault reports LOCKED and the operator
re-enters the keys (the next save starts a fresh file). With no DASHBOARD_PASSWORD (local runs) the vault is off and
keys can only come from environment variables.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

VAULT_FILE = "provider_keys.vault"
_ROUNDS = 390_000


class VaultError(RuntimeError):
    pass


class KeyVault:
    def __init__(self, path: str | Path | None, password: str | None):
        self.path = Path(path) if path else None
        self._password = (password or "").encode()
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None
        self.locked = False

    @property
    def enabled(self) -> bool:
        return self.path is not None and bool(self._password)

    def _fernet(self, salt: bytes) -> Any:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ROUNDS)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(self._password)))

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        data: dict[str, dict[str, Any]] = {}
        if self.enabled and self.path.exists():
            try:
                box = json.loads(self.path.read_text(encoding="utf-8"))
                salt = base64.b64decode(box["salt"])
                data = json.loads(self._fernet(salt).decrypt(box["data"].encode()).decode())
                self.locked = False
            except Exception:                          # wrong password or a damaged file: never guess, never crash
                self.locked = True
                data = {}
        self._cache = data
        return data

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        if not self.enabled:
            raise VaultError("the key vault is off: DASHBOARD_PASSWORD is not set on the server")
        salt = os.urandom(16)
        token = self._fernet(salt).encrypt(json.dumps(data).encode()).decode()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "salt": base64.b64encode(salt).decode(), "data": token}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)
        self._cache = data
        self.locked = False

    def get(self, provider_id: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._load().get(provider_id)
        ok = row and row.get("key") and (row.get("secret") or row.get("optional_secret"))
        return (row["key"], row.get("secret") or "") if ok else None

    def set(self, provider_id: str, key: str, secret: str, optional_secret: bool = False) -> None:
        """optional_secret: a provider whose second field may be empty (the Telegram bot's chat id)."""
        key, secret = (key or "").strip(), (secret or "").strip()
        if not key or (not secret and not optional_secret):
            raise VaultError("both the API key and the secret are required")
        with self._lock:
            data = {} if self.locked else dict(self._load())
            data[provider_id] = {"key": key, "secret": secret, "ts": int(time.time() * 1000)}
            if not secret:
                data[provider_id]["optional_secret"] = True
            self._save(data)

    def delete(self, provider_id: str) -> bool:
        with self._lock:
            data = dict(self._load())
            if provider_id not in data:
                return False
            data.pop(provider_id)
            self._save(data)
            return True

    def status(self) -> dict[str, Any]:
        """Presence only: which providers have keys saved here and when. Never a value."""
        with self._lock:
            data = self._load()
        return {"enabled": self.enabled, "locked": self.locked,
                "saved": {pid: {"set_ts": row.get("ts")} for pid, row in data.items()}}

    def values(self) -> list[str]:
        """Every stored value, for log redaction only."""
        with self._lock:
            return [v for row in self._load().values() for v in (row.get("key"), row.get("secret")) if v]
