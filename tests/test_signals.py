"""Canned-candle tests for S01, S02, S03, S15 and S20.

Every scenario is built from explicit close series (make_candles) so the expected signal follows from the
strategy's documented rule, not from a seeded random walk. Assertions target the spec (side, stop side,
R multiples, TP fractions, trail kind, gates) rather than incidental numbers.
"""
from __future__ import annotations

import pytest

from app.core.types import Signal
from app.strategies.base import param_specs
from app.strategies.registry import load_all
from app.strategies.s01_ema_cross import EmaCrossMomentum
from app.strategies.s02_rsi_sniper import RsiExtremeSniper
from app.strategies.s03_bb_squeeze import BollingerSqueezeBreak
from app.strategies.s08_orb import OpeningRangeBreak
from app.strategies.s15_liq_cascade_fade import LiquidationCascadeFade
from app.strategies.s20_ensemble_vote import CONTRIBUTORS, EnsembleVote
from tests.conftest import T0, FakeCtx, make_candles, run_strategy

SYM = "BTCUSDT"


def only_signal(out: list) -> Signal:
    """The scenario must produce exactly one signal bar carrying exactly one entry."""
    assert len(out) == 1, f"expected exactly one signal bar, got {[(c.open_time, len(s)) for c, s in out]}"
    _, sigs = out[0]
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.kind == "entry"
    return sig


def atr_as_of(candles: list, bar, tf: str = "1m", n: int = 14) -> float:
    """ATR(n) computed from the bars up to and including `bar` (what the strategy saw on that close)."""
    ctx = FakeCtx()
    ctx.set_candles(SYM, tf, candles[:candles.index(bar) + 1])
    atr = ctx.ind_last(SYM, tf, "atr", n=n)
    assert atr is not None and atr > 0
    return atr


# ---- S01 EMA Cross Momentum (1m) ----------------------------------------------------------

DOWN_UP = [100 - 0.1 * i for i in range(40)] + [96 + 0.15 * i for i in range(40)]
UP_DOWN = [100 + 0.1 * i for i in range(40)] + [104 - 0.15 * i for i in range(40)]


RISING_15M = [80 + 0.25 * i for i in range(60)]    # close well above its own EMA50 -> longs allowed
FALLING_15M = [130 - 0.5 * i for i in range(60)]   # close well below its own EMA50 -> shorts allowed


def s01_ctx(htf: list[float]) -> FakeCtx:
    """The 15m EMA50 filter is ON by default, so every S01 scenario needs a 15m series."""
    ctx = FakeCtx()
    ctx.set_candles(SYM, "15m", make_candles(closes=htf, tf="15m"))
    return ctx


