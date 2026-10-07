"""Dashboard-entered provider keys: the encrypted vault, key precedence, verification before saving, the private
routes, and the Alpaca client's host guard. No network, no real keys."""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest

from app.core.storage import Storage
from app.exchange.alpaca_client import AlpacaClient, AlpacaError
from app.live.key_vault import KeyVault, VaultError
from app.live.providers import PROVIDERS, Providers

KEY, SECRET = "PKTESTKEY-0123456789", "SECRET-abcdefghijklmnop"


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self, ok=True):
        self.ok = ok

    async def fetch_balance(self):
        if not self.ok:
            raise RuntimeError(f"401 unauthorized for key {KEY}")
        return {"wallet": 100000.0, "available": 200000.0, "currency": "USD"}

    async def close(self):
        pass


class TestVault:
    def test_round_trip_and_nothing_readable_on_disk(self, tmp_path):
        v = KeyVault(tmp_path / "k.vault", "dash-pass")
        v.set("alpaca:testnet", KEY, SECRET)
        raw = (tmp_path / "k.vault").read_text()
        assert KEY not in raw and SECRET not in raw
        again = KeyVault(tmp_path / "k.vault", "dash-pass")
        assert again.get("alpaca:testnet") == (KEY, SECRET)
        st = again.status()
        assert st["enabled"] and not st["locked"] and set(st["saved"]) == {"alpaca:testnet"}
        assert KEY not in json.dumps(st) and SECRET not in json.dumps(st)
        assert again.delete("alpaca:testnet") and again.get("alpaca:testnet") is None

    def test_a_different_password_locks_and_a_new_save_starts_fresh(self, tmp_path):
        KeyVault(tmp_path / "k.vault", "old").set("bybit:testnet", KEY, SECRET)
        v = KeyVault(tmp_path / "k.vault", "new")
        assert v.get("bybit:testnet") is None and v.status()["locked"] is True
        v.set("alpaca:testnet", "k2", "s2")
        assert KeyVault(tmp_path / "k.vault", "new").get("alpaca:testnet") == ("k2", "s2")

    def test_off_without_a_dashboard_password(self, tmp_path):
        v = KeyVault(tmp_path / "k.vault", "")
        assert not v.enabled and v.get("x") is None
        with pytest.raises(VaultError):
            v.set("alpaca:testnet", KEY, SECRET)


class TestProviders:
    def test_dashboard_keys_win_over_railway_variables(self, tmp_path):
        v = KeyVault(tmp_path / "k.vault", "p")
        pr = Providers({"ALPACA_PAPER_API_KEY": "envk", "ALPACA_PAPER_API_SECRET": "envs"}, vault=v)
        assert pr.keys("alpaca", "testnet") == ("envk", "envs") and pr.source("alpaca", "testnet") == "railway"
        v.set("alpaca:testnet", KEY, SECRET)
        assert pr.keys("alpaca", "testnet") == (KEY, SECRET) and pr.source("alpaca", "testnet") == "dashboard"
        assert pr.redact(f"boom {SECRET}") == "boom ***"

    def test_alpaca_is_a_stocks_provider(self):
        assert PROVIDERS[("alpaca", "testnet")]["market"] == "stocks" and PROVIDERS[("alpaca", "mainnet")]["market"] == "stocks"
        assert {PROVIDERS[k]["market"] for k in PROVIDERS if k[0] in ("bybit", "binance")} == {"crypto"}
        assert PROVIDERS[("reddit", "data")]["market"] == "data"

    def test_keys_are_saved_only_after_the_provider_accepts_them(self, tmp_path):
        v = KeyVault(tmp_path / "k.vault", "p")
        bad = Providers({}, client_factory=lambda ex, net: Fake(ok=False), vault=v)
        out = run(bad.save_keys("alpaca", "testnet", KEY, SECRET))
        assert out["ok"] is False and KEY not in out["error"] and v.get("alpaca:testnet") is None
        good = Providers({}, client_factory=lambda ex, net: Fake(), vault=v)
        out = run(good.save_keys("alpaca", "testnet", KEY, SECRET))
        assert out["ok"] and out["balance"]["available"] == 200000.0 and v.get("alpaca:testnet") == (KEY, SECRET)
        assert good.forget_keys("alpaca", "testnet") and v.get("alpaca:testnet") is None

    def test_status_reports_presence_and_source_never_values(self, tmp_path):
        v = KeyVault(tmp_path / "k.vault", "p")
        v.set("alpaca:testnet", KEY, SECRET)
        pr = Providers({}, client_factory=lambda ex, net: Fake(), vault=v)
        st = Storage(str(tmp_path / "s.db"))
        rows = run(pr.status(st))
        alp = next(r for r in rows if (r["exchange"], r["network"]) == ("alpaca", "testnet"))
        assert alp["keys_configured"] and alp["key_source"] == "dashboard" and alp["can_save_keys"] and alp["key_saved_ts"]
        assert KEY not in repr(rows) and SECRET not in repr(rows)
        st.close()

    def test_without_a_vault_saving_is_refused(self):
        out = run(Providers({}, client_factory=lambda ex, net: Fake()).save_keys("alpaca", "testnet", KEY, SECRET))
        assert out["ok"] is False and "DASHBOARD_PASSWORD" in out["error"]


class TestRoutes:
    def test_key_routes_are_private_posts(self):
        from app.core import api_live_mirror
        routes = {(r.path, tuple(sorted(r.methods))) for r in api_live_mirror.router.routes}
        assert ("/api/mirror/providers/{exchange}/{network}/keys", ("POST",)) in routes
        assert ("/api/mirror/providers/{exchange}/{network}/keys/clear", ("POST",)) in routes

    def test_unauthenticated_requests_are_rejected(self, tmp_path):
        from fastapi.testclient import TestClient

        from app.main import create_app
        from tests.conftest import settings_factory
        app = create_app(settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="pw-123456"))
        with TestClient(app) as c:
            r = c.post("/api/mirror/providers/alpaca/testnet/keys", json={"key": KEY, "secret": SECRET},
                       headers={"X-PaperLab": "1"})
            assert r.status_code == 401


class TestAlpacaClient:
    def test_paper_uses_the_paper_host_and_parses_the_account(self):
        seen = []

        def opener(req, timeout):
            seen.append((req.full_url, req.get_header("Apca-api-key-id")))
            return io.BytesIO(json.dumps({"equity": "100000", "buying_power": "400000", "cash": "100000",
                                          "currency": "USD", "status": "ACTIVE"}).encode())
        c = AlpacaClient("testnet", KEY, SECRET, opener=opener)
        b = run(c.fetch_balance())
        assert b == {"wallet": 100000.0, "available": 400000.0, "cash": 100000.0, "currency": "USD", "status": "ACTIVE"}
        assert seen[0][0].startswith("https://paper-api.alpaca.markets/v2/account") and seen[0][1] == KEY

    def test_foreign_hosts_are_refused_and_errors_carry_no_keys(self):
        c = AlpacaClient("testnet", KEY, SECRET, opener=lambda req, timeout: pytest.fail("no socket"))
        with pytest.raises(AlpacaError):
            c._call("GET", "https://evil.example/v2/account", None)

        def denied(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message": "forbidden."}'))
        c = AlpacaClient("mainnet", KEY, SECRET, opener=denied)
        with pytest.raises(AlpacaError) as e:
            run(c.fetch_balance())
        assert "403" in str(e.value) and KEY not in str(e.value) and SECRET not in str(e.value)
