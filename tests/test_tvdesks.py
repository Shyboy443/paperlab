"""T3-CCI and CHI desks: indicators, the cash broker (both modes) and the forward paper service (restart safety)."""
import math

import numpy as np
import pytest

from app.tvdesks import broker as B
from app.tvdesks.data import H1, H4, aggregate_4h
from app.tvdesks.indicators import chi_series, t3cci_series
from app.tvdesks.service import TvDeskService

T0 = 1_790_000_000_000 // H4 * H4


def bars4(closes, t0=T0):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append({"t": t0 + i * H4, "o": o, "h": max(o, c) * 1.01, "l": min(o, c) * 0.99, "c": c, "v": 1.0})
    return out


# -- data ---------------------------------------------------------------------------------------------------------------
def test_aggregation_needs_all_four_hours():
    hours = [{"t": T0 + k * H1, "o": 1 + k, "h": 2 + k, "l": 0.5, "c": 1.5 + k, "v": 1} for k in range(8)]
    del hours[6]                                                         # second bucket incomplete
    b = aggregate_4h(hours)
    assert len(b) == 1 and b[0] == {"t": T0, "o": 1.0, "h": 5.0, "l": 0.5, "c": 4.5, "v": 4.0}


# -- indicators -------------------------------------------------------------------------------------------------------------
def test_t3cci_direction_and_zero_keeps_previous():
    closes = [100 + i for i in range(40)] + [140 - 2 * i for i in range(40)]
    s = t3cci_series(closes)
    assert s["pos"][35] == 1 and s["pos"][-1] == -1
    flat = t3cci_series([100.0] * 40)                                    # MAD 0 -> CCI unavailable -> pos stays 0
    assert set(flat["pos"]) == {0.0}


def test_t3cci_smoothing_matches_the_source_recurrence():
    closes = list(np.linspace(100, 120, 30)) + list(np.linspace(120, 90, 30))
    s = t3cci_series(closes)
    cci = s["cci"]
    e, stages = cci, []
    for _ in range(6):
        st = []
        for i, x in enumerate(e):
            prev = st[i - 1] if i and math.isfinite(st[i - 1]) else 0.0
            st.append(0.5 * x + 0.5 * prev)
        stages.append(st)
        e = st
    b = 0.618
    want = -b ** 3 * stages[5][-1] + 3 * (b * b + b ** 3) * stages[4][-1] - 3 * (2 * b * b + b + b ** 3) * stages[3][-1] \
        + (1 + 3 * b + b ** 3 + 3 * b * b) * stages[2][-1]
    assert math.isclose(s["t3"][-1], want, rel_tol=1e-12)


def test_chi_islast_uses_previous_close_on_the_last_candle_only():
    closes = [100 + math.sin(i / 5) * 5 for i in range(120)] + [300.0]
    a = chi_series(bars4(closes), islast=True)
    b = chi_series(bars4(closes), islast=False)
    assert np.allclose(a["fast"][:-1], b["fast"][:-1], equal_nan=True)  # history unchanged
    assert a["fast"][-1] != b["fast"][-1]                                # the spike is ignored on the last candle


def test_chi_cross_is_the_change_in_width_not_the_width():
    closes = [100.0 + (i % 2) * 0.01 for i in range(80)] + [100 + 3 * (i % 2) for i in range(10)]
    s = chi_series(bars4(closes), islast=False)
    k = int(np.flatnonzero(s["cross"])[0])
    assert s["roc"][k] > 9 >= s["roc"][k - 1] and s["width"][k] < 0.09 * 10   # crossing is on the % change


# -- broker (the package's cash broker rules) --------------------------------------------------------------------------
def six():
    o, h, l, c = [100, 100, 95, 110, 120, 115], [102, 105, 100, 115, 125, 120], [98, 90, 85, 100, 110, 105], [100, 95, 98, 112, 118, 110]
    return [{"t": T0 + i * H4, "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": 1} for i in range(6)]


def test_simultaneous_requests_buy_when_flat_close_when_held_and_final_bar_never_fills():
    d = six()
    buy = [True, True, False, True, False, True]
    sell = [True, True, False, True, False, False]
    r = B.replay(d, buy, sell, d[0]["t"], d[-1]["t"] + H4)
    t = r["trades"]
    assert t[0]["entry_index"] == 1 and t[0]["exit_index"] == 2          # buy@0 -> open 1; sell@1 -> open 2
    assert t[1]["entry_index"] == 4 and t[1]["forced_exit"]               # buy@3 -> open 4, sold at the final close; buy@5 ignored


