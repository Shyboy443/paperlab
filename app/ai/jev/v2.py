"""Jev V2 for the V3 aggressive intraday arena: JEV_PROMPT_V2, JEV_STATE_V2 and JEV_POLICY_V2.

V1 (models.QUESTIONS_V1, state.JevStateBuilder, policy.POLICY_V1) is frozen historical evidence and
is not touched. V2 is a NEW version with its own question set, state and policy, fingerprinted
separately so no V2 answer can ever be mistaken for a V1 one (different cache keys, too).

Pipeline position (docs/V3_PROTOCOL.md):

    strategy candidate -> legality / min-notional -> cost gate -> JEV V2 -> RiskManager -> execution

Jev is asked only about candidates that already passed legality and EDGE_COST_RATIO >= 2.0, never on
every candle. Its `action` choice (SKIP / DEFENSIVE / NORMAL / ATTACK) translates deterministically
into a risk multiplier; ATTACK additionally needs EDGE_COST_RATIO >= 3.0. A failed request is SKIP.
The resized order goes back through the RiskManager, which Jev can never bypass.

The state follows V1's two rules: nothing that did not exist at the signal bar's close, and no
calendar dates or absolute price levels (returns, ratios, distances and the UTC hour only).
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from app.ai.jev.gate import GateVerdict, ReproducibilityError, cache_key, outcome_from_row
from app.ai.jev.models import JevDecision, JevOutcome, SchemaError, _prob, _probs, fingerprint
from app.ai.jev.policy import PolicyResult
from app.ai.jev.state import DecisionSnapshot, LookAheadError, _atr, _ema, _r, _rsi

PROMPT_VERSION_V2 = "JEV_PROMPT_V2"
STATE_VERSION_V2 = "JEV_STATE_V2"
ACTIONS: tuple[str, ...] = ("SKIP", "DEFENSIVE", "NORMAL", "ATTACK")
LEVEL_ORDER = {"SKIP": 0, "DEFENSIVE": 1, "NORMAL": 2, "ATTACK": 3}
ACTION_OF = {"SKIP": "SKIP", "DEFENSIVE": "REDUCE", "NORMAL": "TAKE", "ATTACK": "TAKE"}
FUNDING_PERIOD_MS = 8 * 3600 * 1000

QUESTIONS_V2: dict[str, dict[str, Any]] = {
    "win": {
        "type": "noul",
        "instructions": (
            "An aggressive intraday trading bot's rule-based strategy proposes the candidate trade in "
            "`signal` for one crypto perpetual. It has already passed the exchange minimums and a cost "
            "check. Using only the fast-market, multi-timeframe context, execution-cost, derivatives and "
            "bot-health data in this state: will this trade close with a net profit after fees, spread "
            "and slippage?"),
        "criteria": {
            "true": "The trade reaches enough of its objective to finish with a net profit after all costs.",
            "false": "The trade stops out, stalls or times out with a net loss after all costs.",
        },
    },
    "action": {
        "type": "choice",
        "instructions": ("Choose how this intraday candidate should be sized. The bot is built to trade "
                         "actively; reserve SKIP for candidates that are likely to lose after costs."),
        "criteria": {
            "ATTACK": ("Clearly favourable: the fast market and the higher-timeframe context both support "
                       "the direction, momentum is building, the expected move is large relative to the "
                       "round-trip cost, and recent bot health is good. Size above normal."),
            "NORMAL": "A typical tradeable setup for this strategy, with no strong evidence either way. Standard size.",
            "DEFENSIVE": ("Tradeable but weaker than usual: mixed context, stretched or fading momentum, "
                          "cost high relative to the expected move, or the bot is in a drawdown. Half size."),
            "SKIP": ("Likely to lose after costs: the context contradicts the signal, the move is already "
                     "exhausted, or costs dominate the expected move. Do not trade."),
        },
    },
    "setup_quality": {
        "type": "score",
        "instructions": "Rate the quality of this intraday trade setup.",
        "criteria": [
            "Very poor: contradicted by the context or dominated by costs",
            "Weak",
            "Average",
            "Good",
            "Excellent: aligned across timeframes with a large expected move relative to costs",
        ],
    },
}
QUESTIONS_V2_FINGERPRINT = fingerprint({"version": PROMPT_VERSION_V2, "questions": QUESTIONS_V2})


def parse_decision_v2(body: Mapping[str, Any], questions: Mapping[str, Any] = QUESTIONS_V2) -> JevDecision:
    """Strict, like V1: any value outside the schema is an error. `take_probability` carries P(win),
    `risk_state` the action choice; V2 asks no regime question."""
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise SchemaError("MISSING_ANSWER", "response has no answers object")
    for key, q in questions.items():
        a = answers.get(key)
        if not isinstance(a, dict):
            raise SchemaError("MISSING_ANSWER", f"no answer for {key}")
        if a.get("type") != q["type"]:
            raise SchemaError("SCHEMA_MISMATCH", f"{key} answered as {a.get('type')!r}")
    win = _prob(answers["win"].get("noul"), "win.noul")
    act = answers["action"]
    if act.get("choice") not in ACTIONS:
        raise SchemaError("SCHEMA_MISMATCH", "action.choice not an allowed option")
    aprobs = _probs(act.get("probabilities"), set(ACTIONS), "action")
    sq = answers["setup_quality"]
    score = sq.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not (0 <= score <= 4):
        raise SchemaError("SCHEMA_MISMATCH", "setup_quality.score outside the scale")
    qprobs = _probs(sq.get("probabilities"), {str(i) for i in range(5)}, "setup_quality")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return JevDecision(take_probability=win, risk_state=str(act["choice"]), risk_probabilities=aprobs,
                       setup_quality=float(score) / 4.0, quality_score=float(score),
                       quality_probabilities=qprobs, regime="", regime_probabilities={},
                       model_resolved=str(body.get("model") or ""), provider=str(body.get("provider") or ""),
                       request_id=str(body.get("id") or ""), input_tokens=int(usage.get("input_tokens") or 0),
                       output_tokens=int(usage.get("output_tokens") or 0), cost_usd=float(usage.get("cost") or 0.0))


# ---- policy ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JevPolicyV2Config:
    version: str = "JEV_POLICY_V2"
    multipliers: tuple[tuple[str, float], ...] = (("SKIP", 0.0), ("DEFENSIVE", 0.5), ("NORMAL", 1.0),
                                                  ("ATTACK", 1.5))
    attack_strong_multiplier: float = 2.0
    attack_strong_prob: float = 0.60
    attack_min_edge_to_cost: float = 3.0
    on_error: str = "SKIP"

    def multiplier(self, level: str) -> float:
        return dict(self.multipliers)[level]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["multipliers"] = dict(self.multipliers)
        return d

    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V2 = JevPolicyV2Config()


def decide_v2(decision: JevDecision | None, edge_to_cost: float | None, cfg: JevPolicyV2Config = POLICY_V2,
              error_code: str = "") -> PolicyResult:
    """Jev's action choice -> level -> multiplier. Only two deterministic rules act on it: ATTACK needs
    EDGE_COST_RATIO >= 3 (else NORMAL), and ATTACK sizes 2.0x only when Jev itself put >= 60% on it."""
    if decision is None:
        level = cfg.on_error
        return PolicyResult(ACTION_OF[level], level, cfg.multiplier(level), reason=f"JEV_ERROR {error_code or 'unknown'}")
    chosen = decision.risk_state if decision.risk_state in LEVEL_ORDER else "SKIP"
    level, why = chosen, f"Jev chose {chosen}"
    if chosen == "ATTACK" and not (isinstance(edge_to_cost, (int, float)) and edge_to_cost >= cfg.attack_min_edge_to_cost):
        level, why = "NORMAL", f"Jev chose ATTACK; EDGE_COST_RATIO {edge_to_cost} < {cfg.attack_min_edge_to_cost} -> NORMAL"
    mult = cfg.multiplier(level)
    p_attack = float((decision.risk_probabilities or {}).get("ATTACK") or 0.0)
    if level == "ATTACK" and p_attack >= cfg.attack_strong_prob:
        mult, why = cfg.attack_strong_multiplier, why + f"; P(ATTACK) {p_attack:.2f} -> {cfg.attack_strong_multiplier}x"
    return PolicyResult(ACTION_OF[level], level, mult, by_probability=f"{decision.take_probability:.3f}",
                        by_risk_state=chosen, reason=why)


# ---- state ----------------------------------------------------------------------------------------------

def _ret(closes: Sequence[float], k: int) -> float | None:
    return (closes[-1] / closes[-1 - k] - 1.0) if len(closes) > k and closes[-1 - k] else None


def _context(candles: Sequence[Any]) -> dict[str, Any]:
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    n = len(closes)
    if n < 2:
        return {"bars": n}
    e20, e50 = _ema(closes, 20), _ema(closes, 50)
    atr = _atr(highs, lows, closes, 14)
    spread = (e20[-1] / e50[-1] - 1.0) if n >= 50 and e50[-1] else None
    slope = (e50[-1] / e50[-6] - 1.0) if n >= 56 and e50[-6] else None
    direction = None
    if spread is not None and slope is not None:
        direction = ("up" if spread > 0 and slope > 0 and closes[-1] > e50[-1] else
                     "down" if spread < 0 and slope < 0 and closes[-1] < e50[-1] else "flat")
    return {"bars": n, "direction": direction, "ema20_vs_ema50": _r(spread), "ema50_slope_5": _r(slope),
            "ret_5": _r(_ret(closes, 5)), "rsi_14": _r(_rsi(closes, 14), 2),
            "atr_pct": _r(atr / closes[-1]) if atr and closes[-1] else None}


def session_of(hour: int) -> str:
    return "ASIA" if hour < 8 else "EUROPE" if hour < 13 else "US" if hour < 21 else "LATE_US"


class JevStateBuilderV2:
    """JEV_STATE_V2: signal, fast market (1m tape), two context timeframes, execution, derivatives,
    bot health. Built only from series closed at the decision time `ts`."""

    def build(self, *, ts: int, bot: Mapping[str, Any], sig: Any, series: Mapping[str, Sequence[Any]],
              execution: Mapping[str, Any], health: Mapping[str, Any], position: Mapping[str, Any],
              funding: Sequence[tuple[int, float]] = ()) -> DecisionSnapshot:
        for tf, cs in series.items():
            late = [c for c in cs if int(c.close_time) > ts]
            if late:
                raise LookAheadError(f"{len(late)} {tf} candle(s) close after the decision time")
        if any(int(f[0]) > ts for f in funding):
            raise LookAheadError("funding settles after the decision time")
        meta = getattr(sig, "meta", None) or {}
        tf = bot.get("timeframe")
        one = list(series.get("1m") or [])[-120:]
        sigs = list(series.get(tf) or [])[-120:]
        closes1 = [float(c.close) for c in one]
        rets1 = [closes1[i] / closes1[i - 1] - 1.0 for i in range(1, len(closes1)) if closes1[i - 1]]
        last15 = one[-15:]
        vol15 = sum(float(c.volume) for c in last15)
        tb15 = sum(float(getattr(c, "taker_buy_volume", 0.0) or 0.0) for c in last15)
        sclose = [float(c.close) for c in sigs]
        shigh = [float(c.high) for c in sigs]
        slow_ = [float(c.low) for c in sigs]
        satr = _atr(shigh, slow_, sclose, 14) if len(sigs) > 15 else None
        last_bar = sigs[-1] if sigs else None
        prev_vol = [float(c.volume) for c in sigs[-21:-1]]
        price = sclose[-1] if sclose else None
        hi50 = max(shigh[-50:]) if shigh else None
        lo50 = min(slow_[-50:]) if slow_ else None
        r5 = _ret(closes1, 5)
        r5_prev = (closes1[-6] / closes1[-11] - 1.0) if len(closes1) > 11 and closes1[-11] else None
        taker = float(execution.get("taker_fee") or 0.0)
        half = float(execution.get("half_spread_bps") or 0.0)
        slip = float(execution.get("expected_slippage_bps") or half)
        round_trip_bps = 2.0 * taker * 1e4 + 2.0 * slip
        em = meta.get("expected_move_pct")
        last_f = funding[-1][1] if funding else None
        recent_f = [f[1] for f in list(funding)[-3:]]
        hour = (ts // 3_600_000) % 24
        ctx_fast, ctx_slow = (meta.get("context_tfs") or [None, None])[:2]
        state = {
            "bot": {"strategy_id": bot.get("strategy_id"), "family": meta.get("family"),
                    "signal_timeframe": tf, "context_timeframes": [ctx_fast, ctx_slow],
                    "execution_timeframe": "1m", "utc_hour": hour, "session": session_of(hour)},
            "signal": {
                "side": sig.side, "signal_quality": _r(meta.get("signal_quality"), 3),
                "quality_factors": meta.get("quality_factors"),
                "expected_move_bps": _r(em * 1e4 if isinstance(em, (int, float)) else None, 2),
                "stop_distance_bps": _r((meta.get("stop_pct") or 0) * 1e4, 2) if meta.get("stop_pct") else None,
                "target_distance_bps": _r((meta.get("target_pct") or 0) * 1e4, 2) if meta.get("target_pct") else None,
                "reward_risk": _r(meta.get("reward_risk"), 3),
                "edge_to_cost": _r(meta.get("edge_to_cost"), 3),
                "max_hold_minutes": int(sig.max_hold_s // 60) if getattr(sig, "max_hold_s", None) else None,
                "setup": str(getattr(sig, "reason", "") or "")[:90],
            },
            "fast_market": {
                "ret_1m": _r(_ret(closes1, 1)), "ret_3m": _r(_ret(closes1, 3)), "ret_5m": _r(r5),
                "ret_15m": _r(_ret(closes1, 15)), "ret_60m": _r(_ret(closes1, 60)),
                "momentum_accel_5m": _r(r5 - r5_prev) if (r5 is not None and r5_prev is not None) else None,
                "realized_vol_1m_60": _r(statistics.pstdev(rets1[-60:])) if len(rets1) >= 20 else None,
                "atr_pct_signal": _r(satr / price) if (satr and price) else None,
                "range_expansion": _r((last_bar.high - last_bar.low) / satr, 3) if (last_bar and satr) else None,
                "relative_volume": _r(float(last_bar.volume) / (sum(prev_vol) / len(prev_vol)), 3)
                if (last_bar is not None and len(prev_vol) >= 10 and sum(prev_vol) > 0) else None,
                "taker_buy_share_signal_bar": _r(float(getattr(last_bar, "taker_buy_volume", 0.0) or 0.0) / float(last_bar.volume), 4)
                if (last_bar is not None and last_bar.volume and getattr(last_bar, "taker_buy_volume", 0.0)) else None,
                "taker_buy_share_15m": _r(tb15 / vol15, 4) if (vol15 > 0 and tb15 > 0) else None,
                "dist_to_high_50_atr": _r((hi50 - price) / satr, 3) if (hi50 and price and satr) else None,
                "dist_to_low_50_atr": _r((price - lo50) / satr, 3) if (lo50 and price and satr) else None,
            },
            "context": {"fast": _context(list(series.get(ctx_fast) or [])[-120:]) if ctx_fast else None,
                        "slow": _context(list(series.get(ctx_slow) or [])[-120:]) if ctx_slow else None},
            "execution": {
                "taker_fee_bps": _r(taker * 1e4, 3), "half_spread_bps": _r(half, 3),
                "expected_slippage_bps": _r(slip, 3), "round_trip_cost_bps": _r(round_trip_bps, 3),
                "expected_move_vs_cost": _r((em * 1e4) / round_trip_bps, 3) if (isinstance(em, (int, float)) and round_trip_bps > 0) else None,
                "min_legal_notional_vs_equity": position.get("min_legal_notional_vs_equity"),
                "proposed_notional_vs_equity": position.get("proposed_notional_vs_equity"),
                "effective_leverage": position.get("proposed_notional_vs_equity"),
                "proposed_leverage": position.get("proposed_leverage"),
                "proposed_risk_pct": position.get("proposed_risk_pct"),
                "available_margin_pct": position.get("available_margin_pct"),
                "leverage_ceiling": position.get("leverage_ceiling"),
            },
            "derivatives": {
                "last_funding_rate": _r(last_f, 6),
                "recent_funding_direction": (("positive" if sum(recent_f) > 0 else "negative" if sum(recent_f) < 0 else "flat")
                                             if recent_f else None),
                "minutes_to_next_funding": int((FUNDING_PERIOD_MS - (ts % FUNDING_PERIOD_MS)) // 60000),
                "open_interest": "not available",
            },
            "bot_health": dict(health),
            "state_version": STATE_VERSION_V2,
        }
        return DecisionSnapshot(ts=int(ts), state=state, fingerprint=fingerprint(state), version=STATE_VERSION_V2,
                                meta={"bars_1m": len(one), "bars_signal": len(sigs)})


def health_features_v2(equity: float, peak: float, start: float, trades: Sequence[Any], ts: int,
                       window: int = 20) -> dict[str, Any]:
    """Bot health from its OWN closed trades: expectancy, PF, wins/losses, streak, trades today and
    the recent cost-to-edge ratio (costs / |gross|)."""
    recent = list(trades)[-window:]
    rs = [float(t.r_multiple) for t in recent]
    nets = [float(t.net) for t in recent]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    streak = 0
    for t in reversed(list(trades)):
        if float(t.net) > 0:
            break
        streak += 1
    day0 = ts - ts % 86_400_000
    gross = sum(abs(float(t.pnl)) for t in recent)
    costs = sum(float(t.fees) for t in recent)
    return {
        "equity_vs_start": _r(equity / start - 1.0 if start else None, 4),
        "drawdown": _r(1.0 - equity / peak if peak > 0 else 0.0, 4),
        "closed_trades": len(trades), "recent_trades": len(recent),
        "recent_expectancy_r": _r(sum(rs) / len(rs), 3) if rs else None,
        "recent_profit_factor": _r(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
        "recent_wins": len(wins), "recent_losses": len(losses), "losing_streak": streak,
        "trades_today": sum(1 for t in trades if int(t.exit_ts) >= day0),
        "recent_cost_to_edge": _r(costs / gross, 3) if gross > 0 else None,
    }


# ---- deciders and the gate ------------------------------------------------------------------------------

class ClientDeciderV2:
    def __init__(self, client: Any):
        self.client = client

    def decide(self, state: Any) -> JevOutcome:
        out = self.client.decide(state, QUESTIONS_V2, parse=False)
        if not out.ok:
            return out
        raw = out.extra.get("raw") or {}
        try:
            dec = parse_decision_v2(raw)
        except SchemaError as e:
            return JevOutcome(False, error_code=e.code, error_message=str(e)[:200], latency_ms=out.latency_ms,
                              attempts=out.attempts, http_status=out.http_status)
        return JevOutcome(True, decision=dec, latency_ms=out.latency_ms, attempts=out.attempts,
                          http_status=out.http_status)


class CachedDeciderV2:
    """The run's ledger first, then the shared cache, then Jev; V2 constants on every cache row."""

    def __init__(self, storage: Any, inner: Any, run_id: str, bot_key: str, model: str,
                 replay_only: bool = False, max_calls: int | None = None, max_cost_usd: float | None = None):
        self.storage, self.inner, self.run_id, self.bot_key, self.model = storage, inner, run_id, bot_key, model
        self.replay_only, self.max_calls, self.max_cost_usd = replay_only, max_calls, max_cost_usd

    def _spent(self) -> tuple[int, float]:
        row = self.storage.conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM jev_decisions "
                                        "WHERE run_id=? AND source='api'", (self.run_id,)).fetchone()
        return int(row[0]), float(row[1])

    def decide(self, key: str, snap: DecisionSnapshot) -> tuple[JevOutcome, str]:
        row = self.storage.jev_ledger_lookup(self.run_id, self.bot_key, key)
        if row is not None:
            return outcome_from_row(row), "ledger"
        cached = self.storage.jev_cache_get(key)
        if cached is not None:
            out = JevOutcome.from_dict(json.loads(cached["outcome_json"] or "{}"))
            out.cache_hit = True
            return out, "cache"
        if self.replay_only:
            raise ReproducibilityError(f"{self.bot_key}: no frozen decision for {key}")
        if self.inner is None:
            return JevOutcome(False, error_code="NOT_CONFIGURED", error_message="no decision source"), "none"
        if self.max_calls is not None or self.max_cost_usd is not None:
            n, cost = self._spent()
            if (self.max_calls is not None and n >= self.max_calls) or \
                    (self.max_cost_usd is not None and cost >= self.max_cost_usd):
                return JevOutcome(False, error_code="BUDGET", error_message="call/cost budget exhausted"), "budget"
        out = self.inner.decide(dict(snap.state))
        if out.ok and out.decision is not None:
            self.storage.jev_cache_put({"cache_key": key, "model_requested": self.model,
                                        "model_resolved": out.decision.model_resolved,
                                        "prompt_version": PROMPT_VERSION_V2,
                                        "questions_fingerprint": QUESTIONS_V2_FINGERPRINT,
                                        "state_fingerprint": snap.fingerprint, "outcome": out.to_dict()})
        return out, "api"


