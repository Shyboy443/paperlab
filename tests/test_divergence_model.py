import math
import random

import pytest

from app.divergence.model import BASE, HOUR, aggregate, clean_bars, indicators, pivots, analyze, generate_signals
from app.divergence.backtest import backtest, split_backtest


def candle(t, o=100, h=101, l=99, c=100, v=10):
    return dict(t=t, o=o, h=h, l=l, c=c, v=v)


def signal(t=BASE, side="long", stop=95, ident="fixture"):
    return dict(id=ident, symbol="BTCUSDT", side=side, divergence_tf="1h", trigger_tf="15m",
                confirmed_at=t, divergence_at=0, entry_reference=100, stop=stop, target=110)


def test_aggregation_rejects_partial_and_gappy_candles():
    rows = [candle(i * BASE) for i in range(12)]
    assert len(aggregate(rows, "1h")) == 3
    assert len(aggregate(rows[:-1], HOUR)) == 2
    assert [b["t"] for b in aggregate(rows[:2] + rows[3:], HOUR)] == [HOUR, 2 * HOUR]
    assert len(clean_bars(rows, 2 * HOUR - 1)) == 7


def test_wilder_rsi_atr_and_gap_reset():
    rows = [candle(i * BASE, 100+i, 102+i, 99+i, 101+i) for i in range(40)]
    rsi, atr, segments = indicators(rows, BASE)
    assert all(v is None for v in rsi[:14])
    assert rsi[14] == 100
    assert atr[13] == 3
    assert len(set(segments)) == 1
    flat = [candle(i * BASE) for i in range(20)]
    assert indicators(flat, BASE)[0][-1] == 50
    gap = rows[:20] + rows[21:]
    rr, aa, ss = indicators(gap, BASE)
    assert rr[20] is None and rr[33] is None and rr[34] == 100
    assert ss[19] != ss[20]


def test_swings_are_closes_five_left_three_right_known_three_bars_later():
    closes = [99, 99, 98, 98, 97, 95, 97, 98, 99, 98]
    rows = [candle(i * BASE, c=x, l=min(x, 99) - 2) for i, x in enumerate(closes)]
    assert [p for p in pivots(rows[:8], BASE) if p["kind"] == "low"] == []
    p = [p for p in pivots(rows[:9], BASE) if p["kind"] == "low"][0]
    assert p["t"] == 5 * BASE and p["known_at"] == 9 * BASE
    assert p["price"] == 95 and p["wick"] == 93                         # the swing is the CLOSE; the wick is kept for the stop
    wick_only = [candle(i * BASE, l=(80 if i == 5 else 99)) for i in range(10)]   # a long wick, closes all 100
    assert [p for p in pivots(wick_only, BASE) if p["kind"] == "low"] == []
    equal = [candle(i * BASE, c=x) for i, x in enumerate([99, 99, 99, 99, 99, 95, 95, 98, 99, 99])]
    assert [p for p in pivots(equal, BASE) if p["kind"] == "low"] == []


def swing(kind, i, price, rsi, segment=0):
    return dict(kind=kind, index=i, t=i * HOUR, price=price, wick=price, rsi=rsi, rsi_index=i, rsi_t=i * HOUR,
                known_at=(i + 3) * HOUR, segment=segment)


def flat_bars(n, h=110.0, l=90.0):
    return [candle(i * HOUR, 100, h, l, 100) for i in range(n)]


@pytest.mark.parametrize("kind,a,b,want", [
    ("low", (10, 95, 30), (20, 93, 36), "regular_bullish"),      # price LL, RSI HL
    ("high", (10, 105, 72), (20, 107, 64), "regular_bearish"),   # price HH, RSI LH
    ("low", (10, 95, 44), (20, 97, 38), "hidden_bullish"),       # price HL, RSI LL
    ("high", (10, 105, 58), (20, 103, 66), "hidden_bearish"),    # price LH, RSI HH
    ("low", (10, 95, 30), (20, 93, 28), None),                   # both lower: no divergence
    ("high", (10, 105, 72), (20, 107, 71), None),                # RSI gap below 3 points
    ("high", (10, 105, 55), (20, 107, 50), None),                # regular bearish needs RSI >= 60 at a swing
    ("low", (10, 95, 45), (20, 93, 50), None),                   # regular bullish needs RSI <= 40 at a swing
    ("low", (10, 95, 30), (13, 93, 36), None),                   # swings too close
])
def test_four_divergence_types_and_their_limits(kind, a, b, want):
    from app.divergence.model import classify
    rsi = [50.0] * 40
    bars = flat_bars(40, h=102 if kind == "high" else 110, l=98 if kind == "low" else 90)
    assert classify(swing(kind, *a), swing(kind, *b), bars, rsi) == want


