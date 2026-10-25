"""Donchian desk live switch: one chosen coin, long AND short with reversals, on the shared desk executor."""
import asyncio

import pytest

from app.donchian.live import CONFIRM, DonchianLive
from app.donchian.service import DonchianService
from app.live.accounts import AccountError, LinkedAccounts
from app.live.desk_live import DISASTER, LiveError
from tests.test_donchian import FakeMarket
from tests.test_macd_live import FakeEx, FakeProviders, FakeVault


def run(c):
    return asyncio.run(c)


def make(tmp_path):
    ex = FakeEx(mark=2.0, balance=1000.0)
    prov = FakeProviders(ex)
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    svc = DonchianService(tmp_path / "d.db", env={"DONCHIAN_ENABLED": "false"}, market=FakeMarket([]), clock=lambda: 1_800_000_000)
    live = DonchianLive(svc, accounts, prov, lambda: [], lambda *a: None, clock=lambda: 1_800_000_000)
    live._next = (1, None)
    live.decide = lambda symbol, held: live._next
    acct = run(accounts.add("XRP sub", "binance", "testnet", "K-1", "S-1"))["id"]
    return ex, accounts, live, acct


def step(live, t, action):
    live._next = (t, action)
    run(live.tick())


def test_coin_must_be_one_of_the_desk_universe(tmp_path):
    ex, accounts, live, acct = make(tmp_path)
    with pytest.raises(LiveError, match="choose one of"):
        run(live.arm(acct, 100, 30, CONFIRM, "PEPEUSDT"))
    with pytest.raises(LiveError, match="choose one of"):
        run(live.arm(acct, 100, 30, CONFIRM))                         # ten coins: no default
    v = run(live.arm(acct, 100, 30, CONFIRM, "XRPUSDT"))
    assert v["status"] == "ARMED" and v["symbol"] == "XRPUSDT" and v["rules"]["shorts"] is True


def test_short_entry_has_its_disaster_stop_above(tmp_path):
    ex, accounts, live, acct = make(tmp_path)
    run(live.arm(acct, 100, 30, CONFIRM, "XRPUSDT"))
    step(live, 2, ("enter", -1))
    p = live.state()["position"]
    assert ex.orders[0][0] == "SELL" and not ex.orders[0][2] and p["side"] == "short" and ex.pos["XRPUSDT"] < 0
    assert abs(ex.stops[0] - 2.0 * (1 + DISASTER)) < 1e-12


def test_reversal_closes_reduce_only_then_opens_the_other_side(tmp_path):
    ex, accounts, live, acct = make(tmp_path)
    run(live.arm(acct, 100, 30, CONFIRM, "XRPUSDT"))
    step(live, 2, ("enter", 1))
    qty = live.state()["position"]["qty"]
    ex.mark = 2.4
    step(live, 3, ("reverse", -1))
    st = live.state()
    assert ex.orders[1] == ("SELL", qty, True)                       # the long closed reduce-only first
    assert ex.orders[2][0] == "SELL" and not ex.orders[2][2]          # then a fresh short
    assert st["position"]["side"] == "short" and st["trades"][-1]["pnl"] > 0 and ex.pos["XRPUSDT"] < 0
    ex.mark = 2.2
    step(live, 4, ("exit", 0))
    st = live.state()
    assert ex.orders[-1][0] == "BUY" and ex.orders[-1][2] and st["position"] is None and ex.pos["XRPUSDT"] == 0
    assert st["trades"][-1]["side"] == "short" and st["trades"][-1]["pnl"] > 0


def test_short_loss_limit_and_switch_off(tmp_path):
    ex, accounts, live, acct = make(tmp_path)
    run(live.arm(acct, 100, 10, CONFIRM, "XRPUSDT"))
    step(live, 2, ("enter", -1))
    ex.mark = 2.4                                                     # +20 % against the short
    run(live.tick())
    st = live.state()
    assert st["status"] == "OFF" and ex.pos["XRPUSDT"] == 0 and st["realized"] < -10
    assert accounts.get(acct)["owner"] is None


def test_one_account_cannot_serve_two_desks(tmp_path):
    from app.macd.live import CONFIRM as MACD_CONFIRM, MacdLive
    from app.macd.service import MacdService
    ex, accounts, live, acct = make(tmp_path)
    run(live.arm(acct, 100, 30, CONFIRM, "XRPUSDT"))
    msvc = MacdService(tmp_path / "m.db", env={"MACD_ENABLED": "false"}, market=FakeMarket([]), clock=lambda: 1_800_000_000)
    macd = MacdLive(msvc, accounts, live.providers, lambda: [], lambda *a: None, clock=lambda: 1_800_000_000)
    macd.decide = lambda symbol, held: (1, None)
    with pytest.raises(AccountError, match="one strategy per account"):
        run(macd.arm(acct, 50, 10, MACD_CONFIRM))


def test_routes_for_both_desks_are_private(tmp_path):
    import base64

    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import settings_factory
    app = create_app(settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="pw-123456"))
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:pw-123456").decode(), "X-PaperLab": "1"}
    with TestClient(app) as c:
        for desk in ("macd", "donchian", "macd-stx", "macd-dash", "macd-xlm"):
            assert c.get(f"/api/{desk}/live").status_code == 401
            r = c.get(f"/api/{desk}/live", headers=auth)
            assert r.status_code == 200 and r.json()["status"] == "OFF"
        assert c.get("/api/donchian/live", headers=auth).json()["confirm_phrase"] == CONFIRM
        assert c.get("/api/nope/live", headers=auth).status_code == 404
        assert c.post("/api/donchian/live/stop", json={}, headers=auth).status_code == 400
