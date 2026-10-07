"""The min-notional audit, pinned: what Binance USD-M actually enforces, and nothing more.

Evidence (see docs/MIN_NOTIONAL_AUDIT.md):
* MIN_NOTIONAL "defines the minimum notional value allowed for an order ... price * quantity. Since
  MARKET orders have no price, the mark price is used."  -- USD-M common definitions
* -4164 MIN_NOTIONAL: "Order's notional must be no smaller than 5.0 (unless you choose reduce only)"
* Fills below the minimum are routine on the production tape (0.001 ETH fills, 11% of ETH trades).
"""
from __future__ import annotations

import pytest

from app.execution.config import ExecutionConfig, FeeSchedule
from app.execution.model import ExecutionModel, MarketState
from app.execution.orders import Order, remainder_policy
from app.core.types import BookSnapshot, MarketRules
from app.exchange.paper_router import plan
from tests.conftest import BTC_RULES

ETH = MarketRules("ETHUSDT", 0.01, 0.001, 0.001, 20.0, 0.004, 2, 3)


class TestMarketRules:
    def test_an_order_must_clear_min_notional(self):
        assert not ETH.qty_ok(0.005, 2750.0)            # 13.75 USDT < 20
        assert ETH.qty_ok(0.008, 2750.0)                # 22.00 USDT

    def test_reduce_only_is_exempt_from_min_notional_but_not_from_min_qty(self):
        assert ETH.qty_ok(0.001, 2750.0, reduce_only=True)   # 2.75 USDT, reduce-only
        assert not ETH.qty_ok(0.0005, 2750.0, reduce_only=True)

    def test_min_order_qty_is_the_first_lot_at_or_above_both_minimums(self):
        assert ETH.min_order_qty(2750.0) == pytest.approx(0.008)
        assert ETH.min_order_notional(2750.0) == pytest.approx(22.0)
        btc = MarketRules("BTCUSDT", 0.1, 0.001, 0.001, 50.0)
        assert btc.min_order_qty(86360.0) == pytest.approx(0.001)   # minQty binds, not minNotional

    def test_safety_multiplier_scales_only_the_notional_floor(self):
        assert ETH.min_order_notional(2750.0, safety=2.0) == pytest.approx(0.015 * 2750.0)


def _book_state(qty_at_touch: float) -> MarketState:
    book = BookSnapshot("ETHUSDT", bids=[(2749.9, 5.0)], asks=[(2750.0, qty_at_touch)], ts=0)
    return MarketState("ETHUSDT", ts=10, last=2750.0, bid=2749.9, ask=2750.0, book=book, tick=0.01)


class TestRemainder:
    """What happens to the unfilled part is decided by order type and time in force -- never size."""

    def test_policies(self):
        assert remainder_policy("MARKET") == "EXPIRE"
        assert remainder_policy("LIMIT", "IOC") == "EXPIRE"
        assert remainder_policy("LIMIT", "FOK") == "ALL_OR_NONE"
        assert remainder_policy("LIMIT", "GTX") == "REJECT_IF_TAKING"
        assert remainder_policy("LIMIT", "GTC") == "REST"

    def test_a_valid_market_order_may_fill_below_min_notional_and_the_rest_expires(self):
        order = Order("ETHUSDT", "BUY", 0.010, "MARKET", decision_price=2750.0, execute_at=0)
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(0.003))
        assert res.filled_qty == pytest.approx(0.003)          # 8.25 USDT, below the 20 USDT minimum
        assert res.partial and res.remainder == "EXPIRED" and not res.rejected

    def test_gtc_remainder_rests_even_below_min_notional(self):
        order = Order("ETHUSDT", "BUY", 0.010, "LIMIT", limit_price=2750.0, decision_price=2750.0,
                      execute_at=0, time_in_force="GTC")
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(0.009))
        assert res.remainder == "RESTING"                       # 0.001 ETH = 2.75 USDT left resting

    def test_ioc_remainder_expires(self):
        order = Order("ETHUSDT", "BUY", 0.010, "LIMIT", limit_price=2750.0, decision_price=2750.0,
                      execute_at=0, time_in_force="IOC")
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(0.004))
        assert res.remainder == "EXPIRED" and res.filled_qty == pytest.approx(0.004)

    def test_fok_that_cannot_fill_completely_has_no_fills(self):
        order = Order("ETHUSDT", "BUY", 0.010, "LIMIT", limit_price=2750.0, decision_price=2750.0,
                      execute_at=0, time_in_force="FOK")
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(0.004))
        assert res.fills == [] and res.rejected == "fok_not_fully_fillable"

    def test_post_only_that_would_take_is_rejected(self):
        order = Order("ETHUSDT", "BUY", 0.010, "LIMIT", limit_price=2750.0, decision_price=2750.0,
                      execute_at=0, time_in_force="GTX")
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(1.0))
        assert res.rejected == "post_only_would_take" and res.fills == []

    def test_a_full_fill_leaves_no_remainder(self):
        order = Order("ETHUSDT", "BUY", 0.010, "MARKET", decision_price=2750.0, execute_at=0)
        res = ExecutionModel(ExecutionConfig(level=1)).execute(order, _book_state(1.0))
        assert not res.partial and res.remainder == ""