def test_lines_must_be_clean_on_price_and_rsi():
    from app.divergence.model import classify
    a, b = swing("high", 10, 105, 72), swing("high", 20, 107, 64)
    bars, rsi = flat_bars(40, h=102, l=96), [50.0] * 40
    assert classify(a, b, bars, rsi) == "regular_bearish"
    spiked = [dict(x) for x in bars]
    spiked[15]["c"] = 108                                         # a CLOSE pokes above the line between the swings
    assert classify(a, b, spiked, rsi) is None
    rsi2 = list(rsi)
    rsi2[15] = 75                                                 # RSI pokes above its line
    assert classify(a, b, bars, rsi2) is None


def test_reported_btc_divergence_uses_closes_and_the_longest_clean_pair():
    """BTC 1h, 7-8 Oct 2026 (operator's chart): close swing lows 82,978.1 (RSI 21.1), 83,118.8 (32.4), 82,612.1 (28.7),
    80,718.1 (24.2). Only the first and the last form a clean regular bullish divergence; consecutive swings do not."""
    from app.divergence.model import choose_pairs, classify
    a, mid1, mid2, b = (swing("low", 10, 82978.1, 21.1), swing("low", 17, 83118.8, 32.4),
                        swing("low", 24, 82612.1, 28.7), swing("low", 37, 80718.1, 24.2))
    bars = flat_bars(50, l=79000)
    for j in range(11, 37):
        bars[j]["c"] = 83500.0                                        # closes between the swings sit above the line
    for x in (a, mid1, mid2, b):
        bars[x["index"]]["c"] = x["price"]
    rsi = [50.0] * 50
    for x in (a, mid1, mid2, b):
        rsi[x["index"]] = x["rsi"]
    assert classify(mid2, b, bars, rsi, 591.5) is None                # consecutive pair: RSI also lower -> none
    assert choose_pairs([a, mid1, mid2], b, bars, rsi, 591.5) == {"regular_bullish": a}


def test_rsi_is_read_on_the_swing_candle_itself():
    rows = [candle(i * HOUR, 100, 106, 99, 100 + (5 if i == 30 else 0) + (i % 3) * 0.1) for i in range(60)]
    rsi = [None] * 14 + [float(40 + (i % 7)) for i in range(14, 60)]
    p = [p for p in pivots(rows, HOUR, rsi) if p["kind"] == "high" and p["index"] == 30][0]
    assert p["rsi_index"] == 30 and p["rsi"] == rsi[30] and p["known_at"] == rows[33]["t"] + HOUR


def test_major_levels_cluster_4h_swing_closes_of_both_sides_known_in_time():
    """BTC 4h closes from Aug-Sep 2026: highs 80,457.6 / 80,273.6 / 79,991.0 / 80,301.1 and a low 80,286.3 form one
    support band; the 10-08 low 80,990.7 is not known yet when the 10-07 swing forms."""
    from app.divergence.model import major_levels
    pts = [dict(kind=k, t=i * HOUR, price=p, known_at=i * HOUR) for i, (k, p) in
           enumerate([("high", 80457.6), ("high", 80273.6), ("high", 79991.0), ("high", 80301.1), ("low", 80286.3),
                      ("high", 81722.9), ("high", 85000.0)])]
    pts.append(dict(kind="low", t=100 * HOUR, price=80990.7, known_at=10 ** 15))
    levels = major_levels(pts, 397.6, as_of=50 * HOUR)
    assert len(levels) == 1 and len(levels[0]["touches"]) == 5
    assert levels[0]["low"] == 79991.0 and levels[0]["high"] == 80457.6
    assert {t["kind"] for t in levels[0]["touches"]} == {"high", "low"}     # old resistance counts as support
    assert major_levels(pts, 150.0, as_of=50 * HOUR)[0]["high"] == 80301.1   # a narrower ATR splits the band


