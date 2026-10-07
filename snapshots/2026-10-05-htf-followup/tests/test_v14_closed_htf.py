"""Regression checks for causal HTF context and strategy-specific regime decisions."""
from dataclasses import replace

import pytest

from app.core.types import Candle, Signal
from app.strategies.v14 import htf
from tests.test_v14 import Ctx, DOWN, FLAT, T, T0, UP


def test_complete_utc_ohlc_groups_use_last_close_and_sum_volume():
    bars = [Candle("A", "1h", T0 + i * htf.HOUR, 100 + i, 102 + i, 99 + i, 101 + i,
                   i + 1, T0 + (i + 1) * htf.HOUR - 1, quote_volume=10 * (i + 1)) for i in range(24)]
    four = htf.completed_bars(bars, 4)
    daily = htf.completed_bars(bars, 24)
    assert len(four) == 6 and len(daily) == 1
    assert (four[0].open, four[0].high, four[0].low, four[0].close, four[0].volume) == (100, 105, 99, 104, 10)
    assert daily[0].close == bars[-1].close and daily[0].quote_volume == sum(c.quote_volume for c in bars)
    assert daily[0].close_time == T0 + htf.DAY - 1
    assert htf.completed_bars(bars[:-1], 24) == []
    assert len(htf.completed_bars(bars[1:], 4)) == 5  # no partial first bucket


def test_one_missing_hour_does_not_become_a_fake_htf_candle():
    ctx = Ctx({"A": UP})
    del ctx.bars["A"][-20]
    assert htf.htf_view(ctx, "A", 100) is None
    assert htf.completed_bars(ctx.bars["A"], 24)[-1].close_time < T


def test_preloaded_future_candles_do_not_leak_into_trend():
    asof = T0 + 576 * htf.HOUR - 1
    full = Ctx({"A": UP})
    truncated = Ctx({"A": UP[:576]})
    assert htf.htf_view(full, "A", 100, asof) == htf.htf_view(truncated, "A", 100, asof)
    full.bars["A"][-1] = replace(full.bars["A"][-1], close=1e8, high=1e8)
    assert htf.htf_view(full, "A", 100, asof) == htf.htf_view(truncated, "A", 100, asof)


def test_forming_hour_is_ignored_until_closed():
    ctx = Ctx({"A": UP})
    c = ctx.bars["A"][-1]
    ctx.bars["A"].append(replace(c, open_time=T + 1, close_time=T + htf.HOUR, closed=False, close=1e8, high=1e8))
    assert htf.htf_view(ctx, "A", 100, T) == htf.htf_view(Ctx({"A": UP}), "A", 100, T)


def test_hour_and_daily_rollover_only_at_completed_boundary_even_with_cached_future_history():
    ctx = Ctx({"A": UP})
    prior = htf.htf_view(ctx, "A", 100, T - 1)
    current = htf.htf_view(ctx, "A", 100, T)
    assert prior["d1_close_ms"] == T - htf.DAY and current["d1_close_ms"] == T
    assert prior["h4_close_ms"] == T - 4 * htf.HOUR and current["h4_close_ms"] == T


def test_stale_history_invalidates_cached_result_at_next_hour():
    ctx = Ctx({"A": UP})
    assert htf.htf_view(ctx, "A", 100, T)
    assert htf.htf_view(ctx, "A", 100, T + htf.HOUR) is None


@pytest.mark.parametrize("corrupt", [
    lambda cs: cs.append(cs[-1]),
    lambda cs: cs.reverse(),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], closed=False)),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], close=float("nan"))),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], high=0.0)),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], low=1e8)),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], close_time=T - 1)),
    lambda cs: cs.__setitem__(-1, replace(cs[-1], symbol="B")),
])
def test_invalid_tape_fails_closed(corrupt):
    ctx = Ctx({"A": UP})
    corrupt(ctx.bars["A"])
    assert htf.htf_view(ctx, "A", 100) is None


@pytest.mark.parametrize("price", [0, -1, float("inf"), float("nan")])
def test_invalid_entry_price_fails_closed(price):
    assert htf.htf_view(Ctx({"A": UP}), "A", price) is None


def test_no_implicit_future_clock():
    class NoClock:
        candles = Ctx({"A": UP}).candles
    assert htf.htf_view(NoClock(), "A", 100) is None


def test_actual_4h_and_daily_ema_used():
    ctx = Ctx({"A": UP})
    view = htf.htf_view(ctx, "A", 1)  # large LTF dip cannot change the HTF state
    four = htf.completed_bars(ctx.bars["A"], 4)
    daily = htf.completed_bars(ctx.bars["A"], 24)
    assert (view["h4"], view["h4_slope_atr"]) == htf._trend(four, 21, 3)
    assert (view["d1"], view["d1_slope_atr"]) == htf._trend(daily, 10, 1)
    assert view["h4"] == view["d1"] == 1