class TestS01EmaCross:
    def test_defaults_are_the_fast_5_13_pack_with_the_htf_filter_on(self):
        p = EmaCrossMomentum.Params()
        assert (p.fast, p.slow) == (5, 13)
        assert p.htf_ema50_filter is True
        assert p.htf_ema_period == 50
        assert p.stop_atr_mult == pytest.approx(1.0)
        assert p.tp1_r == pytest.approx(2.0) and p.tp1_fraction == pytest.approx(0.40)
        assert p.trail_atr_mult == pytest.approx(1.2) and p.be_at_r == pytest.approx(0.8)
        assert EmaCrossMomentum.min_rr <= p.tp1_r, "the RR floor must not refuse the strategy's own TP1"

    def test_down_then_up_gives_long_with_2r_tp1_and_atr_runner(self):
        ctx = s01_ctx(RISING_15M)
        candles = make_candles(closes=DOWN_UP, tf="1m")
        out = run_strategy(EmaCrossMomentum(), ctx, candles)
        sig = only_signal(out)
        assert sig.strategy_id == "S01" and sig.symbol == SYM and sig.tf == "1m"
        assert sig.side == "long"
        assert 0 < sig.stop < sig.entry_price
        assert sig.rr() == pytest.approx(2.0)
        assert len(sig.take_profits) == 1
        assert sig.take_profits[0].fraction == pytest.approx(0.40)
        assert sig.take_profits[0].price > sig.entry_price
        assert sig.be_at_r == pytest.approx(0.8)
        assert sig.trail is not None and sig.trail.kind == "atr"
        assert sig.trail.mult == pytest.approx(1.2)
        assert sig.trail.activate_after_tp1 is True
        # stop sits 1.0 x ATR(14) beyond the cross candle's low, with the ATR as of that bar
        cross_candle = out[0][0]
        assert sig.stop == pytest.approx(cross_candle.low - 1.0 * atr_as_of(candles, cross_candle))
        # the cross happened on the rising leg, i.e. after the turn
        assert cross_candle.close > min(DOWN_UP)

    def test_up_then_down_gives_short_mirror(self):
        ctx = s01_ctx(FALLING_15M)
        candles = make_candles(closes=UP_DOWN, tf="1m")
        out = run_strategy(EmaCrossMomentum(), ctx, candles)
        sig = only_signal(out)
        assert sig.side == "short"
        assert sig.stop > sig.entry_price > 0
        assert sig.rr() == pytest.approx(2.0)
        assert sig.take_profits[0].fraction == pytest.approx(0.40)
        assert sig.take_profits[0].price < sig.entry_price
        assert sig.trail is not None and sig.trail.kind == "atr"
        cross_candle = out[0][0]
        assert sig.stop == pytest.approx(cross_candle.high + 1.0 * atr_as_of(candles, cross_candle))

    def test_flat_series_gives_no_signal(self):
        ctx = s01_ctx(RISING_15M)
        out = run_strategy(EmaCrossMomentum(), ctx, make_candles(closes=[100.0] * 80, tf="1m"))
        assert out == []

    def test_a_long_is_blocked_below_the_15m_ema50_and_fires_above_it(self):
        """Same 1m tape, opposite 15m context: the default filter is what decides."""
        blocked = FakeCtx()
        blocked.set_candles(SYM, "15m", make_candles(closes=FALLING_15M, tf="15m"))
        st_blocked = EmaCrossMomentum()
        assert run_strategy(st_blocked, blocked, make_candles(closes=DOWN_UP, tf="1m")) == []
        cross = st_blocked.state()["last_cross"][SYM]
        assert cross["side"] == "long" and cross["filtered"] is True
        assert blocked.candles(SYM, "15m")[-1].close < blocked.ind_last(SYM, "15m", "ema", n=50)

        allowed = s01_ctx(RISING_15M)
        sig = only_signal(run_strategy(EmaCrossMomentum(), allowed, make_candles(closes=DOWN_UP, tf="1m")))
        assert sig.side == "long"
        assert allowed.candles(SYM, "15m")[-1].close > allowed.ind_last(SYM, "15m", "ema", n=50)

    def test_the_filter_can_be_switched_off(self):
        ctx = FakeCtx()  # no 15m series at all
        st = EmaCrossMomentum(EmaCrossMomentum.Params(htf_ema50_filter=False))
        sig = only_signal(run_strategy(st, ctx, make_candles(closes=DOWN_UP, tf="1m")))
        assert sig.side == "long"

    def test_htf_filter_blocks_long_when_15m_close_is_below_ema50(self):
        ctx = FakeCtx()
        # a falling 15m series: its last close (100.5) is well below its EMA50 (~112.8)
        ctx.set_candles(SYM, "15m", make_candles(closes=[130 - 0.5 * i for i in range(60)], tf="15m"))
        st = EmaCrossMomentum(EmaCrossMomentum.Params(htf_ema50_filter=True))
        out = run_strategy(st, ctx, make_candles(closes=DOWN_UP, tf="1m"))
        assert out == []
        htf_close = ctx.candles(SYM, "15m")[-1].close
        assert htf_close < ctx.ind_last(SYM, "15m", "ema", n=50)
        last_cross = st.state()["last_cross"][SYM]
        assert last_cross["side"] == "long" and last_cross["filtered"] is True

    def test_htf_filter_lets_an_aligned_long_through(self):
        ctx = FakeCtx()
        # a rising 15m series: close above its EMA50 -> longs allowed
        ctx.set_candles(SYM, "15m", make_candles(closes=[80 + 0.25 * i for i in range(60)], tf="15m"))
        st = EmaCrossMomentum(EmaCrossMomentum.Params(htf_ema50_filter=True))
        out = run_strategy(st, ctx, make_candles(closes=DOWN_UP, tf="1m"))
        sig = only_signal(out)
        assert sig.side == "long"
        assert st.state()["last_cross"][SYM]["filtered"] is False