def test_stop_first_with_costs_and_accounting_identity():
    rows = [candle(0), candle(BASE, h=115, l=94)]
    out = backtest(rows, [signal()], [])
    trade = out["trades"][0]
    assert trade["entry_t"] == BASE
    assert trade["reason"] == "stop_and_target_same_bar"
    assert trade["entry"] == pytest.approx(100.02)
    assert trade["exit"] == pytest.approx(95 * .9998)
    assert trade["net"] == pytest.approx(trade["gross"] - trade["fees"] - trade["slippage"] + trade["funding"])
    assert out["metrics"]["end_equity"] == pytest.approx(1000 + trade["net"])
    assert out["metrics"]["funding_data_available"] is False


def test_funding_sign_and_no_same_timestamp_entry_collection():
    rows = [candle(i * BASE) for i in range(5)]
    funding = [(BASE, .001, 100), (2 * BASE, .001, 101), (3 * BASE, -.0005, 99)]
    long = backtest(rows, [signal()], funding)
    short = backtest(rows, [signal(side="short", stop=105)], funding)
    assert long["funding_events"] == 2
    qty = long["trades"][0]["quantity"]
    assert long["metrics"]["funding"] == pytest.approx(qty * (-.001 * 101 + .0005 * 99))
    assert short["metrics"]["funding"] > 0
    assert long["metrics"]["end_equity"] == pytest.approx(1000 + sum(t["net"] for t in long["trades"]))


def test_adverse_open_gap_fill_and_missing_entry_cancelled():
    rows = [candle(0), candle(BASE), candle(2 * BASE, 90, 92, 89, 91)]
    out = backtest(rows, [signal()], [])
    assert out["trades"][0]["raw_exit"] == 90
    missing = backtest([rows[0], rows[2]], [signal()], [])
    assert not missing["trades"]
    assert "Missing" in missing["skipped"][0]["reason"]


def test_no_position_carry_or_pre_split_signal_in_oos():
    rows = [candle(i * BASE) for i in range(20)]
    result = split_backtest(rows, [signal(), signal(16 * BASE, ident="oos")], [])
    assert len(result["in_sample"]["trades"]) == len(result["out_of_sample"]["trades"]) == 1
    assert result["in_sample"]["trades"][0]["exit_t"] == 14 * BASE
    assert result["out_of_sample"]["trades"][0]["id"] == "oos"
    assert result["out_of_sample"]["metrics"]["start_equity"] == 1000


def test_equity_notional_capped_and_partial_month_not_diluted():
    from datetime import datetime, timezone
    t = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [candle(t), candle(t+BASE, h=100.05, l=99.995, c=100.01)]
    out = backtest(rows, [signal(t+BASE, stop=99.99)], [], fee_per_side=0, slippage_per_side=0)
    assert out["trades"][0]["quantity"] * out["trades"][0]["entry"] <= 1000
    assert len(out["monthly"]) == 1
    assert out["monthly"][0]["month"] == "2026-01"


def random_market(n=9000):
    rng = random.Random(12)
    close = 100
    rows = []
    for i in range(n):
        opened = close
        close = max(10, opened + rng.gauss(0, .35))
        rows.append(candle(i*BASE, opened, max(opened, close)+rng.random()*.35,
                           min(opened, close)-rng.random()*.35, close, 5 + rng.random() * 10))
    return rows


def test_prefix_invariance_confirmed_signals_never_repaint():
    from app.divergence.model import _study
    rows = random_market()
    setups, whole, _ = _study(clean_bars(rows), "TESTUSDT")
    # A random walk rarely passes every DIV-SR-3 rule, so the leakage check runs on every FINISHED setup (whatever its
    # status, checks and hypothetical levels) and must not pass vacuously.
    assert len(setups) > 20
    for count in (4000, 6000, 8000):
        prefix, prefix_signals, _ = _study(clean_bars(rows[:count]), "TESTUSDT")
        done = lambda rows_: [x for x in rows_ if x["expires_at"] <= count * BASE]  # noqa: E731
        assert done(prefix) == done(setups)
        assert prefix_signals == [s for s in whole if s["confirmed_at"] <= count*BASE]
    for s in whole:
        assert s["confirmed_at"] > s["divergence_at"]
        assert s["pivot2"]["known_at"] == s["divergence_at"]
        assert s["divergence_type"] in ("regular_bullish", "regular_bearish", "hidden_bullish", "hidden_bearish")
        assert s["side"] == ("long" if s["divergence_type"].endswith("bullish") else "short")
        if s["regular"]:
            assert len(s["zone"]["touches"]) >= 2
            assert all(t["known_at"] <= s["pivot1"]["time"] and t["timeframe"] == "4h" for t in s["zone"]["touches"])
        assert all(c["passed"] or c.get("warning") for c in s["checks"]), s["checks"]
        assert s["swing_gap_atr"] >= 0.25
        assert abs(s["entry_reference"] - s["pivot2"]["price"]) <= 2.0 * s["atr"]
        assert s["volume_ratio"] >= 1.0
        assert s["pivot2"]["rsi_time"] <= s["divergence_at"]
        assert s["target"] == pytest.approx(s["entry_reference"] + (2 if s["side"] == "long" else -2) * abs(s["entry_reference"] - s["stop"]))


