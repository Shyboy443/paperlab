"""No-Wick Retest desk model (operator's Pine script): levels, taps, 1-minute trend, ATR exits. No network."""
import pytest

from app.nowick import model as M

MIN = 60_000


def minutes(prices):
    """1-minute bars from (o, h, l, c) tuples, starting at t=0."""
    return [dict(t=i * MIN, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(prices)]


def trend_up(n, start=100.0, step=0.02):
    out, p = [], start
    for _ in range(n):
        out.append((p, p + 0.05, p - 0.05, p + step))
        p += step
    return out


def test_bullish_candle_without_lower_wick_makes_a_long_level_and_a_retest_trades_it(monkeypatch):
    monkeypatch.setattr(M, "TIMEFRAMES", {"5m": 5 * MIN})
    bars = trend_up(300)                                                # warm-up: a steady 1m uptrend, ATR ready
    base = bars[-1][3]
    # one 5m candle: opens at its low and closes higher -> no lower wick -> LONG level at its low
    bars += [(base, base + 0.3, base, base + 0.2)] + [(base + 0.2, base + 0.4, base + 0.15, base + 0.3)] * 4
    bars += trend_up(5, base + 0.3, 0.02)                                # still rising (trend stays bullish)
    bars += [(base + 0.4, base + 0.45, base - 0.01, base + 0.05)]       # dips through the level: the retest
    bars += trend_up(200, base + 0.05, 0.05)                             # then runs up to the target
    res = M.simulate(minutes(bars), "5m", "TESTUSDT")
    longs = [t for t in res["trades"] if t["side"] == "long" and abs(t["entry"] - base) < 1e-9]
    assert longs, [lv["outcome"] for lv in res["levels"]][-5:]
    t = longs[0]
    assert t["target"] == pytest.approx(t["entry"] + 2 * t["atr"]) and t["stop"] == pytest.approx(t["entry"] - t["atr"])
    assert t["reason"] == "target" and t["r_gross"] == pytest.approx(2.0)


def test_levels_need_an_exact_no_wick_candle_and_only_three_are_kept():
    rows = []
    for k in range(6):                                                    # six 5m candles, alternating no-wick kinds
        o = 100 + k
        if k % 2:
            rows += [(o, o, o - 0.5, o - 0.3)] + [(o - 0.3, o - 0.1, o - 0.6, o - 0.4)] * 4    # bearish, no upper wick
        else:
            rows += [(o, o + 0.5, o, o + 0.3)] + [(o + 0.3, o + 0.6, o + 0.1, o + 0.4)] * 4    # bullish, no lower wick
    res = M.simulate(minutes(rows), "5m", "TESTUSDT")
    assert [lv["dir"] for lv in res["levels"]] == [1, -1, 1, -1, 1, -1]
    assert len(res["open_levels"]) == 3
    assert sum(lv["outcome"].startswith("dropped") for lv in res["levels"]) == 3


def test_costs_make_a_stop_worse_than_minus_one_r():
    r, rg = M._r(1, 100.0, 99.0, 99.0 * (1 - M.STOP_SLIP), "stop")
    assert rg < -1.0 and r < rg
    r, rg = M._r(-1, 100.0, 101.0, 98.0, "target")
    assert rg == pytest.approx(2.0) and r < 2.0