# ---- S02 RSI Extreme Sniper (5m) -----------------------------------------------------------

ZIGZAG = [100 + (0.2 if i % 2 else 0.0) for i in range(30)]  # RSI(7) parks near 50 before the extreme


class TestS02RsiSniper:
    def test_sharp_drop_then_bounce_gives_long_4r_full_tp(self):
        drop = [ZIGZAG[-1] - 1.0 * (k + 1) for k in range(6)]
        bounce = [drop[-1] + 2.0]
        ctx = FakeCtx()
        st = RsiExtremeSniper()
        candles = make_candles(closes=ZIGZAG + drop + bounce, tf="5m")
        out = run_strategy(st, ctx, candles)
        sig = only_signal(out)
        assert out[0][0] is candles[-1], "the reclaim bar itself is the signal bar"
        assert sig.strategy_id == "S02" and sig.tf == "5m"
        assert sig.side == "long"
        assert 0 < sig.stop < sig.entry_price
        assert sig.rr() == pytest.approx(4.0)
        assert len(sig.take_profits) == 1 and sig.take_profits[0].fraction == pytest.approx(1.0)
        # RSI(7) printed below 18 within the previous 5 bars and now closes at/above 22
        rsi = ctx.ind(SYM, "5m", "rsi", n=7)
        assert rsi[-1] >= 22.0
        assert min(v for v in rsi[-6:-1] if v is not None) < 18.0
        # stop = lowest low of the last 10 bars - 0.8 x ATR(14)
        lowest = min(c.low for c in candles[-10:])
        atr = ctx.ind_last(SYM, "5m", "atr", n=14)
        assert sig.stop == pytest.approx(lowest - 0.8 * atr)

    def test_sharp_rip_then_fade_gives_short_mirror(self):
        rip = [ZIGZAG[-1] + 1.0 * (k + 1) for k in range(6)]
        fade = [rip[-1] - 2.0]
        ctx = FakeCtx()
        candles = make_candles(closes=ZIGZAG + rip + fade, tf="5m")
        out = run_strategy(RsiExtremeSniper(), ctx, candles)
        sig = only_signal(out)
        assert sig.side == "short"
        assert sig.stop > sig.entry_price > 0
        assert sig.rr() == pytest.approx(4.0)
        assert len(sig.take_profits) == 1 and sig.take_profits[0].fraction == pytest.approx(1.0)
        rsi = ctx.ind(SYM, "5m", "rsi", n=7)
        assert rsi[-1] <= 78.0
        assert max(v for v in rsi[-6:-1] if v is not None) > 82.0
        highest = max(c.high for c in candles[-10:])
        atr = ctx.ind_last(SYM, "5m", "atr", n=14)
        assert sig.stop == pytest.approx(highest + 0.8 * atr)

    def test_no_signal_without_an_extreme(self):
        ctx = FakeCtx()
        out = run_strategy(RsiExtremeSniper(), ctx, make_candles(closes=ZIGZAG + ZIGZAG, tf="5m"))
        assert out == []


# ---- S03 Bollinger Squeeze Break (5m) ------------------------------------------------------

def squeeze_closes(n: int = 60) -> list[float]:
    """A zigzag whose amplitude contracts every bar, so the prior bar's bandwidth is always a 20-bar low."""
    return [100 + (0.5 - 0.45 * i / (n - 1)) * (1 if i % 2 else -1) for i in range(n)]


