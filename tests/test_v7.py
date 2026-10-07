"""Active challenger isolation, causal features, exits, risk and reporting regressions."""
import dataclasses
from types import SimpleNamespace

import pytest

from app.competition import v6_config, v7_config
from app.strategies.v7.active import PullbackV7, BreakoutV7
from app.core.v6_view import jev_block
from tests.conftest import FakeCtx, make_candles, T0
from tests.test_public_api import anon  # noqa: F401

HOUR = 3_600_000


def active_setup(cls=PullbackV7, short=False):
    cs = make_candles([100.0] * 60, symbol="ENAUSDT", tf="15m", wick=0)
    last = dataclasses.replace(cs[-1], open=100, high=101.1, low=99.8, close=101, volume=300)
    if short:
        last = dataclasses.replace(last, high=100.2, low=98.9, close=99)
    cs[-1] = last
    hour = (last.close_time + 1) // HOUR * HOUR
    seen = []
    feed = SimpleNamespace(caps={hour: {}},
                           snapshot=lambda t: seen.append(t) or {"funding_pct_90d": 0.5, "oi_chg_24h": 0.01},
                           expected_funding=lambda *a: 0.0)
    st = cls.for_class("ACTIVE", feed)()
    st.ctx_trend = lambda *a: "down" if short else "up"
    st.atr = lambda *a: 1.0
    ctx = FakeCtx({("ENAUSDT", "15m"): cs, ("ENAUSDT", "4h"): cs}, symbols=["ENAUSDT"])
    ctx.ind = lambda *a, **kw: [100.0] * 60
    return st, ctx, last, feed, seen


@pytest.mark.parametrize("cls", [PullbackV7, BreakoutV7])
@pytest.mark.parametrize("short", [False, True])
def test_active_signal_exits_are_symmetric_and_cost_aware(cls, short):
    st, ctx, c, _, _ = active_setup(cls, short)
    sig, = st.on_candle(c, ctx)
    distance = abs(sig.entry_price - sig.stop)
    assert sig.side == ("short" if short else "long")
    assert abs(sig.take_profits[0].price - sig.entry_price) == pytest.approx(1.8 * distance)
    assert sig.max_hold_s == 6 * 3600
    assert 0.006 <= distance / sig.entry_price <= 0.03
    assert st.cooldown_ms() == 30 * 60_000


def test_quarter_hour_uses_frozen_hour_and_missing_watermark_refuses():
    st, ctx, c, feed, seen = active_setup()
    c = dataclasses.replace(c, close_time=c.close_time + 15 * 60_000)
    assert st.on_candle(c, ctx)
    assert seen[-1] == (c.close_time + 1) // HOUR * HOUR
    feed.caps.clear()
    assert st.on_candle(c, ctx) == []


def test_expensive_funding_opposing_trend_and_wide_stop_refuse():
    st, ctx, c, feed, _ = active_setup()
    feed.expected_funding = lambda *a: 0.01
    assert not st.on_candle(c, ctx)
    feed.expected_funding = lambda *a: 0.0
    st.ctx_trend = lambda ctx, sym, tf: "up" if tf == "1h" else "down"
    assert not st.on_candle(c, ctx)


def test_high_percentile_with_small_absolute_funding_is_not_a_veto():
    st, ctx, c, feed, _ = active_setup()
    feed.snapshot = lambda t: {"funding_pct_90d": 1.0, "oi_chg_24h": 0.01}
    feed.expected_funding = lambda *a: 0.000075
    assert st.on_candle(c, ctx)
    st.ctx_trend = lambda *a: "up"
    c = dataclasses.replace(c, low=90)
    assert not st.on_candle(c, ctx)


def test_field_identity_and_default_off():
    from app.live.v6_service import V6ForwardService
    specs = v7_config.field_plan(["ARB", "ENA", "XRP", "DOGE"])
    assert len(specs) == 8 and len({s.key for s in specs}) == 8
    assert all(s.timeframe == "15m" and s.role == "CONTROL" for s in specs)
    assert v6_config.verify_freeze(v6_config.load_freeze()) == []
    assert v7_config.verify_freeze(v7_config.load_freeze()) == []
    assert v7_config.experiment_identity(v7_config.load_freeze())[0].startswith("v7x-")
    svc = V6ForwardService("unused.db", env={"V6_FORWARD_ENABLED": "true"}, program="V7")
    assert not svc.enabled and svc.cfg["jev"] is False
    assert v6_config.settings_v6().dry_run
    assert v6_config.AGGRESSIVE_V6.max_risk_pct == 0.02


def test_challenger_api_is_separate_and_read_only(anon):
    r = anon.get("/api/public/competition/v7")
    assert r.status_code == 200 and r.json()["read_only"]
    assert r.json()["experiment"] is None
    assert anon.post("/api/public/competition/v7").status_code in (403, 405)
    app = anon.app
    assert app.state.v7.cfg["db"] != app.state.v6.cfg["db"]
    assert app.state.v7.cfg["program"] == "V7" and not app.state.v7.cfg["jev"]


def test_jev_acceptances_include_unresolved_positions():
    d = [{"role": "JEV", "source": "api", "final_level": action, "outcome_r": r}
         for action, r in [("TAKE", None), ("ATTACK", None), ("TAKE", 1.0), ("SKIP", -1.0)]]
    p = jev_block(d, [])
    assert p["accepted"] == 3 and p["accepted_resolved"] == 1
    assert p["selection_alpha_r"] == 1.0


def test_fees_show_at_entry_and_are_not_counted_twice_on_close():
    from tests.test_v6 import tape, session
    bars = tape()
    bot, _ = session(bars[:8 * 60], bars[60].open_time, bars[60].open_time, stop_after=True)
    assert bot.status["trades"] == 0 and bot.status["fees"] > 0
    full, events = session(bars, bars[60].open_time, bars[60].open_time)
    closed = [d for kind, d in events if kind == "closed" and not d.get("counterfactual")]
    assert full.status["fees"] == pytest.approx(sum(t["fees"] for t in closed))
    assert full.status["slippage"] == pytest.approx(sum(t["slippage"] for t in closed))
