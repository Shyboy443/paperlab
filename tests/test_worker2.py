"""Worker 2 (afternoon session momentum): signal maths, event timing, the paper book on fake market data, and the live
switch's 16:00 open / 02:00 close against a fake Binance USD-M account (no network)."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.ensemble.data import Store
from app.live.accounts import LinkedAccounts
from app.worker2 import service as S
from app.worker2.live import MAX_LATE_MS, Worker2Live
from tests.test_ensemble import Ex
from tests.test_macd_live import FakeProviders, FakeVault

DAY, HOUR = S.DAY, S.HOUR
D0 = S.REPLAY_FROM                                   # 2026-07-01 00:00 UTC
SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "BTCUSDT"]


def bars(seed=1, jump_day=D0 + 2 * DAY, jump=0.15):
    """Hourly opens/closes from 2026-05-20, 1 % hourly noise; AAA rises `jump` between 00:00 and 15:00 of jump_day."""
    rng = np.random.default_rng(seed)
    t0 = D0 - 42 * DAY
    T = 50 * DAY // HOUR
    lr = rng.normal(0, 0.01, (T, len(SYMS)))
    b = (jump_day - t0) // HOUR
    lr[b:b + 15, 0] = np.log(1 + jump) / 15
    O = 100 * np.exp(np.cumsum(np.vstack([np.zeros((1, len(SYMS))), lr[:-1]]), 0))
    C = 100 * np.exp(np.cumsum(lr, 0))
    return t0, O, C


class FakeRest:
    def __init__(self):
        self.t0, self.O, self.C = bars()

    def perps(self):
        return [{"symbol": s, "status": "TRADING", "underlyingType": "COIN", "onboardDate": 0} for s in SYMS]

    def klines(self, s, interval, start, end):
        j = SYMS.index(s)
        i0, i1 = max(0, (start - self.t0) // HOUR), min(len(self.O), (end - self.t0) // HOUR)
        return [[self.t0 + i * HOUR, self.O[i, j], 0, 0, self.C[i, j], 0, 0, 1e6] for i in range(i0, i1)]

    def funding(self, s, start, end):
        return []


def daily(tmp_path):
    st = Store(tmp_path / "ens.db")
    for k, s in enumerate(SYMS):
        rows = [[D0 - 40 * DAY + i * DAY, 100, 101, 99, 100, 0, 0, 1e9 * (k + 1)] for i in range(45)]
        st.save_d1(s, rows, D0 + 10 * DAY)
    return st


def test_signal_is_the_research_formula():
    t0, O, C = bars()
    b = (D0 + 2 * DAY - t0) // HOUR
    sig = S.day_signals(O, C, b)
    j = [x for x in sig if x[0] == 0]
    assert j and j[0][1] == 1.0                                       # AAA: long
    v = S.hvol(O, b)[0]
    z = np.log(C[b + 15, 0] / O[b, 0]) / (v * 4)
    assert z >= 2 and j[0][2] == pytest.approx(min(1.0, 0.01154 / v))


def test_event_timing():
    d = D0 + 5 * DAY
    assert S.Worker2Service.event_at(d + 16 * HOUR + 60_000) == ("open", d + 16 * HOUR, d)
    assert S.Worker2Service.event_at(d + 3 * HOUR) == ("close", d + 2 * HOUR, d - DAY)
    assert S.Worker2Service.event_at(d + HOUR) == ("open", d - DAY + 16 * HOUR, d - DAY)


def test_paper_book_opens_at_16_and_books_at_02(tmp_path):
    clock = {"t": (D0 + 2 * DAY + 16 * HOUR + 60_000) / 1000}
    svc = S.Worker2Service(tmp_path / "w2.db", daily(tmp_path), rest=FakeRest(), clock=lambda: clock["t"])
    assert svc.cycle()
    ev = svc.latest()
    assert ev["kind"] == "open" and "AAAUSDT" in ev["targets"] and ev["targets"]["AAAUSDT"]["units"] > 0
    assert "BTCUSDT" not in svc.store.get("book")["universe"]
    assert not svc.cycle()                                             # same event: nothing to do
    clock["t"] = (D0 + 3 * DAY + 2 * HOUR + 60_000) / 1000
    assert svc.cycle()
    b = svc.store.get("book")
    assert b["event"]["kind"] == "close" and b["event"]["targets"] == {}
    assert b["curve"][-1][0] == "2026-07-03" and any(t["symbol"] == "AAAUSDT" and t["day"] == "2026-07-03" for t in b["trades"])
    assert svc.public()["positions"] == [] and svc.public()["event"]["kind"] == "close"


class Book:
    def __init__(self, tmp_path, ev):
        self.store = Store(tmp_path / "w2live.db")
        self.ev = ev

    def latest(self):
        return self.ev


def make(tmp_path, ev, now_ms):
    ex = Ex(balance=3000.0)
    prov = FakeProviders(ex, switch=False)
    prov.max_amount = lambda: 5000.0
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    book = Book(tmp_path, ev)
    live = Worker2Live(book, accounts, prov, lambda: [], lambda *a: None, clock=lambda: now_ms / 1000)
    acct = asyncio.run(accounts.add("W2 sub", "binance", "testnet", "KEY-123", "SECRET-xyz"))
    return SimpleNamespace(ex=ex, book=book, live=live, acct=acct["id"])


OPEN_T = 1_800_000_000_000 // DAY * DAY + 16 * HOUR


def ev_open(targets):
    return {"kind": "open", "day": OPEN_T, "date": "d 16:00 UTC", "equity": 10_000.0, "targets": targets, "trades": {}}


def test_fresh_open_is_copied_and_the_close_flattens(tmp_path):
    k = make(tmp_path, ev_open({"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1}}), OPEN_T + 60_000)
    v = asyncio.run(k.live.arm(k.acct, 1000, None, "GO LIVE WORKER 2"))
    assert v["status"] == "ARMED" and k.ex.pos["AAAUSDT"] == pytest.approx(20 * 0.95 * 1000 / 10_000)
    k.book.ev = {"kind": "close", "day": OPEN_T + 10 * HOUR, "date": "d 02:00", "equity": 10_100.0, "targets": {}, "trades": {}}
    asyncio.run(k.live.tick())
    assert not any(k.ex.pos.values()) and k.live.state()["status"] == "ARMED"


def test_a_late_open_is_not_bought(tmp_path):
    k = make(tmp_path, ev_open({"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1}}), OPEN_T + MAX_LATE_MS + 60_000)
    v = asyncio.run(k.live.arm(k.acct, 1000, None, "GO LIVE WORKER 2"))
    assert v["status"] == "ARMED" and not any(k.ex.pos.values())
    assert k.live.state()["last_day"] == OPEN_T
    asyncio.run(k.live.tick())                                         # same event again: still nothing
    assert not any(k.ex.pos.values())


def test_switch_on_works_on_a_day_without_signals(tmp_path):
    k = make(tmp_path, ev_open({}), OPEN_T + 60_000)
    v = asyncio.run(k.live.arm(k.acct, 1000, None, "GO LIVE WORKER 2"))
    assert v["status"] == "ARMED" and not any(k.ex.pos.values())


def test_worker2_minimum_is_200_and_worker1_stays_500(tmp_path):
    k = make(tmp_path, ev_open({}), OPEN_T + 60_000)
    assert k.live.view()["rules"]["min_amount"] == 200
    with pytest.raises(Exception, match="between 200 and"):
        asyncio.run(k.live.arm(k.acct, 199, None, "GO LIVE WORKER 2"))
    v = asyncio.run(k.live.arm(k.acct, 200, None, "GO LIVE WORKER 2"))
    assert v["status"] == "ARMED"
    from app.ensemble.live import EnsembleLive
    assert EnsembleLive.MIN_AMOUNT == 500


# -- reliability (system review 2026-10-25) ------------------------------------------------------------------------------
from app.exchange.client import ExchangeClient  # noqa: E402

A20 = {"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1}}


class LossyEx(Ex):
    """Fills the order, then the answer is lost (timeout); the order can be looked up by client id."""
    lose = False
    _result = staticmethod(ExchangeClient._result)

    async def market_order(self, intent):
        r = await super().market_order(intent)
        self.done = {intent.client_id: r}
        if self.lose:
            raise TimeoutError("read timed out")
        return r

    async def get_order(self, symbol, cid):
        r = self.done[cid]
        return {"status": "FILLED", "executedQty": str(r.executed_qty), "avgPrice": str(self.mark), "orderId": "x"}


def armed(tmp_path):
    k = make(tmp_path, ev_open(A20), OPEN_T + 60_000)
    asyncio.run(k.live.arm(k.acct, 1000, None, "GO LIVE WORKER 2"))
    return k


def test_a_lost_order_answer_is_looked_up_not_halted(tmp_path):
    k = make(tmp_path, ev_open(A20), OPEN_T + 60_000)
    k.ex.__class__ = LossyEx
    k.ex.lose = True
    asyncio.run(k.live.arm(k.acct, 1000, None, "GO LIVE WORKER 2"))
    st = k.live.state()
    assert st["positions"]["AAAUSDT"] == pytest.approx(1.9) and k.ex.pos["AAAUSDT"] == pytest.approx(1.9)
    asyncio.run(k.live.tick())
    assert k.live.state()["status"] == "ARMED"                        # recorded, so reconciliation agrees


def test_the_close_runs_on_the_clock_when_the_book_is_late(tmp_path):
    k = armed(tmp_path)
    assert k.ex.pos["AAAUSDT"] > 0
    k.live.clock = lambda: (OPEN_T + 10 * HOUR + 60_000) / 1000      # 02:01, the book still says "open"
    asyncio.run(k.live.tick())
    assert k.ex.pos["AAAUSDT"] > 0                                     # inside the grace: wait for the book
    k.live.clock = lambda: (OPEN_T + 10 * HOUR + 6 * 60_000) / 1000    # 02:06
    asyncio.run(k.live.tick())
    assert not any(k.ex.pos.values()) and k.live.state()["last_day"] == OPEN_T + 10 * HOUR
    n = len(k.ex.orders)
    k.book.ev = {"kind": "close", "day": OPEN_T + 10 * HOUR, "date": "d 02:00", "equity": 10_000.0, "targets": {}, "trades": {}}
    asyncio.run(k.live.tick())                                          # the book's late close: already done
    assert len(k.ex.orders) == n


def test_a_failed_close_is_retried_until_it_works(tmp_path):
    k = armed(tmp_path)
    k.ex.fail_close = True
    k.book.ev = {"kind": "close", "day": OPEN_T + 10 * HOUR, "date": "d 02:00", "equity": 10_000.0, "targets": {}, "trades": {}}
    k.live.clock = lambda: (OPEN_T + 10 * HOUR + 60_000) / 1000
    asyncio.run(k.live.tick())
    assert k.ex.pos["AAAUSDT"] > 0 and k.live.state()["last_day"] == OPEN_T
    k.ex.fail_close = False
    asyncio.run(k.live.tick())
    assert not any(k.ex.pos.values()) and k.live.state()["last_day"] == OPEN_T + 10 * HOUR


def test_switch_off_closes_what_the_account_holds_even_if_unrecorded(tmp_path):
    k = armed(tmp_path)
    k.ex.pos["ZZZUSDT"] = -3.0                                          # e.g. an order whose fill was never recorded
    asyncio.run(k.live.tick())
    assert k.live.state()["status"] == "HALTED"
    v = asyncio.run(k.live.disarm("switched off by the operator"))
    assert v["status"] == "OFF" and not any(k.ex.pos.values())
