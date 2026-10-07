"""Jev for Bizzy's +JEV twin (JEV_PROMPT_V12_BIZZY / JEV_STATE_V12), operator's request 2026-10-02.

beebots' Bizzy shows Jev a menu only when a coin is through its trigger -- BREAKOUT_<coin> or WAIT ("not convinced, keep
waiting") -- and takes the breakout at full size if Jev picks it. PaperLab asks the equivalent with its three-way
conditions question (parse_decision_v4):

    CONTRADICT                 WAIT: the twin skips; the bot re-proposes after its 5-minute cooldown while the coin is
                               still above its trigger (a timeout, an error or a late answer is also WAIT)
    SUPPORT / STRONGLY_SUPPORT take the breakout at Bizzy's full size (2x the book); conviction does not change the size
                               (beebots' conviction is a label, the size is always full)

The state holds only candles closed at the decision instant (5m / 15m / 1h of the coin), today's breakout levels, costs
and the bot's health. No future return, no claimed edge.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.ai.jev.models import JevDecision, fingerprint
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import _context
from app.ai.jev.v4 import parse_decision_v4
from app.ai.jev.v6 import _dir_vs

PROMPT_VERSION_V12 = "JEV_PROMPT_V12_BIZZY"
STATE_VERSION_V12 = "JEV_STATE_V12"

QUESTIONS_V12: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "Bizzy is a one-shot DAY BREAKOUT bot (Larry Williams volatility breakout) on Bybit USDT perpetuals. Each "
            "UTC day she may make ONE trade: when a coin trades above today's UTC open plus half of yesterday's "
            "high-low range, she can go LONG at full size (2x her book) and ride it to the UTC day close; her stop is "
            "back below today's open. The coin in `setup` is through its trigger right now. Round-trip taker fees and "
            "spread are a real cost, and a failed breakout costs the whole distance back to the open at 2x. This is a "
            "LIVE FORWARD paper test: no historical edge is claimed. Only take a breakout that looks real. Given the "
            "coin's 5m / 15m / 1h trend ladder, how far it is through the trigger, the day's move so far, recent price "
            "action, relative volume, volatility, costs and the bot's health, do current conditions CONTRADICT, "
            "SUPPORT or STRONGLY SUPPORT taking this breakout now?"),
        "criteria": {
            "CONTRADICT": ("Not convinced, keep waiting: the higher-timeframe trend is against it, the move is a spike "
                           "that is already fading, volume does not confirm, or it is too late in the day for the move "
                           "to pay its costs."),
            "SUPPORT": "A real breakout with nothing materially against it: take it at full size.",
            "STRONGLY_SUPPORT": ("Trend ladder aligned, volume and momentum confirming a genuine range expansion: take "
                                 "it at full size."),
        },
    },
}
QUESTIONS_V12_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V12, "questions": QUESTIONS_V12})


def parse_decision_v12(body: Mapping[str, Any]) -> JevDecision:
    return parse_decision_v4(body, QUESTIONS_V12)


class JevStateBuilderV12:
    """JEV_STATE_V12: Bizzy, the breakout coin (trend ladder 5m / 15m / 1h, recent price action, volume, volatility),
    today's levels, economics, sizing and bot health -- all closed at the decision instant."""

    def build(self, *, t: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], sizing: Mapping[str, Any], health: Mapping[str, Any],
              position: Mapping[str, Any]) -> DecisionSnapshot:
        for tf, cs in series.items():
            if any(int(c.close_time) >= t for c in cs):
                raise LookAheadError(f"{tf} candle closes at or after the decision instant")
        meta = getattr(sig, "meta", None) or {}
        ladder, agree, against = {}, 0, 0
        for tf in ("5m", "15m", "1h"):
            cs = list(series.get(tf) or [])[-120:]
            if len(cs) < 20:
                continue
            block = _context(cs)
            block["vs_trade"] = _dir_vs(block.get("direction"), sig.side)
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
        minutes_left = int((((t // 86_400_000) + 1) * 86_400_000 - t) / 60_000)
        state = {
            "bot": {"name": "Bizzy", "strategy_id": bot.get("strategy_id"), "family": meta.get("family"),
                    "symbol": sig.symbol, "rule": "one long day-breakout a day, full size 2x, ride to the UTC close",
                    "utc_hour": (t // 3_600_000) % 24, "minutes_to_day_close": minutes_left},
            "setup": {"side": sig.side, "setup": meta.get("setup"), "thesis": bot.get("thesis"),
                      "fails_when": bot.get("fails_when"), "day_open": meta.get("day_open"),
                      "trigger": meta.get("trigger"), "through_trigger_pct": meta.get("through_trigger_pct"),
                      "day_move_pct": meta.get("day_move_pct"), "prev_day_range_pct": meta.get("prev_range_pct"),
                      "stop_distance_pct": _r(stop * 100, 3) if stop else None, "exit": meta.get("exit")},
            "trend": {"ladder": ladder, "aligned_with_trade": agree, "against_trade": against},
            "price_action": {"last_hour_5m": recent, "chg_15m": _r(chg(3), 5), "chg_1h": _r(chg(12), 5),
                             "chg_4h": _r(chg(48), 5), "relative_volume_5m": _r(rel_vol, 3)},
            "volatility": {"atr_pct_5m": (ladder.get("5m") or {}).get("atr_pct"),
                           "atr_pct_1h": (ladder.get("1h") or {}).get("atr_pct")},
            "economics": {"historical_edge": "none claimed: the rule lost after costs in its back-test (V12 study)",
                          "round_trip_cost_bps": _r(rt_bps, 3),
                          "round_trip_cost_share_of_stop": _r(rt_bps / 1e4 / stop, 4) if stop else None},
            "sizing": {"size": "full: 2x the book in notional", "proposed_risk_pct": position.get("proposed_risk_pct")},
            "execution": {"taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                          "spread_source": execution.get("spread_source"), "fill": "market order ~60 s after the minute",
                          "proposed_leverage": position.get("proposed_leverage")},
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V12,
        }
        state = json.loads(json.dumps(state, default=str))
        return DecisionSnapshot(ts=int(t), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V12,
                                meta={"ladder": list(ladder)})


class ClientDeciderV12:
    """Asks Bizzy's breakout question through the shared client and parses strictly; failures are coded outcomes."""

    def __init__(self, client: Any):
        self.client = client

    def __call__(self, state: Mapping[str, Any]) -> Any:
        from app.ai.jev.models import JevOutcome, SchemaError
        out = self.client.decide(dict(state), QUESTIONS_V12, parse=False)
        if not out.ok:
            return out
        try:
            dec = parse_decision_v12(out.extra.get("raw") or {})
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts, http_status=out.http_status)


def v12_jev_fingerprints() -> dict[str, str]:
    import hashlib
    import inspect
    src = inspect.getsource(JevStateBuilderV12).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V12_FINGERPRINT, "prompt_version": PROMPT_VERSION_V12, "state_version": STATE_VERSION_V12,
            "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12]}
