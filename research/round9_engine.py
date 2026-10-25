"""Round 9 — cross-venue perpetual basis.

Different input class from rounds 1..8: not price patterns on a single
venue's candles, but the spread between two venues' mark prices for the
*same* perpetual contract. Costs are 2 legs × 2 sides = 4 sides per
round trip, defaulting to std_taker_alt (0.1% fee + 0.1% slip per side)
= 0.8% per round trip.

Strategy:
  - Each minute, align OKX mark and Binance mark (or Bybit mark if
    Binance is geo-blocked) for BTC-USDT.
  - basis_bps = 10000 * (mark_A - mark_B) / mark_B
  - Enter when |basis_bps| > entry_thresh (default 25 bps)
    * short the rich venue, long the cheap venue
  - Exit when |basis_bps| < exit_thresh (default 5 bps) OR after max_hold
    minutes (default 240)
  - No look-ahead: signals only use data from bars that have fully closed.

Honest limitations:
  - We can only observe marks, not the *executed* fills. A backtest that
    uses marks as fills is optimistic by the bid-ask spread of each
    venue, which is exactly the slippage the cost tier models. So the
    cost tier does the work of approximating execution.
  - Funding is not modeled (the perp leg *pays/receives* funding while
    open). Round 3 measured funding as a positive-gross ~5%/yr edge —
    including it would only help this strategy. Excluded to be
    conservative.
  - Single-venue timeouts, custody, withdrawal friction, and queue
    priority on either leg are NOT modeled.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from .costs import round_trip_cost, DEFAULT_TIER
from .metrics import avg_daily_from_equity, sharpe_from_equity, max_drawdown
from .stop_rule import StopCriteria, RoundMetrics, evaluate
from .walkforward import walkforward_windows, split70_30

@dataclass(frozen=True)
class BasisParams:
    entry_bps: float = 25.0
    exit_bps: float = 5.0
    max_hold_min: int = 240
    notional_usd: float = 10_000.0
    cost_tier: str = "std_taker_alt"

def _align(okx: list[dict], other: list[dict]) -> list[tuple[int, float, float]]:
    """Return [(ts, okx_mark, other_mark), ...] on the intersection of ts."""
    other_by_ts = {r["ts"]: r["mark"] for r in other}
    out = []
    for r in okx:
        ts = r["ts"]
        if ts in other_by_ts:
            out.append((ts, r["mark"], other_by_ts[ts]))
    return out

def _simulate(aligned: list[tuple[int, float, float]], p: BasisParams) -> tuple[list[tuple[int, float]], list[tuple[int, int, float]], int]:
    """Run the basis engine on the aligned minute marks. Returns
    (equity_curve, trades[(entry_ts, exit_ts, net_usd)], n_trades)."""
    eq = p.notional_usd
    eq_curve = []
    trades = []
    pos = None  # (entry_ts, side, leg_A_price, leg_B_price)
    rt_cost = round_trip_cost(p.cost_tier, legs=2)  # 2 legs per round trip
    entry_thresh = p.entry_bps / 10_000.0
    exit_thresh = p.exit_bps / 10_000.0
    n_trades = 0
    for ts, mark_a, mark_b in aligned:
        if mark_b == 0:
            eq_curve.append((ts, eq)); continue
        basis = (mark_a - mark_b) / mark_b
        if pos is None:
            if abs(basis) > entry_thresh:
                side = -1 if basis > 0 else +1
                pos = (ts, side, mark_a, mark_b)
        else:
            _, side, ea, eb = pos
            # Mark-to-market the basis leg's PnL on the open.
            current = (mark_a - mark_b)
            entry = (ea - eb)
            pnl_per_unit = (entry - current) * side
            mtm = pnl_per_unit * (p.notional_usd / eb)  # notional sized in venue B
            if (ts - pos[0]) >= p.max_hold_min * 60_000 or abs(basis) < exit_thresh:
                # Close: pay round-trip cost on notional, realize pnl.
                net = mtm - rt_cost * p.notional_usd
                eq += net
                trades.append((pos[0], ts, net))
                pos = None
                n_trades += 1
            else:
                eq_curve.append((ts, eq + mtm))
                continue
        eq_curve.append((ts, eq))
    # Force-close at the end.
    if pos is not None and aligned:
        ts, mark_a, mark_b = aligned[-1]
        _, side, ea, eb = pos
        current = (mark_a - mark_b)
        entry = (ea - eb)
        pnl_per_unit = (entry - current) * side
        mtm = pnl_per_unit * (p.notional_usd / eb)
        net = mtm - rt_cost * p.notional_usd
        eq += net
        trades.append((pos[0], ts, net))
        n_trades += 1
    return eq_curve, trades, n_trades

def _summarize(eq_curve: list[tuple[int, float]], trades: list, initial: float,
                n_windows: int) -> RoundMetrics:
    if not eq_curve:
        return RoundMetrics(0, 0, 0, 1.0, 0, n_windows)
    avg_d = avg_daily_from_equity(eq_curve, initial)
    sh = sharpe_from_equity(eq_curve, bars_per_day=24*60)  # 1-min bars
    mdd = max_drawdown(eq_curve)
    min_eq = min(e for _, e in eq_curve) / initial
    return RoundMetrics(avg_d, sh, mdd, min_eq, len(trades), n_windows)

def run(okx_marks: list[dict], other_marks: list[dict], p: BasisParams,
        mode: str = "walkforward") -> tuple[RoundMetrics, list]:
    aligned = _align(okx_marks, other_marks)
    if mode == "walkforward":
        windows = walkforward_windows(len(aligned))
        chain: list[tuple[int, float]] = []
        all_trades = []
        for tr_s, tr_e, te_s, te_e in windows:
            sub = aligned[te_s:te_e]
            if not sub: continue
            eq_curve, trades, _ = _simulate(sub, p)
            if chain and eq_curve:
                # Chain the test windows: scale the next window's equity to
                # start at the last point of the previous chain.
                start_eq = chain[-1][1]
                end_eq = eq_curve[-1][1] if eq_curve else start_eq
                scale = start_eq / p.notional_usd if p.notional_usd > 0 else 1
                chain.extend((ts, e * scale) for ts, e in eq_curve)
            elif eq_curve:
                chain.extend(eq_curve)
            all_trades.extend(trades)
        m = _summarize(chain, all_trades, p.notional_usd, len(windows))
        return m, all_trades
    elif mode == "split":
        tr_s, tr_e, te_s, te_e = split70_30(len(aligned))
        sub = aligned[te_s:te_e]
        eq_curve, trades, _ = _simulate(sub, p)
        m = _summarize(eq_curve, trades, p.notional_usd, 1)
        return m, trades
    elif mode == "train":
        tr_s, tr_e, te_s, te_e = split70_30(len(aligned))
        sub = aligned[tr_s:tr_e]
        eq_curve, trades, _ = _simulate(sub, p)
        m = _summarize(eq_curve, trades, p.notional_usd, 1)
        return m, trades
    else:
        raise ValueError(f"unknown mode: {mode}")
