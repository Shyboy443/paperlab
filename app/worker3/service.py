"""Worker 3 paper book: the 30-day-high long/short book (research/worker3/round5/high52_plus, selected config: top 20 by
volume, long the 5 coins closest to their 30-day high, short the 5 closest to their 30-day low, re-sized every day at
the 00:00 UTC open, 1x). The rule is app/worker3/engine.py, which reproduces the research engine exactly.

Data: the daily bars come from Worker 1's store (the Ensemble refreshes every listed USDT perp just after 00:00 UTC);
the book waits for that refresh, and refreshes those bars itself if Worker 1 has not done so by FALLBACK_MS. Funding of
the coins it holds and the listing dates live in Worker 3's own SQLite file.

The book is replayed from BOOK_FROM on the server's own data; days before the server's first day are a replay, later
days are forward paper. The day's target units and trades go to the live switch (app/worker3/live.py), which ships OFF.
Public data only; no keys, no orders here.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.ensemble.data import DAY, EXCL_DAILY, Rest, Store

from . import engine as E

log = logging.getLogger(__name__)

DATA_FROM = 1767225600000      # 2026-01-01: the bars Worker 1's store keeps
BOOK_FROM = 1782864000000      # 2026-07-01: the paper book (same start as Workers 1 and 2)
TICK_S, AFTER_OPEN_MS, FALLBACK_MS = 60, 90_000, 20 * 60_000
VERSION = "worker3-high52-top20-k5-v1"


def ymd(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class Worker3Service:
    def __init__(self, db_path: Path | str, ensemble: Any, rest: Rest | None = None, clock: Callable[[], float] = time.time,
                 telegram: Callable[[], Any] = lambda: None):
        self.store = Store(db_path)
        self.ensemble = ensemble                     # Worker 1's service: its store holds the daily bars
        self.daily = ensemble.store
        self.stop_event = threading.Event()
        self.rest = rest or Rest(self.stop_event)
        self.clock, self.telegram = clock, telegram
        self._err_alert_ts = 0
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.status: dict[str, Any] = {"phase": "starting"}

    def now(self) -> int:
        return int(self.clock() * 1000)

    def start(self) -> None:
        import os
        if os.environ.get("WORKER3_PAPER_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
            self.status = {"phase": "disabled (WORKER3_PAPER_ENABLED=false)"}
            return
        self.thread = threading.Thread(target=self._run, name="worker3-paper", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.cycle()
            except InterruptedError:
                return
            except Exception as exc:
                log.exception("worker3 cycle")
                self.status = {"phase": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "ts": self.now()}
                if self.now() - self._err_alert_ts >= 3_600_000:
                    self._err_alert_ts = self.now()
                    t = self.telegram()
                    if t is not None and hasattr(t, "live_alert"):
                        try:
                            t.live_alert(f"⚠️ Worker 3 paper book: update failed ({type(exc).__name__}: {str(exc)[:120]}). "
                                         "It retries every minute.")
                        except Exception:
                            pass
            self.stop_event.wait(TICK_S)

    def cycle(self) -> bool:
        """Run the day once its open is known and Worker 1's store has the day's bars. True when a day was processed."""
        now = self.now()
        day = now // DAY * DAY
        if now < day + AFTER_OPEN_MS or self.store.get("done_day") == day:
            if self.status.get("phase") == "starting" and self.store.get("done_day") is not None:
                self.status = {"phase": "ok", "day": ymd(self.store.get("done_day")),
                               "ts": (self.store.get("book") or {}).get("computed_ts")}
            return False
        if self.daily.get("done_day") != day:
            if now < day + FALLBACK_MS:
                self.status = {"phase": "waiting for Worker 1's daily data", "day": ymd(day), "ts": now}
                return False
            log.warning("worker3: Worker 1 has not refreshed the daily bars for %s; refreshing them here", ymd(day))
            with self.ensemble.lock:
                self.ensemble.refresh_daily(day, now)
        with self.lock:
            self.status = {"phase": "running", "day": ymd(day), "ts": now}
            if self.store.get("first_day") is None:
                self.store.set("first_day", day)
            out = self.replay(day, now)
            self.store.set("book", out)
            self.store.set("done_day", day)
            self.status = {"phase": "ok", "day": ymd(day), "ts": self.now(), "took_s": round((self.now() - now) / 1000)}
        return True

    # -- data ------------------------------------------------------------------------------------------------------------------
    def listings(self) -> dict[str, int]:
        cached = self.store.get("onboard")
        if cached and cached.get("ts", 0) > self.now() - DAY:
            return cached["map"]
        perps = self.rest.perps()
        m = {s["symbol"]: int(s.get("onboardDate") or 0) for s in perps}
        self.store.set("onboard", {"ts": self.now(), "map": m,
                                   "coin": [s["symbol"] for s in perps if s.get("underlyingType") == "COIN"]})
        return m

    def symbols(self) -> list[str]:
        self.listings()
        known = (self.store.get("onboard") or {})
        noncoin = set(known.get("map") or {}) - set(known.get("coin") or [])   # index / non-coin contracts; delisted coins stay
        return [s for s in self.daily.d1_symbols() if s not in EXCL_DAILY and s not in noncoin]

    def refresh_funding(self, syms: list[str], end: int) -> None:
        failed = []
        for s in syms:
            last = self.store.mark(f"fund:{s}")
            start = BOOK_FROM if last is None else last + 1
            if start >= end:
                continue
            try:
                rows = self.rest.funding(s, start, end)
            except InterruptedError:
                raise
            except Exception:
                failed.append(s)
                continue
            self.store.save_funding(s, rows)
            self.store.set_mark(f"fund:{s}", max([t for t, _ in rows], default=end - 1))
        if failed:
            log.warning("worker3 funding refresh failed for %d coin(s): %s", len(failed), ", ".join(failed[:8]))
            if len(failed) > max(3, len(syms) // 4):
                raise RuntimeError(f"funding refresh failed for {len(failed)} of {len(syms)} coins")

    def panel(self, day: int, syms: list[str]):
        names, days, A, _ = self.daily.d1_panel(DATA_FROM, day + DAY, syms)
        F = np.zeros_like(A["C"])
        col = {s: j for j, s in enumerate(names)}
        with self.store.lock:
            rows = self.store.db.execute("SELECT symbol,t,rate FROM funding WHERE t>=? AND t<?", (DATA_FROM, day + DAY)).fetchall()
        for s, t, r in rows:
            j = col.get(s)
            if j is not None:
                F[(t - DATA_FROM) // DAY, j] += r
        onboard = self.listings()
        ob = np.array([onboard.get(s, 0) for s in names], dtype=np.int64)
        return names, days, A, F, ob

    # -- the book ----------------------------------------------------------------------------------------------------------------
    def replay(self, day: int, now: int) -> dict[str, Any]:
        syms = self.symbols()
        names, days, A, F, ob = self.panel(day, syms)
        i0, i1 = int(np.searchsorted(days, BOOK_FROM)), int(np.searchsorted(days, day)) + 1
        r = E.run(A["O"], A["H"], A["L"], A["C"], A["QV"], F, days, ob, i0, i1, last_open_only=True)
        held = sorted({names[j] for j, *_ in r["closed"]} | {names[j] for j in r["open"]})
        self.refresh_funding(held, now)                          # funding of every coin the book ever held
        names, days, A, F, ob = self.panel(day, syms)
        r = E.run(A["O"], A["H"], A["L"], A["C"], A["QV"], F, days, ob, i0, i1, last_open_only=True)
        o = A["O"][i1 - 1]
        eq = float(r["equity"])
        u0, u1 = r["prev_units"], r["units"]
        touched = [j for j in range(len(names)) if abs(u1[j] - u0[j]) > 1e-12 * max(abs(u0[j]), abs(u1[j]), 1e-12)]
        today = {"day": day, "date": ymd(day), "equity": eq,
                 "targets": {names[j]: {"units": float(u1[j]), "price": float(o[j]), "w": float(u1[j] * o[j] / eq)}
                             for j in np.where(u1 != 0)[0]},
                 "trades": {names[j]: {"from": float(u0[j]), "to": float(u1[j]), "price": float(o[j])} for j in touched},
                 "version": VERSION}
        curve = [[ymd(t), round(e, 2)] for t, e in r["curve"]]
        months: dict[str, list[float]] = {}
        for t, e in r["curve"]:
            months.setdefault(ymd(t)[:7], []).append(e)
        mrows, prev = [], 10_000.0
        for k, v in months.items():
            mrows.append([k, round((v[-1] / prev - 1) * 100, 2)])
            prev = v[-1]
        trades = [{"symbol": names[j], "side": side, "opened": ymd(int(days[a])), "closed": ymd(int(days[min(b, len(days) - 1)])),
                   "days": int(b - a), "pnl": round(p, 2)} for j, side, a, b, p in r["closed"]]
        first = self.store.get("first_day") or day
        return {"today": today, "curve": curve, "months": mrows, "trades": trades[-200:], "n_trades": len(trades),
                "liquidations": [[names[j], ymd(int(days[i]))] for j, i in r["liquidated"]],
                "n_positions": len(today["targets"]), "longs": sum(1 for x in today["targets"].values() if x["units"] > 0),
                "shorts": sum(1 for x in today["targets"].values() if x["units"] < 0),
                "net_pct": round(eq / 100 - 100, 2), "first_forward_day": ymd(first), "computed_ts": self.now(),
                "version": VERSION}

    # -- views -------------------------------------------------------------------------------------------------------------------
    def latest(self) -> dict[str, Any] | None:
        b = self.store.get("book")
        return b["today"] if b else None

    def public(self) -> dict[str, Any]:
        b = self.store.get("book") or {}
        return {"status": self.status, "book": {k: v for k, v in b.items() if k != "today"},
                "today": {k: b["today"][k] for k in ("date", "equity")} if b.get("today") else None,
                "positions": sorted(([s.replace("USDT", ""), round(x["w"] * 100, 2)] for s, x in
                                     (b.get("today") or {}).get("targets", {}).items()), key=lambda r: -r[1]),
                "rules": "Every day at 00:00 UTC, among the 20 most-traded USDT perps: long the 5 coins whose last close "
                         "is nearest their 30-day high and short the 5 nearest their 30-day low, 1/10 of the money each, "
                         "1x, all positions re-sized daily; all fees, slippage and funding. Frozen: "
                         "research/worker3/round5/high52_plus/PREREG.md."}