class TestPartialEntryIsBookedAtTheFilledQuantity:
    def test_open_position_uses_the_filled_qty(self, portfolio):
        from tests.test_portfolio import decision, entry_signal, REF
        portfolio.ensure_wallet("S01", 500.0)
        pos, fill = portfolio.open_position(entry_signal(), decision(0.01), REF, simulated=True,
                                            qty=0.004)
        assert pos.qty == pytest.approx(0.004) and fill.qty == pytest.approx(0.004)
        assert pos.initial_risk_usd == pytest.approx(0.004 * abs(fill.price - pos.stop))


class TestRouterReduceOnly:
    PX = 65000.0

    def test_a_small_reducing_delta_goes_out_reduce_only(self):
        intents, residual = plan("BTCUSDT", 0.0095, 0.01, self.PX, BTC_RULES,
                                 cid_factory=lambda: "c")          # 0.0005 BTC = 32.50 USDT < 50
        assert len(intents) == 1 and intents[0].reduce_only and intents[0].qty == pytest.approx(0.0005)
        assert residual == pytest.approx(0.0)

    def test_a_small_opening_delta_still_waits(self):
        intents, residual = plan("BTCUSDT", 0.0105, 0.01, self.PX, BTC_RULES, cid_factory=lambda: "c")
        assert intents == [] and residual == pytest.approx(0.0005)

    def test_a_flip_whose_opening_leg_is_below_min_notional_keeps_it_as_residual(self):
        intents, residual = plan("BTCUSDT", -0.0005, 0.01, self.PX, BTC_RULES, cid_factory=lambda: "c")
        assert len(intents) == 1 and intents[0].reduce_only and intents[0].qty == pytest.approx(0.01)
        assert residual == pytest.approx(-0.0005)


class TestReplayCharges:
    """The fee schedule must be what the wallet is charged -- and the gate must read the same rate."""

    def _engine(self, **kw):
        from app.backtest.replay import ReplayEngine
        from tests.conftest import RULES, settings_factory
        return ReplayEngine(settings_factory(balance=20), ["SOLUSDT"], rules=RULES, **kw)

    def test_schedule_rates_are_charged_by_default(self):
        eng = self._engine()
        assert eng.portfolio.fee_for(1000.0) == pytest.approx(0.50)      # 0.05% taker
        assert eng.portfolio.fee_for(1000.0, maker=True) == pytest.approx(0.20)
        assert eng.risk.settings.taker_fee == pytest.approx(0.0005)

    def test_a_stress_fee_schedule_is_actually_charged(self):
        eng = self._engine(fees=FeeSchedule(maker_rate=0.0003, taker_rate=0.00075))
        assert eng.portfolio.fee_for(1000.0) == pytest.approx(0.75)

    def test_the_legacy_source_reproduces_old_runs(self):
        eng = self._engine(fee_source="legacy")
        assert eng.portfolio.fee_for(1000.0) == pytest.approx(0.40)      # frozen pre-2026-09-23 0.04%

    def test_an_unknown_source_is_refused(self):
        with pytest.raises(ValueError):
            self._engine(fee_source="guess")


