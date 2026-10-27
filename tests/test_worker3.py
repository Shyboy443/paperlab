"""Worker 3 (30-day-high long/short): the frozen rule's maths, the 1x short liquidation, the paper book on a fake daily
store (waits for Worker 1's data), and the live switch against a fake Binance USD-M account (no network)."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.ensemble.data import Store
from app.live.accounts import LinkedAccounts
from app.worker3 import engine as E
from app.worker3 import service as S
from app.worker3.live import Worker3Live
from tests.test_ensemble import Ex
from tests.test_macd_live import FakeProviders, FakeVault

DAY = E.DAY


def synth(T=80, N=24, seed=5):
    """Random-walk daily bars; coin 0 trends up (always near its high), coin 1 trends down (always near its low)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.02, (T, N))
    r[:, 0] += 0.01
    r[:, 1] -= 0.01
    C = 100 * np.exp(np.cumsum(r, 0))
    O = np.vstack([C[:1], C[:-1]])
    H, L = np.maximum(O, C) * 1.005, np.minimum(O, C) * 0.995
    QV = np.tile(np.linspace(2e8, 1e8, N), (T, 1))
    t = np.arange(T, dtype=np.int64) * DAY + S.DATA_FROM
    return O, H, L, C, QV, np.zeros((T, N)), t, np.zeros(N, np.int64)


def test_score_is_the_position_in_the_30_day_range():
    O, H, L, C, *_ = synth()
    s = E.score(H, L, C)
    i, j = 50, 3
    lo, hi = L[i - 30:i, j].min(), H[i - 30:i, j].max()
    assert s[i, j] == pytest.approx((C[i - 1, j] - lo) / (hi - lo))
    assert np.isnan(s[20]).all()                                       # fewer than 27 days of range


def test_universe_is_the_top_20_by_30_day_volume():
    O, H, L, C, QV, F, t, ob = synth()
    U = E.universe(C, QV, t, ob)
    assert U[40].sum() == 20 and U[40, :20].all() and not U[40, 20:].any()
    ob2 = ob.copy()
    ob2[0] = int(t[40]) - 10 * DAY                                     # listed 10 days before: not eligible
    assert not E.universe(C, QV, t, ob2)[40, 0]


def test_book_longs_the_highs_shorts_the_lows_at_a_tenth_each():
    O, H, L, C, QV, F, t, ob = synth()
    r = E.run(O, H, L, C, QV * 1e3, F, t, ob, 40, 60, last_open_only=True)
    u = r["units"]
    assert (u > 0).sum() == 5 and (u < 0).sum() == 5
    assert u[0] > 0 and u[1] < 0
    w = u * O[59] / r["equity"]
    assert np.allclose(np.abs(w[u != 0]), 0.1, rtol=1e-3)                # sized before the day's costs (as research)
    assert len(r["curve"]) == 19                                       # the last day: open only


def test_a_short_on_a_coin_that_doubles_loses_its_margin_only():
    O, H, L, C, QV, F, t, ob = synth()
    a = E.run(O, H, L, C, QV * 1e3, F, t, ob, 40, 46)
    j = int(np.where(a["units"] < 0)[0][0])
    H2, C2 = H.copy(), C.copy()
    H2[45, j], C2[45, j] = O[45, j] * 3.0, O[45, j] * 2.5             # +200 % intraday
    b = E.run(O, H2, L, C2, QV * 1e3, F, t, ob, 40, 46)
    assert b["liquidations"] == 1 and b["liquidated"][0][0] == j
    lost = a["curve"][-2][1] - b["curve"][-1][1]
    pos = 0.1 * a["curve"][-2][1]
    assert lost == pytest.approx(pos, rel=0.15)                        # ~ the position's margin, not 150 % of it


class FakeRest:
    def __init__(self, syms):
        self.syms = syms

    def perps(self):
        return [{"symbol": s, "status": "TRADING", "underlyingType": "COIN", "onboardDate": 0} for s in self.syms]

    def funding(self, s, start, end):
        return []


