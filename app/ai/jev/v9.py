"""Jev for V9 STOCKS (JEV_PROMPT_V9_STOCKS / JEV_STATE_V9). A gate on a V9 stock scalper's candidate, never a trade
source.

The actions and the policy are Jev V6's, unchanged (CONTRADICT -> SKIP, SUPPORT -> TAKE, STRONGLY SUPPORT -> ATTACK,
a failed or late answer -> SKIP). The state is JEV_STATE_V8's (closed 5m / 15m / 1h candles of the bot's own symbol,
the setup, costs, sizing tier and health) re-labelled for a US stock in its regular session, plus the minutes left
before the close. No future return, no rank, no claimed edge.
"""
from __future__ import annotations

import json
from datetime import datetime, time as dtime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.ai.jev.models import JevDecision, fingerprint
from app.ai.jev.state import DecisionSnapshot
from app.ai.jev.v4 import parse_decision_v4
from app.ai.jev.v6 import POLICY_V6
from app.ai.jev.v8 import JevStateBuilderV8

PROMPT_VERSION_V9 = "JEV_PROMPT_V9_STOCKS"
STATE_VERSION_V9 = "JEV_STATE_V9"
ET = ZoneInfo("America/New_York")

QUESTIONS_V9: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "A SCALPING bot proposes the trade in `setup` on one US stock or ETF during the regular session (09:30-"
            "16:00 New York). It decides on closed 5-minute candles, exits at a 1.5R target, at its structural stop "
            "(0.25-1.0% away) or after at most 45 minutes, and never holds overnight. Trades are commission-free; "
            "the cost is mostly the bid/ask spread. This is a LIVE FORWARD paper test: no historical edge is claimed. "
            "Given the bot's own 5m / 15m / 1h trend ladder, the last hour of price action, relative volume, "
            "volatility, the time left in the session, costs and the bot's health, do current conditions CONTRADICT, "
            "SUPPORT or STRONGLY SUPPORT this scalp?"),
        "criteria": {
            "CONTRADICT": ("The immediate move is unlikely or already exhausted: the 15m / 1h trend is against it, the "
                           "entry chases an extended bar, volume does not confirm, volatility is too low to reach the "
                           "target in 45 minutes, or too little session is left. Skip it."),
            "SUPPORT": "A valid scalp with nothing materially against it. Take it at the normal size.",
            "STRONGLY_SUPPORT": ("Trend ladder aligned, momentum and volume confirming, enough volatility and session "
                                 "left to reach 1.5R quickly, and a healthy bot. Exceptional: trade it above normal size."),
        },
    },
}
QUESTIONS_V9_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V9, "questions": QUESTIONS_V9})


def parse_decision_v9(body: Mapping[str, Any]) -> JevDecision:
    return parse_decision_v4(body, QUESTIONS_V9)


def minutes_to_close(t: int) -> float:
    """Minutes from `t` to 16:00 New York the same day (a half day closes earlier: the bots stop entering 50 minutes
    before their session's real close, which the strategy knows from the calendar)."""
    et = datetime.fromtimestamp(t / 1000, ET)
    close = datetime.combine(et.date(), dtime(16, 0), ET)
    return round(max(0.0, (close - et).total_seconds() / 60.0), 1)


class JevStateBuilderV9(JevStateBuilderV8):
    """JEV_STATE_V9 = JEV_STATE_V8 for a US stock: market labels, session time left, equity costs."""

    def build(self, *, t: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], sizing: Mapping[str, Any], health: Mapping[str, Any],
              position: Mapping[str, Any]) -> DecisionSnapshot:
        snap = super().build(t=t, bot=bot, sig=sig, series=series, execution=execution, sizing=sizing, health=health,
                             position=position)
        state = json.loads(json.dumps(snap.state))
        state["bot"]["market"] = "US stock / ETF, regular session"
        state["session"] = {"minutes_to_close": minutes_to_close(t), "overnight": "never held"}
        state["price_action"].pop("chg_24h", None)                 # 288 bars would span several sessions
        state["economics"]["historical_edge"] = "none claimed: V9 is evaluated on live forward data only"
        state["economics"]["commission"] = "none (regulatory fees on sells only)"
        state["execution"]["fill"] = "market order ~60 s after the candle close, regular session only"
        state["state_version"] = STATE_VERSION_V9
        return DecisionSnapshot(ts=int(t), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V9,
                                meta=dict(snap.meta or {}))


class ClientDeciderV9:
    def __init__(self, client: Any):
        self.client = client

    def __call__(self, state: Mapping[str, Any]) -> Any:
        from app.ai.jev.models import JevOutcome, SchemaError
        out = self.client.decide(dict(state), QUESTIONS_V9, parse=False)
        if not out.ok:
            return out
        try:
            dec = parse_decision_v9(out.extra.get("raw") or {})
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts, http_status=out.http_status)


def v9_jev_fingerprints() -> dict[str, str]:
    import hashlib
    import inspect
    src = (inspect.getsource(JevStateBuilderV8) + inspect.getsource(JevStateBuilderV9)).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V9_FINGERPRINT, "policy": POLICY_V6.fingerprint(), "prompt_version": PROMPT_VERSION_V9,
            "state_version": STATE_VERSION_V9, "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12]}
