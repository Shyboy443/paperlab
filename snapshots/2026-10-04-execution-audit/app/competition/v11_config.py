"""V11 SCAN arena: the universe, field, execution, elimination, qualification and freeze identity (docs/V11_PROTOCOL.md).

Separate from V6-V9: its own strategies (app/strategies/v11/scan.py), market feed (app/live/scan_market.py), worker
(app/live/v11_worker.py), database (v11-forward.db) and freeze file (docs/V11_FREEZE.json). It reuses the shared engine,
fees and AGGRESSIVE_V6 legal sizing unchanged -- all pinned by V6's freeze, which V11 checks first. Paper only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping

from app.competition import v6_config as baseline
from app.strategies.v11.scan import ANCHOR, load_v11_scanners as load_v11

FREEZE_FILE = baseline.DOCS / "V11_FREEZE.json"
PROTOCOL = "V11_SCAN_PAPER_V1"
VENUE = baseline.VENUE
# scripts/v11_scan_data.py on 2026-09-29: Bybit linear crypto perps listed >= 120 days, top 30 by 30-day MEDIAN daily
# turnover. Fixed for the experiment (a new universe is a new experiment).
UNIVERSE: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "HYPEUSDT", "NEARUSDT", "DOGEUSDT", "ENAUSDT", "SUIUSDT",
    "ADAUSDT", "ARBUSDT", "UNIUSDT", "1000PEPEUSDT", "PUMPFUNUSDT", "TAOUSDT", "LINKUSDT", "AKEUSDT", "WLDUSDT",
    "BNBUSDT", "USELESSUSDT", "ONDOUSDT", "AAVEUSDT", "LITUSDT", "TRUMPUSDT", "AVAXUSDT", "XMRUSDT", "FARTCOINUSDT",
    "LTCUSDT", "XPLUSDT")

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

STARTING_BALANCE = 20.0            # the operator's size (as V8). Bybit's minimum orders then rule out BTC entirely and
                                   # ETH / ZEC on wide stops: the sizing tiers refuse those (MIN_NOTIONAL), the rest trade
LEVERAGE_CEILING = 20
DECISION_WINDOW_MS = 60_000        # a bar closes at H - 1 ms; the order fills at the 1m OPEN at or after H + 60 s
LATE_AFTER_MS = 55_000
EXECUTION_V11 = dataclasses.replace(baseline.EXECUTION_V6, signal_latency_ms=DECISION_WINDOW_MS - 400 + 1,
                                    order_latency_ms=400)
WARMUP_DAYS = 5                    # 1h EMA 20/50 + slope, 290 5m bars for the day's median volume
HISTORY_FUNDING_DAYS = 2

# the +JEV twins: Jev must answer inside the 60 s window (a scanner may send several candidates at once, asked in turn)
JEV_TIMEOUT_MS = 10_000
JEV_RETRIES = 0
JEV_DEADLINE_MARGIN_MS = 5_000

ELIMINATE_AFTER_H = 24
ELIMINATE_MIN_TRADES_24H = 3
ELIMINATE_DRAWDOWN = 0.25
QUALIFY = {"min_days": 2.0, "min_trades": 40, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.15}


def settings_v11() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


@dataclasses.dataclass(frozen=True)
class BotSpecV11(baseline.BotSpecV6):
    """One scanner bot: a single book over the whole universe (coin 'ALL', no single symbol)."""
    coin: str = "ALL"
    horizon: str = "SCAN"

    @property
    def symbol(self) -> str:
        return ""

    @property
    def control_key(self) -> str:
        return f"{self.strategy_id}-SCAN"

    @property
    def key(self) -> str:
        return self.control_key + ("+JEV" if self.role == "JEV" else "")

    @property
    def pair_id(self) -> str:
        return "v11:" + self.control_key

    @property
    def timeframe(self) -> str:
        return load_v11()[self.strategy_id].signal_tf


def field_plan(jev: bool = True) -> list[BotSpecV11]:
    """Every scanner and, when `jev`, its +JEV twin (same candidates; Jev may skip, its confidence stretches the TPs)."""
    out: list[BotSpecV11] = []
    for sid in load_v11():
        out.append(BotSpecV11(sid))
        if jev:
            out.append(BotSpecV11(sid, role="JEV"))
    return out


def code_fingerprints() -> dict[str, str]:
    return {m: baseline._src(importlib.import_module(m))
            for m in ("app.strategies.v11.scan", "app.strategies.v11.ladder", "app.competition.v11_config",
                      "app.live.v11_worker", "app.live.scan_market", "app.live.scan_engine", "app.ai.jev.v11")}


def profile() -> dict[str, Any]:
    fams = load_v11()
    return {"signal": {k: v.signal_tf for k, v in fams.items()},
            "slots": {k: v.max_positions for k, v in fams.items()},
            "decision_window_ms": DECISION_WINDOW_MS, "late_after_ms": LATE_AFTER_MS,
            "execution": dataclasses.asdict(EXECUTION_V11), "warmup_days": WARMUP_DAYS,
            "universe": list(UNIVERSE), "anchor": ANCHOR, "starting_balance": STARTING_BALANCE,
            "leverage_ceiling": LEVERAGE_CEILING,
            "risk": {"base": 0.01, "cap": 0.02, "sizing": "AGGRESSIVE_V6 legal tiers"},
            "elimination": {"after_h": ELIMINATE_AFTER_H, "min_trades_24h": ELIMINATE_MIN_TRADES_24H,
                            "drawdown": ELIMINATE_DRAWDOWN},
            "qualify": QUALIFY, "storage": "v11-forward.db", "families": list(fams),
            "market_data": "Bybit linear public REST: 1m klines + tickers polled every minute, one ordered batch",
            "ladder": _ladder(),
            "pre_trade_check": "skip an order whose fill price has already run past TP1 or through the stop",
            "break_even": "after TP1 the stop moves to entry + 0.15% (pays the round-trip fees), not plain entry",
            "after_tp1": "stop locked LOCK_AFTER_TP1 of the way to TP1 (never less than the fees) the moment TP1 fills; "
                         "after TP2 onto TP1",
            "exit_fills": "a stop or take-profit passed inside a 1m bar fills at its level (+ spread / slippage); a gap "
                          "through it fills at the open",
            "take_profit_orders": "resting LIMIT orders: filled at the TP price at the maker fee once price trades 0.5 bp "
                                  "through it; stops and time exits are market orders (docs/V11_ZAP_STUDY.json)",
            "jev": {"twins": True, "timeout_ms": JEV_TIMEOUT_MS, "retries": JEV_RETRIES,
                    "deadline_margin_ms": JEV_DEADLINE_MARGIN_MS, **_jev_fp()}}


def _jev_fp() -> dict[str, str]:
    from app.ai.jev.v11 import v11_jev_fingerprints
    return v11_jev_fingerprints()


def _ladder() -> dict[str, Any]:
    from app.strategies.v11 import ladder
    return {"shares": {k: list(v) for k, v in ladder.SHARES.items()}, "lock_after_tp1": dict(ladder.LOCK_AFTER_TP1),
            "rungs_r": {k: list(v) for k, v in ladder.RUNGS.items()},
            "stops": "TP1 -> entry + fees or LOCK_AFTER_TP1, TP2 -> TP1 (both the moment the TP fills)",
            "source": "docs/V11_EQUAL_LADDER.json, docs/V11_TP1_STUDY.json"}


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "params": {k: dataclasses.asdict(v.Params()) for k, v in load_v11().items()}, "profile": profile()}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "params", "profile", "rules", "funding_interval_min")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V11_FREEZE.json is missing: V11 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V11_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "params",
                                                                 "profile")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    missing = [s for s in UNIVERSE if s not in (man.get("rules") or {})]
    if missing:
        diffs.append("instrument rules missing for " + ", ".join(missing))
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    """The frozen manifest + the Jev model actually requested: a different model is a different experiment."""
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE, "jev_model": jev_model}
    return "v11x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


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
          and row.get("risk_state") not in ("HALTED", "FLOOR_HALT"))
    return ("QUALIFIED" if ok else "ACTIVE"), checks


def should_eliminate(row: Mapping[str, Any], age_ms: int) -> str | None:
    start = float(row.get("start_equity") or STARTING_BALANCE)
    eq = row.get("equity_live") if row.get("equity_live") is not None else row.get("equity")
    if eq is not None and float(eq) <= start * (1.0 - ELIMINATE_DRAWDOWN):
        return "DRAWDOWN_25PCT"
    if (row.get("role", "CONTROL") != "JEV" and age_ms >= ELIMINATE_AFTER_H * HOUR
            and int(row.get("trades_24h") or 0) < ELIMINATE_MIN_TRADES_24H):      # a +JEV twin may skip a lot
        return "INACTIVE_UNDER_3_TRADES_24H"
    return None
