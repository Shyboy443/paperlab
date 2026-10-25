"""Scribd round 7: volatility-ETF shorts, calendar seasonality and the lunar effect (docs/SCRIBD_ROUND7_STUDY.md),
written down before any result. Daily candles (swing.D); trades {t, exit_t, dir, entry, ret_pct, r, result}."""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from . import ind as I
from .round3 import _trade, _window
from .swing import D

DAY = 86_400_000


# ------------------------------------------------------------------------------------------------- volatility ETF
def _short_signal_rule(b: D, enter: np.ndarray, cover: np.ndarray, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Short at the next open after `enter` at a close; cover at the next open after `cover` at a close."""
    first, last = _window(b, s0, s1)
    out, i = [], max(first, 21)
    while i < last:
        if not enter[i]:
            i += 1
            continue
        j = i + 1
        k = j
        while k < last and not cover[k]:
            k += 1
        x = min(k + 1, last)
        out.append(_trade(b.t[j], b.t[x], -1, b.o[j], b.o[x], cost, "cover" if k < last else "end"))
        i = x
    return out


def grossmann_vxx_bb(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Strategies for Trading Inverse Volatility (250728698): Bollinger(20, 1.4) on VXX (VIXY). A close below the middle
    line -> short at the next open; a close above the upper band -> cover at the next open."""
    mid = I.sma(b.c, 20)
    up = mid + 1.4 * I.rolling(b.c, 20, np.std)
    return _short_signal_rule(b, np.nan_to_num(b.c < mid).astype(bool), np.nan_to_num(b.c > up).astype(bool), cost, s0, s1)


def grossmann_vxx_sma(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Strategies for Trading Inverse Volatility (250728698), SMA variant: SMA5 crosses below SMA15 -> short at the next
    open; SMA5 crosses above SMA15 -> cover at the next open."""
    f, s = I.sma(b.c, 5), I.sma(b.c, 15)
    dn = np.r_[False, (f[:-1] >= s[:-1]) & (f[1:] < s[1:])]
    upx = np.r_[False, (f[:-1] <= s[:-1]) & (f[1:] > s[1:])]
    return _short_signal_rule(b, np.nan_to_num(dn).astype(bool), np.nan_to_num(upx).astype(bool), cost, s0, s1)


# ------------------------------------------------------------------------------------------------- seasonality
def _easter(y: int) -> dt.date:
    a, bq, c = y % 19, y // 100, y % 100
    d, e = bq // 4, bq % 4
    f = (bq + 8) // 25
    g = (bq - f + 1) // 3
    h = (19 * a + bq - d - g + 15) % 30
    i, k = c // 4, c % 4
    l2 = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l2) // 451
    month = (h + l2 - 7 * m + 114) // 31
    day = (h + l2 - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(y, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, month: int, weekday: int) -> dt.date:
    d = dt.date(y, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(y, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d


def market_holidays(y0: int, y1: int) -> set[dt.date]:
    """Minton's list: Presidents' Day, Good Friday, Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas,
    New Year's Day (calendar rules, observed dates)."""
    out = set()
    for y in range(y0, y1 + 2):
        out |= {_nth_weekday(y, 2, 0, 3), _easter(y) - dt.timedelta(days=2), _last_weekday(y, 5, 0), _observed(dt.date(y, 7, 4)),
                _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4), _observed(dt.date(y, 12, 25)), _observed(dt.date(y, 1, 1))}
    return out


def _held_trades(b: D, held: np.ndarray, cost: float, s0: int, s1: int, why: str) -> list[dict[str, Any]]:
    """Each run of held days is one trade: bought at the close before its first day, sold at the close of its last."""
    first, last = _window(b, s0, s1)
    out, i = [], max(first, 1)
    while i <= last:
        if not held[i]:
            i += 1
            continue
        k = i
        while k + 1 <= last and held[k + 1]:
            k += 1
        out.append(_trade(b.t[i - 1], b.t[k], 1, b.c[i - 1], b.c[k], cost, why))
        i = k + 1
    return out


def _month_positions(b: D):
    days = [dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).date() for x in b.t]
    ym = np.array([d.year * 12 + d.month for d in days])
    starts = np.flatnonzero(np.r_[True, ym[1:] != ym[:-1]])
    ends = np.r_[starts[1:], len(ym)]
    fwd = np.zeros(len(ym), int)                      # 1-based trading day of the month
    back = np.zeros(len(ym), int)                     # -1 = last trading day
    for s, e in zip(starts, ends):
        fwd[s:e] = np.arange(1, e - s + 1)
        back[s:e] = -np.arange(e - s, 0, -1)
    return days, fwd, back


def fosback_seasonal(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Fosback's Seasonality Indicator (in 40367053): hold the S&P 500 (SPY) on the last trading day and the first four
    trading days of each month, and on the two trading days before each market holiday (Presidents' Day, Good Friday,
    Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas, New Year's Day)."""
    days, fwd, back = _month_positions(b)
    hol = market_holidays(days[0].year, days[-1].year)
    held = (back == -1) | ((fwd >= 1) & (fwd <= 4))
    for i, d in enumerate(days):                      # the two trading days before a holiday
        for step in (1, 2, 3, 4):                     # the holiday falls within the next few calendar days
            nd = d + dt.timedelta(days=step)
            if nd in hol:
                nxt = days[i + 1] if i + 1 < len(days) else None
                if nxt is None or nxt > nd:           # this is the last trading day before the holiday
                    held[i] = True
                    if i >= 1:
                        held[i - 1] = True
                break
    return _held_trades(b, held, cost, s0, s1, "seasonal window")


def kaeppel_monthly10(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Kaeppel's "Monthly 10" (in 40367053): hold the Dow (DIA) on the last two and first four trading days of each
    month, plus trading days 9-12."""
    _, fwd, back = _month_positions(b)
    held = (back >= -2) | ((fwd >= 1) & (fwd <= 4)) | ((fwd >= 9) & (fwd <= 12))
    return _held_trades(b, held, cost, s0, s1, "seasonal window")


# ------------------------------------------------------------------------------------------------- lunar
SYNODIC = 29.530588853
NEW_MOON_2000 = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc).timestamp() * 1000


def lunar_phase_sign(t_ms: np.ndarray) -> np.ndarray:
    """+1 when the nearest phase centre is a new moon, -1 when it is a full moon."""
    age = ((t_ms + 16 * 3_600_000 - NEW_MOON_2000) / DAY) % SYNODIC        # the US close is ~20-21h UTC; day stamps are 00:00
    return np.where((age < SYNODIC / 4) | (age >= 3 * SYNODIC / 4), 1, -1)


def lunar_phase(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Are Investors Moonstruck? (372211422): long during the 15 days around each new moon, short during the 15 days
    around each full moon; each window is one trade, close to close."""
    sgn = lunar_phase_sign(b.t)
    first, last = _window(b, s0, s1)
    out, i = [], max(first, 1)
    while i <= last:
        d = int(sgn[i])
        k = i
        while k + 1 <= last and sgn[k + 1] == d:
            k += 1
        out.append(_trade(b.t[i - 1], b.t[k], d, b.c[i - 1], b.c[k], cost, "new moon" if d > 0 else "full moon"))
        i = k + 1
    return out
