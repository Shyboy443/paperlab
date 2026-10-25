"""The YouTube "tested 100 times" strategies from the operator's ranking PDF (Trading Strategy Rankings, 2026-10-19),
written down as exact rules. The PDF gives only each video's title, reward:risk, win rate, timeframe and "GOA" (the net
R over its 100 hand-taken trades at 1 % risk); it does not give the rules. These are the standard published versions
of each setup, fixed here BEFORE any result was seen (docs/YT_STRATEGIES_STUDY.md). Both directions, as in the videos.

Shared conventions:
- a signal is read at a candle's CLOSE and filled at the next candle's open;
- "trend filter" = close above the 200 EMA for longs, below for shorts;
- "swing stop" = the lowest low (long) / highest high (short) of the last SWING candles, signal candle included;
- the target is RR x the risk from the actual fill; a 2-stage take profit closes half at each target.

Each rule returns (sig, stop): sig +1 long / -1 short / 0 per candle, stop = the stop price for that signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import ind as I

SWING = 10
M15, M30, H1, H4 = 15, 30, 60, 240


@dataclass(frozen=True)
class Strategy:
    key: str
    title: str                 # the video title in the PDF
    tf: int                    # minutes
    targets: tuple             # ((share of the position, R multiple), ...)
    claim_wr: float            # the PDF's win rate, %
    claim_goa: float           # the PDF's "GOA" = net R over 100 trades, %
    rule: str                  # the rule in words
    fn: Callable
    assets: tuple = ()         # an asset the PDF names for this video (operator 2026-10-20: then ONLY that asset is tested)


def _arr(*xs):
    return [np.asarray(x, dtype=float) for x in xs]


def swing(h, l, sig):
    lo, hi = I.lowest(l, SWING), I.highest(h, SWING)
    return np.where(sig > 0, lo, np.where(sig < 0, hi, np.nan))


def _sig(long, short):
    long, short = np.nan_to_num(long).astype(bool), np.nan_to_num(short).astype(bool)
    return np.where(long, 1, np.where(short, -1, 0))


def _trend(c):
    e = I.ema(c, 200)
    with np.errstate(invalid="ignore"):
        return c > e, c < e


# -- the rules -----------------------------------------------------------------------------------------------------------
def rsi_stoch_ema(o, h, l, c, v):
    up, dn = _trend(c)
    r = I.rsi(c, 14)
    k, d = I.stoch(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & (r > 50) & I.cross_up(k, d) & (I.shift(k, 1) < 20),
                 dn & (r < 50) & I.cross_down(k, d) & (I.shift(k, 1) > 80))
    return s, swing(h, l, s)


def ichimoku(o, h, l, c, v):
    ten, kij, ca, cb, a_now, b_now = I.ichimoku(h, l)
    lag = I.shift(c, 26)
    with np.errstate(invalid="ignore"):
        above, below = c > np.fmax(ca, cb), c < np.fmin(ca, cb)
        s = _sig(above & I.cross_up(ten, kij) & (c > lag) & (a_now > b_now),
                 below & I.cross_down(ten, kij) & (c < lag) & (a_now < b_now))
    return s, swing(h, l, s)


def ash(o, h, l, c, v):
    """Absolute Strength Histogram (RSI mode, length 9, smoothing 3) crossing, with the trend filter."""
    up, dn = _trend(c)
    pc = I.shift(c, 1)
    bulls, bears = 0.5 * (np.abs(c - pc) + (c - pc)), 0.5 * (np.abs(c - pc) - (c - pc))
    sb, sr = I.ema(I.ema(bulls, 9), 3), I.ema(I.ema(bears, 9), 3)
    s = _sig(up & I.cross_up(sb, sr), dn & I.cross_down(sb, sr))
    return s, swing(h, l, s)


def cmf_macd_atr(o, h, l, c, v):
    """MACD crosses its signal line, CMF(20) agrees, trend filter; stop 1.5 ATR(14)."""
    up, dn = _trend(c)
    m, sg = I.macd(c)
    f = I.cmf(h, l, c, v)
    a = I.atr(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(m, sg) & (f > 0), dn & I.cross_down(m, sg) & (f < 0))
    return s, np.where(s > 0, c - 1.5 * a, np.where(s < 0, c + 1.5 * a, np.nan))


def mfi_ema_stoch(o, h, l, c, v):
    up, dn = _trend(c)
    m = I.mfi(h, l, c, v)
    k, d = I.stoch(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(k, d) & (I.shift(k, 1) < 20) & (I.lowest(m, 3) < 30),
                 dn & I.cross_down(k, d) & (I.shift(k, 1) > 80) & (I.highest(m, 3) > 70))
    return s, swing(h, l, s)


def triple_supertrend(o, h, l, c, v):
    """200 EMA + Stoch RSI cross from beyond 20/80 + at least 2 of Supertrend (12,3), (11,2), (10,1) agreeing; the stop
    is the second-nearest agreeing Supertrend line."""
    up, dn = _trend(c)
    k, d = I.stoch_rsi(c)
    lines = [I.supertrend(h, l, c, n, m) for n, m in ((12, 3.0), (11, 2.0), (10, 1.0))]
    bull = sum((dd > 0).astype(int) for _, dd in lines)
    bear = sum((dd < 0).astype(int) for _, dd in lines)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(k, d) & (I.shift(k, 1) < 20) & (bull >= 2),
                 dn & I.cross_down(k, d) & (I.shift(k, 1) > 80) & (bear >= 2))
    stop = np.full(len(c), np.nan)
    for i in np.flatnonzero(s):
        vals = sorted((ln[i] for ln, dd in lines if dd[i] == s[i]), reverse=s[i] > 0)
        stop[i] = vals[1] if len(vals) >= 2 else np.nan
    return s, stop


def ema_stochrsi_atr(o, h, l, c, v):
    """EMA 8 > 14 > 50 fanned and the close above the 8 EMA, Stoch RSI crosses up; stop 3 ATR, target 2 ATR."""
    e8, e14, e50 = I.ema(c, 8), I.ema(c, 14), I.ema(c, 50)
    k, d = I.stoch_rsi(c)
    a = I.atr(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig((e8 > e14) & (e14 > e50) & (c > e8) & I.cross_up(k, d),
                 (e8 < e14) & (e14 < e50) & (c < e8) & I.cross_down(k, d))
    return s, np.where(s > 0, c - 3 * a, np.where(s < 0, c + 3 * a, np.nan))


def macd_ema(o, h, l, c, v):
    """The classic: trend filter, MACD crosses its signal line below zero (longs) / above zero (shorts)."""
    up, dn = _trend(c)
    m, sg = I.macd(c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(m, sg) & (m < 0), dn & I.cross_down(m, sg) & (m > 0))
    return s, swing(h, l, s)


def wavetrend(o, h, l, c, v):
    up, dn = _trend(c)
    w1, w2 = I.wavetrend(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(w1, w2) & (w1 < -53), dn & I.cross_down(w1, w2) & (w1 > 53))
    return s, swing(h, l, s)


def stc(o, h, l, c, v):
    up, dn = _trend(c)
    x = I.stc(c)
    s = _sig(up & I.cross_up(x, 25), dn & I.cross_down(x, 75))
    return s, swing(h, l, s)


def alligator(o, h, l, c, v):
    """Lips (5,3) / teeth (8,5) / jaw (13,8) of the median price open up in order, the close beyond all three."""
    hl2 = (h + l) / 2
    jaw, teeth, lips = I.shift(I.smma(hl2, 13), 8), I.shift(I.smma(hl2, 8), 5), I.shift(I.smma(hl2, 5), 3)
    with np.errstate(invalid="ignore"):
        bull, bear = (lips > teeth) & (teeth > jaw), (lips < teeth) & (teeth < jaw)
        s = _sig(bull & ~I.shift(bull.astype(float), 1).astype(bool) & (c > lips),
                 bear & ~I.shift(bear.astype(float), 1).astype(bool) & (c < lips))
    return s, swing(h, l, s)


def ttm_squeeze(o, h, l, c, v):
    """Bollinger (20, 2) leaves the Keltner channel (20, 1.5 ATR): the squeeze fires in the momentum's direction."""
    up, dn = _trend(c)
    basis = I.sma(c, 20)
    dev = I.rolling(c, 20, np.std)
    a = I.atr(h, l, c, 20)
    with np.errstate(invalid="ignore"):
        on = (basis + 2 * dev < basis + 1.5 * a) & (basis - 2 * dev > basis - 1.5 * a)
        fired = I.shift(on.astype(float), 1).astype(bool) & ~on
    mom = I.linreg(c - ((I.highest(h, 20) + I.lowest(l, 20)) / 2 + basis) / 2, 20)
    with np.errstate(invalid="ignore"):
        s = _sig(up & fired & (mom > 0), dn & fired & (mom < 0))
    return s, swing(h, l, s)


