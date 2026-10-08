"""V17 SWING: the long-hold crypto families that passed their pre-registered studies, as paper bots on the 10 majors.

Operator, 2026-10-08: "build day trading / swing trading strategies that can hold trades for longer durations" and then
"needs better improved strategies, keep going". The V17 swing study (scripts/v17_swing_study.py,
docs/V17_SWING_STUDY.json) and the V18 trend study (scripts/v18_trend_study.py, docs/V18_TREND_STUDY.json) decide which
families run: FAMILIES below lists exactly the ones those studies passed or confirmed, nothing else.

Every bot is one family on one coin with its own 1000 USDT book at 1% risk per trade, as in the studies (V6 execution,
fees and real funding). The strategy code is the studied code unchanged, except where the daily bars come from: live,
the engine drops a whole daily candle when any 1m bar of that day is missing, so the bots read COMPLETED daily bars from
Bybit's public daily klines instead (DailyBars, below; a day is used only once fetched after it closed). V18.2's BTC
regime reads BTC's closes from the same store.

Paper only: no Jev twins, no live mirror (app/live/v6_golive.py accepts V6 bots only).
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from app.competition import v6_config as baseline

FREEZE_FILE = baseline.DOCS / "V17_FREEZE.json"
PROTOCOL = "V17_SWING_PAPER_V1"
VENUE = "BYBIT_LINEAR"
STARTING_BALANCE = 1000.0
DAY = 86_400_000
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "BNB")
# docs/V18_TREND_STUDY.json (2026-10-08): the only families positive in all four windows, incl. the untouched 2022 bear
# market (HOLD2). V17.1 / V17.2 passed V17 but lost on HOLD2; V18.1 / .2 / .3 / .5 failed.
FAMILIES: tuple[str, ...] = ("V17.3", "V17.4", "V18.4")
DAILY_HISTORY_DAYS = 400


@dataclasses.dataclass(frozen=True)
class BotSpecV17(baseline.BotSpecV6):
    @property
    def control_key(self) -> str:
        return f"V17-{self.coin}-{self.strategy_id}"

    @property
    def pair_id(self) -> str:
        return "v17pair:" + self.control_key


def field_plan() -> list[BotSpecV17]:
    return [BotSpecV17(sid, coin, "SWING", "CONTROL") for sid in FAMILIES for coin in COINS]


def settings_v17() -> Any:
    return dataclasses.replace(baseline.settings_v6(), strategy_starting_balance=STARTING_BALANCE)


def family_classes() -> dict[str, type]:
    from app.strategies.v17.swing import load_v17
    from app.strategies.v18.trend import load_v18
    return {**load_v17(), **load_v18()}


# -- daily bars --------------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class DayBar:
    symbol: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    close_time: int
    closed: bool = True


class DailyBars:
    """Completed UTC daily bars per symbol from Bybit's public daily klines. A bar counts only when it was fetched after
    its day ended (a bar fetched mid-day is the forming day, never used). `completed()` fetches on demand when the
    day just ended is not final yet, so a decision at 00:00 UTC sees yesterday's final bar. Thread-safe."""

    def __init__(self, fetch: Callable[[str, int, int], Sequence[Sequence[Any]]], now: Callable[[], int] | None = None):
        self._fetch = fetch
        self._now = now or (lambda: int(time.time() * 1000))
        self._bars: dict[str, dict[int, DayBar]] = {}
        self._lock = threading.Lock()
        self._sym_locks: dict[str, threading.Lock] = {}

    def merge(self, symbol: str, rows: Sequence[Sequence[Any]], fetched_ms: int) -> None:
        with self._lock:
            book = self._bars.setdefault(symbol, {})
            for r in rows:
                t = int(r[0])
                if t + DAY <= fetched_ms:                                  # final: fetched after the day closed
                    book[t // DAY] = DayBar(symbol, t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), t + DAY - 1)

    def load(self, symbol: str, days: int) -> None:
        now = self._now()
        start = (now // DAY - days) * DAY
        self.merge(symbol, self._fetch(symbol, start, now // DAY * DAY), now)

    def _ensure(self, symbol: str, day: int) -> None:
        """Make day number `day` final in the store if it has ended (one fetch per symbol at a time)."""
        with self._lock:
            if day in self._bars.get(symbol, {}):
                return
            lock = self._sym_locks.setdefault(symbol, threading.Lock())
        with lock:
            with self._lock:
                if day in self._bars.get(symbol, {}):
                    return
            now = self._now()
            if (day + 1) * DAY > now:
                return
            try:
                self.merge(symbol, self._fetch(symbol, (day - 3) * DAY, (day + 1) * DAY), now)
            except Exception:
                pass

    def completed(self, symbol: str, t: int) -> list[DayBar]:
        """Final daily bars that closed before instant `t` (ms), oldest first."""
        self._ensure(symbol, t // DAY - 1)
        with self._lock:
            book = self._bars.get(symbol, {})
            return [book[d] for d in sorted(book) if book[d].close_time < t]

    def closes(self, symbol: str) -> dict[int, float]:
        with self._lock:
            return {d: b.close for d, b in self._bars.get(symbol, {}).items()}

    def count(self, symbol: str) -> int:
        with self._lock:
            return len(self._bars.get(symbol, {}))


def bybit_daily(symbol: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    from app.backtest import bybit_archive as bb
    return bb.klines(symbol, "D", start_ms, end_ms)


def live_class(sid: str, store: DailyBars) -> type:
    """The studied family class with its daily bars (and V18.2's BTC regime) read from `store`."""
    from app.strategies.v18.trend import btc_side
    base = family_classes()[sid].for_class("SWING")

    def days(self, ctx, c, _store=store):
        return _store.completed(c.symbol, c.close_time + 1)

    attrs: dict[str, Any] = {"days": days, "__module__": base.__module__}
    if sid == "V18.2":
        def regime(h: int, _store=store) -> str | None:
            _store.completed("BTCUSDT", h)                      # makes yesterday's BTC bar final first
            return btc_side(_store.closes("BTCUSDT"), h)
        attrs["regime"] = staticmethod(regime)
    return type(base.__name__ + "_LIVE", (base,), attrs)


# -- freeze ----------------------------------------------------------------------------------------------------------------
STRATEGY_MODULES = ("app.strategies.v17.swing", "app.strategies.v18.trend")


def code_fingerprints() -> dict[str, str]:
    return {m: baseline._src(importlib.import_module(m))
            for m in ("app.competition.v17_config", "app.live.v17_worker") + STRATEGY_MODULES}


def rules_and_intervals() -> tuple[dict[str, Any], dict[str, int]]:
    v11 = json.loads((baseline.DOCS / "V11_FREEZE.json").read_text(encoding="utf-8"))
    syms = [f"{coin}USDT" for coin in COINS]
    return ({s: v11["rules"][s] for s in syms}, {s: int(v11["funding_interval_min"][s]) for s in syms})


def manifest_core() -> dict[str, Any]:
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 shared dependencies differ from their freeze")
    rules, intervals = rules_and_intervals()
    return {"protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"], "code": code_fingerprints(),
            "field": {"families": list(FAMILIES), "coins": list(COINS)}, "starting_balance": STARTING_BALANCE,
            "daily_bars": {"source": "Bybit linear public daily klines, UTC", "history_days": DAILY_HISTORY_DAYS},
            "breadth_set": list(base["breadth_set"]), "rules": rules, "funding_interval_min": intervals,
            "studies": ["docs/V17_SWING_STUDY.json", "docs/V18_TREND_STUDY.json"]}


KEYS = ("protocol", "baseline_fingerprint", "code", "field", "starting_balance", "daily_bars", "breadth_set", "rules",
        "funding_interval_min", "studies")


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
        return ["docs/V17_FREEZE.json is missing: V17 is not frozen"]
    try:
        core = manifest_core()
    except ValueError as exc:
        return [str(exc)]
    diffs = [f"{k} differs from docs/V17_FREEZE.json" for k in KEYS if json.loads(json.dumps(core[k])) != man.get(k)]
    if man.get("fingerprint") != fingerprint(man):
        diffs.append("the manifest's own fingerprint does not match its contents")
    return diffs


def experiment_identity(man: Mapping[str, Any], jev_model: Any = None) -> tuple[str, dict[str, Any]]:
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": VENUE}
    return "v17x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident
