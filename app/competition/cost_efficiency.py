"""COST EFFICIENCY: is a bot's edge big enough to pay for its own trading?

Two bots can both lose money for completely different reasons:

    GROSS_NEGATIVE   the trading logic loses before a single fee -- costs only make it worse
    FEE_DESTROYED    the logic makes money at decision prices, and fees + slippage eat all of it
    MARGINAL         still net positive, but costs consume more than half of the gross edge
    HEALTHY          net positive with costs well inside the gross edge
    NO_TRADES        nothing to judge

Everything is derived from stored metrics and the stored trade ledger, so it can be computed for any
finished run -- including immutable ones -- without re-running or rewriting it. These labels are
diagnostics. They are not qualification gates unless a gate is explicitly configured to use them.

Definitions (all USDT unless noted):

    gross edge          PnL at DECISION prices, before slippage, fees or funding
    costs               fees + slippage + funding actually paid (funding received is not a cost)
    cost-to-edge        costs / gross edge; > 1 means costs exceed the edge
    round-trip cost     costs per trade, also in bps of the average position notional
    break-even win rate the win rate at which this bot's average net win and net loss break even
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

CLASSES = ("NO_TRADES", "GROSS_NEGATIVE", "FEE_DESTROYED", "MARGINAL", "HEALTHY")


@dataclass(frozen=True)
class CostEfficiencyConfig:
    healthy_max_cost_share: float = 0.50     # net > 0 and costs <= 50% of gross edge -> HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_COST = CostEfficiencyConfig()


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cost_efficiency(m: Mapping[str, Any] | None, ledger: Sequence[Mapping[str, Any]] | None = None,
                    window_days: float | None = None,
                    cfg: CostEfficiencyConfig = DEFAULT_COST) -> dict[str, Any]:
    m = m or {}
    n = int(m.get("trades") or 0)
    gross = _f(m.get("gross_pnl"))
    fees, slip, funding = _f(m.get("fees_paid")), _f(m.get("slippage_cost")), _f(m.get("funding_paid"))
    net = _f(m.get("net_profit"))
    funding_cost = max(0.0, -funding)
    costs = fees + slip + funding_cost
    notional = _f(m.get("total_notional_traded"))
    entry_notional = notional / 2.0 if notional > 0 else 0.0
    out: dict[str, Any] = {
        "trades": n, "gross_edge": gross, "fees": fees, "slippage": slip, "funding": funding,
        "total_costs": costs, "net": net,
        "trades_per_day": (n / window_days) if window_days else None,
        "avg_holding_minutes": (_f(m.get("average_holding_ms")) / 60000.0) if n else None,
        "turnover": m.get("turnover"),
    }
    if n == 0:
        out.update({"class": "NO_TRADES", "cost_to_edge": None, "avg_gross_edge_per_trade": None,
                    "avg_round_trip_cost": None, "avg_gross_edge_bps": None, "avg_round_trip_cost_bps": None,
                    "fees_to_gross_winning": None, "break_even_win_rate": None, "win_rate": None,
                    "gross_break_even_win_rate": None, "gross_win_rate": None})
        return out
    out["avg_gross_edge_per_trade"] = gross / n
    out["avg_round_trip_cost"] = costs / n
    if entry_notional > 0:
        per_trade_notional = entry_notional / n
        out["avg_position_notional"] = per_trade_notional
        out["avg_gross_edge_bps"] = gross / entry_notional * 1e4
        out["avg_round_trip_cost_bps"] = costs / entry_notional * 1e4
    else:
        out["avg_gross_edge_bps"] = out["avg_round_trip_cost_bps"] = None
    out["cost_to_edge"] = (costs / gross) if gross > 0 else None
    avg_win, avg_loss = _f(m.get("average_win")), _f(m.get("average_loss"))
    out["win_rate"] = m.get("win_rate")
    out["break_even_win_rate"] = (-avg_loss / (avg_win - avg_loss)) if (avg_win > 0 and avg_loss < 0) else None
    # From the ledger: payoffs BEFORE fees (slippage is already inside a fill-price PnL)
    pnls = [_f(t.get("pnl")) for t in ledger or []]
    if pnls:
        wins = [p for p in pnls if p > 0]
        losses = [-p for p in pnls if p <= 0]
        fee_per_trade = (fees + funding_cost) / n
        out["gross_win_rate"] = len(wins) / len(pnls)
        out["fees_to_gross_winning"] = (fees / sum(wins)) if wins else None
        if wins and losses:
            w, l = statistics.fmean(wins), statistics.fmean(losses)
            out["gross_break_even_win_rate"] = min(1.0, (l + fee_per_trade) / (w + l))
        else:
            out["gross_break_even_win_rate"] = None
    else:
        out["gross_win_rate"] = out["gross_break_even_win_rate"] = None
        gp = _f(m.get("gross_profit"))
        out["fees_to_gross_winning"] = (fees / gp) if gp > 0 else None
    if gross <= 0:
        cls = "GROSS_NEGATIVE"
    elif net <= 0:
        cls = "FEE_DESTROYED"
    elif costs > cfg.healthy_max_cost_share * gross:
        cls = "MARGINAL"
    else:
        cls = "HEALTHY"
    out["class"] = cls
    return out


# ---- JEV_ELIGIBLE_CONTROL ----------------------------------------------------------------------------

@dataclass(frozen=True)
class JevEligibilityConfig:
    """A base bot worth asking Jev about. Research defaults; every number is configurable.

    Path A (a promising edge): enough trades, positive gross expectancy, net expectancy not severely
    negative, no liquidation, and costs well inside the gross edge. Path B (a proven discovery result):
    positive net after all costs on enough trades, with no liquidation.
    """
    min_trades: int = 20
    min_gross_expectancy_usdt: float = 0.0
    min_net_expectancy_r: float = -0.10
    max_liquidations: int = 0
    max_cost_to_edge: float = 0.75
    allow_positive_net: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_ELIGIBILITY = JevEligibilityConfig()


def jev_eligible(m: Mapping[str, Any] | None, ce: Mapping[str, Any] | None = None,
                 cfg: JevEligibilityConfig = DEFAULT_ELIGIBILITY) -> tuple[bool, list[str]]:
    """(eligible, reasons it is not). Never true for a bot without trades or with a liquidation."""
    m = m or {}
    ce = ce or cost_efficiency(m)
    n = int(m.get("trades") or 0)
    liq = int(m.get("liquidation_count") or 0)
    reasons: list[str] = []
    if n < cfg.min_trades:
        reasons.append(f"{n} trades < {cfg.min_trades}")
    if liq > cfg.max_liquidations:
        reasons.append(f"{liq} liquidation(s)")
    gross_exp = (ce.get("avg_gross_edge_per_trade") or 0.0) if n else 0.0
    if gross_exp <= cfg.min_gross_expectancy_usdt:
        reasons.append(f"gross expectancy {gross_exp:+.4f} USDT/trade <= {cfg.min_gross_expectancy_usdt}")
    exp_r = _f(m.get("expectancy_r"))
    if exp_r < cfg.min_net_expectancy_r:
        reasons.append(f"net expectancy {exp_r:+.3f}R < {cfg.min_net_expectancy_r}R")
    c2e = ce.get("cost_to_edge")
    if c2e is None or c2e > cfg.max_cost_to_edge:
        reasons.append("costs exceed the gross edge" if c2e is None
                       else f"cost-to-edge {c2e:.2f} > {cfg.max_cost_to_edge}")
    if not reasons:
        return True, []
    if cfg.allow_positive_net and n >= cfg.min_trades and liq <= cfg.max_liquidations and _f(m.get("net_profit")) > 0:
        return True, []
    return False, reasons