def _series(g: Mapping[str, Any], symbol: str, tfs: Sequence[str]) -> dict[str, list[Any]]:
    ctx, ts = g.get("ctx"), int(g["ts"])
    out: dict[str, list[Any]] = {}
    if ctx is None:
        return {tfs[0]: list(g.get("candles") or [])} if tfs else {}
    for tf in dict.fromkeys(tfs):
        out[tf] = [c for c in ctx.candles(symbol, tf) if int(c.close_time) <= ts]
    return out


class JevGateV2:
    """ReplayEngine gate for a +JEV2 bot. Only scales a size the RiskManager already approved."""

    def __init__(self, decider: Any, storage: Any, run_id: str, bot: Mapping[str, Any], pair_id: str,
                 model: str, policy: JevPolicyV2Config = POLICY_V2, builder: JevStateBuilderV2 | None = None,
                 clock: Callable[[], float] = time.time, persist: bool = True):
        self.decider, self.storage, self.run_id = decider, storage, run_id
        self.bot, self.pair_id, self.model, self.policy = dict(bot), pair_id, model, policy
        self.builder = builder or JevStateBuilderV2()
        self.clock, self.persist = clock, persist
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def context(self, sig: Any, g: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        eq = float(g["equity"])
        h = g["health"]
        d = g["decision"]
        health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]),
                                    list(g.get("trades") or []), int(g["ts"]))
        rules = g.get("rules")
        price = float(g.get("price") or 0.0)
        min_legal = rules.min_order_notional(price, 1.0) if (rules is not None and price > 0) else None
        position = {"open_positions": g["open_positions"],
                    "available_margin_pct": _r(g["available"] / eq, 4) if eq > 0 else None,
                    "leverage_ceiling": g["leverage_ceiling"],
                    "proposed_risk_pct": _r(d.risk_usd / eq, 5) if eq > 0 else None,
                    "proposed_notional_vs_equity": _r(d.notional / eq, 4) if eq > 0 else None,
                    "min_legal_notional_vs_equity": _r(min_legal / eq, 4) if (min_legal and eq > 0) else None,
                    "proposed_leverage": d.leverage}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": g["half_spread_bps"],
                     "expected_slippage_bps": g["expected_slippage_bps"]}
        return health, position, execution

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        health, position, execution = self.context(sig, g)
        tfs = [self.bot["timeframe"], *(meta.get("context_tfs") or []), "1m"]
        snap = self.builder.build(ts=int(g["ts"]), bot=self.bot, sig=sig, series=_series(g, self.bot["symbol"], tfs),
                                  execution=execution, health=health, position=position, funding=g["funding"])
        key = cache_key(model=self.model, bot_version=self.bot.get("control_version", ""),
                        strategy_id=self.bot["strategy_id"], params_version=self.bot["params_version"],
                        symbol=self.bot["symbol"], timeframe=self.bot["timeframe"], signal_ts=int(g["ts"]),
                        state_fingerprint=snap.fingerprint, prompt_version=PROMPT_VERSION_V2,
                        questions_fingerprint=QUESTIONS_V2_FINGERPRINT)
        t0 = self.clock()
        out, source = self.decider.decide(key, snap)
        roundtrip = int((self.clock() - t0) * 1000)
        e2c = meta.get("edge_to_cost")
        pol = decide_v2(out.decision if out.ok else None, e2c, self.policy, out.error_code)
        did = hashlib.sha256(f"{self.run_id}|{self.bot['key']}|{key}".encode()).hexdigest()[:20]
        dec = out.decision
        self.decisions += 1
        latency = out.latency_ms if source == "api" else None
        if self.persist and source != "ledger" and self.storage is not None:
            self.storage.save_jev_decision({
                "id": did, "run_id": self.run_id, "bot_key": self.bot["key"], "pair_id": self.pair_id,
                "cache_key": key, "signal_ts": int(g["ts"]), "symbol": self.bot["symbol"],
                "timeframe": self.bot["timeframe"], "side": sig.side, "model_requested": self.model,
                "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V2,
                "policy_version": self.policy.version, "state_fingerprint": snap.fingerprint,
                "state_json": json.dumps(dict(snap.state)),
                "take_probability": dec.take_probability if dec else None,
                "setup_quality": dec.setup_quality if dec else None,
                "risk_state": dec.risk_state if dec else None, "regime": None,
                "answers_json": json.dumps(dec.to_dict()) if dec else None,
                "final_action": pol.action, "final_level": pol.level, "risk_multiplier": pol.multiplier,
                "request_latency_ms": out.latency_ms if source == "api" else 0,
                "roundtrip_ms": roundtrip if source == "api" else 0,
                "input_tokens": dec.input_tokens if (dec and source == "api") else 0,
                "output_tokens": dec.output_tokens if (dec and source == "api") else 0,
                "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0,
                "cache_hit": int(source == "cache"), "source": source, "attempts": out.attempts,
                "error_code": out.error_code or None, "error_message": out.error_message or None,
                "created_ts": int(self.clock() * 1000)})
        self.rows.append({"id": did, "ts": int(g["ts"]), "side": sig.side,
                          "p_win": dec.take_probability if dec else None,
                          "chosen": dec.risk_state if dec else None,
                          "p_attack": (dec.risk_probabilities or {}).get("ATTACK") if dec else None,
                          "quality_jev": dec.setup_quality if dec else None,
                          "level": pol.level, "mult": pol.multiplier, "e2c": e2c,
                          "signal_quality": meta.get("signal_quality"), "source": source,
                          "latency_ms": latency, "error": out.error_code or None,
                          "cost_usd": dec.cost_usd if (dec and source == "api") else 0.0})
        info = {"decision_id": did, "jev_action": pol.action, "jev_level": pol.level,
                "jev_multiplier": pol.multiplier, "jev_source": source,
                "take_probability": dec.take_probability if dec else None, "jev_error": out.error_code or None}
        return GateVerdict(pol.multiplier, info, error=not out.ok)


