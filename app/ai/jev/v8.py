"""Jev for V8 SCALP (JEV_PROMPT_V8_SCALP / JEV_STATE_V8). A gate on a V8 scalper's candidate, never a trade source.

Same actions and policy as Jev V6 (imported, unchanged): CONTRADICT -> SKIP, SUPPORT -> TAKE, STRONGLY SUPPORT ->
ATTACK (2% risk on a healthy bot, if legal), a failed or late answer -> SKIP. The question and the state speak about
a 5-minute scalp: 45-minute maximum hold, 1.5R target, a stop of 0.45-1.2%, and a round-trip cost of roughly a third
of R -- so what matters is whether the move is likely to continue immediately and cleanly.

The state holds only candles closed at the decision instant (5m / 15m / 1h of the bot's own coin), the setup, the
costs, the sizing tier and the bot's health. No future return, no rank, no claimed edge.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.ai.jev.models import JevDecision, fingerprint
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import _context
from app.ai.jev.v4 import parse_decision_v4
from app.ai.jev.v6 import POLICY_V6, _dir_vs

PROMPT_VERSION_V8 = "JEV_PROMPT_V8_SCALP"
STATE_VERSION_V8 = "JEV_STATE_V8"

QUESTIONS_V8: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "A crypto futures SCALPING bot proposes the trade in `setup` on one Bybit USDT perpetual. It decides on "
            "closed 5-minute candles, exits at a 1.5R target, at its structural stop (0.45-1.2% away) or after at "
            "most 45 minutes. Round-trip taker fees and spread cost about a third of R, so a scalp only pays when "
            "price moves in its favour soon and cleanly. This is a LIVE FORWARD paper test: no historical edge is "
            "claimed. Given the bot's own 5m / 15m / 1h trend ladder, the last hour of price action, relative "
            "volume, volatility, costs and the bot's health, do current conditions CONTRADICT, SUPPORT or STRONGLY "
            "SUPPORT this scalp?"),
        "criteria": {
            "CONTRADICT": ("The immediate move is unlikely or already exhausted: the 15m / 1h trend is against it, "
                           "the entry chases an extended bar, volume does not confirm, volatility is too low to reach "
                           "the target in 45 minutes, or costs eat most of the target. Skip it."),
            "SUPPORT": "A valid scalp with nothing materially against it. Take it at the normal size.",
            "STRONGLY_SUPPORT": ("Trend ladder aligned, momentum and volume confirming, enough volatility to reach "
                                 "1.5R quickly and a healthy bot. Exceptional: trade it above normal size."),
        },
    },
}
QUESTIONS_V8_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V8, "questions": QUESTIONS_V8})


def parse_decision_v8(body: Mapping[str, Any]) -> JevDecision:
    return parse_decision_v4(body, QUESTIONS_V8)


class JevStateBuilderV8:
    """JEV_STATE_V8: bot, setup, trend ladder (5m / 15m / 1h), the last hour of price action, volatility, economics,
    sizing, execution and bot health -- all closed at the decision instant."""

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
        last_hour = [{"o": _r(c.open, 8), "h": _r(c.high, 8), "l": _r(c.low, 8), "c": _r(c.close, 8),
                      "v": _r(c.volume, 3)} for c in five[-12:]]
        chg = lambda n: (five[-1].close / five[-1 - n].close - 1.0) if len(five) > n and five[-1 - n].close else None  # noqa: E731
        prev_vol = [float(c.volume) for c in five[-21:-1]]
        rel_vol = (float(five[-1].volume) / (sum(prev_vol) / len(prev_vol))) if (five and len(prev_vol) >= 10 and sum(prev_vol) > 0) else None
        taker = float(execution.get("taker_fee") or 0.0)
        half = float(execution.get("half_spread_bps") or 0.0)
        rt_bps = 2.0 * taker * 1e4 + 2.0 * half
        stop = float(meta.get("stop_pct") or 0.0)
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "family": meta.get("family"), "symbol": bot.get("symbol"),
                    "signal_timeframe": "5m", "context_timeframes": ["15m", "1h"], "utc_hour": (t // 3_600_000) % 24},
            "setup": {"side": side, "setup": meta.get("setup"), "thesis": bot.get("thesis"),
                      "fails_when": bot.get("fails_when"), "max_hold_minutes": 45, "target_r": meta.get("target_r"),
                      "stop_distance_pct": _r(stop * 100, 3) if stop else None,
                      "signal_quality": meta.get("signal_quality"), "quality_factors": meta.get("quality_factors")},
            "trend": {"ladder": ladder, "aligned_with_trade": agree, "against_trade": against},
            "price_action": {"last_hour_5m": last_hour, "chg_15m": _r(chg(3), 5), "chg_1h": _r(chg(12), 5),
                             "chg_4h": _r(chg(48), 5), "chg_24h": _r(chg(288), 5),
                             "relative_volume_signal_bar": _r(rel_vol, 3)},
            "volatility": {"atr_pct_5m": (ladder.get("5m") or {}).get("atr_pct"), "atr_pct_1h": (ladder.get("1h") or {}).get("atr_pct")},
            "economics": {"historical_edge": "none claimed: V8 is evaluated on live forward data only",
                          "round_trip_cost_bps": _r(rt_bps, 3),
                          "round_trip_cost_r": _r(rt_bps / 1e4 / stop, 4) if stop else None},
            "sizing": {"tier": sizing.get("tier"), "legal_min_risk_pct": sizing.get("legal_min_risk_pct"),
                       "attack_only": bool(sizing.get("attack_only")), "proposed_risk_pct": position.get("proposed_risk_pct")},
            "execution": {"taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                          "spread_source": execution.get("spread_source"), "fill": "market order ~60 s after the candle close",
                          "proposed_leverage": position.get("proposed_leverage")},
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V8,
        }
        state = json.loads(json.dumps(state, default=str))
        return DecisionSnapshot(ts=int(t), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V8,
                                meta={"ladder": list(ladder)})


class ClientDeciderV8:
    """Asks the V8 scalp question through the shared client and parses strictly; failures are coded outcomes."""

    def __init__(self, client: Any):
        self.client = client

    def __call__(self, state: Mapping[str, Any]) -> Any:
        from app.ai.jev.models import JevOutcome, SchemaError
        out = self.client.decide(dict(state), QUESTIONS_V8, parse=False)
        if not out.ok:
            return out
        try:
            dec = parse_decision_v8(out.extra.get("raw") or {})
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts, http_status=out.http_status)


def v8_jev_fingerprints() -> dict[str, str]:
    import hashlib
    import inspect
    src = inspect.getsource(JevStateBuilderV8).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V8_FINGERPRINT, "policy": POLICY_V6.fingerprint(), "prompt_version": PROMPT_VERSION_V8,
            "state_version": STATE_VERSION_V8, "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12]}
