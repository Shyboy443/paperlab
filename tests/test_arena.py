"""Specialist Bot Arena: one strategy, one coin, one timeframe, one wallet."""
from __future__ import annotations

import pytest

from app.competition.arena import Arena, ArenaConfig, MIN_TRADES_BY_TF
from app.competition.bots import (AGGRESSIVE, CONSERVATIVE, AttackPolicy, BotSpec, attack_state,
                                  candidates, native_timeframe, preflight, select, signal_quality,
                                  sizing_window, supported_timeframes, timeframe_table)
from app.core.types import MarketRules
from tests.test_competition import OneShot


class ArenaBot(OneShot):
    """The arena entrant used throughout: OneShot is a 1m test strategy and 1m is deliberately not
    an arena signal timeframe, so it would be refused before any other check could run."""
    id = "T01"
    timeframes = ("5m",)


# Production filters as published on 2026-09-23 (fapi/v1/exchangeInfo).
SOL = MarketRules("SOLUSDT", 0.01, 0.01, 0.01, 5.0, 0.005, 4, 2)
BTC = MarketRules("BTCUSDT", 0.1, 0.001, 0.001, 50.0, 0.004, 2, 3)
ETH = MarketRules("ETHUSDT", 0.01, 0.001, 0.001, 20.0, 0.004, 2, 3)
SOL_PX, BTC_PX, ETH_PX = 76.58, 64230.6, 1884.0      # Jul-Aug 2026 median closes
UNIVERSE = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]


def pf(spec, cls=ArenaBot, rules=SOL, bars=100_000, price=SOL_PX, **kw):
    return preflight(spec, cls, rules, UNIVERSE, bars, price=price, **kw)


class TestBotIdentity:
    def test_key_names_strategy_symbol_timeframe_leverage_and_version(self):
        assert BotSpec("S08", "SOLUSDT", "5m", 20).key == "S08-SOLUSDT-5m@20x-v1"

    def test_same_strategy_on_another_coin_is_another_bot(self):
        a = BotSpec("S08", "BTCUSDT", "15m", 10)
        b = BotSpec("S08", "SOLUSDT", "15m", 10)
        assert a.key != b.key and a.version() != b.version()

    def test_same_strategy_on_another_timeframe_is_another_bot(self):
        a = BotSpec("S08", "BTCUSDT", "5m", 10)
        b = BotSpec("S08", "BTCUSDT", "15m", 10)
        assert a.version() != b.version()

    def test_leverage_is_part_of_identity(self):
        assert BotSpec("S08", "BTCUSDT", "15m", 5).version() != \
               BotSpec("S08", "BTCUSDT", "15m", 20).version()

    def test_a_new_strategy_version_is_a_new_bot(self):
        v1 = BotSpec("S08", "SOLUSDT", "5m", 20)
        v2 = BotSpec("S08", "SOLUSDT", "5m", 20, params_version="v2")
        assert v1.key != v2.key and v1.version() != v2.version()

    def test_parameters_change_the_version(self):
        spec = BotSpec("T01", "BTCUSDT", "5m", 10)
        assert spec.version(OneShot.Params(at=5)) != spec.version(OneShot.Params(at=9))


