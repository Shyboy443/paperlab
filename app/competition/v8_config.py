"""V8 SCALP arena: field, execution, elimination, fast qualification and the freeze identity (docs/V8_PROTOCOL.md).

Separate from frozen V6 and V7: its own strategies (app/strategies/v8/arena.py), worker (app/live/v8_worker.py),
database (v8-forward.db) and freeze file (docs/V8_FREEZE.json). It reuses the shared engine, the Bybit live market,
the stale-data / downtime gates and AGGRESSIVE_V6 legal sizing unchanged -- all pinned by V6's freeze, which V8
checks first. Paper only: nothing here can place an order.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping, Sequence

from app.competition import v6_config as baseline
from app.strategies.v8.arena import load_v8_scalpers

# V8.1 (micro pullback) and V8.2 (micro breakout) RETIRED 2026-10-04 (operator: "retire them"). Seven single changes and
# a long-only rule were tested on 99 days AND on an independent 18-month window (2025-01 .. 2026-06): none helped, both
# lose ~0.07-0.14 R a trade in every configuration (docs/V8_TREND_FIX_STUDY.json, docs/V8_TREND_FIX_CONFIRM.json,
# docs/V8_PROTOCOL.md). Their classes stay in app/strategies/v8/arena.py (V9's stock bots inherit them); V8's field is
# V8.3 alone.
RETIRED: tuple[str, ...] = ("V8.1", "V8.2")


def load_v8() -> dict[str, Any]:
    """The V8 families that still run (every scalper family minus RETIRED)."""
    return {k: v for k, v in load_v8_scalpers().items() if k not in RETIRED}


FREEZE_FILE = baseline.DOCS / "V8_FREEZE.json"
PROTOCOL = "V8_SCALP_PAPER_V1"
VENUE = baseline.VENUE
COINS: tuple[str, ...] = ("ETH", "SOL", "XRP", "DOGE", "ARB", "ENA")
CONTEXT: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

# A 5m candle closes at H - 1 ms; latency = window + 1 ms fills at the OPEN of the first 1m bar at or after H + 60 s.
DECISION_WINDOW_MS = 60_000
LATE_AFTER_MS = 55_000
# Jev (the +JEV twins) must answer inside the window: 20 s timeout, one retry, answers after H + 55 s are SKIP.
JEV_TIMEOUT_MS = 20_000
JEV_RETRIES = 1
JEV_DEADLINE_MARGIN_MS = 5_000
EXECUTION_V8 = dataclasses.replace(baseline.EXECUTION_V6, signal_latency_ms=DECISION_WINDOW_MS - 400 + 1,
                                   order_latency_ms=400)
WARMUP_DAYS = 5                   # 1h EMA 20/50 + slope, 5m / 15m indicators; nothing older is needed
HISTORY_DAYS = {"funding": 10, "positioning": 3, "context": 5, "context_oi": 2}

# activity and elimination (checked by the worker; recorded as events, replayed on restart)
ELIMINATE_AFTER_H = 24            # nobody is judged before a full day live
ELIMINATE_MIN_TRADES_24H = 5      # fewer closed trades in the last 24 h: eliminated as inactive
# V8.3 holds up to 3 h since 2026-10-03 (docs/V8_SNAP_HOLD_STUDY.json): about 7 trades a coin-day instead of 13, and the
# study's coins traded fewer than 5 on 0-4 of 94 days (ETH on 76). Its inactivity bar is 2, so trading less by design is
# not read as "inactive".
ELIMINATE_MIN_TRADES_24H_BY_FAMILY = {"V8.3": 2}
ELIMINATE_DRAWDOWN = 0.25         # 25% below the starting capital: eliminated

# fast qualification: a gate in front of the private go-live button, never a winner claim
QUALIFY = {"min_days": 2.0, "min_trades": 40, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.15}


@dataclasses.dataclass(frozen=True)
class BotSpecV8(baseline.BotSpecV6):
    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-{self.coin}-5M"

    @property
    def timeframe(self) -> str:
        return "5m"

    @property
    def pair_id(self) -> str:
        return "v8pair:" + self.control_key

    @property
    def key(self) -> str:
        return self.control_key + {"JEV": "+JEV", "LADDER": "+LADDER"}.get(self.role, "")


def field_plan(coins: Sequence[str], jev: bool = True, ladder: bool = True) -> list[BotSpecV8]:
    """Every CONTROL scalper and its twins on the same candidates, data, capital, execution and risk: +JEV (Jev can
    only SKIP, TAKE or ATTACK the control's candidate) and +LADDER (the control's entries, TP1/TP2/TP3 ladder exits
    with stop moves: app/strategies/v8/ladder.py)."""
    out: list[BotSpecV8] = []
    for sid in load_v8():
        for coin in coins:
            out.append(BotSpecV8(sid, coin, "SCALP", "CONTROL"))
            if jev:
                out.append(BotSpecV8(sid, coin, "SCALP", "JEV"))
            if ladder:
                out.append(BotSpecV8(sid, coin, "SCALP", "LADDER"))
    return out


def code_fingerprints() -> dict[str, str]:
    return {m: baseline._src(importlib.import_module(m))
            for m in ("app.strategies.v8.arena", "app.strategies.v8.ladder", "app.competition.v8_config",
                      "app.live.v8_worker", "app.ai.jev.v8", "app.live.v8_engine", "app.live.scan_engine")}


def profile() -> dict[str, Any]:
    return {"signal": "5m", "context": ["15m", "1h"], "decision_window_ms": DECISION_WINDOW_MS,
            "late_after_ms": LATE_AFTER_MS, "execution": dataclasses.asdict(EXECUTION_V8), "warmup_days": WARMUP_DAYS,
            "history_days": HISTORY_DAYS, "coins": list(COINS), "context_symbols": list(CONTEXT),
            "risk": {"base": 0.01, "cap": 0.02, "sizing": "AGGRESSIVE_V6 legal tiers"},
            "exits_2026_10_02": {"fills": "stops and targets fill at their level (app/live/v8_engine.py)",
                                 "targets": "resting LIMIT orders: maker fee, filled on a 0.5 bp trade-through",
                                 "min_stop": {"V8.1": 0.010, "V8.2": 0.012, "V8.3": 0.012},
                                 "time_stop": {"V8.1": "none (to the UTC day close)", "V8.2": "none (to the UTC day close)",
                                               "V8.3": "45 min"},
                                 "source": "docs/V8_COST_STUDY.json"},
            "elimination": {"after_h": ELIMINATE_AFTER_H, "min_trades_24h": ELIMINATE_MIN_TRADES_24H,
                            "min_trades_24h_by_family": ELIMINATE_MIN_TRADES_24H_BY_FAMILY,
                            "drawdown": ELIMINATE_DRAWDOWN},
            "qualify": QUALIFY, "storage": "v8-forward.db", "families": list(load_v8()), "retired": list(RETIRED),
            "jev": {"twins": True, "timeout_ms": JEV_TIMEOUT_MS, "retries": JEV_RETRIES,
                    "deadline_margin_ms": JEV_DEADLINE_MARGIN_MS, **_jev_fp()},
            "ladder": _ladder_spec()}


def _ladder_spec() -> dict[str, Any]:
    from app.strategies.v8 import ladder
    return {"twins": True, "tps_r_fraction": [list(x) for x in ladder.LADDER], "lock_after_tp1": f"entry + {ladder.COST_LOCK:.2%}",
            "lock_after_tp2_r": ladder.LOCK_AFTER_TP2_R}


def manifest_core() -> dict[str, Any]:
    """Everything V8's identity depends on that is computable offline (the freeze adds the instrument rules)."""
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "params": {k: dataclasses.asdict(v.Params()) for k, v in load_v8().items()}, "profile": profile()}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "params", "profile", "rules", "funding_interval_min")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V8_FREEZE.json is missing: V8 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V8_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "params", "profile")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    missing = [f"{c}USDT" for c in COINS if f"{c}USDT" not in (man.get("rules") or {})]
    if missing:
        diffs.append("instrument rules missing for " + ", ".join(missing))
    return diffs