class TestHardRiskCeiling:
    def test_an_oversized_entry_is_shrunk_to_the_ceiling(self):
        from app.backtest.replay import ReplayEngine
        from app.core.risk import StrategyMeta
        from app.core.types import RiskDecision, Signal, TakeProfit
        from tests.conftest import RULES, settings_factory
        eng = ReplayEngine(settings_factory(balance=20), ["SOLUSDT"], rules=RULES, max_risk_pct=0.02)
        eng.portfolio.ensure_wallet("T01", 20.0)
        eng.ctx.set_price("SOLUSDT", 100.0, 0)
        sig = Signal("T01", "SOLUSDT", "entry", "long", 0, "5m", 100.0, stop=95.0,
                     take_profits=[TakeProfit(115.0, 1.0)])
        d = RiskDecision(True, "ok", qty=1.0, notional=100.0, margin=5.0, leverage=20, risk_usd=5.0)
        assert eng._cap_risk(d, sig, StrategyMeta("T01", True, 1.0, 20, 1, 0.0, True))
        assert d.risk_usd <= 0.40 + 1e-9 and d.qty == pytest.approx(0.08)

    def test_needed_leverage_is_the_smallest_that_fits(self):
        from app.backtest.replay import ReplayEngine
        from app.core.risk import StrategyMeta
        from app.core.types import RiskDecision
        from tests.conftest import RULES, settings_factory
        eng = ReplayEngine(settings_factory(balance=20), ["SOLUSDT"], rules=RULES,
                           leverage_policy="needed")
        eng.portfolio.ensure_wallet("T01", 20.0)
        eng.portfolio.set_allocation("T01", 20.0)
        d = RiskDecision(True, "ok", qty=0.5, notional=50.0, margin=2.5, leverage=20, risk_usd=0.2)
        eng._fit_leverage(d, StrategyMeta("T01", True, 1.0, 20, 1, 0.0, True))
        assert d.leverage == 3 and d.margin == pytest.approx(50.0 / 3)


class TestArenaStorage:
    def test_a_run_and_its_bots_round_trip(self, tmp_path):
        from app.core.storage import Storage
        st = Storage(tmp_path / "a.db")
        st.start_arena_run({"run_id": "r1", "created_ts": 1, "status": "running", "label": "x",
                            "symbols": ["SOLUSDT"], "timeframes": ["5m"], "active_bots": 1,
                            "not_entered": 0, "advanced": 0, "min_active_bots": 10,
                            "config": {"a": 1}, "summary": {"s": 1}, "preflight": {"p": 1}})
        st.save_arena_bot("r1", {"key": "S02-SOLUSDT-5m@20x-v1", "strategy_id": "S02",
                                 "symbol": "SOLUSDT", "timeframe": "5m", "rank": 1,
                                 "state": "FAILED", "metrics": {"net_profit": -1.0, "trades": 50},
                                 "equity": [[1, 20.0]], "trades_ledger": [{"net": -1}]})
        st.update_arena_run("r1", status="complete", finished_ts=2, summary_json={"s": 2})
        run = st.arena_run("r1", heavy=True)
        assert run["status"] == "complete" and run["summary"] == {"s": 2}
        assert run["config"] == {"a": 1} and run["preflight"] == {"p": 1}
        light = st.arena_bots("r1")
        assert light[0]["key"] == "S02-SOLUSDT-5m@20x-v1" and "equity" not in light[0]
        assert st.arena_bot("r1", "S02-SOLUSDT-5m@20x-v1")["trades_ledger"] == [{"net": -1}]
