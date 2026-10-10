"""Renata V12-VOL: a prospective, PAPER-ONLY forward test of one research rule (docs/V12_VOL_FORWARD.md).

The rule is "E vol-expansion follow H24h +C regime" from the V12 research (docs/V12_RESEARCH.md): after a closed 1h
bar whose range is >= 2.5 x ATR(1h) on >= 3x its 7-day mean hourly volume, trade in the direction of the close location
(top quarter = long, bottom quarter = short), longs only while BTC is up and shorts only while BTC is down, hold 24h
behind the guard stop 1.5 x ATR(1h) x sqrt(24) (clamped 1-25%). Book "VOL" runs it; book "VOL-RAW" runs the same rule
without the regime filter as an unjudged comparison.

Nothing is re-implemented: the signal is app.lab12.families.vol_expansion on a Panel built by families.panel from
Bybit's closed 5m klines (the research path), and fills, stops, costs and funding follow engine.trade exactly --
entry at the open of the minute after the decision, taker 5.5 bp + 2/4 bp slippage per side, stop first on 5m bars
(gap opens fill at the open), time exit at the close of the 24th hour, real Bybit funding, at most 6 positions and one
per coin per book, notional = book equity / 6. Every input is closed exchange history, so a decision missed during a
deploy is processed late with the same result (flagged catch_up; only the live spread snapshot is lost).

Frozen: the trading code's fingerprint must match docs/V12_VOL_FREEZE.json or the books stay shut (FROZEN_MISMATCH).
No live orders exist anywhere in this module.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.lab12 import engine as E
from app.lab12 import families as F

log = logging.getLogger("paperlab.v12vol")

PROTOCOL = "RENATA_V12_VOL_FORWARD_V1"
DOCS = Path(__file__).resolve().parents[2] / "docs"
FREEZE_FILE = DOCS / "V12_VOL_FREEZE.json"


def _ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


START_MS = _ms("2026-10-11T00:00:00")
END_MS = _ms("2027-04-11T00:00:00")
HALF_MS = _ms("2027-01-10T00:00:00")
BOOKS = {"VOL": "E vol-expansion follow H24h +C regime", "VOL-RAW": "E vol-expansion follow H24h"}
PRIMARY = "VOL"
STARTING_BALANCE = 1000.0
MAX_POS = 6
WARM_DAYS = 40
FILL_DELAY_MS = 2 * E.MIN + 5_000          # the entry minute (D+1m .. D+2m) must have closed
COINS: tuple[str, ...] = tuple(sorted(json.loads((DOCS / "V11_FREEZE.json").read_text(encoding="utf-8"))["rules"]))
# Pre-registered pass rules (docs/V12_VOL_FORWARD.md), judged on the PRIMARY book only, once, at END_MS.
CRITERIA = {"expectancy_r_gt": 0.0, "t_daily_ge": 2.0, "pf_ge": 1.10, "trades_ge": 150, "expectancy_r_2x_costs_gt": 0.0,
            "both_halves_positive": True}


def book_configs() -> dict[str, dict[str, Any]]:
    by_name = {c["name"]: c for c in F.configs()}
    return {b: by_name[n] for b, n in BOOKS.items()}


def code_fingerprint() -> str:
    h = hashlib.sha256()
    for p in ("app/lab12/engine.py", "app/lab12/families.py", "app/lab12/forward.py"):
        h.update((DOCS.parent / p).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()[:16]


def freeze_manifest() -> dict[str, Any]:
    return {"protocol": PROTOCOL, "code": code_fingerprint(), "books": BOOKS, "primary": PRIMARY,
            "configs": book_configs(), "coins": list(COINS), "start": START_MS, "end": END_MS, "half": HALF_MS,
            "starting_balance": STARTING_BALANCE, "max_pos": MAX_POS, "criteria": CRITERIA,
            "costs": {"taker": E.TAKER, "slip_majors": 2e-4, "slip_others": 4e-4, "majors": sorted(E.MAJORS)}}


def write_freeze() -> dict[str, Any]:
    man = freeze_manifest()
    FREEZE_FILE.write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def freeze_problem() -> str | None:
    if not FREEZE_FILE.exists():
        return "no freeze file"
    want = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    have = freeze_manifest()
    bad = [k for k in have if want.get(k) != have[k]]
    return ("frozen fields differ: " + ", ".join(bad)) if bad else None


# -- market data --------------------------------------------------------------------------------------------------------
class Bars:
    """Closed Bybit 5m bars (open, high, low, close, turnover) per symbol, plus funding settlements. Thread-safe."""

    def __init__(self, klines: Callable[..., list], funding: Callable[..., list]):
        self._klines, self._funding = klines, funding
        self._bars: dict[str, dict[int, tuple[float, ...]]] = {}
        self._lock = threading.Lock()

    def refresh(self, sym: str, now: int) -> None:
        end = now // E.BAR * E.BAR                                   # bars that OPENED before this one are closed
        with self._lock:
            book = self._bars.get(sym, {})
            last = max(book) if book else None
        start = (last - E.BAR) if last is not None else (end - WARM_DAYS * E.DAY - E.DAY) // E.DAY * E.DAY
        rows = self._klines(sym, "5", start, end)
        with self._lock:
            book = self._bars.setdefault(sym, {})
            for r in rows:
                t = int(r[0])
                if t + E.BAR <= now:
                    book[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]))
            cut = end - (WARM_DAYS + 3) * E.DAY
            for t in [t for t in book if t < cut]:
                del book[t]

    def has(self, sym: str, t: int) -> bool:
        with self._lock:
            return t in self._bars.get(sym, {})

    def bar(self, sym: str, t: int) -> tuple[float, ...] | None:
        with self._lock:
            return self._bars.get(sym, {}).get(t)

    def last_close(self, sym: str) -> float | None:
        with self._lock:
            b = self._bars.get(sym)
            return b[max(b)][3] if b else None

    def array(self, sym: str, a: int, nb: int) -> np.ndarray:
        out = np.full((nb, 5), np.nan, np.float32)
        with self._lock:
            book = self._bars.get(sym, {})
            for k in range(nb):
                v = book.get(a + k * E.BAR)
                if v is not None:
                    out[k] = v
        return out

    def funding(self, sym: str, a: int, b: int) -> list[tuple[int, float]]:
        return [(int(t), float(r)) for t, r in self._funding(sym, a, b)]


def decide(bars: Bars, D: int, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The rule's signals at decision instant D (an hour boundary), best score first, exactly as the research makes
    them: families.panel over the closed 5m bars of the last WARM_DAYS days, then families.vol_expansion on the hour
    that just closed. One padding hour of NaN bars makes panel's regime index i+1 exist (it is the regime computed from
    hours <= i, the same value the research reads)."""
    a = (D - WARM_DAYS * E.DAY) // E.DAY * E.DAY
    nb = (D - a) // E.BAR
    w = E.Window("LIVE", start=D - E.HOUR, a=a, end=D)
    pad = np.full((12, 5), np.nan, np.float32)
    for s in COINS:
        w.coins[s] = E.Coin(s, np.zeros(0, np.float32), np.concatenate([bars.array(s, a, nb), pad]), np.zeros((0, 2)))
    p = F.panel(w)
    i = nb // 12 - 1
    p = dataclasses.replace(p, first=i, last=i + 1)
    ev = F.vol_expansion(w, p, cfg)
    out = []
    for k in np.lexsort((-ev.score, ev.t)):
        c = int(ev.coin[k])
        out.append({"sym": p.syms[c], "side": int(ev.side[k]), "stop": float(ev.stop[k]), "hold": int(ev.hold[k]),
                    "score": float(ev.score[k]), "regime": int(p.regime[i + 1]), "atr": float(p.atr_h[c, i]),
                    "vol_rel": float(p.vol_rel[c, i])})
    return out


