"""Bot Arena: a discovery competition between single-market specialist bots.

Every entrant is one strategy on one coin at one signal timeframe with its own isolated 20 USDT
wallet. Nothing is shared -- not capital, not positions, not risk budget -- so a result attributes
to that exact combination and nothing else.

The rules the arena enforces:

* **A bot cannot be entered unless it can actually trade.** `preflight` checks the strategy's data
  needs, its NATIVE signal timeframe, the symbol's presence in the eligible universe, enough
  history, and whether a 20 USDT book at the profile's ordinary risk can reach Binance's real order
  minimum under the fee gate. A failure is NOT_ENTERED with a stated reason and does NOT count
  toward the field size.
* **Selection never looks at results.** Candidates that pass preflight are trimmed to `max_bots`
  by a deterministic strategy x coin rotation (`bots.select`), so the field spreads across markets.
* **A competition needs a real field.** Below `min_active_bots` the run reports
  INSUFFICIENT_COMPETITORS and declares no winner. Ranking three bots is not a tournament.

Discovery advances a bot to multi-year validation; it never qualifies one:

    DISCOVERY -> ADVANCE -> MULTI-YEAR VALIDATION -> QUALIFIED -> SHADOW_LIVE -> LIVE_CANDIDATE
              -> MANUAL GO LIVE

Winning here only earns the right to be tested properly, because one window is exactly the
evidence that overfits.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from app.backtest import archive
from app.backtest.brackets import BracketTable
from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine, ReplayResult
from app.competition import metrics as mx
from app.competition.bots import (AGGRESSIVE, ARENA_TIMEFRAMES, AttackPolicy, BotSpec, PROFILES,
                                  Preflight, RiskProfile, candidates, preflight, select,
                                  sizing_window, timeframe_table)
from app.core.types import Candle
from app.execution.config import BINANCE_USDM, ExecutionConfig, FeeSchedule

log = logging.getLogger("paperlab.competition.arena")

ARENA_VERSION = "arena-2"

# A 30m bot legitimately trades less than a 5m one. Requiring the same sample from both would
# reject the slower bot for its cadence rather than its edge. Discovery thresholds only: long-term
# validation demands far more evidence.
MIN_TRADES_BY_TF: dict[str, int] = {"5m": 40, "15m": 20, "30m": 12}

# How the leaderboard is ordered. Rank is not advancement -- `state` decides that.
STATE_ORDER = {"ADVANCE": 0, "FAILED": 1, "INSUFFICIENT_SAMPLE": 1, "DISQUALIFIED": 2, "ERROR": 3}


@dataclass(frozen=True)
class ArenaConfig:
    """Discovery settings. Every threshold is configurable research input."""
    min_active_bots: int = 10
    max_bots: int = 30
    starting_balance: float = 20.0
    profile: str = "AGGRESSIVE"
    leverage_ceiling: int = 20
    params_version: str = "v1"
    seed: int = 7
    # Advancement gates. Passing ALL of them promotes a bot to VALIDATION, never to live.
    min_net_profit: float = 0.0          # net after fees, slippage and funding must be > this
    min_expectancy_r: float = 0.0        # > this
    min_profit_factor: float = 1.10      # >= this
    max_drawdown_pct: float = 0.35       # <= this
    max_liquidations: int = 0            # <= this
    min_trades: tuple[tuple[str, int], ...] = tuple(MIN_TRADES_BY_TF.items())
    min_bars_history: int = 5000
    # Cost and sizing model.
    fees: FeeSchedule = BINANCE_USDM
    fee_source: str = "schedule"
    execution: ExecutionConfig = ExecutionConfig()
    min_notional_safety_multiplier: float = 1.0
    max_fee_share_of_r: float = 0.25
    leverage_policy: str = "needed"
    # Research switches (off = exactly the behaviour arena 9bbe42e53fd8 ran with).
    cost_gate_min_ratio: float | None = None
    shadow_cost_rejects: bool = True
    dataset_role: str = ""                  # DEVELOPMENT / TEST / ... (docs/DATASET_SPLIT_V2.md)
    # A pre-registered field: when set, ONLY these bot keys may enter (fixed before the run, e.g. the
    # v2 TEST field chosen from DEVELOPMENT results). Empty = the usual preflight + selection.
    only_keys: tuple[str, ...] = ()
    # Source fingerprints of the strategy versions this run evaluates (v2 freeze), as (name, sha).
    strategy_fingerprints: tuple[tuple[str, str], ...] = ()

    def min_trades_for(self, timeframe: str) -> int:
        return dict(self.min_trades).get(timeframe, 20)

    @property
    def risk(self) -> RiskProfile:
        base = PROFILES.get(self.profile, AGGRESSIVE)
        return dataclasses.replace(base, starting_balance=float(self.starting_balance),
                                   max_leverage=int(self.leverage_ceiling))

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["min_trades"] = dict(self.min_trades)
        d["only_keys"] = list(self.only_keys)
        d["strategy_fingerprints"] = dict(self.strategy_fingerprints)
        d["risk_profile"] = self.risk.to_dict()
        d["arena_version"] = ARENA_VERSION
        return d

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, default=str)
                              .encode()).hexdigest()[:16]


@dataclass
class BotResult:
    spec: BotSpec
    version: str = ""
    entered: bool = True
    reason: str = ""                      # why NOT_ENTERED
    state: str = "COMPETING"
    metrics: mx.CompetitorMetrics | None = None
    result: ReplayResult | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)   # preflight numbers
    extra: dict[str, Any] = field(default_factory=dict)    # signals, states, leverage used ...
    elapsed_s: float = 0.0
    rank: int = 0

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def traded(self) -> bool:
        return bool(self.metrics and self.metrics.trades)

    def to_dict(self, heavy: bool = False) -> dict[str, Any]:
        m = self.metrics
        d = {**self.spec.to_dict(), "version": self.version, "entered": self.entered,
             "reason": self.reason, "state": self.state, "rank": self.rank, "gates": self.gates,
             "reasons": self.reasons, "detail": self.detail, "elapsed_s": round(self.elapsed_s, 2),
             **self.extra, "metrics": m.to_dict() if m else None}
        if heavy and self.result is not None:
            d["equity"] = _downsample(self.result.equity, 400)
            d["trades_ledger"] = [_trade_row(t) for t in self.result.trades]
        return d


@dataclass
class ArenaRun:
    config: ArenaConfig
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str = ""
    symbols: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    bots: list[BotResult] = field(default_factory=list)          # the selected field
    not_entered: list[BotResult] = field(default_factory=list)   # failed preflight
    eligible_not_selected: list[str] = field(default_factory=list)
    symbol_table: list[dict[str, Any]] = field(default_factory=list)
    strategy_table: list[dict[str, Any]] = field(default_factory=list)
    status: str = "RUNNING"
    bars: int = 0
    started_ts: int = 0
    finished_ts: int = 0

    @property
    def active(self) -> list[BotResult]:
        return [b for b in self.bots if b.entered]

    def ranked(self) -> list[BotResult]:
        """ADVANCE first, then net result after every cost; bots that never traded last."""
        ran = [b for b in self.active if b.metrics]
        return sorted(ran, key=lambda b: (0 if b.traded else 1, STATE_ORDER.get(b.state, 3),
                                          -b.metrics.net_profit, b.key))

    def advanced(self) -> list[BotResult]:
        return [b for b in self.bots if b.state == "ADVANCE"]

    def summary(self) -> dict[str, Any]:
        ran = [b for b in self.active if b.metrics]
        traded = [b for b in ran if b.traded]
        profitable = [b for b in ran if b.metrics.net_profit > 0]
        liquidated = [b for b in ran if b.metrics.liquidation_count]
        reasons: dict[str, int] = {}
        for b in self.not_entered:
            reasons[b.reason] = reasons.get(b.reason, 0) + 1
        ranked = self.ranked()
        best = ranked[0] if ranked else None
        worst = min(traded, key=lambda b: b.metrics.net_profit) if traded else None
        states: dict[str, int] = {}
        for b in self.active:
            states[b.state] = states.get(b.state, 0) + 1
        return {
            "run_id": self.run_id, "label": self.label, "status": self.status,
            "active_bots": len(self.active), "min_active_bots": self.config.min_active_bots,
            "configured_bots": len(self.bots), "not_entered": len(self.not_entered),
            "eligible_not_selected": len(self.eligible_not_selected),
            "bots_with_results": len(ran), "bots_that_traded": len(traded),
            "profitable_after_costs": len(profitable), "liquidated": len(liquidated),
            "advanced": len(self.advanced()), "advanced_keys": [b.key for b in self.advanced()],
            "states": states,
            "total_fees": sum(b.metrics.fees_paid for b in ran),
            "total_slippage": sum(b.metrics.slippage_cost for b in ran),
            "total_funding": sum(b.metrics.funding_paid for b in ran),
            "total_gross_pnl": sum(b.metrics.gross_pnl for b in ran),
            "total_net_pnl": sum(b.metrics.net_profit for b in ran),
            "total_trades": sum(b.metrics.trades for b in ran),
            "best_bot": best.key if best else None,
            "worst_bot": worst.key if worst else None,
            "coins": sorted({b.spec.coin for b in self.active}),
            "symbols": sorted({b.spec.symbol for b in self.active}),
            "timeframes": [tf for tf in ARENA_TIMEFRAMES if any(b.spec.timeframe == tf for b in self.active)],
            "strategies": sorted({b.spec.strategy_id for b in self.active}),
            "not_entered_reasons": reasons,
            "months": list(self.months), "bars": self.bars,
            "starting_balance": self.config.starting_balance, "profile": self.config.profile,
            "leverage_ceiling": self.config.leverage_ceiling,
            "elapsed_s": max(0.0, (self.finished_ts - self.started_ts) / 1000) if self.finished_ts else None,
        }

    def preflight_report(self) -> dict[str, Any]:
        return {"symbols": self.symbol_table, "strategies": self.strategy_table,
                "not_entered": [{"key": b.key, "strategy_id": b.spec.strategy_id,
                                 "symbol": b.spec.symbol, "timeframe": b.spec.timeframe,
                                 "reason": b.reason} for b in self.not_entered],
                "eligible_not_selected": list(self.eligible_not_selected)}


# ---- helpers -------------------------------------------------------------------------------------

def _downsample(series: Sequence[tuple[int, float]], n: int) -> list[list[float]]:
    if not series:
        return []
    step = max(1, len(series) // n)
    pts = [list(series[i]) for i in range(0, len(series), step)]
    if pts[-1][0] != series[-1][0]:
        pts.append(list(series[-1]))
    return [[int(t), round(float(v), 4)] for t, v in pts]


def _trade_row(t: Any) -> dict[str, Any]:
    return {"side": t.side, "qty": t.qty, "entry": t.entry_price, "exit": t.exit_price,
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "pnl": round(t.pnl, 6),
            "fees": round(t.fees, 6), "net": round(t.net, 6), "r": round(t.r_multiple, 4),
            "exit_kind": t.exit_kind}


def stream(settings: Any, symbol: str, months: Sequence[str]) -> Iterator[Candle]:
    for month in months:
        for c in archive.load_klines(settings, symbol, [month]):
            yield c


def history(settings: Any, symbol: str, months: Sequence[str]) -> tuple[int, float | None]:
    """(1m bars cached, median close) for this symbol over the window. Counted from the files,
    not from metadata: the dataset manifest is rewritten by each fetch."""
    closes: list[float] = []
    for month in months:
        closes.extend(c.close for c in archive.load_klines(settings, symbol, [month]))
    return len(closes), (statistics.median(closes) if closes else None)


def funding_for(settings: Any, symbols: Sequence[str],
                months: Sequence[str]) -> FundingSchedule:
    return FundingSchedule({s: archive.load_funding(settings, s, months) for s in symbols})


def entry_stats(res: ReplayResult) -> dict[str, Any]:
    """What the bot actually did at entry: its states, risk and position leverage."""
    states: dict[str, int] = {}
    quality: dict[str, int] = {}
    risk, lev = [], []
    for f in res.fills:
        if f.kind != "entry":
            continue
        meta = f.meta or {}
        s = meta.get("attack_state")
        if s:
            states[s] = states.get(s, 0) + 1
        q = meta.get("quality")
        if q:
            quality[q] = quality.get(q, 0) + 1
        if meta.get("risk_pct") is not None:
            risk.append(float(meta["risk_pct"]))
        lev.append(int(f.leverage or 0))
    cg = list(getattr(res, "cost_gate_events", []) or [])
    cg_ids = {e["id"] for e in cg}
    shadows = {t.position_id: t for t in getattr(res, "shadow_trades", []) or []}
    cg_trades = [shadows[pid] for pid, did in (getattr(res, "shadow_links", {}) or {}).items()
                 if did in cg_ids and pid in shadows]
    ratios = [e.get("edge_to_cost") for e in cg if isinstance(e.get("edge_to_cost"), (int, float))]
    qualities = [float(f.meta["signal_quality"]) for f in res.fills
                 if f.kind == "entry" and isinstance((f.meta or {}).get("signal_quality"), (int, float))]
    return {"entry_states": states, "entry_quality": quality,
            "avg_signal_quality": (sum(qualities) / len(qualities)) if qualities else None,
            "cost_gate": {"rejected": len(cg), "shadowed": len(cg_trades),
                          "shadow_net": sum(t.net for t in cg_trades),
                          "shadow_wins": sum(1 for t in cg_trades if t.net > 0),
                          "median_rejected_ratio": sorted(ratios)[len(ratios) // 2] if ratios else None}
            if cg else None,
            "avg_risk_pct": (sum(risk) / len(risk)) if risk else None,
            "max_risk_pct": max(risk) if risk else None,
            "avg_position_leverage": (sum(lev) / len(lev)) if lev else None,
            "max_position_leverage": max(lev) if lev else None,
            "signals": res.signals, "partial_fills": res.partial_fills,
            "rejects": dict(res.rejects), "halted": res.halted, "fee_source": res.fee_source}


# ---- parallel workers ------------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _init_worker(settings: Any, cfg: "ArenaConfig", months: Sequence[str], rules: dict[str, Any],
                 brackets: Any, version: str) -> None:
    """Each worker process builds its own arena once. Strategy classes are looked up by id inside
    the worker because a v2 class bound to a timeframe is created dynamically and cannot pickle."""
    from app.strategies.registry import load_all, load_v2
    _WORKER["arena"] = Arena(settings, cfg, months, rules, brackets=brackets)
    _WORKER["classes"] = load_v2() if version == "v2" else load_all(strict=True)


def _run_spec(spec: dict[str, Any]) -> tuple[str, Any, Any, str, float]:
    arena: Arena = _WORKER["arena"]
    bs = BotSpec(**spec)
    t0 = time.time()
    try:
        res, m = arena.run_bot(bs, _WORKER["classes"][bs.strategy_id])
        return bs.key, res, m, "", time.time() - t0
    except Exception as exc:                              # reported, never fatal to the arena
        log.exception("%s failed", bs.key)
        return bs.key, None, None, f"{type(exc).__name__}: {exc}"[:200], time.time() - t0


# ---- the arena -----------------------------------------------------------------------------------

class Arena:
    def __init__(self, settings: Any, cfg: ArenaConfig, months: Sequence[str],
                 rules: dict[str, Any], brackets: BracketTable | None = None,
                 storage: Any = None, label: str = ""):
        self.settings = settings
        self.cfg = cfg
        self.months = list(months)
        self.rules = rules
        self.brackets = brackets or BracketTable.fallback()
        self.storage = storage
        self.label = label
        self.universe_reasons: dict[str, str] = {}    # symbol -> why the universe filter excluded it
        self._history: dict[str, tuple[int, float | None]] = {}

    # -- configuration ----------------------------------------------------------------------
    def _settings_for(self, spec: BotSpec) -> Any:
        """The bot's own profile applied to the engine it runs in. Position size still comes from
        the RiskManager; this sets the ordinary risk it sizes against and the model knobs."""
        risk = self.cfg.risk
        return dataclasses.replace(
            self.settings, strategy_starting_balance=float(self.cfg.starting_balance),
            risk_per_trade_pct=float(risk.ordinary_risk_pct),
            max_fee_share_of_r=float(self.cfg.max_fee_share_of_r),
            min_notional_safety_multiplier=float(self.cfg.min_notional_safety_multiplier))

    def _hist(self, symbol: str) -> tuple[int, float | None]:
        if symbol not in self._history:
            self._history[symbol] = history(self.settings, symbol, self.months)
        return self._history[symbol]

    def check(self, spec: BotSpec, cls: Any, universe: Sequence[str]) -> Preflight:
        bars, price = self._hist(spec.symbol)
        pf = preflight(spec, cls, self.rules.get(spec.symbol), universe, bars, self.cfg.risk,
                       self.cfg.min_bars_history, self.cfg.fees.taker_rate,
                       self.cfg.max_fee_share_of_r, price=price,
                       safety=self.cfg.min_notional_safety_multiplier)
        why = self.universe_reasons.get(spec.symbol)
        if not pf.ok and pf.reason == "symbol not in the eligible universe" and why:
            pf.reason = f"symbol not in the eligible universe: {why}"
        return pf

    def symbol_feasibility(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """One row per symbol: the exchange minimum, PaperLab's minimum, the fee-gate cap, the risk
        needed and whether a bot on it can trade at all -- independent of strategy."""
        out = []
        for sym in symbols:
            r = self.rules.get(sym)
            bars, price = self._hist(sym)
            row: dict[str, Any] = {"symbol": sym, "bars": bars, "reference_price": price}
            if r is None or not price:
                row.update({"tradeable": False,
                            "reason": "no exchange metadata" if r is None else "no market data"})
                out.append(row)
                continue
            w = sizing_window(r, self.cfg.risk, self.cfg.leverage_ceiling, price,
                              self.cfg.fees.taker_rate, self.cfg.max_fee_share_of_r,
                              self.cfg.min_notional_safety_multiplier)
            ok = bool(w["feasible"]) and bars >= self.cfg.min_bars_history
            reason = ""
            if bars < self.cfg.min_bars_history:
                reason = f"insufficient history: {bars:,} bars"
            elif not w["feasible"]:
                reason = (f"smallest legal order {w['floor']:.2f} USDT ({w['binding_minimum']}) > "
                          f"fee-gate cap {w['ceiling']:.2f} USDT at "
                          f"{self.cfg.risk.ordinary_risk_pct:.2%} risk")
            row.update({**w, "tradeable": ok, "reason": reason})
            out.append(row)
        return out

    # -- one bot ------------------------------------------------------------------------------
    @staticmethod
    def bind(spec: BotSpec, cls: Any) -> Any:
        """A v2 family bound to this bot's timeframe; a v1 class as it is."""
        if hasattr(cls, "for_timeframe") and not getattr(cls, "signal_tf", ""):
            return cls.for_timeframe(spec.timeframe)
        return cls

    def build_engine(self, spec: BotSpec, gate: Any = None, funding: Any = None) -> ReplayEngine:
        """THE construction of a bot's engine: fees, execution model, sizing policy, leverage policy,
        risk ceiling, cost gate and gate. The arena replay and the live shadow both call this, so a
        live shadow bot is the arena bot, fed live bars instead of archive bars."""
        cost_gate = None
        if self.cfg.cost_gate_min_ratio is not None:
            from app.competition.cost_gate import CostGate, CostGateConfig
            cost_gate = CostGate(CostGateConfig(min_edge_to_cost=float(self.cfg.cost_gate_min_ratio)))
        return ReplayEngine(self._settings_for(spec), [spec.symbol], rules=self.rules,
                            seed=self.cfg.seed,
                            funding=funding if funding is not None
                            else funding_for(self.settings, [spec.symbol], self.months),
                            execution=self.cfg.execution, fees=self.cfg.fees,
                            brackets=self.brackets, fee_source=self.cfg.fee_source,
                            sizing=AttackPolicy(self.cfg.risk),
                            leverage_policy=self.cfg.leverage_policy,
                            max_risk_pct=self.cfg.risk.max_risk_pct, gate=gate, cost_gate=cost_gate,
                            shadow_cost_rejects=bool(cost_gate is not None and self.cfg.shadow_cost_rejects))

    def run_bot(self, spec: BotSpec, cls: Any,
                gate: Any = None) -> tuple[ReplayResult, mx.CompetitorMetrics]:
        cls = self.bind(spec, cls)
        eng = self.build_engine(spec, gate)
        res = eng.run(cls, stream(self.settings, spec.symbol, self.months),
                      leverage=spec.max_leverage, signal_tf=spec.timeframe,
                      only_symbol=spec.symbol)
        m = mx.compute(spec.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                       rejects=res.rejects, halted=res.halted, version=spec.version(),
                       leverage=res)
        m.configured_max_leverage = spec.max_leverage
        return res, m

    def judge(self, spec: BotSpec, m: mx.CompetitorMetrics) -> tuple[str, list[dict], list[str]]:
        """Discovery advancement by hard gates. Passing means 'test this properly', never 'trade
        it live'. The gates are never loosened because nobody passed them."""
        c = self.cfg
        need = c.min_trades_for(spec.timeframe)
        pf = 999.0 if m.profit_factor == float("inf") else m.profit_factor
        gates = [
            {"name": "no_liquidation", "ok": m.liquidation_count <= c.max_liquidations,
             "actual": m.liquidation_count, "threshold": c.max_liquidations, "comparison": "<="},
            {"name": f"min_trades_{spec.timeframe}", "ok": m.trades >= need,
             "actual": m.trades, "threshold": need, "comparison": ">="},
            {"name": "net_profit_after_costs", "ok": m.net_profit > c.min_net_profit,
             "actual": m.net_profit, "threshold": c.min_net_profit, "comparison": ">"},
            {"name": "positive_expectancy", "ok": m.expectancy_r > c.min_expectancy_r,
             "actual": m.expectancy_r, "threshold": c.min_expectancy_r, "comparison": ">"},
            {"name": "profit_factor", "ok": pf >= c.min_profit_factor,
             "actual": pf, "threshold": c.min_profit_factor, "comparison": ">="},
            {"name": "max_drawdown", "ok": m.max_drawdown_pct <= c.max_drawdown_pct,
             "actual": m.max_drawdown_pct, "threshold": c.max_drawdown_pct, "comparison": "<="},
        ]
        failed = [g for g in gates if not g["ok"]]
        reasons = [f"{g['name']} {g['actual']:.4g} {g['comparison']} {g['threshold']:.4g} failed"
                   for g in failed]
        if m.liquidation_count > c.max_liquidations:
            return "DISQUALIFIED", gates, ["liquidated"] + reasons
        if m.trades < need:
            return "INSUFFICIENT_SAMPLE", gates, [f"{m.trades} trades < {need} required for "
                                                  f"{spec.timeframe}"] + [r for r in reasons
                                                                         if "min_trades" not in r]
        if failed:
            return "FAILED", gates, reasons
        return "ADVANCE", gates, []

    # -- the competition ----------------------------------------------------------------------
    def plan(self, classes: dict[str, Any], symbols: Sequence[str], universe: Sequence[str],
             timeframes: Sequence[str] = ARENA_TIMEFRAMES) -> ArenaRun:
        """Preflight every candidate, then select the field. Nothing has been replayed yet."""
        run = ArenaRun(config=self.cfg, label=self.label, months=self.months,
                       started_ts=int(time.time() * 1000))
        run.symbol_table = self.symbol_feasibility(symbols)
        run.strategy_table = timeframe_table(classes)
        cands = candidates(classes, symbols, timeframes=timeframes,
                           leverages=(self.cfg.leverage_ceiling,), profile=self.cfg.profile,
                           params_version=self.cfg.params_version)
        if self.cfg.only_keys:
            wanted = set(self.cfg.only_keys)
            unknown = sorted(wanted - {c.key for c in cands})
            if unknown:
                raise ValueError(f"pre-registered keys not among the candidates: {unknown}")
            cands = [c for c in cands if c.key in wanted]
        # Strategies with no arena timeframe produce no candidates at all; record why, per symbol,
        # so "why is S08 not here" always has an answer.
        have = {c.strategy_id for c in cands}
        for sid in sorted(classes):
            if sid in have:
                continue
            row = next(r for r in run.strategy_table if r["strategy_id"] == sid)
            tf = row["native_timeframe"] or (row["declared_timeframes"] or ["?"])[0]
            for sym in symbols:
                spec = BotSpec(sid, sym, tf, self.cfg.leverage_ceiling, self.cfg.profile,
                               self.cfg.params_version, getattr(classes[sid], "name", sid))
                pf = self.check(spec, classes[sid], universe)
                run.not_entered.append(BotResult(spec, entered=False, state="NOT_ENTERED",
                                                 reason=pf.reason or "no arena signal timeframe",
                                                 detail=pf.detail))
        passed: list[BotSpec] = []
        details: dict[str, dict[str, Any]] = {}
        for spec in cands:
            pf = self.check(spec, classes[spec.strategy_id], universe)
            if pf.ok:
                passed.append(spec)
                details[spec.key] = pf.detail
            else:
                run.not_entered.append(BotResult(spec, entered=False, state="NOT_ENTERED",
                                                 reason=pf.reason, detail=pf.detail))
        picked = select(passed, self.cfg.max_bots)
        chosen = {s.key for s in picked}
        run.eligible_not_selected = [s.key for s in passed if s.key not in chosen]
        for spec in picked:
            run.bots.append(BotResult(spec, version=spec.version(), detail=details.get(spec.key, {})))
        run.symbols = sorted({s.symbol for s in picked})
        return run

    def run(self, classes: dict[str, Any], symbols: Sequence[str], universe: Sequence[str],
            timeframes: Sequence[str] = ARENA_TIMEFRAMES,
            on_done: Callable[[BotResult, int, int], None] | None = None,
            workers: int = 1, version: str = "v1") -> ArenaRun:
        run = self.plan(classes, symbols, universe, timeframes)
        self._persist_start(run)
        if len(run.bots) < self.cfg.min_active_bots:
            run.status = "INSUFFICIENT_COMPETITORS"
            run.finished_ts = int(time.time() * 1000)
            log.warning("arena has %d active bots, below the minimum of %d; no winner declared",
                        len(run.bots), self.cfg.min_active_bots)
            self._persist_finish(run)
            return run

        def finish(br: BotResult, res: Any, m: Any, err: str, elapsed: float, i: int) -> None:
            if err:
                br.state, br.reason = "ERROR", err
            else:
                br.result, br.metrics = res, m
                br.state, br.gates, br.reasons = self.judge(br.spec, m)
                br.extra = entry_stats(res)
                if not m.trades:
                    rej = ", ".join(f"{k} x{v}" for k, v in sorted(res.rejects.items(),
                                                                     key=lambda kv: -kv[1]))
                    br.reasons = [f"0 trades from {res.signals} signals"
                                  + (f" (refused: {rej})" if rej else "")] + br.reasons[1:]
                run.bars += res.bars_seen
            br.elapsed_s = elapsed
            self._persist_bot(run, br)
            if on_done:
                on_done(br, i, len(run.bots))

        if workers <= 1:
            for i, br in enumerate(run.bots, 1):
                t0 = time.time()
                try:
                    res, m = self.run_bot(br.spec, classes[br.spec.strategy_id])
                    finish(br, res, m, "", time.time() - t0, i)
                except Exception as exc:                  # one bad bot must not void the arena
                    log.exception("%s failed", br.key)
                    finish(br, None, None, f"{type(exc).__name__}: {exc}"[:200], time.time() - t0, i)
        else:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor, as_completed
            by_key = {b.key: b for b in run.bots}
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker,
                                     initargs=(self.settings, self.cfg, self.months, self.rules,
                                               self.brackets, version)) as pool:
                futures = [pool.submit(_run_spec, dataclasses.asdict(b.spec)) for b in run.bots]
                for i, f in enumerate(as_completed(futures), 1):
                    key, res, m, err, elapsed = f.result()
                    finish(by_key[key], res, m, err, elapsed, i)
        run.status = "COMPLETE"
        run.finished_ts = int(time.time() * 1000)
        for n, b in enumerate(run.ranked(), 1):
            b.rank = n
            self._persist_bot(run, b)
        self._persist_finish(run)
        return run

    # -- persistence -----------------------------------------------------------------------------
    def _persist_start(self, run: ArenaRun) -> None:
        if self.storage is None:
            return
        self.storage.start_arena_run({
            "run_id": run.run_id, "created_ts": run.started_ts, "status": "running",
            "label": run.label, "config_fingerprint": self.cfg.fingerprint(),
            "first_month": self.months[0] if self.months else None,
            "last_month": self.months[-1] if self.months else None,
            "symbols": run.symbols,
            "timeframes": [tf for tf in ARENA_TIMEFRAMES if any(b.spec.timeframe == tf for b in run.bots)],
            "active_bots": len(run.bots), "not_entered": len(run.not_entered), "advanced": 0,
            "min_active_bots": self.cfg.min_active_bots,
            "config": {**self.cfg.to_dict(), "months": self.months,
                       "rules": {s: dataclasses.asdict(r) for s, r in self.rules.items()}},
            "summary": run.summary(), "preflight": run.preflight_report()})

    def _persist_bot(self, run: ArenaRun, br: BotResult) -> None:
        if self.storage is not None:
            self.storage.save_arena_bot(run.run_id, br.to_dict(heavy=True))

    def _persist_finish(self, run: ArenaRun) -> None:
        if self.storage is None:
            return
        s = run.summary()
        self.storage.update_arena_run(
            run.run_id, status=run.status.lower(),
            finished_ts=run.finished_ts, advanced=s["advanced"], active_bots=s["active_bots"],
            summary_json=s)
