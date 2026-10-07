"""The decision snapshot Jev sees: compact, deterministic, and closed at decision time.

What Jev may know is exactly what existed when the strategy's signal bar CLOSED:

* bars whose close_time <= the decision timestamp (anything later is refused, not trimmed quietly),
* funding RATES that have already settled (the next settlement's time is public schedule; its rate
  is not known yet and is never included),
* the bot's OWN closed trades and equity so far -- never the matched control's results, the
  leaderboard, a qualification, or anything about how this trade ends.

Two further rules, because the model is a trained system and not a blank slate:

* **No calendar dates and no absolute price levels.** Jev was trained on data that may include the
  very months being replayed. A date plus a price is enough to recall what happened next. The state
  therefore carries returns, ratios and distances only, plus the UTC hour.
* **Small.** A dozen features per block, rounded, so a request is a few hundred tokens and the
  fingerprint is stable across platforms.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.ai.jev.models import canonical, fingerprint

STATE_VERSION = "JEV_STATE_V1"
FUNDING_PERIOD_MS = 8 * 3600 * 1000


class LookAheadError(ValueError):
    """A snapshot was asked to include something that did not exist at decision time."""


def _r(x: float | None, nd: int = 5) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    if not math.isfinite(float(x)):
        return None
    return round(float(x), nd)


def _ema(xs: Sequence[float], n: int) -> list[float]:
    if not xs:
        return []
    k = 2.0 / (n + 1)
    out = [xs[0]]
    for v in xs[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(len(closes) - n, len(closes))]
    return sum(trs) / n


def _rsi(closes: Sequence[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def market_features(candles: Sequence[Any]) -> dict[str, Any]:
    """Relative features of the signal-timeframe series. Nothing absolute, nothing dated."""
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    vols = [float(c.volume) for c in candles]
    n = len(closes)
    last = closes[-1] if closes else None
    if not last:
        return {"bars": n}

    def ret(k: int) -> float | None:
        return (last / closes[-1 - k] - 1.0) if n > k and closes[-1 - k] else None

    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(max(1, n - 20), n) if closes[i - 1]]
    atr = _atr(highs, lows, closes, 14)
    e20, e50 = _ema(closes, 20), _ema(closes, 50)
    prev_vol = vols[-21:-1]
    hi50, lo50 = max(highs[-50:]), min(lows[-50:])
    atr_hist = []
    for j in range(max(15, n - 100), n + 1):
        a = _atr(highs[:j], lows[:j], closes[:j], 14)
        if a is not None and closes[j - 1]:
            atr_hist.append(a / closes[j - 1])
    atr_pct = (atr / last) if atr else None
    rank = (sum(1 for v in atr_hist if v <= atr_pct) / len(atr_hist)) if (atr_pct and atr_hist) else None
    return {
        "bars": n,
        "ret_1": _r(ret(1)), "ret_5": _r(ret(5)), "ret_20": _r(ret(20)),
        "realized_vol_20": _r(statistics.pstdev(rets)) if len(rets) >= 5 else None,
        "atr_pct": _r(atr_pct), "atr_pct_rank_100": _r(rank, 3),
        "rsi_14": _r(_rsi(closes, 14), 2),
        "ema20_vs_ema50": _r(e20[-1] / e50[-1] - 1.0) if n >= 50 and e50[-1] else None,
        "ema20_slope_5": _r(e20[-1] / e20[-6] - 1.0) if n >= 26 and e20[-6] else None,
        "volume_ratio_20": _r(vols[-1] / (sum(prev_vol) / len(prev_vol)), 3)
        if len(prev_vol) >= 5 and sum(prev_vol) > 0 else None,
        "last_range_vs_atr": _r((highs[-1] - lows[-1]) / atr, 3) if atr else None,
        "dist_from_high_50": _r(last / hi50 - 1.0) if hi50 else None,
        "dist_from_low_50": _r(last / lo50 - 1.0) if lo50 else None,
    }


def health_features(equity: float, peak: float, start: float, rs: Sequence[float],
                    window: int = 20) -> dict[str, Any]:
    recent = list(rs)[-window:]
    wins = [r for r in recent if r > 0]
    losses = [r for r in recent if r <= 0]
    streak = 0
    for r in reversed(list(rs)):
        if r > 0:
            break
        streak += 1
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    return {
        "equity_vs_start": _r(equity / start - 1.0 if start else None, 4),
        "drawdown": _r(1.0 - equity / peak if peak > 0 else 0.0, 4),
        "closed_trades": len(rs), "recent_trades": len(recent),
        "recent_expectancy_r": _r(sum(recent) / len(recent), 3) if recent else None,
        "recent_profit_factor": _r(pf, 3) if pf is not None else None,
        "recent_win_rate": _r(len(wins) / len(recent), 3) if recent else None,
        "losing_streak": streak,
        "recent_r_min": _r(min(recent), 3) if recent else None,
        "recent_r_median": _r(statistics.median(recent), 3) if recent else None,
        "recent_r_max": _r(max(recent), 3) if recent else None,
    }


@dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable. `state` is exactly what is sent; `ts` is never part of it."""
    ts: int
    state: Mapping[str, Any]
    fingerprint: str
    version: str = STATE_VERSION
    meta: Mapping[str, Any] = field(default_factory=dict)   # kept locally, never sent

    def size_bytes(self) -> int:
        return len(canonical(self.state))


