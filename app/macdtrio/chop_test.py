"""Forward paper test of a choppy-market filter for the MACD coin bots (pre-registered in docs/MACD_CHOP_TEST.md).

Per coin (STX, DASH, XLM) two paper books from the same start, on the coin's real Binance USD-M 4h candles and funding:
CONTROL = the live rule (MACD 12/26/9, long and short, half size, 30 % disaster stop, maker costs); FILTER = the same, but
flat at a cross while CHOP(14) > 61.8. Recomputed from the stored candles every cycle, so a restart changes nothing.
Paper only: no keys, no orders. Switch: MACD_CHOP_TEST_ENABLED (default on). Evaluated once on EVALUATE_ON.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import sim

log = logging.getLogger(__name__)

COINS = ("STX", "DASH", "XLM")
CHOP_LEN, CHOP_MAX = 14, 61.8
SIZE, FEE, SLIP = 0.5, 0.0002, 0.0001             # the live coin bots: half size, maker 0.02% + 0.01% adverse
WARM_MS = 120 * 86_400_000
LOOP_S = 300
EVALUATE_ON = "2026-12-31"
BOOKS = {"control": {}, "filter": {"chop_len": CHOP_LEN, "chop_max": CHOP_MAX}}
H4 = sim.H4


def _week(t: int) -> str:
    d = datetime.fromtimestamp((t - 1) / 1000, timezone.utc).isocalendar()
    return f"{d[0]}-W{d[1]:02d}"


def _periods(times: list[int], equity: list[float], start_eq: float) -> list[dict[str, Any]]:
    last: dict[str, tuple[int, float]] = {}
    for t, e in zip(times, equity):
        last[_week(t)] = (t, e)
    out, prev = [], start_eq
    for k in sorted(last):
        out.append({"week": k, "end": last[k][0], "return_pct": (last[k][1] / prev - 1) * 100, "equity": last[k][1]})
        prev = last[k][1]
    return out


def _dd(equity: list[float], start: float) -> float:
    peak, dd = start, 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak if peak > 0 else 0.0)
    return dd * 100


class ChopPaperTest:
    def __init__(self, market: Any, env: dict | None = None, clock: Callable[[], float] = time.time):
        env = os.environ if env is None else env
        self.enabled = str(env.get("MACD_CHOP_TEST_ENABLED", "true")).strip().lower() in ("1", "true", "yes", "on")
        self.market, self.store, self.clock = market, market.store, clock
        self.stop_event = threading.Event()
        self.health: dict[str, Any] = {"status": "STARTING" if self.enabled else "DISABLED", "error": None, "last_cycle_ms": None}
        if self.enabled and not self.store.get("chop_test:start_ms"):
            self.store.set("chop_test:start_ms", (self.now() // H4 + 1) * H4)       # the first 4h boundary from now

    def now(self) -> int:
        return int(self.clock() * 1000)

    @property
    def start_ms(self) -> int | None:
        v = self.store.get("chop_test:start_ms")
        return int(v) if v else None

    # -- data ---------------------------------------------------------------------------------------------------------------
    def _bars(self, sym: str) -> list[dict]:
        key, since = f"{sym}|perp4h", self.start_ms - WARM_MS
        have = self.store.bars(key, since)
        if not have or have[0]["t"] > since + H4:
            self.store.save_bars(key, self.market.klines(sym, since, have[0]["t"] if have else self.now()))
            have = self.store.bars(key, since)
        expect = (self.now() // H4 - 1) * H4                             # the newest completed 4h candle
        if have and have[-1]["t"] < expect:
            self.store.save_bars(key, self.market.klines(sym, have[-1]["t"], self.now()))
            have = self.store.bars(key, since)
        return have

    def _funding(self, sym: str, bars: list[dict]) -> list[tuple[int, float, float]]:
        key = f"{sym}|perp"
        have = self.store.funding(key, self.start_ms)
        nxt = (have[-1][0] + 1) if have else self.start_ms
        if nxt < self.now():
            self.store.save_funding(key, self.market.client.funding(sym, nxt, self.now()))
            have = self.store.funding(key, self.start_ms)
        opens = {b["t"]: b["o"] for b in bars}
        return [(t, r, m or opens.get(t // H4 * H4) or 0.0) for t, r, m in have]

    # -- the books ---------------------------------------------------------------------------------------------------------
    def cycle(self) -> dict[str, Any]:
        start = self.start_ms
        end = (self.now() // H4) * H4                                     # completed candles only
        coins: dict[str, Any] = {}
        curves: dict[str, dict[str, dict[int, float]]] = {b: {} for b in BOOKS}
        crosses = 0
        for coin in COINS:
            sym = coin + "USDT"
            bars = self._bars(sym)
            fund = self._funding(sym, bars)
            sig = sim.signals(bars)
            crosses += sum(1 for b, s in zip(bars, sig) if s and start <= b["t"] < end)
            row = {}
            for book, kw in BOOKS.items():
                r = sim.simulate(bars, fund, start, end, size=SIZE, fee=FEE, slip=SLIP, close_at_end=False, **kw)
                curves[book][sym] = dict(zip(r["times"], r["equity"]))
                row[book] = {"metrics": r["metrics"], "open": r["open"], "weekly": _periods(r["times"], r["equity"], 10_000.0),
                             "trades": r["trades"][-50:]}
            coins[sym] = row
        combined = {}
        for book in BOOKS:
            ts = sorted(set.intersection(*[set(c) for c in curves[book].values()])) if curves[book] else []
            eq = [sum(c[t] for c in curves[book].values()) / len(curves[book]) for t in ts]
            combined[book] = {"return_pct": ((eq[-1] / 10_000.0) - 1) * 100 if eq else 0.0, "max_drawdown_pct": _dd(eq, 10_000.0),
                              "weekly": _periods(ts, eq, 10_000.0), "equity": [[t, e] for t, e in list(zip(ts, eq))[-400:]]}
        c, f = combined["control"], combined["filter"]
        out = {"name": "MACD coin bots: choppy-market filter (forward paper test)", "pre_registration": "docs/MACD_CHOP_TEST.md",
               "start_ms": start, "as_of_ms": end, "evaluate_on": EVALUATE_ON, "coins": coins, "combined": combined,
               "crosses": crosses, "rule": {"chop_len": CHOP_LEN, "chop_max": CHOP_MAX, "size": SIZE, "fee": FEE, "slip": SLIP},
               "decision_rule": "adopt only if FILTER return >= CONTROL, FILTER max DD <= CONTROL, and >= 20 crosses in total",
               "so_far": {"filter_return_ge_control": f["return_pct"] >= c["return_pct"],
                          "filter_dd_le_control": f["max_drawdown_pct"] <= c["max_drawdown_pct"], "enough_crosses": crosses >= 20},
               "paper_only": True}
        self.store.set("chop_test:summary", out)
        return out

    def summary(self) -> dict[str, Any]:
        s = self.store.get("chop_test:summary") or {"start_ms": self.start_ms, "evaluate_on": EVALUATE_ON,
                                                    "pre_registration": "docs/MACD_CHOP_TEST.md", "paper_only": True}
        return {**s, "health": dict(self.health), "read_only": True}

    # -- thread ------------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._run, name="macd-chop-test", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.cycle()
                self.health.update(status="RUNNING", error=None, last_cycle_ms=self.now())
            except Exception as exc:
                log.warning("macd chop test: %s", exc)
                self.health.update(status="DEGRADED", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            if self.stop_event.wait(LOOP_S):
                break
