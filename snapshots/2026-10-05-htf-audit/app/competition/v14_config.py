"""V14 HTF arena: copies of the best current strategies that check the higher timeframes before every entry
(docs/V14_PROTOCOL.md) -- its field, execution, sizing, qualification and freeze identity.

Separate from V6-V13: its own strategy wrapper (app/strategies/v14/htf.py), worker (app/live/v14_worker.py), database
(v14-forward.db) and freeze file (docs/V14_FREEZE.json). Each bot runs the COPIED strategy's own class, parameters and
engine (V8.3: app/live/v8_engine.py; V11.1 / V11.2: app/live/scan_engine.py with the 25/50/25 ladder; V13: app/live/
v13_engine.py) on V11's ordered Bybit feed; those objects are pinned here, so a change to a copied strategy restarts V14
(a different strategy is a different experiment). Every V14 bot has a running original to compare with. Paper only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from typing import Any, Mapping

from app.competition import v6_config as baseline
from app.strategies.v14.htf import SIGNAL_TF, SOURCES

FREEZE_FILE = baseline.DOCS / "V14_FREEZE.json"
PROTOCOL = "V14_HTF_PAPER_V1"
VENUE = baseline.VENUE
ANCHOR = "BTCUSDT"
# the V11 universe (scripts/v11_scan_data.py, 2026-09-29): the scanners' coins; it contains V8's six coins
UNIVERSE: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "HYPEUSDT", "NEARUSDT", "DOGEUSDT", "ENAUSDT", "SUIUSDT",
    "ADAUSDT", "ARBUSDT", "UNIUSDT", "1000PEPEUSDT", "PUMPFUNUSDT", "TAOUSDT", "LINKUSDT", "AKEUSDT", "WLDUSDT",
    "BNBUSDT", "USELESSUSDT", "ONDOUSDT", "AAVEUSDT", "LITUSDT", "TRUMPUSDT", "AVAXUSDT", "XMRUSDT", "FARTCOINUSDT",
    "LTCUSDT", "XPLUSDT")
V8_COINS: tuple[str, ...] = ("ETH", "SOL", "XRP", "DOGE", "ARB", "ENA")      # the V8.3 copy trades V8's own coins

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000

STARTING_BALANCE = 20.0
LEVERAGE_CEILING = 20
DECISION_WINDOW_MS = 60_000
LATE_AFTER_MS = 55_000
EXECUTION_V14 = dataclasses.replace(baseline.EXECUTION_V6, signal_latency_ms=DECISION_WINDOW_MS - 400 + 1,
                                    order_latency_ms=400)
WARMUP_DAYS = 14                   # the HTF rule needs 300 hourly candles (12.5 days)

# A strict bar, as V13 (docs/SYSTEM_REVIEW.md R5): two weeks, 100 closed trades, profitable after every cost,
# PF >= 1.2, drawdown <= 20%. Never eliminated: each bot is half of an A/B test with its original.
QUALIFY = {"min_days": 14.0, "min_trades": 100, "min_net": 0.0, "min_profit_factor": 1.2, "max_drawdown": 0.20}


def settings_v14() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


@dataclasses.dataclass(frozen=True)
class BotSpecV14(baseline.BotSpecV6):
    """A V14 bot: one coin (the V8.3 copy) or the whole universe (coin 'ALL': the scanner copies)."""
    horizon: str = "HTF"

    @property
    def symbol(self) -> str:
        return "" if self.coin == "ALL" else f"{self.coin}USDT"

    @property
    def control_key(self) -> str:
        tag = {"single": "5M", "scanner": "SCAN", "snapback": "SNAP"}[SOURCES[self.strategy_id]["kind"]]
        return f"{self.strategy_id}-{tag}" if self.coin == "ALL" else f"{self.strategy_id}-{self.coin}-{tag}"

    @property
    def key(self) -> str:
        return self.control_key

    @property
    def pair_id(self) -> str:
        return "v14:" + self.control_key

    @property
    def timeframe(self) -> str:
        return SIGNAL_TF[self.strategy_id]

    @property
    def multi(self) -> bool:
        return self.coin == "ALL"

    @property
    def copies(self) -> str:
        return SOURCES[self.strategy_id]["copies"]


def field_plan(jev: bool = False) -> list[BotSpecV14]:
    """V14.1 on each of V8's six coins; V14.2, V14.3 and V14.4 over the whole universe."""
    out = [BotSpecV14("V14.1", c) for c in V8_COINS]
    out += [BotSpecV14(sid, "ALL") for sid in ("V14.2", "V14.3", "V14.4")]
    return out