# ---- baselines that stand in for Jev --------------------------------------------------------------------

def _u(*parts: Any) -> float:
    """Deterministic uniform in [0, 1) from the parts (seeded, platform-independent)."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:13]
    return int(h, 16) / float(1 << 52)


class PolicyGate:
    """ALWAYS-TAKE (constant NORMAL) and RANDOM-FILTER (a random action drawn from the Jev bot's own
    action distribution, through the SAME deterministic ATTACK rule). No request is ever made."""

    def __init__(self, kind: str, bot_key: str, seed: int = 0, distribution: Mapping[str, float] | None = None,
                 attack_strong_share: float = 0.0, policy: JevPolicyV2Config = POLICY_V2):
        self.kind, self.bot_key, self.seed, self.policy = kind, bot_key, seed, policy
        dist = dict(distribution or {"NORMAL": 1.0})
        tot = sum(max(0.0, v) for v in dist.values()) or 1.0
        self.cum: list[tuple[str, float]] = []
        acc = 0.0
        for lvl in ACTIONS:
            acc += max(0.0, dist.get(lvl, 0.0)) / tot
            self.cum.append((lvl, acc))
        self.attack_strong_share = attack_strong_share
        self.decisions = 0
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> GateVerdict:
        meta = getattr(sig, "meta", None) or {}
        ts = int(g["ts"])
        if self.kind == "TAKE":
            chosen = "NORMAL"
        else:
            u = _u(self.seed, self.bot_key, ts, sig.side)
            chosen = next((lvl for lvl, c in self.cum if u < c), "NORMAL")
        e2c = meta.get("edge_to_cost")
        level = chosen
        if chosen == "ATTACK" and not (isinstance(e2c, (int, float)) and e2c >= self.policy.attack_min_edge_to_cost):
            level = "NORMAL"
        mult = self.policy.multiplier(level)
        if level == "ATTACK" and _u("strong", self.seed, self.bot_key, ts) < self.attack_strong_share:
            mult = self.policy.attack_strong_multiplier
        did = f"{self.kind.lower()}{self.seed}:{self.bot_key}:{ts}:{sig.side}"
        self.decisions += 1
        self.rows.append({"id": did, "ts": ts, "side": sig.side, "p_win": None, "chosen": chosen, "level": level,
                          "mult": mult, "e2c": e2c, "signal_quality": meta.get("signal_quality"),
                          "source": self.kind.lower(), "latency_ms": None, "error": None, "cost_usd": 0.0})
        return GateVerdict(mult, {"decision_id": did, "jev_action": ACTION_OF[level], "jev_level": level,
                                  "jev_multiplier": mult, "jev_source": self.kind.lower()})


def v2_fingerprints() -> dict[str, str]:
    return {"prompt": QUESTIONS_V2_FINGERPRINT, "policy": POLICY_V2.fingerprint(),
            "state_version": STATE_VERSION_V2, "prompt_version": PROMPT_VERSION_V2}


def finite_or_none(x: Any) -> Any:
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None
