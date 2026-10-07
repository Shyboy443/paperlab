"""Queued HTF entries keep their entry filters until the actual fill."""
import pytest

from app.core.types import Candle, MarketRules
from app.execution.config import ExecutionConfig
from app.live import v14_engine
from app.strategies.base import Strategy
from tests.conftest import settings_factory
from tests.test_v14 import Ctx, T, UP

MINUTE = 60_000


def replay(engine_cls, sid, *, timestamps=None, after_queue=None):
    start = T + 1
    limit = engine_cls is v14_engine.HTFLimitEngine
    class Scripted(Strategy):
        id = sid
        timeframes = ("1m",)
        min_rr = 0

        def on_candle(self, c, ctx):
            if c.open_time != start:
                return []
            price = 99 if limit else 100
            return [self.make_entry(symbol="A", side="long", ts=c.close_time, tf="1m", price=price,
                                    stop=97, tps_price=[(106, 1)],
                                    meta={"htf": {"policy": "reversion"},
                                          **({"limit": price, "expire_ms": start + 20 * MINUTE} if limit else {})})]
    cfg = ExecutionConfig(signal_latency_ms=1, order_latency_ms=0, base_slippage_bps=0, vol_component=0)
    eng = engine_cls(settings_factory(balance=100), ["A"],
                     rules={"A": MarketRules("A", 0.0001, 0.000001, 0.000001, 1, qty_precision=6)}, execution=cfg)
    for c in Ctx({"A": UP}).bars["A"]:
        eng.ctx.push(c)

    def feed():
        for i, ts in enumerate(timestamps or [start + i * MINUTE for i in range(5)]):
            if i == 2 and after_queue:
                after_queue(eng)
            yield Candle("A", "1m", ts, 100, 100, 98.9 if i >= 3 else 100, 100, 100,
                         ts + MINUTE - 1, quote_volume=10000)
    return eng, eng.run(Scripted, feed(), since_ms=start)


@pytest.mark.parametrize("engine_cls,sid", [(v14_engine.HTFScalpEngine, "V14.1"),
                                           (v14_engine.HTFScanEngine, "V14.2"),
                                           (v14_engine.HTFLimitEngine, "V14.4")])
def test_valid_context_fills_and_preserves_both_contexts(engine_cls, sid):
    _, result = replay(engine_cls, sid)
    fill = next(f for f in result.fills if f.kind == "entry")
    assert fill.meta["htf"]["policy"] == "reversion"
    view = fill.meta["htf_fill"]
    assert view["h4_close_ms"] <= view["decision_ms"] < fill.ts
    assert view["d1_close_ms"] <= view["decision_ms"]
    assert view["policy"] == ("breakout" if sid == "V14.2" else "reversion")


@pytest.mark.parametrize("engine_cls,sid", [(v14_engine.HTFScalpEngine, "V14.1"),
                                           (v14_engine.HTFScanEngine, "V14.2"),
                                           (v14_engine.HTFLimitEngine, "V14.4")])
def test_feed_gap_cancels_queued_orders_without_entry_fees(engine_cls, sid):
    _, result = replay(engine_cls, sid, timestamps=[T + 1, T + 1 + 60 * MINUTE])
    assert not result.trades and not result.fills
    assert result.rejects["htf_fill_missing_or_stale"] == 1


def test_resting_limit_is_cancelled_on_regime_change_before_it_is_hit(monkeypatch):
    original = v14_engine.htf_view
    def changed(ctx, symbol, price, asof):
        view = original(ctx, symbol, price, asof)
        if asof >= T + 2 * MINUTE:
            return {**view, "h4": -1, "d1": -1, "h4_slope_atr": -0.3}
        return view
    monkeypatch.setattr(v14_engine, "htf_view", changed)
    eng, result = replay(v14_engine.HTFLimitEngine, "V14.4")
    assert result.rejects["htf_fill_regime_veto"] == 1
    assert not eng._pending and not result.fills and not result.trades


def test_risk_gap_protection_still_runs_after_htf_guard():
    def gap(eng):
        eng._pending[0][1].stop = 101  # invalidated stop on a queued resting order
    _, result = replay(v14_engine.HTFLimitEngine, "V14.4", after_queue=gap)
    assert not result.fills and result.rejects["gap_past_stop"] == 1
