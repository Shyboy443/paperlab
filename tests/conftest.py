"""Shared test fixtures: synthetic candles, a fake MarketContext, in-memory storage, fixed clock, rules."""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any, Sequence

import pytest

from app.config import Settings, load_settings
from app.core.indicators import IndicatorCache, last as _last
from app.core.portfolio import Portfolio
from app.core.risk import RiskManager, StrategyMeta
from app.core.signals import SignalBoard
from app.core.storage import Storage
from app.core.types import BookSnapshot, Candle, FundingInfo, MarketRules, Tick, VirtualPosition, tf_ms

BTC_RULES = MarketRules("BTCUSDT", 0.1, 0.0001, 0.0001, 50.0, 0.025, 2, 4)
ETH_RULES = MarketRules("ETHUSDT", 0.01, 0.001, 0.001, 20.0, 0.025, 2, 3)
SOL_RULES = MarketRules("SOLUSDT", 0.01, 0.01, 0.01, 5.0, 0.025, 4, 2)
RULES = {"BTCUSDT": BTC_RULES, "ETHUSDT": ETH_RULES, "SOLUSDT": SOL_RULES}
T0 = 1_760_000_000_000  # a fixed epoch-ms origin (UTC 00:00 aligned below)
DAY_MS = 86_400_000
T0 = T0 - (T0 % DAY_MS)


class FixedClock:
    def __init__(self, now: int = T0):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> int:
        self.now += ms
        return self.now


