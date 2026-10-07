"""ExecutionModel, order lifecycle, latency, maker/taker and maintenance-margin brackets.

Every case here is deterministic: no RNG, no network, fixed prices. If one of these changes
behaviour, a competition result changed with it.
"""
from __future__ import annotations

import pytest

from app.backtest.brackets import Bracket, BracketTable
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.core.types import BookSnapshot, MarketRules
from app.execution.config import ExecutionConfig, FeeSchedule
from app.execution.model import ExecutionModel, MarketState
from app.execution.orders import Order
from tests.conftest import T0, make_candles, settings_factory
from tests.test_competition import OneShot, big_rules, rising

SYM = "BTCUSDT"


def book(bids, asks) -> BookSnapshot:
    return BookSnapshot(SYM, bids=list(bids), asks=list(asks), ts=T0)


def order(side="BUY", qty=2.0, decision=99.5, otype="MARKET", limit=None, at=0) -> Order:
    o = Order(SYM, side, qty, order_type=otype, limit_price=limit, decision_price=decision,
              signal_ts=at, execute_at=at)
    return o.submit(at)


def state(**over) -> MarketState:
    base = dict(symbol=SYM, ts=T0, last=99.5)
    base.update(over)
    return MarketState(**base)


# ---- TEST 1: order-book walk -------------------------------------------------------------------

