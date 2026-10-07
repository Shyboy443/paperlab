"""TRADEABILITY SCORE: which coins can a 20 USDT intraday book trade at all, and how cheaply?

Market-structure facts only -- never a strategy's PnL, never a backtest result. Everything is measured on
the SCORING MONTH (the 30 days before a window's first trading day), so a window's universe is decided
with data that existed before the window began:

    liquidity     median daily turnover on the venue (Bybit linear, USDT)
    spread        half of one tick in bps of the median close (the top of book on these perps is one tick)
    volatility    median 30m high-low range in bps (intraday movement available to a trade)
    cost ratio    round-trip taker cost (2 x (taker fee + half spread)) / median 30m range: how much of a
                  typical 30m move the costs eat
    tick size     one tick in bps of price (part of the spread; also the resolution of stops)
    order limits  can a 20 USDT book size a TAKE position (1% risk) at a typical stop (one median 30m range,
                  clamped 0.6%..2.5%): notional >= the venue minimum (value and quantity), quantity step
                  <= 25% of that notional, leverage needed <= the 20x cap
    availability  the 1m price tape exists for every month of the window and its warm-up month, and the
                  contract was listed >= 60 days before the window

GATES (all must hold) then SCORE = mean of within-pool percentile ranks of turnover (higher better), cost
ratio (lower better) and step granularity (lower better). UNIVERSE = the `universe_size` best scores.
The RULE is frozen with V4 (docs/V4_PROTOCOL.md); the coin list is recomputed per window by the rule.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TradeabilityRule:
    pool_size: int = 40                   # candidates: the most-traded contracts in the scoring month
    min_listing_days: int = 60
    min_turnover_usdt: float = 20_000_000.0
    max_half_spread_bps: float = 2.0
    min_range_30m_bps: float = 30.0
    max_cost_ratio: float = 0.35
    taker_fee_bps: float = 5.5            # Bybit linear taker
    balance: float = 20.0
    take_risk_pct: float = 0.010
    min_stop_pct: float = 0.006
    max_stop_pct: float = 0.025
    max_step_share: float = 0.25
    max_leverage: int = 20
    universe_size: int = 10

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def _median(xs: Sequence[float]) -> float | None:
    v = [float(x) for x in xs if x is not None]
    return statistics.median(v) if v else None


def measure(daily: Sequence[Sequence[Any]], bars_30m: Sequence[Sequence[Any]], filters: Mapping[str, Any],
            rule: TradeabilityRule) -> dict[str, Any]:
    """One coin's facts from Bybit klines ([start, open, high, low, close, volume, turnover]) and its
    instrument filters (tick, step, min_qty, min_notional, max_leverage)."""
    turnover = _median([float(k[6]) for k in daily])
    close = _median([float(k[4]) for k in daily])
    ranges = [(float(k[2]) - float(k[3])) / float(k[1]) * 1e4 for k in bars_30m if float(k[1]) > 0]
    range_30m = _median(ranges)
    tick, step = float(filters["tick"]), float(filters["step"])
    min_qty, min_value = float(filters["min_qty"]), float(filters.get("min_notional") or 5.0)
    out: dict[str, Any] = {"turnover_usdt": turnover, "close": close, "range_30m_bps": range_30m,
                           "days": len(daily), "bars_30m": len(bars_30m)}
    if not close or not range_30m:
        return {**out, "measurable": False}
    half_spread = tick / 2.0 / close * 1e4
    rt_cost = 2.0 * (rule.taker_fee_bps + half_spread)
    stop = min(max(range_30m / 1e4, rule.min_stop_pct), rule.max_stop_pct)
    risk_usd = rule.balance * rule.take_risk_pct
    notional = risk_usd / stop
    min_order = max(min_value, min_qty * close)
    step_value = step * close
    lev_cap = min(rule.max_leverage, int(float(filters.get("max_leverage") or rule.max_leverage)))
    out.update({"measurable": True, "tick_bps": round(tick / close * 1e4, 4), "half_spread_bps": round(half_spread, 4),
                "round_trip_cost_bps": round(rt_cost, 3), "cost_ratio": round(rt_cost / range_30m, 4),
                "typical_stop_pct": round(stop, 5), "take_notional_usdt": round(notional, 3),
                "min_order_usdt": round(min_order, 4), "step_value_usdt": round(step_value, 6),
                "step_share": round(step_value / notional, 5), "leverage_needed": round(notional / rule.balance, 3),
                "leverage_cap": lev_cap})
    return out


def gates(m: Mapping[str, Any], rule: TradeabilityRule) -> list[str]:
    """The reasons a coin is NOT tradeable (empty = eligible)."""
    if not m.get("available", False):
        return ["NO_PRICE_TAPE"]
    if not m.get("listed_long_enough", False):
        return ["LISTED_TOO_RECENTLY"]
    if not m.get("measurable"):
        return ["NOT_MEASURABLE"]
    out = []
    if (m["turnover_usdt"] or 0) < rule.min_turnover_usdt:
        out.append("LOW_LIQUIDITY")
    if m["half_spread_bps"] > rule.max_half_spread_bps:
        out.append("WIDE_SPREAD")
    if m["range_30m_bps"] < rule.min_range_30m_bps:
        out.append("TOO_QUIET")
    if m["cost_ratio"] > rule.max_cost_ratio:
        out.append("COSTS_EAT_THE_MOVE")
    if m["take_notional_usdt"] < m["min_order_usdt"]:
        out.append("BELOW_EXCHANGE_MINIMUM_AT_20_USDT")
    if m["step_share"] > rule.max_step_share:
        out.append("QUANTITY_STEP_TOO_COARSE")
    if m["leverage_needed"] > m["leverage_cap"]:
        out.append("NEEDS_TOO_MUCH_LEVERAGE")
    return out


def _pct_rank(values: Mapping[str, float], higher_better: bool) -> dict[str, float]:
    items = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(items)
    if n <= 1:
        return {k: 1.0 for k, _ in items}
    rank = {k: i / (n - 1) for i, (k, _) in enumerate(items)}
    return rank if higher_better else {k: 1.0 - r for k, r in rank.items()}


def score(measured: Mapping[str, Mapping[str, Any]], rule: TradeabilityRule) -> dict[str, Any]:
    """Gate every candidate, rank the eligible ones, pick the universe. Deterministic: ties -> turnover,
    then symbol."""
    rows = {s: {**m, "fail": gates(m, rule)} for s, m in measured.items()}
    ok = {s: r for s, r in rows.items() if not r["fail"]}
    comp = {"liquidity": _pct_rank({s: r["turnover_usdt"] for s, r in ok.items()}, True),
            "cost": _pct_rank({s: r["cost_ratio"] for s, r in ok.items()}, False),
            "granularity": _pct_rank({s: r["step_share"] for s, r in ok.items()}, False)}
    for s, r in ok.items():
        parts = {k: round(v[s], 4) for k, v in comp.items()}
        r["components"] = parts
        r["score"] = round(sum(parts.values()) / len(parts), 4)
    ranked = sorted(ok, key=lambda s: (-ok[s]["score"], -(ok[s]["turnover_usdt"] or 0), s))
    universe = ranked[:rule.universe_size]
    return {"rule": rule.to_dict(), "rule_fingerprint": rule.fingerprint(), "ranked": ranked, "universe": universe,
            "coins": rows, "eligible": len(ok), "candidates": len(rows),
            "failures": {k: sum(1 for r in rows.values() if k in r["fail"])
                         for k in sorted({f for r in rows.values() for f in r["fail"]})}}


def pool(turnovers: Mapping[str, float | None], rule: TradeabilityRule) -> list[str]:
    """The `pool_size` contracts with the highest median daily turnover in the scoring month."""
    have = [(s, t) for s, t in turnovers.items() if t]
    return [s for s, _ in sorted(have, key=lambda kv: (-kv[1], kv[0]))[:rule.pool_size]]


def split_by_speed(universe: Sequence[str], per_tf: Mapping[str, int]) -> dict[str, list[str]]:
    """Coins per trigger timeframe: faster timeframes pay the costs more often, so they get the best-scored
    (cheapest, most liquid) coins; every timeframe takes a prefix of the same ranking."""
    return {tf: list(universe[:n]) for tf, n in per_tf.items()}
