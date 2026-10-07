import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from app.competition import v9_config as v9
from app.core.types import Candle, MarketRules
from app.live.stock_engine import StockReplayEngine
from app.live import alpaca_market as am
from app.strategies.v9.stocks import PullbackV9, VwapSnapV9, session_bars, session_vwap
from tests.test_v9 import FRI_OPEN, MON_OPEN, CAL, FakeClient, bar

MINUTE = 60_000


def run_exit(side, event, session_close=None, gate=None):
    t0 = FRI_OPEN

    class OneShot(VwapSnapV9.for_class('SCALP')):
        def on_candle(self, c, ctx):
            if c.close_time != t0 + 10 * MINUTE - 1:
                return []
            sig = self.make_entry(symbol='SPY', side=side, ts=c.close_time, tf='5m', price=100,
                                  stop=99 if side == 'long' else 101, tps_r=[(1, 1)])
            sig.max_hold_s = 3600
            if session_close:
                sig.meta['stock_session_close_ms'] = session_close
            return [sig]

    bars = []
    for m in range(35):
        o, h, l, c = event if m == 20 else (100, 100.05, 99.95, 100)
        t = t0 + m * MINUTE
        bars.append(Candle('SPY', '1m', t, o, h, l, c, 100_000, t + MINUTE - 1, True))
    engine = StockReplayEngine(dataclasses.replace(v9.settings_v9(), strategy_halt_pct=1, daily_halt_pct=1),
                               ['SPY'], rules={'SPY': MarketRules(**v9.RULES['SPY'])}, seed=7,
                               execution=v9.EXECUTION_V9, fees=v9.FEES_V9, fee_source='schedule', gate=gate)
    return engine.run(OneShot, bars, since_ms=t0, leverage=3, signal_tf='5m', only_symbol='SPY')


@pytest.mark.parametrize('side,event,reference', [('long', (100, 100.1, 97, 99.5), 99),
                                                ('short', (100, 103, 99.9, 100.5), 101)])
def test_crossed_stop_prices_at_level_not_later_extreme(side, event, reference):
    fill, = [f for f in run_exit(side, event).fills if f.kind == 'stop']
    assert fill.ref_price == reference
    assert abs(fill.price - reference) < 0.1  # Costs still apply.


@pytest.mark.parametrize('side,event,reference', [('long', (98, 100, 97, 99), 98),
                                                ('short', (102, 103, 100, 101), 102)])
def test_opening_gap_does_not_receive_fictitious_stop_level(side, event, reference):
    fill, = [f for f in run_exit(side, event).fills if f.kind == 'stop']
    assert fill.ref_price == reference


@pytest.mark.parametrize('side,event,target', [('long', (100, 104, 99.9, 100), 101),
                                             ('short', (100, 100.1, 96, 100), 99)])
def test_limit_target_never_receives_favourable_extreme(side, event, target):
    fill, = [f for f in run_exit(side, event).fills if f.kind == 'tp']
    assert fill.price == pytest.approx(target)
    assert fill.fee == pytest.approx(fill.qty * target * v9.FEES_V9.maker_rate)


def test_exact_target_touch_does_not_guarantee_queue_fill():
    assert not [f for f in run_exit('long', (100, 101, 99.9, 100)).fills if f.kind == 'tp']


def test_same_bar_stop_and_target_uses_conservative_stop_first():
    exits = [f for f in run_exit('long', (100, 104, 97, 100)).fills if f.kind != 'entry']
    assert exits[0].kind == 'stop'


def test_session_close_caps_time_exit_even_when_signal_wants_an_hour():
    result = run_exit('long', (100, 100.1, 99.9, 100), session_close=FRI_OPEN + 25 * MINUTE)
    fill, = [f for f in result.fills if f.kind == 'time']
    assert fill.ts == FRI_OPEN + 24 * MINUTE


@pytest.mark.parametrize('side,event', [('long', (100, 100.1, 97, 99.5)),
                                      ('short', (100, 103, 99.9, 100.5)),
                                      ('long', (100, 104, 99.9, 100)),
                                      ('short', (100, 100.1, 96, 100))])
def test_refused_ai_candidate_has_same_stock_exit_and_costs(side, event):
    refused = lambda *a, **kw: SimpleNamespace(multiplier=0, info={'decision_id': 'refused'})
    control = run_exit(side, event).trades[0]
    result = run_exit(side, event, gate=refused)
    assert not result.trades
    shadow, = result.shadow_trades
    assert shadow.exit_kind == control.exit_kind
    assert shadow.exit_price == pytest.approx(control.exit_price)
    assert shadow.fees == pytest.approx(control.fees)
    assert shadow.net == pytest.approx(control.net)