def step_position(pos: dict[str, Any], bars: Bars, upto: int) -> dict[str, Any] | None:
    """Advance one open position over the closed 5m bars that started before `upto`, exactly as engine.trade does.
    Returns the exit {exit_ms, exit_px, stopped} when the trade ends, else None (pos["checked"] advances)."""
    D, side, entry, stop, hold = pos["D"], pos["side"], pos["entry"], pos["stop"], pos["hold"]
    level = entry * (1 - side * stop)
    j = pos["checked"]
    while j < hold and D + (j + 1) * E.BAR <= upto:
        b = bars.bar(pos["sym"], D + j * E.BAR)
        if b is None:
            break                                                  # wait for the bar; never skip one
        o, h, lo, c = b[0], b[1], b[2], b[3]
        if (lo <= level) if side > 0 else (h >= level):
            px = level if j == 0 else (min(level, o) if side > 0 else max(level, o))
            pos["checked"] = j + 1
            return {"exit_ms": D + (j + 1) * E.BAR, "exit_px": px, "stopped": True}
        j += 1
        pos["checked"] = j
        if j == hold:
            return {"exit_ms": D + hold * E.BAR, "exit_px": c, "stopped": False}
    return None


def settle(pos: dict[str, Any], ex: dict[str, Any], funding: list[tuple[int, float]]) -> dict[str, Any]:
    side, entry = pos["side"], pos["entry"]
    gross = side * (ex["exit_px"] / entry - 1.0)
    cost = 2 * (E.TAKER + E.slip(pos["sym"]))
    t_ms = pos["D"] + E.MIN
    fund = -side * sum(r for t, r in funding if t_ms < t <= ex["exit_ms"])
    ret = gross - cost + fund
    return {**ex, "gross": gross, "cost": cost, "funding": fund, "ret": ret, "ret_2x": gross - 2 * cost + fund,
            "r": ret / pos["stop"], "pnl": pos["notional"] * ret}


