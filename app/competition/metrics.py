"""Competitor metrics computed from the fill ledger.

The ledger is authoritative. Nothing here recalculates a fill; it reads what
`Portfolio.open_position` / `close_position` / `apply_funding` actually produced, which is the same
code path the live lab runs. That is what makes a competition result comparable to a paper result.

GROSS vs NET is the point of this module:

    gross_pnl   price movement only, before any cost
    fees        taker/maker commission on every fill, entry and exit
    slippage    (fill price - decision price) * qty, signed as a cost
    funding     perpetual funding settled while the position was open
    net_pnl     gross - fees - slippage + funding

`net_pnl` is the only number the leaderboard is allowed to call PnL.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.core.portfolio import ClosedTrade
from app.core.types import Fill

DAY_MS = 86_400_000


def slippage_usdt(f: Fill) -> float:
    """Cost of the gap between the price the decision was made at and the price that filled.

    Always >= 0: a BUY filling above its reference and a SELL filling below it are both costs, and
    the engine's slippage model never fills favourably. Kept as a signed subtraction anyway so a
    future price-improvement model does not silently become a bonus.
    """
    if f.kind == "funding" or not f.ref_price or f.qty <= 0:
        return 0.0
    signed = (f.price - f.ref_price) if f.side == "BUY" else (f.ref_price - f.price)
    return signed * f.qty


def _streaks(values: Sequence[float]) -> tuple[int, int]:
    best_win = best_loss = cur_win = cur_loss = 0
    for v in values:
        if v > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        best_win = max(best_win, cur_win)
        best_loss = max(best_loss, cur_loss)
    return best_win, best_loss


@dataclass
class CompetitorMetrics:
    strategy_id: str
    version: str = ""

    starting_equity: float = 0.0
    ending_equity: float = 0.0
    net_return_pct: float = 0.0

    gross_profit: float = 0.0          # sum of winning trades, after their own costs (for PF)
    gross_loss: float = 0.0            # sum of losing trades, after their own costs (for PF)
    gross_pnl: float = 0.0             # PnL at DECISION prices, before any cost at all
    fees_paid: float = 0.0
    funding_paid: float = 0.0          # signed: negative when the bot paid
    slippage_cost: float = 0.0
    latency_cost: float = 0.0          # decomposition of slippage_cost, not additional to it
    spread_cost: float = 0.0
    impact_cost: float = 0.0
    net_profit: float = 0.0

    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    expectancy_usdt: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    average_r: float = 0.0
    median_r: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0

    max_drawdown_pct: float = 0.0
    max_drawdown_usdt: float = 0.0
    drawdown_duration_ms: int = 0
    recovery_factor: float = 0.0
    sharpe_like: float = 0.0
    sortino_like: float = 0.0

    turnover: float = 0.0
    total_notional_traded: float = 0.0
    average_holding_ms: int = 0
    maker_percentage: float = 0.0
    taker_percentage: float = 0.0
    max_leverage_used: int = 0          # the configured ceiling stamped on fills
    configured_max_leverage: int = 0
    avg_effective_leverage: float = 0.0     # mean while holding a position
    max_effective_leverage: float = 0.0
    time_weighted_leverage: float = 0.0     # mean across all bars; flat counts as zero
    margin_utilization_avg: float = 0.0
    margin_utilization_max: float = 0.0
    time_in_market_pct: float = 0.0

    fee_to_gross_profit_ratio: float = 0.0
    slippage_to_gross_profit_ratio: float = 0.0
    largest_trade_profit_contribution_pct: float = 0.0
    liquidation_count: int = 0

    by_symbol: dict[str, dict[str, float]] = field(default_factory=dict)
    by_exit_kind: dict[str, int] = field(default_factory=dict)
    rejects: dict[str, int] = field(default_factory=dict)
    halted: bool = False

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def compute(strategy_id: str, trades: Sequence[ClosedTrade], fills: Sequence[Fill],
            equity: Sequence[tuple[int, float]], starting_equity: float,
            rejects: dict[str, int] | None = None, halted: bool = False,
            version: str = "", leverage: Any = None) -> CompetitorMetrics:
    """`leverage` is the ReplayResult (or anything exposing the sampled leverage properties), so
    the metrics can report what the book actually ran at rather than only its ceiling."""
    m = CompetitorMetrics(strategy_id=strategy_id, version=version,
                          starting_equity=starting_equity, rejects=dict(rejects or {}), halted=halted)
    m.ending_equity = equity[-1][1] if equity else starting_equity
    if starting_equity > 0:
        m.net_return_pct = m.ending_equity / starting_equity - 1.0

    # -- costs, straight off the ledger --------------------------------------------------
    for f in fills:
        if f.kind == "funding":
            m.funding_paid += f.realized_pnl
            continue
        m.fees_paid += f.fee
        m.slippage_cost += slippage_usdt(f)
        m.latency_cost += float(f.meta.get("latency_cost") or 0.0)
        m.spread_cost += float(f.meta.get("spread_cost") or 0.0)
        m.impact_cost += float(f.meta.get("impact_cost") or 0.0)
        m.total_notional_traded += f.qty * f.price
        m.max_leverage_used = max(m.max_leverage_used, int(f.leverage or 0))
        if f.kind == "liq":
            m.liquidation_count += 1
    trade_fills = [f for f in fills if f.kind != "funding" and f.qty > 0]
    if trade_fills:
        makers = sum(1 for f in trade_fills if f.meta.get("maker"))
        m.maker_percentage = makers / len(trade_fills)
        m.taker_percentage = 1.0 - m.maker_percentage
    if starting_equity > 0:
        m.turnover = m.total_notional_traded / starting_equity

    # -- trade statistics ------------------------------------------------------------------
    nets = [t.net for t in trades]
    rs = [t.r_multiple for t in trades]
    m.trades = len(trades)
    # `ClosedTrade.pnl` is computed from FILLED prices, so slippage is already inside it. Adding the
    # slippage back out gives PnL at the decision prices -- the genuinely pre-cost number, which is
    # what "gross" has to mean for `net = gross - fees - slippage + funding` to reconcile with equity.
    m.gross_pnl = sum(t.pnl for t in trades) + m.slippage_cost
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    m.gross_profit = sum(wins)
    m.gross_loss = sum(losses)
    # net_profit is derived from equity, not from summing trades: it is the only figure that
    # necessarily includes funding on positions that were still open across a settlement.
    m.net_profit = m.ending_equity - starting_equity
    if trades:
        m.wins, m.losses = len(wins), len(losses)
        m.win_rate = m.wins / m.trades
        m.average_win = st.fmean(wins) if wins else 0.0
        m.average_loss = st.fmean(losses) if losses else 0.0
        m.expectancy_usdt = st.fmean(nets)
        m.expectancy_r = st.fmean(rs)
        m.average_r = m.expectancy_r
        m.median_r = st.median(rs)
        m.best_trade = max(nets)
        m.worst_trade = min(nets)
        m.profit_factor = (m.gross_profit / -m.gross_loss) if m.gross_loss < 0 else math.inf
        m.longest_win_streak, m.longest_loss_streak = _streaks(nets)
        m.average_holding_ms = int(st.fmean([t.exit_ts - t.entry_ts for t in trades]))
        if m.gross_profit > 0:
            m.largest_trade_profit_contribution_pct = max(wins) / m.gross_profit
            m.fee_to_gross_profit_ratio = m.fees_paid / m.gross_profit
            m.slippage_to_gross_profit_ratio = m.slippage_cost / m.gross_profit
        for t in trades:
            b = m.by_symbol.setdefault(t.symbol, {"trades": 0, "net": 0.0, "r": 0.0})
            b["trades"] += 1
            b["net"] += t.net
            b["r"] += t.r_multiple
            m.by_exit_kind[t.exit_kind] = m.by_exit_kind.get(t.exit_kind, 0) + 1

    # -- equity-curve risk ------------------------------------------------------------------
    m.max_drawdown_pct, m.max_drawdown_usdt, m.drawdown_duration_ms = _drawdown(equity, starting_equity)
    if m.max_drawdown_usdt > 0:
        m.recovery_factor = m.net_profit / m.max_drawdown_usdt
    m.sharpe_like, m.sortino_like = _risk_ratios(equity)
    if leverage is not None:
        m.avg_effective_leverage = getattr(leverage, "avg_effective_leverage", 0.0)
        m.max_effective_leverage = getattr(leverage, "lev_max", 0.0)
        m.time_weighted_leverage = getattr(leverage, "time_weighted_leverage", 0.0)
        m.margin_utilization_avg = getattr(leverage, "margin_utilization_avg", 0.0)
        m.margin_utilization_max = getattr(leverage, "margin_max", 0.0)
        seen = getattr(leverage, "bars_seen", 0) or 0
        m.time_in_market_pct = (getattr(leverage, "bars_in_market", 0) / seen) if seen else 0.0
    return m


def _drawdown(equity: Sequence[tuple[int, float]], starting: float) -> tuple[float, float, int]:
    peak, peak_ts = starting, equity[0][0] if equity else 0
    worst_pct, worst_usdt, worst_dur = 0.0, 0.0, 0
    for ts, eq in equity:
        if eq >= peak:
            peak, peak_ts = eq, ts
            continue
        worst_usdt = max(worst_usdt, peak - eq)
        if peak > 0:
            worst_pct = max(worst_pct, 1.0 - eq / peak)
        worst_dur = max(worst_dur, ts - peak_ts)
    return worst_pct, worst_usdt, worst_dur


def _risk_ratios(equity: Sequence[tuple[int, float]]) -> tuple[float, float]:
    """Daily-return Sharpe/Sortino analogues. Not annualised and not risk-free-adjusted --
    named `_like` so nobody quotes them as the real thing."""
    if len(equity) < 3:
        return 0.0, 0.0
    daily: dict[int, float] = {}
    for ts, eq in equity:
        daily[ts // DAY_MS] = eq
    series = [v for _, v in sorted(daily.items())]
    if len(series) < 3:
        return 0.0, 0.0
    rets = [(b / a - 1.0) for a, b in zip(series, series[1:]) if a > 0]
    if len(rets) < 2:
        return 0.0, 0.0
    mean = st.fmean(rets)
    sd = st.pstdev(rets)
    downs = [r for r in rets if r < 0]
    dsd = st.pstdev(downs) if len(downs) > 1 else 0.0
    return (mean / sd if sd > 0 else 0.0), (mean / dsd if dsd > 0 else 0.0)
