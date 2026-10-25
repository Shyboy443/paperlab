"""MACD desk live executor + linked accounts, against a fake Binance USD-M account (no network)."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.types import ExchangePosition, MarketRules, OrderResult
from app.live.accounts import AccountError, LinkedAccounts
from app.macd import live as L
from app.macd.service import MacdService
from tests.test_donchian import FakeMarket


class FakeVault:
    enabled, locked = True, False

    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, key, secret, optional_secret=False):
        self.d[k] = (key, secret)

    def delete(self, k):
        return self.d.pop(k, None) is not None


class FakeEx:
    """One account: USDT balance, one-way positions, orders log."""

    def __init__(self, mark=50.0, balance=1000.0):
        self.mark, self.balance = mark, balance
        self.pos = {}                      # symbol -> qty
        self.entry = {}
        self.orders, self.stops = [], []
        self.reject_stop = self.fail_close = False
        self.open = []

    async def fetch_balance(self):
        return {"wallet": self.balance, "available": self.balance, "upnl": 0.0}

    async def fetch_positions(self, symbols, all_symbols=False):
        syms = list(self.pos) if all_symbols else symbols
        return {s: ExchangePosition(s, self.pos.get(s, 0.0), self.entry.get(s, 0.0), None, 0.0, 0.0, 1, self.mark) for s in syms}

    async def open_orders(self, symbol):
        return list(self.open)

    async def ensure_account_mode(self, symbols, leverage):
        self.leverage = leverage
        return {}

    async def load_rules(self, symbols):
        return {s: MarketRules(s, 0.001, 0.001, 0.001, 5.0) for s in symbols}

    async def fetch_premium_index(self, symbol):
        return SimpleNamespace(mark=self.mark)

    async def market_order(self, intent):
        if intent.reduce_only and self.fail_close:
            return OrderResult(intent.client_id, "x", "REJECTED", None, 0.0, {})
        sign = 1 if intent.side == "BUY" else -1
        self.pos[intent.symbol] = round(self.pos.get(intent.symbol, 0.0) + sign * intent.qty, 9)
        if sign > 0:
            self.entry[intent.symbol] = self.mark
        self.orders.append((intent.side, intent.qty, intent.reduce_only))
        return OrderResult(intent.client_id, "x", "FILLED", self.mark, intent.qty, {})

    async def place_backstop(self, symbol, side, stop_price, client_id, qty=None):
        self.stops.append(stop_price)
        return OrderResult(client_id, "s", "REJECTED" if self.reject_stop else "NEW", None, 0.0, {})

    async def cancel_all(self, symbol):
        self.cancelled = True

    async def close(self):
        pass


class FakeProviders:
    def __init__(self, ex, switch=False):
        self.ex, self.switch = ex, switch

    def client(self, exchange, network, keys=None):
        return self.ex

    def keys(self, exchange, network):
        return None

    def mainnet_switch(self):
        return self.switch

    def max_amount(self):
        return 200.0

    def redact(self, t):
        return str(t)


def make(tmp_path, network="testnet", switch=False, checks=()):
    ex = FakeEx()
    prov = FakeProviders(ex, switch)
    vault = FakeVault()
    accounts = LinkedAccounts(tmp_path / "accounts.json", vault, prov)
    svc = MacdService(tmp_path / "m.db", env={"MACD_ENABLED": "false"}, market=FakeMarket([]), clock=lambda: 1_800_000_000)
    saved = []
    live = L.MacdLive(svc, accounts, prov, lambda: list(checks), lambda *a: saved.append(a), clock=lambda: 1_800_000_000)
    live._bars = lambda: [{"t": 1}]
    live._sig = (1, None)
    live._signal = lambda bars: live._sig                             # instance-level: no leak into other tests
    acct = asyncio.run(accounts.add("ZEC sub", "binance", network, "KEY-123", "SECRET-xyz"))
    return SimpleNamespace(ex=ex, prov=prov, vault=vault, accounts=accounts, live=live, acct=acct["id"], saved=saved)


def run(c):
    return asyncio.run(c)


def arm(e, amount=100, max_loss=30, phrase=L.CONFIRM):
    return run(e.live.arm(e.acct, amount, max_loss, phrase))


def signal(e, t, kind):
    e.live._sig = (t, kind)
    run(e.live.tick())


class TestAccounts:
    def test_link_stores_keys_in_the_vault_only(self, tmp_path):
        e = make(tmp_path)
        pub = e.accounts.public()
        assert pub[0]["label"] == "ZEC sub" and "KEY-123" not in str(pub) and "SECRET" not in str(pub)
        assert e.vault.get("acct:" + e.acct) == ("KEY-123", "SECRET-xyz")
        assert "SECRET" not in (tmp_path / "accounts.json").read_text()
        with pytest.raises(AccountError):
            run(e.accounts.add("other", "binance", "testnet", "KEY-123", "s"))          # same key twice

    def test_one_strategy_per_account(self, tmp_path):
        e = make(tmp_path)
        e.accounts.claim(e.acct, "bot", "V6.2 ETH")
        with pytest.raises(AccountError):
            e.accounts.claim(e.acct, "desk", "MACD")
        with pytest.raises(AccountError):
            e.accounts.remove(e.acct)
        e.accounts.release(e.acct, "bot", "V6.2 ETH")
        e.accounts.remove(e.acct)
        assert e.accounts.public() == [] and e.vault.get("acct:" + e.acct) is None


class TestSwitchOn:
    def test_refusals(self, tmp_path):
        e = make(tmp_path)
        for kw, msg in ((dict(phrase="go live"), "type"), (dict(amount=500), "at most 200"), (dict(max_loss=150), "loss limit")):
            with pytest.raises(L.LiveError, match=msg):
                arm(e, **kw)
        e.ex.pos["BTCUSDT"] = 0.01                                        # not a dedicated, flat account
        with pytest.raises(L.LiveError, match="not flat"):
            arm(e)
        e.ex.pos.clear()
        e.accounts.claim(e.acct, "agent", "Trading Desk")
        with pytest.raises(AccountError):
            arm(e)

    def test_mainnet_needs_switch_and_a_testnet_round_trip(self, tmp_path):
        e = make(tmp_path, network="mainnet")
        with pytest.raises(L.LiveError, match="LIVE_MIRROR_MAINNET_ENABLED"):
            arm(e)
        e.prov.switch = True
        with pytest.raises(L.LiveError, match="testnet round trip"):
            arm(e)
        e.live.checks = lambda: [{"ok": True, "network": "testnet", "ts": 1}]
        assert arm(e)["status"] == "ARMED"

    def test_arm_claims_the_account_at_1x_and_waits_for_a_new_cross(self, tmp_path):
        e = make(tmp_path)
        e.live._sig = (5, "buy")                                         # a cross that closed BEFORE switching on
        v = arm(e)
        assert v["status"] == "ARMED" and e.ex.leverage == 1 and e.accounts.get(e.acct)["owner"]["ref"] == L.REF
        run(e.live.tick())
        assert e.ex.orders == []                                         # never jumps into a running trade


class TestTrading:
    def test_buy_with_disaster_stop_then_sell_on_the_bearish_cross(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        signal(e, 2, "buy")
        p = e.live.state()["position"]
        assert e.ex.orders[0][0] == "BUY" and abs(p["qty"] * 50 - 100 * (1 - 0.003)) < 0.06
        assert abs(e.ex.stops[0] - 50 * (1 - L.DISASTER)) < 1e-9 and p["stop_client_id"]
        e.ex.mark = 60.0
        signal(e, 3, "sell")
        st = e.live.state()
        assert e.ex.orders[-1] == ("SELL", p["qty"], True) and st["position"] is None and st["realized"] > 0
        assert st["status"] == "ARMED" and e.ex.pos["ZECUSDT"] == 0

    def test_switch_off_closes_and_releases(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        signal(e, 2, "buy")
        v = run(e.live.disarm("switched off by the operator"))
        assert v["status"] == "OFF" and e.ex.pos["ZECUSDT"] == 0 and e.accounts.get(e.acct)["owner"] is None

    def test_a_rejected_stop_closes_the_position(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        e.ex.reject_stop = True
        signal(e, 2, "buy")
        st = e.live.state()
        assert st["status"] == "OFF" and st["position"] is None and e.ex.pos["ZECUSDT"] == 0

    def test_foreign_trade_halts_without_trading(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        signal(e, 2, "buy")
        e.ex.pos["ZECUSDT"] += 1.0                                       # someone else bought on this account
        n = len(e.ex.orders)
        signal(e, 3, "sell")
        signal(e, 4, "buy")
        assert e.live.state()["status"] == "HALTED" and len(e.ex.orders) == n

    def test_position_gone_switches_off(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        signal(e, 2, "buy")
        e.ex.pos["ZECUSDT"] = 0.0                                        # the disaster stop fired
        run(e.live.tick())
        st = e.live.state()
        assert st["status"] == "OFF" and st["position"] is None and st["trades"][-1]["reason"].startswith("position gone")

    def test_loss_limit_flattens_and_switches_off(self, tmp_path):
        e = make(tmp_path)
        arm(e, amount=100, max_loss=10)
        signal(e, 2, "buy")
        e.ex.mark = 40.0                                                 # -20 %
        run(e.live.tick())
        st = e.live.state()
        assert st["status"] == "OFF" and e.ex.pos["ZECUSDT"] == 0 and st["realized"] < -10

    def test_failed_close_keeps_a_stop_and_retries(self, tmp_path):
        e = make(tmp_path)
        arm(e)
        signal(e, 2, "buy")
        e.ex.fail_close = True
        with pytest.raises(L.LiveError):
            run(e.live.disarm("switched off by the operator"))
        st = e.live.state()
        assert st["status"] == "CLOSING" and st["position"] and len(e.ex.stops) == 2   # disaster stop re-placed
        e.ex.fail_close = False
        run(e.live.tick())
        assert e.live.state()["status"] == "OFF" and e.ex.pos["ZECUSDT"] == 0


class TestRoundTrip:
    def test_testnet_round_trip_is_recorded(self, tmp_path):
        e = make(tmp_path)
        out = run(e.live.verify(e.acct))
        assert out["ok"] and e.saved and e.saved[0][:2] == ("binance", "testnet") and e.ex.pos["ZECUSDT"] == 0


def test_routes_are_private_and_stop_needs_sure(tmp_path):
    import base64

    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import settings_factory
    app = create_app(settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="pw-123456"))
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:pw-123456").decode(), "X-PaperLab": "1"}
    with TestClient(app) as c:
        assert c.get("/api/accounts").status_code == 401
        assert c.post("/api/macd/live/stop", json={"sure": True}, headers={"X-PaperLab": "1"}).status_code == 401
        assert c.post("/api/macd/live/stop", json={"sure": True}, headers={"Authorization": auth["Authorization"]}).status_code == 403
        assert c.post("/api/macd/live/stop", json={}, headers=auth).status_code == 400          # "are you sure?" is required
        r = c.get("/api/macd/live", headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "OFF" and r.json()["confirm_phrase"] == L.CONFIRM
        assert c.get("/api/public/macd").status_code == 200 and "account" not in c.get("/api/public/macd").text


def test_the_mirrors_shared_key_can_be_linked_but_never_by_two_paths(tmp_path):
    e = make(tmp_path)
    e.prov.keys = lambda ex, net: ("SHARED-KEY", "SHARED-SECRET")      # the key the live mirror uses (Providers & Live)
    acct = run(e.accounts.add("Main sub", "binance", "testnet", "SHARED-KEY", "SHARED-SECRET"))["id"]
    assert next(a for a in e.accounts.public() if a["id"] == acct)["shared_with_mirror"] is True
    e.accounts.mirror_busy = lambda ex, net: "v6 V6.2-XRP"                # a mirrored bot is live on that key
    with pytest.raises(AccountError, match="stop that bot's live copy first"):
        e.accounts.claim(acct, "desk", "MACD")
    e.accounts.mirror_busy = lambda ex, net: None                        # stopped: the desk may take it
    e.accounts.claim(acct, "desk", "MACD")
    assert e.accounts.shared_owner("binance", "testnet") == "MACD"     # ... and now the mirror is refused (account_guard)
    e.accounts.release(acct, "desk", "MACD")
    assert e.accounts.shared_owner("binance", "testnet") is None


def test_the_providers_and_live_key_links_without_retyping(tmp_path):
    e = make(tmp_path)
    e.prov.keys = lambda ex, net: ("SAVED-KEY", "SAVED-SECRET") if (ex, net) == ("binance", "mainnet") else None
    assert e.accounts.shared_keys() == [{"exchange": "binance", "network": "mainnet", "linked": False}]
    row = run(e.accounts.import_shared("binance", "mainnet"))
    assert row["network"] == "mainnet" and "SAVED" not in str(row)                     # nothing secret comes back
    assert e.vault.get("acct:" + row["id"]) == ("SAVED-KEY", "SAVED-SECRET")
    assert e.accounts.shared_keys()[0]["linked"] is True
    with pytest.raises(AccountError, match="already linked"):
        run(e.accounts.import_shared("binance", "mainnet"))
    with pytest.raises(AccountError, match="no binance testnet key"):
        run(e.accounts.import_shared("binance", "testnet"))


def test_balances_are_read_only_cached_and_errors_reported(tmp_path):
    e = make(tmp_path)
    calls = []
    orig = e.ex.fetch_balance

    async def counted():
        calls.append(1)
        return await orig()
    e.ex.fetch_balance = counted
    b = run(e.accounts.balances())
    assert b[e.acct]["ok"] and b[e.acct]["available"] == 1000.0 and b[e.acct]["wallet"] == 1000.0
    run(e.accounts.balances())
    assert len(calls) == 1                                           # cached

    async def boom():
        raise RuntimeError("invalid key SECRET-xyz")
    e.ex.fetch_balance = boom
    e.prov.redact = lambda t: str(t).replace("SECRET-xyz", "***")
    b = run(e.accounts.balances(max_age_s=0))
    assert b[e.acct]["ok"] is False and "SECRET" not in b[e.acct]["error"] and e.ex.orders == []


def test_switch_on_never_claims_the_account_when_candles_are_unavailable(tmp_path):
    e = make(tmp_path)

    def down():
        raise RuntimeError("market data down")
    e.live._bars = down
    with pytest.raises(L.LiveError, match="unavailable right now"):
        arm(e)
    assert e.accounts.get(e.acct)["owner"] is None and e.live.state().get("status", "OFF") == "OFF"
    e.live._bars = lambda: []                                         # no completed candle known: refused too
    e.live._signal = lambda bars: (None, None)
    with pytest.raises(L.LiveError, match="no completed"):
        arm(e)
    assert e.accounts.get(e.acct)["owner"] is None


def test_a_fill_without_an_average_price_takes_the_positions_entry(tmp_path):
    """2026-10-14 on mainnet: Binance answered FILLED with no average price; the desk used 0, its stop (0) was refused
    and the position was closed at once, booking a fake +18.98. The entry now comes from the position (or the mark)."""
    from app.core.types import OrderResult
    e = make(tmp_path)
    arm(e)
    orig = e.ex.market_order

    async def no_avg(intent):
        r = await orig(intent)
        return OrderResult(r.client_id, r.exchange_id, r.status, None, r.executed_qty, {})
    e.ex.market_order = no_avg
    e.ex.mark = 1265.0
    e.live._sig = (2, "buy")
    run(e.live.tick())
    st = e.live.state()
    assert st["status"] == "ARMED" and st["position"] and st["position"]["entry"] == 1265.0
    assert abs(e.ex.stops[-1] - 1265.0 * (1 - L.DISASTER)) < 1e-9 and e.ex.pos["ZECUSDT"] > 0
    assert any(x["kind"] == "entry_price_filled_in" for x in st["log"])
