"""Rolling walk-forward evaluation and the MASTER OOS LEDGER.

    train 180d | validation 30d | test 30d      rolled forward 30d at a time

Only trades whose ENTRY falls inside a **test** window reach the master ledger, and every final
qualification number is computed from that ledger alone. Training and validation PnL never enter it.

**Why one continuous replay rather than one replay per window.** None of PaperLab's 27 strategies
fits parameters: `Params` are set by the operator and frozen for the run, so there is nothing for a
training window to learn. Re-replaying each window separately would therefore produce identical
signals at far higher cost, and worse, it would reset position state at every boundary -- a trade
opened on the last day of a train window would vanish instead of carrying into the test window as
it does in reality. So the tape is replayed once and trades are partitioned by entry timestamp.

The train window still does real work: it is warm-up context for indicators, and `freeze_check()`
verifies the parameters really were frozen -- if a strategy mutated its own `params` while running,
the later windows would be contaminated by earlier ones and the "out-of-sample" claim would be false.

Entry-time attribution is deliberate. A trade belongs to the window it was DECIDED in; attributing
by exit would let a decision made with in-sample information be counted as out-of-sample.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics as st
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.competition import metrics as mx
from app.core.portfolio import ClosedTrade
from app.core.types import Fill

DAY_MS = 86_400_000


@dataclass(frozen=True)
class WalkForwardConfig:
    train_days: int = 180
    validation_days: int = 30
    test_days: int = 30
    step_days: int = 30
    # Activity rules. Deliberately NOT "100 trades per window": that would exclude any
    # lower-frequency strategy by construction rather than on merit.
    min_total_oos_trades: int = 100
    min_active_window_ratio: float = 0.60
    min_profitable_window_ratio: float = 0.55

    def span_days(self) -> int:
        return self.train_days + self.validation_days + self.test_days


@dataclass(frozen=True)
class Window:
    index: int
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    test_start: int
    test_end: int

    def contains_test(self, ts: int) -> bool:
        return self.test_start <= ts < self.test_end

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def schedule(first_ms: int, last_ms: int, cfg: WalkForwardConfig) -> list[Window]:
    """Rolling windows that fit entirely inside [first_ms, last_ms]."""
    out: list[Window] = []
    train = cfg.train_days * DAY_MS
    val = cfg.validation_days * DAY_MS
    test = cfg.test_days * DAY_MS
    step = cfg.step_days * DAY_MS
    start = first_ms
    i = 0
    while start + train + val + test <= last_ms:
        ts = start + train
        vs = ts + val
        out.append(Window(i, start, ts, ts, vs, vs, vs + test))
        start += step
        i += 1
    return out


def params_fingerprint(strategy: Any) -> str:
    """Hash of a strategy's parameter values, for the freeze check."""
    p = getattr(strategy, "params", None)
    try:
        values = {f.name: getattr(p, f.name) for f in dataclasses.fields(p)}
    except TypeError:
        values = {}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class WindowResult:
    index: int
    test_start: int
    test_end: int
    trades: int = 0
    net: float = 0.0
    gross: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    r_sum: float = 0.0

    @property
    def active(self) -> bool:
        return self.trades > 0

    @property
    def profitable(self) -> bool:
        return self.trades > 0 and self.net > 0

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["active"] = self.active
        d["profitable"] = self.profitable
        return d


@dataclass
class WalkForwardResult:
    strategy_id: str
    version: str
    leverage: int | None
    config: WalkForwardConfig
    windows: list[WindowResult] = field(default_factory=list)
    oos_trades: list[ClosedTrade] = field(default_factory=list)
    oos_fills: list[Fill] = field(default_factory=list)
    oos_equity: list[tuple[int, float]] = field(default_factory=list)
    metrics: mx.CompetitorMetrics | None = None
    starting_equity: float = 0.0
    params_frozen: bool = True
    params_before: str = ""
    params_after: str = ""

    # -- window statistics ---------------------------------------------------------------
    @property
    def total_windows(self) -> int:
        return len(self.windows)

    @property
    def active_windows(self) -> int:
        return sum(1 for w in self.windows if w.active)

    @property
    def profitable_windows(self) -> int:
        return sum(1 for w in self.windows if w.profitable)

    @property
    def active_ratio(self) -> float:
        return self.active_windows / self.total_windows if self.total_windows else 0.0

    @property
    def profitable_ratio(self) -> float:
        """Share of ACTIVE windows that made money. A window with no trades is not a loss."""
        return self.profitable_windows / self.active_windows if self.active_windows else 0.0

    def window_returns(self) -> list[float]:
        base = self.starting_equity or 1.0
        return [w.net / base for w in self.windows if w.active]

    def median_window_return(self) -> float:
        r = self.window_returns()
        return st.median(r) if r else 0.0

    def worst_window_return(self) -> float:
        r = self.window_returns()
        return min(r) if r else 0.0

    def best_window_return(self) -> float:
        r = self.window_returns()
        return max(r) if r else 0.0

    def by_symbol(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for t in self.oos_trades:
            b = out.setdefault(t.symbol, {"trades": 0, "net": 0.0, "r": 0.0})
            b["trades"] += 1
            b["net"] += t.net
            b["r"] += t.r_multiple
        return out

    def symbol_concentration(self) -> float:
        """Share of total OOS profit contributed by the single best symbol.

        High concentration is not automatically disqualifying -- it is exposed so "BTC happened to
        make this strategy rich" cannot hide inside a portfolio number.
        """
        gains = [v["net"] for v in self.by_symbol().values() if v["net"] > 0]
        return (max(gains) / sum(gains)) if gains else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "version": self.version, "leverage": self.leverage,
            "config": dataclasses.asdict(self.config),
            "windows": [w.to_dict() for w in self.windows],
            "total_windows": self.total_windows, "active_windows": self.active_windows,
            "profitable_windows": self.profitable_windows,
            "active_ratio": self.active_ratio, "profitable_ratio": self.profitable_ratio,
            "median_window_return": self.median_window_return(),
            "worst_window_return": self.worst_window_return(),
            "best_window_return": self.best_window_return(),
            "by_symbol": self.by_symbol(), "symbol_concentration": self.symbol_concentration(),
            "starting_equity": self.starting_equity,
            "params_frozen": self.params_frozen,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "oos_equity": self.oos_equity,
        }