class TestS03BbSqueeze:
    def test_break_above_upper_band_on_3x_volume_gives_long(self):
        closes = squeeze_closes() + [102.0]
        volumes = [100.0] * 60 + [300.0]
        ctx = FakeCtx()
        st = BollingerSqueezeBreak()
        out = run_strategy(st, ctx, make_candles(closes=closes, volumes=volumes, tf="5m"))
        sig = only_signal(out)
        assert sig.strategy_id == "S03" and sig.tf == "5m"
        assert sig.side == "long"
        assert 0 < sig.stop < sig.entry_price
        assert sig.take_profits and sig.take_profits[0].fraction == pytest.approx(0.40)
        assert sig.take_profits[0].price > sig.entry_price
        assert sig.trail is not None and sig.trail.kind == "atr"
        # stop is the prior bar's lower band; TP sits 1.6 x the prior squeeze height above the entry
        _, upper, lower, _ = ctx.ind(SYM, "5m", "bollinger", n=20, k=2.0)
        u_prev, l_prev = upper[-2], lower[-2]
        assert sig.entry_price > u_prev
        assert sig.stop == pytest.approx(l_prev)
        assert sig.rr() == pytest.approx(2.0), "TP1 is 2.0R; the squeeze height is only a note"
        assert sig.meta["squeeze_height_target"] == pytest.approx(
            sig.entry_price + 1.6 * (u_prev - l_prev))

    def test_break_below_lower_band_gives_short(self):
        closes = squeeze_closes() + [98.0]
        volumes = [100.0] * 60 + [300.0]
        ctx = FakeCtx()
        out = run_strategy(BollingerSqueezeBreak(), ctx, make_candles(closes=closes, volumes=volumes, tf="5m"))
        sig = only_signal(out)
        assert sig.side == "short"
        assert sig.stop > sig.entry_price > 0
        assert sig.take_profits[0].fraction == pytest.approx(0.40)
        assert sig.take_profits[0].price < sig.entry_price
        assert sig.trail is not None and sig.trail.kind == "atr"

    def test_no_signal_when_volume_is_normal(self):
        closes = squeeze_closes() + [102.0]
        volumes = [100.0] * 61
        ctx = FakeCtx()
        out = run_strategy(BollingerSqueezeBreak(), ctx, make_candles(closes=closes, volumes=volumes, tf="5m"))
        assert out == []

    def test_no_signal_without_a_squeeze(self):
        # expanding amplitude: the prior bar's bandwidth is never the 20-bar low
        closes = [100 + (0.05 + 0.45 * i / 59) * (1 if i % 2 else -1) for i in range(60)] + [102.0]
        volumes = [100.0] * 60 + [300.0]
        ctx = FakeCtx()
        out = run_strategy(BollingerSqueezeBreak(), ctx, make_candles(closes=closes, volumes=volumes, tf="5m"))
        assert out == []


# ---- S15 Liquidation Cascade Fade (1m trigger, 15m regime gate) -------------------------------

CALM_1M = [100 + (0.05 if i % 2 else -0.05) for i in range(30)]
RANGE_15M = [100 + (0.3 if i % 2 else -0.3) for i in range(20)]


