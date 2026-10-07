"""Operator-controlled paper book hosted by PaperLab's lifecycle."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from app.video_breakout.data import recent_bars
from app.video_breakout.engine import BreakoutBot, Config, ENTRY_BARS, STEP
from app.video_breakout.runtime import atomic_json, paper_step, state_lock


class VideoBreakoutService:
    def __init__(self, path, *, feed=recent_bars, can_enter=lambda: True):
        self.path = Path(path)
        self.feed = feed
        self.can_enter = can_enter
        self.process_lock = state_lock(self.path)
        self.process_lock.__enter__()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
            self.bot = (BreakoutBot.from_state(saved["book"], Config(**saved["book"]["config"]))
                        if saved else BreakoutBot())
            self.enabled = saved.get("enabled", False) if saved else False
            self.blocked = saved.get("blocked", False) if saved else False
            self.journal = saved.get("journal", []) if saved else []
            self.error = saved.get("error") if saved else None
            self.quote = saved.get("quote") if saved else None
        except Exception:
            self.process_lock.__exit__(None, None, None)
            raise
        self.lock = asyncio.Lock()
        self.task = None
        self.heartbeat = None
        self.closed = False

    def save(self):
        # Keep the chart bounded; peak/drawdown and the entire trading ledger persist.
        self.bot.curve = self.bot.curve[-2000:]
        atomic_json(self.path, {"book": self.bot.to_state(), "enabled": self.enabled,
                    "blocked": self.blocked, "journal": self.journal[-100:], "error": self.error,
                    "quote": self.quote})

    def note(self, event, detail):
        self.journal.append({"ts": int(time.time() * 1000), "event": event, "detail": detail})
        self.journal = self.journal[-100:]

    def cancel_entry(self):
        if self.bot.pending and self.bot.pending["side"] == "BUY":
            self.bot.pending = None
            self.bot.signals[-1]["canceled"] = True

    async def read_feed(self):
        observed, bars = await asyncio.to_thread(self.feed, self.bot.config.symbol)
        return observed, bars

    def accept_quote(self, observed, bars):
        current = next((c for c in bars if not c.closed), None)
        if current is None or not current.open_time <= observed < current.close_time:
            raise ValueError("A fresh current quote is required")
        from app.video_breakout.engine import validate_bar
        validate_bar(current, self.bot.config.symbol)
        self.quote = {"ts": observed, "price": current.close, "open": current.open,
                      "next_close": current.close_time}

    async def tick(self):
        async with self.lock:
            if self.blocked:
                return
            if self.enabled and not self.can_enter():
                self.enabled = False
                self.cancel_entry()
                self.note("paused", "Lab kill switch prevents new entries")
            try:
                observed, bars = await self.read_feed()
                result = paper_step(self.bot, observed, bars, 30_000, allow_entry=self.enabled)
                self.accept_quote(observed, bars)
                if result.get("fill"):
                    fill = result["fill"]
                    self.note("fill", f"{fill['side']} {fill['qty']:.8f} {self.bot.config.symbol} at {fill['price']:.2f}")
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.enabled = False
                self.blocked = True
                self.cancel_entry()
                self.error = f"{type(exc).__name__}: {exc}"
                self.note("blocked", self.error)
            self.heartbeat = int(time.time() * 1000)
            self.save()

    def start(self):
        self.task = asyncio.create_task(self.loop(), name="video-breakout-paper")

    async def loop(self):
        while True:
            await self.tick()
            await asyncio.sleep(10)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.process_lock.__exit__(None, None, None)

    async def arm(self, body):
        async with self.lock:
            if not self.can_enter():
                raise ValueError("Resume the lab kill switch before starting this strategy")
            if self.enabled:
                raise ValueError("The paper strategy is already running")
            cfg = Config(symbol="BTCUSDT", initial_cash=float(body.get("balance", self.bot.config.initial_cash)),
                         allocation=float(body.get("allocation", self.bot.config.allocation)),
                         fee_bps=float(body.get("fee_bps", self.bot.config.fee_bps)),
                         slippage_bps=float(body.get("slippage_bps", self.bot.config.slippage_bps)))
            if not 10 <= cfg.initial_cash <= 10_000_000 or cfg.fee_bps > 100 or cfg.slippage_bps > 100:
                raise ValueError("Balance must be 10..10,000,000 USDT; costs must be 0..100 bps")
            if cfg != self.bot.config and (self.bot.fills or self.bot.qty):
                raise ValueError("This book has trade history. Close the position and reset before changing its configuration")
            candidate = (BreakoutBot.from_state(self.bot.to_state(), cfg)
                         if cfg == self.bot.config else BreakoutBot(cfg))
            observed, bars = await self.read_feed()
            # Explicit operator resume synchronizes history without fictional catch-up fills.
            closed = [c for c in bars if c.closed][-ENTRY_BARS:]
            candidate.history = []
            candidate.pending = None
            for c in closed:
                candidate._append(c)
            if len(closed) != ENTRY_BARS:
                raise ValueError("Not enough completed 4h history")
            paper_step(candidate, observed, bars, 30_000, allow_entry=False)
            self.accept_quote(observed, bars)
            self.bot = candidate
            self.enabled = True
            self.blocked = False
            self.error = None
            self.note("started", "Paper entries enabled. Waiting for the next completed 4h candle; historical signals skipped")
            self.save()
            return self.summary()

    async def pause(self):
        async with self.lock:
            self.enabled = False
            self.cancel_entry()
            self.note("paused", "New entries paused. An open paper position still follows its 3-day exit")
            self.save()
            return self.summary()

    async def flatten(self, reason="manual"):
        async with self.lock:
            self.enabled = False
            self.cancel_entry()
            self.save()  # Even a quote failure must leave new entries disabled.
            if self.bot.qty:
                try:
                    observed, bars = await self.read_feed()
                    self.accept_quote(observed, bars)
                    self.bot.manual_close(observed, self.quote["price"], reason)
                    self.note("closed", "Paper position closed at the fresh observed price with fees and slippage")
                except Exception as exc:
                    self.blocked = True
                    self.error = f"Close failed: {exc}"
                    self.save()
                    raise
            self.save()
            return self.summary()

    async def reset(self):
        async with self.lock:
            if self.enabled or self.bot.qty:
                raise ValueError("Pause entries and close the paper position before resetting")
            archive = self.path.parent / "video-breakout-history" / f"{time.time_ns()}.json"
            atomic_json(archive, {"book": self.bot.to_state(), "journal": self.journal})
            self.bot = BreakoutBot(self.bot.config)
            self.blocked = False
            self.error = None
            self.quote = None
            self.note("reset", "Previous book archived; a new paper wallet starts with its configured balance")
            self.save()
            return self.summary()

    def summary(self):
        b = self.bot
        upper = max((c.high for c in b.history), default=None)
        lower = min((c.low for c in b.history[-18:]), default=None)
        status = "BLOCKED" if self.blocked else "RUNNING" if self.enabled else "MANAGING_POSITION" if b.qty else "PAUSED"
        quote_age = (int(time.time() * 1000) - self.quote["ts"]) / 1000 if self.quote else None
        return {"ok": True, "name": "Bitcoin breakout", "mode": "PAPER", "status": status,
                "enabled": self.enabled, "error": self.error, "heartbeat": self.heartbeat,
                "quote": self.quote, "quote_age_seconds": quote_age,
                "entry_high": upper, "exit_low": lower,
                "last_closed": b.history[-1].close_time if b.history else None,
                "market_bars": [{"ts": c.close_time, "close": c.close} for c in b.history],
                "cash": b.cash, "qty": b.qty, "can_start": self.can_enter(),
                "has_fills": bool(b.fills), **b.summary(),
                "fills": list(reversed(b.fills[-50:])), "trades": list(reversed(b.trades[-50:])),
                "signals": list(reversed(b.signals[-50:])), "curve": b.curve[-300:],
                "journal": list(reversed(self.journal[-20:])),
                "rules": "Completed 4h close above the prior 7-day high buys at the next open; close below the prior 3-day low exits at the next open.",
                "execution": "Paper fills at a fresh quote within 30 seconds of the next open, with fees and slippage. No exchange orders."}
