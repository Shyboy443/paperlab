"""Public Binance USD-M market data for the Ensemble (no keys): daily bars of every USDT perpetual, 4h bars of the ML
universe, funding rates. Kept in the Ensemble's own SQLite file; only completed bars are stored, except the current
day's bar, whose OPEN the book trades at (it is overwritten once the day completes).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np

DAY = 86_400_000
H4 = 4 * 3_600_000
from app.config import AUTORESEARCH_PERP_REST as BASE  # noqa: E402  (live USD-M public REST; config.py owns hosts)
# research/monthly20/r2_trend/data_r2.py: stable / index / non-coin contracts are not traded by the daily legs
EXCL_DAILY = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "USDEUSDT", "USD1USDT", "XUSDUSDT",
              "RLUSDUSDT", "EURUSDT", "PAXGUSDT", "XAUTUSDT", "DEFIUSDT", "BTCDOMUSDT", "FOOTBALLUSDT",
              "BLUEBIRDUSDT", "ALLUSDT"}
# research/monthly20/r2_ml/run.py eligible_symbols(): what the ML universe may hold
EXCL_ML = {"BLUEBIRDUSDT", "FOOTBALLUSDT", "DEFIUSDT", "BTCDOMUSDT"}


class RateLimited(RuntimeError):
    pass


class Rest:
    """GET-only public futures market data, with a request-weight budget below Binance's 2,400 / minute."""

    ENDPOINTS = ("exchangeInfo", "klines", "fundingRate")

    def __init__(self, stop: threading.Event | None = None, opener: Callable = urllib.request.urlopen, budget: int = 900):
        self.stop = stop or threading.Event()
        self.opener, self.budget = opener, budget
        self.window, self.used = time.monotonic(), 0

    def get(self, endpoint: str, params: dict[str, Any] | None = None, weight: int = 2) -> Any:
        if endpoint not in self.ENDPOINTS:
            raise ValueError("only public market data endpoints are allowed")
        if time.monotonic() - self.window >= 60:
            self.window, self.used = time.monotonic(), 0
        if self.used + weight > self.budget:
            if self.stop.wait(max(0.0, 60 - (time.monotonic() - self.window))):
                raise InterruptedError()
            self.window, self.used = time.monotonic(), 0
        self.used += weight
        url = f"{BASE}/fapi/v1/{endpoint}" + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "PaperLab-Ensemble/1"})
        for attempt in range(3):
            try:
                with self.opener(req, timeout=20) as res:
                    if urllib.parse.urlsplit(res.geturl()).netloc != urllib.parse.urlsplit(BASE).netloc:
                        raise ValueError("unexpected data host redirect")
                    if float(res.headers.get("X-MBX-USED-WEIGHT-1M", "0") or 0) >= 1800:
                        self.used = self.budget
                    return json.load(res)
            except urllib.error.HTTPError as e:
                if e.code in (418, 429):
                    raise RateLimited(f"Binance HTTP {e.code}") from None
                if e.code < 500 or attempt == 2:
                    raise RuntimeError(f"Binance market data HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
            if self.stop.wait(2 * (attempt + 1)):
                raise InterruptedError()

    def perps(self) -> list[dict[str, Any]]:
        info = self.get("exchangeInfo", weight=1)
        return [s for s in info["symbols"] if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"]

    def klines(self, symbol: str, interval: str, start: int, end: int) -> list[list]:
        step = {"1d": DAY, "4h": H4, "1h": H4 // 4}[interval]
        out, cur = [], start
        while cur < end:
            need = min(1000, max(1, (end - cur) // step + 1))
            limit, weight = (99, 1) if need < 100 else ((499, 2) if need < 500 else (1000, 5))   # Binance's weight steps
            part = self.get("klines", {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end - 1,
                                       "limit": limit}, weight=weight)
            if not part:
                break
            out += part
            nxt = int(part[-1][0]) + step
            if nxt <= cur or len(part) < limit:
                break
            cur = nxt
        return out

    def funding(self, symbol: str, start: int, end: int) -> list[tuple[int, float]]:
        out, cur = [], start
        while cur < end:
            part = self.get("fundingRate", {"symbol": symbol, "startTime": cur, "endTime": end - 1, "limit": 1000}, weight=1)
            if not part:
                break
            out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in part]
            nxt = int(part[-1]["fundingTime"]) + 1
            if nxt <= cur or len(part) < 1000:
                break
            cur = nxt
        return out


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS d1(symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, qv REAL, done INTEGER,
                                        PRIMARY KEY(symbol, t));
          CREATE TABLE IF NOT EXISTS h4(symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, qv REAL, tbq REAL,
                                        PRIMARY KEY(symbol, t));
          CREATE TABLE IF NOT EXISTS funding(symbol TEXT, t INTEGER, rate REAL, PRIMARY KEY(symbol, t));
          CREATE TABLE IF NOT EXISTS preds(day INTEGER PRIMARY KEY, src TEXT, data TEXT);
          CREATE TABLE IF NOT EXISTS marks(key TEXT PRIMARY KEY, t INTEGER);
        """)
        self.db.commit()

    # -- key/value --
    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value, allow_nan=False)))

    def mark(self, key: str) -> int | None:
        with self.lock:
            row = self.db.execute("SELECT t FROM marks WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else None

    def set_mark(self, key: str, t: int) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO marks VALUES(?,?)", (key, int(t)))

    # -- bars --
    def save_d1(self, symbol: str, rows: list[list], now: int) -> None:
        recs = []
        for r in rows:
            t = int(r[0])
            done = int(t + DAY <= now)
            o = float(r[1])
            recs.append((symbol, t, o, float(r[2]) if done else o, float(r[3]) if done else o, float(r[4]) if done else o,
                         float(r[7]) if done else 0.0, done))
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO d1 VALUES(?,?,?,?,?,?,?,?)", recs)

    def save_h4(self, symbol: str, rows: list[list], now: int) -> None:
        recs = [(symbol, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]), float(r[10]))
                for r in rows if int(r[0]) + H4 <= now]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO h4 VALUES(?,?,?,?,?,?,?,?)", recs)

    def save_funding(self, symbol: str, rows: list[tuple[int, float]]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO funding VALUES(?,?,?)", [(symbol, t, r) for t, r in rows])

    def last_d1(self, symbol: str, done_only: bool = True) -> int | None:
        q = "SELECT MAX(t) FROM d1 WHERE symbol=?" + (" AND done=1" if done_only else "")
        with self.lock:
            row = self.db.execute(q, (symbol,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def last_h4(self, symbol: str) -> int | None:
        with self.lock:
            row = self.db.execute("SELECT MAX(t) FROM h4 WHERE symbol=?", (symbol,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def d1_symbols(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT symbol FROM d1 ORDER BY symbol")]

    def d1_panel(self, start: int, end: int, symbols: list[str] | None = None):
        """Day x coin arrays over [start, end): O H L C QV (NaN = no bar) and FUND (sum of the day's funding rates)."""
        days = np.arange(start, end, DAY, dtype=np.int64)
        with self.lock:
            rows = self.db.execute("SELECT symbol,t,o,h,l,c,qv FROM d1 WHERE t>=? AND t<?", (start, end)).fetchall()
            frows = self.db.execute("SELECT symbol,t,rate FROM funding WHERE t>=? AND t<?", (start, end)).fetchall()
        syms = symbols or sorted({r[0] for r in rows})
        col = {s: j for j, s in enumerate(syms)}
        T, N = len(days), len(syms)
        A = {k: np.full((T, N), np.nan) for k in ("O", "H", "L", "C", "QV")}
        F = np.zeros((T, N))
        for s, t, o, h, l, c, qv in rows:
            j = col.get(s)
            if j is None:
                continue
            i = (t - start) // DAY
            A["O"][i, j], A["H"][i, j], A["L"][i, j], A["C"][i, j], A["QV"][i, j] = o, h, l, c, qv
        for s, t, r in frows:
            j = col.get(s)
            if j is not None:
                F[(t - start) // DAY, j] += r
        return syms, days, A, F

    def h4_panel(self, syms: list[str], start: int, end: int):
        times = np.arange(start, end, H4, dtype=np.int64)
        col = {s: j for j, s in enumerate(syms)}
        T, N = len(times), len(syms)
        A = {k: np.full((T, N), np.nan) for k in ("o", "h", "l", "c", "qv", "tbq")}
        F = np.zeros((T, N))
        qs = ",".join("?" * len(syms))
        with self.lock:
            rows = self.db.execute(f"SELECT symbol,t,o,h,l,c,qv,tbq FROM h4 WHERE t>=? AND t<? AND symbol IN ({qs})",
                                   (start, end, *syms)).fetchall()
            frows = self.db.execute(f"SELECT symbol,t,rate FROM funding WHERE t>=? AND t<? AND symbol IN ({qs})",
                                    (start, end, *syms)).fetchall()
        for s, t, *v in rows:
            if v[5] <= 0:                                                    # zero-volume bars = missing (research)
                continue
            i, j = (t - start) // H4, col[s]
            for k, x in zip(("o", "h", "l", "c", "qv", "tbq"), v):
                A[k][i, j] = x
        for s, t, r in frows:
            F[(t - start) // H4, col[s]] += r
        return times, A, F

    def save_preds(self, day: int, src: str, data: dict[str, float]) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO preds VALUES(?,?,?)", (day, src, json.dumps(data)))

    def preds(self) -> dict[int, dict[str, float]]:
        with self.lock:
            return {int(d): json.loads(x) for d, _, x in self.db.execute("SELECT day,src,data FROM preds")}

    def pred_src(self) -> dict[int, str]:
        with self.lock:
            return {int(d): s for d, s in self.db.execute("SELECT day,src FROM preds")}
