"""Queued orders, intrabar loss limits and multi-coin execution accounting."""
from __future__ import annotations

import pytest

from app.backtest.replay import ReplayEngine
from app.core.types import Candle, MarketRules
from app.execution.config import ExecutionConfig
from app.live.scan_engine import ScanReplayEngine
from app.live.v13_engine import LimitEntryEngineV13
from app.strategies.base import Strategy
from tests.conftest import T0, settings_factory

MINUTE, DAY = 60_000, 86_400_000
ANCHOR = "BTCUSDT"
SYMS = ("AAAUSDT", "BBBUSDT", ANCHOR)


def replay(orders, prices=None, *, engine_cls=ReplayEngine, max_positions=4, minutes=8,
           timestamps=None, max_risk_pct=None, **settings):
    class Scripted(Strategy):
        id = "GUARD_TEST"
        timeframes = ("1m",)
        min_rr = 0.0
        default_leverage = 1 if engine_cls is LimitEntryEngineV13 else 5

        def on_candle(self, c, ctx):
            if c.symbol != ANCHOR:
                return []
            out = []
            for sym, side, entry, stop, target, limit in orders.get(c.open_time, []):
                meta = {"limit": entry, "expire_ms": c.close_time + 20 * MINUTE} if limit else {}
                out.append(self.make_entry(symbol=sym, side=side, ts=c.close_time, tf="1m", price=entry,
                                           stop=stop, tps_price=[(target, 1.0)], meta=meta))
            return out

    Scripted.max_positions = max_positions
    cfg = ExecutionConfig(signal_latency_ms=1, order_latency_ms=0, base_slippage_bps=0, vol_component=0)
    rules = {s: MarketRules(s, 0.0001, 0.000001, 0.000001, 1.0, qty_precision=6) for s in SYMS}
    eng = engine_cls(settings_factory(balance=100, **settings), SYMS, rules=rules,
                     execution=cfg, max_risk_pct=max_risk_pct)
    prices = prices or {}

    def tape():
        for t in timestamps or [T0 + i * MINUTE for i in range(minutes)]:
            for sym in SYMS:
                px = prices.get((sym, t), (100, 100, 100, 100) if sym != ANCHOR else (50000, 50000, 50000, 50000))
                yield Candle(sym, "1m", t, *px, 10, t + MINUTE - 1, quote_volume=10 * px[3])

    return eng, eng.run(Scripted, tape(), since_ms=T0)


@pytest.mark.parametrize("engine_cls", [ReplayEngine, ScanReplayEngine])
def test_simultaneous_delayed_entries_cannot_overfill_position_slots(engine_cls):
    orders = {T0: [(s, "long", 100, 98, 104, False) for s in SYMS[:2]]}
    _, res = replay(orders, engine_cls=engine_cls, max_positions=1)
    entries = [f for f in res.fills if f.kind == "entry"]
    assert len(entries) == 1 and res.rejects["fill_max_positions"] == 1


@pytest.mark.parametrize("side,open_px,stop,target", [("long", 97, 98, 104), ("long", 105, 98, 104),
                                                      ("short", 103, 102, 96), ("short", 95, 102, 96)])
def test_a_gap_that_invalidates_the_entry_never_pays_entry_fees(side, open_px, stop, target):
    orders = {T0: [("AAAUSDT", side, 100, stop, target, False)]}
    _, res = replay(orders, {("AAAUSDT", T0 + MINUTE): (open_px,) * 4})
    assert not res.trades and not res.fills
    assert sum(res.rejects.get(k, 0) for k in ("gap_past_stop", "gap_past_tp1")) == 1


@pytest.mark.parametrize("side,open_px,stop,target", [("long", 101, 98, 110), ("short", 99, 102, 90)])
def test_fill_price_drift_cannot_increase_the_approved_stop_risk(side, open_px, stop, target):
    orders = {T0: [("AAAUSDT", side, 100, stop, target, False)]}
    eng, res = replay(orders, {("AAAUSDT", T0 + MINUTE): (open_px,) * 4}, max_risk_pct=0.02)
    entry = next(f for f in res.fills if f.kind == "entry")
    assert entry.qty * abs(entry.price - stop) <= 2.0 + 1e-8
    assert eng.portfolio.available("GUARD_TEST", eng.ctx.prices) >= 0


@pytest.mark.parametrize("engine_cls", [ReplayEngine, ScanReplayEngine])
def test_daily_limit_fires_on_an_intrabar_loss_even_when_the_close_recovers(engine_cls):
    orders = {T0: [("AAAUSDT", "long", 100, 90, 130, False)],
              T0 + 3 * MINUTE: [("BBBUSDT", "long", 100, 90, 130, False)]}
    eng, res = replay(orders, {("AAAUSDT", T0 + 2 * MINUTE): (100, 100, 94, 100)},
                      engine_cls=engine_cls, RISK_PER_TRADE_PCT=0.10, DAILY_HALT_PCT=0.05)
    assert len(res.daily_halts) == 1 and eng.risk.state.daily_halted
    assert len(res.trades) == 1 and res.trades[0].exit_kind == "halt"
    assert res.rejects["daily_halt"] == 1
    assert not eng.portfolio.positions_of("GUARD_TEST")