class TestSizingFeasibility:
    """Floor = Binance's own minimum at 1x; ceiling = the fee gate at 1% of 20 USDT."""

    def test_btc_is_bound_by_min_qty_not_min_notional(self):
        w = sizing_window(BTC, AGGRESSIVE, 20, BTC_PX)
        assert w["binding_minimum"] == "minQty"
        assert w["floor"] == pytest.approx(64.23, abs=0.01)
        assert w["ceiling_fee"] == pytest.approx(50.0)
        assert not w["feasible"]
        assert w["min_risk_pct_needed"] == pytest.approx(0.01285, abs=1e-4)

    def test_eth_is_feasible_once_the_2x_rule_is_gone(self):
        w = sizing_window(ETH, AGGRESSIVE, 20, ETH_PX)
        assert w["floor"] == pytest.approx(20.72, abs=0.01)     # 0.011 ETH, the first step >= 20 USDT
        assert w["feasible"]

    def test_eth_was_excluded_by_the_2x_rule_not_by_too_little_risk(self):
        """At the OLD 0.75% risk the fee gate caps ETH at 37.50 USDT: 1x (20.72) fits, 2x (41.45)
        does not. Correcting the exchange rule admits ETH without touching risk at all."""
        old = 0.0075
        assert sizing_window(ETH, AGGRESSIVE, 20, ETH_PX, risk_pct=old)["feasible"]
        assert not sizing_window(ETH, AGGRESSIVE, 20, ETH_PX, risk_pct=old, safety=2.0)["feasible"]

    def test_the_legal_stop_band_is_reported(self):
        w = sizing_window(ETH, AGGRESSIVE, 20, ETH_PX)
        assert w["min_stop_pct"] == pytest.approx(0.004)        # fee gate: 2 x 0.05% / 25%
        assert w["max_stop_pct"] == pytest.approx(0.20 / 20.72, rel=1e-3)

    def test_the_same_book_can_trade_sol(self):
        assert sizing_window(SOL, AGGRESSIVE, 20, SOL_PX)["feasible"]

    def test_the_fee_gate_not_leverage_is_what_binds(self):
        w = sizing_window(BTC, AGGRESSIVE, 20, BTC_PX)
        assert w["ceiling_fee"] < w["ceiling_leverage"], "more leverage cannot lift a fee-gate cap"

    def test_higher_risk_would_admit_btc_but_preflight_uses_the_ordinary_target(self):
        assert sizing_window(BTC, AGGRESSIVE, 20, BTC_PX, risk_pct=0.015)["feasible"]
        p = pf(BotSpec("T01", "BTCUSDT", "5m", 20), rules=BTC, price=BTC_PX)
        assert not p.ok and "unreachable order size" in p.reason and "minQty" in p.reason

    def test_preflight_admits_a_reachable_market(self):
        assert pf(BotSpec("T01", "SOLUSDT", "5m", 20)).ok
        assert pf(BotSpec("T01", "ETHUSDT", "5m", 20), rules=ETH, price=ETH_PX).ok


class TestPreflightReasons:
    def test_missing_history_is_refused(self):
        p = pf(BotSpec("T01", "SOLUSDT", "5m", 10), bars=10)
        assert not p.ok and "insufficient history" in p.reason

    def test_symbol_outside_the_universe_is_refused(self):
        p = pf(BotSpec("T01", "PEPEUSDT", "5m", 10))
        assert not p.ok and "universe" in p.reason

    def test_missing_metadata_is_refused(self):
        p = pf(BotSpec("T01", "SOLUSDT", "5m", 10), rules=None)
        assert not p.ok and "metadata" in p.reason

    def test_missing_price_is_refused(self):
        p = pf(BotSpec("T01", "SOLUSDT", "5m", 10), price=None)
        assert not p.ok and "price" in p.reason

    def test_a_strategy_needing_a_live_feed_is_refused(self):
        class NeedsBook(ArenaBot):
            id = "S17"
            needs_book = True
        p = pf(BotSpec("S17", "SOLUSDT", "5m", 10), cls=NeedsBook)
        assert not p.ok and "order book" in p.reason

    def test_an_unsupported_timeframe_is_refused(self):
        p = pf(BotSpec("T01", "SOLUSDT", "30m", 10))
        assert not p.ok and "timeframe" in p.reason

    def test_a_one_minute_native_strategy_is_refused_with_its_native_timeframe(self):
        class OneMinute(OneShot):
            timeframes = ("1m", "15m")

            def on_candle(self, c, ctx):
                if c.tf != "1m":
                    return []
                return []
        p = pf(BotSpec("T01", "SOLUSDT", "15m", 20), cls=OneMinute)
        assert not p.ok and "native signal timeframe is 1m" in p.reason


