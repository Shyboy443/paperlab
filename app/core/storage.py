"""SQLite (WAL) persistence. Event-sourced: fills, signals, orders and events are append-only;
wallets are rebuilt from fills of the current epoch, open positions from their latest snapshot.

Used only from the asyncio thread (writes are tiny). `Storage(":memory:")` works for tests.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.core.storage_analysis import AnalysisQueries
from app.core.storage_candidates import CANDIDATE_DDL, CandidateQueries
from app.core.storage_shadow import SHADOW_DDL, ShadowQueries
from app.core.storage_v3 import V3_DDL, V3Queries
from app.core.storage_v31 import V31_DDL, V31Queries
from app.core.storage_v4 import V4_DDL, V4Queries
from app.core.storage_v5 import V5_DDL, V5Queries
from app.core.storage_fwd6 import FWD6_DDL, Fwd6Queries
from app.core.storage_mirror import MIRROR_DDL, MirrorQueries
from app.core.types import Candle, Fill, FundingInfo, RiskDecision, Signal, TakeProfit, TrailSpec, VirtualPosition

SCHEMA_VERSION = 17
EXPORTABLE = ("fills", "signals", "orders", "equity", "events", "candles", "funding",
              "strategy_daily", "analysis_notes", "competition_runs", "competition_competitors")

_DDL = [
    """CREATE TABLE IF NOT EXISTS candles(
        symbol TEXT NOT NULL, tf TEXT NOT NULL, open_time INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER,
        quote_volume REAL DEFAULT 0, trades INTEGER DEFAULT 0, source TEXT DEFAULT 'live',
        PRIMARY KEY(symbol, tf, open_time))""",
    """CREATE TABLE IF NOT EXISTS fills(
        id TEXT PRIMARY KEY, ts INTEGER, epoch INTEGER, strategy_id TEXT, symbol TEXT, position_id TEXT,
        side TEXT, qty REAL, price REAL, fee REAL, slippage_bps REAL, kind TEXT, realized_pnl REAL,
        simulated INTEGER, signal_id TEXT, exchange_order_id TEXT, meta_json TEXT, ref_price REAL,
        leverage INTEGER, position_side TEXT, is_open INTEGER)""",
    "CREATE INDEX IF NOT EXISTS fills_epoch_ts ON fills(epoch, ts)",
    """CREATE TABLE IF NOT EXISTS signals(
        id TEXT PRIMARY KEY, ts INTEGER, epoch INTEGER, strategy_id TEXT, symbol TEXT, kind TEXT, side TEXT,
        tf TEXT, entry_price REAL, stop REAL, tps_json TEXT, size_mult REAL, status TEXT, reason TEXT,
        risk_json TEXT, meta_json TEXT, leg TEXT)""",
    "CREATE INDEX IF NOT EXISTS signals_ts ON signals(ts)",
    """CREATE TABLE IF NOT EXISTS orders(
        client_id TEXT PRIMARY KEY, ts INTEGER, symbol TEXT, side TEXT, qty REAL, reduce_only INTEGER,
        purpose TEXT, exchange_order_id TEXT, status TEXT, avg_price REAL, executed_qty REAL, ref_price REAL,
        drift_bps REAL, error TEXT, raw_json TEXT, stop_price REAL)""",
    """CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, epoch INTEGER, kind TEXT, strategy_id TEXT,
        payload_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS equity(
        ts INTEGER, epoch INTEGER, series TEXT, equity REAL, upnl REAL, realized REAL,
        PRIMARY KEY(ts, series))""",
    """CREATE TABLE IF NOT EXISTS strategy_state(
        strategy_id TEXT PRIMARY KEY, enabled INTEGER, allocation REAL, leverage INTEGER, size_mult REAL,
        params_json TEXT, halted INTEGER DEFAULT 0, halt_floor REAL, updated_ts INTEGER)""",
    """CREATE TABLE IF NOT EXISTS funding(
        symbol TEXT, ts INTEGER, rate REAL, mark REAL, next_funding_ts INTEGER, PRIMARY KEY(symbol, ts))""",
    """CREATE TABLE IF NOT EXISTS positions_open(
        id TEXT PRIMARY KEY, epoch INTEGER, strategy_id TEXT, symbol TEXT, json TEXT, updated_ts INTEGER)""",
    """CREATE TABLE IF NOT EXISTS strategy_daily(
        day_utc TEXT NOT NULL, epoch INTEGER NOT NULL, strategy_id TEXT NOT NULL,
        start_equity REAL, end_equity REAL, realized REAL, fees REAL, funding REAL, upnl_eod REAL,
        trades INTEGER, wins INTEGER, losses INTEGER, win_rate REAL, profit_factor REAL, avg_r REAL,
        max_dd_pct REAL, expectancy REAL,
        signals_total INTEGER, signals_approved INTEGER, signals_rejected INTEGER, signals_warmup INTEGER,
        signals_stale INTEGER, time_in_market_s INTEGER, gross_notional_max REAL, reject_reasons_json TEXT,
        life INTEGER DEFAULT 1, updated_ts INTEGER,
        PRIMARY KEY(day_utc, epoch, strategy_id, life))""",
    """CREATE TABLE IF NOT EXISTS analysis_notes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, epoch INTEGER, strategy_id TEXT, symbol TEXT,
        author TEXT, kind TEXT, text TEXT)""",
    "CREATE INDEX IF NOT EXISTS notes_epoch_ts ON analysis_notes(epoch, ts)",
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
    # schema 5: bot-competition seasons. One row per run, one per competitor. The heavy artefacts
    # (equity curve, fill ledger) stay as JSON on the competitor row: a season is written once and
    # read whole, so a normalised fills table would buy nothing and cost a migration.
    """CREATE TABLE IF NOT EXISTS competition_runs(
        run_id TEXT PRIMARY KEY, competition_id TEXT, season_id TEXT, label TEXT,
        fingerprint TEXT, created_ts INTEGER, finished_ts INTEGER, status TEXT,
        start_ms INTEGER, end_ms INTEGER, symbols TEXT, bars INTEGER,
        starting_balance REAL, max_leverage INTEGER, execution_profile TEXT,
        season_json TEXT, summary_json TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS competition_runs_created ON competition_runs(created_ts)",
    """CREATE TABLE IF NOT EXISTS competition_competitors(
        run_id TEXT NOT NULL, strategy_id TEXT NOT NULL, version TEXT NOT NULL,
        name TEXT, state TEXT, rank INTEGER, score REAL, skipped TEXT,
        metrics_json TEXT, qualification_json TEXT, score_json TEXT,
        equity_json TEXT, fills_json TEXT,
        PRIMARY KEY(run_id, strategy_id, version))""",
    "CREATE INDEX IF NOT EXISTS competition_competitors_run ON competition_competitors(run_id)",
    # schema 6: multi-year validation runs (walk-forward -> Monte Carlo -> stress -> qualification).
    # Separate from competition_* because a validation run evaluates leverage identities across many
    # windows rather than one season, and is checkpointed competitor by competitor so a restart
    # resumes instead of repeating hours of replay.
    """CREATE TABLE IF NOT EXISTS validation_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT,
        config_fingerprint TEXT, dataset_fingerprint TEXT, symbols TEXT,
        first_month TEXT, last_month TEXT, windows INTEGER, total_competitors INTEGER,
        starting_balance REAL, leverages TEXT, config_json TEXT, summary_json TEXT,
        stage TEXT, progress_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS validation_competitors(
        run_id TEXT NOT NULL, key TEXT NOT NULL, strategy_id TEXT, version TEXT, leverage INTEGER,
        name TEXT, state TEXT, score REAL, oos_trades INTEGER, oos_net REAL, elapsed_s REAL,
        result_json TEXT, finished_ts INTEGER,
        PRIMARY KEY(run_id, key))""",
    "CREATE INDEX IF NOT EXISTS validation_competitors_run ON validation_competitors(run_id)",
    # schema 7: specialist bot arena (discovery). One row per run; one per SELECTED bot, i.e. the
    # active field. Candidates that failed preflight live in the run's preflight_json with their
    # reason, because they never ran and have no metrics to store.
    """CREATE TABLE IF NOT EXISTS arena_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT,
        label TEXT, config_fingerprint TEXT, first_month TEXT, last_month TEXT,
        symbols TEXT, timeframes TEXT, active_bots INTEGER, not_entered INTEGER,
        advanced INTEGER, min_active_bots INTEGER, config_json TEXT, summary_json TEXT,
        preflight_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS arena_bots(
        run_id TEXT NOT NULL, key TEXT NOT NULL, strategy_id TEXT, symbol TEXT, timeframe TEXT,
        max_leverage INTEGER, profile TEXT, params_version TEXT, version TEXT, name TEXT,
        state TEXT, rank INTEGER, net_profit REAL, trades INTEGER, result_json TEXT,
        finished_ts INTEGER, PRIMARY KEY(run_id, key))""",
    "CREATE INDEX IF NOT EXISTS arena_bots_run ON arena_bots(run_id)",
    # schema 8: CONTROL vs +JEV paired experiments. `jev_decisions` is the permanent decision ledger
    # of an experiment -- replaying it reproduces the run without a single network call.
    # `jev_cache` is shared across experiments so an identical question is never paid for twice.
    # Neither table has a column that could hold a credential.
    """CREATE TABLE IF NOT EXISTS jev_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT,
        label TEXT, arena_run_id TEXT, config_fingerprint TEXT, model TEXT, prompt_version TEXT,
        policy_version TEXT, first_month TEXT, last_month TEXT, pairs INTEGER,
        config_json TEXT, summary_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS jev_bots(
        run_id TEXT NOT NULL, key TEXT NOT NULL, pair_id TEXT, role TEXT, control_key TEXT,
        strategy_id TEXT, symbol TEXT, timeframe TEXT, state TEXT, net_profit REAL,
        trades INTEGER, result_json TEXT, finished_ts INTEGER, PRIMARY KEY(run_id, key))""",
    "CREATE INDEX IF NOT EXISTS jev_bots_run ON jev_bots(run_id)",
    """CREATE TABLE IF NOT EXISTS jev_decisions(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, bot_key TEXT, pair_id TEXT, cache_key TEXT,
        signal_ts INTEGER, symbol TEXT, timeframe TEXT, side TEXT, model_requested TEXT,
        model_resolved TEXT, prompt_version TEXT, policy_version TEXT, state_fingerprint TEXT,
        state_json TEXT, take_probability REAL, setup_quality REAL, risk_state TEXT, regime TEXT,
        answers_json TEXT, final_action TEXT, final_level TEXT, risk_multiplier REAL,
        request_latency_ms INTEGER, roundtrip_ms INTEGER, input_tokens INTEGER,
        output_tokens INTEGER, cost_usd REAL, cache_hit INTEGER, source TEXT, attempts INTEGER,
        error_code TEXT, error_message TEXT, created_ts INTEGER, result TEXT, outcome_kind TEXT,
        outcome_net REAL, outcome_r REAL, outcome_exit TEXT,
        UNIQUE(run_id, bot_key, cache_key))""",
    "CREATE INDEX IF NOT EXISTS jev_decisions_run ON jev_decisions(run_id, bot_key)",
    """CREATE TABLE IF NOT EXISTS jev_cache(
        cache_key TEXT PRIMARY KEY, model_requested TEXT, model_resolved TEXT,
        prompt_version TEXT, questions_fingerprint TEXT, state_fingerprint TEXT,
        outcome_json TEXT, created_ts INTEGER)""",
]