# -- the service --------------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS decisions (D INTEGER PRIMARY KEY, done_ms INTEGER, catch_up INTEGER, signals TEXT);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, sym TEXT, side INTEGER, D INTEGER, entry REAL, stop REAL,
  hold INTEGER, notional REAL, score REAL, regime INTEGER, atr REAL, vol_rel REAL, bid REAL, ask REAL, catch_up INTEGER,
  status TEXT, checked INTEGER, exit_ms INTEGER, exit_px REAL, stopped INTEGER, gross REAL, cost REAL, funding REAL,
  ret REAL, ret_2x REAL, r REAL, pnl REAL);
CREATE INDEX IF NOT EXISTS pos_book ON positions(book, status);
"""
POS_COLS = ("id", "book", "sym", "side", "D", "entry", "stop", "hold", "notional", "score", "regime", "atr", "vol_rel",
            "bid", "ask", "catch_up", "status", "checked", "exit_ms", "exit_px", "stopped", "gross", "cost", "funding",
            "ret", "ret_2x", "r", "pnl")


class V12VolForward:
    def __init__(self, db_path: str | Path, klines: Callable[..., list] | None = None,
                 funding: Callable[..., list] | None = None, tickers: Callable[[], dict] | None = None,
                 now: Callable[[], int] | None = None):
        from app.backtest import bybit_archive as bb
        self.db_path = str(db_path)
        self.bars = Bars(klines or bb.klines, funding or bb.funding)
        self._tickers = tickers or (lambda: {r["symbol"]: r for r in bb.get(f"{bb.BYBIT}/tickers?category=linear")["list"]})
        self._now = now or (lambda: int(time.time() * 1000))
        self.cfgs = book_configs()
        self._snap: dict[int, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.status, self.error, self.last_refresh = "STARTING", "", 0
        self.frozen = freeze_problem()
        with self._db() as c:
            c.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # lifecycle
    def start(self) -> None:
        if self.frozen:
            self.status = "FROZEN_MISMATCH"
            log.error("Renata V12-VOL not started: %s", self.frozen)
            return
        self._thread = threading.Thread(target=self._run, name="v12vol", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
                self.error = ""
            except Exception as exc:                               # network etc.: retry next loop
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("v12vol step failed: %s", self.error)
            self._stop.wait(15)

    # one loop
    def _positions(self, c: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute(f"SELECT * FROM positions {where} ORDER BY id", args)]

    def _next_decision(self, c: sqlite3.Connection) -> int:
        row = c.execute("SELECT MAX(D) FROM decisions").fetchone()
        return START_MS if row[0] is None else int(row[0]) + E.HOUR

    def step(self) -> None:
        now = self._now()
        if now < START_MS - 2 * E.HOUR:
            self.status = "WAITING_FOR_START"
            return
        with self._lock, self._db() as c:
            due = self._next_decision(c)
            if now - self.last_refresh > 4 * E.MIN or (due < END_MS and now >= due + FILL_DELAY_MS):
                for s in COINS:
                    self.bars.refresh(s, now)
                self.last_refresh = now
            if due < END_MS and due + E.MIN - 5_000 <= now < due + FILL_DELAY_MS and due not in self._snap:
                try:
                    self._snap[due] = self._tickers()                  # live spread at the entry minute (diagnostic)
                except Exception:
                    self._snap[due] = {}
            # decisions strictly in order: exits up to D first, then the entries at D
            while due < END_MS and now >= due + FILL_DELAY_MS:
                if not self.bars.has("BTCUSDT", due - E.BAR) and now < due + 30 * E.MIN:
                    break                                              # BTC's last bar not published yet: wait
                self._manage(c, due)
                self._decide(c, due, catch_up=now > due + 15 * E.MIN)
                due += E.HOUR
            self._manage(c, now // E.BAR * E.BAR)
            self.status = "RUNNING" if now < END_MS else "FINISHED"
            for d in [d for d in self._snap if d < due - E.HOUR]:
                del self._snap[d]

    def _manage(self, c: sqlite3.Connection, upto: int) -> None:
        for pos in self._positions(c, "WHERE status='open'"):
            ex = step_position(pos, self.bars, upto)
            if ex is None:
                c.execute("UPDATE positions SET checked=? WHERE id=?", (pos["checked"], pos["id"]))
                continue
            res = settle(pos, ex, self.bars.funding(pos["sym"], pos["D"], ex["exit_ms"] + 1))
            c.execute("UPDATE positions SET status='closed', checked=?, exit_ms=?, exit_px=?, stopped=?, gross=?, cost=?,"
                      " funding=?, ret=?, ret_2x=?, r=?, pnl=? WHERE id=?",
                      (pos["checked"], res["exit_ms"], res["exit_px"], int(res["stopped"]), res["gross"], res["cost"],
                       res["funding"], res["ret"], res["ret_2x"], res["r"], res["pnl"], pos["id"]))
            log.info("v12vol %s %s %s closed %+.3fR", pos["book"], pos["sym"], "long" if pos["side"] > 0 else "short", res["r"])

    def _decide(self, c: sqlite3.Connection, D: int, catch_up: bool) -> None:
        snap = self._snap.get(D, {}) if not catch_up else {}
        record = {}
        for book, cfg in self.cfgs.items():
            sig = decide(self.bars, D, cfg)
            record[book] = sig
            # occupancy at D exactly as engine.simulate: a position occupies its slot until its exit bar
            live = [p for p in self._positions(c, "WHERE book=? AND D<?", (book, D))
                    if p["status"] == "open" or (p["exit_ms"] or 0) > D]
            busy = {p["sym"] for p in live}
            equity = STARTING_BALANCE + sum(p["pnl"] or 0.0 for p in self._positions(c, "WHERE book=? AND status='closed'"
                                                                                    " AND exit_ms<=?", (book, D)))
            for s in sig:
                if len(live) >= MAX_POS:
                    break
                if s["sym"] in busy:
                    continue
                k = self.bars._klines(s["sym"], "1", D + E.MIN, D + 2 * E.MIN)
                if not k or int(k[0][0]) != D + E.MIN:
                    continue                                           # no entry minute: no trade (engine.trade)
                entry = float(k[0][1])
                t = snap.get(s["sym"], {})
                bid, ask = (float(t.get("bid1Price") or 0) or None), (float(t.get("ask1Price") or 0) or None)
                c.execute("INSERT INTO positions (book, sym, side, D, entry, stop, hold, notional, score, regime, atr,"
                          " vol_rel, bid, ask, catch_up, status, checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',0)",
                          (book, s["sym"], s["side"], D, entry, s["stop"], s["hold"], equity / MAX_POS, s["score"],
                           s["regime"], s["atr"], s["vol_rel"], bid, ask, int(catch_up)))
                busy.add(s["sym"])
                live.append({"sym": s["sym"]})
                log.info("v12vol %s %s %s entry %.6g stop %.2f%%", book, s["sym"], "long" if s["side"] > 0 else "short",
                         entry, 100 * s["stop"])
        c.execute("INSERT INTO decisions VALUES (?,?,?,?)", (D, self._now(), int(catch_up), json.dumps(record)))

    # read-only view -----------------------------------------------------------------------------------------------------
    def public_state(self) -> dict[str, Any]:
        with self._db() as c:
            pos = self._positions(c)
            last = c.execute("SELECT MAX(D), COUNT(*) FROM decisions").fetchone()
        now = self._now()
        books = {}
        for book in BOOKS:
            mine = [p for p in pos if p["book"] == book]
            closed = [p for p in mine if p["status"] == "closed"]
            opened = [p for p in mine if p["status"] == "open"]
            for p in opened:
                px = self.bars.last_close(p["sym"])
                p["mark_ret"] = p["side"] * (px / p["entry"] - 1) - 2 * (E.TAKER + E.slip(p["sym"])) if px else None
            equity = STARTING_BALANCE + sum(p["pnl"] for p in closed)
            books[book] = {"rule": BOOKS[book], "judged": book == PRIMARY, "equity": round(equity, 2),
                           "metrics": summarize(closed, min(now, END_MS)),
                           "open": [_view(p) for p in opened], "closed": [_view(p) for p in closed[::-1][:200]]}
        prim = books[PRIMARY]["metrics"]
        return {"program": "Renata V12-VOL", "protocol": PROTOCOL, "paper_only": True, "status": self.status,
                "frozen_problem": self.frozen, "error": self.error, "start": START_MS, "end": END_MS, "half": HALF_MS,
                "last_decision": last[0], "decisions": last[1], "coins": list(COINS), "criteria": CRITERIA,
                "verdict": verdict(prim) if now >= END_MS else "no verdict before " +
                dt.datetime.fromtimestamp(END_MS / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                "books": books, "fills": fill_quality([p for p in pos if p["book"] == PRIMARY])}


def _view(p: dict[str, Any]) -> dict[str, Any]:
    keep = ("sym", "side", "D", "entry", "stop", "notional", "regime", "catch_up", "exit_ms", "exit_px", "stopped", "gross",
            "cost", "funding", "ret", "ret_2x", "r", "pnl", "mark_ret", "score", "vol_rel")
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items() if k in keep}


def summarize(closed: list[dict[str, Any]], now: int) -> dict[str, Any]:
    if not closed:
        return {"trades": 0}
    r = np.array([p["r"] for p in closed])
    r2 = np.array([p["ret_2x"] / p["stop"] for p in closed])
    days = max(1, int((now - START_MS) // E.DAY))
    daily = np.zeros(days)
    for p in closed:
        d = int((p["exit_ms"] - START_MS) // E.DAY)
        if 0 <= d < days:
            daily[d] += p["r"]
    sd = daily.std(ddof=1) if days > 1 else 0.0
    eq = np.cumsum(r)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    halves = {}
    for lab, sel in (("half1", [p for p in closed if p["D"] < HALF_MS]), ("half2", [p for p in closed if p["D"] >= HALF_MS])):
        halves[lab] = {"trades": len(sel), "expectancy_r": round(float(np.mean([p["r"] for p in sel])), 4) if sel else None}
    reg = {}
    for k, nm in ((1, "BTC up"), (-1, "BTC down"), (0, "BTC range")):
        sel = [p["r"] for p in closed if p["regime"] == k]
        if sel:
            reg[nm] = {"trades": len(sel), "expectancy_r": round(float(np.mean(sel)), 4)}
    coins: dict[str, list] = {}
    for p in closed:
        coins.setdefault(p["sym"], []).append(p["r"])
    return {"trades": len(r), "win_rate": round(float((r > 0).mean()), 3), "expectancy_r": round(float(r.mean()), 4),
            "expectancy_r_2x_costs": round(float(r2.mean()), 4), "pf": round(float(wins / losses), 3) if losses else None,
            "t_daily": round(float(daily.mean() / (sd / math.sqrt(days))), 2) if sd > 0 else 0.0,
            "net_r": round(float(r.sum()), 2), "max_dd_r": round(float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)), 2),
            "pnl_usdt": round(float(sum(p["pnl"] for p in closed)), 2), "stopped_share": round(float(np.mean([p["stopped"] for p in closed])), 3),
            "funding_usdt": round(float(sum(p["notional"] * p["funding"] for p in closed)), 2),
            "fees_usdt": round(float(sum(p["notional"] * p["cost"] for p in closed)), 2),
            "days": days, **halves, "regimes": reg,
            "coins": {k: {"trades": len(v), "expectancy_r": round(float(np.mean(v)), 4)} for k, v in sorted(coins.items())}}


def verdict(m: dict[str, Any]) -> str:
    if not m.get("trades"):
        return "FAIL: no trades"
    checks = {"expectancy > 0": (m.get("expectancy_r") or 0) > CRITERIA["expectancy_r_gt"],
              "daily t >= 2": (m.get("t_daily") or 0) >= CRITERIA["t_daily_ge"],
              "PF >= 1.10": (m.get("pf") or 0) >= CRITERIA["pf_ge"],
              ">= 150 trades": m["trades"] >= CRITERIA["trades_ge"],
              "> 0 at 2x costs": (m.get("expectancy_r_2x_costs") or 0) > CRITERIA["expectancy_r_2x_costs_gt"],
              "both halves > 0": all((m.get(h) or {}).get("expectancy_r") is not None and m[h]["expectancy_r"] > 0
                                     for h in ("half1", "half2"))}
    failed = [k for k, ok in checks.items() if not ok]
    return "PASS (paper evidence only; live capital still needs operator approval)" if not failed else "FAIL: " + ", ".join(failed)


def fill_quality(pos: list[dict[str, Any]]) -> dict[str, Any]:
    """Modelled slippage vs the live book at the entry minute: half the quoted spread, and the entry price's distance
    from the quoted mid on the paying side (positive = worse than mid), both in bp."""
    rows = [p for p in pos if p["bid"] and p["ask"] and p["entry"]]
    if not rows:
        return {"samples": 0}
    half = [1e4 * (p["ask"] - p["bid"]) / (p["ask"] + p["bid"]) for p in rows]
    drift = [1e4 * p["side"] * (p["entry"] / ((p["ask"] + p["bid"]) / 2) - 1) for p in rows]
    model = [1e4 * E.slip(p["sym"]) for p in rows]
    return {"samples": len(rows), "half_spread_bp": round(float(np.mean(half)), 2),
            "entry_vs_mid_bp": round(float(np.mean(drift)), 2), "modelled_slippage_bp": round(float(np.mean(model)), 2)}