def daily_store(tmp_path, upto):
    O, H, L, C, QV, F, t, ob = synth(T=(upto - S.DATA_FROM) // DAY + 1)
    st = Store(tmp_path / "ens.db")
    syms = [f"C{j:02d}USDT" for j in range(C.shape[1])]
    for j, s in enumerate(syms):
        rows = [[int(t[i]), O[i, j], H[i, j], L[i, j], C[i, j], 0, 0, QV[i, j] * 1e3] for i in range(len(t))]
        st.save_d1(s, rows, upto + 2 * 3_600_000)                      # the last day: open only (not complete)
    return st, syms


def test_paper_book_waits_for_worker1_then_posts_the_day(tmp_path):
    day = S.BOOK_FROM + 5 * DAY
    st, syms = daily_store(tmp_path, day)
    ens = SimpleNamespace(store=st, lock=__import__("threading").RLock(), refresh_daily=lambda d, n: st.set("done_day", d))
    clock = {"t": (day + 5 * 60_000) / 1000}
    svc = S.Worker3Service(tmp_path / "w3.db", ens, rest=FakeRest(syms), clock=lambda: clock["t"])
    assert not svc.cycle() and "waiting" in svc.status["phase"]       # Worker 1 has not refreshed yet
    st.set("done_day", day)
    assert svc.cycle()
    today = svc.latest()
    assert today["date"] == S.ymd(day) and len(today["targets"]) == 10
    assert today["targets"]["C00USDT"]["units"] > 0 and today["targets"]["C01USDT"]["units"] < 0
    b = svc.store.get("book")
    assert b["curve"][-1][0] == S.ymd(day - DAY) and b["n_positions"] == 10
    assert not svc.cycle()                                             # same day: nothing to do
    pub = svc.public()
    assert pub["positions"][0][1] > 0 and pub["positions"][-1][1] < 0 and "today" in pub


def test_paper_book_refreshes_the_bars_itself_when_worker1_is_late(tmp_path):
    day = S.BOOK_FROM + 5 * DAY
    st, syms = daily_store(tmp_path, day)
    called = []
    ens = SimpleNamespace(store=st, lock=__import__("threading").RLock(), refresh_daily=lambda d, n: called.append(d))
    svc = S.Worker3Service(tmp_path / "w3.db", ens, rest=FakeRest(syms), clock=lambda: (day + S.FALLBACK_MS + 1) / 1000)
    assert svc.cycle() and called == [day]


class Book:
    def __init__(self, tmp_path, today):
        self.store = Store(tmp_path / "w3live.db")
        self.today = today

    def latest(self):
        return self.today


def test_live_switch_ships_off_and_copies_the_book(tmp_path):
    ex = Ex(balance=3000.0)
    prov = FakeProviders(ex, switch=False)
    prov.max_amount = lambda: 5000.0
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    today = {"day": 1_800_000_000_000 // DAY * DAY, "date": "d", "equity": 10_000.0,
             "targets": {"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1},
                         "BBBUSDT": {"units": -10.0, "price": 100.0, "w": -0.1}}, "trades": {}}
    live = Worker3Live(Book(tmp_path, today), accounts, prov, lambda: [], lambda *a: None,
                       clock=lambda: (today["day"] + 120_000) / 1000)
    assert live.view()["status"] == "OFF" and live.view()["rules"]["min_amount"] == 200.0
    acct = asyncio.run(accounts.add("W3 sub", "binance", "testnet", "KEY-123", "SECRET-xyz"))["id"]
    with pytest.raises(Exception):
        asyncio.run(live.arm(acct, 1000, None, "GO LIVE WORKER 2"))   # wrong phrase
    with pytest.raises(Exception):
        asyncio.run(live.arm(acct, 150, None, "GO LIVE WORKER 3"))    # below the minimum
    v = asyncio.run(live.arm(acct, 1000, None, "GO LIVE WORKER 3"))
    assert v["status"] == "ARMED"
    assert ex.pos["AAAUSDT"] == pytest.approx(20 * 0.95 * 1000 / 10_000)
    assert ex.pos["BBBUSDT"] == pytest.approx(-10 * 0.95 * 1000 / 10_000)
