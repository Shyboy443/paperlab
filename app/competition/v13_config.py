"""V13 SNAPBACK arena: limit-order (maker) mean reversion over the V11 scan universe (docs/V13_PROTOCOL.md) -- its field,
execution, sizing, qualification and freeze identity.

Separate from V6-V12: its own strategy (app/strategies/v13/snapback.py), engine (app/live/v13_engine.py: resting post-only
limit entries), worker (app/live/v13_worker.py), database (v13-forward.db) and freeze file (docs/V13_FREEZE.json). It
reuses V11's ordered Bybit feed (app/live/scan_market.py), scan engine (app/live/scan_engine.py) and gate helpers
(app/live/v11_worker.py) unchanged; those are pinned here object by object, so a V11-only change does not restart V13.
No Jev twin: no Jev gate has beaten chance in any earlier test (docs/V4_ROOT_CAUSE.md). Paper only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping

from app.competition import v6_config as baseline
from app.strategies.v13.snapback import ANCHOR, load_v13

FREEZE_FILE = baseline.DOCS / "V13_FREEZE.json"
PROTOCOL = "V13_SNAPBACK_PAPER_V1"
VENUE = baseline.VENUE
# the V11 universe (scripts/v11_scan_data.py, 2026-09-29); BTC is the feed's anchor only: a 20 USDT book cannot trade it
UNIVERSE: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "HYPEUSDT", "NEARUSDT", "DOGEUSDT", "ENAUSDT", "SUIUSDT",
    "ADAUSDT", "ARBUSDT", "UNIUSDT", "1000PEPEUSDT", "PUMPFUNUSDT", "TAOUSDT", "LINKUSDT", "AKEUSDT", "WLDUSDT",
    "BNBUSDT", "USELESSUSDT", "ONDOUSDT", "AAVEUSDT", "LITUSDT", "TRUMPUSDT", "AVAXUSDT", "XMRUSDT", "FARTCOINUSDT",
    "LTCUSDT", "XPLUSDT")

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

STARTING_BALANCE = 20.0            # the operator's book size (as V8 / V11 / V12)
LEVERAGE_CEILING = 20
DECISION_WINDOW_MS = 60_000        # a 5m bar closes at H - 1 ms; the limit reaches the book at the 1m bar at H + 60 s
LATE_AFTER_MS = 55_000
EXECUTION_V13 = dataclasses.replace(baseline.EXECUTION_V6, signal_latency_ms=DECISION_WINDOW_MS - 400 + 1,
                                    order_latency_ms=400)
WARMUP_DAYS = 3                    # 289 five-minute bars for the 24 h VWAP / volatility, plus margin

# The bar to call a paper bot QUALIFIED is far stricter than V8 / V11's 2 days / 40 trades (docs/SYSTEM_REVIEW.md R5):
# two weeks, 150 closed trades, profitable after every cost, PF >= 1.2, drawdown <= 20%. Never eliminated: it is an
# experiment that runs until judged.
QUALIFY = {"min_days": 14.0, "min_trades": 150, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.20}


def settings_v13() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


@dataclasses.dataclass(frozen=True)
class BotSpecV13(baseline.BotSpecV6):
    """Snapback: one book over the whole universe (no single symbol)."""
    coin: str = "ALL"
    horizon: str = "SCAN"

    @property
    def symbol(self) -> str:
        return ""

    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-SNAP"

    @property
    def key(self) -> str:
        return self.control_key

    @property
    def pair_id(self) -> str:
        return "v13:" + self.control_key

    @property
    def timeframe(self) -> str:
        # the live runner decides on THIS timeframe (V6Bot: signal_tf=spec.timeframe): every 5 minutes
        return load_v13()[self.strategy_id].signal_tf


def field_plan(jev: bool = False) -> list[BotSpecV13]:
    """One control bot per V13 family (no Jev twin)."""
    return [BotSpecV13(sid) for sid in load_v13()]


def code_fingerprints() -> dict[str, str]:
    """V13's own modules whole; of V11 only what V13 runs on -- the feed and scan engine modules whole, the scanner base
    and the worker's gate / helpers object by object."""
    out = {m: baseline._src(importlib.import_module(m))
           for m in ("app.strategies.v13.snapback", "app.competition.v13_config", "app.live.v13_worker",
                     "app.live.v13_engine", "app.live.scan_market", "app.live.scan_engine")}
    scan = importlib.import_module("app.strategies.v11.scan")
    worker = importlib.import_module("app.live.v11_worker")
    for mod, names in ((scan, ("ScanV11", "Params", "Cand", "rank_pct")),
                       (worker, ("ScanGateV11", "load_recorded_v11", "recorded_for", "load_eliminated", "live_marks",
                                 "prices"))):
        for n in names:
            out[f"{mod.__name__}.{n}"] = baseline._src(getattr(mod, n))
    out["app.strategies.v11.scan.ANCHOR"] = scan.ANCHOR
    return out


def profile() -> dict[str, Any]:
    fams = load_v13()
    return {"signal": {k: v.signal_tf for k, v in fams.items()}, "slots": {k: v.max_positions for k, v in fams.items()},
            "decision_window_ms": DECISION_WINDOW_MS, "late_after_ms": LATE_AFTER_MS,
            "execution": dataclasses.asdict(EXECUTION_V13), "warmup_days": WARMUP_DAYS,
            "universe": list(UNIVERSE), "anchor": ANCHOR, "starting_balance": STARTING_BALANCE,
            "leverage_ceiling": LEVERAGE_CEILING,
            "sizing": "SizingV6 tiers (1% base risk, legal minimums), hard cap 2% per trade",
            "entry": "post-only limit beyond the close; filled on a 0.5 bp trade-through at the limit, maker fee; "
                     "rejected if it would cross when placed; cancelled at expiry",
            "exit_fills": "stop at its level (+ spread / slippage, a gap fills at the open); TP as a resting limit "
                          "(maker); time limit at market",
            "elimination": "none", "qualify": QUALIFY, "storage": "v13-forward.db", "families": list(fams),
            "market_data": "Bybit linear public REST: 1m klines + tickers polled every minute, one ordered batch",
            "jev": {"twins": False}}


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "params": {k: dataclasses.asdict(v.Params()) for k, v in load_v13().items()}, "profile": profile()}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "params", "profile", "rules", "funding_interval_min")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V13_FREEZE.json is missing: V13 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V13_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "params",
                                                                 "profile")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    missing = [s for s in UNIVERSE if s not in (man.get("rules") or {})]
    if missing:
        diffs.append("instrument rules missing for " + ", ".join(missing))
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE}
    return "v13x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


def bot_status(row: Mapping[str, Any], age_ms: int, eliminated: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """ACTIVE / QUALIFIED, with the numbers behind it (never eliminated)."""
    q = QUALIFY
    checks = {"days": round(age_ms / DAY, 2), "trades": int(row.get("trades") or 0), "net": row.get("net_now"),
              "profit_factor": row.get("profit_factor"), "max_dd": row.get("max_dd")}
    ok = (checks["days"] >= q["min_days"] and checks["trades"] >= q["min_trades"]
          and (checks["net"] or 0.0) > q["min_net"] and (checks["profit_factor"] or 0.0) >= q["min_profit_factor"]
          and (checks["max_dd"] if checks["max_dd"] is not None else 1.0) <= q["max_drawdown"]
          and row.get("risk_state") not in ("HALTED", "FLOOR_HALT", "DAILY_HALT"))
    return ("QUALIFIED" if ok else "ACTIVE"), checks


def should_eliminate(row: Mapping[str, Any], age_ms: int) -> str | None:
    return None