class TestLevel1OrderBook:
    def test_walks_levels_and_vwaps(self):
        """asks 100@1, 101@2; BUY 2 -> 1@100 + 1@101, VWAP 100.5."""
        m = ExecutionModel()
        res = m.execute(order(), state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        assert res.execution_level_used == 1
        assert [(f.price, f.qty) for f in res.fills] == [(100.0, 1.0), (101.0, 1.0)]
        assert res.avg_price == pytest.approx(100.5)
        assert res.role == "TAKER"

    def test_each_level_is_its_own_fill_record(self):
        m = ExecutionModel()
        res = m.execute(order(), state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        assert len(res.fills) == 2
        assert [f.level_index for f in res.fills] == [0, 1]
        assert all(f.execution_level == 1 for f in res.fills)

    def test_fee_is_charged_per_fill_at_the_taker_rate(self):
        fees = FeeSchedule(maker_rate=0.0002, taker_rate=0.0005)
        m = ExecutionModel(fees=fees)
        res = m.execute(order(), state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        expected = 100.0 * 1 * 0.0005 + 101.0 * 1 * 0.0005
        assert m.fee_for(res) == pytest.approx(expected)

    def test_sell_walks_the_bids(self):
        m = ExecutionModel()
        res = m.execute(order(side="SELL", qty=2.0, decision=100.5),
                        state(bid=100.0, ask=101.0, book=book([(100.0, 1), (99.0, 3)], [(101.0, 5)])))
        assert [(f.price, f.qty) for f in res.fills] == [(100.0, 1.0), (99.0, 1.0)]
        assert res.avg_price == pytest.approx(99.5)

    def test_depth_consumed_is_recorded(self):
        m = ExecutionModel()
        res = m.execute(order(), state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        assert res.book_depth_consumed == pytest.approx(100.0 * 1 + 101.0 * 1)


# ---- TEST 6: partial fills ---------------------------------------------------------------------

class TestPartialFills:
    def test_insufficient_depth_partially_fills(self):
        m = ExecutionModel()
        o = order(qty=10.0)
        res = m.execute(o, state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        assert res.filled_qty == pytest.approx(3.0)
        for f in res.fills:
            o.add_fill(f)
        assert o.state == "PARTIALLY_FILLED"
        assert o.remaining_qty == pytest.approx(7.0)
        assert o.filled_qty + o.remaining_qty == pytest.approx(o.qty)

    def test_no_depth_at_all_is_not_a_fill(self):
        m = ExecutionModel()
        res = m.execute(order(), state(bid=99.0, ask=100.0, book=book([(99.0, 1)], [])))
        assert res.fills == []
        assert res.rejected == "no_depth"


# ---- TESTS 2 & 3: bid/ask fallback --------------------------------------------------------------

class TestLevel2Quote:
    def test_buy_fills_at_or_above_the_ask(self):
        m = ExecutionModel()
        res = m.execute(order(), state(bid=99.0, ask=100.0))
        assert res.execution_level_used == 2
        assert res.avg_price >= 100.0

    def test_sell_fills_at_or_below_the_bid(self):
        m = ExecutionModel()
        res = m.execute(order(side="SELL", decision=100.5), state(bid=100.0, ask=101.0))
        assert res.execution_level_used == 2
        assert res.avg_price <= 100.0

    def test_degrading_from_l1_is_recorded_not_silent(self):
        m = ExecutionModel(ExecutionConfig(level=1))
        res = m.execute(order(), state(bid=99.0, ask=100.0))
        assert res.execution_level_used == 2
        assert "no book" in res.level_reason


# ---- LEVEL 3 -------------------------------------------------------------------------------------

class TestLevel3Fallback:
    def test_never_fills_at_the_candle_close(self):
        m = ExecutionModel()
        res = m.execute(order(decision=99.5), state(last=99.5))
        assert res.execution_level_used == 3
        assert res.avg_price > 99.5, "a market buy may not fill at the reference price"

    def test_sell_is_also_a_cost(self):
        m = ExecutionModel()
        res = m.execute(order(side="SELL", decision=99.5), state(last=99.5))
        assert res.avg_price < 99.5

    def test_volatility_widens_the_spread(self):
        m = ExecutionModel()
        calm = m.execute(order(), state(last=100.0, atr=0.01)).avg_price
        wild = m.execute(order(), state(last=100.0, atr=5.0)).avg_price
        assert wild > calm

    def test_size_component_needs_a_liquidity_proxy(self):
        m = ExecutionModel(ExecutionConfig(size_component=1.0))
        no_proxy = m.execute(order(qty=10.0), state(last=100.0)).avg_price
        with_proxy = m.execute(order(qty=10.0), state(last=100.0, quote_volume=1000.0)).avg_price
        assert with_proxy > no_proxy

    def test_stress_multiplier_increases_cost(self):
        base = ExecutionModel(ExecutionConfig(slippage_mult=1.0)).execute(order(), state()).avg_price
        worse = ExecutionModel(ExecutionConfig(slippage_mult=2.0)).execute(order(), state()).avg_price
        assert worse > base


# ---- TESTS 4 & 5 & 10: limit orders and liquidity role -------------------------------------------

class TestLimitOrders:
    def test_resting_limit_that_is_never_reached_does_not_fill(self):
        m = ExecutionModel()
        o = order(otype="LIMIT", limit=90.0, qty=1.0)
        res = m.execute(o, state(last=100.0, bar_range=1.0))     # range 99.5..100.5, never 90
        assert res.fills == []
        assert res.rejected == "resting"
        assert res.role is None, "a resting order has no liquidity role"
        assert o.role is None

    def test_crossing_limit_buy_is_taker(self):
        m = ExecutionModel()
        res = m.execute(order(otype="LIMIT", limit=101.0, qty=1.0), state(bid=99.0, ask=100.0))
        assert res.role == "TAKER"
        assert res.avg_price >= 100.0

    def test_limit_traded_through_fills_as_maker_at_its_price(self):
        m = ExecutionModel()
        res = m.execute(order(otype="LIMIT", limit=99.0, qty=1.0), state(last=100.0, bar_range=4.0))
        assert res.role == "MAKER"
        assert res.avg_price == pytest.approx(99.0)
        assert res.spread_cost == 0.0 and res.impact_cost == 0.0

    def test_touching_the_limit_exactly_is_not_a_fill(self):
        m = ExecutionModel()
        # range is exactly 98.0..102.0; a buy limit at 98.0 is only touched, never traded through
        res = m.execute(order(otype="LIMIT", limit=98.0, qty=1.0), state(last=100.0, bar_range=4.0))
        assert res.rejected == "resting"

    def test_maker_and_taker_fees_differ(self):
        fees = FeeSchedule(maker_rate=0.0002, taker_rate=0.0005)
        m = ExecutionModel(fees=fees)
        maker = m.execute(order(otype="LIMIT", limit=99.0, qty=1.0), state(last=100.0, bar_range=4.0))
        taker = m.execute(order(qty=1.0), state(bid=99.0, ask=100.0))
        assert m.fee_for(maker) == pytest.approx(99.0 * 0.0002)
        assert m.fee_for(taker) == pytest.approx(taker.avg_price * 0.0005)
        assert m.fee_for(maker) < m.fee_for(taker)

    def test_force_taker_stress_reprices_a_maker_fill(self):
        fees = FeeSchedule(maker_rate=0.0002, taker_rate=0.0005)
        m = ExecutionModel(ExecutionConfig(force_taker=True), fees=fees)
        res = m.execute(order(otype="LIMIT", limit=99.0, qty=1.0), state(last=100.0, bar_range=4.0))
        assert res.role == "MAKER"
        assert m.fee_for(res) == pytest.approx(99.0 * 0.0005)


# ---- TEST 7 & 8: latency and look-ahead -----------------------------------------------------------

class TestLatency:
    def test_execution_before_execute_at_is_refused(self):
        m = ExecutionModel()
        o = Order(SYM, "BUY", 1.0, decision_price=100.0, signal_ts=T0, execute_at=T0 + 400)
        o.submit(T0)
        res = m.execute(o, state(ts=T0 + 100, last=100.0))
        assert res.rejected == "before_execute_at"
        assert res.fills == []

    def test_a_fill_cannot_be_recorded_before_execute_at(self):
        from app.execution.orders import PartialFill
        o = Order(SYM, "BUY", 1.0, execute_at=T0 + 400)
        with pytest.raises(ValueError, match="precedes execute_at"):
            o.add_fill(PartialFill(100.0, 1.0, T0 + 100, "TAKER"))

    def test_latency_moves_the_fill_to_the_next_bar(self):
        """A signal on bar N's close fills against bar N+1's open, not bar N's close."""
        bars = rising()
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1,
                         execution=ExecutionConfig(signal_latency_ms=250, order_latency_ms=150))
        res = e.run(OneShot, bars)
        entry = next(f for f in res.fills if f.kind == "entry")
        signalled = [c for c in bars if c.close_time < entry.ts]
        assert signalled, "entry did not move past its signal bar"
        assert entry.ts > signalled[-1].close_time - 1
        assert entry.meta["latency_ms"] >= 400

    def test_zero_latency_is_still_not_a_free_fill(self):
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1,
                         execution=ExecutionConfig(signal_latency_ms=0, order_latency_ms=0))
        res = e.run(OneShot, rising())
        entry = next(f for f in res.fills if f.kind == "entry")
        assert entry.price > entry.ref_price, "a market buy still pays the modelled spread"

    def test_latency_makes_a_rising_entry_worse(self):
        fast = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1,
                            execution=ExecutionConfig(signal_latency_ms=0, order_latency_ms=0))
        slow = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1,
                            execution=ExecutionConfig(signal_latency_ms=250, order_latency_ms=150))
        a = next(f for f in fast.run(OneShot, rising()).fills if f.kind == "entry")
        b = next(f for f in slow.run(OneShot, rising()).fills if f.kind == "entry")
        assert b.price >= a.price, "chasing an uptrend after a delay cannot be cheaper"


# ---- TEST 9: slippage counted exactly once ---------------------------------------------------------

class TestSlippageCountedOnce:
    def test_three_terms_decompose_total_slippage(self):
        m = ExecutionModel(ExecutionConfig(size_component=2.0))
        res = m.execute(order(qty=5.0, decision=100.0), state(last=100.0, atr=1.0, quote_volume=500.0))
        parts = res.latency_cost + res.spread_cost + res.impact_cost
        assert parts == pytest.approx(res.slippage_usdt, rel=1e-9)

    def test_latency_term_captures_the_move_in_flight(self):
        """decision at 100, market at 100.5 when the order lands -> 0.5/unit is latency, not spread."""
        m = ExecutionModel()
        res = m.execute(order(qty=2.0, decision=100.0), state(last=100.5))
        assert res.latency_cost == pytest.approx(1.0)
        assert res.latency_cost + res.spread_cost + res.impact_cost == pytest.approx(res.slippage_usdt)

    def test_book_walk_decomposes_too(self):
        m = ExecutionModel()
        res = m.execute(order(decision=99.5),
                        state(bid=99.0, ask=100.0, book=book([(99.0, 5)], [(100.0, 1), (101.0, 2)])))
        parts = res.latency_cost + res.spread_cost + res.impact_cost
        assert parts == pytest.approx(res.slippage_usdt, rel=1e-9)

    def test_replay_charges_slippage_once(self):
        """The wallet must reconcile with gross - fees - slippage + funding, not gross - 2x slippage."""
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1)
        res = e.run(OneShot, rising())
        m = mx.compute("T01", res.trades, res.fills, res.equity, res.starting_equity)
        rebuilt = m.gross_pnl - m.fees_paid - m.slippage_cost + m.funding_paid
        assert m.net_profit == pytest.approx(rebuilt, abs=1e-9)

    def test_execution_level_is_recorded_on_every_fill(self):
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1)
        res = e.run(OneShot, rising())
        for f in res.fills:
            if f.kind == "funding":
                continue
            assert f.meta.get("execution_level") in (1, 2, 3)
            assert f.meta.get("liquidity_role") in ("MAKER", "TAKER")


