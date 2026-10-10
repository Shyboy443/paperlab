"""V11.5 study simulator: fills, costs, the ladder and the variant filters on synthetic paths. No network."""
from __future__ import annotations

import numpy as np
import pytest

from scripts.v115_reversal_study import COL, H, MAKER, SLIP, TAKER, select, simulate

COST = TAKER + SLIP


def path(rows):
    """rows: list of (open, high, low, close) returns relative to entry; padded flat to H minutes."""
    p = np.zeros((1, H, 4), np.float32)
    for i, r in enumerate(rows):
        p[0, i] = r
    last = rows[-1][3]
    p[0, len(rows):] = (last, last, last, last)
    return p


def test_time_exit_pays_taker_both_ways():
    r = simulate(path([(0, 0.004, -0.002, 0.003)] * 3), np.array([1.0]), np.array([0.01]), hold=3, target="time")
    assert r[0] == pytest.approx((0.003 - COST) / 0.01 - COST / 0.01)


def test_stop_first_when_one_minute_reaches_both():
    r = simulate(path([(0, 0.03, -0.02, 0.0)]), np.array([1.0]), np.array([0.01]), hold=5, target="1R")
    assert r[0] == pytest.approx((-0.01 - COST) / 0.01 - COST / 0.01)


def test_a_gap_through_the_stop_fills_at_the_open():
    r = simulate(path([(0, 0.001, -0.001, 0.0), (-0.02, -0.019, -0.025, -0.02)]), np.array([1.0]), np.array([0.01]),
                 hold=5, target="time")
    assert r[0] == pytest.approx((-0.02 - COST) / 0.01 - COST / 0.01)


def test_short_target_is_a_maker_fill():
    r = simulate(path([(0, 0.001, -0.012, -0.011)]), np.array([-1.0]), np.array([0.01]), hold=5, target="1R")
    assert r[0] == pytest.approx((0.01 - MAKER) / 0.01 - COST / 0.01)


def test_ladder_partials_and_moved_stop():
    # +0.5R, +1R, then back to entry: 25% at 0.5R, 50% at 1R, the last 25% stopped at TP1 (the stop after TP2)
    rows = [(0, 0.005, -0.001, 0.005), (0.005, 0.01, 0.004, 0.009), (0.009, 0.009, 0.0, 0.0)]
    r = simulate(path(rows), np.array([1.0]), np.array([0.01]), hold=10, target="ladder")
    want = (0.25 * (0.005 - MAKER) + 0.5 * (0.01 - MAKER) + 0.25 * (0.005 - COST)) / 0.01 - COST / 0.01
    assert r[0] == pytest.approx(want, abs=1e-6)


def meta_row(hour, coin, side, rank, z, **kw):
    m = np.zeros(len(COL))
    m[COL["hour"]], m[COL["coin"]], m[COL["side"]], m[COL["rank"]], m[COL["z"]] = hour, coin, side, rank, z
    m[COL["atr_pct"]] = 0.01
    for k, v in kw.items():
        m[COL[k]] = v
    return m


def test_select_baseline_takes_two_per_side_and_filters_work():
    meta = np.stack([meta_row(1, 0, 1, 0, -3.0, q15=1), meta_row(1, 1, 1, 1, -1.2, q15=-1), meta_row(1, 2, 1, 2, -0.5),
                     meta_row(1, 3, -1, 0, 2.6, q15=-1), meta_row(1, 4, -1, 1, 0.8)])
    assert sorted(select(meta, {"per_side": 2})) == [0, 1, 3, 4]
    assert sorted(select(meta, {"per_side": 2, "z_min": 1.0})) == [0, 1, 3]            # 0.5 / 0.8 ATR are not extremes
    assert sorted(select(meta, {"per_side": 2, "z_min": 1.0, "confirm": True})) == [0, 3]
    assert list(select(meta, {"per_side": 1, "z_min": 1.0})) in ([0, 3], [3, 0])         # the strongest per side


# -- limit-entry study ----------------------------------------------------------------------------------------------------
from scripts.v115_limit_study import fills, simulate_limit  # noqa: E402


def test_a_limit_needs_a_trade_through_not_a_touch():
    touch = path([(0, 0.001, -0.005, 0.0)])                      # the low only touches a limit 0.5% below
    through = path([(0, 0.001, -0.0051, 0.0)])
    assert fills(touch, np.array([1.0]), np.array([-0.005]), 15)[0] == -1
    assert fills(through, np.array([1.0]), np.array([-0.005]), 15)[0] == 0
    assert fills(path([(0, 0.0051, -0.001, 0.0)]), np.array([-1.0]), np.array([0.005]), 15)[0] == 0   # a short above


def test_unfilled_orders_are_not_trades_and_a_fill_pays_the_maker_fee():
    flat = path([(0, 0.001, -0.001, 0.0)])
    r, placed = simulate_limit(flat, np.array([1.0]), np.array([0.01]), offset=0.5, ttl=15, hold=30, target="time")
    assert placed == 1 and len(r) == 0                            # the limit 0.5 ATR below was never reached
    dip = path([(0, 0.0, -0.006, -0.005)] + [(-0.005, 0.0, -0.005, 0.0)] * 40)
    r, _ = simulate_limit(dip, np.array([1.0]), np.array([0.01]), offset=0.5, ttl=15, hold=30, target="time")
    stop = 0.02                                                    # 2 ATR of 1% (inside the 1.5-8% clamp)
    fill_px = -0.005
    exit_rel = (1 + 0.0) / (1 + fill_px) - 1                       # back to the reference = +0.5% from the fill
    assert r[0] == pytest.approx((exit_rel - (TAKER + SLIP)) / stop - MAKER / stop, rel=1e-4)
