"""Renata V12 research engine: fills, stops, costs, funding, portfolio limits and no-lookahead features. No network."""
from __future__ import annotations

import numpy as np
import pytest

from app.lab12 import engine as E
from app.lab12 import families as F


def window(n_bars: int = 400, price=100.0, syms=("BTCUSDT", "ADAUSDT")) -> E.Window:
    w = E.Window("T", start=0, a=0, end=n_bars * E.BAR)
    for s in syms:
        bars = np.full((n_bars, 5), price, np.float32)
        bars[:, 4] = 1000.0
        w.coins[s] = E.Coin(s, np.full(n_bars * 5, price, np.float32), bars, np.zeros((0, 2)))
    return w


COST = lambda s: 2 * (E.TAKER + E.slip(s))  # noqa: E731


def test_entry_is_the_next_minute_open_and_time_exit_pays_taker():
    w = window()
    c = w.coins["ADAUSDT"]
    c.open1m[10 * 5 + 1] = 101.0                          # the minute after the decision
    c.bars[10:22, 3] = 103.0
    ret, cost, fund, exit_t, stopped = E.trade(w, "ADAUSDT", 10, 1.0, 0.05, 12)
    assert not stopped and exit_t == 22 and cost == pytest.approx(COST("ADAUSDT"))
    assert ret == pytest.approx(103.0 / 101.0 - 1 - COST("ADAUSDT"))


def test_stop_fills_at_the_stop_or_at_a_gap_open():
    w = window()
    c = w.coins["BTCUSDT"]
    c.bars[13, 2] = 97.0                                   # a wick through a 2% stop
    ret, _, _, exit_t, stopped = E.trade(w, "BTCUSDT", 10, 1.0, 0.02, 12)
    assert stopped and exit_t == 14 and ret == pytest.approx(-0.02 - COST("BTCUSDT"))
    c.bars[13] = (95.0, 95.0, 94.0, 95.0, 1000.0)          # the bar OPENS below the stop: filled at the open
    ret, *_ = E.trade(w, "BTCUSDT", 10, 1.0, 0.02, 12)
    assert ret == pytest.approx(95.0 / 100.0 - 1 - COST("BTCUSDT"))


def test_short_stop_and_funding_sign():
    w = window()
    c = w.coins["ADAUSDT"]
    c.funding = np.array([[11 * E.BAR, 0.001], [30 * E.BAR, 0.001]])   # one settlement inside the hold
    ret, _, fund, _, _ = E.trade(w, "ADAUSDT", 10, -1.0, 0.05, 12)
    assert fund == pytest.approx(0.001)                    # a short RECEIVES a positive rate
    ret_l, _, fund_l, _, _ = E.trade(w, "ADAUSDT", 10, 1.0, 0.05, 12)
    assert fund_l == pytest.approx(-0.001)


def test_missing_data_is_no_trade():
    w = window()
    w.coins["ADAUSDT"].bars[15, 3] = np.nan
    assert E.trade(w, "ADAUSDT", 10, 1.0, 0.05, 12) is None


def test_portfolio_capacity_and_one_trade_per_coin():
    w = window()
    ev = E.Events(np.array([10, 10, 12]), np.array([0, 1, 0]), np.array([1.0, 1.0, 1.0]), np.full(3, 0.05),
                  np.array([12, 12, 12]), np.array([2.0, 1.0, 3.0]))
    tr = E.simulate(w, ev, ["BTCUSDT", "ADAUSDT"], max_pos=1)
    assert list(tr["coin"]) == [0]                         # the best-scored at t=10 only; the book is full until 22
    tr = E.simulate(w, ev, ["BTCUSDT", "ADAUSDT"], max_pos=6)
    assert sorted(tr["coin"]) == [0, 1]                    # coin 0 again at 12 is refused: already held


def test_breakout_levels_exclude_the_current_bar():
    x = np.array([[1.0, 2.0, 3.0, 10.0, 4.0]])
    top = F._prior_extremes(x, 2, np.nanmax)
    assert np.isnan(top[0, 1]) and top[0, 2] == 2.0 and top[0, 3] == 3.0 and top[0, 4] == 10.0


def test_regime_uses_only_closed_hours():
    w = window(n_bars=12 * 400, syms=("BTCUSDT",))
    b = w.coins["BTCUSDT"].bars
    b[:, :4] = np.repeat(np.linspace(100, 200, len(b))[:, None], 4, axis=1)      # a steady rise
    reg = E.btc_regime_hourly(w)
    assert reg[0] == 0 and reg[-1] == 1
    b[-12:, :4] = 1.0                                       # crash in the LAST hour: hour i's regime cannot see it
    assert E.btc_regime_hourly(w)[-1] == 1


def test_configs_are_the_preregistered_grid():
    cs = F.configs()
    assert len(cs) == 86 and len({c["name"] for c in cs}) == 86
    assert sum(1 for c in cs if c.get("regime")) == 42          # 44 rules; the 2 benchmarks get no overlay