def settings_factory(mode: str = "FUTURES_TESTNET", dry_run: bool = True, data_dir: str = "data",
                     balance: float | None = None, **over: Any) -> Settings:
    """Build Settings for a test. `balance` is the per-strategy isolated book (STRATEGY_STARTING_BALANCE);
    anything else goes through **over as a raw env var."""
    env = {"MODE": mode, "DASHBOARD_PASSWORD": "test-pw", "DATA_DIR": data_dir,
           "DRY_RUN": "true" if dry_run else "false",
           # a shipped results seed must not leak into test databases
           "SEED_RESULTS": "false"}
    if not dry_run:
        env.update({"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20})
    if balance is not None:
        env["STRATEGY_STARTING_BALANCE"] = str(balance)
    env.update({k: str(v) for k, v in over.items()})
    return load_settings(env)


def make_candles(closes: Sequence[float] | None = None, n: int = 300, symbol: str = "BTCUSDT", tf: str = "1m",
                 start_ts: int = T0, volumes: Sequence[float] | None = None, wick: float = 0.001,
                 seed: int = 1, drift: float = 0.0, vol: float = 0.002, source: str = "live",
                 highs: Sequence[float] | None = None, lows: Sequence[float] | None = None,
                 opens: Sequence[float] | None = None) -> list[Candle]:
    """Build closed candles. Pass explicit closes (and optionally highs/lows/opens/volumes) for deterministic
    scenarios, or let a seeded random walk generate n bars."""
    rng = random.Random(seed)
    if closes is None:
        px = 100.0
        closes = []
        for _ in range(n):
            px *= math.exp(rng.gauss(drift, vol))
            closes.append(px)
    closes = list(closes)
    step = tf_ms(tf)
    out: list[Candle] = []
    prev_close = closes[0]
    for i, c in enumerate(closes):
        o = opens[i] if opens is not None else prev_close
        h = highs[i] if highs is not None else max(o, c) * (1 + abs(rng.gauss(0, wick)))
        l = lows[i] if lows is not None else min(o, c) * (1 - abs(rng.gauss(0, wick)))
        h = max(h, o, c)
        l = min(l, o, c)
        v = volumes[i] if volumes is not None else rng.uniform(50, 150)
        ot = start_ts + i * step
        out.append(Candle(symbol, tf, ot, o, h, l, c, v, ot + step - 1, True, v * c, int(v), source))
        prev_close = c
    return out


class FakeCtx:
    """Minimal MarketContext for strategy tests: real IndicatorCache + SignalBoard over supplied candles."""

    def __init__(self, candles: dict[tuple[str, str], Sequence[Candle]] | None = None, symbols: Sequence[str] = ("BTCUSDT",),
                 venue: str = "futures", now: int | None = None, enabled: Sequence[str] = ()):
        self.symbols = list(symbols)
        self._candles: dict[tuple[str, str], deque[Candle]] = {k: deque(v, maxlen=600) for k, v in (candles or {}).items()}
        self.cache = IndicatorCache()
        self.board = SignalBoard()
        for sid in enabled:
            self.board.set_enabled(sid, True)
        self.venue = venue
        self.prices: dict[str, float] = {}
        self.marks: dict[str, float] = {}
        self.books: dict[str, BookSnapshot] = {}
        self.fundings: dict[str, FundingInfo] = {}
        self.positions: dict[str, list[VirtualPosition]] = {}
        self.ticks: dict[str, list[Tick]] = {}
        self.wallets: dict[str, dict[str, float]] = {}
        self._now = now
        self.rules_map = dict(RULES)

    # -- helpers for tests --
    def set_candles(self, symbol: str, tf: str, candles: Sequence[Candle]) -> None:
        self._candles[(symbol, tf)] = deque(candles, maxlen=600)
        self.cache.invalidate(symbol, tf)

    def push(self, c: Candle) -> None:
        dq = self._candles.setdefault((c.symbol, c.tf), deque(maxlen=600))
        dq.append(c)
        self.cache.invalidate(c.symbol, c.tf)
        self.prices.setdefault(c.symbol, c.close)
        self.prices[c.symbol] = c.close

    # -- MarketContext --
    def now_ms(self) -> int:
        if self._now is not None:
            return self._now
        newest = max((dq[-1].close_time for dq in self._candles.values() if dq), default=T0)
        return newest + 1

    def venue_kind(self) -> str:
        return self.venue

    def candles(self, symbol: str, tf: str) -> Sequence[Candle]:
        return self._candles.get((symbol, tf), ())

    def forming(self, symbol: str, tf: str) -> Candle | None:
        return None

    def last_price(self, symbol: str) -> float | None:
        if symbol in self.prices:
            return self.prices[symbol]
        for tf in ("1m", "5m", "15m"):
            dq = self._candles.get((symbol, tf))
            if dq:
                return dq[-1].close
        return None

    def mark_price(self, symbol: str) -> float | None:
        return self.marks.get(symbol, self.last_price(symbol))

    def book(self, symbol: str) -> BookSnapshot | None:
        return self.books.get(symbol)

    def funding(self, symbol: str) -> FundingInfo | None:
        return self.fundings.get(symbol)

    def ind(self, symbol: str, tf: str, name: str, **params: Any) -> Any:
        return self.cache.get(symbol, tf, name, self._candles.get((symbol, tf), deque()), **params)

    def ind_last(self, symbol: str, tf: str, name: str, **params: Any) -> float | None:
        series = self.ind(symbol, tf, name, **params)
        if isinstance(series, tuple):
            series = series[0]
        return _last(series)

    def positions_of(self, strategy_id: str) -> list[VirtualPosition]:
        return list(self.positions.get(strategy_id, []))

    def rules(self, symbol: str) -> MarketRules:
        return self.rules_map[symbol]

    def recent_trades(self, symbol: str, window_ms: int) -> list[Tick]:
        cutoff = self.now_ms() - window_ms
        return [t for t in self.ticks.get(symbol, []) if t.ts >= cutoff]

    def is_enabled(self, strategy_id: str) -> bool:
        return self.board.is_enabled(strategy_id)

    def wallet_info(self, strategy_id: str) -> dict[str, float]:
        w = self.wallets.get(strategy_id, {"allocation": 500.0, "equity": 500.0, "margin_used": 0.0})
        w = dict(w)
        w.setdefault("available", w["equity"] - w["margin_used"])
        return w


def run_strategy(strategy: Any, ctx: FakeCtx, candles: Sequence[Candle], warm: int = 0) -> list[tuple[Candle, list]]:
    """Push candles one by one, calling on_candle for each after `warm` bars; returns (candle, signals) pairs."""
    out = []
    for i, c in enumerate(candles):
        ctx.push(c)
        if i < warm:
            continue
        sigs = strategy.on_candle(c, ctx)
        if sigs:
            out.append((c, sigs))
    return out


def force_equity(portfolio: Portfolio, strategy_id: str, target: float,
                 prices: dict[str, float] | None = None) -> float:
    """Move a wallet's realized PnL so its equity is exactly `target` (uPnL of open positions included).

    Used by the lives tests to park a 100 USDT book just under its -25% halt floor without having to
    engineer a losing trade of exactly the right size.
    """
    w = portfolio.wallets[strategy_id]
    upnl = portfolio.upnl(strategy_id, prices or {})
    w.realized = target - (w.allocation - w.fees + w.funding + upnl)
    return portfolio.wallet_equity(strategy_id, prices or {})


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return settings_factory(data_dir=str(tmp_path / "data"))


@pytest.fixture
def mem_storage() -> Storage:
    st = Storage(":memory:")
    yield st
    st.close()


@pytest.fixture
def portfolio(settings, clock) -> Portfolio:
    pf = Portfolio(settings, RULES, clock)
    pf.fixed_slippage_bps = 2.0
    return pf


@pytest.fixture
def risk(settings, portfolio, clock) -> RiskManager:
    rm = RiskManager(settings, portfolio, RULES, clock)
    rm.state.engine_running = True
    rm.roll_day(10_000.0, "2026-01-01")
    return rm


def meta_for(sid: str = "S01", enabled: bool = True, size_mult: float = 1.0, leverage: int = 15, max_positions: int = 1,
             min_rr: float = 2.5, supported: bool = True) -> StrategyMeta:
    return StrategyMeta(sid, enabled, size_mult, leverage, max_positions, min_rr, supported)


# ---- engine harness ---------------------------------------------------------------------------
# A real Engine over an in-memory (or tmp) Storage, a fake exchange client and a fake feed. Everything
# else (boot order, wallets, risk, analytics) is the production code.

class FakeClient:
    """The only client calls engine_boot makes in DRY_RUN: load_rules and backfill."""

    def __init__(self, candles: Sequence[Candle] | None = None, symbols: Sequence[str] = ("BTCUSDT",),
                 tfs: Sequence[str] = ("1m", "5m", "15m"), bars: int = 60):
        self.symbols = list(symbols)
        self.tfs = list(tfs)
        self.bars = bars
        self._explicit = list(candles) if candles is not None else None
        self.served: set[tuple[str, str]] = set()

    async def load_rules(self, symbols: Sequence[str]) -> dict[str, MarketRules]:
        return {s: RULES.get(s, MarketRules(s, 0.001, 0.001, 0.001, 5.0, 0.025, 2, 3)) for s in symbols}

    async def backfill(self, symbol: str, tf: str, since: int | None, until: int, max_bars: int | None = None):
        if (symbol, tf) in self.served:
            return []
        self.served.add((symbol, tf))
        if self._explicit is not None:
            return [c for c in self._explicit if c.symbol == symbol and c.tf == tf]
        base = {"BTCUSDT": 65000.0, "ETHUSDT": 3000.0, "SOLUSDT": 150.0}.get(symbol, 100.0)
        return make_candles(closes=[base * (1 + 0.0001 * i) for i in range(self.bars)], symbol=symbol, tf=tf)


class FakeFeed:
    """Stands in for MarketFeed: connected from the start, no sockets."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.market = {"connected": True, "detail": "fake"}
        self.user = {"connected": False, "detail": "fake"}
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def status(self) -> dict[str, Any]:
        return {"market": dict(self.market), "user": dict(self.user)}


def make_engine(settings: Settings, storage: Storage, clock: Any | None = None, client: Any | None = None) -> Any:
    from app.core.engine import Engine
    from app.strategies.registry import load_all
    e = Engine(settings, storage, client if client is not None else FakeClient(symbols=settings.symbols),
               load_all(), clock=clock)
    e.portfolio.fixed_slippage_bps = 2.0
    return e


async def boot_engine(settings: Settings, storage: Storage, monkeypatch: Any, clock: Any | None = None,
                      client: Any | None = None) -> Any:
    """Run the real boot() with a fake feed. Caller must `await engine.stop()`."""
    from app.core import engine_boot
    monkeypatch.setattr(engine_boot, "MarketFeed", FakeFeed)
    e = make_engine(settings, storage, clock, client)
    await e.boot()
    return e


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    """A booted engine on a fresh file DB with one symbol (20 isolated 100 USDT books)."""
    s = settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS="BTCUSDT")
    storage = Storage(str(tmp_path / "paperlab.db"))
    e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
    yield e
    await e.stop()
    storage.close()
