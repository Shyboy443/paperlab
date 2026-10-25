# FROZEN COPY of app/divergence/model.py at DIV-SR-5 (2026-10-11), kept only for the DIV-SR-5 vs DIV-SR-6 comparison.
"""Frozen, causal RSI(14) divergence rules (DIV-SR-5); no broker access.

Times are UTC milliseconds. ``t`` is a candle OPEN time; all evidence exposes
its availability time separately. Feed completed 15-minute OHLCV dictionaries.

Four types on two swings of CLOSING prices (docs/DIVERGENCE_RULES.md):

    regular bullish   close lower low,   RSI higher low   -> long  (reversal)
    regular bearish   close higher high, RSI lower high   -> short (reversal)
    hidden bullish    close higher low,  RSI lower low    -> long  (continuation)
    hidden bearish    close lower high,  RSI higher high  -> short (continuation)

DIV-SR-4 (2026-10-11): swings on closes, not wicks; each swing is compared with every earlier same-side swing (the
longest clean divergence wins), equal highs/lows are not recorded, and support/resistance comes from major 4h levels.
DIV-SR-5 (2026-10-11): volume confirmation = buying vs selling volume on the lower timeframe since swing 2.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
import math

MINUTE = 60_000
BASE = 15 * MINUTE
HOUR = 60 * MINUTE
RULE_VERSION = "DIV-SR-5"
PIVOT_LEFT, PIVOT_RIGHT = 5, 3          # a swing is the extreme CLOSE of 5 bars before and 3 after; known 3 bars later
MIN_RSI_GAP = 3.0                       # RSI points between the two swings
SEPARATION = (5, 60)                    # divergence-timeframe bars between the two swings
TREND_EMA, TREND_SLOPE = 50, 10         # hidden divergences must agree with the EMA50 trend
VOLUME_MIN = 1.0                        # lower-TF volume with the trade >= against it, from swing 2 to the break
SWING_GAP_ATR = 0.25                    # swing 2 must beat swing 1 by >= 0.25 ATR (ATR at swing 2); less = equal
CHASE_MAX_ATR = 1.5                     # entry no further than 1.5 ATR from swing 2
ROOM_LOOKBACK = 60                      # divergence-timeframe bars searched for an opposite swing blocking the target
LEVEL_TF, LEVEL_TOL_ATR, LEVEL_MIN_TOUCHES = "4h", 0.5, 2   # major S/R: clusters of >= 2 4h swing closes
STOP_BUFFER_ATR, TRIGGER_MAX_AGE, EXPIRY_BARS = 0.1, 20, 6
HISTORY_DAYS = 90                       # completed 15m history the scanner analyses and the chart shows
TYPES = ("regular_bullish", "regular_bearish", "hidden_bullish", "hidden_bearish")
RULES = {
    "version": RULE_VERSION,
    "pairs": [["4h", "1h"], ["1h", "15m"]],
    "history_days": HISTORY_DAYS,
    "rsi_period": 14, "atr_period": 14,
    "swings": "closing prices: a close below (above) the closes of the 5 bars before and 3 after; known 3 bars later",
    "pivot_left": PIVOT_LEFT, "pivot_right": PIVOT_RIGHT,
    "divergence": "each new swing vs every earlier same-side swing 5-60 bars back; the earliest valid one per type; RSI at the swing candles",
    "types": {"regular_bullish": "close LL, RSI HL -> long", "regular_bearish": "close HH, RSI LH -> short",
              "hidden_bullish": "close HL, RSI LL -> long", "hidden_bearish": "close LH, RSI HH -> short"},
    "min_rsi_gap": MIN_RSI_GAP, "swing_gap_atr": SWING_GAP_ATR,
    "equal_swings": "closes less than 0.25 ATR apart are an equal high/low: not a divergence, not recorded",
    "clean_lines": "no close and no RSI value between the swings crosses either connecting line",
    "regular_rsi_zone": "bullish: a swing RSI <= 40; bearish: a swing RSI >= 60",
    "pivot_min_separation": SEPARATION[0], "pivot_max_separation": SEPARATION[1],
    "regular_prior_trend": "at swing 1: bullish close < EMA50 and EMA50 below its value 10 bars earlier; bearish mirrored",
    "major_levels": "4h swing closes (highs and lows) of the stored history, clustered within 0.5 x 4h ATR; >= 2 swings = a level",
    "regular_filter": "swing 2 close within 0.5 x 4h ATR of a major level whose swings were all known before swing 1 formed",
    "hidden_filter": "trend: close beyond EMA50 and EMA50 sloping that way over 10 bars, at availability",
    "confirmation": "first lower-timeframe close beyond the latest opposing lower-timeframe swing close (2/2), frozen at availability",
    "volume_confirmation": "lower-timeframe volume of candles closing in the trade direction >= volume of candles closing against it, from the close of the swing-2 candle to the break (inclusive)",
    "no_chase": "skip if entry > 1.5 ATR from swing 2, or a confirmed opposite swing of the last 60 bars lies between entry and the 2R target",
    "trigger_max_age_bars": TRIGGER_MAX_AGE, "expiry_divergence_bars": EXPIRY_BARS,
    "stop": "0.1 ATR beyond the WICK of the swing 2 candle", "stop_buffer_atr": STOP_BUFFER_ATR, "reward_risk": 2.0,
    "entry": "next available contiguous 15m open after trigger close; no same-bar fill",
    "risk_fraction": 0.005, "max_notional_equity": 1.0,
    "fee_per_side": 0.0004, "slippage_per_side": 0.0002,
    "gap_policy": "reset indicators/pivots across gaps; invalidate pending setups",
    "news": "context only; does not change frozen execution rules",
}
LABELS = {"regular_bullish": ("lower low", "higher low"), "regular_bearish": ("higher high", "lower high"),
          "hidden_bullish": ("higher low", "lower low"), "hidden_bearish": ("lower high", "higher high")}


def clean_bars(bars, now_ms=None):
    """Reject malformed candles; duplicate timestamps resolve to final input row."""
    found = {}
    for raw in bars:
        try:
            row = {k: float(raw[k]) for k in ("o", "h", "l", "c", "v")}
            t = int(raw["t"])
            if t % BASE or (now_ms is not None and t + BASE > now_ms):
                continue
            if not all(math.isfinite(x) for x in row.values()):
                continue
            if min(row[k] for k in ("o", "h", "l", "c")) <= 0 or row["v"] < 0:
                continue
            if row["h"] < max(row["o"], row["c"], row["l"]) or row["l"] > min(row["o"], row["c"]):
                continue
            found[t] = dict(t=t, **row)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return [found[t] for t in sorted(found)]


def aggregate(bars, duration):
    """UTC-aligned bars, only complete buckets with every constituent present."""
    duration = {"15m": BASE, "1h": HOUR, "4h": 4 * HOUR}.get(duration, duration)
    if duration == BASE:
        return list(bars)
    buckets = {}
    for bar in bars:
        buckets.setdefault(bar["t"] // duration * duration, []).append(bar)
    result = []
    count = duration // BASE
    for t, rows in sorted(buckets.items()):
        if len(rows) != count or any(r["t"] != t + i * BASE for i, r in enumerate(rows)):
            continue
        result.append(dict(t=t, o=rows[0]["o"], h=max(r["h"] for r in rows),
                           l=min(r["l"] for r in rows), c=rows[-1]["c"],
                           v=sum(r["v"] for r in rows)))
    return result


def indicators(bars, duration):
    """Wilder RSI(14) and ATR(14), independently seeded after each data gap."""
    duration = {"15m": BASE, "1h": HOUR, "4h": 4 * HOUR}.get(duration, duration)
    n = len(bars)
    rsi, atr, segments = [None] * n, [None] * n, [0] * n
    gains, losses, trs = [], [], []
    avg_gain = avg_loss = avg_tr = None
    segment = -1
    for i, b in enumerate(bars):
        reset = i == 0 or b["t"] != bars[i - 1]["t"] + duration
        if reset:
            gains, losses, trs = [], [], []
            avg_gain = avg_loss = avg_tr = None
            segment += 1
        segments[i] = segment
        prev = b["c"] if reset else bars[i - 1]["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        if avg_tr is None and len(trs) == 14:
            avg_tr = sum(trs) / 14
        elif avg_tr is not None:
            avg_tr = (avg_tr * 13 + tr) / 14
        atr[i] = avg_tr
        if not reset:
            delta = b["c"] - prev
            gains.append(max(delta, 0)); losses.append(max(-delta, 0))
            if avg_gain is None and len(gains) == 14:
                avg_gain, avg_loss = sum(gains) / 14, sum(losses) / 14
            elif avg_gain is not None:
                avg_gain = (avg_gain * 13 + gains[-1]) / 14
                avg_loss = (avg_loss * 13 + losses[-1]) / 14
            if avg_gain is not None:
                rsi[i] = 50.0 if avg_gain == avg_loss == 0 else (100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return rsi, atr, segments


def rsi_series(bars, duration=BASE):
    return indicators(bars, duration)[0]


def atr_series(bars, duration=BASE):
    return indicators(bars, duration)[1]


def pivots(bars, duration, rsi=None, segments=None, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    """Swings on CLOSING prices: a swing low is a close below every close of the `left` bars before it and the `right`
    bars after it (highs mirrored); equal closes are not swings. `price` is that close; `wick` is the candle's low (high)
    and is used only for the stop. A swing is known only at the close of its last right-side bar. Its RSI is the RSI of
    the same candle (RSI is computed on closes too)."""
    if segments is None:
        _, _, segments = indicators(bars, duration)
    out = []
    for i in range(left, len(bars) - right):
        if segments[i - left] != segments[i + right]:
            continue
        c = bars[i]["c"]
        before = [bars[j]["c"] for j in range(i - left, i)]
        after = [bars[j]["c"] for j in range(i + 1, i + right + 1)]
        for kind, ok, wick in (("low", c < min(before) and c < min(after), bars[i]["l"]),
                               ("high", c > max(before) and c > max(after), bars[i]["h"])):
            if ok:
                r = rsi[i] if rsi else None
                out.append(dict(kind=kind, index=i, t=bars[i]["t"], price=c, wick=wick,
                                known_at=bars[i + right]["t"] + duration, segment=segments[i],
                                rsi=r, rsi_index=i if r is not None else None, rsi_t=bars[i]["t"] if r is not None else None))
    return sorted(out, key=lambda p: (p["known_at"], p["index"], p["kind"]))


def _clean(values, i1, v1, i2, v2, above):
    """No value strictly between i1 and i2 crosses the straight line from (i1, v1) to (i2, v2)."""
    for j in range(i1 + 1, i2):
        x = values[j]
        if x is None:
            return False
        line = v1 + (v2 - v1) * (j - i1) / (i2 - i1)
        if (x > line) if above else (x < line):
            return False
    return True


def pair_reason(a, b, bars, rsi, atr2=None):
    """(divergence type, None) for the swing pair a -> b, or (None, why not). Closes are compared, RSI at the swings."""
    if a is None or a["rsi"] is None or b["rsi"] is None or a["segment"] != b["segment"]:
        return None, "no RSI / data gap"
    if not SEPARATION[0] <= b["index"] - a["index"] <= SEPARATION[1]:
        return None, "swings too close or too far apart"
    dp, dr = b["price"] - a["price"], b["rsi"] - a["rsi"]
    high = b["kind"] == "high"
    if high:
        kind = "regular_bearish" if dp > 0 and dr < 0 else "hidden_bearish" if dp < 0 and dr > 0 else None
    else:
        kind = "regular_bullish" if dp < 0 and dr > 0 else "hidden_bullish" if dp > 0 and dr < 0 else None
    if kind is None:
        return None, "price and RSI agree"
    if abs(dr) < MIN_RSI_GAP:
        return None, f"RSI differs by only {abs(dr):.1f} points"
    if atr2 and abs(dp) < SWING_GAP_ATR * atr2:
        return None, f"Equal {'high' if high else 'low'}: swings {abs(dp) / atr2:.2f} ATR apart (minimum {SWING_GAP_ATR})"
    if kind == "regular_bearish" and max(a["rsi"], b["rsi"]) < 60:
        return None, "regular bearish needs a swing RSI >= 60"
    if kind == "regular_bullish" and min(a["rsi"], b["rsi"]) > 40:
        return None, "regular bullish needs a swing RSI <= 40"
    closes = [x["c"] for x in bars]
    if not _clean(closes, a["index"], a["price"], b["index"], b["price"], above=high):
        return None, "a close between the swings crosses the price line"
    if not _clean(rsi, a["rsi_index"], a["rsi"], b["rsi_index"], b["rsi"], above=high):
        return None, "an RSI value between the swings crosses the RSI line"
    return kind, None


def classify(a, b, bars, rsi, atr2=None):
    return pair_reason(a, b, bars, rsi, atr2)[0]


def choose_pairs(earlier, b, bars, rsi, atr2):
    """Compare swing b with every earlier same-side swing 5-60 bars back (not just the previous one). For each type
    found, keep the EARLIEST valid swing 1 (the longest clean divergence). Returns {type: swing 1}."""
    found = {}
    for a in sorted(earlier, key=lambda p: p["index"]):
        kind = classify(a, b, bars, rsi, atr2)
        if kind and kind not in found:
            found[kind] = a
    return found


def major_levels(points, tolerance, as_of=None):
    """Major support/resistance from 4h swings (highs AND lows: a broken resistance is later support): swing closes
    known by `as_of`, in price order; a swing joins the current cluster when it is within `tolerance` (0.5 x 4h ATR) of
    the previous swing and the cluster stays at most 2 x `tolerance` (one ATR) wide. A cluster with >= 2 swings is a
    level (price = mean close, band = lowest .. highest close)."""
    pts = sorted((p for p in points if as_of is None or p["known_at"] <= as_of), key=lambda p: p["price"])
    levels, cluster = [], []
    for p in pts + [None]:
        if (p is not None and cluster and p["price"] - cluster[-1]["price"] <= tolerance
                and p["price"] - cluster[0]["price"] <= 2 * tolerance):
            cluster.append(p)
            continue
        if len(cluster) >= LEVEL_MIN_TOUCHES:
            levels.append(dict(price=sum(x["price"] for x in cluster) / len(cluster),
                               low=min(x["price"] for x in cluster), high=max(x["price"] for x in cluster),
                               touches=[dict(time=x["t"], price=x["price"], kind=x["kind"], known_at=x["known_at"],
                                             timeframe=LEVEL_TF) for x in cluster]))
        cluster = [p] if p is not None else []
    return levels


def ema_series(bars, n, duration):
    out, e, k, seg_start = [None] * len(bars), None, 2 / (n + 1), 0
    for i, b in enumerate(bars):
        if i and b["t"] - bars[i - 1]["t"] != duration:
            e, seg_start = None, i                       # restart after a gap, like the other indicators
        if e is None:
            if i - seg_start + 1 == n:
                e = sum(x["c"] for x in bars[seg_start:i + 1]) / n
        else:
            e += k * (b["c"] - e)
        out[i] = e
    return out


def _check(checks, rule, passed, detail):
    checks.append(dict(rule=rule, passed=passed, detail=detail))
    return passed


def _hhmm(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M")


def prefilter_checks(kind, first, pivot, higher, ema, atr2, levels, level_tol, timeframe):
    """Filters on the divergence timeframe, all evaluated (for the label), in order. Returns (checks, reason of the first
    failure or None, zone or None). levels: the major 4h levels known before swing 1 formed; level_tol: 0.5 x the 4h
    ATR at that time."""
    checks = []
    long = kind.endswith("bullish")
    low = pivot["kind"] == "low"
    gap = abs(pivot["price"] - first["price"]) / atr2
    reason = None
    if not _check(checks, "Swing gap >= 0.25 ATR", gap >= SWING_GAP_ATR,
                  f"{abs(pivot['price'] - first['price']):.6g} = {gap:.2f} ATR (ATR {atr2:.6g} at swing 2)"):
        reason = f"Equal {'low' if low else 'high'}: swings {gap:.2f} ATR apart (minimum {SWING_GAP_ATR})"
    zone = None
    if kind.startswith("regular"):
        i1 = first["index"]
        e1 = ema[i1]
        e0 = ema[i1 - TREND_SLOPE] if i1 - TREND_SLOPE >= 0 else None
        c1 = higher[i1]["c"]
        if e1 is None or e0 is None:
            ok, detail = False, "EMA50 not warmed up at swing 1"
        else:
            ok = (c1 < e1 and e1 < e0) if long else (c1 > e1 and e1 > e0)
            detail = f"swing 1 close {c1:.6g} vs EMA50 {e1:.6g}; EMA50 {e0:.6g} -> {e1:.6g} over {TREND_SLOPE} bars"
        if not _check(checks, f"Prior {'down' if long else 'up'}trend at swing 1", ok, detail) and reason is None:
            reason = "No trend to reverse: " + detail
        # Major support/resistance from 4h swings (highs and lows) known before swing 1 formed.
        near = sorted((L for L in levels if level_tol and L["low"] - level_tol <= pivot["price"] <= L["high"] + level_tol),
                      key=lambda L: abs(L["price"] - pivot["price"]))
        if near:
            L = near[0]
            used = ", ".join(f"{t['price']:.6g} {t['kind']} @ {_hhmm(t['time'])}" for t in L["touches"])
            detail = (f"swing 2 close {pivot['price']:.6g} is {abs(pivot['price'] - L['price']):.6g} from the {LEVEL_TF} level "
                      f"{L['price']:.6g} ({len(L['touches'])} swings: {used})")
            zone = dict(kind="support" if long else "resistance", timeframe=LEVEL_TF, tolerance=level_tol, price=L["price"],
                        lower=L["low"], upper=L["high"], low=L["low"], high=L["high"], touches=L["touches"])
        else:
            detail = (f"no major {LEVEL_TF} level within {level_tol:.6g} of {pivot['price']:.6g} ({len(levels)} levels known)"
                      if level_tol else f"{LEVEL_TF} ATR not available yet")
        ok = _check(checks, f"At a major {LEVEL_TF} {'support' if long else 'resistance'} level", bool(near), detail)
        if not ok and reason is None:
            reason = f"Away from {'support' if long else 'resistance'}: " + detail
    else:
        avail = pivot["index"] + PIVOT_RIGHT                     # the bar whose close makes swing 2 known
        e_now = ema[avail] if avail < len(ema) else None
        e_old = ema[avail - TREND_SLOPE] if avail - TREND_SLOPE >= 0 else None
        close = higher[avail]["c"]
        if e_now is None or e_old is None:
            ok, detail = False, "EMA50 not warmed up"
        else:
            ok = (close > e_now and e_now > e_old) if long else (close < e_now and e_now < e_old)
            detail = f"close {close:.6g} vs EMA50 {e_now:.6g}; EMA50 {e_old:.6g} -> {e_now:.6g} over {TREND_SLOPE} bars"
        if not _check(checks, f"With the EMA50 {'up' if long else 'down'}trend", ok, detail) and reason is None:
            reason = "Against the EMA50 trend: " + detail
    return checks, reason, zone


def chase_checks(long, entry, target, swing2, atr2, opposite):
    """No chasing at entry: within 1.5 ATR of swing 2, and no opposite swing (for a long, a confirmed swing high)
    strictly between the entry and the 2R target. Returns (checks, reason or None)."""
    checks = []
    reason = None
    dist = abs(entry - swing2) / atr2
    if not _check(checks, "Entry within 1.5 ATR of swing 2", dist <= CHASE_MAX_ATR,
                  f"entry {entry:.6g} is {dist:.2f} ATR from swing 2 {swing2:.6g}"):
        reason = f"Chasing: entry {dist:.2f} ATR from swing 2 (maximum {CHASE_MAX_ATR})"
    lo, hi = (entry, target) if long else (target, entry)
    blocking = [p for p in opposite if lo < p["price"] < hi]
    nearest = min(blocking, key=lambda p: abs(p["price"] - entry)) if blocking else None
    detail = (f"swing {'high' if long else 'low'} {nearest['price']:.6g} @ {_hhmm(nearest['t'])} sits between entry "
              f"{entry:.6g} and target {target:.6g}") if nearest else f"clear between {entry:.6g} and {target:.6g}"
    if not _check(checks, "No opposite swing before the 2R target", nearest is None, detail) and reason is None:
        reason = "Chasing: " + detail
    return checks, reason


def _study(bars, symbol):
    setups, signals, views = [], [], {}
    # Major support/resistance comes from the 4h swings of the whole history, for both divergence timeframes.
    h4 = aggregate(bars, 4 * HOUR)
    r4, a4, s4 = indicators(h4, 4 * HOUR)
    level_points = pivots(h4, 4 * HOUR, r4, s4)
    h4_close = [b["t"] + 4 * HOUR for b in h4]

    def level_tolerance(ms):
        k = bisect_right(h4_close, ms) - 1                   # the last 4h candle closed by 
        return LEVEL_TOL_ATR * a4[k] if k >= 0 and a4[k] else None

    for div_tf, trigger_tf, duration, lower_duration in (("4h", "1h", 4 * HOUR, HOUR), ("1h", "15m", HOUR, BASE)):
        higher, lower = aggregate(bars, duration), aggregate(bars, lower_duration)
        rsi, atr, segments = indicators(higher, duration)
        ema = ema_series(higher, TREND_EMA, duration)
        hp, lp = pivots(higher, duration, rsi, segments), pivots(lower, lower_duration, left=2, right=2)
        lower_closes = [b["t"] + lower_duration for b in lower]
        lower_pivot_known = [p["known_at"] for p in lp]
        hp_known = [p["known_at"] for p in hp]
        last_close = bars[-1]["t"] + BASE if bars else 0
        views[div_tf] = [dict(b, rsi=rsi[i], atr=atr[i], ema50=ema[i])
                         for i, b in enumerate(higher[-240:], start=max(0, len(higher) - 240))]
        earlier = {"low": [], "high": []}
        for pivot in hp:
            same = earlier[pivot["kind"]]
            candidates = [q for q in same if q["segment"] == pivot["segment"]
                          and SEPARATION[0] <= pivot["index"] - q["index"] <= SEPARATION[1]]
            same.append(pivot)
            atr2 = atr[pivot["index"]]
            if not atr2 or atr2 <= 0:
                continue
            for kind, first in choose_pairs(candidates, pivot, higher, rsi, atr2).items():
                _setup(setups, signals, symbol, kind, first, pivot, higher, rsi, atr2, ema, div_tf, trigger_tf, duration,
                       lower, lower_closes, lp, lower_pivot_known, lower_duration, hp, hp_known, last_close,
                       level_points, level_tolerance)
    return sorted(setups, key=lambda s: s["divergence_at"]), sorted(signals, key=lambda s: (s["confirmed_at"], s["id"])), views


def _setup(setups, signals, symbol, kind, first, pivot, higher, rsi, atr2, ema, div_tf, trigger_tf, duration, lower,
           lower_closes, lp, lower_pivot_known, lower_duration, hp, hp_known, last_close, level_points, level_tolerance):
    long = kind.endswith("bullish")
    side = "long" if long else "short"
    known = pivot["known_at"]
    # Swings are closes; the stop sits beyond the swing candle's wick so ordinary wicks do not stop the trade.
    stop = pivot["wick"] + (-STOP_BUFFER_ATR if long else STOP_BUFFER_ATR) * atr2
    price_rel, rsi_rel = LABELS[kind]
    tol = level_tolerance(first["t"])
    levels = major_levels(level_points, tol, as_of=first["t"]) if tol else []
    checks, filtered, zone = prefilter_checks(kind, first, pivot, higher, ema, atr2, levels, tol, div_tf)
    setup = dict(id=f"{RULE_VERSION}:{symbol}:{div_tf}:{kind}:{pivot['t']}",
                 symbol=symbol, side=side, divergence_type=kind, regular=kind.startswith("regular"),
                 price_relation=price_rel, rsi_relation=rsi_rel,
                 divergence_tf=div_tf, trigger_tf=trigger_tf,
                 divergence_at=known, pivot_at=pivot["t"], first_pivot=first, second_pivot=pivot,
                 zone=zone, stop=stop, atr=atr2, swing_gap_atr=abs(pivot["price"] - first["price"]) / atr2,
                 expires_at=known + EXPIRY_BARS * duration, checks=checks,
                 status="WAITING", reason="Waiting for a closed lower-timeframe structure break")
    setup.update(pivot1=dict(time=first["t"], price=first["price"], wick=first["wick"], rsi=first["rsi"],
                             rsi_time=first["rsi_t"], known_at=first["known_at"]),
                 pivot2=dict(time=pivot["t"], price=pivot["price"], wick=pivot["wick"], rsi=pivot["rsi"],
                             rsi_time=pivot["rsi_t"], known_at=known))
    setups.append(setup)
    # The lower-timeframe stage is evaluated even for FILTERED setups so the label shows every rule; only an
    # unfiltered setup can become a signal.
    status, reason = _confirm(setup, long, stop, known, lower, lower_closes, lp, lower_pivot_known,
                              lower_duration, checks, duration)
    if status == "CONFIRMED":
        cut = bisect_right(hp_known, setup["confirmed_at"])
        opposite = [p for p in hp[:cut] if p["kind"] == ("high" if long else "low")
                    and p["t"] >= setup["confirmed_at"] - ROOM_LOOKBACK * duration]
        more, chase = chase_checks(long, setup["entry_reference"], setup["target"], pivot["price"], atr2, opposite)
        checks.extend(more)
        if chase:
            status, reason = "SKIPPED", chase
    if filtered:
        status, reason = "FILTERED", filtered
    elif status == "WAITING" and last_close >= setup["expires_at"]:
        status, reason = "EXPIRED", f"No structure break within {EXPIRY_BARS} divergence-timeframe bars"
    if status != "CONFIRMED" and "confirmed_at" in setup:
        # What the lower timeframe would have done; never a signal, so no confirmed_at / entry on the setup.
        setup["hypothetical"] = {k: setup.pop(k) for k in ("confirmed_at", "entry_reference", "target")}
    setup.update(status=status, reason=reason)
    if status == "CONFIRMED":
        signals.append(dict(setup))


def _confirm(setup, long, stop, known, lower, lower_closes, lp, lower_pivot_known, lower_duration, checks,
             divergence_duration):
    """The lower-timeframe structure break: the latest opposing lower-timeframe swing frozen when the divergence becomes
    known; the FIRST later close beyond it must carry >= the 20-candle mean volume. Returns (status, reason) and records
    trigger_level / trigger_candle / confirmed_at / entry_reference / target on the setup."""
    cutoff = bisect_right(lower_pivot_known, known)
    opposing = next((p for p in reversed(lp[:cutoff]) if p["kind"] == ("high" if long else "low")), None)
    at_known = bisect_right(lower_closes, known) - 1
    if opposing is None or at_known < 0 or known - opposing["t"] > TRIGGER_MAX_AGE * lower_duration:
        _check(checks, "Lower-timeframe trigger swing", False, "no confirmed opposing swing in the last 20 candles")
        return "INVALID", "No recent confirmed opposing lower-timeframe swing"
    if lower_closes[at_known] != known:
        _check(checks, "Lower-timeframe trigger swing", False, "missing lower-timeframe candle at availability")
        return "INVALID", "Missing lower-timeframe candle at divergence availability"
    if any(lower[j]["t"] != lower[j - 1]["t"] + lower_duration for j in range(opposing["index"] + 1, at_known + 1)):
        _check(checks, "Lower-timeframe trigger swing", False, "data gap in the lower-timeframe structure")
        return "INVALID", "Data gap in lower-timeframe structure"
    level = opposing["price"]
    setup.update(trigger_level=level, trigger_pivot=opposing)
    close = lower[at_known]["c"]
    if (long and close > level) or (not long and close < level):
        _check(checks, "Trigger not already broken", False, f"close {close:.6g} already beyond {level:.6g}")
        return "INVALID", "Structure already broke before divergence was available"
    _check(checks, "Lower-timeframe trigger swing", True, f"{'high' if long else 'low'} {level:.6g} @ {_hhmm(opposing['t'])}")
    prev_t = lower[at_known]["t"]
    for j in range(at_known + 1, len(lower)):
        bar, closed_at = lower[j], lower_closes[j]
        if closed_at > setup["expires_at"]:
            break
        if bar["t"] != prev_t + lower_duration:
            _check(checks, "Structure break", False, "data gap before confirmation")
            return "INVALID", "Data gap before confirmation"
        prev_t = bar["t"]
        if (long and bar["l"] <= stop) or (not long and bar["h"] >= stop):
            setup["invalidated_at"] = closed_at
            _check(checks, "Structure break", False, f"stop {stop:.6g} hit first @ {_hhmm(bar['t'])}")
            return "INVALID", "Divergence swing stop breached before confirmation"
        if (long and bar["c"] > level) or (not long and bar["c"] < level):
            # Volume confirms the reversal when, on the lower timeframe, the candles closing in the trade's direction
            # carried at least as much volume as the opposite ones -- from the close of the swing-2 candle up to and
            # including the break. (DIV-SR-4 compared the break candle with the 20-candle mean, which still held the
            # climax candles of the swing itself, so a normal candle after a capitulation could never pass.)
            start = setup["pivot2"]["time"] + divergence_duration
            window = [w for w in lower[:j + 1] if w["t"] >= start]
            contiguous = bool(window) and window[0]["t"] == start and all(
                window[k]["t"] == window[0]["t"] + k * lower_duration for k in range(len(window)))
            with_v = sum(w["v"] for w in window if (w["c"] > w["o"]) == long and w["c"] != w["o"])
            against_v = sum(w["v"] for w in window if (w["c"] < w["o"]) == long and w["c"] != w["o"])
            ratio = (with_v / against_v if against_v > 0 else (float("inf") if with_v > 0 else None)) if contiguous else None
            setup["volume_ratio"] = None if ratio == float("inf") else ratio
            setup["trigger_candle"] = dict(time=bar["t"], close=bar["c"], volume=bar["v"], timeframe=setup["trigger_tf"],
                                           window_start=start, volume_with=with_v, volume_against=against_v,
                                           ratio=setup["volume_ratio"])
            _check(checks, "Structure break", True, f"close {bar['c']:.6g} beyond {level:.6g} @ {_hhmm(bar['t'])}")
            side_word = ("buying", "selling") if long else ("selling", "buying")
            detail = (f"{side_word[0]} {with_v:,.0f} vs {side_word[1]} {against_v:,.0f} on {setup['trigger_tf']} since swing 2 "
                      f"({'all' if ratio == float('inf') else f'{ratio:.2f}x'})") if ratio is not None else \
                "lower-timeframe candles since swing 2 are missing"
            if not _check(checks, f"{side_word[0].capitalize()} volume >= {side_word[1]} volume since swing 2",
                          ratio is not None and ratio >= VOLUME_MIN, detail):
                setup["invalidated_at"] = closed_at
                return "INVALID", "Structure break without volume confirmation: " + detail
            entry = bar["c"]
            distance = abs(entry - stop)
            if distance <= 0 or (long and stop >= entry) or (not long and stop <= entry):
                return "INVALID", "Invalid stop distance"
            setup.update(confirmed_at=closed_at, entry_reference=entry, target=entry + (2 if long else -2) * distance)
            return "CONFIRMED", "Closed, volume-confirmed structure break after confirmed divergence"
    _check(checks, "Structure break", None, "not yet / not within the expiry window")
    return "WAITING", "Waiting for a closed lower-timeframe structure break"


def generate_signals(bars, symbol):
    """Return all historical causal confirmations; caller supplies completed bars."""
    return _study(clean_bars(bars), symbol)[1]


def analyze(bars, symbol, now_ms):
    rows = clean_bars(bars, now_ms)
    setups, signals, views = _study(rows, symbol)
    return dict(version=RULE_VERSION, rules=RULES, symbol=symbol, as_of=now_ms,
                last_bar=rows[-1]["t"] if rows else None, bars=len(rows),
                setups=setups[-100:], signals=signals[-100:], indicators=views,
                data_gaps=sum(b["t"] != rows[i-1]["t"] + BASE for i, b in enumerate(rows) if i))


def current_levels(bars):
    """The major 4h support/resistance levels known at the last completed candle (for the chart)."""
    rows = clean_bars(bars)
    h4 = aggregate(rows, 4 * HOUR)
    if not h4:
        return dict(timeframe=LEVEL_TF, tolerance=None, levels=[])
    r4, a4, s4 = indicators(h4, 4 * HOUR)
    tol = LEVEL_TOL_ATR * a4[-1] if a4[-1] else None
    return dict(timeframe=LEVEL_TF, tolerance=tol, levels=major_levels(pivots(h4, 4 * HOUR, r4, s4), tol) if tol else [])