class TestS15CascadeFade:
    def test_dump_on_5x_volume_inside_15m_range_is_faded_long(self):
        c1 = make_candles(closes=CALM_1M + [99.0], volumes=[100.0] * 30 + [500.0], tf="1m")
        ctx = FakeCtx()
        ctx.set_candles(SYM, "15m", make_candles(closes=RANGE_15M, tf="15m"))
        st = LiquidationCascadeFade()
        out = run_strategy(st, ctx, c1)
        sig = only_signal(out)
        dump = c1[-1]
        assert out[0][0] is dump
        assert sig.strategy_id == "S15" and sig.tf == "1m"
        assert sig.side == "long"
        assert sig.stop < dump.low, "stop must sit below the cascade low"
        assert sig.stop == pytest.approx(dump.low * (1 - 0.0035))
        assert len(sig.take_profits) == 1 and sig.take_profits[0].fraction == pytest.approx(1.0)
        assert sig.take_profits[0].price > sig.entry_price
        assert sig.max_hold_s == 1800
        assert st.state()["regime_skips"] == 0
        assert st.state()["last_cascade"][SYM]["direction"] == "down"

    def test_rip_on_5x_volume_is_faded_short(self):
        c1 = make_candles(closes=CALM_1M + [101.0], volumes=[100.0] * 30 + [500.0], tf="1m")
        ctx = FakeCtx()
        ctx.set_candles(SYM, "15m", make_candles(closes=RANGE_15M, tf="15m"))
        out = run_strategy(LiquidationCascadeFade(), ctx, c1)
        sig = only_signal(out)
        assert sig.side == "short"
        assert sig.stop > c1[-1].high
        assert sig.take_profits[0].price < sig.entry_price

    def test_15m_close_outside_prior_range_skips_and_counts(self):
        c1 = make_candles(closes=CALM_1M + [99.0], volumes=[100.0] * 30 + [500.0], tf="1m")
        ctx = FakeCtx()
        # the latest closed 15m bar breaks above the prior 16-bar range -> breakout, not noise
        ctx.set_candles(SYM, "15m", make_candles(closes=RANGE_15M[:-1] + [103.0], tf="15m"))
        st = LiquidationCascadeFade()
        before = st.state()["regime_skips"]
        out = run_strategy(st, ctx, c1)
        assert out == []
        assert st.state()["regime_skips"] == before + 1

    def test_dump_on_normal_volume_is_ignored(self):
        c1 = make_candles(closes=CALM_1M + [99.0], volumes=[100.0] * 31, tf="1m")
        ctx = FakeCtx()
        ctx.set_candles(SYM, "15m", make_candles(closes=RANGE_15M, tf="15m"))
        out = run_strategy(LiquidationCascadeFade(), ctx, c1)
        assert out == []


# ---- S08 Opening Range Break (hourly UTC ranges) --------------------------------------------

HOUR_MS = 3_600_000
MINUTE_MS = 60_000
ORB_START = T0 + 13 * HOUR_MS  # 13:00 UTC of the conftest day


def orb_candles(breaks=(20,), break_close: float = 101.0, break_volume: float = 500.0, n: int = 41) -> list:
    """`n` flat 1m bars from 13:00 UTC (range 99.5 / 100.5) with a break bar at each index in `breaks`."""
    closes, highs, lows, opens, volumes = [], [], [], [], []
    for i in range(n):
        breaking = i in breaks
        closes.append(break_close if breaking else 100.0)
        opens.append(100.0)
        highs.append(max(100.5, closes[-1]))
        lows.append(min(99.5, closes[-1]))
        volumes.append(break_volume if breaking else 100.0)
    return make_candles(closes=closes, highs=highs, lows=lows, opens=opens, volumes=volumes,
                        tf="1m", start_ts=ORB_START)