# ---- maintenance-margin brackets --------------------------------------------------------------------

class TestBrackets:
    def test_bracket_one_is_not_the_exchangeinfo_default(self):
        t = BracketTable.fallback()
        assert t.mmr("BTCUSDT", 400) == pytest.approx(0.004)
        assert t.mmr("BTCUSDT", 400) != 0.025, "2.5% is bracket 6, not the competition-size rate"

    def test_mmr_rises_with_notional(self):
        t = BracketTable.fallback()
        rates = [t.mmr("BTCUSDT", n) for n in (100, 500_000, 5_000_000, 50_000_000)]
        assert rates == sorted(rates)
        assert len(set(rates)) > 1, "maintenance margin must not be one constant"

    def test_cumulative_amount_keeps_maintenance_continuous(self):
        t = BracketTable.fallback()
        just_below = t.maintenance_margin("BTCUSDT", 299_999)
        just_above = t.maintenance_margin("BTCUSDT", 300_001)
        assert just_above == pytest.approx(just_below, rel=0.01), "a tier edge must not jump"

    def test_unknown_symbol_is_conservative(self):
        t = BracketTable({})
        assert t.mmr("NEWCOIN", 100) >= 0.005

    def test_liquidation_distance_follows_the_bracket(self):
        """At 20x a 0.4% MMR liquidates ~4.6% away, not ~2.5% as the wrong constant implied."""
        t = BracketTable.fallback()
        mmr = t.mmr("BTCUSDT", 400)
        assert 1 / 20 - mmr == pytest.approx(0.046, abs=1e-6)

    def test_replay_uses_bracket_mmr_not_rules_mmr(self):
        misleading = {SYM: MarketRules(SYM, 0.01, 1e-6, 1e-6, 1.0, 0.025, 2, 6)}
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=misleading, seed=1)
        assert e.exits.mmr(SYM) == pytest.approx(0.004)


