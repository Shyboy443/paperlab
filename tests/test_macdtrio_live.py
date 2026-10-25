"""MACD coin bots (STX / DASH / XLM): the backtest's long/short decisions, reversals through the shared live executor,
one state and one account per coin (fake exchange, no network)."""
import asyncio
import math

import pytest

from app.live.accounts import AccountError, LinkedAccounts
from app.live.desk_live import DISASTER, LiveError
from app.macdtrio import sim
from app.macdtrio.live import COINS, MacdCoinLive, TrioMarket, backtests
from tests.test_macd_live import FakeEx, FakeProviders, FakeVault

H4 = sim.H4
NOW = 1_800_000_000


def run(c):
    return asyncio.run(c)


class FakeClient:
    def __init__(self, bars):
        self.bars = bars

    def get(self, venue, endpoint, params, weight=5):
        assert (venue, endpoint) == ("perp", "klines")
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], 1.0] for b in self.bars
                if params["startTime"] <= b["t"] <= params["endTime"]][:params["limit"]]


def wave(n, t_end):
    cl = [100 + 30 * math.sin(i / 12) for i in range(n)]
    t0 = t_end - n * H4
    return [{"t": t0 + i * H4, "o": cl[i - 1] if i else cl[0], "h": max(cl[i], cl[i - 1] if i else cl[0]) * 1.002,
             "l": min(cl[i], cl[i - 1] if i else cl[0]) * 0.998, "c": cl[i]} for i in range(n)]


def make(tmp_path, bars=None, coin="STX"):
    ex = FakeEx(mark=2.0, balance=1000.0)
    prov = FakeProviders(ex)
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    market = TrioMarket(tmp_path / "trio.db", client=FakeClient(bars or []), clock=lambda: NOW)
    live = MacdCoinLive(coin, market, accounts, prov, lambda: [], lambda *a: None, clock=lambda: NOW)
    live.MAKER = False                  # reversal / sizing logic here; maker execution: tests/test_maker_execution.py
    acct = run(accounts.add(coin + " sub", "binance", "testnet", "K-" + coin, "S-" + coin))["id"]
    return ex, accounts, market, live, acct


def test_decisions_are_the_backtests_signals_long_and_short(tmp_path):
    end = (NOW * 1000 // H4) * H4                                      # the newest completed candle closes at `end`
    bars = wave(900, end)
    sig = sim.signals(bars)
    seen = {1: 0, -1: 0}
    for k in range(800, 900):
        prefix = bars[:k + 1]
        ex, accounts, market, live, acct = make(tmp_path / str(k), prefix)
        live.clock = lambda k=k: (prefix[-1]["t"] + H4) / 1000
        for held in (-1, 0, 1):
            t, action = live.decide(live.symbol, held)
            assert t == prefix[-1]["t"]
            s = sig[k]
            if s == 0 or s == held:
                assert action is None
            else:
                assert action == (("reverse" if held else "enter"), s)
                seen[s] += 1
    assert seen[1] and seen[-1]


def test_reversal_closes_then_opens_the_other_side_with_a_stop_each_time(tmp_path):
    ex, accounts, market, live, acct = make(tmp_path)
    live._next = (1, None)
    live.decide = lambda symbol, held: live._next
    with pytest.raises(LiveError, match="GO LIVE MACD STX"):
        run(live.arm(acct, 100, 30, "GO LIVE MACD DASH"))
    v = run(live.arm(acct, 100, 30, "GO LIVE MACD STX"))
    assert v["status"] == "ARMED" and v["symbol"] == "STXUSDT" and v["rules"]["shorts"] is True
    live._next = (2, ("enter", 1))
    run(live.tick())
    assert ex.pos["STXUSDT"] > 0 and abs(ex.stops[-1] - 2.0 * (1 - DISASTER)) < 1e-12
    ex.mark = 2.4
    live._next = (3, ("reverse", -1))
    run(live.tick())
    st = live.state()
    assert ex.orders[1][2] is True and st["position"]["side"] == "short" and ex.pos["STXUSDT"] < 0   # reduce-only close first
    assert st["trades"][-1]["pnl"] > 0 and abs(ex.stops[-1] - 2.4 * (1 + DISASTER)) < 1e-12


def test_each_coin_has_its_own_state_and_an_account_serves_one_bot(tmp_path):
    ex, accounts, market, stx, acct = make(tmp_path)
    dash = MacdCoinLive("DASH", market, accounts, stx.providers, lambda: [], lambda *a: None, clock=lambda: NOW)
    for b in (stx, dash):
        b.decide = lambda symbol, held: (1, None)
    run(stx.arm(acct, 100, 30, "GO LIVE MACD STX"))
    assert stx.STATE_KEY != dash.STATE_KEY and dash.state().get("status", "OFF") == "OFF"
    with pytest.raises(AccountError, match="one strategy per account"):
        run(dash.arm(acct, 100, 30, "GO LIVE MACD DASH"))


def test_the_shipped_backtests_have_monthly_and_weekly_returns():
    bt = backtests()
    assert set(bt) == {c + "USDT" for c in COINS} | {"PORTFOLIO"}
    assert bt["PORTFOLIO"]["dd"] < min(bt[c + "USDT"]["dd"] for c in COINS)   # together they swing less than any one
    for b in (bt[c + "USDT"] for c in COINS):
        assert b["months"] == len(b["monthly"]) == 25 and b["weeks"] == len(b["weekly"]) and "hindsight" in b["caveat"]


def test_a_position_uses_the_full_allocation(tmp_path):
    from app.macdtrio.live import SIZE
    ex, accounts, market, live, acct = make(tmp_path)
    live.decide = lambda symbol, held: live._next
    live._next = (1, None)
    run(live.arm(acct, 150, 50, "GO LIVE MACD STX"))
    live._next = (2, ("enter", -1))
    run(live.tick())
    notional = live.state()["position"]["qty"] * 2.0
    assert abs(SIZE - 1.0) < 1e-12 and 148 <= notional <= 150            # all of 150, less the fee/slippage room
    assert live.view()["rules"]["size"] == SIZE
