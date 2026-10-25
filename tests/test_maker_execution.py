"""Maker execution in the shared desk live executor (MACD coin bots): post-only limits at the best price, re-priced,
market for the rest; the disaster stop stays during a maker exit; a switch-off stops the waiting (fake exchange)."""
import asyncio

import pytest

from app.core.types import OrderResult
from app.live import desk_live
from app.live.accounts import LinkedAccounts
from app.macdtrio.live import MacdCoinLive, TrioMarket
from tests.test_macd_live import FakeEx, FakeProviders, FakeVault

NOW = 1_800_000_000


def run(c):
    return asyncio.run(c)


class MakerEx(FakeEx):
    """Limit orders: mode 'fill' fills at the limit at once, 'never' rests, 'half' fills half then rests,
    'cross' is refused once (it would have crossed) and then fills."""

    def __init__(self, mode="fill", **k):
        super().__init__(**k)
        self.mode, self.limits, self.calls = mode, {}, []
        self.bid, self.ask = 1.99, 2.01

    async def book_ticker(self, symbol):
        return self.bid, self.ask

    async def post_only_order(self, intent, price):
        self.calls.append(("limit", intent.side, intent.qty, intent.reduce_only))
        if self.mode == "cross" and not getattr(self, "_crossed", False):
            self._crossed = True
            return OrderResult(intent.client_id, "x", "EXPIRED", None, 0.0, {})
        fill = intent.qty if self.mode in ("fill", "cross") else intent.qty / 2 if self.mode == "half" else 0.0
        if self.mode == "half" and getattr(self, "_halved", False):
            fill = 0.0
        self._halved = True
        if fill:
            sign = 1 if intent.side == "BUY" else -1
            self.pos[intent.symbol] = round(self.pos.get(intent.symbol, 0.0) + sign * fill, 9)
            if sign > 0:
                self.entry[intent.symbol] = price
        self.limits[intent.client_id] = {"status": "FILLED" if fill >= intent.qty else "NEW", "executedQty": fill, "avgPrice": price}
        return OrderResult(intent.client_id, "x", self.limits[intent.client_id]["status"], price if fill else None, fill, {})

    async def get_order(self, symbol, client_id):
        return dict(self.limits[client_id])

    async def cancel_order(self, symbol, client_id):
        self.calls.append(("cancel", client_id))
        if self.limits[client_id]["status"] != "FILLED":
            self.limits[client_id]["status"] = "CANCELED"

    async def cancel_all(self, symbol):
        self.calls.append(("cancel_all",))
        await super().cancel_all(symbol)

    async def market_order(self, intent):
        self.calls.append(("market", intent.side, intent.qty, intent.reduce_only))
        return await super().market_order(intent)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(desk_live, "MAKER_WINDOW_S", 0.2)
    monkeypatch.setattr(desk_live, "MAKER_REPRICE_S", 0.05)
    monkeypatch.setattr(desk_live, "MAKER_POLL_S", 0.01)
    real = asyncio.sleep
    monkeypatch.setattr(desk_live.asyncio, "sleep", lambda s: real(min(s, 0.01)))


def make(tmp_path, mode):
    ex = MakerEx(mode, mark=2.0, balance=1000.0)
    prov = FakeProviders(ex)
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    live = MacdCoinLive("STX", TrioMarket(tmp_path / "t.db", client=object()), accounts, prov, lambda: [], lambda *a: None,
                        clock=lambda: NOW)
    live.decide = lambda symbol, held: live._next
    live._next = (1, None)
    acct = run(accounts.add("STX sub", "binance", "testnet", "K-1", "S-1"))["id"]
    run(live.arm(acct, 100, 30, "GO LIVE MACD STX"))
    return ex, live


def enter(live, side=1, t=2):
    live._next = (t, ("enter", side))
    run(live.tick())
    return live.state()


def test_a_maker_entry_fills_at_the_best_price_with_the_maker_fee(tmp_path):
    ex, live = make(tmp_path, "fill")
    st = enter(live)
    p = st["position"]
    assert p["entry"] == pytest.approx(1.99) and abs(p["fee_est"] - p["qty"] * 1.99 * desk_live.MAKER_EST) < 1e-12
    assert not any(c[0] == "market" for c in ex.calls) and abs(ex.stops[-1] - 1.99 * (1 - desk_live.DISASTER)) < 1e-9
    assert st["log"][-3]["kind"] == "fill" and st["log"][-3]["how"] == "maker"


def test_no_maker_fill_falls_back_to_one_market_order(tmp_path):
    ex, live = make(tmp_path, "never")
    st = enter(live)
    assert st["position"]["qty"] > 0 and [c for c in ex.calls if c[0] == "market"][0][3] is False
    assert any(c[0] == "cancel" for c in ex.calls)                    # the resting limits were cancelled first
    assert [x for x in st["log"] if x["kind"] == "fill"][-1]["how"] == "market"


def test_a_partial_maker_fill_is_completed_at_market_with_a_blended_price(tmp_path):
    ex, live = make(tmp_path, "half")
    st = enter(live)
    p = st["position"]
    assert 1.99 < p["entry"] < 2.0 and [x for x in st["log"] if x["kind"] == "fill"][-1]["how"] == "maker+market"
    assert abs(ex.pos["STXUSDT"] - p["qty"]) < 1e-9


def test_a_refused_post_only_order_is_simply_placed_again(tmp_path):
    ex, live = make(tmp_path, "cross")
    st = enter(live)
    assert st["position"]["entry"] == pytest.approx(1.99) and sum(1 for c in ex.calls if c[0] == "limit") == 2


def test_a_maker_exit_keeps_the_disaster_stop_until_flat(tmp_path):
    ex, live = make(tmp_path, "fill")
    enter(live)
    ex.calls.clear()
    live._next = (3, ("exit", 0))
    run(live.tick())
    st = live.state()
    first_limit = next(i for i, c in enumerate(ex.calls) if c[0] == "limit")
    first_cancel_all = next(i for i, c in enumerate(ex.calls) if c[0] == "cancel_all")
    assert first_limit < first_cancel_all and ex.calls[first_limit][3] is True          # reduce-only, stop still there
    assert st["position"] is None and ex.pos["STXUSDT"] == 0 and st["trades"][-1]["reason"] == "rule exit"


def test_switching_off_stops_the_waiting_and_closes_at_market(tmp_path):
    ex, live = make(tmp_path, "fill")
    enter(live)
    ex.mode = "never"
    ex.calls.clear()
    live.request_abort()
    run(live.disarm("switched off by the operator"))
    st = live.state()
    assert st["status"] == "OFF" and ex.pos["STXUSDT"] == 0
    assert not any(c[0] == "limit" for c in ex.calls) and any(c[0] == "market" and c[3] for c in ex.calls)   # straight to market


def test_desks_without_maker_still_use_market_orders(tmp_path):
    from app.live.desk_live import DeskLive
    assert DeskLive.MAKER is False and MacdCoinLive.MAKER is True
