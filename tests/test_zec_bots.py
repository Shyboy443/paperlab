"""ZEC bots: the six ported TradingView rules (Pine semantics), the long-only decisions on 1h and 4h candles through the
shared live executor, one state and one switch per bot, and the public backtest endpoint (fake exchange, no network)."""
import asyncio
import math
import random

import pytest

from app.live.accounts import LinkedAccounts
from app.live.desk_live import LiveError
from app.macdtrio import sim
from app.zecbots import rules
from app.zecbots.live import ZecBotLive, ZecMarket, backtests
from tests.test_macd_live import FakeEx, FakeProviders, FakeVault

NOW = 1_800_000_000


def run(c):
    return asyncio.run(c)


class FakeClient:
    def __init__(self, bars):
        self.bars = bars
        self.intervals = []

    def get(self, venue, endpoint, params, weight=5):
        assert (venue, endpoint, params["symbol"]) == ("perp", "klines", "ZECUSDT")
        self.intervals.append(params["interval"])
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], 1.0] for b in self.bars
                if params["startTime"] <= b["t"] <= params["endTime"]][:params["limit"]]


def wave(n, t_end, tf, amp=30.0, period=12.0):
    rnd, walk, cl = random.Random(7), 0.0, []
    for i in range(n):                               # a sine plus a random walk: real-looking crosses and pullbacks
        walk += rnd.gauss(0, 1.2)
        cl.append(max(5.0, 150 + amp * math.sin(i / period) + walk))
    t0 = t_end - n * tf
    return [{"t": t0 + i * tf, "o": cl[i - 1] if i else cl[0], "h": max(cl[i], cl[i - 1] if i else cl[0]) * 1.004,
             "l": min(cl[i], cl[i - 1] if i else cl[0]) * 0.996, "c": cl[i]} for i in range(n)]


def make(tmp_path, key, bars=None):
    ex = FakeEx(mark=2.0, balance=1000.0)
    prov = FakeProviders(ex)
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    client = FakeClient(bars or [])
    market = ZecMarket(tmp_path / "zec.db", client=client, clock=lambda: NOW)
    live = ZecBotLive(key, market, accounts, prov, lambda: [], lambda *a: None, clock=lambda: NOW)
    live.MAKER = False
    acct = run(accounts.add(key + " sub", "binance", "testnet", "K-" + key, "S-" + key))["id"]
    return ex, accounts, client, live, acct


def test_pine_indicators():
    up = [float(i) for i in range(1, 40)]
    assert rules.rsi(up, 14)[-1] == 100.0 and rules.rsi(up, 14)[13] is None and rules.rsi(up, 14)[14] == 100.0
    assert rules.rsi(up[::-1], 14)[-1] == 0.0
    assert rules.rma([2.0] * 5 + [7.0], 5)[4] == 2.0 and rules.rma([2.0] * 5 + [7.0], 5)[5] == pytest.approx(3.0)
    x = [1.0, 2, 3, 4, 10]
    m = sum(x) / 5
    dev = sum(abs(v - m) for v in x) / 5
    assert rules.cci(x, 5)[-1] == pytest.approx((10 - m) / (0.015 * dev)) and rules.cci(x, 5)[3] is None
    a, b = [1, 3, 1], [2, 2, 2]
    assert rules.crossover(a, b, 1) and not rules.crossover(a, b, 2) and rules.crossunder(a, b, 2)


@pytest.mark.parametrize("key", list(rules.RULES))
def test_every_rule_gives_one_request_per_candle_and_both_sides_happen(key):
    _, fn, tf, _, _ = rules.RULES[key]
    bars = wave(3000, 3000 * tf, tf, amp=30.0, period=40.0)
    buy, sell = fn(bars)
    assert len(buy) == len(sell) == len(bars)
    assert not any(b and s for b, s in zip(buy, sell))
    if key != "scalpslayer":                 # three growing same-colour candles: a smooth wave never makes them
        assert any(buy) and any(sell)


def test_scalp_slayer_fires_on_three_growing_candles_above_the_trend():
    bars = [{"t": i, "o": 100.0, "h": 100.5, "l": 99.5, "c": 100.0 + 0.01 * (i % 2)} for i in range(80)]
    for o, c in ((100.0, 101.0), (101.0, 101.5), (101.5, 104.0)):      # bodies 1.0, 0.5, 2.5: the middle one smallest
        bars.append({"t": len(bars), "o": o, "h": c + 0.1, "l": o - 0.1, "c": c})
    buy, sell = rules.scalp_slayer(bars)
    assert buy[-1] and not any(buy[:-1]) and not any(sell)