def ha_psar_ema(o, h, l, c, v):
    """Heikin Ashi turns colour, the Parabolic SAR flips to the same side, trend filter; stop at the SAR."""
    up, dn = _trend(c)
    ho, hc = I.heikin_ashi(o, h, l, c)
    green = hc > ho
    sar, sd = I.psar(h, l)
    flip_up, flip_dn = (sd > 0) & (I.shift(sd, 1) < 0), (sd < 0) & (I.shift(sd, 1) > 0)
    pg = I.shift(green.astype(float), 1)
    with np.errstate(invalid="ignore"):
        s = _sig(up & green & (pg == 0) & flip_up, dn & ~green & (pg == 1) & flip_dn)
    return s, np.where(s != 0, sar, np.nan)


def supertrend(o, h, l, c, v):
    up, dn = _trend(c)
    line, d = I.supertrend(h, l, c, 10, 3.0)
    pd = I.shift(d, 1)
    s = _sig(up & (d > 0) & (pd < 0), dn & (d < 0) & (pd > 0))
    return s, np.where(s != 0, line, np.nan)


def engulfing(o, h, l, c, v):
    up, dn = _trend(c)
    po, pc = I.shift(o, 1), I.shift(c, 1)
    with np.errstate(invalid="ignore"):
        bull = (pc < po) & (c > o) & (c >= po) & (o <= pc)
        bear = (pc > po) & (c < o) & (c <= po) & (o >= pc)
    s = _sig(up & bull, dn & bear)
    lo2, hi2 = np.fmin(l, I.shift(l, 1)), np.fmax(h, I.shift(h, 1))
    return s, np.where(s > 0, lo2, np.where(s < 0, hi2, np.nan))