class TestS08HourlyOpeningRange:
    def test_defaults_build_a_fresh_range_every_utc_hour(self):
        p = OpeningRangeBreak.Params()
        assert p.hourly_orb is True
        assert p.range_minutes == 15

    def test_the_13_00_range_is_built_from_13_00_to_13_15_and_traded_at_13_20(self):
        ctx = FakeCtx()
        st = OpeningRangeBreak()
        out = run_strategy(st, ctx, orb_candles())
        sig = only_signal(out)
        bar = out[0][0]
        assert bar.open_time == ORB_START + 20 * MINUTE_MS, "the break is traded at 13:20 UTC"
        assert sig.strategy_id == "S08" and sig.side == "long" and sig.tf == "1m"
        # the range is the 13:00-13:15 window, NOT anything anchored on 00:00 UTC
        assert sig.meta["hourly"] is True
        assert sig.meta["session"] == ORB_START
        assert sig.meta["range_bars"] == 15
        assert sig.meta["range_high"] == pytest.approx(100.5)
        assert sig.meta["range_low"] == pytest.approx(99.5)
        assert sig.meta["range_height"] == pytest.approx(1.0)
        assert sig.stop == pytest.approx(99.5), "stop is the other side of the hour's range"
        assert sig.take_profits[0].price == pytest.approx(sig.entry_price + 2.0 * 1.0)
        rec = st.state()["ranges"][SYM]
        assert rec["session"] == ORB_START
        assert rec["date"].endswith("13:00")
        assert rec["window_end"] == ORB_START + 15 * MINUTE_MS
        assert rec["outcome"] == "traded"

    def test_a_break_inside_the_range_window_is_not_traded(self):
        ctx = FakeCtx()
        out = run_strategy(OpeningRangeBreak(), ctx, orb_candles(breaks=(5,)))
        assert out == [], "bars 13:00-13:14 form the range, they never trade it"

    def test_only_the_first_break_of_the_hour_is_taken(self):
        ctx = FakeCtx()
        out = run_strategy(OpeningRangeBreak(), ctx, orb_candles(breaks=(20, 25, 30)))
        assert len(out) == 1
        assert out[0][0].open_time == ORB_START + 20 * MINUTE_MS

    def test_a_low_volume_break_consumes_the_range(self):
        ctx = FakeCtx()
        st = OpeningRangeBreak()
        out = run_strategy(st, ctx, orb_candles(breaks=(20, 25), break_volume=110.0))
        assert out == []
        assert st.state()["ranges"][SYM]["outcome"] == "low_volume"

    def test_the_next_utc_hour_starts_a_new_range(self):
        ctx = FakeCtx()
        st = OpeningRangeBreak()
        run_strategy(st, ctx, orb_candles())
        assert st.state()["ranges"][SYM]["session"] == ORB_START
        next_hour = make_candles(closes=[100.0] * 3, highs=[100.5] * 3, lows=[99.5] * 3, opens=[100.0] * 3,
                                 volumes=[100.0] * 3, tf="1m", start_ts=ORB_START + HOUR_MS)
        run_strategy(st, ctx, next_hour)
        rec = st.state()["ranges"][SYM]
        assert rec["session"] == ORB_START + HOUR_MS
        assert rec["date"].endswith("14:00")
        assert rec["traded"] is False and rec["complete"] is False


# ---- S20 Ensemble Vote (5m, 15m EMA50 filter) ------------------------------------------------

def rising_ctx() -> tuple[FakeCtx, list]:
    """5m and 15m series both rising so the latest 15m close sits above its EMA50."""
    c5 = make_candles(closes=[100 + 0.05 * i for i in range(100)], tf="5m")
    c15 = make_candles(closes=[95 + 0.1 * i for i in range(60)], tf="15m")
    ctx = FakeCtx()
    ctx.set_candles(SYM, "5m", c5)
    ctx.set_candles(SYM, "15m", c15)
    assert c15[-1].close > ctx.ind_last(SYM, "15m", "ema", n=50)
    return ctx, c5


def post_long_votes(ctx: FakeCtx, sids: tuple[str, ...]) -> list[float]:
    """Post one fresh long entry per contributor with distinct stops below price; returns the stops."""
    now = ctx.now_ms()
    price = ctx.last_price(SYM)
    stops = []
    for k, sid in enumerate(sids, start=1):
        stop = price * (1 - 0.005 * k)
        stops.append(stop)
        ctx.board.post(Signal(sid, SYM, "entry", "long", now - 60_000, "5m", price, stop), "approved")
    return stops