def _jev_fp() -> dict[str, str]:
    from app.ai.jev.v8 import v8_jev_fingerprints
    return v8_jev_fingerprints()


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    """The frozen manifest + the Jev model actually requested: a different model is a different experiment."""
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE, "jev_model": jev_model}
    return "v8x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


def bot_status(row: Mapping[str, Any], age_ms: int, eliminated: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """ACTIVE / QUALIFIED / ELIMINATED, with the numbers behind it."""
    if eliminated:
        return "ELIMINATED", dict(eliminated)
    q = QUALIFY
    checks = {"days": round(age_ms / DAY, 2), "trades": int(row.get("trades") or 0), "net": row.get("net_now"),
              "profit_factor": row.get("profit_factor"), "max_dd": row.get("max_dd")}
    ok = (checks["days"] >= q["min_days"] and checks["trades"] >= q["min_trades"]
          and (checks["net"] or 0.0) > q["min_net"] and (checks["profit_factor"] or 0.0) >= q["min_profit_factor"]
          and (checks["max_dd"] if checks["max_dd"] is not None else 1.0) <= q["max_drawdown"]
          and row.get("risk_state") not in ("HALTED", "FLOOR_HALT", "DAILY_HALT"))
    return ("QUALIFIED" if ok else "ACTIVE"), checks


def should_eliminate(row: Mapping[str, Any], age_ms: int) -> str | None:
    """Why this bot leaves the field now (None: it stays). The inactivity rule judges CONTROL bots only: a +JEV twin
    that skips a lot is doing its job, and it leaves with its control (the worker applies that) or on its own 25%
    loss."""
    start = float(row.get("start_equity") or baseline.STARTING_BALANCE)
    eq = row.get("equity_live") if row.get("equity_live") is not None else row.get("equity")
    if eq is not None and float(eq) <= start * (1.0 - ELIMINATE_DRAWDOWN):
        return "DRAWDOWN_25PCT"
    need = min_trades_24h(row)
    if (row.get("role", "CONTROL") != "JEV" and age_ms >= ELIMINATE_AFTER_H * HOUR
            and int(row.get("trades_24h") or 0) < need):
        return f"INACTIVE_UNDER_{need}_TRADES_24H"
    return None


def min_trades_24h(row: Mapping[str, Any]) -> int:
    """The inactivity bar of this bot's family (V8.3: 2, the others 5)."""
    sid = str(row.get("strategy_id") or str(row.get("key") or "").split("-")[0])
    return ELIMINATE_MIN_TRADES_24H_BY_FAMILY.get(sid, ELIMINATE_MIN_TRADES_24H)
