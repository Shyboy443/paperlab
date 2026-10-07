"""V5 UNIVERSE: which Bybit linear perpetuals an hourly / daily 20 USDT book can trade (docs/V5_PROTOCOL.md §3).

Market structure and data availability only -- never a strategy's PnL, never a backtest result. Measured on the 30
days before a window's first trading day, so a window's coins are decided with data that existed before it began:

    liquidity      median daily turnover (Bybit linear, USDT)
    spread         half of one tick in bps of the median close
    volatility     median 4h high-low range in bps (the move an hourly / daily trade can capture)
    cost ratio     round-trip taker cost (2 x (taker fee + half spread)) / median 4h range
    order limits   the coin must be LEGALLY RISK-SIZABLE at 20 USDT: a TAKE order (1% at risk) at the tightest V5
                   stop (1%) clears the exchange minimum value and quantity, with a quantity step <= 25% of the order
                   and <= the 20x leverage ceiling (BTC / ETH / SOL cannot be). Whether a TYPICAL stop (1.5 x the
                   median 4h range) is legal is reported, not gated: such trades are SKIPPED in the replay and
                   counted MIN_NOTIONAL_LIMITED, and the 50 / 100 USDT capacity twins show what size would change
                   (docs/V5_PROTOCOL.md, Amendment 0)
    data           Bybit 1m tape, funding settlements and 1h open interest all exist from the warm-up start, and the
                   contract was listed >= 90 days before the window

The V4 tradeability rule (app/competition/tradeability.py) is frozen with V4 and not changed; V5 reuses only its
ranking helpers.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.competition.tradeability import _pct_rank


@dataclass(frozen=True)
class UniverseRuleV5:
    pool_size: int = 40
    min_listing_days: int = 90
    min_turnover_usdt: float = 20_000_000.0
    max_half_spread_bps: float = 2.0
    min_range_4h_bps: float = 100.0
    max_cost_ratio: float = 0.15
    taker_fee_bps: float = 5.5            # BYBIT_LINEAR taker (app/execution/config.py)
    balance: float = 20.0
    take_risk_pct: float = 0.010
    stop_range_mult: float = 1.5
    min_stop_pct: float = 0.01
    max_stop_pct: float = 0.06
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


def measure(daily: Sequence[Sequence[Any]], bars_4h: Sequence[Sequence[Any]], filters: Mapping[str, Any],
            rule: UniverseRuleV5) -> dict[str, Any]:
    """One coin's facts from Bybit klines ([start, open, high, low, close, volume, turnover]) and filters."""
    turnover = _median([float(k[6]) for k in daily])
    close = _median([float(k[4]) for k in daily])
    ranges = [(float(k[2]) - float(k[3])) / float(k[1]) * 1e4 for k in bars_4h if float(k[1]) > 0]
    range_4h = _median(ranges)
    out: dict[str, Any] = {"turnover_usdt": turnover, "close": close, "range_4h_bps": range_4h, "days": len(daily),
                           "bars_4h": len(bars_4h)}
    if not close or not range_4h:
        return {**out, "measurable": False}
    tick, step = float(filters["tick"]), float(filters["step"])
    min_qty, min_value = float(filters["min_qty"]), float(filters.get("min_notional") or 5.0)
    half = tick / 2.0 / close * 1e4
    rt = 2.0 * (rule.taker_fee_bps + half)
    stop = min(max(rule.stop_range_mult * range_4h / 1e4, rule.min_stop_pct), rule.max_stop_pct)
    notional = rule.balance * rule.take_risk_pct / rule.min_stop_pct      # the largest TAKE order the rule allows
    typical = rule.balance * rule.take_risk_pct / stop
    min_order = max(min_value, min_qty * close)
    lev_cap = min(rule.max_leverage, int(float(filters.get("max_leverage") or rule.max_leverage)))
    out.update({"measurable": True, "tick_bps": round(tick / close * 1e4, 4), "half_spread_bps": round(half, 4),
                "round_trip_cost_bps": round(rt, 3), "cost_ratio": round(rt / range_4h, 4),
                "typical_stop_pct": round(stop, 5), "take_notional_usdt": round(notional, 3),
                "typical_take_notional_usdt": round(typical, 3), "typical_stop_legal_at_20": typical >= min_order,
                "min_order_usdt": round(min_order, 4), "step_share": round(step * close / notional, 5),
                "leverage_needed": round(notional / rule.balance, 3), "leverage_cap": lev_cap})
    return out


def gates(m: Mapping[str, Any], rule: UniverseRuleV5) -> list[str]:
    """The reasons a coin is NOT eligible (empty = eligible)."""
    if not m.get("listed_long_enough", False):
        return ["LISTED_TOO_RECENTLY"]
    missing = [k for k in ("tape", "funding", "open_interest") if not (m.get("data") or {}).get(k)]
    if missing:
        return ["NO_" + k.upper() + "_HISTORY" for k in missing]
    if not m.get("measurable"):
        return ["NOT_MEASURABLE"]
    out = []
    if (m["turnover_usdt"] or 0) < rule.min_turnover_usdt:
        out.append("LOW_LIQUIDITY")
    if m["half_spread_bps"] > rule.max_half_spread_bps:
        out.append("WIDE_SPREAD")
    if m["range_4h_bps"] < rule.min_range_4h_bps:
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


def score(measured: Mapping[str, Mapping[str, Any]], rule: UniverseRuleV5) -> dict[str, Any]:
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
    return {"rule": rule.to_dict(), "rule_fingerprint": rule.fingerprint(), "ranked": ranked,
            "universe": ranked[:rule.universe_size], "coins": rows, "eligible": len(ok), "candidates": len(rows),
            "failures": {k: sum(1 for r in rows.values() if k in r["fail"])
                         for k in sorted({f for r in rows.values() for f in r["fail"]})}}