def original_key(spec: BotSpecV14) -> tuple[str, str]:
    """(program, bot key) of the running original each V14 bot copies -- its A/B partner."""
    src = SOURCES[spec.strategy_id]
    if src["kind"] == "single":
        return src["program"], f"{src['copies']}-{spec.coin}-5M"
    return src["program"], f"{src['copies']}-{'SNAP' if src['kind'] == 'snapback' else 'SCAN'}"


def code_fingerprints() -> dict[str, str]:
    """V14's own modules whole; of the copied programs exactly the objects V14 runs (the strategy classes, their
    Params, the engines, the V11 ladder, the scan feed and gate helpers)."""
    out = {m: baseline._src(importlib.import_module(m))
           for m in ("app.strategies.v14.htf", "app.competition.v14_config", "app.live.v14_worker",
                     "app.live.scan_market", "app.live.scan_engine", "app.live.v8_engine", "app.live.v13_engine",
                     "app.strategies.v11.ladder", "app.strategies.v13.snapback")}
    for mod, names in (("app.strategies.v8.arena", ("Params", "SnapParams", "ScalpV8", "VwapSnapV8")),
                       ("app.strategies.v11.scan", ("ScanV11", "Params", "Cand", "rank_pct", "RsbParams", "RspParams",
                                                    "RsBreakoutV11", "RsPullbackV11")),
                       ("app.live.v11_worker", ("ScanGateV11", "load_recorded_v11", "recorded_for", "load_eliminated",
                                                "live_marks", "prices"))):
        m = importlib.import_module(mod)
        for n in names:
            out[f"{mod}.{n}"] = baseline._src(getattr(m, n))
    out["app.strategies.v11.scan.ANCHOR"] = importlib.import_module("app.strategies.v11.scan").ANCHOR
    return out


def profile() -> dict[str, Any]:
    from app.strategies.v14.htf import EMA_MID, EMA_SLOW, MIN_1H, base_class
    return {"field": [s.key for s in field_plan()], "copies": {k: v["copies"] for k, v in SOURCES.items()},
            "signal": dict(SIGNAL_TF), "decision_window_ms": DECISION_WINDOW_MS, "late_after_ms": LATE_AFTER_MS,
            "execution": dataclasses.asdict(EXECUTION_V14), "warmup_days": WARMUP_DAYS, "universe": list(UNIVERSE),
            "v8_coins": list(V8_COINS), "anchor": ANCHOR, "starting_balance": STARTING_BALANCE,
            "leverage_ceiling": LEVERAGE_CEILING, "sizing": "SizingV6 tiers (1% base risk, legal minimums), hard cap 2%",
            "htf_rule": {"source": "1h candles", "mid_ema": EMA_MID, "slow_ema": EMA_SLOW, "min_1h": MIN_1H,
                         "rule": "long only if bias >= +1, short only if bias <= -1 (4h-ish and daily-ish trends)"},
            "params": {k: dataclasses.asdict(base_class(k).Params()) for k in SOURCES},
            "elimination": "none", "qualify": QUALIFY, "storage": "v14-forward.db",
            "market_data": "Bybit linear public REST: 1m klines + tickers polled every minute, one ordered batch",
            "jev": {"twins": False}}


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "profile": profile()}


def fingerprint(man: Mapping[str, Any]) -> str:
    keys = ("protocol", "baseline_fingerprint", "code", "profile", "rules", "funding_interval_min")
    return hashlib.sha256(json.dumps({k: man.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V14_FREEZE.json is missing: V14 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V14_FREEZE.json" for k in ("protocol", "baseline_fingerprint", "code", "profile")
             if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    missing = [s for s in UNIVERSE if s not in (man.get("rules") or {})]
    if missing:
        diffs.append("instrument rules missing for " + ", ".join(missing))
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE}
    return "v14x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident


def bot_status(row: Mapping[str, Any], age_ms: int, eliminated: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
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
