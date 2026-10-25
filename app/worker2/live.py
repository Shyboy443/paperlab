"""Worker 2 LIVE switch: copies the Worker 2 paper book (app/worker2/service.py) onto ONE linked Binance USD-M account.

Same machinery as Worker 1's switch (app/ensemble/live.py: dedicated flat account, 1x isolated, market orders, units
scaled by the account's equity / the book's equity x SCALE, reconciliation every minute, kill switch, no loss limit
unless one is set), with two differences:
- the book has two events a day: 16:00 UTC opens the day's positions, 02:00 UTC closes them; every event moves the
  account to the book's targets (closes first);
- an OPEN is only placed while it is fresh (MAX_LATE_MS after 16:00). After a restart or a late switch-on the bot waits
  for the next 16:00 instead of buying a move hours late. Closing is always allowed.
Ships OFF; the operator switches it on from the private dashboard.
"""
from __future__ import annotations

from typing import Any

from app.ensemble.live import EnsembleLive

MAX_LATE_MS = 15 * 60_000


class Worker2Live(EnsembleLive):
    NAME = "Worker 2"
    KIND, REF = "bot", "Worker2"
    CONFIRM = "GO LIVE WORKER 2"
    LABEL = "Worker 2"
    MIN_AMOUNT = 200.0      # at most 5 positions: 200 USDT keeps ~99.5% of orders above Binance's 5 USDT minimum
    THREAD = "worker2-live"

    def symbols(self) -> list[str]:
        t = self.book.latest() or {}
        return sorted(t.get("targets", {}))

    def _book_ready(self, today: dict[str, Any] | None) -> bool:
        return today is not None

    def _armed_note(self, today: dict[str, Any]) -> str:
        if today.get("kind") == "open" and self.now() - int(today["day"]) <= MAX_LATE_MS and today.get("targets"):
            return f"opening today's {len(today['targets'])} positions now (1x isolated)."
        return "it trades from the next 16:00 UTC."

    def view(self) -> dict[str, Any]:
        v = super().view()
        t = self.book.latest() or {}
        v["event"] = {"kind": t.get("kind"), "date": t.get("date")}
        return v

    async def _rebalance(self, st: dict[str, Any], c: Any, today: dict[str, Any], full: bool) -> None:
        if today.get("kind") == "open" and self.now() - int(today["day"]) > MAX_LATE_MS:
            if today.get("targets"):
                self._log(st, "missed", day=today.get("date"), coins=len(today["targets"]),
                          why="the 16:00 open is more than 15 minutes old; waiting for the next one")
            if not any((st.get("positions") or {}).values()):
                st["last_day"] = today["day"]
                self._put(st)
                return
            today = {**today, "targets": {}}                # leftovers of a missed close: close them, open nothing late
        await super()._rebalance(st, c, today, True)
