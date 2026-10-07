"""V9 STOCKS arena: field, execution, costs, session-based elimination and qualification, and the freeze identity
(docs/V9_PROTOCOL.md).

Separate from V6 / V7 / V8: its own strategies (app/strategies/v9/stocks.py), market feed (app/live/alpaca_market.py:
Alpaca's IEX data, regular sessions only), worker (app/live/v9_worker.py), database (v9-forward.db) and freeze file
(docs/V9_FREEZE.json). Its StockReplayEngine subclasses the shared engine; the stale-data / downtime gates,
the V8 gates and AGGRESSIVE_V6
legal sizing unchanged -- all pinned by V6's freeze, which V9 checks first. Paper only: nothing here can place an order.

Costs: 0.2 bps per filled side is a regulatory-fee proxy, not an exact broker bill.
Market orders pay the IEX half-spread capped at 1 bp (or the OHLCV model without
quotes). This cap is an assumption, not an observed consolidated NBBO spread.
Resting targets fill at their limit after trade-through and pay the fee proxy.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping, Sequence

from app.competition import v6_config as baseline
from app.competition import v8_config as v8
from app.execution.config import FeeSchedule
from app.strategies.v9.stocks import load_v9_stock_scalpers as load_v9

FREEZE_FILE = baseline.DOCS / "V9_FREEZE.json"
PROTOCOL = "V9_STOCKS_PAPER_V1"
VENUE = "ALPACA_IEX_US_EQUITY"
SYMBOLS: tuple[str, ...] = ("SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "META", "AMZN", "GOOGL")

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

STARTING_BALANCE = 1000.0         # USD per bot book: whole shares of every symbol are legal at 1% risk
# 3x, not the 4x of Reg T intraday buying power (2026-10-02): the paper engine liquidates a position at 1/leverage - the
# maintenance margin (25% for stocks), which is ZERO at 4x -- every 4x position was 'liquidated' the minute it opened
# (396 of the first 609 trades, all of them 4x; none at 1-3x). At 3x the liquidation sits 8.3% away, beyond any stop.
LEVERAGE_CAP = 3
# The same 60 s decision window as V8 (Jev must answer by H + 55 s); fills at the first 1m open >= H + 60 s.
DECISION_WINDOW_MS = v8.DECISION_WINDOW_MS
LATE_AFTER_MS = v8.LATE_AFTER_MS
JEV_TIMEOUT_MS = v8.JEV_TIMEOUT_MS
JEV_RETRIES = v8.JEV_RETRIES
JEV_DEADLINE_MARGIN_MS = v8.JEV_DEADLINE_MARGIN_MS
EXECUTION_V9 = v8.EXECUTION_V8
FEES_V9 = FeeSchedule(0.00002, 0.00002, "alpaca_us_equity", "2026-10-07")
WARMUP_DAYS = 16                  # >= 10 sessions: the 1h EMA 20/50 + slope needs ~57 session hours
# Whole shares, 1-cent ticks, 25% maintenance margin (Reg T): the same for every symbol in the field.
RULES: dict[str, dict[str, Any]] = {s: {"symbol": s, "tick": 0.01, "step": 1.0, "min_qty": 1.0, "min_notional": 1.0,
                                        "maint_margin_rate": 0.25, "price_precision": 2, "qty_precision": 0}
                                    for s in SYMBOLS}

# Elimination is judged right after each session's close, on the last 3 sessions (a weekend is not inactivity, and one
# quiet session is not either: the pre-freeze activity check, docs/V9_ACTIVITY_CHECK.json, saw zero-trade sessions in
# most bots that average 2-9 per session).
ELIMINATE_WINDOW_SESSIONS = 3
ELIMINATE_MIN_TRADES_WINDOW = 3
ELIMINATE_DRAWDOWN = 0.25
QUALIFY = {"min_sessions": 4, "min_trades": 20, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.15}


@dataclasses.dataclass(frozen=True)
class BotSpecV9(baseline.BotSpecV6):
    @property
    def symbol(self) -> str:
        return self.coin

    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-{self.coin}-5M"

    @property
    def timeframe(self) -> str:
        return "5m"

    @property
    def pair_id(self) -> str:
        return "v9pair:" + self.control_key


def field_plan(symbols: Sequence[str], jev: bool = True) -> list[BotSpecV9]:
    """Every CONTROL stock scalper and, with `jev`, its matched +JEV twin."""
    out: list[BotSpecV9] = []
    for sid in load_v9():
        for sym in symbols:
            out.append(BotSpecV9(sid, sym, "SCALP", "CONTROL"))
            if jev:
                out.append(BotSpecV9(sid, sym, "SCALP", "JEV"))
    return out


def settings_v9() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


def code_fingerprints() -> dict[str, str]:
    return {m: baseline._src(importlib.import_module(m))
            for m in ("app.strategies.v9.stocks", "app.strategies.v8.arena", "app.competition.v9_config",
                      "app.live.stock_engine",
                      "app.live.v9_worker", "app.live.alpaca_market", "app.ai.jev.v9")}


def profile() -> dict[str, Any]:
    return {"signal": "5m", "context": ["15m", "1h"], "market": "US stocks / ETFs, regular sessions (Alpaca IEX)",
            "decision_window_ms": DECISION_WINDOW_MS, "late_after_ms": LATE_AFTER_MS,
            "execution": dataclasses.asdict(EXECUTION_V9), "fees": dataclasses.asdict(FEES_V9), "warmup_days": WARMUP_DAYS,
            "exit_model": {"engine": "StockReplayEngine", "stop": "crossed level plus friction; opening gaps at open",
                           "target": "resting limit, 0.5bp trade-through", "session_deadline": "close minus 1 minute"},
            "symbols": list(SYMBOLS), "starting_balance": STARTING_BALANCE, "leverage_cap": LEVERAGE_CAP,
            "risk": {"base": 0.01, "cap": 0.02, "sizing": "AGGRESSIVE_V6 legal tiers"},
            "elimination": {"window_sessions": ELIMINATE_WINDOW_SESSIONS, "min_trades_window": ELIMINATE_MIN_TRADES_WINDOW,
                            "drawdown": ELIMINATE_DRAWDOWN},
            "qualify": QUALIFY, "storage": "v9-forward.db", "families": list(load_v9()),
            "jev": {"twins": True, "timeout_ms": JEV_TIMEOUT_MS, "retries": JEV_RETRIES,
                    "deadline_margin_ms": JEV_DEADLINE_MARGIN_MS, **_jev_fp()}}


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "params": {k: dataclasses.asdict(v.Params()) for k, v in load_v9().items()}, "profile": profile(),
            "rules": RULES}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "params", "profile", "rules")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V9_FREEZE.json is missing: V9 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V9_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "params",
                                                              "profile", "rules")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    return diffs


def _jev_fp() -> dict[str, str]:
    from app.ai.jev.v9 import v9_jev_fingerprints
    return v9_jev_fingerprints()


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE, "jev_model": jev_model}
    return "v9x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


def bot_status(row: Mapping[str, Any], sessions_live: int, eliminated: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """ACTIVE / QUALIFIED / ELIMINATED, with the numbers behind it (age counted in completed sessions)."""
    if eliminated:
        return "ELIMINATED", dict(eliminated)
    q = QUALIFY
    checks = {"sessions": int(sessions_live), "trades": int(row.get("trades") or 0), "net": row.get("net_now"),
              "profit_factor": row.get("profit_factor"), "max_dd": row.get("max_dd")}
    ok = (checks["sessions"] >= q["min_sessions"] and checks["trades"] >= q["min_trades"]
          and (checks["net"] or 0.0) > q["min_net"] and (checks["profit_factor"] or 0.0) >= q["min_profit_factor"]
          and (checks["max_dd"] if checks["max_dd"] is not None else 1.0) <= q["max_drawdown"]
          and row.get("risk_state") not in ("HALTED", "FLOOR_HALT", "DAILY_HALT"))
    return ("QUALIFIED" if ok else "ACTIVE"), checks


def drawdown_out(row: Mapping[str, Any]) -> bool:
    start = float(row.get("start_equity") or STARTING_BALANCE)
    eq = row.get("equity_live") if row.get("equity_live") is not None else row.get("equity")
    return eq is not None and float(eq) <= start * (1.0 - ELIMINATE_DRAWDOWN)


def should_eliminate(row: Mapping[str, Any], sessions_live: int, window_trades: int | None) -> str | None:
    """Why this bot leaves the field now (None: it stays). `window_trades` is the bot's closed trades over the last
    ELIMINATE_WINDOW_SESSIONS sessions, given right after a session's close (None otherwise: only the drawdown rule
    applies then). Inactivity judges CONTROL bots only; a +JEV twin leaves with its control or on its own 25% loss."""
    if drawdown_out(row):
        return "DRAWDOWN_25PCT"
    if (window_trades is not None and row.get("role", "CONTROL") != "JEV"
            and sessions_live >= ELIMINATE_WINDOW_SESSIONS and window_trades < ELIMINATE_MIN_TRADES_WINDOW):
        return f"INACTIVE_UNDER_{ELIMINATE_MIN_TRADES_WINDOW}_TRADES_IN_{ELIMINATE_WINDOW_SESSIONS}_SESSIONS"
    return None