def darvas(o, h, l, c, v):
    """Box = the prior 20 candles whose high was set at least 4 candles ago; a close beyond the box breaks out; the stop
    is the other side of the box."""
    top, bot = I.shift(I.highest(h, 20), 1), I.shift(I.lowest(l, 20), 1)
    hi_age = np.full(len(c), np.nan)
    lo_age = np.full(len(c), np.nan)
    for i in range(21, len(c)):
        w_h, w_l = h[i - 20:i], l[i - 20:i]
        hi_age[i] = 19 - int(np.argmax(w_h))            # candles since the box high
        lo_age[i] = 19 - int(np.argmin(w_l))
    with np.errstate(invalid="ignore"):
        s = _sig((c > top) & (I.shift(c, 1) <= top) & (hi_age >= 4), (c < bot) & (I.shift(c, 1) >= bot) & (lo_age >= 4))
    return s, np.where(s > 0, bot, np.where(s < 0, top, np.nan))


def triple_ema(o, h, l, c, v):
    """EMA 25 > 50 > 100 fanned; the candle pulls back into the 25 / 50 zone and closes back above the 25 EMA."""
    e25, e50, e100 = I.ema(c, 25), I.ema(c, 50), I.ema(c, 100)
    with np.errstate(invalid="ignore"):
        s = _sig((e25 > e50) & (e50 > e100) & (l <= e25) & (l > e100) & (c > e25) & (c > o),
                 (e25 < e50) & (e50 < e100) & (h >= e25) & (h < e100) & (c < e25) & (c < o))
    return s, swing(h, l, s)


def rsi_simple(o, h, l, c, v):
    up, dn = _trend(c)
    r = I.rsi(c, 14)
    s = _sig(up & I.cross_up(r, 30), dn & I.cross_down(r, 70))
    return s, swing(h, l, s)


def stoch_simple(o, h, l, c, v):
    up, dn = _trend(c)
    k, d = I.stoch(h, l, c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(k, d) & (I.shift(k, 1) < 20), dn & I.cross_down(k, d) & (I.shift(k, 1) > 80))
    return s, swing(h, l, s)


def stochrsi_simple(o, h, l, c, v):
    up, dn = _trend(c)
    k, d = I.stoch_rsi(c)
    with np.errstate(invalid="ignore"):
        s = _sig(up & I.cross_up(k, d) & (I.shift(k, 1) < 20), dn & I.cross_down(k, d) & (I.shift(k, 1) > 80))
    return s, swing(h, l, s)


