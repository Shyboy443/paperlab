"""V15 day traders on a synthetic tape through the live stock engine: each family enters when its rule says so, at most
once per symbol per session, and every trade is closed inside its session (nothing held overnight). No network."""
from __future__ import annotations

import dataclasses
from collections import deque
from datetime import date, timedelta

import pytest

from app.competition import v9_config as v9
from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
from app.core.types import Candle, MarketRules
from app.live import alpaca_market as am
from app.live.stock_engine import StockReplayEngine
from app.strategies.v15.daytrade import load_v15_day_traders

MIN = 60_000
SYM = "SPY"


def calendar(n: int = 22) -> am.Sessions:
    days, d = [], date(2026, 8, 3)                               # a Monday
    while len(days) < n:
        if d.weekday() < 5:
            days.append({"date": d.isoformat(), "open": "09:30", "close": "16:00"})
        d += timedelta(days=1)
    return am.Sessions.from_calendar(days)


def tape(sessions: am.Sessions, loud_from: int = 16) -> list[Candle]:
    """Each session opens at the prior close and climbs 1% in a straight line; the first five minutes climb fastest.
    From session `loud_from` on, the opening five minutes trade five times the usual volume."""
    bars, price = [], 100.0
    for i, (a, b) in enumerate(sessions.iv):
        mins = (b - a) // MIN
        for k in range(mins):
            step = price * (0.0006 if k < 5 else 0.01 / mins)
            o, c = price, price + step
            vol = 50.0 if (k < 5 and i >= loud_from) else 10.0
            bars.append(Candle(SYM, "1m", a + k * MIN, o, c + step * 0.1, o - step * 0.1, c, vol, a + (k + 1) * MIN - 1,
                               True, 0.0, 0, "backfill", 0.0))
            price = c
    return am.session_tape(SYM, bars, sessions)


def replay(sid: str, sessions: am.Sessions, bars: list[Candle]):
    rules = {SYM: MarketRules(**v9.RULES[SYM])}
    cls = load_v15_day_traders()[sid].for_class("DAY", session_at=sessions.at)
    eng = StockReplayEngine(dataclasses.replace(v9.settings_v9(), strategy_halt_pct=1.0, daily_halt_pct=1.0), [SYM],
                            rules=rules, seed=7, funding=None, execution=v9.EXECUTION_V9, fees=v9.FEES_V9,
                            fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                            max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
    eng.portfolio.closed_trades = deque(maxlen=None)
    return eng.run(cls, bars, since_ms=sessions.iv[0][0], leverage=v9.LEVERAGE_CAP, signal_tf="5m", only_symbol=SYM).trades


@pytest.fixture(scope="module")
def market():
    s = calendar()
    return s, tape(s)


@pytest.mark.parametrize("sid", ["V15.1", "V15.2", "V15.3", "V15.4", "V15.5"])
def test_one_trade_a_session_and_flat_by_the_close(market, sid):
    sessions, bars = market
    trades = replay(sid, sessions, bars)
    days = [sessions.at(t.entry_ts) for t in trades]
    assert all(d is not None for d in days) and len(set(days)) == len(days)          # at most one per session
    for t, (a, b) in zip(trades, days):
        assert a <= t.entry_ts and t.exit_ts < b                                        # closed inside its session


def test_opening_candle_follows_the_first_five_minutes(market):
    sessions, bars = market
    trades = replay("V15.1", sessions, bars)
    assert len(trades) >= len(sessions.iv) - 1 and all(t.side == "long" for t in trades)
    a, _ = sessions.at(trades[0].entry_ts)
    assert a + 5 * MIN <= trades[0].entry_ts <= a + 7 * MIN                            # right after the first candle


def test_stocks_in_play_needs_unusual_opening_volume(market):
    sessions, bars = market
    trades = replay("V15.2", sessions, bars)
    loud = {sessions.iv[i][0] for i in range(16, len(sessions.iv))}
    assert trades and {sessions.at(t.entry_ts)[0] for t in trades} <= loud


def test_breakout_waits_for_the_half_hour_and_last30_trades_the_end(market):
    sessions, bars = market
    for t in replay("V15.3", sessions, bars):
        a, b = sessions.at(t.entry_ts)
        assert t.side == "long" and a + 35 * MIN <= t.entry_ts <= b - 59 * MIN
    late = replay("V15.5", sessions, bars)
    assert late
    for t in late:
        a, b = sessions.at(t.entry_ts)
        assert t.side == "long" and b - 30 * MIN <= t.entry_ts < b
