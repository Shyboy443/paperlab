"""Competition engine: cost accounting, isolation, exchange filters and look-ahead protection.

These are the deterministic cases from the competition spec. They run against the real
Portfolio/RiskManager/ExitEngine through ReplayEngine -- if a test here passes, it passes about the
same code the live lab uses, which is the whole point of having one execution model.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.competition.config import QualificationConfig, RiskConfig, Season, competitor_version
from app.execution.config import ExecutionConfig
from app.competition.engine import CompetitionEngine
from app.competition.qualification import judge, to_live_candidate, to_shadow_live
from app.competition.score import score
from app.competition.config import ScoreWeights
from app.core.types import MarketRules
from app.strategies.base import P, Strategy
from tests.conftest import T0, make_candles, settings_factory

SYMBOL = "BTCUSDT"


# ---- a deterministic competitor ---------------------------------------------------------------

class OneShot(Strategy):
    """Goes long on bar `at`, with a fixed stop and target. No indicators, no randomness."""
    id = "T01"
    name = "one shot long"
    timeframes = ("1m",)
    min_rr = 0.0
    warmup_bars = 0
    max_positions = 1
    default_leverage = 5

    @dataclasses.dataclass
    class Params:
        at: int = P(5, min=0, max=10_000)
        stop_pct: float = P(0.02, min=0.0001, max=0.5)
        tp_r: float = P(2.0, min=0.1, max=50.0)

    def __init__(self, params=None):
        super().__init__(params)
        self._fired = False

    def on_candle(self, c, ctx):
        if self._fired or c.symbol != SYMBOL:
            return []
        idx = len(ctx.candles(c.symbol, c.tf))
        if idx < self.params.at:
            return []
        self._fired = True
        stop = c.close * (1 - self.params.stop_pct)
        return [self.make_entry(symbol=c.symbol, side="long", ts=c.close_time, tf=c.tf,
                                price=c.close, stop=stop, tps_r=[(self.params.tp_r, 1.0)],
                                reason="test")]


class Idle(Strategy):
    """Never trades. Used to prove isolation."""
    id = "T02"
    name = "idle"
    timeframes = ("1m",)
    warmup_bars = 0

    def on_candle(self, c, ctx):
        return []


def big_rules() -> dict[str, MarketRules]:
    """Permissive filters so sizing tests are about sizing, not about minimums."""
    return {SYMBOL: MarketRules(SYMBOL, 0.01, 0.000001, 0.000001, 1.0, 0.025, 2, 6)}


def engine(settings=None, rules=None, funding=None, balance=20.0, slippage_mult=1.0,
           execution=None):
    s = settings or settings_factory(balance=balance)
    cfg = execution or ExecutionConfig(slippage_mult=slippage_mult)
    return ReplayEngine(s, [SYMBOL], rules=rules or big_rules(), seed=1, funding=funding,
                        execution=cfg)


def rising(n=40, start=100.0, step=0.004):
    """A clean uptrend: an entry on bar 5 reaches a 2R target without touching its stop."""
    closes = [start * (1 + step) ** i for i in range(n)]
    return make_candles(closes=closes, symbol=SYMBOL, tf="1m", start_ts=T0, wick=0.0)


def falling(n=40, start=100.0, step=0.01):
    closes = [start * (1 - step) ** i for i in range(n)]
    return make_candles(closes=closes, symbol=SYMBOL, tf="1m", start_ts=T0, wick=0.0)


def crash(n=40, start=100.0, at=8, depth=0.12):
    """Flat, then one bar that gaps straight through both the stop and the liquidation price.

    Needed because with the REAL bracket maintenance margin (0.4% for BTCUSDT) a 20x position
    liquidates 4.6% away, well beyond a 2% stop -- so an orderly decline always stops out first.
    Only a gap reaches liquidation, and ExitEngine checks liquidation before the stop.
    """
    closes = [start] * n
    lows = [start] * n
    for i in range(at, n):
        closes[i] = start * (1 - depth)
        lows[i] = start * (1 - depth)
    return make_candles(closes=closes, symbol=SYMBOL, tf="1m", start_ts=T0, wick=0.0,
                        lows=lows, highs=[start] * n)


# ---- TEST 1: full cost decomposition on one profitable trade ------------------------------------

class TestOneProfitableTrade:
    def test_entry_and_exit_each_pay_a_fee(self):
        e = engine()
        res = e.run(OneShot, rising())
        trade_fills = [f for f in res.fills if f.kind != "funding"]
        assert len(trade_fills) >= 2, "expected at least an entry and an exit fill"
        assert all(f.fee > 0 for f in trade_fills), "every fill must be charged a commission"
        assert trade_fills[0].kind == "entry" and trade_fills[0].is_open

    def test_net_equals_gross_minus_costs(self):
        e = engine()
        res = e.run(OneShot, rising())
        m = mx.compute("T01", res.trades, res.fills, res.equity, res.starting_equity)
        assert m.trades == 1
        assert m.gross_pnl > 0
        # equity-derived net must reconcile with the cost decomposition
        rebuilt = m.gross_pnl - m.fees_paid - m.slippage_cost + m.funding_paid
        assert m.net_profit == pytest.approx(rebuilt, abs=1e-6)
        assert m.net_profit < m.gross_pnl, "costs must reduce the result"

    def test_slippage_is_a_cost_never_a_bonus(self):
        e = engine()
        res = e.run(OneShot, rising())
        for f in res.fills:
            assert mx.slippage_usdt(f) >= 0.0


# ---- TEST 2: doubled slippage must reduce net profit --------------------------------------------

class TestSlippageStress:
    def test_double_slippage_lowers_net(self):
        base = engine(slippage_mult=1.0).run(OneShot, rising())
        worse = engine(slippage_mult=2.0).run(OneShot, rising())
        mb = mx.compute("T01", base.trades, base.fills, base.equity, base.starting_equity)
        mw = mx.compute("T01", worse.trades, worse.fills, worse.equity, worse.starting_equity)
        assert mw.slippage_cost > mb.slippage_cost
        assert mw.net_profit < mb.net_profit


# ---- TEST 3: funding across a settlement --------------------------------------------------------

class TestFunding:
    def test_holding_through_a_settlement_pays_funding(self):
        bars = rising()
        # entry fires on bar 5 and the 2R target lands near bar 15: settle in between
        ts = bars[10].close_time
        sched = FundingSchedule({SYMBOL: [(ts, 0.0005)]})
        res = engine(funding=sched).run(OneShot, bars)
        funding_fills = [f for f in res.fills if f.kind == "funding"]
        assert funding_fills, "a position open across the settlement must settle funding"
        # a long pays when the rate is positive
        assert funding_fills[0].realized_pnl < 0

    def test_no_position_means_no_funding(self):
        bars = rising()
        sched = FundingSchedule({SYMBOL: [(bars[0].close_time, 0.0005)]})
        res = engine(funding=sched).run(Idle, bars)
        assert [f for f in res.fills if f.kind == "funding"] == []

    def test_funding_is_not_folded_into_trade_pnl(self):
        bars = rising()
        ts = bars[10].close_time
        sched = FundingSchedule({SYMBOL: [(ts, 0.0005)]})
        res = engine(funding=sched).run(OneShot, bars)
        m = mx.compute("T01", res.trades, res.fills, res.equity, res.starting_equity)
        assert m.funding_paid < 0
        # the closed trade's own PnL knows nothing about funding
        assert all(t.pnl == pytest.approx(t.net + t.fees) for t in res.trades)


# ---- TEST 4: liquidation ------------------------------------------------------------------------

class TestLiquidation:
    def test_liquidation_force_closes_and_is_recorded(self):
        # a 12% gap at 20x blows past the 4.6% liquidation price; ExitEngine checks liq first
        e = engine()
        res = e.run(OneShot, crash(), leverage=20)
        kinds = [f.kind for f in res.fills]
        assert "liq" in kinds, f"expected a liquidation, got {kinds}"
        assert e.portfolio.positions_of("T01") == [], "liquidation must leave no open position"

    def test_liquidation_disqualifies(self):
        e = engine()
        res = e.run(OneShot, crash(), leverage=20)
        m = mx.compute("T01", res.trades, res.fills, res.equity, res.starting_equity)
        assert m.liquidation_count >= 1
        q = judge(m, QualificationConfig())
        assert q.state == "DISQUALIFIED"
        assert not q.passed

    def test_equity_never_goes_negative(self):
        e = engine()
        res = e.run(OneShot, crash(), leverage=20)
        assert all(eq >= 0.0 for _, eq in res.equity), "a liquidated book must not go negative"


# ---- TEST 5: wallet isolation -------------------------------------------------------------------

class TestIsolation:
    def test_a_trading_bot_does_not_move_an_idle_bot(self):
        settings = settings_factory(balance=20.0)
        season = Season("s1", "c1", 0, 10, symbols=(SYMBOL,),
                        risk=RiskConfig(starting_balance=20.0, max_leverage=5))
        run = CompetitionEngine(settings, season, rules=big_rules()).run(
            {"T01": OneShot, "T02": Idle}, rising())
        by_id = {c.strategy_id: c for c in run.competitors}
        assert by_id["T01"].metrics.trades == 1
        assert by_id["T02"].metrics.trades == 0
        assert by_id["T02"].metrics.ending_equity == pytest.approx(20.0)
        assert by_id["T02"].metrics.fees_paid == 0.0

    def test_competitors_do_not_share_a_portfolio(self):
        settings = settings_factory(balance=20.0)
        season = Season("s1", "c1", 0, 10, symbols=(SYMBOL,),
                        risk=RiskConfig(starting_balance=20.0, max_leverage=5))
        eng = CompetitionEngine(settings, season, rules=big_rules())
        a = eng._run_one(OneShot, rising())
        b = eng._run_one(Idle, rising())
        assert a.starting_equity == b.starting_equity == 20.0
        assert b.final_equity == pytest.approx(20.0)


# ---- TEST 7: exchange filters -------------------------------------------------------------------

class TestExchangeFilters:
    def test_order_below_min_notional_is_rejected_not_resized(self):
        strict = {SYMBOL: MarketRules(SYMBOL, 0.1, 0.001, 0.001, 5_000.0, 0.025, 2, 3)}
        res = engine(rules=strict).run(OneShot, rising())
        assert res.trades == []
        assert res.rejects.get("below_min_notional", 0) > 0

    def test_quantity_is_rounded_to_the_step(self):
        stepped = {SYMBOL: MarketRules(SYMBOL, 0.1, 0.01, 0.01, 1.0, 0.025, 2, 2)}
        res = engine(rules=stepped, balance=200.0).run(OneShot, rising())
        for f in res.fills:
            if f.kind == "funding":
                continue
            assert round(f.qty / 0.01) == pytest.approx(f.qty / 0.01, abs=1e-6)


# ---- look-ahead protection ----------------------------------------------------------------------

class TestNoLookAhead:
    def test_a_fill_never_predates_its_signal(self):
        res = engine().run(OneShot, rising())
        entry = next(f for f in res.fills if f.kind == "entry")
        assert entry.ts >= T0
        exits = [f for f in res.fills if f.kind != "entry" and f.kind != "funding"]
        assert all(f.ts >= entry.ts for f in exits), "an exit cannot fill before the entry"

    def test_strategy_only_ever_sees_closed_bars_up_to_now(self):
        seen: list[tuple[int, int]] = []

        class Watcher(Strategy):
            id, name, timeframes, warmup_bars = "T03", "watcher", ("1m",), 0

            def on_candle(self, c, ctx):
                newest = ctx.candles(c.symbol, c.tf)[-1]
                seen.append((c.close_time, newest.close_time))
                return []

        engine().run(Watcher, rising())
        assert seen, "strategy was never called"
        assert all(newest == now for now, newest in seen), "future bars leaked into the context"

    def test_funding_before_the_position_is_not_applied_retroactively(self):
        bars = rising()
        # a settlement that happened before the entry bar must not be charged
        sched = FundingSchedule({SYMBOL: [(bars[0].close_time, 0.005)]})
        res = engine(funding=sched).run(OneShot, bars)
        assert [f for f in res.fills if f.kind == "funding"] == []

    def test_every_competitor_receives_identical_bars(self):
        bars = tuple(rising())
        settings = settings_factory(balance=20.0)
        season = Season("s1", "c1", 0, 10, symbols=(SYMBOL,),
                        risk=RiskConfig(starting_balance=20.0, max_leverage=5))
        run = CompetitionEngine(settings, season, rules=big_rules()).run(
            {"T01": OneShot, "T02": Idle}, bars)
        assert run.bars == len(bars)
        assert bars == tuple(rising()), "the competition mutated the shared tape"


# ---- qualification and ranking ------------------------------------------------------------------

class TestQualification:
    def _metrics(self, **over):
        m = mx.CompetitorMetrics("X")
        defaults = dict(trades=200, net_profit=5.0, expectancy_r=0.3, profit_factor=1.5,
                        max_drawdown_pct=0.10, largest_trade_profit_contribution_pct=0.1,
                        starting_equity=20.0, ending_equity=25.0, net_return_pct=0.25)
        for k, v in {**defaults, **over}.items():
            setattr(m, k, v)
        return m

    def test_too_few_trades_cannot_qualify(self):
        q = judge(self._metrics(trades=14), QualificationConfig())
        assert q.state == "INSUFFICIENT_SAMPLE"

    def test_rank_one_can_still_fail(self):
        """The spec's example: +120% return, -72% drawdown, 14 trades."""
        hot = self._metrics(trades=14, net_return_pct=1.2, net_profit=24.0, max_drawdown_pct=0.72)
        steady = self._metrics(trades=340, net_return_pct=0.28, net_profit=5.6, max_drawdown_pct=0.12)
        w = ScoreWeights()
        assert score(hot, w).total > float("-inf")
        assert judge(hot, QualificationConfig()).state == "INSUFFICIENT_SAMPLE"
        # the steady one still is not QUALIFIED until the later stages have actually run
        assert judge(steady, QualificationConfig()).state == "WATCHLIST"

    def test_unrun_stages_block_qualification(self):
        q = judge(self._metrics(), QualificationConfig())
        assert q.state == "WATCHLIST"
        assert any("not yet evaluated" in r for r in q.reasons)

    def test_all_stages_passing_qualifies(self):
        q = judge(self._metrics(), QualificationConfig(),
                  oos_ratio=0.8, ruin_probability=0.01, stress_worst_net=2.0)
        assert q.state == "QUALIFIED"
        assert q.passed

    def test_drawdown_gate_fails_hard(self):
        q = judge(self._metrics(max_drawdown_pct=0.55), QualificationConfig(),
                  oos_ratio=0.8, ruin_probability=0.01, stress_worst_net=2.0)
        assert q.state == "FAILED"
        assert any("max_drawdown_pct" in r for r in q.reasons)

    def test_promotion_requires_each_step_in_order(self):
        q = judge(self._metrics(), QualificationConfig(),
                  oos_ratio=0.8, ruin_probability=0.01, stress_worst_net=2.0)
        with pytest.raises(ValueError):
            to_live_candidate(q, 100, QualificationConfig())
        to_shadow_live(q)
        assert q.state == "SHADOW_LIVE"
        to_live_candidate(q, 5, QualificationConfig())
        assert q.state == "SHADOW_LIVE", "too little forward evidence must not promote"
        to_live_candidate(q, 100, QualificationConfig())
        assert q.state == "LIVE_CANDIDATE"

    def test_leverage_is_penalised_in_the_score(self):
        low = self._metrics(max_leverage_used=2)
        high = self._metrics(max_leverage_used=20)
        w = ScoreWeights()
        assert score(low, w, max_leverage_allowed=20).total > score(high, w, max_leverage_allowed=20).total


# ---- strategy versioning ------------------------------------------------------------------------

class TestVersioning:
    def test_changing_a_parameter_creates_a_new_competitor(self):
        a = competitor_version(OneShot, OneShot.Params(at=5))
        b = competitor_version(OneShot, OneShot.Params(at=9))
        assert a != b
        assert a.startswith("T01:") and b.startswith("T01:")

    def test_same_parameters_are_the_same_competitor(self):
        assert competitor_version(OneShot, OneShot.Params(at=5)) == \
               competitor_version(OneShot, OneShot.Params(at=5))


# ---- reproducibility ----------------------------------------------------------------------------

class TestReproducibility:
    def test_same_season_same_result(self):
        a = engine().run(OneShot, rising())
        b = engine().run(OneShot, rising())
        assert [f.price for f in a.fills] == [f.price for f in b.fills]

    def test_season_fingerprint_changes_with_config(self):
        s1 = Season("s", "c", 0, 1)
        s2 = Season("s", "c", 0, 1, risk=RiskConfig(max_leverage=5))
        assert s1.fingerprint() != s2.fingerprint()