def test_daily_halt_resumes_on_the_next_utc_day_without_rebasing_the_wallet():
    next_day = T0 + DAY
    orders = {T0: [("AAAUSDT", "long", 100, 90, 130, False)],
              next_day: [("BBBUSDT", "long", 100, 90, 130, False)]}
    eng, res = replay(orders, {("AAAUSDT", T0 + 2 * MINUTE): (100, 100, 94, 100)},
                      timestamps=[T0, T0 + MINUTE, T0 + 2 * MINUTE, next_day, next_day + MINUTE],
                      RISK_PER_TRADE_PCT=0.10, DAILY_HALT_PCT=0.05)
    assert len(res.daily_halts) == 1 and not eng.risk.state.daily_halted
    assert len([f for f in res.fills if f.kind == "entry"]) == 2
    assert res.final_equity < 95 and eng.risk.state.start_of_day_equity < 95


def test_resting_limits_recheck_margin_when_another_order_has_filled():
    orders = {T0: [(s, "long", 99, 97, 110, True) for s in SYMS[:2]]}
    prices = {(s, T0 + 2 * MINUTE): (100, 100, 98.9, 99) for s in SYMS[:2]}
    eng, res = replay(orders, prices, engine_cls=LimitEntryEngineV13, RISK_PER_TRADE_PCT=0.20)
    assert [f for f in res.fills if f.kind == "entry"]
    assert res.rejects["fill_insufficient_capacity"] == 1
    assert eng.portfolio.available("GUARD_TEST", eng.ctx.prices) >= 0


@pytest.mark.parametrize("engine_cls", [ReplayEngine, ScanReplayEngine, LimitEntryEngineV13])
def test_multi_coin_final_close_uses_the_coins_own_market_state_and_books_exit_fees(engine_cls):
    _, res = replay({T0: [("AAAUSDT", "long", 100, 98, 110, False)]}, engine_cls=engine_cls)
    trade = res.trades[0]
    assert 99 < trade.exit_price < 101  # the last bar is BTC at 50,000
    assert res.final_equity == pytest.approx(100 + trade.net)


def test_entry_market_state_cannot_see_the_executing_bars_atr():
    class Inspect(ReplayEngine):
        entry_atrs = []

        def _market_state(self, bar, price, ts, tf="1m", complete=False):
            state = super()._market_state(bar, price, ts, tf, complete)
            if not complete:
                self.entry_atrs.append(state.atr)
            return state

    t = T0 + 15 * MINUTE
    replay({t: [("AAAUSDT", "long", 100, 90, 130, False)]},
           {("AAAUSDT", t + MINUTE): (100, 120, 90, 100)}, engine_cls=Inspect, minutes=18)
    assert Inspect.entry_atrs and all(v == pytest.approx(0.0) for v in Inspect.entry_atrs)


def test_an_order_is_cancelled_after_a_feed_gap_instead_of_using_a_stale_setup():
    orders = {T0: [("AAAUSDT", "long", 100, 98, 110, False)]}
    _, res = replay(orders, timestamps=[T0, T0 + 10 * MINUTE])
    assert not res.trades and res.rejects["fill_signal_expired"] == 1


def test_a_favourable_gap_cannot_turn_the_stop_into_a_fee_dominated_scratch():
    orders = {T0: [("AAAUSDT", "long", 100, 98, 110, False)]}
    _, res = replay(orders, {("AAAUSDT", T0 + MINUTE): (98.1,) * 4})
    assert not res.trades and res.rejects["fill_fee_gt_r"] == 1


def test_daily_halt_cancels_resting_limits_before_they_can_fill():
    orders = {T0: [("AAAUSDT", "long", 100, 90, 130, False),
                   ("BBBUSDT", "long", 99, 97, 110, True)]}
    prices = {("AAAUSDT", T0 + 2 * MINUTE): (100, 100, 94, 100),
              ("BBBUSDT", T0 + 3 * MINUTE): (100, 100, 98.9, 99)}
    _, res = replay(orders, prices, engine_cls=LimitEntryEngineV13,
                    RISK_PER_TRADE_PCT=0.10, DAILY_HALT_PCT=0.05)
    assert len([f for f in res.fills if f.kind == "entry"]) == 1
    assert len(res.daily_halts) == 1 and res.rejects["fill_daily_halt"] == 1


@pytest.mark.parametrize("version", ["v8", "v9", "v11", "v12", "v13", "v14"])
def test_a_daily_halted_bot_cannot_be_qualified(version):
    import importlib
    cfg = importlib.import_module(f"app.competition.{version}_config")
    row = {"trades": 1000, "net_now": 10, "profit_factor": 2, "max_dd": 0.01,
           "risk_state": "DAILY_HALT"}
    assert cfg.bot_status(row, 100 * DAY, None)[0] != "QUALIFIED"
