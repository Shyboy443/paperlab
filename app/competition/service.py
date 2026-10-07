"""CompetitionService: runs seasons for the dashboard and keeps the results queryable.

Design constraints that come from the existing deployment, not from preference:

* **Railway has one web process and no job queue.** A season over a month of 1m bars takes minutes,
  so it cannot run inside an HTTP request without tripping the request timeout. It runs on a worker
  thread via `asyncio.to_thread`, the same shape the engine already uses for its boot task, and the
  route returns immediately with a run id.
* **Bars come from the existing `candles` table**, not from the data.binance.vision archive. The
  archive cache lives in the image (`<project>/data/klines`), not on the Railway volume, so pulling
  it at runtime would re-download on every deploy and write to ephemeral storage. The live feed
  already backfills candles into SQLite; that is the Railway-safe source.
* **Only one season runs at a time.** Two concurrent replays on a small container would starve the
  engine's event loop, and there is no scheduler to arbitrate.

Nothing here can place an order. A season is a replay against `ReplayEngine`, which has no router
and no client; `LIVE_CANDIDATE` is a label, and promotion stays behind the operator path in
`Engine.promote` / the GO LIVE arm phrase.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from app.backtest import brackets as bracket_mod
from app.backtest.funding import FundingSchedule
from app.competition.config import QualificationConfig, RiskConfig, Season
from app.competition.engine import CompetitionEngine, CompetitionRun
from app.core.types import Candle
from app.execution.config import ExecutionConfig, FeeSchedule

log = logging.getLogger("paperlab.competition.service")

# Named execution profiles the UI can offer. "old" exists only so the dashboard can show what
# unrealistic execution did to the leaderboard; it is never a default.
PROFILES: dict[str, ExecutionConfig] = {
    "realistic": ExecutionConfig(),
    "legacy": ExecutionConfig(level=3, base_slippage_bps=2.0, vol_component=0.0,
                              signal_latency_ms=0, order_latency_ms=0),
    "stress_slippage_2x": ExecutionConfig(slippage_mult=2.0),
    "stress_taker": ExecutionConfig(force_taker=True),
    "stress_latency": ExecutionConfig(signal_latency_ms=1000, order_latency_ms=500),
}
PROFILE_LABELS = {
    "realistic": "Realistic (tick spread, 400ms latency, bracket margin)",
    "legacy": "Legacy (flat 2bps, no latency, flat 2.5% margin)",
    "stress_slippage_2x": "Stress: 2x slippage",
    "stress_taker": "Stress: every fill taker",
    "stress_latency": "Stress: 1.5s latency",
}
# The legacy profile also needs the wrong maintenance margin to reproduce the old behaviour.
LEGACY_MMR = 0.025


@dataclass
class Progress:
    """What the dashboard polls while a season is running."""
    run_id: str = ""
    status: str = "idle"                 # idle | running | done | error | cancelled
    started_ts: int = 0
    finished_ts: int = 0
    bars: int = 0
    total_bots: int = 0
    bots_done: int = 0
    current_bot: str = ""
    trades: int = 0
    replay_ts: int = 0                   # the market timestamp last replayed
    error: str = ""
    partial: list[dict[str, Any]] = field(default_factory=list)   # temporary leaderboard

    @property
    def pct(self) -> float:
        return 0.0 if not self.total_bots else min(1.0, self.bots_done / self.total_bots)

    @property
    def elapsed_s(self) -> float:
        end = self.finished_ts or int(time.time() * 1000)
        return max(0.0, (end - self.started_ts) / 1000) if self.started_ts else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pct"] = self.pct
        d["elapsed_s"] = self.elapsed_s
        return d


class CompetitionService:
    def __init__(self, settings: Any, storage: Any, classes: dict[str, Any],
                 rules: dict[str, Any] | None = None):
        self.settings = settings
        self.storage = storage
        self.classes = classes
        self.rules = rules
        self.progress = Progress()
        self.latest: CompetitionRun | None = None
        self.latest_run_id: str = ""
        self._task: asyncio.Task | None = None
        self._cancel = False

    # -- availability ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.progress.status == "running"

    def data_report(self, symbols: Sequence[str], tf: str = "1m") -> dict[str, Any]:
        """What the candle store can actually back a season with, per symbol."""
        out: dict[str, Any] = {"timeframe": tf, "symbols": {}}
        lo_all: list[int] = []
        hi_all: list[int] = []
        for s in symbols:
            lo, hi, n = self.storage.candle_span(s, tf)
            out["symbols"][s] = {"first_ms": lo, "last_ms": hi, "bars": n}
            if lo is not None:
                lo_all.append(lo)
                hi_all.append(hi)
        out["overlap_start_ms"] = max(lo_all) if len(lo_all) == len(symbols) and lo_all else None
        out["overlap_end_ms"] = min(hi_all) if len(hi_all) == len(symbols) and hi_all else None
        out["ready"] = bool(out["overlap_start_ms"] and out["overlap_end_ms"]
                            and out["overlap_end_ms"] > out["overlap_start_ms"])
        return out

    def load_bars(self, symbols: Sequence[str], start_ms: int, end_ms: int,
                  tf: str = "1m") -> list[Candle]:
        """One merged tape, ordered exactly as the live feed would deliver it.

        Every competitor is handed this same immutable sequence, which is what makes the season
        fair -- no bot can see an event another did not.
        """
        merged: list[Candle] = []
        for s in symbols:
            merged.extend(self.storage.candles_between(s, tf, start_ms, end_ms))
        merged.sort(key=lambda c: (c.close_time, c.symbol))
        return merged

    # -- running -----------------------------------------------------------------------------
    def build_season(self, *, symbols: Sequence[str], start_ms: int, end_ms: int,
                     starting_balance: float = 20.0, max_leverage: int = 20,
                     profile: str = "realistic", label: str = "",
                     min_closed_trades: int | None = None, seed: int = 7) -> Season:
        execution = PROFILES.get(profile, PROFILES["realistic"])
        qual = QualificationConfig()
        if min_closed_trades is not None:
            qual = QualificationConfig(min_closed_trades=int(min_closed_trades))
        return Season(
            season_id=label or time.strftime("%Y%m%d-%H%M%S"),
            competition_id="paperlab-bake-off",
            start_ms=int(start_ms), end_ms=int(end_ms), symbols=tuple(symbols),
            label=label or "", seed=seed, fees=FeeSchedule(), execution=execution,
            risk=RiskConfig(starting_balance=float(starting_balance), max_leverage=int(max_leverage)),
            qualification=qual)

    def brackets_for(self, profile: str) -> bracket_mod.BracketTable:
        if profile == "legacy":
            return bracket_mod.BracketTable(
                {s: [bracket_mod.Bracket(float("inf"), LEGACY_MMR, 0.0)] for s in self.settings.symbols},
                source="legacy_exchangeInfo_default")
        try:
            return bracket_mod.load(self.settings.symbols, self.settings.data_dir)
        except Exception as exc:                                   # offline container
            log.warning("bracket fetch failed (%s); using the cached fallback table", exc)
            return bracket_mod.BracketTable.fallback()

    async def start(self, season: Season, profile: str, only: Sequence[str] | None = None) -> str:
        if self.running:
            raise RuntimeError("a competition is already running")
        run_id = uuid.uuid4().hex[:12]
        self._cancel = False
        self.progress = Progress(run_id=run_id, status="running",
                                 started_ts=int(time.time() * 1000))
        self._task = asyncio.create_task(self._run(run_id, season, profile, only),
                                         name=f"competition-{run_id}")
        return run_id

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancel = True
        return True

    async def _run(self, run_id: str, season: Season, profile: str,
                   only: Sequence[str] | None) -> None:
        try:
            bars = await asyncio.to_thread(self.load_bars, season.symbols, season.start_ms, season.end_ms)
            if not bars:
                raise ValueError("no candles stored for that window; let the feed backfill first")
            self.progress.bars = len(bars)
            run = await asyncio.to_thread(self._replay, run_id, season, profile, tuple(bars), only)
            self.latest = run
            self.latest_run_id = run_id
            self.progress.status = "cancelled" if self._cancel else "done"
            self.progress.finished_ts = int(time.time() * 1000)
            await asyncio.to_thread(self.persist, run_id, run, profile)
        except asyncio.CancelledError:
            self.progress.status = "cancelled"
            raise
        except Exception as exc:
            log.exception("competition %s failed", run_id)
            self.progress.status = "error"
            self.progress.error = f"{exc.__class__.__name__}: {exc}"[:300]
            self.progress.finished_ts = int(time.time() * 1000)

    def _replay(self, run_id: str, season: Season, profile: str, bars: tuple[Candle, ...],
                only: Sequence[str] | None) -> CompetitionRun:
        """Run competitors one at a time so the dashboard has a live, partial leaderboard.

        `CompetitionEngine.run` already loops over competitors; it is called once per competitor
        here instead, so progress can be reported and a cancel can take effect between bots. The
        engine still owns every decision -- this only chooses the order and watches.
        """
        engine = CompetitionEngine(self.settings, season, rules=self.rules,
                                   funding=self._funding(season),
                                   brackets=self.brackets_for(profile))
        wanted = list(only) if only else sorted(self.classes)
        self.progress.total_bots = len(wanted)
        self.progress.replay_ts = bars[-1].close_time if bars else 0
        run = CompetitionRun(season=season, fingerprint=season.fingerprint(), bars=len(bars))
        for sid in wanted:
            if self._cancel:
                break
            self.progress.current_bot = sid
            part = engine.run(self.classes, bars, only=[sid])
            for comp in part.competitors:
                run.competitors.append(comp)
                if comp.metrics:
                    self.progress.trades += comp.metrics.trades
            self.progress.bots_done += 1
            self.progress.partial = [c.row() for c in run.leaderboard("score")]
        return run

    def _funding(self, season: Season) -> FundingSchedule | None:
        """Funding settled from whatever the live feed has already persisted."""
        try:
            rows = self.storage.conn.execute(
                "SELECT symbol, ts, rate FROM funding WHERE ts>=? AND ts<=? ORDER BY ts",
                (season.start_ms, season.end_ms)).fetchall()
        except Exception:
            return None
        by: dict[str, list[tuple[int, float]]] = {}
        for r in rows:
            by.setdefault(r["symbol"], []).append((int(r["ts"]), float(r["rate"])))
        return FundingSchedule(by) if by else None

    # -- persistence -------------------------------------------------------------------------
    def persist(self, run_id: str, run: CompetitionRun, profile: str) -> None:
        season = run.season
        ranked = {c.strategy_id: i + 1 for i, c in enumerate(run.leaderboard("score"))}
        rows = []
        for c in run.competitors:
            rows.append({
                "strategy_id": c.strategy_id, "version": c.version, "name": c.name,
                "state": c.state, "rank": ranked.get(c.strategy_id), "score": c.score if c.metrics else None,
                "skipped": c.skipped,
                "metrics": c.metrics.to_dict() if c.metrics else None,
                "qualification": c.qualification.to_dict() if c.qualification else None,
                "score_breakdown": c.breakdown.to_dict() if c.breakdown else None,
                "equity": _downsample(c.result.equity) if c.result else None,
                "fills": [_fill_row(f) for f in c.result.fills] if c.result else None,
            })
        self.storage.save_competition({
            "run_id": run_id, "competition_id": season.competition_id, "season_id": season.season_id,
            "label": season.label, "fingerprint": run.fingerprint,
            "created_ts": self.progress.started_ts, "finished_ts": self.progress.finished_ts,
            "status": self.progress.status, "start_ms": season.start_ms, "end_ms": season.end_ms,
            "symbols": list(season.symbols), "bars": run.bars,
            "starting_balance": season.risk.starting_balance, "max_leverage": season.risk.max_leverage,
            "execution_profile": profile, "season": season.to_dict(), "summary": run.summary(),
            "error": self.progress.error or None,
        }, rows)


MAX_EQUITY_POINTS = 1200


def _downsample(points: Sequence[tuple[int, float]], limit: int = MAX_EQUITY_POINTS
                ) -> list[tuple[int, float]]:
    """Thin an equity curve for transport. A month of 1m bars is ~130k points; a chart needs ~1k.

    Keeps the first and last point exactly, so the curve's endpoints -- the numbers the leaderboard
    quotes -- always agree with the chart.
    """
    n = len(points)
    if n <= limit:
        return [(int(t), float(v)) for t, v in points]
    step = n / limit
    idx = sorted({int(i * step) for i in range(limit)} | {0, n - 1})
    return [(int(points[i][0]), float(points[i][1])) for i in idx]


def _fill_row(f: Any) -> dict[str, Any]:
    """One trade-ledger row, flattened for the API."""
    meta = f.meta or {}
    return {
        "id": f.id, "ts": f.ts, "symbol": f.symbol, "side": f.side, "kind": f.kind,
        "qty": f.qty, "price": f.price, "decision_price": f.ref_price, "fee": f.fee,
        "slippage_bps": f.slippage_bps, "realized_pnl": f.realized_pnl, "leverage": f.leverage,
        "position_side": f.position_side, "position_id": f.position_id, "is_open": bool(f.is_open),
        "liquidity_role": meta.get("liquidity_role"),
        "execution_level": meta.get("execution_level"),
        "latency_cost": meta.get("latency_cost"), "spread_cost": meta.get("spread_cost"),
        "impact_cost": meta.get("impact_cost"), "latency_ms": meta.get("latency_ms"),
        "reason": meta.get("reason"),
    }
