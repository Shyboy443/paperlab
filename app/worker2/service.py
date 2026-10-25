"""Worker 2 paper book: afternoon session momentum, the frozen rule of research/worker2/session/PREREG.md (rule P).

The rule (r4_09 IM ALT H=16 k=2 X=26 K=5 W=480, mom, plus the pre-registered vol sizing):
- universe of day d: the top 40 USDT perps by quote volume over the 30 days before d's 00:00 UTC open (>= 24 days
  traded, listed >= 30 days), minus BTC and ETH;
- sigma = std of a coin's hourly open-to-open log returns over the 480 h before 00:00 (bars t-481..t-2, needs > 80 %);
- z = log(close of the 15:00 bar / open of the 00:00 bar) / (sigma * sqrt(16)); |z| >= 2 -> a position in the direction
  of the move, entered at 16:00 UTC and closed at 02:00 UTC the next day;
- size = equity / max(n signals, 5) x min(1, 1.154 % / sigma), 1x, the rest stays in cash;
- costs 0.05 % fee + 0.02 % slippage per side, plus the funding stamps inside the hold.

The book is replayed from REPLAY_FROM on the server's own data, so it is the research rule on live data. Days before the
server's first day are a replay; later days are forward paper. Differences from the research run: the universe only
knows coins Binance still lists (Worker 1's daily store), and the 1 %-of-bar-volume notional cap is left out (it never
binds at the live sizes).

Two events a day go to the live switch (app/worker2/live.py): at 16:00 "open" (target units) and at 02:00 "close"
(no targets). Public data only; no keys, no orders here.
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

log = logging.getLogger(__name__)

HOUR = 3_600_000
REPLAY_FROM = 1782864000000        # 2026-07-01: the paper book starts here (same start as Worker 1's)
H, X, K, W, ZK, SIG_REF = 16, 26, 5, 480, 2.0, 0.01154
UNIVERSE_N, UNIVERSE_DAYS = 40, 30
FEE, SLIP = 0.0005, 0.0002
AFTER_BAR_MS = 40_000              # wait this long after the hour for Binance to publish the completed bar
TICK_S = 30
EXCLUDE = {"BTCUSDT", "ETHUSDT"}
VERSION = "worker2-session-P-v1"


def ymd(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class H1Store(Store):
    """Worker 1's Store schema plus completed hourly bars."""

    def __init__(self, path: Path | str):
        super().__init__(path)
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS h1(symbol TEXT, t INTEGER, o REAL, c REAL, PRIMARY KEY(symbol, t))")

    def save_h1(self, symbol: str, rows: list[list], now: int) -> None:
        recs = [(symbol, int(r[0]), float(r[1]), float(r[4])) for r in rows if int(r[0]) + HOUR <= now]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO h1 VALUES(?,?,?,?)", recs)

    def last_h1(self, symbol: str) -> int | None:
        with self.lock:
            row = self.db.execute("SELECT MAX(t) FROM h1 WHERE symbol=?", (symbol,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def h1_panel(self, syms: list[str], start: int, end: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        times = np.arange(start, end, HOUR, dtype=np.int64)
        col = {s: j for j, s in enumerate(syms)}
        O = np.full((len(times), len(syms)), np.nan)
        C = np.full_like(O, np.nan)
        qs = ",".join("?" * len(syms))
        with self.lock:
            rows = self.db.execute(f"SELECT symbol,t,o,c FROM h1 WHERE t>=? AND t<? AND symbol IN ({qs})",
                                   (start, end, *syms)).fetchall()
        for s, t, o, c in rows:
            i = (t - start) // HOUR
            O[i, col[s]], C[i, col[s]] = o, c
        return times, O, C

    def funding_sum(self, syms: list[str], start: int, end: int) -> dict[str, list[tuple[int, float]]]:
        qs = ",".join("?" * len(syms))
        with self.lock:
            rows = self.db.execute(f"SELECT symbol,t,rate FROM funding WHERE t>=? AND t<? AND symbol IN ({qs})",
                                   (start, end, *syms)).fetchall()
        out: dict[str, list[tuple[int, float]]] = {}
        for s, t, r in rows:
            out.setdefault(s, []).append((t, r))
        return out


def hvol(O: np.ndarray, t: int, w: int = W) -> np.ndarray:
    """std of hourly log returns LR[t-w-1 .. t-2] (LR[i] = log(O[i+1] / O[i])), as research/monthly20/r4_09_session/base.py."""
    seg = O[max(t - w - 1, 0):max(t, 0)]
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(seg[1:] / seg[:-1])
    n = np.isfinite(lr).sum(0)
    x = np.nan_to_num(lr)
    with np.errstate(invalid="ignore", divide="ignore"):
        v = (x * x).sum(0) / n - (x.sum(0) / n) ** 2
    return np.where(n > w * 0.8, np.sqrt(np.maximum(v, 0)), np.nan)


def day_signals(O: np.ndarray, C: np.ndarray, b: int) -> list[tuple[int, float, float, float]]:
    """Signals of the day whose 00:00 bar is row b: (column, side, size multiplier, sigma)."""
    v = hvol(O, b)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.log(C[b + H - 1] / O[b]) / (v * np.sqrt(H))
    out = []
    for j in np.where(np.isfinite(z) & (np.abs(z) >= ZK))[0]:
        out.append((int(j), float(np.sign(z[j])), float(min(1.0, SIG_REF / v[j])), float(v[j])))
    return out


class Worker2Service:
    def __init__(self, db_path: Path | str, daily: Any, rest: Rest | None = None, clock: Callable[[], float] = time.time,
                 telegram: Callable[[], Any] = lambda: None):
        self.store = H1Store(db_path)
        self.daily = daily                         # Worker 1's Store: daily bars of every listed USDT perp
        self.stop_event = threading.Event()
        self.rest = rest or Rest(self.stop_event)
        self.clock, self.telegram = clock, telegram
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.status: dict[str, Any] = {"phase": "starting"}

    def now(self) -> int:
        return int(self.clock() * 1000)

    def start(self) -> None:
        import os
        if os.environ.get("WORKER2_PAPER_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
            self.status = {"phase": "disabled (WORKER2_PAPER_ENABLED=false)"}
            return
        self.thread = threading.Thread(target=self._run, name="worker2-paper", daemon=True)
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
                log.exception("worker2 cycle")
                self.status = {"phase": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "ts": self.now()}
            self.stop_event.wait(TICK_S)

    # -- which event is current --------------------------------------------------------------------------------------------
    @staticmethod
    def event_at(now: int) -> tuple[str, int, int]:
        """(kind, event time, signal day): the 16:00 open of today, else the 02:00 close of yesterday's positions, else
        (00:00-02:00) yesterday's open, still held."""
        d = now // DAY * DAY
        if now >= d + H * HOUR + AFTER_BAR_MS:
            return "open", d + H * HOUR, d
        if now >= d + (X - 24) * HOUR + AFTER_BAR_MS:
            return "close", d + (X - 24) * HOUR, d - DAY
        return "open", d - DAY + H * HOUR, d - DAY

    def cycle(self) -> bool:
        now = self.now()
        kind, ets, sday = self.event_at(now)
        done = self.store.get("event") or {}
        if done.get("kind") == kind and done.get("day") == ets:
            if self.status.get("phase") == "starting":
                self.status = {"phase": "ok", "event": kind, "ts": (self.store.get("book") or {}).get("computed_ts")}
            return False
        with self.lock:
            self.status = {"phase": "running", "ts": now}
            if self.store.get("first_day") is None:
                self.store.set("first_day", sday)
            book = self.compute(now, kind, ets, sday)
            self.store.set("book", book)
            self.store.set("event", book["event"])
            self.status = {"phase": "ok", "event": kind, "ts": self.now(), "took_s": round((self.now() - now) / 1000)}
        return True

    # -- data ----------------------------------------------------------------------------------------------------------------
    def listings(self) -> dict[str, int]:
        cached = self.store.get("onboard")
        if cached and cached.get("ts", 0) > self.now() - DAY:
            return cached["map"]
        m = {s["symbol"]: int(s.get("onboardDate") or 0) for s in self.rest.perps()
             if s.get("status") == "TRADING" and s.get("underlyingType") == "COIN"}
        self.store.set("onboard", {"ts": self.now(), "map": m})
        return m

    def universes(self, days: list[int]) -> dict[int, list[str]]:
        saved = {int(k): v for k, v in (self.store.get("universes") or {}).items()}
        need = [d for d in days if d not in saved]
        if need:
            onboard = self.listings()
            syms = [s for s in self.daily.d1_symbols() if s in onboard and s not in EXCL_DAILY]
            names, _, A, _ = self.daily.d1_panel(min(need) - UNIVERSE_DAYS * DAY, max(need), syms)
            base = min(need) - UNIVERSE_DAYS * DAY
            for d in need:
                i1 = (d - base) // DAY
                qv, c = A["QV"][i1 - UNIVERSE_DAYS:i1], A["C"][i1 - UNIVERSE_DAYS:i1]
                vol = np.nan_to_num(qv).sum(0)
                ok = (np.isfinite(c).sum(0) >= UNIVERSE_DAYS * 0.8) & (vol > 0)
                ok &= np.array([onboard.get(s, 0) <= d - UNIVERSE_DAYS * DAY for s in names])
                ranked = sorted(((vol[j], s) for j, s in enumerate(names) if ok[j]), reverse=True)[:UNIVERSE_N]
                saved[d] = [s for _, s in ranked if s not in EXCLUDE]
            self.store.set("universes", {str(k): v for k, v in saved.items()})
        return {d: saved[d] for d in days}

    def refresh_h1(self, syms: list[str], start: int, now: int) -> None:
        end = now // HOUR * HOUR
        for s in syms:
            last = self.store.last_h1(s)
            since = start if last is None or last < start else last + HOUR
            if since >= end:
                continue
            rows = self.rest.klines(s, "1h", since, end)
            if rows:
                self.store.save_h1(s, rows, now)

    def refresh_funding(self, syms: list[str], start: int, now: int) -> None:
        for s in syms:
            last = self.store.mark(f"fund:{s}")
            since = start if last is None else last + 1
            if since >= now:
                continue
            rows = self.rest.funding(s, since, now)
            self.store.save_funding(s, rows)
            self.store.set_mark(f"fund:{s}", max([t for t, _ in rows], default=now - 1))

    # -- the book --------------------------------------------------------------------------------------------------------------
    def compute(self, now: int, kind: str, ets: int, sday: int) -> dict[str, Any]:
        days = list(range(REPLAY_FROM, sday + DAY, DAY))
        unis = self.universes(days)
        syms = sorted({s for v in unis.values() for s in v})
        start = REPLAY_FROM - (W + 2) * HOUR
        self.refresh_h1(syms, start, now)
        self.refresh_funding(syms, REPLAY_FROM, now)
        times, O, C = self.store.h1_panel(syms, start, sday + DAY + X * HOUR)
        fund = self.store.funding_sum(syms, REPLAY_FROM, now)
        col = {s: j for j, s in enumerate(syms)}
        eq = 10_000.0
        curve, trades, months = [], [], {}
        today_sig: list[dict[str, Any]] = []
        eq_open = eq
        for d in days:
            b = int((d - start) // HOUR)
            js = np.array([col[s] for s in unis[d] if s in col], dtype=int)
            if not len(js):
                continue
            sig = day_signals(O[:, js], C[:, js], b)
            n = len(sig)
            rows = []
            for jj, side, m, sg in sig:
                j = int(js[jj])
                w_ = m / max(n, K)
                entry = O[b + H, j] if b + H < len(times) and np.isfinite(O[b + H, j]) else C[b + H - 1, j]
                rows.append((syms[j], j, side, w_, float(entry), sg))
            if d == sday:
                eq_open = eq
                today_sig = [{"symbol": s, "side": int(side), "w": round(w_, 5), "price": e, "sigma": round(sg, 5)}
                             for s, _, side, w_, e, sg in rows]
            ex_t = d + X * HOUR
            if now < ex_t + AFTER_BAR_MS:
                break                                                      # the day's exit is not known yet
            pnl = 0.0
            for s, j, side, w_, entry, _ in rows:
                px = O[b + X, j] if b + X < len(times) and np.isfinite(O[b + X, j]) else np.nan
                if not np.isfinite(px):
                    seg = C[b + H:b + X, j]
                    fin = seg[np.isfinite(seg)]
                    px = fin[-1] if len(fin) else entry
                f = sum(r for t, r in fund.get(s, []) if d + H * HOUR <= t < ex_t)
                ret = side * (px / entry - 1) - side * f - 2 * (FEE + SLIP)
                p = eq * w_ * ret
                pnl += p
                trades.append({"day": ymd(d), "symbol": s, "side": int(side), "w": round(w_, 4), "entry": entry,
                               "exit": float(px), "ret_pct": round(ret * 100, 3), "pnl": round(p, 2)})
            eq += pnl
            curve.append([ymd(d), round(eq, 2)])
            months.setdefault(ymd(d)[:7], []).append(eq)
        mrows, prev = [], 10_000.0
        for k_, v in months.items():
            mrows.append([k_, round((v[-1] / prev - 1) * 100, 2)])
            prev = v[-1]
        if kind == "open":
            ev_eq = eq_open
            targets = {r["symbol"]: {"units": r["side"] * ev_eq * r["w"] / r["price"], "price": r["price"],
                                     "w": r["side"] * r["w"]} for r in today_sig}
        else:
            ev_eq, targets = eq, {}
        event = {"kind": kind, "day": ets, "signal_day": ymd(sday),
                 "date": f"{ymd(sday)} {'16:00' if kind == 'open' else '02:00 (close)'} UTC", "equity": ev_eq,
                 "targets": targets, "trades": {}, "version": VERSION}
        first = self.store.get("first_day") or sday
        return {"event": event, "curve": curve, "months": mrows, "trades": trades[-200:], "n_trades": len(trades),
                "signals_today": today_sig if kind == "open" else [], "net_pct": round(eq / 100 - 100, 2),
                "first_forward_day": ymd(first), "universe": unis.get(sday, []), "computed_ts": self.now(),
                "version": VERSION}

    # -- views -------------------------------------------------------------------------------------------------------------------
    def latest(self) -> dict[str, Any] | None:
        b = self.store.get("book")
        return b["event"] if b else None

    def public(self) -> dict[str, Any]:
        b = self.store.get("book") or {}
        ev = b.get("event") or {}
        return {"status": self.status, "book": {k: v for k, v in b.items() if k not in ("event", "universe")},
                "event": {k: ev.get(k) for k in ("kind", "date", "signal_day")} if ev else None,
                "positions": sorted(([s.replace("USDT", ""), round(x["w"] * 100, 2)] for s, x in
                                     (ev.get("targets") or {}).items()), key=lambda r: -abs(r[1])),
                "universe_size": len(b.get("universe") or []),
                "rules": "Every day: among the 40 most-traded USDT perps (not BTC/ETH), alts that moved at least 2 of "
                         "their own volatility units between 00:00 and 16:00 UTC are bought (up) or shorted (down) at "
                         "16:00 and closed at 02:00 UTC; at most 1/5 of the money each, less for wild coins; 1x; all "
                         "fees, slippage and funding. Frozen: research/worker2/session/PREREG.md (rule P)."}