def test_costs_and_cash_conservation():
    d = six()
    buy, sell = [False, True, False, False, False, False], [False, False, False, True, False, False]
    r = B.replay(d, buy, sell, d[0]["t"], d[-1]["t"] + H4)
    t = r["trades"][0]
    entry_px, exit_px = 95 * (1 + B.SLIP), 120 * (1 - B.SLIP)                # buy@1 -> open 2 (95); sell@3 -> open 4 (120)
    q = 10000 / (entry_px * (1 + B.FEE))
    assert math.isclose(t["units"], q, rel_tol=1e-12) and math.isclose(t["exit_cash"], q * exit_px * (1 - B.FEE), rel_tol=1e-12)
    assert math.isclose(t["units"] * t["entry_price"] + t["entry_fee"], 10000, rel_tol=1e-12)
    worse = B.replay(d, buy, sell, d[0]["t"], d[-1]["t"] + H4, cost_multiplier=2)
    assert worse["metrics"]["ending_capital"] < r["metrics"]["ending_capital"]


def test_warmup_candles_never_trade():
    d = six()
    r = B.replay(d, [True] * 6, [False] * 6, d[3]["t"], d[-1]["t"] + H4)
    assert all(t["entry_index"] >= 4 for t in r["trades"])


def test_execution_mode_skips_orders_below_the_exchange_minimum():
    d = six()
    buy = [False, True, False, False, False, False]
    r = B.replay(d, buy, [False] * 6, d[0]["t"], d[-1]["t"] + H4, capital=4.0, filters=B.Filters(0.01, 0.01, 5.0, "test"))
    assert r["trades"] == [] and r["skips"][0]["side"] == "buy" and "below the exchange minimum" in r["skips"][0]["reason"]
    ok = B.replay(d, buy, [False] * 6, d[0]["t"], d[-1]["t"] + H4, filters=B.Filters(0.01, 0.01, 5.0, "test"))
    assert ok["trades"][0]["units"] == pytest.approx(round(ok["trades"][0]["units"], 2))


# -- forward service --------------------------------------------------------------------------------------------------------
class FakeFeeds:
    def __init__(self, hours):
        self.all = hours
        self.calls = 0

    def hours(self, venue, pair, start, end):
        self.calls += 1
        return [h for h in self.all if start <= h["t"] and h["t"] + H1 <= end]

    def filters(self, venue, pair):
        return {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0, "source": "fake"}


def hourly(closes4, t0):
    out = []
    for i, c in enumerate(closes4):
        o = closes4[i - 1] if i else c
        for k in range(4):
            px = o + (c - o) * (k + 1) / 4
            out.append({"t": t0 + i * H4 + k * H1, "o": o + (c - o) * k / 4, "h": max(o, c) * 1.005, "l": min(o, c) * 0.995, "c": px, "v": 1})
    return out


def make(tmp_path, closes4, now_bars):
    t0 = T0 - 800 * H4
    hours = hourly(closes4, t0)
    clock = {"now": (t0 + 780 * H4) / 1000}
    svc = TvDeskService(tmp_path / "t.db", "t3cci", env={"T3CCI_ENABLED": "false"}, feeds=FakeFeeds(hours), clock=lambda: clock["now"])
    svc.books = [b for b in svc.books if b["asset"] == "ZEC"]
    return svc, clock, t0