# ---- TEST 8 (continued): the executing bar's own extremes are not visible ---------------------------

class TestNoIntrabarLookAhead:
    def test_entry_state_uses_the_previous_bar_not_the_executing_one(self):
        """At execution time the current bar's high/low/volume have not happened yet."""
        seen: list[MarketState] = []
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1)
        real = e._market_state

        def spy(bar, price, ts, tf="1m", complete=False):
            st = real(bar, price, ts, tf, complete)
            if not complete:
                seen.append((bar, st))
            return st

        e._market_state = spy
        e.run(OneShot, rising())
        assert seen, "no entry was priced"
        for bar, st in seen:
            if st.bar_range is not None:
                assert st.bar_range != pytest.approx(abs(bar.high - bar.low)) or bar.high == bar.low, \
                    "execution saw the range of the bar it was executing against"

    def test_first_bar_has_no_range_proxy_at_all(self):
        e = ReplayEngine(settings_factory(balance=20.0), [SYM], rules=big_rules(), seed=1)
        bars = rising()
        st = e._market_state(bars[0], bars[0].open, bars[0].open_time)
        assert st.bar_range is None and st.quote_volume is None


# ---- spread calibration ----------------------------------------------------------------------------

class TestSpreadCalibration:
    """Measured against production quotes on 2026-09-22; see ExecutionConfig's docstring."""

    @pytest.mark.parametrize("last,tick,expected_half_bps", [
        (85966.0, 0.1, 0.0058),      # BTCUSDT
        (2750.0, 0.01, 0.0182),      # ETHUSDT
        (117.3, 0.01, 0.4262),       # SOLUSDT
    ])
    def test_half_spread_is_half_a_tick(self, last, tick, expected_half_bps):
        m = ExecutionModel(ExecutionConfig(base_slippage_bps=0.0, vol_component=0.0))
        got = m._half_spread_bps(MarketState(SYM, T0, last, tick=tick))
        assert got == pytest.approx(expected_half_bps, rel=0.01)

    def test_a_majors_round_trip_costs_single_digit_bps_not_tens(self):
        """The regression that motivated the calibration: 0.5 * ATR/price gave ~23 bps a side."""
        m = ExecutionModel()
        st = MarketState(SYM, T0, 85966.0, tick=0.1, atr=85.0)   # ATR ~10 bps of price
        assert m._half_spread_bps(st) < 1.0

    def test_volatility_still_widens_but_does_not_dominate(self):
        m = ExecutionModel()
        calm = m._half_spread_bps(MarketState(SYM, T0, 117.3, tick=0.01, atr=0.05))
        wild = m._half_spread_bps(MarketState(SYM, T0, 117.3, tick=0.01, atr=0.50))
        assert wild > calm
        assert wild < 4 * calm, "volatility must modulate the spread, not replace it"

    def test_floor_applies_when_the_tick_is_unrealistically_fine(self):
        m = ExecutionModel(ExecutionConfig(vol_component=0.0))
        got = m._half_spread_bps(MarketState(SYM, T0, 100.0, tick=1e-8))
        assert got == pytest.approx(0.1)
