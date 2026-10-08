"""V17 swing families through the replay engine on a synthetic market (a long rise, then a long fall): each family
trades, holds for days rather than minutes (V17.5: one day), and closes trades with its own exit. No network."""
from __future__ import annotations

import dataclasses
import math
from collections import deque

import pytest

from app.backtest.replay import ReplayEngine
from app.competition.v6_config import AGGRESSIVE_V6, EXECUTION_V6, FEES_V6, SizingV6, settings_v6
from app.core.types import Candle, MarketRules
from app.strategies.v17.swing import WEEK_MONDAY_OFFSET, load_v17

MIN, DAY = 60_000, 86_400_000
SYM = "BTCUSDT"
T0 = 20_000 * DAY + WEEK_MONDAY_OFFSET % (7 * DAY)          # a Monday 00:00 UTC
DAYS = 150


def tape():
    out = []
    for m in range(DAYS * 1440):
        d = m / 1440
        # up 60% over 75 days, then down, with a daily wiggle so ranges and ATR are real
        trend = 100 * math.exp(0.006 * d) if d < 75 else 100 * math.exp(0.006 * 75 - 0.006 * (d - 75))
        p = trend * (1 + 0.01 * math.sin(2 * math.pi * d))
        q = trend * (1 + 0.01 * math.sin(2 * math.pi * (m + 1) / 1440))
        t = T0 + m * MIN
        out.append(Candle(SYM, "1m", t, p, max(p, q) * 1.0005, min(p, q) * 0.9995, q, 10.0, t + MIN - 1, True,
                          0.0, 0, "historical", 0.0))
    return out


@pytest.fixture(scope="module")
def bars():
    return tape()


def replay(sid, bars):
    rules = {SYM: MarketRules(symbol=SYM, tick=0.1, step=0.001, min_qty=0.001, min_notional=5.0, maint_margin_rate=0.005,
                              price_precision=1, qty_precision=3)}
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0, strategy_starting_balance=1000.0)
    eng = ReplayEngine(settings, [SYM], rules=rules, funding=None, execution=EXECUTION_V6, sizing=SizingV6(rules, jev=False),
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, fees=FEES_V6, fee_source="schedule",
                       leverage_policy="needed", cost_gate=None, seed=7)
    eng.portfolio.closed_trades = deque(maxlen=None)
    return eng.run(load_v17()[sid].for_class("SWING"), iter(bars), since_ms=T0 + 40 * DAY, leverage=20,
                   signal_tf="4h", only_symbol=SYM).trades


@pytest.mark.parametrize("sid", ["V17.1", "V17.2", "V17.3", "V17.4", "V17.5"])
def test_family_trades_and_holds_long(bars, sid):
    trades = replay(sid, bars)
    assert trades, sid
    hold_h = sum((t.exit_ts - t.entry_ts) for t in trades) / len(trades) / 3_600_000
    assert hold_h >= (20 if sid == "V17.5" else 24), (sid, hold_h)
    if sid == "V17.5":
        assert all((t.exit_ts - t.entry_ts) <= DAY + 2 * 3_600_000 for t in trades)


def test_trend_families_ride_the_rise_and_the_fall(bars):
    for sid in ("V17.1", "V17.2"):
        sides = {t.side for t in replay(sid, bars)}
        assert sides == {"long", "short"}, sid
    assert {t.side for t in replay("V17.3", bars)} == {"long"}                 # long only
    assert any(t.exit_kind == "manual" for t in replay("V17.1", bars))          # its own channel exit fired