class TestTimeframeSupport:
    def test_native_timeframes_are_used_when_nothing_is_declared(self):
        class FiveOnly(OneShot):
            timeframes = ("5m",)
        assert supported_timeframes(FiveOnly) == ("5m",)

    def test_a_one_minute_strategy_has_no_arena_timeframe(self):
        class OneMinute(OneShot):
            timeframes = ("1m",)
        assert supported_timeframes(OneMinute) == ()

    def test_a_context_timeframe_is_not_a_signal_timeframe(self):
        """The bug behind five silent bots: 15m was only S06's context series."""
        class Gated(OneShot):
            timeframes = ("5m", "15m")

            def on_candle(self, c, ctx):
                if c.tf != "5m":
                    return []
                return []
        assert native_timeframe(Gated) == "5m"
        assert supported_timeframes(Gated) == ("5m",)

    def test_an_explicit_declaration_wins(self):
        class Declared(OneShot):
            timeframes = ("5m",)
            supported_timeframes = ("5m", "15m")
        assert supported_timeframes(Declared) == ("5m", "15m")

    def test_the_real_strategies_resolve_to_their_gates(self):
        from app.strategies.registry import load_all
        table = {r["strategy_id"]: r for r in timeframe_table(load_all(strict=True))}
        native = {sid: r["native_timeframe"] for sid, r in table.items()}
        assert {s for s, tf in native.items() if tf == "1m"} == \
            {"S01", "S05", "S08", "S09", "S10", "S15", "S17", "S19"}
        assert {s for s, tf in native.items() if tf == "15m"} == {"S18", "S26", "S27"}
        assert all(tf is not None for tf in native.values())
        assert table["S06"]["context_timeframes"] == ["15m"]
        assert table["S06"]["supported_timeframes"] == ["5m"]


