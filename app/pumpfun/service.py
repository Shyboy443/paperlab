"""The pump.fun recorder service: one PumpPortal WebSocket (new tokens + graduations, free), a poller for
post-graduation prices (DexScreener) and holder concentration (GeckoTerminal), and a heartbeat watchdog.

Research only (docs/PUMPFUN_PLAN.md): no wallet, no keys, no orders. Switch: PUMPFUN_RECORDER_ENABLED (default on).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.config import PUMPPORTAL_WS

from . import sources
from .store import PumpStore

log = logging.getLogger(__name__)

MIN, HOUR = 60_000, 3_600_000
# price sampling after a graduation (pre-registered, docs/PUMPFUN_PLAN.md): 30 s to 2 h, 2 min to 6 h, 10 min to 48 h
SCHEDULE = [(2 * HOUR, 30_000), (6 * HOUR, 2 * MIN), (48 * HOUR, 10 * MIN)]
HORIZON = 48 * HOUR
HOLDERS_AFTER = 2 * MIN                  # GeckoTerminal's holder stats lag a little behind the chain
DEX_BATCH, DEX_PER_MIN, GT_PER_MIN = 30, 50, 20
MAX_MISSES = 20                          # no pair found on 20 polls in a row (about 10 min at first): stop tracking
POLL_S, STALL_S, GAP_MIN_S, SAVE_S = 10, 600, 5, 10
SUPPLY = 1e9                             # every pump.fun token


def _flag(v: Any, default: bool) -> bool:
    return default if v is None else str(v).strip().lower() in ("1", "true", "yes", "on")


class PumpfunRecorder:
    def __init__(self, path: str | Path, env: dict | None = None, clock: Callable[[], float] = time.time,
                 telegram: Callable[[], Any] = lambda: None, getter: Callable[[str], Any] = sources.http_json):
        env = os.environ if env is None else env
        self.enabled = _flag(env.get("PUMPFUN_RECORDER_ENABLED"), True)
        self.store = PumpStore(path)
        self.clock, self.telegram, self.getter = clock, telegram, getter
        self.stop_event = threading.Event()
        self.health: dict[str, Any] = {"status": "STARTING" if self.enabled else "DISABLED", "connected": False,
                                       "last_msg_ms": None, "last_poll_ms": None, "error": None, "stalled_alerted": False,
                                       "reconnects": 0}
        self._calls: dict[str, list[int]] = {"dex": [], "gt": []}
        # a restart (deploy, crash) is a data gap too: the last message time is saved every SAVE_S, and the first message
        # after a start closes the gap since then
        saved = self.store.state("last_msg_ms")
        self._restart_from: int | None = int(saved) if saved else None
        self._last_saved = 0

    def now(self) -> int:
        return int(self.clock() * 1000)

    # -- lifecycle --------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        if not self.store.state("started_ms"):
            self.store.set_state("started_ms", str(self.now()))
        threading.Thread(target=lambda: asyncio.run(self._stream()), name="pumpfun-stream", daemon=True).start()
        threading.Thread(target=self._poll_loop, name="pumpfun-poll", daemon=True).start()
        threading.Thread(target=self._watchdog, name="pumpfun-watchdog", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.health.get("last_msg_ms"):
            self.store.set_state("last_msg_ms", str(self.health["last_msg_ms"]))

    # -- the stream -------------------------------------------------------------------------------------------------------
    def on_message(self, raw: str) -> str | None:
        """Store one stream message. Returns 'token' / 'migration' / None."""
        now = self.now()
        self.health["last_msg_ms"] = now
        if self._restart_from is not None:
            if now - self._restart_from >= GAP_MIN_S * 1000:
                self.store.add_gap(self._restart_from, now, "restart")
            self._restart_from = None
        if now - self._last_saved >= SAVE_S * 1000:
            self.store.set_state("last_msg_ms", str(now))
            self._last_saved = now
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        got = sources.parse(msg, now) if isinstance(msg, dict) else None
        if got is None:
            return None
        kind, row = got
        if kind == "token":
            self.store.add_token(row)
        else:
            self.store.add_migration(row["mint"], row["t"], row["pool"], row["sig"])
        return kind

    async def _stream(self) -> None:
        import websockets
        backoff, down_since = 1.0, None
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(PUMPPORTAL_WS, open_timeout=20, ping_interval=20, ping_timeout=20,
                                              max_size=2 ** 20) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    self.health.update(connected=True, status="RUNNING", error=None)
                    if down_since is not None and self.now() - down_since >= GAP_MIN_S * 1000:
                        self.store.add_gap(down_since, self.now(), "stream reconnect")
                    down_since, backoff = None, 1.0
                    while not self.stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        self.on_message(raw)
            except Exception as exc:
                self.health.update(connected=False, status="DEGRADED", error=f"{type(exc).__name__}: {str(exc)[:120]}")
                self.health["reconnects"] += 1
                log.warning("pumpfun stream: %s", self.health["error"])
            if down_since is None:
                down_since = self.health.get("last_msg_ms") or self.now()
            self.health["connected"] = False
            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 60.0)

    # -- prices and holders -----------------------------------------------------------------------------------------------
    def _budget(self, name: str, per_min: int) -> bool:
        now = self.now()
        self._calls[name] = [t for t in self._calls[name] if now - t < MIN]
        if len(self._calls[name]) >= per_min:
            return False
        self._calls[name].append(now)
        return True

    def poll_once(self) -> dict[str, int]:
        now = self.now()
        done = {"priced": 0, "missed": 0, "holders": 0}
        due = self.store.due_prices(now, SCHEDULE, HORIZON)
        for i in range(0, len(due), DEX_BATCH):
            batch = due[i:i + DEX_BATCH]
            if not self._budget("dex", DEX_PER_MIN):
                break
            try:
                rows = sources.dexscreener_prices(batch, now, self.getter)
            except Exception as exc:
                log.warning("pumpfun dexscreener: %s", exc)
                break
            self.store.add_prices(rows.values())
            for m in batch:
                found = rows.get(m)
                self.store.mark_polled(m, now, found)
                done["priced" if found else "missed"] += 1
        with self.store.lock:
            dead = [r[0] for r in self.store.db.execute("SELECT mint FROM migrations WHERE done=0 AND misses>=?", (MAX_MISSES,))]
        for m in dead:
            self.store.finish(m)
        for m in self.store.due_holders(now, HOLDERS_AFTER):
            if not self._budget("gt", GT_PER_MIN):
                break
            base = self.store.liquidity_base(m)
            pool_pct = base / SUPPLY * 100 if base else None
            try:
                count, top10 = sources.geckoterminal_holders(m, self.getter)
                self.store.set_holders(m, now, count, top10, pool_pct)
            except Exception as exc:
                self.store.set_holders(m, now, None, None, pool_pct, f"{type(exc).__name__}: {str(exc)[:100]}")
            done["holders"] += 1
        self.health["last_poll_ms"] = now
        return done

    def _poll_loop(self) -> None:
        while not self.stop_event.wait(POLL_S):
            try:
                self.poll_once()
            except Exception:
                log.exception("pumpfun poll")

    # -- watchdog ---------------------------------------------------------------------------------------------------------
    def check_stall(self) -> str | None:
        last = self.health.get("last_msg_ms")
        stalled = last is not None and self.now() - last > STALL_S * 1000
        msg = None
        if stalled and not self.health["stalled_alerted"]:
            self.health["stalled_alerted"] = True
            msg = f"⚠️ <b>pump.fun recorder</b>: no stream message for {(self.now() - last) // 60000} min (research data gap)."
        elif not stalled and self.health["stalled_alerted"]:
            self.health["stalled_alerted"] = False
            msg = "✅ <b>pump.fun recorder</b>: stream recovered."
        t = self.telegram()
        if msg and t is not None:
            fn = getattr(t, "ops_alert", None) or getattr(t, "alert", None)
            if fn:
                try:
                    fn(msg)
                except Exception:
                    pass
        return msg

    def _watchdog(self) -> None:
        while not self.stop_event.wait(60):
            self.check_stall()

    # -- read-only view ---------------------------------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        now = self.now()
        h = dict(self.health)
        h["last_msg_age_s"] = round((now - h["last_msg_ms"]) / 1000, 1) if h.get("last_msg_ms") else None
        started = self.store.state("started_ms")
        return {"name": "pump.fun recorder", "paper_only": True, "read_only": True, "plan": "docs/PUMPFUN_PLAN.md",
                "started_ms": int(started) if started else None, "health": h, "counts": self.store.counts(now),
                "schedule": [{"until_h": lim / HOUR, "every_s": e / 1000} for lim, e in SCHEDULE],
                "recent_graduations": self.store.recent(10),
                "sources": {"stream": "PumpPortal (new tokens + graduations)", "prices": "DexScreener",
                            "holders": "GeckoTerminal"}}
