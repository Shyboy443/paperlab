"""Parent side of the V6 FORWARD ARENA: starts the worker process at every boot (no manual step), relays its events
onto the realtime bus, keeps its latest status, and restarts it (with backoff) if it dies.

Environment:
    V6_FORWARD_ENABLED   true to run V6 live forward paper trading (default false: tests and local runs never touch
                         the network). DRY_RUN=true stays the hard protection against real orders -- and this worker
                         has no order path at all: every book is simulated.
    V6_FORWARD_JEV       false to run the CONTROL bots only (default true; Jev also needs JEV_ENABLED=true and
                         OPENROUTER_API_KEY)
    V8_FORWARD_JEV       the same for the V8 scalpers' +JEV twins (default true; V7 has no Jev)
    V9_FORWARD_ENABLED   true to run the V9 US-stock paper arena (needs the Alpaca paper keys for market data)
    V9_FORWARD_JEV       the same for the V9 stock scalpers' +JEV twins (default true)
    V11_FORWARD_ENABLED  true to run the V11 multi-coin scanner arena (Bybit public data, no keys)
    V12_FORWARD_ENABLED  true to run V12 BIZZY, beebots' Bizzy Bee day breakout (Bybit public data, no keys)
    V12_FORWARD_JEV      false to run Bizzy without her Jev twin (default true)
    V13_FORWARD_ENABLED  true to run V13 SNAPBACK, limit-order mean reversion over the V11 coins (Bybit public data)
    V14_FORWARD_ENABLED  true to run V14 HTF, copies of the best strategies that check the higher timeframes first
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import threading
import time
from typing import Any, Mapping

log = logging.getLogger("paperlab.live.v6service")


def _flag(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class V6ForwardService:
    def __init__(self, db_path: str, bus: Any = None, env: Mapping[str, str] | None = None, *, program: str = "V6"):
        e = os.environ if env is None else env
        if program not in ("V6", "V7", "V8", "V9", "V11", "V12", "V13", "V14"):
            raise ValueError("unknown forward program")
        self.program = program
        self.enabled = _flag(e.get(program + "_FORWARD_ENABLED"), False)
        jev = {"V6": _flag(e.get("V6_FORWARD_JEV"), True), "V8": _flag(e.get("V8_FORWARD_JEV"), True),
               "V9": _flag(e.get("V9_FORWARD_JEV"), True), "V11": _flag(e.get("V11_FORWARD_JEV"), True),
               "V12": _flag(e.get("V12_FORWARD_JEV"), True)}.get(program, False)
        self.cfg = {"db": db_path, "jev": jev, "program": program}
        self.bus = bus
        self.status: dict[str, Any] = {"status": "DISABLED" if not self.enabled else "STARTING"}
        self.started_at = 0.0
        self.restarts = 0
        self._proc: Any = None
        self._q: Any = None
        self._stop_ev: Any = None
        self._stopping = threading.Event()
        self._pump: threading.Thread | None = None
        self._last_sig: str | None = None
        self.listeners: list[Any] = []          # e.g. the live mirror: (program, event) for every worker event

    # -- lifecycle ----------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        self._spawn()
        self._pump = threading.Thread(target=self._pump_loop, name="v6-pump", daemon=True)
        self._pump.start()

    def _spawn(self) -> None:
        if self.program == "V8":                 # V7's freeze pins the shared worker: V8 has its own
            from app.live.v8_worker import worker_main
        elif self.program == "V9":               # US stocks on Alpaca data
            from app.live.v9_worker import worker_main
        elif self.program == "V11":              # multi-coin scanners on one ordered Bybit tape
            from app.live.v11_worker import worker_main
        elif self.program == "V12":              # Bizzy Bee (beebots) on the same ordered Bybit tape
            from app.live.v12_worker import worker_main
        elif self.program == "V13":              # Snapback: limit-order mean reversion on the same ordered tape
            from app.live.v13_worker import worker_main
        elif self.program == "V14":              # HTF copies of the best strategies, on the same ordered tape
            from app.live.v14_worker import worker_main
        else:
            from app.live.v6_worker import worker_main
        ctx = mp.get_context("spawn")
        self._q = ctx.Queue()
        self._stop_ev = ctx.Event()
        self._proc = ctx.Process(target=worker_main, args=(self.cfg, self._q, self._stop_ev), name="v6-forward",
                                 daemon=True)
        self._proc.start()
        self.started_at = time.time()
        self.status = {"status": "STARTING", "pid": self._proc.pid}
        log.info("V6 forward worker started (pid %s)", self._proc.pid)

    def stop(self, timeout: float = 4.0) -> None:
        self._stopping.set()
        if self._stop_ev is not None:
            self._stop_ev.set()
        if self._proc is not None:
            self._proc.join(timeout)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(1.0)

    # -- the pump: worker -> bus -------------------------------------------------------------------------------------
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
                self.status = {**self.status, "status": "RESTARTING", "error": f"worker exited with code {code}"}
                self._publish("v6", {"type": "system", "event": "WORKER_RESTARTING", "exit_code": code})
                log.warning("V6 forward worker exited (%s); restarting in %.0fs", code, backoff)
                if self._stopping.wait(backoff):
                    break
                self.restarts += 1
                backoff = min(600.0, backoff * 2)
                self._spawn()

    def _handle(self, item: tuple) -> None:
        if item[0] == "status":
            self.status = dict(item[1])
            self._publish_state()
            return
        if item[0] == "event":
            _, channel, data, public = item
            self._publish(channel, data, public)
            for fn in list(self.listeners):
                try:
                    fn(self.program.lower(), data)
                except Exception as exc:
                    log.warning("event listener failed: %s", str(exc)[:120])

    def _publish_state(self) -> None:
        st = self.status or {}
        rows = [{k: b.get(k) for k in ("key", "role", "pair_id", "live", "evaluating", "equity", "equity_live", "net",
                                        "trades", "last_bar_ts", "risk_state", "error", "program_status")}
                | {"positions": [{k: p.get(k) for k in ("symbol", "side", "qty", "entry", "mark", "upnl", "stop", "target")}
                                 for p in (b.get("open_positions") or [])]}
                for b in (st.get("bots") or [])]
        payload = {"health": self.health(), "bots": rows, "prices": st.get("prices") or {}}
        sig = repr(payload)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        # a state push is superseded by the next one: keep it out of the reconnect backlog
        self._publish("v6_state", payload, replay=False)

    def _publish(self, channel: str, data: dict[str, Any], public: bool = True, replay: bool = True) -> None:
        if self.program in ("V7", "V8", "V9", "V11", "V12", "V13", "V14"):
            channel = channel.replace("v6", self.program.lower(), 1)
        if self.bus is not None:
            try:
                self.bus.publish(channel, data, public=public, replay=replay)
            except Exception:
                pass

    # -- reads -----------------------------------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Compact, public-safe: the header, the safety strip and inspection.json read this."""
        st = self.status or {}
        bots = st.get("bots") or []
        m = st.get("market") or {}
        return {"status": st.get("status", "DISABLED"), "enabled": self.enabled, "restarts": self.restarts,
                "session_id": st.get("session_id"), "experiment_id": st.get("experiment_id"),
                "forward_start_ms": st.get("forward_start_ms"), "live_from_ms": st.get("live_from_ms"),
                "bots": len(bots), "live_bots": st.get("live_bots"),
                "controls": sum(1 for b in bots if b.get("role") == "CONTROL"),
                "jev_bots": sum(1 for b in bots if b.get("role") == "JEV"),
                "positions_open": sum(len(b.get("open_positions") or []) for b in bots),
                "evaluating": sum(1 for b in bots if b.get("evaluating")), "jev_state": st.get("jev_state"),
                "ws": (m.get("ws") or {}).get("connected"), "ws_age_s": (m.get("ws") or {}).get("age_s"),
                "klines_age_s": m.get("klines_age_s"), "rest_errors": m.get("rest_errors"),
                "barrier_last": m.get("barrier_last"), "error": st.get("error"),
                "diffs": st.get("diffs"), "detail": st.get("detail"), "session": st.get("session"),
                "sessions_live": st.get("sessions_live")}