def test_analyze_excludes_open_candles_even_if_future_data_supplied():
    rows = random_market(1000)
    result = analyze(rows, "TESTUSDT", 700*BASE + BASE//2)
    prefix = analyze(rows[:700], "TESTUSDT", 700*BASE + BASE//2)
    assert result == prefix


def test_reported_sol_double_bottom_is_rejected_as_equal_low():
    """SOL 1h, 5-6 Oct 2026: swing lows 118.81 (RSI 35.05) and 118.73 (RSI 40.50), ATR 0.7356 at swing 2."""
    from app.divergence.model import pair_reason, prefilter_checks
    a, b = swing("low", 10, 118.81, 35.05), swing("low", 27, 118.73, 40.50)
    bars = flat_bars(60, l=119.5)
    for j in range(60):
        bars[j]["c"] = 119.5
    kind, why = pair_reason(a, b, bars, [50.0] * 60, 0.7356)
    assert kind is None and why.startswith("Equal low")                    # not a divergence: never recorded
    checks, reason, _ = prefilter_checks("regular_bullish", a, b, bars, [None] * 60, 0.7356, [], None, "1h")
    assert checks[0]["passed"] is False and reason.startswith("Equal low")


@pytest.mark.parametrize("kind,p1,p2,gap_ok", [
    ("hidden_bullish", 100.0, 100.2, False), ("hidden_bullish", 100.0, 100.3, True),
    ("hidden_bearish", 100.0, 99.8, False), ("regular_bearish", 100.0, 100.25, True),
])
def test_swing_gap_applies_to_all_four_types(kind, p1, p2, gap_ok):
    from app.divergence.model import prefilter_checks
    side = "high" if kind.endswith("bearish") else "low"
    checks, _, _ = prefilter_checks(kind, swing(side, 10, p1, 50), swing(side, 20, p2, 50), flat_bars(40), [None] * 40, 1.0, [], None, "1h")
    assert checks[0]["passed"] is gap_ok


def test_regular_needs_a_prior_trend_at_swing_one():
    from app.divergence.model import prefilter_checks
    bars = flat_bars(40)
    a, b = swing("low", 20, 95, 30), swing("low", 30, 93, 36)
    falling = [None] * 5 + [110.0 - 0.1 * i for i in range(35)]          # EMA falling, close 100 below it
    checks, reason, _ = prefilter_checks("regular_bullish", a, b, bars, falling, 1.0, [], None, "1h")
    assert checks[1]["passed"] is True
    rising = [None] * 5 + [90.0 + 0.1 * i for i in range(35)]
    checks, reason, _ = prefilter_checks("regular_bullish", a, b, bars, rising, 1.0, [], None, "1h")
    assert checks[1]["passed"] is False and reason.startswith("No trend to reverse")


def test_support_check_uses_the_nearest_major_level():
    from app.divergence.model import prefilter_checks
    a, b = swing("low", 50, 82978.1, 21.1), swing("low", 77, 80718.1, 24.2)
    level = dict(price=80261.9, low=79991.0, high=80457.6,
                 touches=[dict(time=0, price=80262.0, kind="high", known_at=0, timeframe="4h")] * 5)
    falling = [None] * 30 + [90000.0 - 50 * i for i in range(70)]
    checks, reason, zone = prefilter_checks("regular_bullish", a, b, flat_bars(100), falling, 591.5, [level], 397.6, "1h")
    sr = next(c for c in checks if c["rule"].startswith("At a major 4h"))
    assert sr["passed"] and zone["price"] == 80261.9 and zone["timeframe"] == "4h"
    checks, reason, _ = prefilter_checks("regular_bullish", a, b, flat_bars(100), falling, 591.5, [level], 200.0, "1h")
    assert reason.startswith("Away from support")                         # 260 above the band, tolerance 200


def test_no_chase_rules():
    from app.divergence.model import chase_checks
    checks, reason = chase_checks(True, 101.0, 105.0, 100.0, 1.0, [])
    assert all(c["passed"] for c in checks) and reason is None
    checks, reason = chase_checks(True, 101.6, 105.0, 100.0, 1.0, [])     # 1.6 ATR: inside the 2 ATR limit (DIV-SR-7)
    assert checks[0]["passed"] is True and reason is None
    checks, reason = chase_checks(True, 102.1, 106.0, 100.0, 1.0, [])     # 2.1 ATR above swing 2
    assert checks[0]["passed"] is False and reason.startswith("Chasing: entry 2.10 ATR")
    checks, reason = chase_checks(True, 101.0, 105.0, 100.0, 1.0, [dict(price=103.0, t=0)])   # range top in the way
    assert checks[1]["passed"] is False and "103" in reason
    checks, reason = chase_checks(False, 99.0, 95.0, 100.0, 1.0, [dict(price=96.0, t=0)])     # short: swing low in the way
    assert checks[1]["passed"] is False


def test_volume_confirmation_counts_buying_vs_selling_since_swing_two():
    """BTC 15m, 8 Oct 2026 18:00-22:30 after the 80,718 swing-2 close: the break candle (22:30, volume 591) was below the
    20-candle mean that still held the crash candles, but buying volume since the low was 2.99x selling volume."""
    from app.divergence.model import _confirm
    vols = [(80718.1, 80767.4, 1914), (80767.4, 81397.7, 5682), (81397.7, 81482.5, 3690), (81482.5, 81466.9, 1228),
            (81466.9, 81460.0, 2018), (81460.0, 81647.8, 1588), (81647.8, 81705.9, 1872), (81705.9, 81733.0, 989),
            (81733.0, 81833.8, 949), (81833.8, 81717.4, 1026), (81717.4, 81778.2, 971), (81778.2, 81764.6, 646),
            (81764.6, 81718.0, 729), (81718.0, 81640.5, 566), (81640.5, 81665.7, 650), (81665.7, 81669.5, 469),
            (81669.5, 81793.3, 557), (81793.3, 81746.6, 443), (81746.6, 81872.6, 591)]
    t0 = 18 * HOUR
    lower = [dict(t=t0 + k * BASE, o=o, h=max(o, c) + 1, l=min(o, c) - 1, c=c, v=v) for k, (o, c, v) in enumerate(vols)]
    lp = [dict(kind="high", index=8, t=lower[8]["t"], price=81833.8, known_at=lower[10]["t"] + BASE)]
    known = lower[11]["t"] + BASE
    setup = dict(pivot2=dict(time=17 * HOUR), trigger_tf="15m", expires_at=known + 6 * HOUR)
    checks = []
    ema = [81858.4] * len(lower)                                       # the real 15m EMA50 at the 22:30 break
    status, reason = _confirm(setup, True, 80285.7, known, lower, [b["t"] + BASE for b in lower], lp,
                              [p["known_at"] for p in lp], BASE, checks, HOUR, ema)
    assert status == "CONFIRMED", reason
    assert setup["trigger_candle"]["volume_with"] == pytest.approx(19922.0)
    assert setup["trigger_candle"]["volume_against"] == pytest.approx(6656.0)
    assert setup["volume_ratio"] == pytest.approx(19922 / 6656)


def test_backtest_report_replays_every_confirmed_signal_at_two_r():
    from app.divergence.inspect import backtest_report
    from app.divergence.model import generate_signals
    rows = random_market(20000)
    rep = backtest_report(rows, [], "TESTUSDT", 200)
    assert rep["status"] == "READY" and rep["reward_risk"] == 2.0
    assert len(rep["trades"]) + len(rep["skipped"]) == len(generate_signals(rows, "TESTUSDT"))
    for t in rep["trades"]:
        o = t["outcome"]
        long = t["side"] == "long"
        assert o["target"] == pytest.approx(o["entry"] + (2 if long else -2) * abs(o["entry"] - o["stop"]))
        assert o["entry_t"] >= t["confirmed_at"]
    m = rep["summary"]
    assert m["trades"] == len(rep["trades"]) and (m["total_r"] == pytest.approx(sum(t["outcome"]["r"] for t in rep["trades"])))


def test_stop_goes_beyond_the_previous_swing_extreme_for_each_type():
    """Operator 2026-10-11: a regular bullish stop belongs under the lowest wick of the whole pattern (a spike near swing
    1 can be lower than swing 2's own wick); a hidden one under swing 2's own swing low."""
    from app.divergence.model import stop_basis
    bars = flat_bars(40, h=101, l=99)
    bars[11]["l"] = 94.0                                                  # long wick right after swing 1
    bars[20]["l"] = 96.5                                                  # swing 2's own wick
    bars[30]["l"] = 90.0                                                  # after swing 2's window: never used
    a, b = swing("low", 10, 97.0, 30), swing("low", 20, 96.8, 36)
    reg = stop_basis("regular_bullish", a, b, bars)
    assert reg["price"] == 94.0 and reg["time"] == bars[11]["t"]
    hid = stop_basis("hidden_bullish", a, b, bars)
    assert hid["price"] == 96.5                                           # bars 15..23 only
    bars[18]["h"] = 108.0
    assert stop_basis("regular_bearish", swing("high", 10, 103, 70), swing("high", 20, 104, 64), bars)["price"] == 108.0


def test_the_lower_timeframe_must_trend_the_trades_way_at_the_break(monkeypatch):
    from app.divergence import model as MM
    monkeypatch.setattr(MM, "LTF_TREND_CHECK", True)                     # a warning only since DIV-SR-9
    """BTC 1h short, 12 Jun 2026 (operator: "I don't see the 15m was a downtrend"): the 15m break closed at 63,784.6,
    ABOVE the 15m EMA50 63,628.5 -- the short is refused."""
    from app.divergence.model import _confirm
    t0 = 10 * HOUR
    closes = [63900, 63850, 63800, 63950, 63990, 63900, 63870, 63850, 63784.6]
    lower = [dict(t=t0 + k * BASE, o=c + 20, h=c + 40, l=c - 40, c=c, v=100.0 if k < 8 else 300.0) for k, c in enumerate(closes)]
    lower[3]["o"] = 63900                                                # an up candle: the swing high the short waits under
    lp = [dict(kind="low", index=2, t=lower[2]["t"], price=63800.0, known_at=lower[4]["t"] + BASE)]
    known = lower[5]["t"] + BASE
    base = dict(pivot2=dict(time=t0 - HOUR + 3 * HOUR - 3 * HOUR), trigger_tf="15m", expires_at=known + 6 * HOUR)
    for ema_value, want in ((63628.5, "INVALID"), (63900.0, "CONFIRMED")):
        setup, checks = dict(base, pivot2=dict(time=t0 - HOUR)), []
        status, reason = _confirm(setup, False, 64200.0, known, lower, [b["t"] + BASE for b in lower], lp,
                                  [p["known_at"] for p in lp], BASE, checks, HOUR, [ema_value] * len(lower))
        assert status == want, (ema_value, reason)
        if want == "INVALID":
            assert "15m trend against the trade" in reason


def test_no_long_while_a_bearish_divergence_is_active_at_the_top():
    """ETH 4h, Aug 2025 (operator): a hidden bullish long right after a top made on weaker RSI is refused until price
    closes back above that top."""
    from app.divergence.model import opposing_divergence
    bars = flat_bars(40)
    for j, c in enumerate([4000.0] * 40):
        bars[j]["c"] = c
    minor = [dict(kind="high", index=10, t=10 * HOUR, price=4060.0, rsi=77.9, known_at=12 * HOUR),
             dict(kind="high", index=20, t=20 * HOUR, price=4774.9, rsi=77.7, known_at=22 * HOUR)]
    assert opposing_divergence(True, bars, [50.0] * 40, minor, 30, 31 * HOUR).startswith("bearish divergence")
    bars[25]["c"] = 4800.0                                               # a close above the top cancels it
    assert opposing_divergence(True, bars, [50.0] * 40, minor, 30, 31 * HOUR) is None
    minor[0]["rsi"] = 70.0                                               # RSI confirmed the new high: no divergence
    bars[25]["c"] = 4000.0
    assert opposing_divergence(True, bars, [50.0] * 40, minor, 30, 31 * HOUR) is None