@pytest.mark.parametrize("side,d", [("long", 1), ("short", -1)])
def test_policies_distinguish_ranges_breakouts_and_pullbacks(side, d):
    neutral = {"h4": 0, "d1": 0, "h4_slope_atr": 0.0}
    daily = {"h4": 0, "d1": d, "h4_slope_atr": 0.0}
    assert not htf.allows(neutral, side, "reversion")
    assert not htf.allows(neutral, side, "breakout")
    assert not htf.allows(neutral, side, "pullback")
    assert htf.allows(daily, side, "pullback")
    assert htf.allows(daily, side, "reversion")
    assert not htf.allows(daily, side, "breakout")
    strong_opposing_slope = {"h4": 0, "d1": 0, "h4_slope_atr": -d * 0.3}
    assert not htf.allows(strong_opposing_slope, side, "reversion")
    daily_veto = {"h4": d, "d1": -d, "h4_slope_atr": d * 0.3}
    assert all(not htf.allows(daily_veto, side, p) for p in ("reversion", "pullback", "breakout"))


def test_invalid_side_or_policy_is_not_treated_as_short():
    view = {"h4": -1, "d1": -1, "h4_slope_atr": -0.3}
    assert not htf.allows(view, "sell") and not htf.allows(view, "short", "unknown")


def test_trend_deadband_treats_micro_changes_as_neutral():
    cs = [Candle("A", "4h", i * 4 * htf.HOUR, 100, 110, 90, 100 + i * 1e-6,
                 1, (i + 1) * 4 * htf.HOUR - 1) for i in range(60)]
    assert htf._trend(cs, 21, 3)[0] == 0


def test_single_wrapper_keeps_exits_and_records_entry_context_and_rejections():
    class Base:
        timeframes = ("5m",)
        signal_tf = "5m"

        def on_candle(self, c, ctx):
            return [Signal("BASE", "A", "close", "long", T, "5m", 100),
                    Signal("BASE", "A", "entry", "long", T, "5m", 100)]
    cls = htf.htf_single(Base, "V14.1", "test")
    bot = cls()
    ctx = Ctx({"A": UP}, {"A": 100})
    out = bot.on_candle(ctx.candles("A", "5m")[-1], ctx)
    assert [s.kind for s in out] == ["close", "entry"]
    assert out[1].meta["htf"]["policy"] == "reversion"
    assert out[1].meta["htf"]["decision_ms"] == T
    ctx = Ctx({"A": DOWN}, {"A": 100})
    out = bot.on_candle(ctx.candles("A", "5m")[-1], ctx)
    assert [s.kind for s in out] == ["close"] and bot.htf_rejects == {"regime_veto": 1}


def test_scanner_attaches_context_to_final_ladder_signal():
    from app.strategies.v11.scan import Cand
    class Base:
        timeframes = ("15m",)
        signal_tf = "15m"

        def scan(self, ctx, t):
            return [Cand("A", "long", 99, 1, 0.9, {}, "setup")]

        def _signal(self, ctx, x, t):
            return Signal("BASE", x.symbol, "entry", x.side, t, "15m", 100, stop=99)
    bot = htf.htf_scanner(Base, "V14.2", "test")()
    ctx = Ctx({"A": UP}, {"A": 100})
    x = bot.scan(ctx, T)[0]
    assert bot._signal(ctx, x, T).meta["htf"]["policy"] == "breakout"
    assert x.factors["htf_h4"] == x.factors["htf_d1"] == 1
    assert bot.scan(ctx, T - 1) == []  # no fresh entry-timeframe candle
    assert bot.htf_rejects["missing_or_stale_htf"] == 1


def test_warmup_and_history_capacity_support_twenty_daily_candles():
    from app.backtest.context import ReplayContext
    from app.competition.v14_config import WARMUP_DAYS
    ctx = ReplayContext(None, {}, ["A"])
    assert ctx._maxlen >= htf.MIN_1H and WARMUP_DAYS >= htf.MIN_D1 + 1
    for c in Ctx({"A": UP}).bars["A"]:
        ctx.push(c)
    assert htf.htf_view(ctx, "A", 100, T)


def test_snapback_filters_before_order_slots_and_records_selected_context(monkeypatch):
    from types import SimpleNamespace
    from app.strategies.v13 import snapback
    def stretch(cs):
        return (5 if cs[-1].symbol == "A" else 4, 1, 100)
    monkeypatch.setattr(snapback, "stretch_atr", stretch)
    class Base:
        timeframes = ("5m",)
        signal_tf = "5m"
        anchor = "BTC"
        universe = ("A", "B", "BTC")

        def __init__(self):
            self.params = SimpleNamespace(threshold=3, max_signals=1)
            self.resting = {}

        def current(self, ctx, sym, tf, t, need):
            return ctx.candles(sym, tf)

        def limit_signal(self, ctx, last, st, atr, vwap, t):
            return Signal(self.id, last.symbol, "entry", "short", t, "5m", 100,
                          meta={"expire_ms": T + 60_000})
    bot = htf.htf_snapback(Base, "V14.4", "test")()
    ctx = Ctx({"A": UP, "B": DOWN}, {"A": 100, "B": 100, "BTC": 100})
    out = bot.on_candle(ctx.candles("BTC", "5m")[-1], ctx)
    assert [s.symbol for s in out] == ["B"]  # rejected A cannot consume the sole slot
    assert out[0].meta["htf"]["policy"] == "reversion"
    assert out[0].meta["htf"]["h4_close_ms"] == T
    assert bot.resting == {"B": T + 60_000}
