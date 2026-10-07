"""Jev V6 for the V6 FORWARD ARENA: JEV_PROMPT_V6, JEV_STATE_V6 and JEV_POLICY_V6 (docs/V6_PROTOCOL.md §6).

V1-V5 are frozen evidence and untouched. Jev V6 is a GATE on candidates a frozen V6 strategy already produced -- it
never creates a trade. One question per candidate:

    "Given this setup, do current positioning, trend, market, volatility and funding conditions CONTRADICT, SUPPORT
     or STRONGLY SUPPORT it?"

        CONTRADICT        -> SKIP     (no trade)
        SUPPORT           -> TAKE     (the strategy's own legal size: 1%, or the legal minimum a strong setup allows)
        STRONGLY SUPPORT  -> ATTACK   (2.0% risk; needs a healthy bot and an order the RiskManager accepts,
                                       otherwise TAKE, recorded)

There is no DEFENSIVE size. A candidate whose exchange minimum needs 1.5-2% risk (ATTACK_ONLY) is traded only on
STRONGLY SUPPORT. A failed or late answer is SKIP. The state holds the bot's own trend ladder, the market context
(BTC, ETH, breadth, aggregate funding and open interest), the coin's Bybit positioning, volatility, the costs, the
sizing tier and the bot's health -- all closed at the decision instant. It never holds a future return, the trade's
result, a leaderboard rank or any claim of a historical edge: V6 is judged on live forward data only.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from app.ai.jev.models import JevDecision, fingerprint
from app.ai.jev.policy import PolicyResult
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _r
from app.ai.jev.v2 import _context
from app.ai.jev.v4 import ACTION_FOR, ACTIONS_V4, parse_decision_v4

PROMPT_VERSION_V6 = "JEV_PROMPT_V6"
STATE_VERSION_V6 = "JEV_STATE_V6"
ACTIONS_V6 = ACTIONS_V4

QUESTIONS_V6: dict[str, dict[str, Any]] = {
    "conditions": {
        "type": "choice",
        "instructions": (
            "A futures trading bot proposes the candidate trade in `setup` for one Bybit USDT perpetual. It is a frozen "
            "rule-based strategy family (its hypothesis is `setup.thesis`) that decides on closed hourly candles "
            "(HOURLY, holding up to 24 hours) or 4-hour candles (SWING, up to 72 hours), with a structural stop, a 3R "
            "target and a time stop. This is a LIVE FORWARD test: no historical edge is claimed for the family. Given "
            "this setup, do current positioning, trend, market, volatility and funding conditions CONTRADICT, SUPPORT "
            "or STRONGLY SUPPORT it? Use the bot's own trend ladder, the market context (BTC and ETH trends, breadth, "
            "aggregate funding and open interest), the coin's positioning (funding and its percentile and change, "
            "open-interest changes, price / OI divergence, basis, long/short account ratio), volatility and relative "
            "volume, the round-trip cost and the funding expected over the hold, the sizing tier and the bot's "
            "drawdown. Judge the setup, not the chance of one loss: a 3R target can lose more often than it wins and "
            "still pay."),
        "criteria": {
            "CONTRADICT": ("Current conditions materially contradict the setup: the higher timeframes or the whole "
                           "market point against it, positioning says the move is crowded or already unwinding "
                           "against it, costs or funding over the hold eat most of the target, or volatility "
                           "invalidates the thesis. The bot should not trade it."),
            "SUPPORT": ("The setup is valid and nothing in this state materially contradicts it. The bot should "
                        "trade it at its normal size."),
            "STRONGLY_SUPPORT": ("Several independent conditions strongly reinforce the setup: its own trend ladder "
                                 "and the market aligned with its direction, positioning confirming the thesis, "
                                 "supportive volatility and a healthy bot. Exceptional: the bot should trade it above "
                                 "normal size."),
        },
    },
}
QUESTIONS_V6_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V6, "questions": QUESTIONS_V6})


def parse_decision_v6(body: Mapping[str, Any]) -> JevDecision:
    """Strict: CONTRADICT / SUPPORT / STRONGLY_SUPPORT -> SKIP / TAKE / ATTACK; anything else is a schema error."""
    return parse_decision_v4(body, QUESTIONS_V6)


# ---- policy ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevPolicyV6Config:
    version: str = "JEV_POLICY_V6"
    attack_risk_pct: float = 0.020
    mapping: tuple[tuple[str, str], ...] = tuple(ACTION_FOR.items())
    attack_rule: str = ("STRONGLY_SUPPORT on a healthy bot (< 18% below peak, recent expectancy >= -0.25R) sizes the "
                        "trade at 2% risk if the RiskManager accepts it; otherwise TAKE (an ATTACK_ONLY candidate: SKIP)")
    take_rule: str = "SUPPORT keeps the strategy's own legal size; an ATTACK_ONLY candidate (needs 1.5-2%) is SKIP"
    on_error: str = "SKIP"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mapping"] = dict(self.mapping)
        return d

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V6 = JevPolicyV6Config()


def decide_v6(decision: JevDecision | None, health: str, attack_only: bool, cfg: JevPolicyV6Config = POLICY_V6,
              error_code: str = "") -> PolicyResult:
    """The action (SKIP / TAKE / ATTACK) before the legality probe; `multiplier` is 0 (SKIP), 1 (keep) or -1 (ATTACK:
    the caller converts it to the 2%-risk multiple of the approved size)."""
    if decision is None:
        return PolicyResult("SKIP", "SKIP", 0.0, reason=f"JEV_ERROR {error_code or 'unknown'}")
    chosen = decision.risk_state if decision.risk_state in ACTIONS_V6 else "SKIP"
    if chosen == "SKIP":
        return PolicyResult("SKIP", "SKIP", 0.0, by_risk_state=chosen, reason="Jev: CONTRADICT")
    if chosen == "ATTACK" and health == "OK":
        return PolicyResult("TAKE", "ATTACK", -1.0, by_risk_state=chosen, reason="Jev: STRONGLY_SUPPORT")
    if attack_only:
        why = "needs ATTACK" if chosen == "TAKE" else f"ATTACK refused: bot health {health}"
        return PolicyResult("SKIP", "SKIP", 0.0, by_risk_state=chosen, reason=f"ATTACK_ONLY candidate: {why}",
                            extra={"downgrade": why})
    why = None if chosen == "TAKE" else f"ATTACK -> TAKE: bot health {health}"
    return PolicyResult("TAKE", "TAKE", 1.0, by_risk_state=chosen, reason=f"Jev: {chosen}" + (f"; {why}" if why else ""),
                        extra={"downgrade": why})


# ---- state ----------------------------------------------------------------------------------------------

def _dir_vs(direction: Any, side: str) -> str | None:
    if direction not in ("up", "down", "flat"):
        return None
    if direction == "flat":
        return "FLAT"
    return "WITH" if direction == ("up" if side == "long" else "down") else "AGAINST"


def divergence(price_chg: float | None, oi_chg: float | None, eps: float = 0.005) -> str | None:
    """Price vs open interest over the same 24h: who is driving the move."""
    if price_chg is None or oi_chg is None:
        return None
    up, down = price_chg > eps, price_chg < -eps
    build, cut = oi_chg > eps, oi_chg < -eps
    if up and build:
        return "PRICE_UP_OI_UP (new longs)"
    if up and cut:
        return "PRICE_UP_OI_DOWN (short covering)"
    if down and build:
        return "PRICE_DOWN_OI_UP (new shorts)"
    if down and cut:
        return "PRICE_DOWN_OI_DOWN (long liquidation)"
    return "NO_CLEAR_DIVERGENCE"


class JevStateBuilderV6:
    """JEV_STATE_V6: bot, setup, own trend ladder, market, positioning, volatility, economics, sizing, execution,
    bot health. Built only from series closed at the decision instant."""

    def build(self, *, t: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], sizing: Mapping[str, Any], health: Mapping[str, Any],
              position: Mapping[str, Any]) -> DecisionSnapshot:
        for tf, cs in series.items():
            if any(int(c.close_time) >= t for c in cs):
                raise LookAheadError(f"{tf} candle closes at or after the decision instant")
        meta = getattr(sig, "meta", None) or {}
        side = sig.side
        horizon = meta.get("horizon") or bot.get("horizon")
        tfs = [meta.get("signal_tf") or bot.get("timeframe"), *(meta.get("context_tfs") or [])]
        ladder, agree, against = {}, 0, 0
        for tf in dict.fromkeys(x for x in tfs if x):
            cs = list(series.get(tf) or [])[-120:]
            if len(cs) < 20:
                continue
            block = _context(cs)
            block["vs_trade"] = _dir_vs(block.get("direction"), side)
            agree += block["vs_trade"] == "WITH"
            against += block["vs_trade"] == "AGAINST"
            ladder[tf] = block
        sigs = list(series.get(tfs[0]) or []) if tfs and tfs[0] else []
        day_bars = 24 if horizon == "HOURLY" else 6
        price_chg = (sigs[-1].close / sigs[-1 - day_bars].close - 1.0) if len(sigs) > day_bars and sigs[-1 - day_bars].close else None
        prev_vol = [float(c.volume) for c in sigs[-21:-1]]
        rel_vol = (float(sigs[-1].volume) / (sum(prev_vol) / len(prev_vol))) if (sigs and len(prev_vol) >= 10 and sum(prev_vol) > 0) else None
        pos = dict(meta.get("positioning") or {})
        mkt = dict(meta.get("market") or {})
        taker = float(execution.get("taker_fee") or 0.0)
        half = float(execution.get("half_spread_bps") or 0.0)
        rt_bps = 2.0 * taker * 1e4 + 2.0 * half
        stop = float(meta.get("stop_pct") or 0.0)
        fund = meta.get("expected_funding_pct")
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "family": meta.get("family"), "horizon": horizon,
                    "symbol": bot.get("symbol"), "signal_timeframe": tfs[0] if tfs else None,
                    "context_timeframes": tfs[1:], "execution_timeframe": "1m", "utc_hour": (t // 3_600_000) % 24},
            "setup": {"side": side, "setup": meta.get("setup"), "thesis": meta.get("thesis") or bot.get("thesis"),
                      "fails_when": bot.get("fails_when"), "expected_hold": meta.get("expected_hold"),
                      "time_stop_hours": meta.get("time_stop_h"), "target_r": meta.get("target_r"),
                      "stop_distance_pct": _r(stop * 100, 3) if stop else None,
                      "signal_quality": meta.get("signal_quality"), "quality_factors": meta.get("quality_factors")},
            "trend": {"ladder": ladder, "aligned_with_trade": agree, "against_trade": against,
                      "market_regime_vs_trade": meta.get("regime")},
            "market": mkt,
            "positioning": {**{k: _r(v, 6) if isinstance(v, float) else v for k, v in pos.items()},
                            "price_chg_24h": _r(price_chg, 5),
                            "price_vs_oi_24h": divergence(price_chg, pos.get("oi_chg_24h")),
                            "liquidations": "not available (Bybit publishes no liquidation history)"},
            "volatility": {"band": meta.get("vol_band"),
                           "atr_pct_signal": (ladder.get(tfs[0]) or {}).get("atr_pct") if tfs else None,
                           "relative_volume_signal_bar": _r(rel_vol, 3)},
            "economics": {"historical_edge": "none claimed: V6 is evaluated on live forward data only",
                          "round_trip_cost_bps": _r(rt_bps, 3),
                          "round_trip_cost_r": _r(rt_bps / 1e4 / stop, 4) if stop else None,
                          "expected_funding_over_hold_pct": _r(fund * 100, 4) if isinstance(fund, (int, float)) else None,
                          "expected_funding_over_hold_r": _r(fund / stop, 4) if (isinstance(fund, (int, float)) and stop) else None},
            "sizing": {"tier": sizing.get("tier"), "legal_min_risk_pct": sizing.get("legal_min_risk_pct"),
                       "attack_only": bool(sizing.get("attack_only")), "proposed_risk_pct": position.get("proposed_risk_pct")},
            "execution": {"taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                          "spread_source": execution.get("spread_source"),
                          "proposed_leverage": position.get("proposed_leverage"),
                          "available_margin_pct": position.get("available_margin_pct")},
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V6,
        }
        state = json.loads(json.dumps(state, default=str))
        return DecisionSnapshot(ts=int(t), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V6,
                                meta={"ladder": list(ladder)})


class ClientDeciderV6:
    """Asks Jev the V6 question through the shared client (no retries beyond the frozen V6 budget) and parses the
    answer strictly; any failure is a coded outcome, never an exception."""

    def __init__(self, client: Any):
        self.client = client

    def __call__(self, state: Mapping[str, Any]) -> Any:
        from app.ai.jev.models import JevOutcome, SchemaError
        out = self.client.decide(dict(state), QUESTIONS_V6, parse=False)
        if not out.ok:
            return out
        try:
            dec = parse_decision_v6(out.extra.get("raw") or {})
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts,
                          http_status=out.http_status)


def v6_fingerprints() -> dict[str, str]:
    import hashlib
    import inspect
    src = inspect.getsource(JevStateBuilderV6).replace("\r\n", "\n")
    return {"prompt": QUESTIONS_V6_FINGERPRINT, "policy": POLICY_V6.fingerprint(),
            "state_version": STATE_VERSION_V6, "prompt_version": PROMPT_VERSION_V6,
            "state_builder": hashlib.sha256(src.encode()).hexdigest()[:12]}