class TestS20EnsembleVote:
    FIVE = ("S01", "S03", "S06", "S10", "S12")

    def test_five_live_long_votes_give_big_long_with_tightest_stop(self):
        ctx, c5 = rising_ctx()
        for sid in self.FIVE:
            ctx.board.set_enabled(sid, True)
        stops = post_long_votes(ctx, self.FIVE)
        st = EnsembleVote()
        sigs = st.on_candle(c5[-1], ctx)
        assert len(sigs) == 1
        sig = sigs[0]
        assert sig.strategy_id == "S20" and sig.kind == "entry" and sig.tf == "5m"
        assert sig.side == "long"
        assert sig.size_mult == pytest.approx(1.25), "big size is 1.25, not the old 1.4"
        assert sig.stop == pytest.approx(max(stops)), "tightest contributing stop below price"
        assert sig.stop < sig.entry_price
        risk = sig.entry_price - sig.stop
        assert len(sig.take_profits) == 2, "TP1 plus a 6R runner cap"
        assert sig.take_profits[0].fraction == pytest.approx(0.40)
        assert sig.take_profits[0].price == pytest.approx(sig.entry_price + 2.0 * risk)
        assert sig.take_profits[1].fraction == pytest.approx(0.60)
        assert sig.rr() == pytest.approx(6.0), "rr() reports the LAST take-profit"
        row = st.state()["symbols"][SYM]
        assert row["net"] == 5 and row["live_contributors"] == 5 and row["ema_ok"] is True
        assert row["notice"] is None

    def test_three_votes_give_normal_size(self):
        ctx, c5 = rising_ctx()
        for sid in self.FIVE:
            ctx.board.set_enabled(sid, True)
        stops = post_long_votes(ctx, self.FIVE[:3])
        sigs = EnsembleVote().on_candle(c5[-1], ctx)
        assert len(sigs) == 1
        assert sigs[0].side == "long"
        assert sigs[0].size_mult == pytest.approx(1.0)
        assert sigs[0].stop == pytest.approx(max(stops))

    def test_four_votes_still_size_1_0(self):
        """size_big applies from |net| 5; 3-4 votes are a normal-size trade, never 1.4."""
        ctx, c5 = rising_ctx()
        for sid in self.FIVE:
            ctx.board.set_enabled(sid, True)
        post_long_votes(ctx, self.FIVE[:4])
        sigs = EnsembleVote().on_candle(c5[-1], ctx)
        assert len(sigs) == 1
        assert ctx.board.votes(SYM, CONTRIBUTORS, ttl_ms=30 * 60_000, now_ms=ctx.now_ms()) is not None
        assert sigs[0].size_mult == pytest.approx(1.0)
        assert EnsembleVote.Params().size_big == pytest.approx(1.25)
        assert EnsembleVote.Params().big_votes == 5
        assert EnsembleVote.Params().exit_below_net == 2

    def test_two_live_contributors_gate_entries_with_a_notice(self):
        ctx, c5 = rising_ctx()
        for sid in ("S01", "S03"):
            ctx.board.set_enabled(sid, True)
        post_long_votes(ctx, self.FIVE)  # five votes, but only two contributors are live
        st = EnsembleVote()
        assert st.on_candle(c5[-1], ctx) == []
        row = st.state()["symbols"][SYM]
        assert row["live_contributors"] == 2
        assert "live contributors" in (row["notice"] or "")

    def test_shadow_votes_count_only_when_allowed(self):
        ctx, c5 = rising_ctx()
        post_long_votes(ctx, self.FIVE[:3])  # nobody enabled -> all three votes are shadow
        assert EnsembleVote().on_candle(c5[-1], ctx) == []
        st = EnsembleVote(EnsembleVote.Params(allow_shadow_votes=True))
        sigs = st.on_candle(c5[-1], ctx)
        assert len(sigs) == 1 and sigs[0].side == "long"
        assert sigs[0].size_mult == pytest.approx(1.0)

    def test_votes_against_the_15m_ema_are_ignored(self):
        ctx, c5 = rising_ctx()
        for sid in self.FIVE:
            ctx.board.set_enabled(sid, True)
        now = ctx.now_ms()
        price = ctx.last_price(SYM)
        for k, sid in enumerate(self.FIVE, start=1):
            ctx.board.post(Signal(sid, SYM, "entry", "short", now - 60_000, "5m", price, price * (1 + 0.005 * k)),
                           "approved")
        st = EnsembleVote()
        assert st.on_candle(c5[-1], ctx) == []
        assert st.state()["symbols"][SYM]["ema_ok"] is False

    def test_contributor_list_matches_registry(self):
        from app.strategies.registry import ENSEMBLE_CONTRIBUTORS
        assert tuple(CONTRIBUTORS) == tuple(ENSEMBLE_CONTRIBUTORS)


