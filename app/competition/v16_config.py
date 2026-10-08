"""V16 CONDITIONAL: the survivors of the pre-registered coin-by-coin study (scripts/v16_coin_study.py,
docs/V16_COIN_STUDY.json) as paper bots that trade only in the market condition they were chosen for.

Operator, 2026-10-08: "have temporary bots which give higher returns in specific conditions and run them". The study
gave each of 30 coins a (family, condition) pair on year 1 and required profit on year 2 AND on an untouched holdout
year. Three coins passed; luck alone expects about 1.5 (95th percentile 3), so these are LABELLED EXPERIMENTS, not
proven edges. XRP V6.6 "always on" already runs in V6 (and is the live-mirrored bot), so V16 runs the two conditional
ones:

    V16-BTC-V6.6-CHOP   V6.6 trend follower on BTC, entries only when the market is CHOP
    V16-UNI-V6.1-CHOP   V6.1 patient pullback on UNI, entries only when the market is CHOP

The condition is the study's, unchanged: at a decision hour H, from BTC's daily closes (00:00 UTC) of completed days,
BULL = above its 50-day average and the average higher than 5 days earlier; BEAR = below and lower; CHOP = anything
else. A position opened in CHOP is managed to its own exit whatever the condition becomes.

Everything else is V6 as frozen (strategy classes and parameters, execution, fees, funding, positioning feeds, the
16-coin breadth context, the forward gate), except the book: 1000 USDT, as in the study (a 20 USDT book cannot meet
BTC's minimum order). Paper only: no Jev twins, no live mirror (app/live/v6_golive.py accepts V6 bots only).
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import threading
from typing import Any, Callable, Mapping, Sequence

from app.competition import v6_config as baseline

FREEZE_FILE = baseline.DOCS / "V16_FREEZE.json"
PROTOCOL = "V16_CONDITIONAL_PAPER_V1"
VENUE = "BYBIT_LINEAR"
STARTING_BALANCE = 1000.0
DAY = 86_400_000
FIELD: tuple[tuple[str, str, str], ...] = (("V6.6", "BTC", "CHOP"), ("V6.1", "UNI", "CHOP"))
SMA_DAYS = 50
SLOPE_DAYS = 5


@dataclasses.dataclass(frozen=True)
class BotSpecV16(baseline.BotSpecV6):
    condition: str = "CHOP"

    @property
    def control_key(self) -> str:
        return f"V16-{self.coin}-{self.strategy_id}-{self.condition}"

    @property
    def pair_id(self) -> str:
        return "v16pair:" + self.control_key


def field_plan() -> list[BotSpecV16]:
    return [BotSpecV16(sid, coin, "HOURLY", "CONTROL", cond) for sid, coin, cond in FIELD]


def settings_v16() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


# -- the market condition -----------------------------------------------------------------------------------------------
def condition_at(daily: Mapping[int, float], h: int) -> str | None:
    """BULL / BEAR / CHOP at decision instant `h` (ms) from daily closes keyed by UTC day number; None = not enough
    history (never treated as CHOP)."""
    d = h // DAY - 1                                                    # the last COMPLETED day before h
    days = [d - i for i in range(SMA_DAYS + SLOPE_DAYS)]
    if any(x not in daily for x in days):
        return None
    sma = lambda end: sum(daily[end - i] for i in range(SMA_DAYS)) / SMA_DAYS           # noqa: E731
    now, then = sma(d), sma(d - SLOPE_DAYS)
    up, rising = daily[d] > now, now > then
    return "BULL" if up and rising else "BEAR" if (not up and not rising) else "CHOP"


class DailyCloses:
    """BTC's daily closes (UTC day number -> close), shared by the bots of one worker; refreshed from Bybit's public
    daily klines by the worker. Thread-safe."""

    def __init__(self) -> None:
        self._d: dict[int, float] = {}
        self._lock = threading.Lock()

    def merge(self, rows: Sequence[tuple[int, float]]) -> None:
        with self._lock:
            for t, c in rows:
                self._d[int(t) // DAY] = float(c)

    def condition_at(self, h: int) -> str | None:
        with self._lock:
            return condition_at(self._d, h)

    def __len__(self) -> int:
        return len(self._d)


def conditional_class(base: type, condition: str, condition_at_fn: Callable[[int], str | None]) -> type:
    """`base` (a bound V6 family class) that only ENTERS in `condition`; exits and management are untouched."""
    def on_candle(self, c, ctx, _base=base.on_candle):
        sigs = _base(self, c, ctx)
        if not sigs:
            return sigs
        now = condition_at_fn(c.close_time + 1)
        kept = []
        for s in sigs:
            if getattr(s, "kind", "entry") != "entry":
                kept.append(s)
            elif now == condition:
                s.meta = {**(s.meta or {}), "market_condition": now}
                kept.append(s)
        return kept
    return type(base.__name__ + "_" + condition, (base,), {"on_candle": on_candle, "__module__": base.__module__})


# -- freeze ----------------------------------------------------------------------------------------------------------------
def code_fingerprints() -> dict[str, str]:
    return {m: baseline._src(importlib.import_module(m)) for m in ("app.competition.v16_config", "app.live.v16_worker")}


def rules_and_intervals() -> tuple[dict[str, Any], dict[str, int]]:
    v11 = json.loads((baseline.DOCS / "V11_FREEZE.json").read_text(encoding="utf-8"))
    syms = [f"{coin}USDT" for _, coin, _ in FIELD]
    return ({s: v11["rules"][s] for s in syms}, {s: int(v11["funding_interval_min"][s]) for s in syms})


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    rules, intervals = rules_and_intervals()
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "field": [list(f) for f in FIELD], "starting_balance": STARTING_BALANCE,
            "condition": {"sma_days": SMA_DAYS, "slope_days": SLOPE_DAYS, "source": "BTCUSDT daily close, UTC"},
            "breadth_set": list(base["breadth_set"]), "rules": rules, "funding_interval_min": intervals,
            "study": "docs/V16_COIN_STUDY.json"}


KEYS = ("protocol", "baseline_fingerprint", "code", "field", "starting_balance", "condition", "breadth_set", "rules",
        "funding_interval_min", "study")


def fingerprint(man: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: man.get(k) for k in KEYS}, sort_keys=True).encode()).hexdigest()[:16]


def write_freeze() -> dict[str, Any]:
    man = manifest_core()
    man["fingerprint"] = fingerprint(man)
    FREEZE_FILE.write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def load_freeze() -> dict[str, Any] | None:
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(man: Mapping[str, Any] | None) -> list[str]:
    if not man:
        return ["docs/V16_FREEZE.json is missing: V16 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V16_FREEZE.json" for k in KEYS if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE}
    return "v16x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident
