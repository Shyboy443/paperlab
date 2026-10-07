"""Pure net-delta planner and backstop maths (app/exchange/paper_router.py)."""
from __future__ import annotations

import itertools

import pytest

from app.exchange.paper_router import CID_BACKSTOP, backstop_distance, backstop_plan, plan
from tests.conftest import BTC_RULES

SYM = "BTCUSDT"
PX = 65000.0


@pytest.fixture
def cids():
    counter = itertools.count(1)
    return lambda: f"cid-{next(counter)}"


def one(intents):
    assert len(intents) == 1, intents
    return intents[0]


class TestPlan:
    def test_net_zero_sends_nothing(self, cids):
        intents, residual = plan(SYM, 0.0, 0.0, PX, BTC_RULES, cid_factory=cids)
        assert intents == [] and residual == 0.0

    def test_already_in_sync_sends_nothing(self, cids):
        intents, residual = plan(SYM, 0.01, 0.01, PX, BTC_RULES, cid_factory=cids)
        assert intents == [] and residual == pytest.approx(0.0)

    def test_open_from_flat_is_one_non_reduce_buy(self, cids):
        intents, residual = plan(SYM, 0.01, 0.0, PX, BTC_RULES, cid_factory=cids)
        it = one(intents)
        assert it.side == "BUY" and it.qty == pytest.approx(0.01)
        assert it.reduce_only is False
        assert it.purpose == "net_delta" and it.symbol == SYM and it.ref_price == PX
        assert it.client_id == "cid-1"
        assert residual == pytest.approx(0.0)

    def test_open_short_from_flat_is_one_non_reduce_sell(self, cids):
        it = one(plan(SYM, -0.01, 0.0, PX, BTC_RULES, cid_factory=cids)[0])
        assert it.side == "SELL" and it.qty == pytest.approx(0.01) and it.reduce_only is False

    def test_reduce_is_reduce_only_sell(self, cids):
        intents, residual = plan(SYM, 0.004, 0.01, PX, BTC_RULES, cid_factory=cids)
        it = one(intents)
        assert it.side == "SELL" and it.qty == pytest.approx(0.006)
        assert it.reduce_only is True
        assert residual == pytest.approx(0.0)

    def test_adding_to_a_position_is_not_reduce_only(self, cids):
        it = one(plan(SYM, 0.02, 0.01, PX, BTC_RULES, cid_factory=cids)[0])
        assert it.side == "BUY" and it.qty == pytest.approx(0.01) and it.reduce_only is False

    def test_flip_splits_into_reduce_then_open(self, cids):
        intents, residual = plan(SYM, -0.006, 0.01, PX, BTC_RULES, cid_factory=cids)
        assert len(intents) == 2
        first, second = intents
        assert first.side == "SELL" and first.qty == pytest.approx(0.01) and first.reduce_only is True
        assert second.side == "SELL" and second.qty == pytest.approx(0.006) and second.reduce_only is False
        assert first.client_id != second.client_id
        assert residual == pytest.approx(0.0)

    def test_full_close_is_reduce_only(self, cids):
        it = one(plan(SYM, 0.0, -0.01, PX, BTC_RULES, cid_factory=cids)[0])
        assert it.side == "BUY" and it.qty == pytest.approx(0.01) and it.reduce_only is True

    def test_sub_step_delta_stays_in_the_residual_and_folds_into_the_next_plan(self, cids):
        intents, residual = plan(SYM, 1.00005, 1.0, PX, BTC_RULES, cid_factory=cids)
        assert intents == []
        assert residual == pytest.approx(0.00005)
        # half a step more on the next pass: 0.00005 + 0.00005 -> one full step joins the order
        intents, residual = plan(SYM, 1.00105, 1.0, PX, BTC_RULES, residual=residual, cid_factory=cids)
        it = one(intents)
        assert it.side == "BUY" and it.qty == pytest.approx(0.0011)
        assert abs(residual) < 1e-9

    def test_below_min_notional_delta_waits(self, cids):
        intents, residual = plan(SYM, 0.0105, 0.01, PX, BTC_RULES, cid_factory=cids)  # 0.0005 BTC = $32.5 < $50
        assert intents == []
        assert residual == pytest.approx(0.0005)

    def test_full_close_of_a_tiny_position_is_sent_reduce_only(self, cids):
        intents, residual = plan(SYM, 0.0, 0.0005, PX, BTC_RULES, cid_factory=cids)
        it = one(intents)
        assert it.side == "SELL" and it.qty == pytest.approx(0.0005) and it.reduce_only is True
        assert residual == pytest.approx(0.0)

    def test_delta_rounds_toward_zero_to_the_step(self, cids):
        it = one(plan(SYM, 0.012345, 0.0, PX, BTC_RULES, cid_factory=cids)[0])
        assert it.qty == pytest.approx(0.0123)

    def test_default_cid_factory_produces_unique_net_delta_ids(self):
        a = one(plan(SYM, 0.01, 0.0, PX, BTC_RULES)[0])
        b = one(plan(SYM, 0.01, 0.0, PX, BTC_RULES)[0])
        assert a.client_id.startswith("plb-nd-") and b.client_id.startswith("plb-nd-")
        assert a.client_id != b.client_id


