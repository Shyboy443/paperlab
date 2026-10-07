"""Monte Carlo over the MASTER OOS LEDGER.

A positive average return says nothing about whether the path survives. Resampling the realised
out-of-sample trade sequence answers a different question: given this edge, how often does an
account of this size get killed on the way to it?

Run on out-of-sample trades only. Resampling training trades would measure the strategy's fit to
history rather than its forward risk.

Two resampling modes, both offered because they answer different things:

* `iid`  -- draw trades with replacement. Assumes trade outcomes are independent, which breaks any
            serial structure (a losing streak caused by one bad regime gets scattered).
* `block` -- draw contiguous blocks, preserving local clustering. Closer to reality when losses
            arrive together, which is exactly when an account dies.

`block` is the default: for a small account the clustering is the risk.
"""
from __future__ import annotations

import random
import statistics as st
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

Mode = Literal["iid", "block"]


@dataclass(frozen=True)
class MonteCarloConfig:
    simulations: int = 10_000
    mode: Mode = "block"
    block_size: int = 5
    seed: int = 7
    # Ruin is an explicit threshold, not an implied one. For a 20 USDT competition book, an account
    # at or below 5 USDT cannot meet Binance's minimum notional on any symbol here, so it is dead
    # in practice even though the number is not zero.
    ruin_equity: float = 5.0
    drawdown_levels: tuple[float, ...] = (0.75, 0.50, 0.25)   # fractions of starting equity


@dataclass
class MonteCarloResult:
    simulations: int = 0
    mode: str = "block"
    starting_equity: float = 0.0
    trades_per_path: int = 0
    median_ending_equity: float = 0.0
    mean_ending_equity: float = 0.0
    median_max_drawdown: float = 0.0
    p95_max_drawdown: float = 0.0
    p99_max_drawdown: float = 0.0
    worst_max_drawdown: float = 0.0
    median_longest_losing_streak: int = 0
    p95_longest_losing_streak: int = 0
    prob_below: dict[str, float] = field(default_factory=dict)   # "0.75" -> probability
    ruin_probability: float = 0.0
    ruin_equity: float = 0.0
    ran: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _path(nets: Sequence[float], rng: random.Random, cfg: MonteCarloConfig) -> list[float]:
    n = len(nets)
    if cfg.mode == "iid":
        return [nets[rng.randrange(n)] for _ in range(n)]
    out: list[float] = []
    while len(out) < n:
        start = rng.randrange(n)
        out.extend(nets[start:start + cfg.block_size] or [nets[start]])
    return out[:n]


def run(trades: Sequence[Any], starting_equity: float,
        cfg: MonteCarloConfig | None = None) -> MonteCarloResult:
    """Resample the OOS trade sequence. `trades` need only expose `.net`."""
    cfg = cfg or MonteCarloConfig()
    res = MonteCarloResult(simulations=cfg.simulations, mode=cfg.mode,
                           starting_equity=starting_equity, ruin_equity=cfg.ruin_equity)
    nets = [float(getattr(t, "net", 0.0)) for t in trades]
    if len(nets) < 5:
        res.reason = f"only {len(nets)} out-of-sample trades; too few to resample"
        return res
    res.trades_per_path = len(nets)
    rng = random.Random(cfg.seed)

    endings: list[float] = []
    dds: list[float] = []
    streaks: list[int] = []
    below = {f"{lvl:g}": 0 for lvl in cfg.drawdown_levels}
    ruined = 0

    for _ in range(cfg.simulations):
        eq = starting_equity
        peak = starting_equity
        worst_dd = 0.0
        streak = 0
        longest = 0
        hit_ruin = False
        hit_level = {lvl: False for lvl in cfg.drawdown_levels}
        for net in _path(nets, rng, cfg):
            eq += net
            if net <= 0:
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 0
            peak = max(peak, eq)
            if peak > 0:
                worst_dd = max(worst_dd, 1.0 - eq / peak)
            for lvl in cfg.drawdown_levels:
                if not hit_level[lvl] and eq <= starting_equity * lvl:
                    hit_level[lvl] = True
            if not hit_ruin and eq <= cfg.ruin_equity:
                hit_ruin = True
                # A ruined account stops trading; continuing the path would let it "recover"
                # from an equity level at which it could not legally place another order.
                break
        endings.append(eq)
        dds.append(worst_dd)
        streaks.append(longest)
        for lvl in cfg.drawdown_levels:
            if hit_level[lvl]:
                below[f"{lvl:g}"] += 1
        if hit_ruin:
            ruined += 1

    endings.sort()
    dds.sort()
    streaks.sort()
    res.ran = True
    res.median_ending_equity = _percentile(endings, 0.5)
    res.mean_ending_equity = st.fmean(endings)
    res.median_max_drawdown = _percentile(dds, 0.5)
    res.p95_max_drawdown = _percentile(dds, 0.95)
    res.p99_max_drawdown = _percentile(dds, 0.99)
    res.worst_max_drawdown = dds[-1]
    res.median_longest_losing_streak = int(_percentile([float(x) for x in streaks], 0.5))
    res.p95_longest_losing_streak = int(_percentile([float(x) for x in streaks], 0.95))
    res.prob_below = {k: v / cfg.simulations for k, v in below.items()}
    res.ruin_probability = ruined / cfg.simulations
    return res
