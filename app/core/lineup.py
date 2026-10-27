"""Which strategies the server runs (operator, 2026-10-24: "only keep the things we are going to make live").

LEAN (the default): Worker 1 = the Ensemble, Worker 2 = session momentum and Worker 3 = the 30-day-high book (crypto, Binance), and the IBS bot (US stocks, Alpaca), plus the shared parts they
need (keys, linked accounts, Telegram alerts, the kill switch). Every other bot, desk and paper test is still built
(its routes and history stay readable) but never started: no threads, no market data, no orders.
PAPERLAB_LINEUP=full starts everything again, exactly as before.

A removed desk that is still switched on is dealt with once at boot by `retire_desk_live`:
- armed but flat -> switched OFF and its account released (nothing to sell);
- holding a position -> left running so it keeps managing (stop, exit, kill switch) until it is switched off.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

KEPT = frozenset({"ensemble", "ensemble_live", "worker2", "worker2_live", "worker3", "worker3_live", "ibs_bot"})
LIVE_STATES = ("ARMED", "CLOSING", "HALTED")


class Lineup:
    def __init__(self, mode: str = "lean"):
        self.mode = "full" if mode.strip().lower() == "full" else "lean"
        self.started: list[str] = []
        self.skipped: list[str] = []

    @classmethod
    def from_env(cls) -> "Lineup":
        return cls(os.environ.get("PAPERLAB_LINEUP", "lean"))

    def keeps(self, name: str) -> bool:
        return self.mode == "full" or name in KEPT

    def start(self, name: str, svc: Any) -> bool:
        """svc.start() when the line-up keeps `name`; True if it was started."""
        if not self.keeps(name):
            self.skipped.append(name)
            return False
        svc.start()
        self.started.append(name)
        return True

    def retire_desk_live(self, name: str, live: Any, reason: str = "removed from the line-up (operator 2026-10-24)") -> bool:
        """A removed desk live switch. Returns True when it must keep running (it still holds a position)."""
        if self.keeps(name):
            live.start()
            self.started.append(name)
            return True
        st = live.state()
        if st.get("position") or any((st.get("positions") or {}).values()):
            log.warning("%s is removed but still holds a position: left running until it is switched off", name)
            live.start()
            self.started.append(name)
            return True
        if st.get("status") in LIVE_STATES:
            live._off(st, reason)                       # flat: OFF + account released, nothing to sell
            log.info("%s switched off and its account released (removed, flat)", name)
        self.skipped.append(name)
        return False

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "started": list(self.started), "skipped": list(self.skipped)}