class JevStateBuilder:
    def __init__(self, bars: int = 120):
        self.bars = bars

    def build(self, *, ts: int, bot: Mapping[str, Any], sig: Any, candles: Sequence[Any],
              execution: Mapping[str, Any], health: Mapping[str, Any],
              position: Mapping[str, Any], funding: Sequence[tuple[int, float]] = ()) -> DecisionSnapshot:
        """Assemble the snapshot for a signal decided at `ts` (its bar's close time)."""
        future = [c for c in candles if int(c.close_time) > ts]
        if future:
            raise LookAheadError(f"{len(future)} candle(s) close after the decision time")
        late = [f for f in funding if int(f[0]) > ts]
        if late:
            raise LookAheadError(f"{len(late)} funding event(s) settle after the decision time")
        window = list(candles)[-self.bars:]
        entry = float(sig.entry_price)
        stop_pct = abs(entry - float(sig.stop)) / entry if entry else None
        tps = list(getattr(sig, "take_profits", None) or [])
        target_pct = abs(float(tps[-1].price) - entry) / entry if (tps and entry) else None
        trail = getattr(sig, "trail", None)
        taker = float(execution.get("taker_fee", 0.0005))
        round_trip = 2.0 * taker
        last_funding = funding[-1][1] if funding else None
        next_funding_ms = FUNDING_PERIOD_MS - (ts % FUNDING_PERIOD_MS)
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "strategy_name": bot.get("strategy_name"),
                    "params_version": bot.get("params_version"), "symbol": bot.get("symbol"),
                    "timeframe": bot.get("timeframe"), "utc_hour": (ts // 3_600_000) % 24},
            "signal": {
                "side": sig.side, "stop_distance_pct": _r(stop_pct), "target_distance_pct": _r(target_pct),
                "reward_risk": _r(target_pct / stop_pct, 3) if (stop_pct and target_pct) else None,
                "targets": len(tps), "trailing_stop": getattr(trail, "kind", None) if trail else None,
                "max_hold_minutes": int(sig.max_hold_s // 60) if getattr(sig, "max_hold_s", None) else None,
                "strategy_confidence": (getattr(sig, "meta", None) or {}).get("quality") or "not provided",
                "setup": str(getattr(sig, "reason", "") or "")[:60],
            },
            "market": market_features(window),
            "execution": {
                "round_trip_fee_pct": _r(round_trip, 6),
                "fees_vs_target": _r(round_trip / target_pct, 3) if target_pct else None,
                "fees_vs_stop": _r(round_trip / stop_pct, 3) if stop_pct else None,
                "half_spread_bps": _r(execution.get("half_spread_bps"), 3),
                "expected_slippage_bps": _r(execution.get("expected_slippage_bps"), 3),
                "last_funding_rate": _r(last_funding, 6),
                "minutes_to_next_funding": int(next_funding_ms // 60000),
            },
            "bot_health": dict(health),
            "position": {k: position.get(k) for k in ("open_positions", "available_margin_pct",
                                                       "leverage_ceiling", "proposed_risk_pct",
                                                       "proposed_notional_vs_equity",
                                                       "proposed_leverage")},
            "state_version": STATE_VERSION,
        }
        return DecisionSnapshot(ts=int(ts), state=state, fingerprint=fingerprint(state),
                                meta={"bars_used": len(window)})
