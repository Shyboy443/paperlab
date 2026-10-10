"""Renata V12-VOL forward test: the live path must reproduce the research path exactly. No network."""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.lab12 import engine as E
from app.lab12 import families as F
from app.lab12 import forward as V

ORIGINAL_COINS = V.COINS
T0 = V.START_MS - 45 * E.DAY
SYMS = ("BTCUSDT", "ADAUSDT", "ETHUSDT")


class Market:
    """Deterministic 5m bars from T0 (a random walk per coin) with a volume spike hour, served like Bybit REST."""

    def __init__(self, n_days: int = 50, spike_ms: int | None = None, seed: int = 3):
        rng = np.random.default_rng(seed)
        n = n_days * 288
        self.bars: dict[str, np.ndarray] = {}
        for k, s in enumerate(SYMS):
            c = 100.0 * (1 + k) * np.exp(np.cumsum(rng.normal(0.0002, 0.002, n)))
            o = np.concatenate([[c[0]], c[:-1]])
            h = np.maximum(o, c) * (1 + rng.uniform(0, 0.001, n))
            lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.001, n))
            b = np.stack([o, h, lo, c, rng.uniform(900, 1100, n)], axis=1)
            if spike_ms is not None and s == "ADAUSDT":
                j = (spike_ms - T0) // E.BAR
                base = b[j, 0]
                for q in range(12):                               # one hour: a 6% thrust closing at its high
                    b[j + q] = (base * (1 + 0.005 * q), base * (1 + 0.005 * (q + 1)), base * (1 + 0.005 * q) * 0.999,
                                base * (1 + 0.005 * (q + 1)), 20_000.0)
                b[j + 12:, :4] *= b[j + 11, 3] / b[j + 12, 0]
            self.bars[s] = b
        self.now = T0

    def klines(self, sym, interval, start, end):
        b = self.bars[sym]
        if interval == "1":
            out = []
            for t in range(start, end, E.MIN):
                j = (t - T0) // E.BAR
                if 0 <= j < len(b) and t + E.MIN <= self.now:
                    out.append([t, b[j, 0] * 1.0001, 0, 0, 0, 0, 0])
            return out
        lo, hi = max(0, (start - T0) // E.BAR), min(len(b), (end - T0) // E.BAR)
        return [[T0 + j * E.BAR, *b[j, :4], 0.0, b[j, 4]] for j in range(lo, hi)]

    def funding(self, sym, a, b):
        return [(t, 0.0001) for t in range(a // (8 * E.HOUR) * 8 * E.HOUR, b, 8 * E.HOUR) if a <= t < b]


@pytest.fixture(autouse=True)
def few_coins(monkeypatch):
    monkeypatch.setattr(V, "COINS", SYMS)


def research_window(m: Market) -> E.Window:
    w = E.Window("R", start=T0, a=T0, end=T0 + 50 * E.DAY)
    for s in SYMS:
        b = m.bars[s].astype(np.float32)
        o1 = np.repeat(b[:, 0], 5)
        o1[1::5] = b[:, 0] * 1.0001                               # the fake 1m feed's entry minute
        w.coins[s] = E.Coin(s, o1, b, np.array(m.funding(s, T0, T0 + 50 * E.DAY), float).reshape(-1, 2))
    return w


def live_bars(m: Market, now: int) -> V.Bars:
    m.now = now
    bars = V.Bars(m.klines, m.funding)
    for s in SYMS:
        bars.refresh(s, now)
    return bars


@pytest.mark.parametrize("book", list(V.BOOKS))
def test_live_signals_equal_the_research_signals(book):
    D = V.START_MS + 5 * E.HOUR
    m = Market(spike_ms=D - E.HOUR)
    cfg = V.book_configs()[book]
    live = V.decide(live_bars(m, D + 3 * E.MIN), D, cfg)
    w = research_window(m)
    p = F.panel(w)
    i = (D - T0) // E.HOUR - 1
    ev = F.vol_expansion(w, dataclasses.replace(p, first=i, last=i + 1), cfg)
    want = sorted((p.syms[int(c)], int(s), round(float(st), 6)) for c, s, st in zip(ev.coin, ev.side, ev.stop))
    assert sorted((x["sym"], x["side"], round(x["stop"], 6)) for x in live) == want
    if book == "VOL-RAW":
        assert ("ADAUSDT", 1) in {(x["sym"], x["side"]) for x in live}       # the spike is a long signal


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("stop", [0.004, 0.012, 0.25])
def test_position_path_equals_engine_trade(side, stop):
    m = Market(seed=11)
    D = V.START_MS + 2 * E.HOUR
    w = research_window(m)
    t = (D - T0) // E.BAR
    want = E.trade(w, "ETHUSDT", t, float(side), stop, 288)
    bars = live_bars(m, D + 30 * E.HOUR)
    pos = {"sym": "ETHUSDT", "D": D, "side": side, "entry": float(w.coins["ETHUSDT"].open1m[t * 5 + 1]), "stop": stop,
           "hold": 288, "checked": 0, "notional": 100.0}
    ex = V.step_position(pos, bars, D + 30 * E.HOUR)
    got = V.settle(pos, ex, m.funding("ETHUSDT", D, ex["exit_ms"] + 1))
    ret, cost, fund, exit_t, stopped = want
    assert got["stopped"] == stopped and got["exit_ms"] == T0 + exit_t * E.BAR
    assert got["ret"] == pytest.approx(ret, abs=2e-6) and got["funding"] == pytest.approx(fund)


def test_position_waits_for_missing_bars_and_never_skips():
    m = Market(seed=5)
    D = V.START_MS
    bars = live_bars(m, D + 2 * E.HOUR)
    pos = {"sym": "BTCUSDT", "D": D, "side": 1, "entry": float(m.bars["BTCUSDT"][(D - T0) // E.BAR, 0]), "stop": 0.25,
           "hold": 288, "checked": 0}
    assert V.step_position(pos, bars, D + 2 * E.HOUR) is None and pos["checked"] == 24
    assert V.step_position(pos, bars, D + 10 * E.HOUR) is None and pos["checked"] == 24     # bars not fetched yet


def test_service_trades_the_spike_once_with_catch_up_and_occupancy(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "freeze_problem", lambda: None)
    D = V.START_MS + 3 * E.HOUR
    m = Market(spike_ms=D - E.HOUR)
    clock = {"now": V.START_MS + 20 * E.HOUR}                     # down for hours: everything is caught up late

    def now():
        m.now = clock["now"]
        return clock["now"]

    svc = V.V12VolForward(tmp_path / "v.db", klines=m.klines, funding=m.funding, tickers=lambda: {}, now=now)
    svc.step()
    st = svc.public_state()
    raw = st["books"]["VOL-RAW"]
    ada = [p for p in raw["open"] + raw["closed"] if p["sym"] == "ADAUSDT" and p["D"] == D]
    assert len(ada) == 1 and ada[0]["side"] == 1 and ada[0]["catch_up"] == 1
    assert ada[0]["entry"] == pytest.approx(m.bars["ADAUSDT"][(D - T0) // E.BAR, 0] * 1.0001)
    assert st["decisions"] == 20 and st["paper_only"] is True
    clock["now"] += 30 * E.HOUR
    svc.step()
    raw = svc.public_state()["books"]["VOL-RAW"]
    done = [p for p in raw["closed"] if p["sym"] == "ADAUSDT" and p["D"] == D]
    assert done and done[0]["exit_ms"] <= D + 24 * E.HOUR
    for book in V.BOOKS:                                          # never two positions in one coin at once
        ps = svc.public_state()["books"][book]
        rows = ps["open"] + ps["closed"]
        for a in rows:
            for b in rows:
                if a is not b and a["sym"] == b["sym"]:
                    assert not (a["D"] < b["D"] < (a["exit_ms"] or 10 ** 15))


def test_frozen_mismatch_keeps_the_books_shut(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "freeze_problem", lambda: "frozen fields differ: code")
    svc = V.V12VolForward(tmp_path / "v.db", klines=lambda *a: [], funding=lambda *a: [], tickers=lambda: {})
    svc.start()
    assert svc.status == "FROZEN_MISMATCH" and svc._thread is None


def test_verdict_rules():
    good = {"trades": 200, "expectancy_r": 0.05, "t_daily": 2.1, "pf": 1.2, "expectancy_r_2x_costs": 0.02,
            "half1": {"expectancy_r": 0.01}, "half2": {"expectancy_r": 0.04}}
    assert V.verdict(good).startswith("PASS")
    assert V.verdict({**good, "half2": {"expectancy_r": -0.01}}) == "FAIL: both halves > 0"
    assert V.verdict({**good, "trades": 149}).startswith("FAIL")


@pytest.mark.parametrize("j,gap", [(0, False), (7, True), (7, False)])
def test_long_stop_on_entry_bar_and_gap_open_equal_engine_trade(j, gap):
    m = Market(seed=11)
    D = V.START_MS + 2 * E.HOUR
    k = (D - T0) // E.BAR + j
    b = m.bars["ETHUSDT"]
    entry = b[k - j, 0] * 1.0001
    if gap:
        b[k, 0] = b[k, 3] = b[k, 1] = entry * 0.97
        b[k, 2] = entry * 0.96
    else:
        b[k, 2] = entry * 0.97                                   # a wick through a 2% stop
    w = research_window(m)
    want = E.trade(w, "ETHUSDT", k - j, 1.0, 0.02, 288)
    pos = {"sym": "ETHUSDT", "D": D, "side": 1, "entry": float(w.coins["ETHUSDT"].open1m[(k - j) * 5 + 1]), "stop": 0.02,
           "hold": 288, "checked": 0, "notional": 100.0}
    ex = V.step_position(pos, live_bars(m, D + 30 * E.HOUR), D + 30 * E.HOUR)
    got = V.settle(pos, ex, m.funding("ETHUSDT", D, ex["exit_ms"] + 1))
    assert want[4] and got["stopped"] and got["exit_ms"] == T0 + want[3] * E.BAR
    assert got["ret"] == pytest.approx(want[0], abs=2e-6)


def test_committed_freeze_matches_the_code(monkeypatch):
    monkeypatch.setattr(V, "COINS", ORIGINAL_COINS)
    assert V.freeze_problem() is None
    assert V.START_MS < V.HALF_MS < V.END_MS and len(V.COINS) == 30 and V.PRIMARY == "VOL"