class TestCandidateGeneration:
    def test_one_candidate_per_strategy_coin_timeframe_leverage(self):
        c = candidates({"T01": ArenaBot}, ["SOLUSDT", "BNBUSDT"],
                       timeframes=("5m",), leverages=(5, 10))
        assert len(c) == 4 and len({x.key for x in c}) == 4

    def test_no_candidate_is_generated_on_a_non_native_timeframe(self):
        c = candidates({"T01": ArenaBot}, ["SOLUSDT"], timeframes=("5m", "15m", "30m"))
        assert [x.timeframe for x in c] == ["5m"]

    def test_generation_is_deterministic(self):
        a = [x.key for x in candidates({"T01": ArenaBot}, ["SOLUSDT", "BNBUSDT"], ("5m",), (10,))]
        b = [x.key for x in candidates({"T01": ArenaBot}, ["SOLUSDT", "BNBUSDT"], ("5m",), (10,))]
        assert a == b

    def test_selection_spreads_across_strategies(self):
        specs = [BotSpec(f"S{i:02d}", c, "5m", 10)
                 for i in range(1, 6) for c in ("SOLUSDT", "BNBUSDT", "XRPUSDT")]
        picked = select(specs, 5)
        assert len({p.strategy_id for p in picked}) == 5, \
            "a cap must not hand the whole arena to one strategy"

    def test_selection_spreads_across_coins(self):
        coins = ("ADAUSDT", "BNBUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        specs = [BotSpec(f"S{i:02d}", c, "5m", 20) for i in range(1, 11) for c in coins]
        picked = select(specs, 10)
        per_coin = {c: sum(p.symbol == c for p in picked) for c in coins}
        assert set(per_coin.values()) == {2}, per_coin

    def test_selection_does_not_shortchange_a_rare_timeframe(self):
        specs = [BotSpec(f"S{i:02d}", c, "5m", 20) for i in range(1, 5) for c in ("A", "B", "C")]
        specs += [BotSpec("S09", c, "15m", 20) for c in ("A", "B", "C")]
        picked = select(specs, 7)
        assert sum(p.timeframe == "15m" for p in picked) == 2

    def test_selection_is_deterministic(self):
        specs = [BotSpec(f"S{i:02d}", c, "5m", 20) for i in range(1, 8) for c in ("A", "B", "C")]
        assert [p.key for p in select(specs, 9)] == [p.key for p in select(specs, 9)]


class TestRiskProfile:
    def test_the_research_profile(self):
        assert AGGRESSIVE.starting_balance == 20.0 and AGGRESSIVE.max_leverage == 20
        assert AGGRESSIVE.ordinary_risk_pct == pytest.approx(0.01)
        assert AGGRESSIVE.risk_for("NORMAL") == pytest.approx(0.01)
        assert AGGRESSIVE.risk_for("ATTACK", "STRONG") == pytest.approx(0.015)
        assert AGGRESSIVE.risk_for("ATTACK", "EXCEPTIONAL") == pytest.approx(0.02)
        assert AGGRESSIVE.risk_for("DEFENSIVE") == pytest.approx(0.005)
        assert AGGRESSIVE.risk_for("HALTED", "EXCEPTIONAL") == 0.0

    def test_nothing_exceeds_the_hard_ceiling(self):
        for state in ("ATTACK", "NORMAL", "DEFENSIVE", "HALTED"):
            for q in ("ORDINARY", "STRONG", "EXCEPTIONAL"):
                assert AGGRESSIVE.risk_for(state, q) <= AGGRESSIVE.max_risk_pct + 1e-12

    def test_normal_never_sizes_up_even_on_a_graded_signal(self):
        assert AGGRESSIVE.risk_for("NORMAL", "EXCEPTIONAL") == AGGRESSIVE.risk_for("NORMAL")

    def test_attack_needs_health_drawdown_control_and_a_graded_signal(self):
        assert attack_state(0.5, 0.05, trades=20, quality="STRONG") == "ATTACK"
        assert attack_state(0.5, 0.05, trades=20) == "NORMAL", "an ungraded signal cannot attack"
        assert attack_state(0.5, 0.12, trades=20, quality="STRONG") == "NORMAL", "drawdown too deep"
        assert attack_state(0.5, 0.05, trades=5, quality="STRONG") == "NORMAL", "unproven"
        assert attack_state(0.05, 0.05, trades=20, quality="STRONG") == "NORMAL", "health too weak"

    def test_drawdown_outranks_a_good_streak(self):
        assert attack_state(0.5, 0.20, trades=20, quality="STRONG") == "DEFENSIVE"
        assert attack_state(0.5, 0.35, trades=20, quality="STRONG") == "HALTED"
        assert attack_state(-0.1, 0.02) == "NORMAL"

    def test_a_losing_streak_never_increases_size(self):
        """No martingale: worse expectancy can only move the state down, never up."""
        order = {"HALTED": 0, "DEFENSIVE": 1, "NORMAL": 2, "ATTACK": 3}
        for q in ("ORDINARY", "STRONG"):
            assert order[attack_state(-0.3, 0.02, trades=20, quality=q)] <= \
                   order[attack_state(0.3, 0.02, trades=20, quality=q)]
        assert attack_state(-0.3, 0.02, trades=20) == "DEFENSIVE"

    def test_conservative_profile_is_tighter(self):
        assert CONSERVATIVE.risk_for("ATTACK", "EXCEPTIONAL") < AGGRESSIVE.risk_for("NORMAL")

    def test_quality_is_only_what_the_strategy_said(self):
        class Sig:
            meta = {}
        assert signal_quality(Sig()) == "ORDINARY"
        Sig.meta = {"quality": "strong"}
        assert signal_quality(Sig()) == "STRONG"
        Sig.meta = {"quality": "legendary"}
        assert signal_quality(Sig()) == "ORDINARY"

    def test_the_policy_reports_what_it_decided(self):
        class Sig:
            meta = {"quality": "EXCEPTIONAL", "edge_to_cost": 4.0}
        mult, info = AttackPolicy()(Sig(), {"r": [0.4] * 20, "drawdown": 0.02})
        assert info["attack_state"] == "ATTACK" and mult == pytest.approx(2.0)
        mult, info = AttackPolicy()(Sig(), {"r": [0.4] * 20, "drawdown": 0.40})
        assert info["attack_state"] == "HALTED" and mult == 0.0

    def test_attack_v2_needs_a_passed_cost_gate(self):
        """A graded signal without an edge-to-cost ratio (no cost gate ran) or with too small a one
        is sized as ORDINARY: ATTACK can never be reached around the cost gate."""
        class NoGate:
            meta = {"quality": "EXCEPTIONAL"}

        class Thin:
            meta = {"signal_quality": 0.95, "edge_to_cost": 2.2}
        for sig in (NoGate(), Thin()):
            mult, info = AttackPolicy()(sig, {"r": [0.4] * 20, "drawdown": 0.02})
            assert info["attack_state"] == "NORMAL" and mult == pytest.approx(1.0)


def _arena(min_bots=10, symbols=("SOLUSDT", "BTCUSDT")):
    rules = {"SOLUSDT": SOL, "BTCUSDT": BTC, "ETHUSDT": ETH}
    arena = Arena(object(), ArenaConfig(min_active_bots=min_bots), ["2026-08"],
                  {s: rules[s] for s in symbols})
    arena._history = {"SOLUSDT": (100_000, SOL_PX), "BTCUSDT": (100_000, BTC_PX),
                      "ETHUSDT": (100_000, ETH_PX)}
    return arena


class TestArenaFieldSize:
    def test_too_few_active_bots_declares_no_winner(self):
        run = _arena(10).run({"T01": ArenaBot}, ["SOLUSDT"], UNIVERSE)
        assert run.status == "INSUFFICIENT_COMPETITORS"
        assert run.advanced() == [] and len(run.active) == 1

    def test_not_entered_bots_do_not_count_toward_the_minimum(self):
        run = _arena(1, symbols=("BTCUSDT",)).run({"T01": ArenaBot}, ["BTCUSDT"], UNIVERSE)
        assert run.status == "INSUFFICIENT_COMPETITORS"
        assert len(run.not_entered) == 1 and run.active == []
        assert "unreachable order size" in run.not_entered[0].reason

    def test_the_plan_reports_every_symbol_and_strategy(self):
        run = _arena(1).plan({"T01": ArenaBot}, ["SOLUSDT", "BTCUSDT"], UNIVERSE)
        rows = {r["symbol"]: r for r in run.symbol_table}
        assert rows["SOLUSDT"]["tradeable"] and not rows["BTCUSDT"]["tradeable"]
        assert run.strategy_table[0]["native_timeframe"] == "5m"


class TestAdvancement:
    def _metrics(self, **over):
        from app.competition.metrics import CompetitorMetrics
        m = CompetitorMetrics("T01")
        base = dict(trades=100, net_profit=5.0, expectancy_r=0.2, profit_factor=1.4,
                    max_drawdown_pct=0.15, liquidation_count=0)
        for k, v in {**base, **over}.items():
            setattr(m, k, v)
        return m

    def _arena(self):
        return Arena(object(), ArenaConfig(), ["2026-08"], {"SOLUSDT": SOL})

    def test_the_default_gates(self):
        c = ArenaConfig()
        assert (c.min_profit_factor, c.max_drawdown_pct, c.max_liquidations) == (1.10, 0.35, 0)
        assert dict(c.min_trades) == {"5m": 40, "15m": 20, "30m": 12}

    def test_a_sound_bot_advances(self):
        assert self._arena().judge(BotSpec("T01", "SOLUSDT", "5m", 10), self._metrics())[0] == "ADVANCE"

    def test_pf_just_above_the_gate_advances_and_just_below_fails(self):
        a, spec = self._arena(), BotSpec("T01", "SOLUSDT", "5m", 10)
        assert a.judge(spec, self._metrics(profit_factor=1.10))[0] == "ADVANCE"
        assert a.judge(spec, self._metrics(profit_factor=1.09))[0] == "FAILED"

    def test_liquidation_disqualifies_regardless_of_profit(self):
        state, _, _ = self._arena().judge(
            BotSpec("T01", "SOLUSDT", "5m", 10),
            self._metrics(net_profit=50.0, liquidation_count=1))
        assert state == "DISQUALIFIED"

    def test_sample_requirement_is_lighter_on_slower_timeframes(self):
        arena, m = self._arena(), self._metrics(trades=15)
        assert arena.judge(BotSpec("T01", "SOLUSDT", "5m", 10), m)[0] == "INSUFFICIENT_SAMPLE"
        assert arena.judge(BotSpec("T01", "SOLUSDT", "30m", 10), m)[0] == "ADVANCE"
        assert MIN_TRADES_BY_TF["5m"] > MIN_TRADES_BY_TF["30m"]

    def test_negative_expectancy_fails(self):
        state, _, reasons = self._arena().judge(BotSpec("T01", "SOLUSDT", "5m", 10),
                                                self._metrics(expectancy_r=-0.1))
        assert state == "FAILED" and any("expectancy" in r for r in reasons)

    def test_advancement_is_not_qualification(self):
        """ADVANCE means 'now test it properly', never 'trade it live'."""
        state, _, _ = self._arena().judge(BotSpec("T01", "SOLUSDT", "5m", 10), self._metrics())
        assert state not in ("QUALIFIED", "LIVE_CANDIDATE", "SHADOW_LIVE")


class TestCostEfficiency:
    """Fee-destroyed and gross-negative are different diseases and must never be confused."""

    def m(self, **kw):
        base = dict(trades=100, gross_pnl=2.0, fees_paid=3.0, slippage_cost=0.5, funding_paid=0.0,
                    net_profit=-1.5, total_notional_traded=4000.0, average_holding_ms=3_600_000,
                    average_win=0.3, average_loss=-0.2, win_rate=0.4, expectancy_r=-0.05,
                    liquidation_count=0, turnover=200.0)
        base.update(kw)
        return base

    def test_positive_gross_eaten_by_costs_is_fee_destroyed(self):
        from app.competition.cost_efficiency import cost_efficiency
        ce = cost_efficiency(self.m(), window_days=62)
        assert ce["class"] == "FEE_DESTROYED" and ce["cost_to_edge"] == pytest.approx(1.75)
        assert ce["avg_round_trip_cost_bps"] == pytest.approx(3.5 / 2000 * 1e4)
        assert ce["trades_per_day"] == pytest.approx(100 / 62)

    def test_negative_gross_is_its_own_class(self):
        from app.competition.cost_efficiency import cost_efficiency
        ce = cost_efficiency(self.m(gross_pnl=-1.0, net_profit=-4.5))
        assert ce["class"] == "GROSS_NEGATIVE" and ce["cost_to_edge"] is None

    def test_healthy_and_marginal(self):
        from app.competition.cost_efficiency import cost_efficiency
        assert cost_efficiency(self.m(gross_pnl=10.0, net_profit=6.5))["class"] == "HEALTHY"
        assert cost_efficiency(self.m(gross_pnl=5.0, net_profit=1.5))["class"] == "MARGINAL"
        assert cost_efficiency(self.m(trades=0))["class"] == "NO_TRADES"

    def test_break_even_win_rate(self):
        from app.competition.cost_efficiency import cost_efficiency
        ce = cost_efficiency(self.m(), ledger=[{"pnl": 0.5}, {"pnl": -0.2}, {"pnl": 0.3}, {"pnl": -0.4}])
        assert ce["break_even_win_rate"] == pytest.approx(0.2 / 0.5)
        assert ce["gross_win_rate"] == pytest.approx(0.5)
        assert ce["fees_to_gross_winning"] == pytest.approx(3.0 / 0.8)

    def test_jev_eligibility(self):
        from app.competition.cost_efficiency import cost_efficiency, jev_eligible
        good = self.m(gross_pnl=10.0, net_profit=6.5, expectancy_r=0.1)
        assert jev_eligible(good, cost_efficiency(good))[0]
        few = self.m(trades=9, gross_pnl=10.0, net_profit=6.5)
        ok, why = jev_eligible(few, cost_efficiency(few))
        assert not ok and "9 trades < 20" in why
        destroyed = self.m()
        ok, why = jev_eligible(destroyed, cost_efficiency(destroyed))
        assert not ok and any("cost-to-edge" in w for w in why)
        liq = self.m(gross_pnl=10.0, net_profit=6.5, liquidation_count=1)
        assert not jev_eligible(liq, cost_efficiency(liq))[0], "a liquidation disqualifies even a profitable bot"