# additive migrations: (table, column, DDL type) applied when the column is missing
_ADD_COLUMNS = [
    ("candles", "source", "TEXT DEFAULT 'live'"),
    ("fills", "wallet_equity_after", "REAL"),
    ("fills", "wallet_upnl_after", "REAL"),
    ("orders", "epoch", "INTEGER"),
    ("signals", "epoch", "INTEGER"),
    ("events", "epoch", "INTEGER"),
    ("equity", "epoch", "INTEGER"),
    # schema 3: one book can live several lives (respawn after a -25% death)
    ("fills", "life", "INTEGER DEFAULT 1"),
    ("signals", "life", "INTEGER DEFAULT 1"),
    ("strategy_state", "life", "INTEGER DEFAULT 1"),
    ("strategy_state", "lives_today", "INTEGER DEFAULT 0"),
    ("strategy_state", "lives_day", "TEXT"),
    ("strategy_state", "realized_all_lives", "REAL DEFAULT 0"),
    ("strategy_state", "params_custom_json", "TEXT"),
    # schema 10: live shadow sessions belong to a FORWARD EXPERIMENT (compatible sessions stitch)
    ("shadow_sessions", "experiment_id", "TEXT"),
    ("shadow_sessions", "experiment_json", "TEXT"),
    # schema 11: a resumed experiment re-derives its book; trades no session observed are flagged
    ("shadow_trades", "rederived", "INTEGER DEFAULT 0"),
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def position_to_dict(pos: VirtualPosition) -> dict[str, Any]:
    return dataclasses.asdict(pos)


def position_from_dict(d: dict[str, Any]) -> VirtualPosition:
    d = dict(d)
    d["take_profits"] = [TakeProfit(**tp) for tp in d.get("take_profits") or []]
    trail = d.get("trail")
    d["trail"] = TrailSpec(**trail) if trail else None
    return VirtualPosition(**d)


def _fill_event(f: Fill) -> dict[str, Any]:
    """The compact form a fill is streamed in (the full row stays a REST read)."""
    meta = f.meta or {}
    return {"id": f.id, "ts": f.ts, "strategy_id": f.strategy_id, "symbol": f.symbol, "side": f.side,
            "qty": f.qty, "price": f.price, "fee": f.fee, "kind": f.kind, "pnl": f.realized_pnl,
            "is_open": f.is_open, "position_side": f.position_side, "reason": meta.get("reason"),
            "r": meta.get("r"), "simulated": f.simulated}


class Storage(AnalysisQueries, ShadowQueries, CandidateQueries, V3Queries, V31Queries, V4Queries, V5Queries,
              Fwd6Queries, MirrorQueries):
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        # Write listener for the realtime stream: called AFTER a fill, signal or note is stored, so the
        # browser is only ever told about rows that exist. Set by the app; tests and scripts leave it None.
        self.on_write: Any = None
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    # -- schema -------------------------------------------------------------------
    def _migrate(self) -> None:
        for ddl in _DDL + SHADOW_DDL + CANDIDATE_DDL + V3_DDL + V31_DDL + V4_DDL + V5_DDL + FWD6_DDL + MIRROR_DDL:
            self.conn.execute(ddl)
        for table, column, decl in _ADD_COLUMNS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if cols and column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._migrate_strategy_daily_pk()
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        if self.get_meta("epoch") is None:
            self.set_meta("epoch", "1")

    def _migrate_strategy_daily_pk(self) -> None:
        """schema 4: `life` joins the primary key so a book that dies and respawns on the SAME day keeps
        one row per life. Without it the respawned life overwrote the dead one and the graveyard vanished."""
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategy_daily'").fetchone()
        if not sql or "strategy_id, life)" in (sql["sql"] or ""):
            return
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(strategy_daily)")]
        if "life" not in cols:
            self.conn.execute("ALTER TABLE strategy_daily ADD COLUMN life INTEGER DEFAULT 1")
            cols.append("life")
        shared = ",".join(c for c in cols if c != "life") + ",life"
        self.conn.execute("ALTER TABLE strategy_daily RENAME TO strategy_daily_old")
        for ddl in _DDL:
            if "CREATE TABLE IF NOT EXISTS strategy_daily(" in ddl:
                self.conn.execute(ddl)
                break
        self.conn.execute(f"INSERT OR REPLACE INTO strategy_daily({shared}) "
                          f"SELECT {shared} FROM strategy_daily_old")
        self.conn.execute("DROP TABLE strategy_daily_old")

    # -- meta ---------------------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else row["value"]

    def epoch(self) -> int:
        return int(self.get_meta("epoch", "1") or 1)

    def bump_epoch(self) -> int:
        new = self.epoch() + 1
        self.set_meta("epoch", str(new))
        return new

    # -- candles ----------------------------------------------------------------------
    def insert_candles(self, rows: Iterable[Candle]) -> int:
        data = [(c.symbol, c.tf, c.open_time, c.open, c.high, c.low, c.close, c.volume, c.close_time,
                 c.quote_volume, c.trades, c.source) for c in rows if c.closed]
        if not data:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO candles(symbol,tf,open_time,open,high,low,close,volume,close_time,"
            "quote_volume,trades,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", data)
        return len(data)

    def candles(self, symbol: str, tf: str, limit: int = 600, before_open_time: int | None = None) -> list[Candle]:
        q = "SELECT * FROM candles WHERE symbol=? AND tf=?"
        args: list[Any] = [symbol, tf]
        if before_open_time is not None:
            q += " AND open_time<?"
            args.append(before_open_time)
        q += " ORDER BY open_time DESC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(q, args).fetchall()
        return [Candle(r["symbol"], r["tf"], r["open_time"], r["open"], r["high"], r["low"], r["close"],
                       r["volume"], r["close_time"], True, r["quote_volume"] or 0.0, r["trades"] or 0,
                       r["source"] or "live") for r in reversed(rows)]

    def candles_between(self, symbol: str, tf: str, start_ms: int, end_ms: int,
                        limit: int = 2_000_000) -> list[Candle]:
        """Closed candles in [start_ms, end_ms] by open_time, oldest first.

        `candles()` walks backwards from the newest bar, which is what a live dashboard wants. A
        competition replays a fixed historical window forwards, so it needs a range query instead.
        """
        rows = self.conn.execute(
            "SELECT * FROM candles WHERE symbol=? AND tf=? AND open_time>=? AND open_time<=? "
            "ORDER BY open_time ASC LIMIT ?", (symbol, tf, start_ms, end_ms, limit)).fetchall()
        return [Candle(r["symbol"], r["tf"], r["open_time"], r["open"], r["high"], r["low"], r["close"],
                       r["volume"], r["close_time"], True, r["quote_volume"] or 0.0, r["trades"] or 0,
                       r["source"] or "live") for r in rows]

    def candle_span(self, symbol: str, tf: str) -> tuple[int | None, int | None, int]:
        """(first open_time, last open_time, count) for a symbol/timeframe."""
        row = self.conn.execute(
            "SELECT MIN(open_time) AS lo, MAX(open_time) AS hi, COUNT(*) AS n "
            "FROM candles WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
        if row is None or row["n"] == 0:
            return None, None, 0
        return int(row["lo"]), int(row["hi"]), int(row["n"])

    def latest_candle_open_time(self, symbol: str, tf: str, exclude_synthetic: bool = True) -> int | None:
        q = "SELECT MAX(open_time) AS m FROM candles WHERE symbol=? AND tf=?"
        if exclude_synthetic:
            q += " AND source<>'synthetic'"
        row = self.conn.execute(q, (symbol, tf)).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def delete_synthetic(self, symbol: str, tf: str) -> int:
        cur = self.conn.execute("DELETE FROM candles WHERE symbol=? AND tf=? AND source='synthetic'", (symbol, tf))
        return cur.rowcount

    def candle_counts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT symbol, tf, source, COUNT(*) AS n, MIN(open_time) AS first, MAX(open_time) AS last "
            "FROM candles GROUP BY symbol, tf, source").fetchall()
        return [dict(r) for r in rows]

    # -- fills / positions (one transaction) ------------------------------------------------
    def record_fill(self, fill: Fill, snapshot: VirtualPosition | None = None,
                    delete_position_id: str | None = None) -> None:
        self.conn.execute("BEGIN")
        try:
            self._insert_fill(fill)
            if snapshot is not None:
                self.upsert_position(snapshot, fill.epoch)
            if delete_position_id is not None:
                self.conn.execute("DELETE FROM positions_open WHERE id=?", (delete_position_id,))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self._emit("fill", _fill_event(fill))

    def _insert_fill(self, f: Fill) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO fills(id,ts,epoch,strategy_id,symbol,position_id,side,qty,price,fee,slippage_bps,"
            "kind,realized_pnl,simulated,signal_id,exchange_order_id,meta_json,ref_price,leverage,position_side,"
            "is_open,wallet_equity_after,wallet_upnl_after,life) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f.id, f.ts, f.epoch, f.strategy_id, f.symbol, f.position_id, f.side, f.qty, f.price, f.fee,
             f.slippage_bps, f.kind, f.realized_pnl, int(f.simulated), f.signal_id, f.exchange_order_id,
             _dumps(f.meta), f.ref_price, f.leverage, f.position_side, int(f.is_open),
             f.wallet_equity_after, f.wallet_upnl_after, f.life))

    def insert_fill(self, f: Fill) -> None:
        self._insert_fill(f)
        self._emit("fill", _fill_event(f))

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        """Tell the realtime stream about a stored row. Never raises: a slow or broken listener must
        not be able to fail a write."""
        cb = self.on_write
        if cb is None:
            return
        try:
            cb(kind, data)
        except Exception:
            pass

    def fills_since(self, epoch: int, limit: int | None = None) -> list[Fill]:
        q = "SELECT * FROM fills WHERE epoch=? ORDER BY ts ASC, rowid ASC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [self._row_to_fill(r) for r in self.conn.execute(q, (epoch,)).fetchall()]

    def fills_recent(self, limit: int = 50, strategy_id: str | None = None, epoch: int | None = None) -> list[Fill]:
        q = "SELECT * FROM fills WHERE 1=1"
        args: list[Any] = []
        if epoch is not None:
            q += " AND epoch=?"
            args.append(epoch)
        if strategy_id:
            q += " AND strategy_id=?"
            args.append(strategy_id)
        q += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_fill(r) for r in self.conn.execute(q, args).fetchall()]

    @staticmethod
    def _row_to_fill(r: sqlite3.Row) -> Fill:
        return Fill(id=r["id"], ts=r["ts"], epoch=r["epoch"], strategy_id=r["strategy_id"], symbol=r["symbol"],
                    position_id=r["position_id"], side=r["side"], qty=r["qty"], price=r["price"], fee=r["fee"],
                    slippage_bps=r["slippage_bps"], kind=r["kind"], realized_pnl=r["realized_pnl"],
                    simulated=bool(r["simulated"]), signal_id=r["signal_id"], exchange_order_id=r["exchange_order_id"],
                    meta=json.loads(r["meta_json"] or "{}"), ref_price=r["ref_price"] or 0.0,
                    leverage=r["leverage"] or 1, position_side=r["position_side"] or "long", is_open=bool(r["is_open"]),
                    wallet_equity_after=r["wallet_equity_after"] or 0.0,
                    wallet_upnl_after=r["wallet_upnl_after"] or 0.0, life=int(r["life"] or 1))

    def upsert_position(self, pos: VirtualPosition, epoch: int) -> None:
        self.conn.execute(
            "INSERT INTO positions_open(id,epoch,strategy_id,symbol,json,updated_ts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET json=excluded.json, updated_ts=excluded.updated_ts",
            (pos.id, epoch, pos.strategy_id, pos.symbol, _dumps(position_to_dict(pos)), _now_ms()))

    def delete_position(self, position_id: str) -> None:
        self.conn.execute("DELETE FROM positions_open WHERE id=?", (position_id,))

    def open_positions(self, epoch: int) -> list[VirtualPosition]:
        rows = self.conn.execute("SELECT json FROM positions_open WHERE epoch=?", (epoch,)).fetchall()
        return [position_from_dict(json.loads(r["json"])) for r in rows]

    def clear_positions(self) -> None:
        self.conn.execute("DELETE FROM positions_open")

    # -- signals ----------------------------------------------------------------------
    def insert_signal(self, s: Signal, status: str, reason: str, epoch: int, risk: RiskDecision | None = None,
                      life: int = 1) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO signals(id,ts,epoch,strategy_id,symbol,kind,side,tf,entry_price,stop,tps_json,"
            "size_mult,status,reason,risk_json,meta_json,leg,life) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.ts, epoch, s.strategy_id, s.symbol, s.kind, s.side, s.tf, s.entry_price, s.stop,
             _dumps([[tp.price, tp.fraction] for tp in s.take_profits]), s.size_mult, status, reason,
             _dumps(risk.to_dict()) if risk else None, _dumps(s.meta), s.leg, life))
        self._emit("signal", {"id": s.id, "ts": s.ts, "strategy_id": s.strategy_id, "symbol": s.symbol,
                              "kind": s.kind, "side": s.side, "tf": s.tf, "price": s.entry_price,
                              "status": status, "reason": (reason or "")[:160], "leg": s.leg})

    def signals_recent(self, limit: int = 200, strategy_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM signals"
        args: list[Any] = []
        if strategy_id:
            q += " WHERE strategy_id=?"
            args.append(strategy_id)
        q += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            d["tps"] = json.loads(d.pop("tps_json") or "[]")
            d["risk"] = json.loads(d.pop("risk_json") or "null")
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
            out.append(d)
        return out

    # -- orders -----------------------------------------------------------------------------
    def insert_order(self, o: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO orders(client_id,ts,epoch,symbol,side,qty,reduce_only,purpose,exchange_order_id,"
            "status,avg_price,executed_qty,ref_price,drift_bps,error,raw_json,stop_price) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["client_id"], o.get("ts", _now_ms()), o.get("epoch"), o["symbol"], o["side"], o["qty"],
             int(o.get("reduce_only", False)), o.get("purpose", "net_delta"), o.get("exchange_order_id"),
             o.get("status", "NEW"), o.get("avg_price"), o.get("executed_qty", 0.0), o.get("ref_price"),
             o.get("drift_bps"), o.get("error"), _dumps(o.get("raw", {})), o.get("stop_price")))

    def update_order(self, client_id: str, **fields: Any) -> None:
        if not fields:
            return
        if "raw" in fields:
            fields["raw_json"] = _dumps(fields.pop("raw"))
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE orders SET {cols} WHERE client_id=?", (*fields.values(), client_id))

    def orders_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("raw_json", None)
            out.append(d)
        return out

    # -- events / equity / funding ----------------------------------------------------
    def insert_event(self, kind: str, payload: dict[str, Any], epoch: int, strategy_id: str | None = None,
                     ts: int | None = None) -> int:
        cur = self.conn.execute("INSERT INTO events(ts,epoch,kind,strategy_id,payload_json) VALUES(?,?,?,?,?)",
                                (ts if ts is not None else _now_ms(), epoch, kind, strategy_id, _dumps(payload)))
        return int(cur.lastrowid or 0)

    def events_recent(self, limit: int = 100, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM events"
        args: list[Any] = []
        if kinds:
            ks = list(kinds)
            q += " WHERE kind IN (%s)" % ",".join("?" * len(ks))
            args += ks
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json") or "{}")
            out.append(d)
        return out

    # -- competition seasons ---------------------------------------------------------------
    def save_competition(self, run: dict[str, Any], competitors: Iterable[dict[str, Any]]) -> None:
        """Write a finished season. Idempotent on run_id so a re-run overwrites cleanly."""
        rid = run["run_id"]
        with self.conn:
            self.conn.execute("DELETE FROM competition_competitors WHERE run_id=?", (rid,))
            self.conn.execute(
                "INSERT OR REPLACE INTO competition_runs(run_id, competition_id, season_id, label, "
                "fingerprint, created_ts, finished_ts, status, start_ms, end_ms, symbols, bars, "
                "starting_balance, max_leverage, execution_profile, season_json, summary_json, error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, run.get("competition_id"), run.get("season_id"), run.get("label"),
                 run.get("fingerprint"), run.get("created_ts"), run.get("finished_ts"),
                 run.get("status"), run.get("start_ms"), run.get("end_ms"),
                 ",".join(run.get("symbols") or ()), run.get("bars"), run.get("starting_balance"),
                 run.get("max_leverage"), run.get("execution_profile"),
                 _dumps(run.get("season")), _dumps(run.get("summary")), run.get("error")))
            for c in competitors:
                self.conn.execute(
                    "INSERT OR REPLACE INTO competition_competitors(run_id, strategy_id, version, name, "
                    "state, rank, score, skipped, metrics_json, qualification_json, score_json, "
                    "equity_json, fills_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, c["strategy_id"], c.get("version", ""), c.get("name"), c.get("state"),
                     c.get("rank"), c.get("score"), c.get("skipped"), _dumps(c.get("metrics")),
                     _dumps(c.get("qualification")), _dumps(c.get("score_breakdown")),
                     _dumps(c.get("equity")), _dumps(c.get("fills"))))

    def competition_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT run_id, competition_id, season_id, label, fingerprint, created_ts, finished_ts, "
            "status, start_ms, end_ms, symbols, bars, starting_balance, max_leverage, "
            "execution_profile, summary_json, error FROM competition_runs "
            "ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["symbols"] = [s for s in (d.pop("symbols") or "").split(",") if s]
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            out.append(d)
        return out

    def competition_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM competition_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["symbols"] = [s for s in (d.pop("symbols") or "").split(",") if s]
        d["season"] = json.loads(d.pop("season_json") or "null")
        d["summary"] = json.loads(d.pop("summary_json") or "null")
        return d

    def competition_competitors(self, run_id: str, heavy: bool = False) -> list[dict[str, Any]]:
        """Competitor rows for a season. `heavy` also loads the equity curve and fill ledger, which
        are large -- the leaderboard never needs them."""
        cols = ("run_id, strategy_id, version, name, state, rank, score, skipped, metrics_json, "
                "qualification_json, score_json") + (", equity_json, fills_json" if heavy else "")
        rows = self.conn.execute(
            f"SELECT {cols} FROM competition_competitors WHERE run_id=? ORDER BY rank ASC",
            (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for src, dst in (("metrics_json", "metrics"), ("qualification_json", "qualification"),
                             ("score_json", "score_breakdown"), ("equity_json", "equity"),
                             ("fills_json", "fills")):
                if src in d:
                    d[dst] = json.loads(d.pop(src) or "null")
            out.append(d)
        return out

    def competition_competitor(self, run_id: str, strategy_id: str) -> dict[str, Any] | None:
        rows = [c for c in self.competition_competitors(run_id, heavy=True)
                if c["strategy_id"] == strategy_id]
        return rows[0] if rows else None

    # -- validation runs (multi-year walk-forward) ------------------------------------------
    def start_validation_run(self, run: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO validation_runs(run_id, created_ts, finished_ts, status, "
                "config_fingerprint, dataset_fingerprint, symbols, first_month, last_month, windows, "
                "total_competitors, starting_balance, leverages, config_json, summary_json, stage, "
                "progress_json, error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["run_id"], run.get("created_ts"), run.get("finished_ts"), run.get("status"),
                 run.get("config_fingerprint"), run.get("dataset_fingerprint"),
                 ",".join(run.get("symbols") or ()), run.get("first_month"), run.get("last_month"),
                 run.get("windows"), run.get("total_competitors"), run.get("starting_balance"),
                 ",".join(str(x) for x in (run.get("leverages") or ())),
                 _dumps(run.get("config")), _dumps(run.get("summary")), run.get("stage"),
                 _dumps(run.get("progress")), run.get("error")))

    def update_validation_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, args = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(_dumps(v) if k.endswith("_json") or isinstance(v, (dict, list)) else v)
        args.append(run_id)
        with self.conn:
            self.conn.execute(f"UPDATE validation_runs SET {', '.join(cols)} WHERE run_id=?", args)

    def save_validation_competitor(self, run_id: str, cv: dict[str, Any]) -> None:
        """Checkpoint one finished competitor. Called as soon as it completes, so a crash at 82%
        loses at most the competitor in flight."""
        m = cv.get("oos_metrics") or {}
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO validation_competitors(run_id, key, strategy_id, version, "
                "leverage, name, state, score, oos_trades, oos_net, elapsed_s, result_json, "
                "finished_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, cv["key"], cv.get("strategy_id"), cv.get("version"), cv.get("leverage"),
                 cv.get("name"), cv.get("state"), (cv.get("score") or {}).get("total"),
                 m.get("trades"), m.get("net_profit"), cv.get("elapsed_s"), _dumps(cv),
                 _now_ms()))

    def validation_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT run_id, created_ts, finished_ts, status, config_fingerprint, "
            "dataset_fingerprint, symbols, first_month, last_month, windows, total_competitors, "
            "starting_balance, leverages, summary_json, stage, progress_json, error "
            "FROM validation_runs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["symbols"] = [s for s in (d.pop("symbols") or "").split(",") if s]
            d["leverages"] = [int(x) for x in (d.pop("leverages") or "").split(",") if x]
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            d["progress"] = json.loads(d.pop("progress_json") or "null")
            out.append(d)
        return out

    def validation_run(self, run_id: str) -> dict[str, Any] | None:
        rows = [r for r in self.validation_runs(limit=500) if r["run_id"] == run_id]
        return rows[0] if rows else None

    def validation_competitors(self, run_id: str, heavy: bool = False) -> list[dict[str, Any]]:
        cols = "key, strategy_id, version, leverage, name, state, score, oos_trades, oos_net, elapsed_s"
        if heavy:
            cols += ", result_json"
        rows = self.conn.execute(
            f"SELECT {cols} FROM validation_competitors WHERE run_id=? ORDER BY score DESC",
            (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if "result_json" in d:
                d["result"] = json.loads(d.pop("result_json") or "null")
            out.append(d)
        return out

    def validation_competitor(self, run_id: str, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT result_json FROM validation_competitors WHERE run_id=? AND key=?",
            (run_id, key)).fetchone()
        return json.loads(row["result_json"] or "null") if row else None

    def validation_done_keys(self, run_id: str) -> set[str]:
        """Which competitors a resume may skip."""
        return {r["key"] for r in self.conn.execute(
            "SELECT key FROM validation_competitors WHERE run_id=?", (run_id,)).fetchall()}

    # -- specialist arena (discovery) ------------------------------------------------------------
    def start_arena_run(self, run: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO arena_runs(run_id, created_ts, finished_ts, status, label, "
                "config_fingerprint, first_month, last_month, symbols, timeframes, active_bots, "
                "not_entered, advanced, min_active_bots, config_json, summary_json, preflight_json, "
                "error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["run_id"], run.get("created_ts"), run.get("finished_ts"), run.get("status"),
                 run.get("label"), run.get("config_fingerprint"), run.get("first_month"),
                 run.get("last_month"), ",".join(run.get("symbols") or ()),
                 ",".join(run.get("timeframes") or ()), run.get("active_bots"),
                 run.get("not_entered"), run.get("advanced"), run.get("min_active_bots"),
                 _dumps(run.get("config")), _dumps(run.get("summary")),
                 _dumps(run.get("preflight")), run.get("error")))

    def update_arena_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, args = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(_dumps(v) if k.endswith("_json") or isinstance(v, (dict, list)) else v)
        args.append(run_id)
        with self.conn:
            self.conn.execute(f"UPDATE arena_runs SET {', '.join(cols)} WHERE run_id=?", args)

    def save_arena_bot(self, run_id: str, bot: dict[str, Any]) -> None:
        """Checkpoint one bot as soon as it finishes; re-saved once ranks are known."""
        m = bot.get("metrics") or {}
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO arena_bots(run_id, key, strategy_id, symbol, timeframe, "
                "max_leverage, profile, params_version, version, name, state, rank, net_profit, "
                "trades, result_json, finished_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, bot["key"], bot.get("strategy_id"), bot.get("symbol"),
                 bot.get("timeframe"), bot.get("max_leverage"), bot.get("profile"),
                 bot.get("params_version"), bot.get("version"), bot.get("name"), bot.get("state"),
                 bot.get("rank"), m.get("net_profit"), m.get("trades"), _dumps(bot), _now_ms()))

    def arena_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT run_id, created_ts, finished_ts, status, label, config_fingerprint, "
            "first_month, last_month, symbols, timeframes, active_bots, not_entered, advanced, "
            "min_active_bots, summary_json, error FROM arena_runs ORDER BY created_ts DESC LIMIT ?",
            (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["symbols"] = [x for x in (d.pop("symbols") or "").split(",") if x]
            d["timeframes"] = [x for x in (d.pop("timeframes") or "").split(",") if x]
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            out.append(d)
        return out

    def arena_run(self, run_id: str, heavy: bool = False) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM arena_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["symbols"] = [x for x in (d.pop("symbols") or "").split(",") if x]
        d["timeframes"] = [x for x in (d.pop("timeframes") or "").split(",") if x]
        d["summary"] = json.loads(d.pop("summary_json") or "null")
        config = json.loads(d.pop("config_json") or "null")
        preflight = json.loads(d.pop("preflight_json") or "null")
        if heavy:
            d["config"], d["preflight"] = config, preflight
        return d

    def arena_bots(self, run_id: str, heavy: bool = False) -> list[dict[str, Any]]:
        """The field, best rank first. Light rows drop the equity curve and the trade ledger."""
        rows = self.conn.execute(
            "SELECT result_json FROM arena_bots WHERE run_id=? "
            "ORDER BY CASE WHEN rank IS NULL OR rank=0 THEN 1 ELSE 0 END, rank ASC, key ASC",
            (run_id,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["result_json"] or "null") or {}
            if not heavy:
                d.pop("equity", None)
                d.pop("trades_ledger", None)
            out.append(d)
        return out

    def arena_bot(self, run_id: str, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT result_json FROM arena_bots WHERE run_id=? AND key=?",
                                (run_id, key)).fetchone()
        return json.loads(row["result_json"] or "null") if row else None

    # -- Jev paired experiments ----------------------------------------------------------------
    def start_jev_run(self, run: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO jev_runs(run_id, created_ts, finished_ts, status, label, "
                "arena_run_id, config_fingerprint, model, prompt_version, policy_version, first_month, "
                "last_month, pairs, config_json, summary_json, error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["run_id"], run.get("created_ts"), run.get("finished_ts"), run.get("status"),
                 run.get("label"), run.get("arena_run_id"), run.get("config_fingerprint"),
                 run.get("model"), run.get("prompt_version"), run.get("policy_version"),
                 run.get("first_month"), run.get("last_month"), run.get("pairs"),
                 _dumps(run.get("config")), _dumps(run.get("summary")), run.get("error")))

    def update_jev_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, args = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(_dumps(v) if k.endswith("_json") or isinstance(v, (dict, list)) else v)
        args.append(run_id)
        with self.conn:
            self.conn.execute(f"UPDATE jev_runs SET {', '.join(cols)} WHERE run_id=?", args)

    def jev_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT run_id, created_ts, finished_ts, status, label, arena_run_id, config_fingerprint, "
            "model, prompt_version, policy_version, first_month, last_month, pairs, summary_json, error "
            "FROM jev_runs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            out.append(d)
        return out

    def jev_run(self, run_id: str, heavy: bool = False) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jev_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["summary"] = json.loads(d.pop("summary_json") or "null")
        cfg = json.loads(d.pop("config_json") or "null")
        if heavy:
            d["config"] = cfg
        return d

    def save_jev_bot(self, run_id: str, bot: dict[str, Any]) -> None:
        m = bot.get("metrics") or {}
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO jev_bots(run_id, key, pair_id, role, control_key, strategy_id, "
                "symbol, timeframe, state, net_profit, trades, result_json, finished_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, bot["key"], bot.get("pair_id"), bot.get("role"), bot.get("control_key"),
                 bot.get("strategy_id"), bot.get("symbol"), bot.get("timeframe"), bot.get("state"),
                 m.get("net_profit"), m.get("trades"), _dumps(bot), _now_ms()))

    def jev_bots(self, run_id: str, heavy: bool = False) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT result_json FROM jev_bots WHERE run_id=? ORDER BY pair_id, role",
                                 (run_id,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["result_json"] or "null") or {}
            if not heavy:
                d.pop("equity", None)
                d.pop("trades_ledger", None)
            out.append(d)
        return out

    def jev_bot(self, run_id: str, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT result_json FROM jev_bots WHERE run_id=? AND key=?",
                                (run_id, key)).fetchone()
        return json.loads(row["result_json"] or "null") if row else None

    JEV_DECISION_COLS = ("id", "run_id", "bot_key", "pair_id", "cache_key", "signal_ts", "symbol",
                         "timeframe", "side", "model_requested", "model_resolved", "prompt_version",
                         "policy_version", "state_fingerprint", "state_json", "take_probability",
                         "setup_quality", "risk_state", "regime", "answers_json", "final_action",
                         "final_level", "risk_multiplier", "request_latency_ms", "roundtrip_ms",
                         "input_tokens", "output_tokens", "cost_usd", "cache_hit", "source",
                         "attempts", "error_code", "error_message", "created_ts", "result",
                         "outcome_kind", "outcome_net", "outcome_r", "outcome_exit")

    def save_jev_decision(self, d: dict[str, Any]) -> None:
        cols = self.JEV_DECISION_COLS
        row = [(_dumps(d.get(c)) if c.endswith("_json") and not isinstance(d.get(c), (str, type(None)))
                else d.get(c)) for c in cols]
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO jev_decisions({','.join(cols)}) "
                              f"VALUES({','.join('?' for _ in cols)})", row)

    def jev_ledger_lookup(self, run_id: str, bot_key: str, cache_key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jev_decisions WHERE run_id=? AND bot_key=? AND cache_key=?",
                                (run_id, bot_key, cache_key)).fetchone()
        return dict(row) if row else None

    def update_jev_outcome(self, decision_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE jev_decisions SET {cols} WHERE id=?", [*fields.values(), decision_id])

    def jev_decisions(self, run_id: str, bot_key: str | None = None, limit: int = 100_000,
                      offset: int = 0) -> list[dict[str, Any]]:
        q = "SELECT * FROM jev_decisions WHERE run_id=?"
        args: list[Any] = [run_id]
        if bot_key:
            q += " AND bot_key=?"
            args.append(bot_key)
        q += " ORDER BY bot_key, signal_ts LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def jev_cache_get(self, cache_key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jev_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return dict(row) if row else None

    def jev_cache_put(self, row: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO jev_cache(cache_key, model_requested, model_resolved, "
                "prompt_version, questions_fingerprint, state_fingerprint, outcome_json, created_ts) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (row["cache_key"], row.get("model_requested"), row.get("model_resolved"),
                 row.get("prompt_version"), row.get("questions_fingerprint"),
                 row.get("state_fingerprint"), _dumps(row.get("outcome")), _now_ms()))

    def insert_equity(self, ts: int, epoch: int, series: str, equity: float, upnl: float, realized: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO equity(ts,epoch,series,equity,upnl,realized) VALUES(?,?,?,?,?,?)",
                          (ts, epoch, series, equity, upnl, realized))

    def insert_equity_many(self, rows: Iterable[tuple[int, int, str, float, float, float]]) -> None:
        rows = list(rows)
        self.conn.executemany("INSERT OR REPLACE INTO equity(ts,epoch,series,equity,upnl,realized) VALUES(?,?,?,?,?,?)",
                              rows)
        if rows:           # tells an open equity chart that a new point exists (it fetches it via REST)
            self._emit("equity", {"ts": rows[0][0], "series": len(rows)})

    def equity_series(self, series: str, epoch: int, since_ts: int = 0, max_points: int = 600) -> list[list[float]]:
        rows = self.conn.execute(
            "SELECT ts, equity FROM equity WHERE series=? AND epoch=? AND ts>=? ORDER BY ts ASC",
            (series, epoch, since_ts)).fetchall()
        pts = [[int(r["ts"]), float(r["equity"])] for r in rows]
        if len(pts) <= max_points or max_points <= 0:
            return pts
        stride = len(pts) / max_points
        sampled = [pts[int(i * stride)] for i in range(max_points)]
        if sampled[-1] is not pts[-1]:
            sampled.append(pts[-1])
        return sampled

    def equity_extremes(self, series: str, epoch: int) -> tuple[float | None, float | None]:
        row = self.conn.execute("SELECT MAX(equity) AS mx, MIN(equity) AS mn FROM equity WHERE series=? AND epoch=?",
                                (series, epoch)).fetchone()
        return (row["mx"], row["mn"]) if row else (None, None)

    def equity_max_drawdown(self, series: str, epoch: int) -> float:
        peak = None
        worst = 0.0
        for r in self.conn.execute("SELECT equity FROM equity WHERE series=? AND epoch=? ORDER BY ts ASC",
                                   (series, epoch)):
            e = float(r["equity"])
            peak = e if peak is None or e > peak else peak
            if peak and peak > 0:
                worst = min(worst, (e - peak) / peak)
        return worst

    def insert_funding(self, f: FundingInfo) -> None:
        self.conn.execute("INSERT OR REPLACE INTO funding(symbol,ts,rate,mark,next_funding_ts) VALUES(?,?,?,?,?)",
                          (f.symbol, f.ts, f.rate, f.mark, f.next_funding_ts))

    def last_funding(self, symbol: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM funding WHERE symbol=? ORDER BY ts DESC LIMIT 1", (symbol,)).fetchone()
        return dict(row) if row else None

    # -- strategy state ------------------------------------------------------------------
    def upsert_strategy_state(self, strategy_id: str, **fields: Any) -> None:
        if "params" in fields:
            fields["params_json"] = _dumps(fields.pop("params"))
        if "params_custom" in fields:
            fields["params_custom_json"] = _dumps(sorted(fields.pop("params_custom")))
        fields["updated_ts"] = _now_ms()
        cols = list(fields)
        self.conn.execute(
            f"INSERT INTO strategy_state(strategy_id,{','.join(cols)}) VALUES(?,{','.join('?' * len(cols))}) "
            f"ON CONFLICT(strategy_id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in cols)}",
            (strategy_id, *[fields[c] for c in cols]))

    def strategy_states(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.conn.execute("SELECT * FROM strategy_state").fetchall():
            d = dict(r)
            d["params"] = json.loads(d.pop("params_json") or "{}")
            d["params_custom"] = json.loads(d.pop("params_custom_json") or "[]")
            d["enabled"] = bool(d.get("enabled"))
            d["halted"] = bool(d.get("halted"))
            out[d["strategy_id"]] = d
        return out

    # -- export -------------------------------------------------------------------------
    def export_csv(self, table: str, limit: int = 200_000) -> Iterator[str]:
        if table not in EXPORTABLE:
            raise ValueError(f"table must be one of {EXPORTABLE}")
        cur = self.conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        yield ",".join(cols) + "\n"
        for row in cur:
            vals = []
            for v in row:
                s = "" if v is None else str(v)
                if any(ch in s for ch in (",", '"', "\n")):
                    s = '"' + s.replace('"', '""') + '"'
                vals.append(s)
            yield ",".join(vals) + "\n"
