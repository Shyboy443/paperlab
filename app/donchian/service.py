"""Donchian Trend Rules desk: 4h Binance perps, one 1,000 USDT paper book per coin, two-year backtest. Paper only.

Every SCAN_S seconds the completed 4h candles and funding are refreshed and the rule (app/donchian/model.py) is
replayed over the stored history: once from the desk's start (the forward paper book) and once over the last
BACKTEST_DAYS days (the backtest). A position is always the whole book (1x); a signal at a 4h close fills at the next
open. No broker access anywhere.
"""
from __future__ import annotations

import os
import threading
import time

from app.divergence.market import DAY, UNIVERSE, Market
from app.divergence.store import Store

from . import model

SCAN_S, BACKTEST_DAYS, WARM_DAYS = 300, 730, 30


class DonchianService:
    """4h indicator desk; subclasses swap MODEL (simulate/channels/RULES/RULE_VERSION/TF_MS) and the names."""
    MODEL, ENV, NAME, THREAD, EXTRA = model, "DONCHIAN_ENABLED", "Donchian Desk", "donchian-scan", ()
    VENUE = "perp"

    def __init__(self, path, env=None, market=None, clock=time.time):
        env = os.environ if env is None else env
        self.enabled = env.get(self.ENV, "true").lower() in ("1", "true", "yes")
        self.store = Store(path)
        self.clock = clock
        self.stop_event = threading.Event()
        self.market = market or Market(self.stop_event)
        self.universe = list(UNIVERSE) + list(self.EXTRA)
        self.health = {"status": "STARTING" if self.enabled else "DISABLED", "enabled": self.enabled, "error": None,
                       "heartbeat_ms": None, "last_scan_ms": None}
        if not self.store.get("started_ms"):
            self.store.set("started_ms", self.now())
        self.thread = None

    def now(self):
        return int(self.clock() * 1000)

    def start(self):
        if self.enabled:
            self.thread = threading.Thread(target=self._run, name=self.THREAD, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.cycle()
                self.health.update(status="RUNNING", error=None)
            except InterruptedError:
                break
            except Exception as exc:
                self.health.update(status="DEGRADED", error=type(exc).__name__)
            self.health["heartbeat_ms"] = self.now()
            if self.stop_event.wait(SCAN_S):
                break
        self.health["status"] = "STOPPED"

    # -- data ----------------------------------------------------------------------------------------------------------------
    def _klines(self, symbol, start, end):
        out, cursor = [], start // self.MODEL.TF_MS * self.MODEL.TF_MS
        limit = 1000 if self.VENUE == "spot" else 1500                     # Binance's per-request maximum
        while cursor < end:
            part = self.market.client.get(self.VENUE, "klines", {"symbol": symbol, "interval": "4h", "startTime": cursor,
                                                              "endTime": end - 1, "limit": limit}, weight=5)
            if not part:
                break
            for r in part:
                t = int(r[0])
                if t + self.MODEL.TF_MS <= end:                                  # completed candles only
                    out.append(dict(t=t, o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5])))
            nxt = int(part[-1][0]) + self.MODEL.TF_MS
            if nxt <= cursor or len(part) < limit:
                break
            cursor = nxt
        return out

    def _refresh(self, symbol):
        now = self.now()
        start = now - (BACKTEST_DAYS + WARM_DAYS) * DAY
        have = self.store.bars(symbol, start)
        if not have or have[0]["t"] > start + 2 * DAY:
            self.store.save_bars(symbol, self._klines(symbol, start, have[0]["t"] if have else now))
            have = self.store.bars(symbol, start)
        self.store.save_bars(symbol, self._klines(symbol, have[-1]["t"] if have else start, now))
        fund = self.store.funding(symbol, start)
        self.store.save_funding(symbol, self.market.funding(symbol, (fund[-1][0] + 1) if fund else start, now))

    def cycle(self):
        started = self.store.get("started_ms")
        bt_start = self.now() - BACKTEST_DAYS * DAY
        books, backtest = {}, {}
        for symbol in self.universe:
            if self.stop_event.is_set():
                return
            try:
                self._refresh(symbol)
                bars = self.store.bars(symbol, self.now() - (BACKTEST_DAYS + WARM_DAYS) * DAY)
                fund = self.store.funding(symbol, bars[0]["t"]) if bars else []
                fwd = self.MODEL.simulate(bars, fund, symbol, start_ms=started)
                bt = self.MODEL.simulate(bars, fund, symbol, start_ms=bt_start)
                books[symbol] = {"summary": fwd["summary"], "open_trade": fwd["open_trade"], "trades": fwd["trades"][-100:],
                                 "equity_curve": fwd["equity_curve"][-500:]}
                first = next((b for b in bars if b["t"] >= bt_start), None)
                bt["summary"]["buy_hold_pct"] = 100 * (bars[-1]["c"] / first["o"] - 1) if first else None
                backtest[symbol] = {"summary": bt["summary"], "trades": bt["trades"], "open_trade": bt["open_trade"],
                                    "equity_curve": bt["equity_curve"], "start": bt_start, "end": bars[-1]["t"] + self.MODEL.TF_MS}
            except InterruptedError:
                raise
            except Exception as exc:
                books[symbol] = {"error": type(exc).__name__}
        self.store.set("books", books)
        for symbol, rep in backtest.items():
            self.store.set("backtest:" + symbol, rep)
        self.store.set("backtest_index", {s: r["summary"] for s, r in backtest.items()})
        self.health["last_scan_ms"] = self.now()

    # -- read-only views -------------------------------------------------------------------------------------------------------
    def summary(self):
        h = dict(self.health)
        h["heartbeat_age_s"] = (self.now() - h["heartbeat_ms"]) / 1000 if h["heartbeat_ms"] else None
        if self.enabled and h["heartbeat_age_s"] is not None and h["heartbeat_age_s"] > 900:
            h["status"] = "STALE"
        books = self.store.get("books", {})
        lean = {s: {k: v for k, v in b.items() if k != "equity_curve"} for s, b in books.items()}
        return {"name": self.NAME, "rule_version": self.MODEL.RULE_VERSION, "rules": self.MODEL.RULES, "health": h,
                "started_ms": self.store.get("started_ms"), "universe": self.universe, "books": lean,
                "backtest": self.store.get("backtest_index", {}), "execution": "PAPER_ONLY", "read_only": True}

    def backtest(self, symbol):
        if symbol not in self.universe:
            raise ValueError("Unsupported symbol")
        return {**(self.store.get("backtest:" + symbol) or {"status": "COMPUTING", "trades": []}), "read_only": True}

    def chart(self, symbol, around=0):
        if symbol not in self.universe:
            raise ValueError("Unsupported symbol")
        bars = self.store.bars(symbol, self.now() - (BACKTEST_DAYS + WARM_DAYS) * DAY)
        if not bars:
            raise ValueError("No candles stored yet")
        fund = self.store.funding(symbol, bars[0]["t"])
        res = self.MODEL.simulate(bars, fund, symbol, start_ms=self.now() - BACKTEST_DAYS * DAY)
        lo = around - 250 * self.MODEL.TF_MS if around else bars[-1]["t"] - 600 * self.MODEL.TF_MS
        hi = around + 200 * self.MODEL.TF_MS if around else bars[-1]["t"] + self.MODEL.TF_MS
        idx = [i for i, b in enumerate(bars) if lo <= b["t"] < hi]
        trades = [t for t in res["trades"] if t["exit_t"] and t["exit_t"] >= lo and t["entry_t"] < hi]
        if res["open_trade"] and res["open_trade"]["entry_t"] < hi:
            trades.append(res["open_trade"])
        return {"symbol": symbol, "tf": self.MODEL.TIMEFRAME, "around": around or None,
                "candles": [bars[i] for i in idx], "channels": [dict(res["channels"][i], time=bars[i]["t"]) for i in idx],
                "trades": trades, "rules": self.MODEL.RULES, "read_only": True}
