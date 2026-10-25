"""MACD desk (MACD-2): Binance SPOT ZEC/USDT 4h with the operator's audited settings; the Donchian desk's service with
spot candles, no funding, the fixed audit backtest window and one 10,000 USDT forward paper book. Paper only."""
import time

from app.divergence.market import DAY
from app.donchian.service import DonchianService

from . import model, perp_monthly

PERP_EVERY_S = 900                                                       # the ZEC perp monthly table is rebuilt every 15 min


class MacdService(DonchianService):
    MODEL, ENV, NAME, THREAD, EXTRA = model, "MACD_ENABLED", "MACD Desk", "macd-scan", ()
    VENUE = "spot"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.universe = ["ZECUSDT"]

    def _bars(self, symbol):
        return self.store.bars(symbol, model.WARMUP_FROM)

    def _refresh(self, symbol):
        now = self.now()
        have = self._bars(symbol)
        if not have or have[0]["t"] > model.WARMUP_FROM + DAY:
            self.store.save_bars(symbol, self._klines(symbol, model.WARMUP_FROM, have[0]["t"] if have else now))
            have = self._bars(symbol)
        self.store.save_bars(symbol, self._klines(symbol, have[-1]["t"] if have else model.WARMUP_FROM, now))

    def _backtest(self, bars, symbol):
        bt = model.simulate(bars, (), symbol, start_ms=model.BACKTEST_START, end_ms=model.BACKTEST_END, close_at_end=True)
        win = [b for b in bars if model.BACKTEST_START <= b["t"] < model.BACKTEST_END]
        bt["summary"]["buy_hold_pct"] = 100 * (win[-1]["c"] / win[0]["o"] - 1) if win else None
        return bt

    # -- ZEC perpetual monthly study (operator's table, recomputed live; separate store keys from the spot candles) --------
    def _perp_klines(self, interval, step, start, end):
        out, cursor = [], start // step * step
        while cursor < end:
            part = self.market.client.get("perp", "klines", {"symbol": "ZECUSDT", "interval": interval, "startTime": cursor,
                                                             "endTime": end - 1, "limit": 1500}, weight=5)
            if not part:
                break
            out += [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5]))
                    for r in part if int(r[0]) + step <= end]                 # completed candles only
            nxt = int(part[-1][0]) + step
            if nxt <= cursor or len(part) < 1500:
                break
            cursor = nxt
        return out

    def _perp_refresh(self):
        now = self.now()
        for key, interval, step, since in (("ZECUSDT|perp4h", "4h", model.TF_MS, perp_monthly.WARMUP_FROM),
                                           ("ZECUSDT|perp1h", "1h", 3_600_000, perp_monthly.START - DAY)):
            have = self.store.bars(key, since)
            self.store.save_bars(key, self._perp_klines(interval, step, have[-1]["t"] if have else since, now))
        fund = self.store.funding("ZECUSDT|perp", perp_monthly.START - DAY)
        self.store.save_funding("ZECUSDT|perp", self.market.funding("ZECUSDT", (fund[-1][0] + 1) if fund else perp_monthly.START - DAY, now))
        rep = perp_monthly.compute(self.store.bars("ZECUSDT|perp4h", perp_monthly.WARMUP_FROM),
                                   self.store.bars("ZECUSDT|perp1h", perp_monthly.START - DAY),
                                   self.store.funding("ZECUSDT|perp", perp_monthly.START - DAY))
        rep["computed_ms"] = now
        self.store.set("perp_monthly", rep)

    def perp_monthly(self):
        return {**(self.store.get("perp_monthly") or {"status": "COMPUTING", "months": []}), "read_only": True}

    def cycle(self):
        if time.time() - getattr(self, "_perp_at", 0) >= PERP_EVERY_S:
            try:
                self._perp_refresh()
                self._perp_at = time.time()
            except InterruptedError:
                raise
            except Exception as exc:                                     # never block the desk's own scan
                self.health["perp_error"] = type(exc).__name__
        started = self.store.get("started_ms")
        books, backtest = {}, {}
        for symbol in self.universe:
            if self.stop_event.is_set():
                return
            try:
                self._refresh(symbol)
                bars = self._bars(symbol)
                fwd = model.simulate(bars, (), symbol, start_ms=started)
                bt = self._backtest(bars, symbol)
                books[symbol] = {"summary": fwd["summary"], "open_trade": fwd["open_trade"], "trades": fwd["trades"][-100:],
                                 "equity_curve": fwd["equity_curve"][-500:]}
                backtest[symbol] = {"summary": bt["summary"], "trades": bt["trades"], "open_trade": None,
                                    "equity_curve": bt["equity_curve"], "start": model.BACKTEST_START, "end": model.BACKTEST_END}
            except InterruptedError:
                raise
            except Exception as exc:
                books[symbol] = {"error": type(exc).__name__}
        self.store.set("books", books)
        for symbol, rep in backtest.items():
            self.store.set("backtest:" + symbol, rep)
        self.store.set("backtest_index", {s: r["summary"] for s, r in backtest.items()})
        self.health["last_scan_ms"] = self.now()

    def chart(self, symbol, around=0):
        if symbol not in self.universe:
            raise ValueError("Unsupported symbol")
        bars = self._bars(symbol)
        if not bars:
            raise ValueError("No candles stored yet")
        bt = self._backtest(bars, symbol)
        fwd = model.simulate(bars, (), symbol, start_ms=max(self.store.get("started_ms"), model.BACKTEST_END))
        ch = model.channels(bars)
        lo = around - 250 * model.TF_MS if around else bars[-1]["t"] - 600 * model.TF_MS
        hi = around + 200 * model.TF_MS if around else bars[-1]["t"] + model.TF_MS
        idx = [i for i, b in enumerate(bars) if lo <= b["t"] < hi]
        trades = [t for t in bt["trades"] + fwd["trades"] if t["exit_t"] and t["exit_t"] >= lo and t["entry_t"] < hi]
        if fwd["open_trade"] and fwd["open_trade"]["entry_t"] < hi:
            trades.append(fwd["open_trade"])
        return {"symbol": symbol, "tf": model.TIMEFRAME, "market": "spot", "around": around or None,
                "candles": [bars[i] for i in idx], "channels": [dict(ch[i], time=bars[i]["t"]) for i in idx],
                "trades": trades, "rules": model.RULES, "read_only": True}