class TestBackstopDistance:
    def test_reference_values(self):
        assert backstop_distance(15, 0.004) == pytest.approx(0.0439, abs=5e-5)
        assert backstop_distance(25, 0.025) == pytest.approx(0.0105, abs=5e-5)

    def test_floor_is_0_4_pct(self):
        assert backstop_distance(50, 0.02) == pytest.approx(0.004)
        assert backstop_distance(50, 0.05) == pytest.approx(0.004)
        assert backstop_distance(25, 0.025, min_pct=0.02) == pytest.approx(0.02)

    def test_decreasing_with_leverage(self):
        dists = [backstop_distance(lev, 0.004) for lev in (5, 10, 15, 20, 25, 50)]
        assert dists == sorted(dists, reverse=True)
        assert all(d >= 0.004 for d in dists)

    def test_factor_and_formula(self):
        assert backstop_distance(20, 0.01, factor=0.5) == pytest.approx((1 / 20 - 0.01) * 0.5)


class TestBackstopPlan:
    def test_long_net_gets_sell_stop_below_entry_rounded_down(self):
        it = backstop_plan(SYM, 0.01, 65000.0, 65100.0, 15, 0.004, BTC_RULES)
        assert it is not None
        pct = backstop_distance(15, 0.004)
        assert it.side == "SELL" and it.close_position is True and it.reduce_only is True
        assert it.purpose == "backstop" and it.client_id.startswith(CID_BACKSTOP)
        assert it.qty == pytest.approx(0.01)
        assert it.stop_price == pytest.approx(BTC_RULES.round_price(65000.0 * (1 - pct), "down"))
        assert it.stop_price < 65000.0 * (1 - pct) + 1e-9
        assert it.stop_price == round(it.stop_price, 1), "aligned to the 0.1 tick"

    def test_short_net_gets_buy_stop_above_entry_rounded_up(self):
        it = backstop_plan(SYM, -0.01, 65000.0, 64900.0, 15, 0.004, BTC_RULES)
        assert it is not None
        pct = backstop_distance(15, 0.004)
        assert it.side == "BUY" and it.close_position is True
        assert it.stop_price == pytest.approx(BTC_RULES.round_price(65000.0 * (1 + pct), "up"))
        assert it.stop_price > 65000.0 * (1 + pct) - 1e-9

    def test_uses_mark_when_exchange_entry_is_missing(self):
        pct = backstop_distance(15, 0.004)
        it = backstop_plan(SYM, 0.01, None, 66000.0, 15, 0.004, BTC_RULES)
        assert it is not None
        assert it.stop_price == pytest.approx(BTC_RULES.round_price(66000.0 * (1 - pct), "down"))
        assert it.ref_price == 66000.0
        zero_entry = backstop_plan(SYM, 0.01, 0.0, 66000.0, 15, 0.004, BTC_RULES)
        assert zero_entry is not None and zero_entry.stop_price == it.stop_price

    def test_zero_net_returns_none(self):
        assert backstop_plan(SYM, 0.0, 65000.0, 65000.0, 15, 0.004, BTC_RULES) is None
        assert backstop_plan(SYM, 0.00001, 65000.0, 65000.0, 15, 0.004, BTC_RULES) is None

    def test_no_reference_price_returns_none(self):
        assert backstop_plan(SYM, 0.01, None, None, 15, 0.004, BTC_RULES) is None

    def test_leverage_moves_the_stop_closer(self):
        far = backstop_plan(SYM, 0.01, 65000.0, None, 10, 0.004, BTC_RULES)
        near = backstop_plan(SYM, 0.01, 65000.0, None, 25, 0.004, BTC_RULES)
        assert far is not None and near is not None
        assert far.stop_price < near.stop_price < 65000.0
