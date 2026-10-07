"""Walk-forward, master OOS ledger, Monte Carlo and stress.

The property these tests defend is the one the whole pipeline exists for: qualification numbers must
come from out-of-sample trades only, and an unrun stage must never read as a pass.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.competition import montecarlo as mc
from app.competition import stress as st_mod
from app.competition import walkforward as wf
from app.competition.config import LEVERAGE_IDENTITIES, QualificationConfig, competitor_key, competitor_version
from app.competition.qualification import judge
from app.core.portfolio import ClosedTrade
from app.core.types import Fill
from app.execution.config import ExecutionConfig, FeeSchedule
from tests.test_competition import OneShot

DAY = wf.DAY_MS
T0 = 1_700_000_000_000


def trade(entry_ts, exit_ts, net, symbol="BTCUSDT", pid="p", r=1.0) -> ClosedTrade:
    return ClosedTrade(pid, "T01", symbol, "long", 1.0, 100.0, 101.0, entry_ts, exit_ts,
                       net, 0.0, net, r, "tp")


class FakeResult:
    def __init__(self, trades, fills=()):
        self.trades = list(trades)
        self.fills = list(fills)


# ---- window schedule ------------------------------------------------------------------------

class TestSchedule:
    def test_windows_roll_by_the_step(self):
        cfg = wf.WalkForwardConfig(train_days=180, validation_days=30, test_days=30, step_days=30)
        wins = wf.schedule(T0, T0 + 400 * DAY, cfg)
        assert len(wins) >= 4
        assert wins[1].train_start - wins[0].train_start == 30 * DAY
        assert wins[0].test_end - wins[0].test_start == 30 * DAY

    def test_every_window_fits_inside_the_data(self):
        cfg = wf.WalkForwardConfig()
        end = T0 + 400 * DAY
        for w in wf.schedule(T0, end, cfg):
            assert w.train_start >= T0 and w.test_end <= end

    def test_test_windows_follow_validation_which_follows_training(self):
        w = wf.schedule(T0, T0 + 400 * DAY, wf.WalkForwardConfig())[0]
        assert w.train_end == w.val_start < w.val_end == w.test_start < w.test_end

    def test_too_little_history_yields_no_windows(self):
        assert wf.schedule(T0, T0 + 10 * DAY, wf.WalkForwardConfig()) == []


# ---- the master OOS ledger ---------------------------------------------------------------------

class TestMasterLedger:
    def setup_method(self):
        self.cfg = wf.WalkForwardConfig(train_days=10, validation_days=5, test_days=5, step_days=5)
        self.windows = wf.schedule(T0, T0 + 40 * DAY, self.cfg)

    def test_only_test_window_trades_enter_the_ledger(self):
        w0 = self.windows[0]
        inside = trade(w0.test_start + 1000, w0.test_start + 2000, +5.0)
        in_train = trade(w0.train_start + 1000, w0.train_start + 2000, +999.0)
        in_val = trade(w0.val_start + 1000, w0.val_start + 2000, +888.0)
        res = wf.partition(FakeResult([inside, in_train, in_val]), self.windows, 20.0,
                           "T01", "v", 10, self.cfg)
        assert [t.net for t in res.oos_trades] == [5.0]
        assert res.metrics.net_profit == pytest.approx(5.0)

    def test_training_profit_never_reaches_qualification(self):
        w0 = self.windows[0]
        huge_train = trade(w0.train_start + 1, w0.train_start + 2, +1000.0)
        small_oos = trade(w0.test_start + 1, w0.test_start + 2, -1.0)
        res = wf.partition(FakeResult([huge_train, small_oos]), self.windows, 20.0,
                           "T01", "v", 10, self.cfg)
        assert res.metrics.net_profit == pytest.approx(-1.0), "training PnL leaked into the ledger"

    def test_a_trade_is_attributed_by_entry_not_exit(self):
        """A decision made in-sample must not be counted as out-of-sample because it closed late."""
        w0 = self.windows[0]
        straddler = trade(w0.val_start + 1, w0.test_start + 10_000, +50.0)
        res = wf.partition(FakeResult([straddler]), self.windows, 20.0, "T01", "v", 10, self.cfg)
        assert res.oos_trades == []

    def test_equity_curve_compounds_only_oos_trades(self):
        w0, w1 = self.windows[0], self.windows[1]
        ts = [trade(w0.test_start + 1, w0.test_start + 2, +3.0),
              trade(w1.test_start + 1, w1.test_start + 2, -1.0)]
        res = wf.partition(FakeResult(ts), self.windows, 20.0, "T01", "v", 10, self.cfg)
        assert res.oos_equity[-1][1] == pytest.approx(22.0)

    def test_window_stats_count_activity_and_profitability(self):
        w0, w1 = self.windows[0], self.windows[1]
        ts = [trade(w0.test_start + 1, w0.test_start + 2, +3.0),
              trade(w1.test_start + 1, w1.test_start + 2, -1.0)]
        res = wf.partition(FakeResult(ts), self.windows, 20.0, "T01", "v", 10, self.cfg)
        assert res.active_windows == 2
        assert res.profitable_windows == 1
        assert res.profitable_ratio == pytest.approx(0.5)

    def test_an_inactive_window_is_not_counted_as_a_loss(self):
        w0 = self.windows[0]
        res = wf.partition(FakeResult([trade(w0.test_start + 1, w0.test_start + 2, +3.0)]),
                           self.windows, 20.0, "T01", "v", 10, self.cfg)
        assert res.profitable_ratio == pytest.approx(1.0), "quiet windows must not dilute the ratio"
        assert res.active_ratio < 1.0

    def test_symbol_concentration_is_exposed(self):
        w0 = self.windows[0]
        ts = [trade(w0.test_start + 1, w0.test_start + 2, +90.0, "BTCUSDT", "a"),
              trade(w0.test_start + 3, w0.test_start + 4, +10.0, "ETHUSDT", "b")]
        res = wf.partition(FakeResult(ts), self.windows, 20.0, "T01", "v", 10, self.cfg)
        assert res.symbol_concentration() == pytest.approx(0.9)


# ---- Monte Carlo -------------------------------------------------------------------------------

class TestMonteCarlo:
    def _nets(self, values):
        return [trade(0, 0, v) for v in values]

    def test_refuses_to_run_on_a_tiny_sample(self):
        r = mc.run(self._nets([1.0, -1.0]), 20.0)
        assert not r.ran and "too few" in r.reason

    def test_a_losing_edge_shows_high_ruin(self):
        r = mc.run(self._nets([-2.0] * 20 + [1.0] * 10), 20.0,
                   mc.MonteCarloConfig(simulations=500, seed=1))
        assert r.ran and r.ruin_probability > 0.5

    def test_a_strong_edge_shows_low_ruin(self):
        r = mc.run(self._nets([2.0] * 30 + [-0.5] * 10), 20.0,
                   mc.MonteCarloConfig(simulations=500, seed=1))
        assert r.ran and r.ruin_probability < 0.05

    def test_percentile_drawdowns_are_ordered(self):
        r = mc.run(self._nets([1.0, -1.0, 2.0, -2.0, 0.5, -0.5] * 8), 20.0,
                   mc.MonteCarloConfig(simulations=400, seed=3))
        assert r.median_max_drawdown <= r.p95_max_drawdown <= r.p99_max_drawdown <= r.worst_max_drawdown

    def test_it_is_reproducible(self):
        ts = self._nets([1.0, -1.0, 2.0, -2.0, 0.5, -0.5] * 8)
        cfg = mc.MonteCarloConfig(simulations=300, seed=11)
        assert mc.run(ts, 20.0, cfg).ruin_probability == mc.run(ts, 20.0, cfg).ruin_probability

    def test_ruin_threshold_is_configurable(self):
        ts = self._nets([-1.0] * 30)
        lenient = mc.run(ts, 20.0, mc.MonteCarloConfig(simulations=200, ruin_equity=0.0, seed=2))
        strict = mc.run(ts, 20.0, mc.MonteCarloConfig(simulations=200, ruin_equity=15.0, seed=2))
        assert strict.ruin_probability >= lenient.ruin_probability


# ---- stress --------------------------------------------------------------------------------------

class TestStress:
    def test_scenarios_actually_change_the_config(self):
        ex, fe = ExecutionConfig(), FeeSchedule()
        for sc in st_mod.SCENARIOS:
            ex2, fe2 = st_mod.apply(sc, ex, fe)
            if sc.name == "NORMAL":
                assert (ex2, fe2) == (ex, fe)
            elif sc.name == "FUNDING_ADVERSE":
                assert sc.funding_multiplier == 2.0
            else:
                assert (ex2, fe2) != (ex, fe), f"{sc.name} changed nothing"

    def test_all_taker_forces_the_taker_rate(self):
        sc = next(s for s in st_mod.SCENARIOS if s.name == "ALL_TAKER")
        ex, _ = st_mod.apply(sc, ExecutionConfig(), FeeSchedule())
        assert ex.force_taker is True

    def test_survival_requires_every_scenario_profitable(self):
        r = st_mod.StressResult(scenarios=[
            st_mod.ScenarioResult("NORMAL", ran=True, net_profit=5.0),
            st_mod.ScenarioResult("SLIPPAGE_2X", ran=True, net_profit=-0.1)])
        assert not r.survives()
        assert r.worst_scenario == "SLIPPAGE_2X"

    def test_degradation_is_relative_to_normal(self):
        r = st_mod.StressResult(scenarios=[
            st_mod.ScenarioResult("NORMAL", ran=True, net_profit=10.0),
            st_mod.ScenarioResult("SLIPPAGE_2X", ran=True, net_profit=4.0)])
        assert r.degradation() == pytest.approx(0.4)

    def test_one_failing_scenario_does_not_void_the_rest(self):
        def evaluate(sc, ex, fe):
            if sc.name == "ALL_TAKER":
                raise RuntimeError("boom")
            return st_mod.ScenarioResult(sc.name, ran=True, net_profit=1.0)

        r = st_mod.run(evaluate, ExecutionConfig(), FeeSchedule())
        assert r.ran
        assert r.by_name("ALL_TAKER").ran is False
        assert "boom" in r.by_name("ALL_TAKER").reason


# ---- leverage identities ----------------------------------------------------------------------------

class TestLeverageIdentities:
    def test_each_ceiling_is_a_distinct_competitor(self):
        versions = {competitor_version(OneShot, leverage=l) for l in LEVERAGE_IDENTITIES}
        assert len(versions) == len(LEVERAGE_IDENTITIES)

    def test_the_key_names_the_ceiling(self):
        assert competitor_key("S02", 10) == "S02@10x"
        assert competitor_key("S02", None) == "S02"

    def test_version_still_changes_with_parameters(self):
        a = competitor_version(OneShot, OneShot.Params(at=5), leverage=10)
        b = competitor_version(OneShot, OneShot.Params(at=9), leverage=10)
        assert a != b and a.startswith("T01@10x:")


# ---- qualification on out-of-sample evidence ------------------------------------------------------

class TestQualificationUsesOOS:
    def _m(self, **over):
        from app.competition.metrics import CompetitorMetrics
        m = CompetitorMetrics("X")
        base = dict(trades=200, net_profit=5.0, expectancy_r=0.3, profit_factor=1.5,
                    max_drawdown_pct=0.10, largest_trade_profit_contribution_pct=0.1)
        for k, v in {**base, **over}.items():
            setattr(m, k, v)
        return m

    def test_all_stages_passing_qualifies(self):
        q = judge(self._m(), QualificationConfig(), oos_ratio=0.7, active_ratio=0.9,
                  symbol_concentration=0.4, ruin_probability=0.01, stress_worst_net=1.0)
        assert q.state == "QUALIFIED"

    def test_inactive_windows_fail_the_activity_gate(self):
        q = judge(self._m(), QualificationConfig(), oos_ratio=0.7, active_ratio=0.2,
                  symbol_concentration=0.4, ruin_probability=0.01, stress_worst_net=1.0)
        assert q.state == "FAILED"
        assert any("active" in r for r in q.reasons)

    def test_stress_failure_blocks_qualification(self):
        q = judge(self._m(), QualificationConfig(), oos_ratio=0.7, active_ratio=0.9,
                  symbol_concentration=0.4, ruin_probability=0.01, stress_worst_net=-2.0)
        assert q.state == "FAILED"

    def test_high_ruin_blocks_qualification(self):
        q = judge(self._m(), QualificationConfig(), oos_ratio=0.7, active_ratio=0.9,
                  symbol_concentration=0.4, ruin_probability=0.9, stress_worst_net=1.0)
        assert q.state == "FAILED"

    def test_missing_stages_still_cannot_qualify(self):
        q = judge(self._m(), QualificationConfig(), oos_ratio=0.7, active_ratio=0.9)
        assert q.state == "WATCHLIST"
        assert any("not yet evaluated" in r for r in q.reasons)

    def test_rank_one_with_few_trades_is_insufficient_sample(self):
        q = judge(self._m(trades=44, net_profit=16.0, net_return_pct=0.8), QualificationConfig(),
                  oos_ratio=1.0, active_ratio=1.0, symbol_concentration=0.3,
                  ruin_probability=0.0, stress_worst_net=5.0)
        assert q.state == "INSUFFICIENT_SAMPLE"
