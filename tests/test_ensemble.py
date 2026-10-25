"""Ensemble: engine port details and the live switch against a fake Binance USD-M account (no network)."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.types import ExchangePosition
from app.ensemble import engine as E
from app.ensemble.data import Store
from app.ensemble.live import EnsembleLive, LiveError
from app.live.accounts import LinkedAccounts
from tests.test_macd_live import FakeEx, FakeProviders, FakeVault

DAY = E.DAY


def panel(T=120, N=60, seed=3):
    rng = np.random.default_rng(seed)
    C = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, (T, N)), 0))
    O = np.vstack([C[:1], C[:-1]])
    H, L = np.maximum(O, C) * 1.01, np.minimum(O, C) * 0.99
    Q = rng.uniform(1e6, 1e8, (T, N))
    day = E.ANCHOR + np.arange(T) * DAY
    return E.Data([f"C{j}USDT" for j in range(N)], day, O, H, L, C, Q, np.zeros((T, N)))


def test_rotation_rebalances_on_the_research_days_only():
    D = panel()
    F = E.leg_rotation(D, **E.LEGP["R"])
    leg = ("R", F, E.trade_mask(D, F, "R"))
    start = int(D.day[45])                               # not an anchor day (45 % 7 != 0)
    a = E.simulate(D, [leg], start, int(D.day[47]))
    b = E.simulate(D, [leg], start, int(D.day[48]))     # day 47: still not a rebalance day -> no trades
    assert np.allclose(a["state"]["units"], b["state"]["units"])
    c = E.simulate(D, [leg], start, int(D.day[50]))     # day 49 = 7 x 7: the rotation re-sizes
    assert not np.allclose(b["state"]["units"], c["state"]["units"])


def test_ml_leg_long_best_short_worst():
    D = panel()
    d = int(D.day[60])
    preds = {d: {f"C{j}USDT": float(j) for j in range(10)}}
    F = E.leg_ml(D, preds, 3)
    j = {s: i for i, s in enumerate(D.syms)}
    assert all(F[60, j[f"C{k}USDT"]] > 0 for k in (7, 8, 9))
    assert all(F[60, j[f"C{k}USDT"]] < 0 for k in (0, 1, 2))
    assert np.count_nonzero(F[60]) == 6


class Book:
    def __init__(self, tmp_path, targets, trades=None, day=1_800_000_000_000 // DAY * DAY, equity=10_000.0):
        self.store = Store(tmp_path / "ens.db")
        self.t = {"day": day, "date": "x", "equity": equity, "targets": targets, "trades": trades or {}}

    def latest(self):
        return self.t


class Ex(FakeEx):
    async def fetch_positions(self, symbols, all_symbols=False):
        return {s: ExchangePosition(s, q, self.mark, None, 0.0, 0.0, 1, self.mark) for s, q in self.pos.items() if q}


def make(tmp_path, targets, amount_cap=5000.0):
    ex = Ex(balance=3000.0)
    prov = FakeProviders(ex, switch=False)
    prov.max_amount = lambda: amount_cap
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    book = Book(tmp_path, targets)
    live = EnsembleLive(book, accounts, prov, lambda: [], lambda *a: None, clock=lambda: 1_800_000_000)
    acct = asyncio.run(accounts.add("Ens sub", "binance", "testnet", "KEY-123", "SECRET-xyz"))
    return SimpleNamespace(ex=ex, book=book, live=live, acct=acct["id"], accounts=accounts)


T0 = {"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1}, "BBBUSDT": {"units": -10.0, "price": 50.0, "w": -0.05}}


def run(c):
    return asyncio.run(c)


def test_switch_on_opens_the_book_scaled_to_the_amount(tmp_path):
    k = make(tmp_path, T0)
    v = run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))
    assert v["status"] == "ARMED"
    r = 0.95 * 1000 / 10_000                              # SCALE x allocation / book equity
    assert k.ex.pos["AAAUSDT"] == pytest.approx(20 * r)
    assert k.ex.pos["BBBUSDT"] == pytest.approx(-10 * r)
    assert k.accounts.get(k.acct)["owner"]["ref"] == "Ensemble"


def test_refuses_wrong_phrase_small_amount_and_busy_account(tmp_path):
    k = make(tmp_path, T0)
    with pytest.raises(LiveError):
        run(k.live.arm(k.acct, 1000, 300, "go live"))
    with pytest.raises(LiveError):
        run(k.live.arm(k.acct, 100, 30, "GO LIVE ENSEMBLE"))     # below the minimum
    k.ex.pos["XUSDT"] = 1.0
    with pytest.raises(LiveError):
        run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))   # not flat


def test_daily_rebalance_trades_only_the_books_trades(tmp_path):
    k = make(tmp_path, T0)
    run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))
    n = len(k.ex.orders)
    k.book.t = {"day": k.book.t["day"] + DAY, "date": "y", "equity": 10_000.0,
                "targets": {"AAAUSDT": {"units": 20.0, "price": 50.0, "w": 0.1}, "CCCUSDT": {"units": 30.0, "price": 50.0, "w": 0.15}},
                "trades": {"CCCUSDT": {"from": 0.0, "to": 30.0, "price": 50.0}, "BBBUSDT": {"from": -10.0, "to": 0.0, "price": 50.0}}}
    run(k.live.tick())
    assert k.ex.pos["BBBUSDT"] == 0 and k.ex.pos["CCCUSDT"] == pytest.approx(30 * 0.095)
    assert k.ex.pos["AAAUSDT"] == pytest.approx(20 * 0.095)          # untouched
    assert len(k.ex.orders) == n + 2
    assert k.ex.orders[n][2] is True                                   # the close went first, reduce-only
    run(k.live.tick())                                                 # same day again: nothing
    assert len(k.ex.orders) == n + 2


def test_foreign_position_halts_and_vanished_one_is_booked(tmp_path):
    k = make(tmp_path, T0)
    run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))
    k.ex.pos["BBBUSDT"] = 0.0                                          # e.g. a 1x short liquidated
    run(k.live.tick())
    assert k.live.state()["status"] == "ARMED" and k.live.state()["positions"]["BBBUSDT"] == 0.0
    k.ex.pos["ZZZUSDT"] = 3.0                                          # someone else traded the account
    run(k.live.tick())
    assert k.live.state()["status"] == "HALTED"


def test_switch_off_and_loss_limit_close_everything(tmp_path):
    k = make(tmp_path, T0)
    run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))
    k.ex.balance = 3000.0 - 350                                        # the account lost 350 > loss limit 300
    run(k.live.tick())
    st = k.live.state()
    assert st["status"] == "OFF" and not any(k.ex.pos.values())
    assert k.accounts.get(k.acct).get("owner") is None
    k.ex.balance = 3000.0
    run(k.live.arm(k.acct, 1000, 300, "GO LIVE ENSEMBLE"))
    v = run(k.live.disarm("switched off by the operator"))
    assert v["status"] == "OFF" and not any(k.ex.pos.values())


def test_switch_on_without_a_loss_limit_never_stops_on_losses(tmp_path):
    k = make(tmp_path, T0)
    v = run(k.live.arm(k.acct, 1000, None, "GO LIVE ENSEMBLE"))
    assert v["status"] == "ARMED" and v["max_loss"] is None
    k.ex.balance = 3000.0 - 900                                        # a deep drawdown: no loss limit, it stays on
    run(k.live.tick())
    assert k.live.state()["status"] == "ARMED" and any(k.ex.pos.values())
