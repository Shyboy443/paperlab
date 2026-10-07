"""V12 BIZZY arena: Bizzy Bee from github.com/imikerussell/beebots, ported (docs/V12_PROTOCOL.md) -- its universe, field,
execution, sizing, qualification and freeze identity.

Separate from V6-V11: its own strategy (app/strategies/v12/bizzy.py), worker (app/live/v12_worker.py), Jev question
(app/ai/jev/v12.py), database (v12-forward.db) and freeze file (docs/V12_FREEZE.json). It reuses V11's ordered Bybit feed
(app/live/scan_market.py), scan engine (app/live/scan_engine.py: level fills, the pre-trade gap check) and gates
(app/live/v11_worker.py) unchanged -- those V11 files are pinned by V12's freeze too, and by V6's for the shared engine.
Paper only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping

from app.competition import v6_config as baseline
from app.strategies.v12.bizzy import ANCHOR, PREFERENCE, TRADE_COINS, load_v12

FREEZE_FILE = baseline.DOCS / "V12_FREEZE.json"
PROTOCOL = "V12_BIZZY_PAPER_V1"
VENUE = baseline.VENUE
UNIVERSE: tuple[str, ...] = PREFERENCE              # beebots' BIZZY_BREAKOUT_COINS; BTC is the feed's anchor only

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

STARTING_BALANCE = 20.0            # the operator's book size (as V8 / V11)
LEVERAGE_CEILING = 2               # beebots MAX_LEVERAGE 2; Bizzy's full size = the whole book as margin at 2x
DECISION_WINDOW_MS = 60_000        # a 1m bar closes at H - 1 ms; the order fills at the 1m OPEN at or after H + 60 s
LATE_AFTER_MS = 55_000
EXECUTION_V12 = dataclasses.replace(baseline.EXECUTION_V6, signal_latency_ms=DECISION_WINDOW_MS - 400 + 1,
                                    order_latency_ms=400)
WARMUP_DAYS = 3                    # yesterday's full range + the 1h EMA 20/50 trend ladder Jev reads

JEV_TIMEOUT_MS = 10_000
JEV_RETRIES = 0
JEV_DEADLINE_MARGIN_MS = 5_000

# one trade a day at most: qualification needs weeks, and Bizzy is never eliminated (beebots never benches her for
# results; a paper bot that loses keeps running so live data can judge the rule)
QUALIFY = {"min_days": 14.0, "min_trades": 10, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.30}


def settings_v12() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


@dataclasses.dataclass(frozen=True)
class BotSpecV12(baseline.BotSpecV6):
    """Bizzy: one book over her breakout coins (no single symbol)."""
    coin: str = "DAY"
    horizon: str = "DAY"

    @property
    def symbol(self) -> str:
        return ""

    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-DAY"

    @property
    def key(self) -> str:
        return self.control_key + ("+JEV" if self.role == "JEV" else "")

    @property
    def pair_id(self) -> str:
        return "v12:" + self.control_key

    @property
    def timeframe(self) -> str:
        # the live runner decides on THIS timeframe (V6Bot: signal_tf=spec.timeframe): Bizzy decides every minute
        return load_v12()[self.strategy_id].signal_tf


def field_plan(jev: bool = True) -> list[BotSpecV12]:
    """Bizzy and, when `jev`, Bizzy AI (same candidates; Jev may say WAIT, as in beebots)."""
    out: list[BotSpecV12] = []
    for sid in load_v12():
        out.append(BotSpecV12(sid))
        if jev:
            out.append(BotSpecV12(sid, role="JEV"))
    return out


def code_fingerprints() -> dict[str, str]:
    """V12's own modules whole; of V11 only what Bizzy runs on -- the feed and engine modules whole, the scanner base and
    the worker's gates / helpers object by object -- so a V11-only change (a scanner's parameters, V11's run loop) does
    not restart Bizzy."""
    out = {m: baseline._src(importlib.import_module(m))
           for m in ("app.strategies.v12.bizzy", "app.competition.v12_config", "app.live.v12_worker", "app.ai.jev.v12",
                     "app.live.scan_market", "app.live.scan_engine")}
    scan = importlib.import_module("app.strategies.v11.scan")
    worker = importlib.import_module("app.live.v11_worker")
    for mod, names in ((scan, ("ScanV11", "Params", "Cand", "rank_pct")),
                       (worker, ("ScanGateV11", "JevGateV11", "load_recorded_v11", "recorded_for", "load_eliminated",
                                 "live_marks", "prices"))):
        for n in names:
            out[f"{mod.__name__}.{n}"] = baseline._src(getattr(mod, n))
    out["app.strategies.v11.scan.ANCHOR"] = scan.ANCHOR
    return out


def profile() -> dict[str, Any]:
    fams = load_v12()
    return {"signal": {k: v.signal_tf for k, v in fams.items()}, "slots": {k: v.max_positions for k, v in fams.items()},
            "decision_window_ms": DECISION_WINDOW_MS, "late_after_ms": LATE_AFTER_MS,
            "execution": dataclasses.asdict(EXECUTION_V12), "warmup_days": WARMUP_DAYS,
            "universe": list(UNIVERSE), "trade_coins": list(TRADE_COINS), "anchor": ANCHOR,
            "starting_balance": STARTING_BALANCE, "leverage_ceiling": LEVERAGE_CEILING,
            "sizing": "full size: the whole book as margin at 2x (no risk tiers; the engine's fee pre-check applies)",
            "rule": "Larry Williams k = 0.5 day breakout, long only, one trade per UTC day, stop = the day's open, "
                    "exit 1 minute before the UTC close",
            "source": "github.com/imikerussell/beebots src/bees/bizzy.ts (live rules since 2026-09-24)",
            "elimination": "none", "qualify": QUALIFY, "storage": "v12-forward.db", "families": list(fams),
            "market_data": "Bybit linear public REST: 1m klines + tickers polled every minute, one ordered batch",
            "exit_fills": "the stop fills at its level (+ spread / slippage); a gap through it fills at the open",
            "jev": {"twins": True, "question": "CONTRADICT = WAIT (re-proposed after 5 min), SUPPORT / STRONGLY = take",
                    "timeout_ms": JEV_TIMEOUT_MS, "retries": JEV_RETRIES,
                    "deadline_margin_ms": JEV_DEADLINE_MARGIN_MS, **_jev_fp()}}


def _jev_fp() -> dict[str, str]:
    from app.ai.jev.v12 import v12_jev_fingerprints
    return v12_jev_fingerprints()


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "params": {k: dataclasses.asdict(v.Params()) for k, v in load_v12().items()}, "profile": profile()}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "params", "profile", "rules", "funding_interval_min")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V12_FREEZE.json is missing: V12 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V12_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "params",
                                                                 "profile")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    missing = [s for s in UNIVERSE if s not in (man.get("rules") or {})]
    if missing:
        diffs.append("instrument rules missing for " + ", ".join(missing))
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE, "jev_model": jev_model}
    return "v12x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


def bot_status(row: Mapping[str, Any], age_ms: int, eliminated: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """ACTIVE / QUALIFIED, with the numbers behind it (Bizzy is never eliminated)."""
    q = QUALIFY
    checks = {"days": round(age_ms / DAY, 2), "trades": int(row.get("trades") or 0), "net": row.get("net_now"),
              "profit_factor": row.get("profit_factor"), "max_dd": row.get("max_dd")}
    ok = (checks["days"] >= q["min_days"] and checks["trades"] >= q["min_trades"]
          and (checks["net"] or 0.0) > q["min_net"] and (checks["profit_factor"] or 0.0) >= q["min_profit_factor"]
          and (checks["max_dd"] if checks["max_dd"] is not None else 1.0) <= q["max_drawdown"]
          and row.get("risk_state") not in ("HALTED", "FLOOR_HALT"))
    return ("QUALIFIED" if ok else "ACTIVE"), checks


def should_eliminate(row: Mapping[str, Any], age_ms: int) -> str | None:
    return None