@pytest.mark.parametrize("key", ["emarsi", "emacross"])            # one 4h bot, one 1h bot
def test_decisions_are_the_rules_signals_long_only(tmp_path, key):
    tf = rules.RULES[key][2]
    end = (NOW * 1000 // tf) * tf
    bars = wave(1600, end, tf, period=40.0)
    buy, sell = rules.RULES[key][1](bars)
    seen = {"enter": 0, "exit": 0}
    ks = [k for k in range(1300, 1600) if buy[k] or sell[k]][:16] + list(range(1400, 1600, 25))   # every signal + quiet candles
    for k in ks:
        prefix = bars[:k + 1]
        ex, accounts, client, live, acct = make(tmp_path / str(k), key, prefix)
        live.clock = lambda k=k: (prefix[-1]["t"] + tf) / 1000
        for held in (0, 1):
            t, action = live.decide(live.symbol, held)
            assert t == prefix[-1]["t"]
            if buy[k] and held == 0:
                assert action == ("enter", 1)
                seen["enter"] += 1
            elif sell[k] and held == 1:
                assert action == ("exit", 0)
                seen["exit"] += 1
            else:
                assert action is None                             # never a short
        assert set(client.intervals) == {rules.RULES[key][3]}
    assert seen["enter"] and seen["exit"]


def test_each_bot_has_its_own_switch_state_and_phrase_and_never_shorts(tmp_path):
    ex, accounts, client, live, acct = make(tmp_path, "momentum")
    live.decide = lambda symbol, held: (1, None)
    with pytest.raises(LiveError, match="GO LIVE ZEC MOMENTUM"):
        run(live.arm(acct, 100, 30, "GO LIVE ZEC EMARSI"))
    v = run(live.arm(acct, 100, 30, "GO LIVE ZEC MOMENTUM"))
    assert v["status"] == "ARMED" and v["symbol"] == "ZECUSDT" and v["rules"]["shorts"] is False and v["interval"] == "4h"
    assert live.STATE_KEY == "live:zec:momentum"
    live.decide = lambda symbol, held: (2, ("enter", -1))
    run(live.tick())
    assert live.state()["position"] is None                          # a short request is ignored
    live.decide = lambda symbol, held: (3, ("enter", 1))
    run(live.tick())
    assert live.state()["position"]["dir"] == 1
    other = ZecBotLive("wilder", live.svc, accounts, live.providers, lambda: [], lambda *a: None, clock=lambda: NOW)
    assert other.state()["status"] == "OFF" and other.CONFIRM == "GO LIVE ZEC WILDER"


def test_the_simulator_handles_hourly_candles():
    bars = wave(800, 800 * rules.H1, rules.H1, period=20.0)
    r = sim.simulate(bars, (), bars[300]["t"], sig=rules.to_sig(*rules.ema_crossover(bars)), tf=rules.H1, shorts=False)
    assert r["times"][1] - r["times"][0] == rules.H1 and r["metrics"]["trades"] > 0
    assert all(t["side"] == "long" for t in r["trades"])


def test_backtests_and_public_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core import api_macdtrio
    app = FastAPI()
    app.include_router(api_macdtrio.router)
    client = TestClient(app)
    bt = backtests()
    assert set(rules.RULES) <= set(bt) and {"PORTFOLIO", "BUY_HOLD"} <= set(bt)
    for k in rules.RULES:
        assert bt[k]["monthly"] and bt[k]["weekly"] and "long-only" in bt[k]["costs"]
    want = {"emarsi": (4381.41, 47), "momentum": (4079.93, 12), "emacross": (3023.56, 156), "macross": (2951.76, 70),
            "scalpslayer": (2729.02, 8), "wilder": (2092.90, 35)}
    for k, (net, trades) in want.items():                            # the spot reproduction of the study table
        assert bt[k]["spot_reproduction"]["net_pct"] == pytest.approx(net, abs=0.01) and bt[k]["spot_reproduction"]["trades"] == trades
    r = client.get("/api/public/zec-bots")
    assert r.status_code == 200 and set(rules.RULES) <= set(r.json()["bots"])
    assert client.post("/api/public/zec-bots").status_code in (401, 403, 405)


def test_the_testnet_gate_waiver_unlocks_mainnet_only_when_the_operator_sets_it(tmp_path, monkeypatch):
    ex = FakeEx(mark=2.0, balance=1000.0)
    prov = FakeProviders(ex, switch=True)
    accounts = LinkedAccounts(tmp_path / "accounts.json", FakeVault(), prov)
    live = ZecBotLive("emarsi", ZecMarket(tmp_path / "zec.db", client=FakeClient([])), accounts, prov, lambda: [],
                      lambda *a: None, clock=lambda: NOW)
    live.decide = lambda symbol, held: (1, None)
    acct = run(accounts.add("real", "binance", "mainnet", "K-m", "S-m"))["id"]
    monkeypatch.delenv("LIVE_TESTNET_GATE_WAIVED", raising=False)
    with pytest.raises(LiveError, match="testnet round trip"):
        run(live.arm(acct, 15, 5, "GO LIVE ZEC EMARSI"))
    monkeypatch.setenv("LIVE_TESTNET_GATE_WAIVED", "true")
    assert live.mainnet_gate()["testnet_waived"] is True
    with pytest.raises(LiveError, match="GO LIVE ZEC EMARSI"):                 # the phrase still applies
        run(live.arm(acct, 15, 5, "go"))
    assert run(live.arm(acct, 15, 5, "GO LIVE ZEC EMARSI"))["status"] == "ARMED"