def master_ledger_equity(trades: Sequence[ClosedTrade], starting_equity: float
                         ) -> list[tuple[int, float]]:
    """Equity implied by the OOS trades alone, compounded in exit order.

    This is what "metrics on the master OOS ledger" means: the in-sample stretches between test
    windows are removed entirely, so drawdown is measured across the out-of-sample sequence rather
    than across a history the strategy was never judged on.

    Two capitalisation rules are in play and they are deliberately different:

    * **During the replay**, each out-of-sample window is independently capitalised (ReplayEngine's
      `reset_at`). Without that, a book that blows its halt floor in month one stays halted for
      years and windows 2..N produce no evidence at all -- the walk-forward would be vacuous.
    * **In this ledger**, one notional account of the starting size takes every OOS trade in
      sequence and is NOT reset. That asks the question qualification actually cares about: would a
      single account, trading this edge continuously out-of-sample, have survived?

    So the curve can fall below zero and drawdown can exceed 100%. That is a finding, not an
    artefact: it means the concatenated out-of-sample sequence would have wiped the account out.
    Per-window returns (`window_returns`, `worst_window_return`) are the scale-free view.
    """
    eq = starting_equity
    out = [(trades[0].entry_ts, eq)] if trades else []
    for t in sorted(trades, key=lambda x: x.exit_ts):
        eq += t.net
        out.append((t.exit_ts, eq))
    return out


def build_master_metrics(res: WalkForwardResult, leverage_source: Any = None) -> mx.CompetitorMetrics:
    """Competitor metrics computed ONLY from out-of-sample trades and their fills.

    `leverage_source` is the ReplayResult, which carries the per-bar leverage samples. They are
    taken from the whole replay rather than the OOS slices alone -- the sampler runs per bar, not
    per trade -- so treat them as the book's behaviour over the run, not an OOS-only statistic.
    """
    res.oos_equity = master_ledger_equity(res.oos_trades, res.starting_equity)
    m = mx.compute(res.strategy_id, res.oos_trades, res.oos_fills, res.oos_equity,
                   res.starting_equity, version=res.version, leverage=leverage_source)
    m.configured_max_leverage = int(res.leverage or 0)
    return m


def partition(result: Any, windows: Sequence[Window], starting_equity: float,
              strategy_id: str, version: str, leverage: int | None,
              cfg: WalkForwardConfig) -> WalkForwardResult:
    """Split one full-history ReplayResult into windows and build the master OOS ledger."""
    wf = WalkForwardResult(strategy_id=strategy_id, version=version, leverage=leverage,
                           config=cfg, starting_equity=starting_equity)
    wf.windows = [WindowResult(w.index, w.test_start, w.test_end) for w in windows]
    by_index = {w.index: w for w in windows}
    slot = {w.index: wr for w, wr in zip(windows, wf.windows)}

    # fills are attributed through their position, so a trade's costs follow the trade
    fills_by_pos: dict[str, list[Fill]] = {}
    for f in result.fills:
        fills_by_pos.setdefault(f.position_id, []).append(f)

    for t in result.trades:
        win = next((w for w in windows if w.contains_test(t.entry_ts)), None)
        if win is None:
            continue                      # entered during train/validation: not out-of-sample
        wr = slot[win.index]
        wr.trades += 1
        wr.net += t.net
        wr.gross += t.pnl
        wr.fees += t.fees
        wr.r_sum += t.r_multiple
        wf.oos_trades.append(t)
        pos_fills = fills_by_pos.get(t.position_id, [])
        wf.oos_fills.extend(pos_fills)
        for f in pos_fills:
            if f.kind == "funding":
                wr.funding += f.realized_pnl
            else:
                wr.slippage += mx.slippage_usdt(f)
    wf.oos_trades.sort(key=lambda x: x.exit_ts)
    wf.metrics = build_master_metrics(wf, leverage_source=result)
    return wf


def run(replay: Callable[[], Any], strategy_factory: Callable[[], Any], windows: Sequence[Window],
        starting_equity: float, strategy_id: str, version: str, leverage: int | None,
        cfg: WalkForwardConfig) -> WalkForwardResult:
    """Replay the whole tape once, then partition. `replay` returns a ReplayResult.

    `strategy_factory` is only used for the parameter-freeze check: it builds a fresh instance so
    its parameter fingerprint can be compared with the one the replay finished holding.
    """
    before = params_fingerprint(strategy_factory())
    result = replay()
    after = params_fingerprint(getattr(result, "strategy", None) or strategy_factory())
    wf = partition(result, windows, starting_equity, strategy_id, version, leverage, cfg)
    wf.params_before, wf.params_after = before, after
    wf.params_frozen = before == after
    return wf
