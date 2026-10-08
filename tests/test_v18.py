"""V18 trend families through the replay engine on a synthetic market (a long rise, then a long fall, with a weekly
wave so pullbacks exist): each family trades, holds for days, and the BTC gate blocks trades against BTC. No network."""
from __future__ import annotations

import dataclasses
import math
from collections import deque

import pytest

from app.backtest.replay import ReplayEngine
from app.competition.v6_config import AGGRESSIVE_V6, EXECUTION_V6, FEES_V6, SizingV6, settings_v6
from app.core.types import Candle, MarketRules
from app.strategies.v17.swing import WEEK_MONDAY_OFFSET
from app.strategies.v18.trend import btc_side, load_v18

MIN, DAY = 60_000, 86_400_000
T0 = 20_000 * DAY + WEEK_MONDAY_OFFSET % (7 * DAY)
DAYS = 220


def price(d: float) -> float:
    trend = 100 * math.exp(0.006 * d) if d < 120 else 100 * math.exp(0.006 * 120 - 0.006 * (d - 120))
    return trend * (1 + 0.04 * math.sin(2 * math.pi * d / 7)) * (1 + 0.01 * math.sin(2 * math.pi * d))


def tape(sym: str):
    out = []
    for m in range(DAYS * 1440):
        p, q = price(m / 1440), price((m + 1) / 1440)
        t = T0 + m * MIN
        out.append(Candle(sym, "1m", t, p, max(p, q) * 1.0005, min(p, q) * 0.9995, q, 10.0, t + MIN - 1, True,
                          0.0, 0, "historical", 0.0))
    return out


@pytest.fixture(scope="module")
def bars():
    return {s: tape(s) for s in ("BTCUSDT", "ETHUSDT")}


def replay(cls, sym, bars):
    rules = {sym: MarketRules(symbol=sym, tick=0.01, step=0.001, min_qty=0.001, min_notional=5.0, maint_margin_rate=0.005,
                              price_precision=2, qty_precision=3)}
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0, strategy_starting_balance=1000.0)
    eng = ReplayEngine(settings, [sym], rules=rules, funding=None, execution=EXECUTION_V6, sizing=SizingV6(rules, jev=False),
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, fees=FEES_V6, fee_source="schedule",
                       leverage_policy="needed", cost_gate=None, seed=7)
    eng.portfolio.closed_trades = deque(maxlen=None)
    return eng.run(cls, iter(bars[sym]), since_ms=T0 + 90 * DAY, leverage=20, signal_tf="4h", only_symbol=sym).trades


@pytest.mark.parametrize("sid", ["V18.1", "V18.2", "V18.3", "V18.4", "V18.5"])
def test_family_trades_and_holds_days(bars, sid):
    trades = replay(load_v18()[sid].for_class("SWING"), "BTCUSDT", bars)
    assert trades, sid
    hold_h = sum((t.exit_ts - t.entry_ts) for t in trades) / len(trades) / 3_600_000
    assert hold_h >= 24, (sid, hold_h)


def test_btc_side_reads_completed_days_only():
    daily = {d: 100.0 + d for d in range(100)}                  # rising: last close above its 50-day mean
    assert btc_side(daily, 100 * DAY) == "long"
    assert btc_side({d: 200.0 - d for d in range(100)}, 100 * DAY) == "short"
    assert btc_side(daily, 30 * DAY) is None                     # not enough history


def test_btc_gate_blocks_alt_trades_against_btc(bars):
    base = load_v18()["V18.2"].for_class("SWING")
    always_short = type("G", (base,), {"regime": staticmethod(lambda h: "short"), "__module__": base.__module__})
    always_long = type("G", (base,), {"regime": staticmethod(lambda h: "long"), "__module__": base.__module__})
    assert {t.side for t in replay(always_short, "ETHUSDT", bars)} <= {"short"}
    assert {t.side for t in replay(always_long, "ETHUSDT", bars)} <= {"long"}
    assert replay(always_long, "ETHUSDT", bars)


def test_chandelier_exits_before_the_channel(bars):
    trades = replay(load_v18()["V18.4"].for_class("SWING"), "BTCUSDT", bars)
    assert any(t.exit_kind == "manual" for t in trades)