def awesome(o, h, l, c, v):
    up, dn = _trend(c)
    hl2 = (h + l) / 2
    ao = I.sma(hl2, 5) - I.sma(hl2, 34)
    s = _sig(up & I.cross_up(ao, 0), dn & I.cross_down(ao, 0))
    return s, swing(h, l, s)


def ema_cross(o, h, l, c, v):
    e9, e21 = I.ema(c, 9), I.ema(c, 21)
    s = _sig(I.cross_up(e9, e21), I.cross_down(e9, e21))
    return s, swing(h, l, s)


T2, T15, T1 = ((1.0, 2.0),), ((1.0, 1.5),), ((1.0, 1.0),)
# The PDF names an asset class for two of these videos ("Forex"): those are tested on the 7 forex majors only
# (the operator's MT5 terminal data, scripts/mt5_export.py).
FOREX = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
STRATEGIES: tuple[Strategy, ...] = (
    Strategy("rsi_stoch_ema", "Highly Profitable Trading Strategy Proven 100 Trades - RSI + Stochastic + 200 EMA", M30, T2, 54, 62,
             "200 EMA trend; RSI(14) on the trend side of 50; Stochastic(14,3,3) crosses out of oversold/overbought; swing stop; 2R", rsi_stoch_ema),
    Strategy("ichimoku", "Crazy Results Best Ichimoku Cloud Trading Strategy Proven 100 Trades", H1, T2, 52, 56,
             "close beyond the cloud; Tenkan crosses Kijun; lagging close beyond the close 26 back; future cloud agrees; swing stop; 2R", ichimoku),
    Strategy("ash", "Highly Profitable Indicator Absolute Strength Histogram Proven 100 Trades", M30, T2, 50, 50,
             "200 EMA trend; ASH(9,3, RSI mode) bulls/bears cross; swing stop; 2R", ash),
    Strategy("cmf_macd_atr", "Chaikin Money Flow + MACD + ATR Best Simple Forex Trading Strategy Tested 100 Times", M30, T2, 50, 50,
             "200 EMA trend; MACD crosses signal; CMF(20) agrees; stop 1.5 ATR(14); 2R", cmf_macd_atr, FOREX),
    Strategy("mfi_ema_stoch", "Complete Trading Strategy With Proven Results - MFI + 200 EMA + Stochastic", M30, T2, 48, 44,
             "200 EMA trend; MFI(14) below 30 / above 70 in the last 3 candles; Stochastic crosses out of oversold/overbought; swing stop; 2R", mfi_ema_stoch),
    Strategy("triple_supertrend", "HIGHEST PROFIT Triple Supertrend Trading Strategy With Proven 100 Trade Results", H1, T15, 56, 40,
             "200 EMA trend; Stoch RSI crosses out of 20/80; 2 of 3 Supertrends agree; stop at the 2nd Supertrend line; 1.5R", triple_supertrend),
    Strategy("ema_stochrsi_atr", "76% Win Rate Highly Profitable Trading Strategy Proven 100 Trades - EMA + Stochastic RSI + ATR", H1, ((1.0, 2 / 3),), 76, 40,
             "EMA 8/14/50 fanned, close beyond the 8; Stoch RSI crosses; stop 3 ATR, target 2 ATR", ema_stochrsi_atr),
    Strategy("macd_2stage", "Highly Profitable MACD Trading Strategy Proven 100 Trades (2 Stage Take Profit)", M30, ((0.5, 1.0), (0.5, 2.0)), 62, 40,
             "200 EMA trend; MACD crosses signal below / above zero; swing stop; half at 1R, half at 2R", macd_ema),
    Strategy("macd_ema_1r4", "200 EMA + MACD Trading Strategy Tested 100 Times With 1:4 Risk Reward Ratio", M30, ((1.0, 4.0),), 28, 40,
             "200 EMA trend; MACD crosses signal below / above zero; swing stop; 4R", macd_ema),
    Strategy("macd_ema_1r3", "200 EMA + MACD Trading Strategy Tested 100 Times With 3:1 RR", M30, ((1.0, 3.0),), 33, 32,
             "200 EMA trend; MACD crosses signal below / above zero; swing stop; 3R", macd_ema),
    Strategy("macd_ema_1r15", "MACD Trading Strategy 200 Times! Full Results", M30, T15, 53, 34,
             "200 EMA trend; MACD crosses signal below / above zero; swing stop; 1.5R", macd_ema),
    Strategy("macd_ema_1r1", "MACD Trading Strategy But With A 1:1 Risk Reward Ratio 100 Trade Results", M30, T1, 62, 24,
             "200 EMA trend; MACD crosses signal below / above zero; swing stop; 1R", macd_ema),
    Strategy("wavetrend", "Profitable WaveTrend Oscillator Trading Strategy Tested 100 Times", M30, T2, 44, 32,
             "200 EMA trend; WaveTrend(10,21) crosses below -53 / above +53; swing stop; 2R", wavetrend),
    Strategy("stc", "STC Indicator Trading Strategy Proven 100 Trades", M30, T2, 44, 32,
             "200 EMA trend; STC(10,23,50) crosses up through 25 / down through 75; swing stop; 2R", stc),
    Strategy("alligator", "Williams Alligator Indicator Traded 100 Times To See How Profitable It Is", M30, T2, 43, 29,
             "Alligator lines open in order, close beyond the lips; swing stop; 2R", alligator),
    Strategy("ttm_squeeze", "TTM Squeeze Forex Trading Strategy Tested 100 Times", M30, T2, 42, 26,
             "200 EMA trend; the squeeze fires with momentum on the trend side; swing stop; 2R", ttm_squeeze, FOREX),
    Strategy("ha_psar_ema", "Testing Heikin Ashi + Parabolic Sar + 200 EMA Trading Srategy 200 Times", M15, T2, 43, 29,
             "200 EMA trend; Heikin Ashi turns colour as the SAR flips; stop at the SAR; 2R", ha_psar_ema),
    Strategy("triple_ema", "Simple Profitable Triple EMA Trading Strategy Tested 100 Times", M15, T2, 42, 26,
             "EMA 25/50/100 fanned; pullback into the 25-100 zone closing back beyond the 25; swing stop; 2R", triple_ema),
    Strategy("rsi_simple", "Easy Profitable RSI Indicator Trading Strategy - Surprisingly Good Results", M15, ((1.0, 1.3),), 51, 25.3,
             "200 EMA trend; RSI(14) crosses back above 30 / below 70; swing stop; 1.3R", rsi_simple),
    Strategy("supertrend", "Supertrend Indicator Trading Strategy Tested 100 Times - Full Result", M15, ((1.0, 1.6),), 48, 24.8,
             "200 EMA trend; Supertrend(10,3) flips; stop at the Supertrend line; 1.6R", supertrend),
    Strategy("engulfing", "Engulfing Candlestick Pattern Trading Strategy Tested 100 Times - Full Result", H4, T1, 62, 24,
             "200 EMA trend; an engulfing candle; stop beyond the two candles; 1R", engulfing),
    Strategy("darvas", "4 Hour Chart Swing Trading Strategy Tested 100 Times (Darvas Box Strategy)", H4, T15, 49, 22.5,
             "a close out of a 20-candle box whose extreme is at least 4 candles old; stop at the box's other side; 1.5R", darvas),
    Strategy("stoch_1r6", "Taking 100 Trades With A 1:6 Risk Reward Ratio Stochastic Strategy", M30, ((1.0, 6.0),), 17, 19,
             "200 EMA trend; Stochastic crosses out of oversold/overbought; swing stop; 6R", stoch_simple),
    Strategy("stochrsi", "Simple Stochastic RSI Day Trading Strategy Tested 100 Times", M15, T2, 39, 17,
             "200 EMA trend; Stoch RSI crosses out of 20/80; swing stop; 2R", stochrsi_simple),
    Strategy("stoch_1r05", "Taking 100 Trades With A 1:0.5 Risk Reward Ratio Stochastic Strategy Part 2", M30, ((1.0, 0.5),), 71, 6.5,
             "200 EMA trend; Stochastic crosses out of oversold/overbought; swing stop; 0.5R", stoch_simple),
    Strategy("awesome", "Trading Awesome Oscillator Indicator 100 Times - How Profitable Is It?", M30, T2, 35, 5,
             "200 EMA trend; Awesome Oscillator crosses zero; swing stop; 2R", awesome),
    Strategy("ema_cross", "EMA Crossover Day Trading Strategy Tested 100 Times! Full Results", M15, T15, 36, -10,
             "EMA 9 crosses EMA 21; swing stop; 1.5R", ema_cross),
)
BY_KEY = {s.key: s for s in STRATEGIES}