def test_forward_books_start_flat_fill_next_open_and_survive_restart(tmp_path):
    closes = [100 + 0.2 * i for i in range(790)] + [258 - 3 * i for i in range(10)]
    svc, clock, t0 = make(tmp_path, closes, 780)
    start = svc.forward_start
    assert start == t0 + 781 * H4                                       # first boundary after creation
    clock["now"] = (t0 + 782 * H4 + 1) / 1000                            # candle 781 complete, 782's first hour not yet
    svc.cycle()
    st = svc._state("execution", "ZEC")
    assert st["trades"] == [] and st["units"] == 0 and st["pending"]["kind"] == "buy"   # rising -> buy request, not filled yet
    assert st["pending"]["fill_t"] == t0 + 782 * H4
    clock["now"] = (t0 + 782 * H4 + H1 + 1) / 1000                       # the 782 open is known now (its first hour)
    svc.cycle()
    st = svc._state("execution", "ZEC")
    assert st["units"] > 0 and st["entry"]["fill_t"] == t0 + 782 * H4
    assert st["entry"]["entry_price"] == pytest.approx(closes[781] * (1 + B.SLIP))   # the 4h open = previous close here
    # restart: a new service on the same database must not refill or re-book anything
    svc2 = TvDeskService(tmp_path / "t.db", "t3cci", env={"T3CCI_ENABLED": "false"}, feeds=svc.feeds, clock=lambda: clock["now"])
    svc2.books = svc.books
    svc2.cycle()
    st2 = svc2._state("execution", "ZEC")
    assert st2["entry"] == st["entry"] and st2["trades"] == [] and svc2.forward_start == start
    # the fall turns T3 negative -> a sell request -> closed at a next open, once
    clock["now"] = (t0 + 800 * H4) / 1000
    svc2.cycle()
    st3 = svc2._state("execution", "ZEC")
    assert len(st3["trades"]) == 1 and st3["units"] == 0
    svc2.cycle()
    assert len(svc2._state("execution", "ZEC")["trades"]) == 1           # idempotent
    res = svc2._state("research", "ZEC")
    assert len(res["trades"]) == 1 and res["cash"] > 0                   # the research reference book followed too
    s = svc2.summary()
    assert s["books"][0]["execution"]["trades"] == 1 and s["paper_only"] and s["read_only"]


def test_execution_mode_records_skips_with_their_reason(tmp_path):
    closes = [100 + 0.2 * i for i in range(790)]
    svc, clock, t0 = make(tmp_path, closes, 780)
    svc.feeds.filters = lambda venue, pair: {"step": 0.001, "min_qty": 0.001, "min_notional": 1e9, "source": "fake"}
    clock["now"] = (t0 + 784 * H4 + H1 + 1) / 1000
    svc.cycle()
    ex, rs = svc._state("execution", "ZEC"), svc._state("research", "ZEC")
    assert ex["units"] == 0 and "below the exchange minimum" in ex["skips"][-1]["reason"]
    assert rs["units"] > 0                                               # the fractional research book still filled


def test_heartbeat_gap_alert_once(tmp_path):
    sent = []
    class T:
        def live_alert(self, text):
            sent.append(text)
    svc, clock, t0 = make(tmp_path, [100.0] * 800, 780)
    svc.telegram = lambda: T()
    svc.health["heartbeat_ms"] = svc.now()
    assert svc.check_stall() is None
    clock["now"] += 700
    assert "stalled" in svc.check_stall() and svc.check_stall() is None and len(sent) == 1
    svc.health["heartbeat_ms"] = svc.now()
    assert "recovered" in svc.check_stall() and len(sent) == 2


def test_bitget_pages_history_candles_back_from_end_time():
    from app.tvdesks.feeds import Feeds
    start, now = T0, T0 + 450 * H1 + 1
    asked = []
    def getter(url):
        assert "history-candles" in url
        end = int(url.split("endTime=")[1].split("&")[0])
        asked.append(end)
        ts = [t for t in range(T0 - 1000 * H1, now, H1) if end - 200 * H1 <= t < end and t + H1 <= now]
        return {"data": [[str(t), "1", "2", "0.5", "1.5", "10", "15", "15"] for t in reversed(ts)]}
    f = Feeds(binance_client=object(), clock=lambda: now / 1000, getter=getter)
    b = f.hours("Bitget", "BGBUSDT", start, now)
    assert [x["t"] for x in b] == [start + k * H1 for k in range(450)] and len(asked) == 4   # 3 pages + one empty probe


def test_an_empty_feed_is_a_book_error_not_silence(tmp_path):
    svc, clock, t0 = make(tmp_path, [100.0] * 800, 780)
    svc.feeds.all = []
    svc.cycle()
    assert "no hourly candles" in svc.summary()["books"][0]["error"]["error"]
    svc.feeds.all = hourly([100.0] * 800, t0)
    svc.cycle()
    assert svc.summary()["books"][0]["error"] is None                   # cleared once the book works again
