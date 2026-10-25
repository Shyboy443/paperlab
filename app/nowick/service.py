"""No-Wick Retest desk: live paper scanner, forward paper books (5m and 15m) and a 90-day backtest. Paper only.

Data: completed Binance USDT-perpetual 1-minute candles for the Divergence Desk's ten markets, kept for HISTORY_DAYS.
Every SCAN_S seconds the newest minutes are fetched and the strategy (app/nowick/model.py) is replayed over the last
LIVE_DAYS days for each market and timeframe. A trade whose entry falls after the desk started is a FORWARD paper trade:
it is recorded once (never rewritten) and its exit is filled in when the replay closes it. Each timeframe is its own
book: 1,000 USDT, 0.5% of equity risked per trade, one trade at a time per market.
The backtest replays the whole 90 days every BACKTEST_S seconds. No broker access anywhere.
"""
from __future__ import annotations

import os
import threading
import time

from app.divergence.market import DAY, MIN, UNIVERSE, Market
from app.divergence.store import Store

from . import model

HISTORY_DAYS, LIVE_DAYS = 90, 3
SCAN_S, BACKTEST_S = 30, 6 * 3600
RISK = 0.005
START_EQUITY = 1000.0


class NoWickService:
    def __init__(self, path, env=None, market=None, clock=time.time):
        env = os.environ if env is None else env
        self.enabled = env.get("NOWICK_ENABLED", "true").lower() in ("1", "true", "yes")
        self.store = Store(path)
        self.clock = clock
        self.stop_event = threading.Event()
        self.market = market or Market(self.stop_event)
        self.universe = list(UNIVERSE)
        self.threads = []
        self.health = {"status": "STARTING" if self.enabled else "DISABLED", "enabled": self.enabled, "error": None,
                       "heartbeat_ms": None, "last_scan_ms": None}
        if not self.store.get("started_ms"):
            self.store.set("started_ms", self.now())
        self.lock = threading.RLock()

    def now(self):
        return int(self.clock() * 1000)

    # -- lifecycle ---------------------------------------------------------------------------------------------------------
    def start(self):
        if not self.enabled:
            return
        for fn, name in ((self._run, "nowick-scan"), (self._backtest_loop, "nowick-backtest")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self.threads.append(t)

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
    def _refresh(self, symbol, days):
        now = self.now()
        have = self.store.bars(symbol, now - days * DAY)
        if not have or have[0]["t"] > now - days * DAY + 2 * DAY:            # backfill the start of the window
            self.store.save_bars(symbol, self.market.bars(symbol, now - days * DAY, have[0]["t"] if have else now, "1m"))
        have_last = self.store.bars(symbol, now - 2 * DAY)
        start = have_last[-1]["t"] if have_last else now - days * DAY
        self.store.save_bars(symbol, self.market.bars(symbol, start, now, "1m"))

    def cycle(self):
        started = self.store.get("started_ms")
        live = {}
        for symbol in self.universe:
            if self.stop_event.is_set():
                return
            try:
                self._refresh(symbol, LIVE_DAYS + 1)
                bars = self.store.bars(symbol, self.now() - LIVE_DAYS * DAY)
                live[symbol] = {}
                for tf in model.TIMEFRAMES:
                    res = model.simulate(bars, tf, symbol, start_ms=started)
                    for t in res["trades"]:
                        self._record(t)
                    live[symbol][tf] = {"open_levels": [{k: v for k, v in lv.items() if k != "origin_k"}
                                                        for lv in res["open_levels"]], "last_bar": bars[-1]["t"] if bars else None}
            except InterruptedError:
                raise
            except Exception as exc:
                live[symbol] = {"error": type(exc).__name__}
        self.store.set("live", live)
        self.health["last_scan_ms"] = self.now()

    def _record(self, t):
        """A forward trade is recorded once; afterwards only its exit may be filled in."""
        import json
        with self.store.lock:
            row = self.store.db.execute("SELECT data FROM records WHERE kind='trade' AND id=?", (t["id"],)).fetchone()
        old = json.loads(row[0]) if row else None
        if old is None:
            self.store.record("trade", t["id"], t["entry_t"], t)
        elif old.get("reason") == "open" and t.get("reason") != "open":
            old.update({k: t[k] for k in ("exit_t", "exit", "reason", "r", "r_gross")})
            self.store.record("trade", t["id"], t["entry_t"], old)

    # -- backtest ------------------------------------------------------------------------------------------------------------
    def _backtest_loop(self):
        while not self.stop_event.is_set():
            for symbol in self.universe:
                if self.stop_event.is_set():
                    return
                try:
                    self._refresh(symbol, HISTORY_DAYS)
                except InterruptedError:
                    return
                except Exception as exc:
                    self.store.set("backtest_error", {"symbol": symbol, "error": type(exc).__name__, "ts": self.now()})
            try:
                self._compute_backtest()
            except Exception as exc:
                self.store.set("backtest_error", {"error": type(exc).__name__, "ts": self.now()})
            if self.stop_event.wait(BACKTEST_S):
                return

    def _compute_backtest(self):
        out = {}
        for tf in model.TIMEFRAMES:
            per, trades = {}, []
            start = end = None
            for symbol in self.universe:
                bars = self.store.bars(symbol, self.now() - HISTORY_DAYS * DAY)
                if not bars:
                    continue
                res = model.simulate(bars, tf, symbol)
                per[symbol] = model.summarize(res["trades"])
                trades.extend(res["trades"])
                self.store.set(f"backtest:{tf}:{symbol}", res["trades"])
                start = bars[0]["t"] if start is None else min(start, bars[0]["t"])
                end = bars[-1]["t"] + MIN if end is None else max(end, bars[-1]["t"] + MIN)
            trades.sort(key=lambda t: t["entry_t"])
            out[tf] = {"status": "READY", "rule_version": model.RULE_VERSION, "tf": tf, "start": start, "end": end,
                       "summary": model.summarize(trades), "per_symbol": per, "trades": trades[-1500:],
                       "computed_ms": self.now()}
        for tf, rep in out.items():
            self.store.set("backtest:" + tf, rep)

    # -- read-only views -------------------------------------------------------------------------------------------------------
    def books(self):
        trades = sorted(self.store.records("trade", 5000), key=lambda t: t["entry_t"])
        out = {}
        for tf in model.TIMEFRAMES:
            mine = [t for t in trades if t["tf"] == tf]
            eq, curve = START_EQUITY, []
            for t in sorted([t for t in mine if t.get("r") is not None], key=lambda t: t["exit_t"]):
                eq *= 1 + RISK * t["r"]
                curve.append({"time": t["exit_t"], "value": eq})
            s = model.summarize(mine)
            out[tf] = {**s, "start_equity": START_EQUITY, "equity": eq, "equity_curve": curve[-2000:],
                       "open_trades": [t for t in mine if t.get("reason") == "open"],
                       "recent": list(reversed(mine))[:200]}
        return out

    def summary(self):
        h = dict(self.health)
        h["heartbeat_age_s"] = (self.now() - h["heartbeat_ms"]) / 1000 if h["heartbeat_ms"] else None
        if self.enabled and h["heartbeat_age_s"] is not None and h["heartbeat_age_s"] > 180:
            h["status"] = "STALE"
        bt = {tf: {k: v for k, v in (self.store.get("backtest:" + tf) or {"status": "COMPUTING"}).items() if k != "trades"}
              for tf in model.TIMEFRAMES}
        return {"name": "No-Wick Desk", "rule_version": model.RULE_VERSION, "rules": model.RULES, "health": h,
                "started_ms": self.store.get("started_ms"), "universe": self.universe, "books": self.books(),
                "live": self.store.get("live", {}), "backtest": bt, "backtest_error": self.store.get("backtest_error"),
                "execution": "PAPER_ONLY", "read_only": True}

    def backtest(self, tf, symbol=""):
        if tf not in model.TIMEFRAMES:
            raise ValueError("timeframe must be 5m or 15m")
        rep = self.store.get("backtest:" + tf) or {"status": "COMPUTING", "tf": tf, "trades": []}
        if symbol:
            if symbol not in self.universe:
                raise ValueError("Unsupported symbol")
            rep = {**rep, "symbol": symbol, "trades": self.store.get(f"backtest:{tf}:{symbol}") or [],
                   "summary_symbol": (rep.get("per_symbol") or {}).get(symbol)}
        return {**rep, "read_only": True}

    def chart(self, symbol, tf, around=0):
        """Candles of the chart timeframe with the 1-minute EMA20 / EMA50 (sampled at each candle close), every level
        (origin, price, what happened) and every trade in the window -- the replay of the stored 1-minute history."""
        if symbol not in self.universe or tf not in model.TIMEFRAMES:
            raise ValueError("Unsupported symbol or timeframe")
        tf_ms = model.TIMEFRAMES[tf]
        now = self.now()
        if around:
            lo, hi = around - 300 * tf_ms, around + 200 * tf_ms
        else:
            lo, hi = now - 500 * tf_ms, now
        bars = [b for b in self.store.bars(symbol, lo - DAY) if b["t"] < hi]
        if not bars:
            raise ValueError("No stored history for that window")
        res = model.simulate(bars, tf, symbol)
        cb = [c for c in model.aggregate(bars, tf_ms) if c["t"] >= lo]
        closes = [b["c"] for b in bars]
        fast, slow = model.ema_series(closes, model.FAST_EMA), model.ema_series(closes, model.SLOW_EMA)
        ema = [{"time": c["t"], "fast": fast[c["i1"] - 1], "slow": slow[c["i1"] - 1]} for c in cb]
        candles = [{k: c[k] for k in ("t", "o", "h", "l", "c", "v")} for c in cb]
        levels = [{k: v for k, v in lv.items() if k != "origin_k"} for lv in res["levels"] if lv["origin_t"] >= lo - 50 * tf_ms]
        trades = [t for t in res["trades"] if t["entry_t"] >= lo]
        return {"symbol": symbol, "tf": tf, "around": around or None, "candles": candles, "ema": ema, "levels": levels,
                "trades": trades, "rules": model.RULES, "read_only": True}