def test_refused_ai_candidate_has_same_session_deadline():
    refused = lambda *a, **kw: SimpleNamespace(multiplier=0, info={'decision_id': 'refused'})
    result = run_exit('long', (100, 100.1, 99.9, 100),
                      session_close=FRI_OPEN + 25 * MINUTE, gate=refused)
    shadow, = result.shadow_trades
    assert shadow.exit_kind == 'time'
    assert shadow.exit_ts == FRI_OPEN + 24 * MINUTE


def test_repair_can_fill_same_session_only_with_known_previous_timestamp():
    sessions = am.Sessions.from_calendar(CAL)
    raw = [bar('SPY', MON_OPEN + 2 * MINUTE, 110)]
    old = am.session_tape('SPY', raw, sessions, last_close=100, last_open_ms=FRI_OPEN + 389 * MINUTE)
    assert [b.open_time for b in old] == [MON_OPEN + 2 * MINUTE]
    same = am.session_tape('SPY', raw, sessions, last_close=109, last_open_ms=MON_OPEN)
    assert same[0].source == 'filled' and same[0].close == 109


def test_live_silence_cannot_invent_an_open_from_yesterday():
    now = MON_OPEN + 4 * MINUTE
    feed = am.AlpacaLiveMarket(['SPY'], FakeClient([], down=True), lambda *a: None,
                              FRI_OPEN, clock=lambda: now / 1000)
    feed.sessions = am.Sessions.from_calendar(CAL)
    queue = feed.subscribe('SPY')
    feed._deliver(bar('SPY', FRI_OPEN + 389 * MINUTE, 100), 'live')
    feed.stats['calendar_ts'] = now / 1000
    feed.stats['ws'].update(connected=True, last_msg_ts=now / 1000)
    asyncio.run(feed._tick())
    assert queue.qsize() == 1
    asyncio.run(feed._deliver_live(bar('SPY', MON_OPEN + 3 * MINUTE, 110)))
    assert queue.qsize() == 2
    assert queue.get_nowait().open_time < MON_OPEN
    assert queue.get_nowait().open_time == MON_OPEN + 3 * MINUTE


def test_session_vwap_excludes_yesterday_and_forming_future_bars():
    def five(t, price, volume=10, closed=True):
        return Candle('SPY', '5m', t, price, price, price, price, volume, t + 5 * MINUTE - 1, closed)
    current = five(MON_OPEN + 15 * MINUTE, 110)
    bars = [five(FRI_OPEN + 385 * MINUTE, 2000, 10_000), five(MON_OPEN, 100),
            five(MON_OPEN + 5 * MINUTE, 105), current, five(MON_OPEN + 20 * MINUTE, 999, closed=False)]
    ctx = SimpleNamespace(candles=lambda *a: bars)
    todays = session_bars(ctx, current, am.Sessions.from_calendar(CAL).at)
    assert session_vwap(todays) == 105


def test_no_entry_can_be_generated_from_zero_volume_synthetic_bar():
    cls = PullbackV9.for_class('SCALP', session_at=am.Sessions.from_calendar(CAL).at)
    candle = bar('SPY', MON_OPEN + 20 * MINUTE, v=0)
    assert cls().on_candle(candle, SimpleNamespace()) == []


def test_far_below_ema_is_not_misclassified_as_a_pullback_touch():
    def five(index, low, high, close):
        t = MON_OPEN + index * 5 * MINUTE
        return Candle('SPY', '5m', t, close - 0.1, high, low, close, 100, t + 5 * MINUTE - 1, True)
    bars = [five(i, 98.8, 99.4, 99.2) for i in range(5)]
    bars.append(five(5, 100, 100.9, 100.8))
    ctx = SimpleNamespace(candles=lambda *a: bars, ind=lambda *a, **kw: [100.] * len(bars))
    strategy = PullbackV9.for_class('SCALP', session_at=am.Sessions.from_calendar(CAL).at)()
    assert strategy.scalp_setup(ctx, bars[-1], bars, 1, 'up', 'up') is None
    bars[-2] = five(4, 99.8, 100.1, 99.9)
    assert strategy.scalp_setup(ctx, bars[-1], bars, 1, 'up', 'up') is not None


def test_changed_stock_rules_cannot_reuse_previous_backtest_cache(tmp_path, monkeypatch):
    from scripts import backtest_2y as bt
    job = {'program': 'v9', 'key': 'V9.1-TSLA-5M'}
    monkeypatch.setattr(bt, 'freeze', lambda p: {'fingerprint': 'old'})
    old = bt.result_path(tmp_path, job)
    old.parent.mkdir()
    old.write_text('{"net":10000}')
    monkeypatch.setattr(bt, 'freeze', lambda p: {'fingerprint': 'fixed'})
    fixed = bt.result_path(tmp_path, job)
    assert not fixed.exists() and old.exists()
    monkeypatch.setattr(bt, 'END', bt.END + 86400000)
    assert bt.result_path(tmp_path, job) != fixed