class TestHoldExperimentV1:
    """The `hold_v1` controlled change: max_hold_s x4 on the 1m books that had a clock.

    These values are the experiment. If someone edits a hold without updating this test, the run's
    before/after numbers stop meaning anything, so the four numbers are pinned here deliberately.
    """

    HOLDS = {"S01": ("max_hold_s", None), "S05": ("max_hold_s", None),
             "S17": ("hold_s", 180), "S19": ("max_hold_s", 14400)}
    CONTROL = {"S10": ("max_hold_s", None), "S11": ("max_hold_s", None),
               "S15": ("max_hold_s", 1800), "S16": ("max_hold_s", None)}

    @pytest.mark.parametrize("sid,attr,want", [(s, a, w) for s, (a, w) in HOLDS.items()])
    def test_the_four_holds(self, sid, attr, want):
        # `want is None` means the book has no clock at all - the param is absent, not zero - so these
        # two (S01, S05) were correctly left alone by the experiment's "if max_hold_s is set" rule.
        assert getattr(load_all(strict=True)[sid].Params(), attr, None) == want

    @pytest.mark.parametrize("sid,attr,want", [(s, a, w) for s, (a, w) in CONTROL.items()])
    def test_control_group_is_untouched(self, sid, attr, want):
        """S10/S11/S15/S16 are the control: their holds must not move while the experiment runs."""
        assert getattr(load_all(strict=True)[sid].Params(), attr, None) == want

    def test_the_changed_books_document_the_change(self):
        """A reader looking at S17/S19 must see why the number is what it is, not just the number."""
        for sid, attr in (("S17", "hold_s"), ("S19", "max_hold_s")):
            spec = next(p for p in param_specs(load_all(strict=True)[sid].Params) if p.name == attr)
            assert "hold_v1" in (spec.help or "")


class TestNewCandidates:
    """S21-S25 were added from public TradingView techniques. These lock the contract each one must
    honour so a broken candidate cannot quietly join the bake-off."""

    IDS = ("S21", "S22", "S23", "S24", "S25")

    @pytest.mark.parametrize("sid", IDS)
    def test_constructs_and_declares_itself(self, sid):
        cls = load_all(strict=True)[sid]
        s = cls()
        assert cls.id == sid and cls.name and cls.doc.idea
        assert cls.timeframes and all(tf in ("1m", "5m", "15m") for tf in cls.timeframes)
        assert isinstance(s.state(), dict)

    @pytest.mark.parametrize("sid", IDS)
    def test_is_candle_only_so_it_can_be_backtested(self, sid):
        """A candidate that needs the book, ticks or funding cannot be validated on historical klines,
        and an unvalidated strategy is exactly what we are trying to avoid adding."""
        cls = load_all(strict=True)[sid]
        assert not cls.needs_book and not cls.needs_funding and not cls.needs_ticks

    @pytest.mark.parametrize("sid", IDS)
    def test_quiet_on_flat_tape(self, sid):
        """No signal from a dead flat market: a strategy that fires on nothing will fire on anything."""
        cls = load_all(strict=True)[sid]
        s = cls()
        ctx = FakeCtx(symbols=["BTCUSDT"])
        out = []
        for tf in cls.timeframes:
            flat = make_candles([100.0] * 300, tf=tf, symbol="BTCUSDT", wick=0.0)
            ctx.set_candles("BTCUSDT", tf, flat)
            for c in flat[-5:]:
                out += s.on_candle(c, ctx)
        assert out == []

    @pytest.mark.parametrize("sid", IDS)
    def test_reset_clears_state(self, sid):
        s = load_all(strict=True)[sid]()
        before = s.state()
        s.reset()
        assert isinstance(s.state(), dict) and set(s.state()) == set(before)

    @pytest.mark.parametrize("sid", IDS)
    def test_every_param_has_a_label(self, sid):
        for spec in param_specs(load_all(strict=True)[sid].Params):
            assert spec.label, f"{sid}.{spec.name} has no label for the GUI slider"
