"""Jev for the V11 scanners' +JEV twins (JEV_PROMPT_V11_SCAN / JEV_STATE_V11), operator's request 2026-09-30: "use Jev
to decide TP1, TP2, TP3 based on its confidence".

Every scanner has a +JEV twin on the same candidates, data, capital, execution and risk. For each candidate the twin asks
Jev whether conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT the trade, and uses the answer twice:

    CONTRADICT (or no answer in time)   the twin skips the trade
    otherwise                           it takes it at the SAME size, with the take-profit ladder stretched by Jev's
                                        confidence c = P(SUPPORT) + P(STRONGLY_SUPPORT):

        TP spacing = the scanner's spacing x tp_scale(c),   tp_scale(c) = 0.5 + c, clamped to [0.75, 1.5]

    so a barely-supported trade (c = 50%) keeps the scanner's own TPs, c = 80% reaches 1.3x further, c = 100% 1.5x.
    The ladder stays equally spaced (TP1, 2 x TP1, 3 x TP1) and closes the scanner's shares of the position
    (app/strategies/v11/ladder.SHARES, 25% / 50% / 25%).

The state holds only candles closed at the decision instant (5m / 15m / 1h of the candidate's coin), the scanner's
setup and its ladder, the costs and the bot's health. No future return, no claimed edge.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.ai.jev.models import JevDecision, fingerprint
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import _context
from app.ai.jev.v4 import parse_decision_v4
from app.ai.jev.v6 import _dir_vs

PROMPT_VERSION_V11 = "JEV_PROMPT_V11_SCAN"
STATE_VERSION_V11 = "JEV_STATE_V11"
TP_SCALE_MIN, TP_SCALE_MAX = 0.75, 1.5

QUESTIONS_V11: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "A crypto futures SCANNER bot watches 30 Bybit USDT perpetuals and proposes the trade in `setup` on the "
            "coin where it found its best setup. It exits in three steps -- 25% of the position at TP1, 50% at TP2, "
            "the last 25% at TP3, with the stop moved to entry after TP1 and to TP1 after TP2 -- or at its structural "
            "stop, or at its time stop. Round-trip taker fees and spread are a real cost. This is a LIVE FORWARD paper "
            "test: no historical edge is claimed. Your confidence decides how far the take-profits reach: the more "
            "sure you are that the move runs, the further the ladder is stretched. Given the coin's 5m / 15m / 1h "
            "trend ladder, its recent price action, relative volume, volatility, costs and the bot's health, do "
            "current conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT this trade?"),
        "criteria": {
            "CONTRADICT": ("The move is unlikely or already exhausted: the higher-timeframe trend is against it, the "
                           "entry chases an extended bar, volume does not confirm, or costs eat most of the first "
                           "target. Skip it."),
            "SUPPORT": "A valid trade with nothing materially against it: take it; the take-profits stay near normal.",
            "STRONGLY_SUPPORT": ("Trend ladder aligned, momentum and volume confirming and enough volatility for the "
                                 "move to run: take it and let the take-profits reach further."),
        },
    },
}
QUESTIONS_V11_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V11, "questions": QUESTIONS_V11})


def parse_decision_v11(body: Mapping[str, Any]) -> JevDecision:
    return parse_decision_v4(body, QUESTIONS_V11)


def tp_scale(confidence: float | None) -> float:
    """How far Jev stretches the take-profit ladder: 0.5 + confidence, clamped to [0.75, 1.5]."""
    c = float(confidence or 0.0)
    return round(min(TP_SCALE_MAX, max(TP_SCALE_MIN, 0.5 + c)), 4)


class JevStateBuilderV11:
    """JEV_STATE_V11: the scanner, its candidate's coin (trend ladder 5m / 15m / 1h, recent price action, volume,
    volatility), the setup and its take-profit ladder, economics, sizing and bot health -- all closed at the instant."""

    def build(self, *, t: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], sizing: Mapping[str, Any], health: Mapping[str, Any],
              position: Mapping[str, Any]) -> DecisionSnapshot:
        for tf, cs in series.items():
            if any(int(c.close_time) >= t for c in cs):
                raise LookAheadError(f"{tf} candle closes at or after the decision instant")
        meta = getattr(sig, "meta", None) or {}
        side = sig.side
        ladder, agree, against = {}, 0, 0
        for tf in ("5m", "15m", "1h"):
            cs = list(series.get(tf) or [])[-120:]
            if len(cs) < 20:
                continue
            block = _context(cs)
            block["vs_trade"] = _dir_vs(block.get("direction"), side)
            agree += block["vs_trade"] == "WITH"
            against += block["vs_trade"] == "AGAINST"
            ladder[tf] = block
        five = list(series.get("5m") or [])
        recent = [{"o": _r(c.open, 8), "h": _r(c.high, 8), "l": _r(c.low, 8), "c": _r(c.close, 8),
                   "v": _r(c.volume, 3)} for c in five[-12:]]
        chg = lambda n: (five[-1].close / five[-1 - n].close - 1.0) if len(five) > n and five[-1 - n].close else None  # noqa: E731
        prev_vol = [float(c.volume) for c in five[-21:-1]]
        rel_vol = (float(five[-1].volume) / (sum(prev_vol) / len(prev_vol))) if (five and len(prev_vol) >= 10 and sum(prev_vol) > 0) else None
        taker = float(execution.get("taker_fee") or 0.0)
        half = float(execution.get("half_spread_bps") or 0.0)
        rt_bps = 2.0 * taker * 1e4 + 2.0 * half
        stop = float(meta.get("stop_pct") or 0.0)
        risk = abs(float(sig.entry_price) - float(sig.stop)) if sig.stop else 0.0
        tps_r = [_r(abs(tp.price - sig.entry_price) / risk, 3) for tp in (sig.take_profits or [])] if risk else []
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "family": meta.get("family"), "symbol": sig.symbol,
                    "scans": "30 coins, best setups first", "signal_timeframe": meta.get("signal_tf"),
                    "utc_hour": (t // 3_600_000) % 24},
            "setup": {"side": side, "setup": meta.get("setup"), "thesis": bot.get("thesis"),
                      "fails_when": bot.get("fails_when"),
                      "max_hold_minutes": int((getattr(sig, "max_hold_s", 0) or 0) / 60) or None,
                      "stop_distance_pct": _r(stop * 100, 3) if stop else None,
                      "take_profit_ladder_r": tps_r, "take_profit_close_fractions": [tp.fraction for tp in (sig.take_profits or [])],
                      "signal_quality": meta.get("signal_quality"), "quality_factors": meta.get("quality_factors"),
                      "scanner_score": meta.get("score")},
            "trend": {"ladder": ladder, "aligned_with_trade": agree, "against_trade": against},
            "price_action": {"last_hour_5m": recent, "chg_15m": _r(chg(3), 5), "chg_1h": _r(chg(12), 5),
                             "chg_4h": _r(chg(48), 5), "relative_volume_5m": _r(rel_vol, 3)},
            "volatility": {"atr_pct_5m": (ladder.get("5m") or {}).get("atr_pct"),
                           "atr_pct_1h": (ladder.get("1h") or {}).get("atr_pct")},
            "economics": {"historical_edge": "none claimed: V11 is evaluated on live forward data only",
                          "round_trip_cost_bps": _r(rt_bps, 3),
                          "round_trip_cost_r": _r(rt_bps / 1e4 / stop, 4) if stop else None},
            "sizing": {"tier": sizing.get("tier"), "proposed_risk_pct": position.get("proposed_risk_pct")},
            "execution": {"taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                          "spread_source": execution.get("spread_source"), "fill": "market order ~60 s after the candle close",
                          "proposed_leverage": position.get("proposed_leverage")},
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V11,
        }
        state = json.loads(json.dumps(state, default=str))
        return DecisionSnapshot(ts=int(t), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V11,
                                meta={"ladder": list(ladder)})


class ClientDeciderV11:
    """Asks the V11 scanner question through the shared client and parses strictly; failures are coded outcomes."""

    def __init__(self, client: Any):
        self.client = client

    def __call__(self, state: Mapping[str, Any]) -> Any:
        from app.ai.jev.models import JevOutcome, SchemaError
        out = self.client.decide(dict(state), QUESTIONS_V11, parse=False)
        if not out.ok:
            return out
        try:
            dec = parse_decision_v11(out.extra.get("raw") or {})
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts, http_status=out.http_status)


def v11_jev_fingerprints() -> dict[str, str]:
    import hashlib
    import inspect
    src = (inspect.getsource(JevStateBuilderV11) + inspect.getsource(tp_scale)).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V11_FINGERPRINT, "prompt_version": PROMPT_VERSION_V11, "state_version": STATE_VERSION_V11,
            "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12],
            "tp_scale": f"0.5 + confidence, clamped to [{TP_SCALE_MIN}, {TP_SCALE_MAX}]"}
