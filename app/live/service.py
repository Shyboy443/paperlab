"""Parent side of the live shadow: starts the worker process, relays its events onto the realtime
bus, keeps its latest status, and restarts it (with backoff) if it dies.

Environment (all optional):
    LIVE_SHADOW_ENABLED      true to run it (default false: tests and local runs never touch the network)
    LIVE_SHADOW_SOURCE_RUN   arena run id to take the field from (default: latest COMPLETE v2 TEST run)
    LIVE_SHADOW_MAX_BOTS     cap on CONTROL bots (default 40)
    LIVE_SHADOW_JEV          false to run CONTROL bots only, never calling Jev (default true; Jev also
                             needs JEV_ENABLED=true and OPENROUTER_API_KEY -- the kill switch stays
                             JEV_ENABLED=false)
    LIVE_SHADOW_WARMUP_HOURS REST history replayed before going live (default 504 = 21 days)
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import threading
import time
from typing import Any, Mapping

log = logging.getLogger("paperlab.live.service")


def _flag(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class LiveShadowService:
    def __init__(self, db_path: str, bus: Any = None, env: Mapping[str, str] | None = None):
        e = os.environ if env is None else env
        self.enabled = _flag(e.get("LIVE_SHADOW_ENABLED"), False)
        self.cfg = {"db": db_path, "source_run": (e.get("LIVE_SHADOW_SOURCE_RUN") or "").strip(),
                    "max_bots": int(e.get("LIVE_SHADOW_MAX_BOTS") or 40),
                    "jev": _flag(e.get("LIVE_SHADOW_JEV"), True),
                    "warmup_hours": float(e.get("LIVE_SHADOW_WARMUP_HOURS") or 504)}
        self.bus = bus
        self.status: dict[str, Any] = {"status": "DISABLED" if not self.enabled else "STARTING"}
        self.started_at = 0.0
        self.restarts = 0
        self._proc: Any = None
        self._q: Any = None
        self._stop_ev: Any = None
        self._stopping = threading.Event()
        self._pump: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        self._spawn()
        self._pump = threading.Thread(target=self._pump_loop, name="shadow-pump", daemon=True)
        self._pump.start()

    def _spawn(self) -> None:
        from app.live.worker import worker_main
        ctx = mp.get_context("spawn")
        self._q = ctx.Queue()
        self._stop_ev = ctx.Event()
        self._proc = ctx.Process(target=worker_main, args=(self.cfg, self._q, self._stop_ev),
                                 name="live-shadow", daemon=True)
        self._proc.start()
        self.started_at = time.time()
        self.status = {"status": "STARTING", "pid": self._proc.pid}
        log.info("live shadow worker started (pid %s)", self._proc.pid)

    def stop(self, timeout: float = 4.0) -> None:
        self._stopping.set()
        if self._stop_ev is not None:
            self._stop_ev.set()
        if self._proc is not None:
            self._proc.join(timeout)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(1.0)

    # -- the pump: worker -> bus -----------------------------------------------------------------
    def _pump_loop(self) -> None:
        backoff = 10.0
        while not self._stopping.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                item = None
            except (EOFError, OSError):
                item = None
            if item is not None:
                self._handle(item)
                continue
            if self._proc is not None and not self._proc.is_alive() and not self._stopping.is_set():
                code = self._proc.exitcode
                self.status = {**self.status, "status": "RESTARTING",
                               "error": f"worker exited with code {code}"}
                self._publish("shadow", {"type": "session", "status": "RESTARTING", "exit_code": code})
                log.warning("live shadow worker exited (%s); restarting in %.0fs", code, backoff)
                if self._stopping.wait(backoff):
                    break
                self.restarts += 1
                backoff = min(600.0, backoff * 2)
                self._spawn()

    def _handle(self, item: tuple) -> None:
        kind = item[0]
        if kind == "status":
            self.status = dict(item[1])
            self._publish_state()
            return
        if kind == "event":
            _, channel, data, public = item
            self._publish(channel, data, public)

    def _publish_state(self) -> None:
        """The arena's live numbers, pushed (public) when they change: header counts, each bot's
        mode / equity / open position, and the live market per coin."""
        st = self.status or {}
        rows = [{k: b.get(k) for k in ("key", "role", "pair_id", "mode", "live", "evaluating", "equity", "net",
                                        "trades", "last_bar_ts", "error")}
                | {"positions": [{k: p.get(k) for k in ("side", "qty", "entry", "mark", "upnl")}
                                 for p in (b.get("open_positions") or [])]}
                for b in (st.get("bots") or [])]
        payload = {"health": self.health(), "bots": rows, "prices": st.get("prices") or {}}
        sig = repr(payload)
        if sig == getattr(self, "_last_state_sig", None):
            return
        self._last_state_sig = sig
        self._publish("shadow_state", payload)

    def _publish(self, channel: str, data: dict[str, Any], public: bool = True) -> None:
        if self.bus is not None:
            try:
                self.bus.publish(channel, data, public=public)
            except Exception:
                pass

    # -- reads ----------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Compact, public-safe: the arena header and the health strip read this."""
        st = self.status or {}
        bots = st.get("bots") or []
        controls = [b for b in bots if b.get("role") == "CONTROL"]
        market = st.get("market") or {}
        return {
            "status": st.get("status", "DISABLED"), "session_id": st.get("session_id"),
            "enabled": self.enabled, "restarts": self.restarts,
            "active": sum(1 for b in controls if b.get("live")),
            "bots": len(bots), "controls": len(controls), "pairs": st.get("pairs", 0),
            "positions_open": sum(len(b.get("open_positions") or []) for b in bots),
            "evaluating": sum(1 for b in bots if b.get("evaluating")),
            "attack": sum(1 for b in bots if b.get("mode") == "ATTACK"),
            "warming": sum(1 for b in bots if not b.get("live")),
            "eligible_controls": len(st.get("eligible_controls") or []),
            "insufficient_controls": st.get("insufficient_controls"),
            "min_pairs": st.get("min_pairs"), "jev_state": st.get("jev_state"),
            "feed": {"klines": (market.get("klines") or {}).get("connected"),
                     "klines_age_s": (market.get("klines") or {}).get("age_s"),
                     "book": (market.get("book") or {}).get("connected"),
                     "warm_symbols": market.get("warm"), "symbols": market.get("symbols"),
                     "bars_live": market.get("bars_live"), "gaps_repaired": market.get("gaps_repaired")},
            "error": st.get("error"),
        }
