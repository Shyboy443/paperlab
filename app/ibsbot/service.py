"""IBS bot: the frozen QQQ IBS rule (rule.py) on Alpaca, two books (QQQ, TQQQ), LIVE only when the operator arms it.

Every US session, whatever the switch says:
- two minutes before the close (Alpaca calendar, so early closes too) it estimates QQQ's IBS from the session so far
  and records the decision (the SHADOW books follow every decision on paper, so the operator can watch before arming);
- twenty minutes after the close it records the official candle, the official IBS and exit test, any "signal slip"
  (estimate and official disagree), and marks every book to market next to buy and hold.
The bot never acts on a closed day.

Live orders need ALL of: the operator's private authenticated POST with the typed phrase, Alpaca keys for the chosen
network saved in System -> Providers & Live (encrypted vault; never read here beyond the client), an ACTIVE USD account
with the capital in cash (no margin) and no QQQ/TQQQ position or open order, no other strategy on that Alpaca account,
and for real money (mainnet) LIVE_MIRROR_MAINNET_ENABLED=true and an authenticated dashboard. Capital per book and the
total loss limit are the operator's; the total is capped by IBS_BOT_MAX_CAPITAL_USD (default: LIVE_MIRROR_MAX_AMOUNT_USDT).

Execution: fractional day market orders two minutes before the close (Alpaca refuses market-on-close orders for
fractional shares), only for QQQ and TQQQ and only the quantity this bot bought. Every order is journalled with a
deterministic client order id BEFORE it is sent, so a restart never sends it twice. A position that differs from the
ledger HALTs the bot (nothing traded automatically). The loss limit (all books, marked to market every minute while the
market is open, and at each official close) closes everything at market and switches the bot OFF; so do the switch-off
and the kill switch (at the next open if the market is closed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import market
from .rule import BOOKS, IBS_ENTRY, Session, decide, freeze_hash, ibs

log = logging.getLogger(__name__)

CONFIRM = "GO LIVE IBS"
SYMBOLS = tuple(BOOKS.values())                 # QQQ, TQQQ: the only symbols this bot may ever trade
PRE_CLOSE = timedelta(seconds=120)              # decide and send orders two minutes before the close
LATE = timedelta(seconds=30)                    # no new order in the last 30 s (a market order must fill before the bell)
RECORD_AFTER = timedelta(minutes=20)            # the official SIP daily candle is complete (free plan: 15-min delay)
TICK_S, CHECK_S, CAL_S = 15, 60, 3600
MIN_BOOK, MIN_NOTIONAL = 10.0, 1.0              # USD; Alpaca's fractional minimum is 1 USD
SHADOW_CAPITAL = 10_000.0
LIVE_STATES = ("ARMED", "CLOSING", "HALTED")
FINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}
EPS = 1e-6


class Blocked(ValueError):
    pass


def qty_str(q: float) -> str:
    return f"{q:.9f}".rstrip("0").rstrip(".")


def new_books(capital: dict[str, float]) -> dict[str, dict[str, Any]]:
    return {b: {"capital": float(capital.get(b, 0.0)), "cash": float(capital.get(b, 0.0)), "qty": 0.0, "entry_px": None,
                "entry_day": None, "realized": 0.0} for b in BOOKS}


class IbsBot:
    NAME = "IBS bot (QQQ + TQQQ)"

    def __init__(self, path: Path | str, providers: Any, env: dict[str, str] | None = None, secure: bool = True,
                 telegram: Callable[[], Any] = lambda: None, other_user: Callable[[str], str | None] = lambda net: None,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.providers, self.secure, self.telegram, self.other_user, self.utcnow = providers, secure, telegram, other_user, utcnow
        self.env = os.environ if env is None else env
        self.feed = (self.env.get("IBS_BOT_FEED") or "iex").strip().lower()
        if self.feed not in ("iex", "sip"):
            raise ValueError("IBS_BOT_FEED must be iex or sip")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
          CREATE TABLE IF NOT EXISTS decisions(day TEXT PRIMARY KEY, ts TEXT, estimate TEXT, official TEXT, live TEXT);
          CREATE TABLE IF NOT EXISTS orders(cid TEXT PRIMARY KEY, day TEXT, book TEXT, symbol TEXT, side TEXT, body TEXT,
                                            status TEXT, filled_qty REAL, avg_px REAL, applied INTEGER DEFAULT 0, ts TEXT,
                                            reason TEXT);
          CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, kind TEXT, generation TEXT, book TEXT, symbol TEXT,
                                            entry_day TEXT, exit_day TEXT, entry_px REAL, exit_px REAL, qty REAL,
                                            ret_pct REAL, pnl REAL, reason TEXT);
          CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, qqq REAL, tqqq REAL, ibs REAL, shadow TEXT, live TEXT);
          CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY, ts TEXT, event TEXT, detail TEXT);
        """)
        self.cfg = self._get("config") or {"status": "OFF"}
        self.shadow = self._get("shadow") or {"books": new_books({b: SHADOW_CAPITAL for b in BOOKS}), "since": None}
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.closing = False
        self.sessions: dict[str, dict[str, datetime]] = {}
        self.next_cal = 0.0
        self.next_check = 0.0
        self.heartbeat: str | None = None
        self.error: str | None = None
        self.account: dict[str, Any] | None = None

    # -- persistence -----------------------------------------------------------------------------------------------------
    def _get(self, k: str) -> Any:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None

    def _set(self, k: str, v: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))

    def save(self) -> None:
        self._set("config", self.cfg)
        self._set("shadow", self.shadow)

    def note(self, event: str, detail: str = "", alert: bool = False) -> None:
        detail = self.providers.redact(detail)
        with self.db:
            self.db.execute("INSERT INTO journal(ts,event,detail) VALUES(?,?,?)", (self.utcnow().isoformat(timespec="seconds"), event, detail))
        if alert:
            t = self.telegram()
            if t is not None and hasattr(t, "live_alert"):
                try:
                    t.live_alert(f"IBS bot: {event}" + (f" · {detail}" if detail else ""))
                except Exception:
                    pass

    @property
    def status(self) -> str:
        return self.cfg.get("status", "OFF")

    def max_capital(self) -> float:
        try:
            v = float(self.env.get("IBS_BOT_MAX_CAPITAL_USD") or self.providers.max_amount())
            return v if math.isfinite(v) and v > 0 else 0.0
        except ValueError:
            return 0.0

    def uses_provider(self, network: str) -> bool:
        """True while this bot is on (or still holds) the Alpaca account of that network: its keys must not change."""
        return self.cfg.get("network") == network and (self.status in LIVE_STATES or self._holding() or bool(self._open_orders()))

    def _holding(self) -> bool:
        return any(b["qty"] > EPS for b in (self.cfg.get("books") or {}).values())

    def _open_orders(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM orders WHERE applied=0 AND status NOT IN ('skipped','lost') ORDER BY rowid")]

    # -- loop ------------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name="ibs-bot")

    async def close(self) -> None:
        self.closing = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.db.close()

    async def _loop(self) -> None:
        while not self.closing:
            try:
                async with self.lock:
                    await self.tick()
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = self.providers.redact(f"{type(exc).__name__}: {exc}")[:300]
                log.error("IBS bot tick: %s", self.error)
            self.heartbeat = self.utcnow().isoformat(timespec="seconds")
            await asyncio.sleep(TICK_S)

    def data_network(self) -> str | None:
        """Market data comes from whichever Alpaca key set exists (the armed account's first)."""
        for net in (self.cfg.get("network"), "mainnet", "testnet"):
            if net and self.providers.keys("alpaca", net):
                return net
        return None

    async def tick(self) -> None:
        now = self.utcnow()
        net = self.data_network()
        if net is None:
            self.error = "no Alpaca keys on the server: save Alpaca keys in System -> Providers & Live"
            return
        problems = []
        c = self.providers.client("alpaca", net)
        try:
            if not self.sessions or time.time() >= self.next_cal:
                self.sessions = await market.calendar(c, now.astimezone(market.ET).date())
                self.next_cal = time.time() + CAL_S
            today = now.astimezone(market.ET).date().isoformat()
            s = self.sessions.get(today)
            if s and s["close"] - PRE_CLOSE <= now < s["close"] - LATE and not self._decided(today):
                await self.pre_close(c, today, now)
            days = sorted(self.sessions)
            for day in days[1:]:
                if day <= today and self.sessions[day]["close"] + RECORD_AFTER <= now and self._needs_record(day):
                    try:
                        await self.record(c, day, now)
                    except Exception as exc:                # a late candle is retried next tick; it never blocks live
                        problems.append(f"record {day}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
        finally:
            await c.close()
        if self.cfg.get("account_id") and (self.status in LIVE_STATES or self._open_orders()):
            await self.manage(now)
        if problems:
            raise market.DataError("; ".join(problems))

    # -- the decision, two minutes before the close ----------------------------------------------------------------------
    def _decided(self, day: str) -> bool:
        return self.db.execute("SELECT 1 FROM decisions WHERE day=?", (day,)).fetchone() is not None

    def _needs_record(self, day: str) -> bool:
        since = self.shadow.get("since")
        if since is None or day < since:
            return False
        r = self.db.execute("SELECT official FROM decisions WHERE day=?", (day,)).fetchone()
        return r is None or r[0] is None

    async def pre_close(self, c: Any, day: str, now: datetime) -> None:
        try:
            sess, extra = await market.estimate(c, self.sessions, day, now, self.feed)
        except Exception as exc:
            msg = self.providers.redact(f"{type(exc).__name__}: {exc}")[:200]
            with self.db:
                self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (day, now.isoformat(timespec="seconds"),
                                json.dumps({"error": msg}), None, json.dumps({"status": self.status, "orders": []})))
            self.note("no_decision", f"{day}: market data unavailable two minutes before the close ({msg}); no orders", alert=self.status == "ARMED")
            if self.shadow.get("since") is None:
                self.shadow["since"] = day
            self.save()
            return
        prices = {"QQQ": sess.last, "TQQQ": extra["tqqq_last"]}
        shadow_actions = {}
        for book, sym in BOOKS.items():
            b = self.shadow["books"][book]
            act, info = decide(b["qty"] > EPS, sess)
            shadow_actions[book] = act
            if act in ("enter", "exit"):
                self._fill(b, "buy" if act == "enter" else "sell", b["cash"] / prices[sym] if act == "enter" else b["qty"],
                           prices[sym], day, "shadow", None, book, sym, "IBS < 0.10" if act == "enter" else "close > previous high")
        est = {**info, **extra, "time": now.isoformat(timespec="seconds"), "shadow": shadow_actions, "freeze_hash": freeze_hash()}
        live = {"status": self.status, "orders": []}
        if self.status == "ARMED":
            for book, sym in BOOKS.items():
                b = self.cfg["books"][book]
                if b["capital"] <= 0:
                    continue
                act, _ = decide(b["qty"] > EPS, sess)
                if act == "enter":
                    live["orders"].append(self._plan(day, book, sym, "buy", notional=math.floor(b["cash"] * 100) / 100))
                elif act == "exit":
                    live["orders"].append(self._plan(day, book, sym, "sell", qty=b["qty"]))
        with self.db:
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (day, now.isoformat(timespec="seconds"), json.dumps(est), None, json.dumps(live)))
        if self.shadow.get("since") is None:
            self.shadow["since"] = day
        self.save()
        self.note("decision", f"{day}: estimated IBS {info['ibs'] if info['ibs'] is None else round(info['ibs'], 3)}, last {sess.last:.2f}, "
                              f"previous high {sess.prev_high:.2f}: shadow {shadow_actions['IBS-QQQ']}"
                              + (f"; live orders {', '.join(live['orders'])}" if live["orders"] else ""),
                  alert=bool(live["orders"]))

    def _plan(self, day: str, book: str, sym: str, side: str, notional: float | None = None, qty: float | None = None,
              reason: str | None = None) -> str:
        cid = f"ibs-{self.cfg['generation']}-{day}-{book}-{side}"
        body = {"symbol": sym, "side": side, "type": "market", "time_in_force": "day", "client_order_id": cid}
        if side == "buy":
            body["notional"] = f"{notional:.2f}"
        else:
            body["qty"] = qty_str(qty)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO orders(cid,day,book,symbol,side,body,status,ts,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                            (cid, day, book, sym, side, json.dumps(body), "planned", self.utcnow().isoformat(timespec="seconds"), reason))
        return cid

    # -- ledger ----------------------------------------------------------------------------------------------------------
    def _fill(self, b: dict[str, Any], side: str, qty: float, px: float, day: str, kind: str, generation: str | None,
              book: str, sym: str, reason: str) -> None:
        if qty <= EPS:
            return
        if side == "buy":
            cost = qty * px
            if b["qty"] > EPS:                                           # (never happens: one entry per position)
                b["entry_px"] = (b["entry_px"] * b["qty"] + cost) / (b["qty"] + qty)
            else:
                b["entry_px"], b["entry_day"] = px, day
            b["qty"] += qty
            b["cash"] -= cost
            return
        qty = min(qty, b["qty"])
        pnl = (px - b["entry_px"]) * qty
        b["cash"] += qty * px
        b["qty"] -= qty
        b["realized"] += pnl
        with self.db:
            self.db.execute("INSERT INTO trades(kind,generation,book,symbol,entry_day,exit_day,entry_px,exit_px,qty,ret_pct,pnl,reason)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (kind, generation, book, sym, b["entry_day"], day, b["entry_px"], px, qty, (px / b["entry_px"] - 1) * 100, pnl, reason))
        if b["qty"] <= EPS:
            b["qty"], b["entry_px"], b["entry_day"] = 0.0, None, None

    @staticmethod
    def equity(books: dict[str, dict[str, Any]], prices: dict[str, float]) -> dict[str, float]:
        return {k: b["cash"] + b["qty"] * prices.get(BOOKS[k], b["entry_px"] or 0.0) for k, b in books.items()}

    # -- the official record, twenty minutes after the close -------------------------------------------------------------
    async def record(self, c: Any, day: str, now: datetime) -> None:
        d = await market.daily(c, list(SYMBOLS), self.sessions, day, now)
        prev = max(x for x in self.sessions if x < day)
        if day not in d["QQQ"] or day not in d["TQQQ"] or prev not in d["QQQ"]:
            raise market.DataError(f"official daily candles for {day} are not available yet")
        q, t = d["QQQ"][day], d["TQQQ"][day]
        off_ibs = ibs(q["c"], q["h"], q["l"])
        exit_test = q["c"] > d["QQQ"][prev]["h"]
        row = self.db.execute("SELECT estimate FROM decisions WHERE day=?", (day,)).fetchone()
        est = json.loads(row[0]) if row else None
        official = {"ibs": off_ibs, "high": q["h"], "low": q["l"], "close": q["c"], "prev_high": d["QQQ"][prev]["h"],
                    "entry_signal": off_ibs is not None and off_ibs < IBS_ENTRY, "exit_signal": exit_test, "tqqq_close": t["c"]}
        if est and est.get("ibs") is not None:
            est_entry = est["ibs"] < IBS_ENTRY
            est_exit = est["last"] > est["prev_high"]
            official["slip"] = est_entry != official["entry_signal"] or est_exit != official["exit_signal"]
        else:
            official["slip"] = None
            official["missed"] = True                           # no estimate that day (server down or data missing)
        prices = {"QQQ": q["c"], "TQQQ": t["c"]}
        shadow_eq = self.equity(self.shadow["books"], prices)
        live_eq = self.equity(self.cfg["books"], prices) if self.cfg.get("books") and (self.status in LIVE_STATES or self._holding()) else None
        with self.db:
            if row:
                self.db.execute("UPDATE decisions SET official=? WHERE day=?", (json.dumps(official), day))
            else:
                self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (day, now.isoformat(timespec="seconds"), None,
                                                                             json.dumps(official), json.dumps({"status": self.status, "orders": []})))
            self.db.execute("INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?)", (day, q["c"], t["c"], off_ibs, json.dumps(shadow_eq),
                                                                                 json.dumps(live_eq) if live_eq else None))
        if official["slip"]:
            self.note("signal_slip", f"{day}: the estimate two minutes before the close disagrees with the official candle "
                                     f"(estimated IBS {est['ibs']:.3f}, official {off_ibs if off_ibs is None else round(off_ibs, 3)})")
        if live_eq and self.status == "ARMED":
            self._loss_check(sum(live_eq.values()), "official close")

    # -- live: orders, reconciliation, loss limit ------------------------------------------------------------------------
    def client(self) -> Any:
        return self.providers.client("alpaca", self.cfg["network"])

    async def manage(self, now: datetime) -> None:
        c = self.client()
        try:
            await self._sync_orders(c)
            if self._open_orders() and any(o["status"] == "planned" for o in self._open_orders()):
                await self._submit_planned(c, now)
            if self.status == "HALTED" or time.time() < self.next_check:
                return
            self.next_check = time.time() + CHECK_S
            a = await self._account(c)
            self.account = {k: a.get(k) for k in ("equity", "cash", "buying_power", "status")}
            if not self._open_orders() and not await self._reconcile(c):
                return
            clock = await c.request("GET", "/v2/clock")
            if not clock.get("is_open"):
                return
            if self.status == "ARMED" and self._holding():
                px = await market.latest(c, [BOOKS[k] for k, b in self.cfg["books"].items() if b["qty"] > EPS], self.feed,
                                         market.stamp(clock["timestamp"]))
                self._loss_check(sum(self.equity(self.cfg["books"], px).values()), "market")
            if self.status == "CLOSING":
                await self._flatten(c, market.stamp(clock["timestamp"]))
        finally:
            await c.close()

    async def _account(self, c: Any) -> dict[str, Any]:
        a = await c.fetch_account()
        if a.get("id") != self.cfg.get("account_id"):
            self._halt("the Alpaca account behind the saved keys changed")
            raise Blocked("account identity changed")
        return a

    def _halt(self, why: str) -> None:
        if self.status != "HALTED":
            self.cfg["status"], self.cfg["reason"] = "HALTED", why
            self.save()
            self.note("halted", why + "; nothing is traded until the operator switches the bot off", alert=True)

    def _loss_check(self, equity: float, where: str) -> None:
        capital = sum(b["capital"] for b in self.cfg["books"].values())
        if capital - equity >= float(self.cfg["max_loss"]):
            self.cfg["status"], self.cfg["reason"] = "CLOSING", f"loss limit {self.cfg['max_loss']:g} USD reached ({where})"
            self.save()
            self.note("loss_limit", f"equity {equity:.2f} of {capital:.2f} USD: closing everything", alert=True)
            self.next_check = 0.0

    async def _reconcile(self, c: Any) -> bool:
        positions = {p["symbol"]: float(p["qty"]) for p in await c.request("GET", "/v2/positions") if p.get("symbol") in SYMBOLS}
        mine = {BOOKS[k]: b["qty"] for k, b in self.cfg["books"].items()}
        for sym in SYMBOLS:
            if abs(positions.get(sym, 0.0) - mine.get(sym, 0.0)) > EPS + 1e-6 * mine.get(sym, 0.0):
                self._halt(f"the broker holds {positions.get(sym, 0.0):g} {sym}, the bot's ledger {mine.get(sym, 0.0):g}")
                return False
        ours = {r[0] for r in self.db.execute("SELECT cid FROM orders")}
        foreign = [o for o in await c.request("GET", "/v2/orders?status=open&limit=500")
                   if o.get("symbol") in SYMBOLS and o.get("client_order_id") not in ours]
        if foreign:
            self._halt(f"{len(foreign)} open {'/'.join(SYMBOLS)} order(s) not sent by this bot")
            return False
        return True

    async def _lookup(self, c: Any, cid: str) -> dict[str, Any] | None:
        from app.exchange.alpaca_client import AlpacaError
        import urllib.parse
        try:
            return await c.request("GET", "/v2/orders:by_client_order_id?" + urllib.parse.urlencode({"client_order_id": cid}))
        except AlpacaError as exc:
            if exc.status == 404:
                return None
            raise

    async def _submit_planned(self, c: Any, now: datetime) -> None:
        for o in self._open_orders():
            if o["status"] != "planned":
                continue
            day_close = self.sessions.get(o["day"], {}).get("close")
            late = o["reason"] is None and (day_close is None or now >= day_close - LATE)   # rule orders: before the bell only
            if late:
                self._mark(o["cid"], "skipped", "too late for this session's close")
                self.note("order_skipped", f"{o['cid']}: not sent before the close", alert=True)
                continue
            if self.status == "HALTED":
                self._mark(o["cid"], "skipped", "bot halted")
                continue
            a = await self._account(c)
            body = json.loads(o["body"])
            if o["side"] == "buy":
                if self.status != "ARMED":
                    self._mark(o["cid"], "skipped", f"bot is {self.status}")
                    continue
                cash = float(a.get("cash") or 0)
                notional = min(float(body["notional"]), math.floor(cash * 100) / 100)      # never margin
                if notional < MIN_NOTIONAL:
                    self._mark(o["cid"], "skipped", f"account cash {cash:.2f} USD")
                    self.note("order_skipped", f"{o['cid']}: not enough cash in the account ({cash:.2f} USD)", alert=True)
                    continue
                body["notional"] = f"{notional:.2f}"
            found = await self._lookup(c, o["cid"])
            if found is None:
                with self.db:
                    self.db.execute("UPDATE orders SET status='submitting', body=? WHERE cid=?", (json.dumps(body), o["cid"]))
                try:
                    found = await c.request("POST", "/v2/orders", body)
                except Exception as exc:
                    self._mark(o["cid"], "uncertain", self.providers.redact(str(exc))[:160])
                    raise
            self._update(o["cid"], found)
            deadline = time.monotonic() + 40
            while found.get("status") not in FINAL and time.monotonic() < deadline:
                await asyncio.sleep(1)
                found = await self._lookup(c, o["cid"]) or found
                self._update(o["cid"], found)
            await self._sync_orders(c)

    def _mark(self, cid: str, status: str, reason: str) -> None:
        with self.db:
            self.db.execute("UPDATE orders SET status=?, reason=COALESCE(reason || '; ', '') || ? WHERE cid=?", (status, reason, cid))

    def _update(self, cid: str, o: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("UPDATE orders SET status=?, filled_qty=?, avg_px=? WHERE cid=?",
                            (o.get("status", "unknown"), float(o.get("filled_qty") or 0),
                             float(o["filled_avg_price"]) if o.get("filled_avg_price") else None, cid))

    async def _sync_orders(self, c: Any) -> None:
        """Apply every final broker order to the ledger exactly once."""
        for o in self._open_orders():
            if o["status"] == "planned":
                continue
            found = await self._lookup(c, o["cid"])
            if found is None:
                if o["status"] in ("uncertain", "submitting") and (self.utcnow() - datetime.fromisoformat(o["ts"])).total_seconds() > 120:
                    self._mark(o["cid"], "lost", "the broker has no such order: it was never placed")
                continue
            self._update(o["cid"], found)
            if found.get("status") not in FINAL:
                continue
            qty = float(found.get("filled_qty") or 0)
            book = self.cfg["books"][o["book"]]
            if qty > EPS:
                px = float(found["filled_avg_price"])
                self._fill(book, o["side"], qty, px, o["day"], "live", self.cfg.get("generation"), o["book"], o["symbol"],
                           o["reason"] or ("IBS < 0.10" if o["side"] == "buy" else "close > previous high"))
            with self.db:
                self.db.execute("UPDATE orders SET applied=1 WHERE cid=?", (o["cid"],))
            self.save()
            self.note("order_" + found["status"], f"{o['book']} {o['side']} {qty:g} {o['symbol']}"
                      + (f" at {float(found['filled_avg_price']):.2f}" if qty > EPS else ""), alert=True)
            if self.status == "CLOSING" and not self._holding() and not self._open_orders():
                self._off((self.cfg.get("reason") or "switched off") + ": all positions closed")

    async def _flatten(self, c: Any, now: datetime) -> None:
        day = now.astimezone(market.ET).date().isoformat()
        for book, b in self.cfg["books"].items():
            if b["qty"] > EPS and not any(o["book"] == book for o in self._open_orders()):
                self._plan(f"{day}-{int(time.time())}", book, BOOKS[book], "sell", qty=b["qty"], reason=self.cfg.get("reason") or "switched off")
        await self._submit_planned(c, now)
        if not self._holding() and not self._open_orders():
            self._off(self.cfg.get("reason") or "switched off")

    def _off(self, reason: str) -> None:
        self.cfg.update(status="OFF", reason=reason, stopped_ts=self.utcnow().isoformat(timespec="seconds"))
        self.save()
        self.note("off", reason, alert=True)

    # -- operator controls (private, authenticated) ----------------------------------------------------------------------
    async def arm(self, network: str, capital_qqq: Any, capital_tqqq: Any, max_loss: Any, confirm: str) -> dict[str, Any]:
        async with self.lock:
            if self.status in LIVE_STATES:
                raise Blocked(f"the bot is {self.status}: switch it off first")
            if self._holding() or self._open_orders():
                raise Blocked("a previous live position or order is still recorded: resolve it first")
            if (confirm or "").strip() != CONFIRM:
                raise Blocked(f'type "{CONFIRM}" to confirm')
            if network not in ("testnet", "mainnet"):
                raise Blocked("choose Alpaca live (mainnet) or Alpaca paper (testnet)")
            try:
                cap = {"IBS-QQQ": float(capital_qqq or 0), "IBS-TQQQ": float(capital_tqqq or 0)}
                max_loss = float(max_loss)
            except (TypeError, ValueError):
                raise Blocked("capital and loss limit must be numbers") from None
            if not all(math.isfinite(v) for v in (*cap.values(), max_loss)):
                raise Blocked("capital and loss limit must be numbers")
            if any(v != 0 and v < MIN_BOOK for v in cap.values()) or any(v < 0 for v in cap.values()):
                raise Blocked(f"each book's capital is 0 (book unused) or at least {MIN_BOOK:g} USD")
            total = sum(cap.values())
            if total <= 0:
                raise Blocked("give at least one book some capital")
            if total > self.max_capital():
                raise Blocked(f"total capital is capped at {self.max_capital():g} USD on this server (IBS_BOT_MAX_CAPITAL_USD)")
            if not 0 < max_loss <= total:
                raise Blocked("the loss limit must be above 0 and at most the total capital")
            if network == "mainnet":
                if not self.secure:
                    raise Blocked("real money needs the password-protected private dashboard")
                if not self.providers.mainnet_switch():
                    raise Blocked("real money is locked on the server: set LIVE_MIRROR_MAINNET_ENABLED=true (operator)")
            if not self.providers.keys("alpaca", network):
                raise Blocked(f"no Alpaca {'live' if network == 'mainnet' else 'paper'} keys: save them in System -> Providers & Live")
            owner = self.other_user(network)
            if owner:
                raise Blocked(f"{owner} uses this Alpaca account: one strategy per account")
            c = self.providers.client("alpaca", network)
            try:
                a = await c.fetch_account()
                if a.get("status") != "ACTIVE" or (a.get("currency") or "USD") != "USD" or not a.get("id"):
                    raise Blocked("the Alpaca account must be ACTIVE and in USD")
                if a.get("trading_blocked") or a.get("account_blocked") or a.get("trade_suspended_by_user"):
                    raise Blocked("trading is blocked on the Alpaca account")
                cash = float(a.get("cash") or 0)
                if not math.isfinite(cash) or cash < total:
                    raise Blocked(f"the account has {cash:.2f} USD cash; the books need {total:.2f} (no margin is used)")
                if any(p.get("symbol") in SYMBOLS for p in await c.request("GET", "/v2/positions")):
                    raise Blocked("the account already holds QQQ or TQQQ: the bot starts flat and only trades its own shares")
                if any(o.get("symbol") in SYMBOLS for o in await c.request("GET", "/v2/orders?status=open&limit=500")):
                    raise Blocked("the account has open QQQ/TQQQ orders: cancel them first")
                for book, sym in BOOKS.items():
                    if cap[book] > 0:
                        asset = await c.request("GET", "/v2/assets/" + sym)
                        if not asset.get("tradable") or not asset.get("fractionable"):
                            raise Blocked(f"{sym} is not tradable in fractional shares on this account")
            finally:
                await c.close()
            self.cfg = {"status": "ARMED", "network": network, "account_id": a["id"], "capital": cap, "max_loss": max_loss,
                        "books": new_books(cap), "generation": str(int(time.time())), "freeze_hash": freeze_hash(),
                        "armed_ts": self.utcnow().isoformat(timespec="seconds"), "stopped_ts": None, "reason": None}
            self.next_check = 0.0
            self.save()
            self.note("armed", f"Alpaca {'LIVE' if network == 'mainnet' else 'paper'}: QQQ book {cap['IBS-QQQ']:g} USD, TQQQ book "
                               f"{cap['IBS-TQQQ']:g} USD, loss limit {max_loss:g} USD; starts flat, first decision at the next close",
                      alert=True)
        return self.private_view()

    async def disarm(self, reason: str) -> dict[str, Any]:
        async with self.lock:
            if self.status == "OFF" and not self._holding():
                return self.private_view()
            if self.status == "HALTED":
                # The ledger no longer matches the broker: nothing is sold automatically. The operator settles the
                # positions in Alpaca; the ledger's last state stays in the journal.
                self.note("halted_off", f"{reason}; ledger at switch-off: {json.dumps(self.cfg.get('books'))}", alert=True)
                self.cfg["books"] = new_books(self.cfg.get("capital") or {})
                with self.db:
                    self.db.execute("UPDATE orders SET status='skipped', reason=COALESCE(reason || '; ', '') || 'halted' WHERE applied=0 AND status='planned'")
                self._off(reason + " (was HALTED: settle any QQQ/TQQQ position in Alpaca yourself)")
                return self.private_view()
            if not self._holding() and not self._open_orders():
                self._off(reason)
                return self.private_view()
            self.cfg["status"], self.cfg["reason"] = "CLOSING", reason
            self.save()
            self.note("closing", f"{reason}: selling the bot's positions (at the next open if the market is closed)", alert=True)
            self.next_check = 0.0
            try:
                await self.manage(self.utcnow())
            except Exception as exc:
                self.error = self.providers.redact(f"{type(exc).__name__}: {exc}")[:300]
        return self.private_view()

    async def call(self, fn: Callable[[], Any]) -> Any:
        """The kill switch's entry point (app/core/api.py): same as the other live switches."""
        return await fn()

    # -- views -----------------------------------------------------------------------------------------------------------
    def _decisions(self, n: int) -> list[dict[str, Any]]:
        out = []
        for r in self.db.execute("SELECT * FROM decisions ORDER BY day DESC LIMIT ?", (n,)):
            live = json.loads(r["live"]) if r["live"] else {}
            out.append({"day": r["day"], "estimate": json.loads(r["estimate"]) if r["estimate"] else None,
                        "official": json.loads(r["official"]) if r["official"] else None,
                        "live_status": live.get("status"), "live_orders": len(live.get("orders") or [])})
        return out

    def _trades(self, kind: str, money: bool) -> list[dict[str, Any]]:
        keep = ("book", "symbol", "entry_day", "exit_day", "entry_px", "exit_px", "ret_pct", "reason") + (("qty", "pnl", "generation") if money else ())
        return [{k: r[k] for k in keep} for r in self.db.execute("SELECT * FROM trades WHERE kind=? ORDER BY id DESC LIMIT 300", (kind,))]

    def _curve(self) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM daily ORDER BY day")]
        if not rows:
            return []
        q0, t0 = rows[0]["qqq"], rows[0]["tqqq"]
        out = []
        for r in rows:
            sh = json.loads(r["shadow"])
            out.append({"day": r["day"], "ibs": r["ibs"], "qqq": r["qqq"], "tqqq": r["tqqq"],
                        "shadow_qqq_pct": (sh["IBS-QQQ"] / SHADOW_CAPITAL - 1) * 100, "shadow_tqqq_pct": (sh["IBS-TQQQ"] / SHADOW_CAPITAL - 1) * 100,
                        "hold_qqq_pct": (r["qqq"] / q0 - 1) * 100, "hold_tqqq_pct": (r["tqqq"] / t0 - 1) * 100})
        return out

    def _schedule(self) -> dict[str, Any]:
        now = self.utcnow()
        nxt = next(((d, s) for d, s in sorted(self.sessions.items()) if s["close"] - LATE > now), None)
        if not nxt:
            return {}
        d, s = nxt
        return {"next_session": d, "decision_at": (s["close"] - PRE_CLOSE).astimezone(timezone.utc).isoformat(),
                "close_at": s["close"].astimezone(timezone.utc).isoformat(), "early_close": s["close"].astimezone(market.ET).hour < 16,
                "record_at": (s["close"] + RECORD_AFTER).astimezone(timezone.utc).isoformat()}

    def _book_view(self, books: dict[str, dict[str, Any]], capital: float | None, money: bool) -> dict[str, Any]:
        out = {}
        for k, b in books.items():
            cap = b["capital"] if capital is None else capital
            row = {"symbol": BOOKS[k], "holding": b["qty"] > EPS, "entry_px": b["entry_px"], "entry_day": b["entry_day"],
                   "realized_pct": b["realized"] / cap * 100 if cap else None}
            if money:
                row.update(capital=b["capital"], cash=b["cash"], qty=b["qty"], realized=b["realized"])
            out[k] = row
        return out

    def public_view(self) -> dict[str, Any]:
        """GET /api/public/ibs-bot: read-only; no account, no amounts, no keys."""
        sh = self.shadow["books"]
        last = self.db.execute("SELECT shadow FROM daily ORDER BY day DESC LIMIT 1").fetchone()
        sh_eq = json.loads(last[0]) if last else {k: SHADOW_CAPITAL for k in BOOKS}
        return {"name": self.NAME, "rule": {"signal": "QQQ IBS = (close - low) / (high - low), regular session",
                                            "entry": f"flat and IBS < {IBS_ENTRY} -> buy at the close",
                                            "exit": "holding and close > previous session high -> sell at the close",
                                            "books": BOOKS, "long_only": True, "stop": None, "freeze_hash": freeze_hash(),
                                            "spec": "docs/IBS_LIVE_BOT.md"},
                "execution": {"decision": "2 minutes before the close (Alpaca calendar, early closes too)",
                              "record": "20 minutes after the close (official SIP daily candle)",
                              "orders": "fractional day market orders, QQQ and TQQQ only", "feed": self.feed.upper()},
                "live": {"status": self.status, "network": self.cfg.get("network") if self.status in LIVE_STATES else None,
                         "armed_ts": self.cfg.get("armed_ts") if self.status in LIVE_STATES else None,
                         "books": self._book_view(self.cfg["books"], None, False) if self.cfg.get("books") and self.status in LIVE_STATES else None,
                         "trades": self._trades("live", False)[:100]},
                "shadow": {"since": self.shadow.get("since"), "capital_each": SHADOW_CAPITAL,
                           "equity_pct": {k: (v / SHADOW_CAPITAL - 1) * 100 for k, v in sh_eq.items()},
                           "books": self._book_view(sh, SHADOW_CAPITAL, False), "trades": self._trades("shadow", False)[:100]},
                "decisions": self._decisions(60), "curve": self._curve()[-500:], "schedule": self._schedule(),
                "heartbeat": self.heartbeat, "data_ok": self.error is None, "read_only": True}

    def private_view(self) -> dict[str, Any]:
        v = self.public_view()
        books = self.cfg.get("books")
        v["live"].update({"status": self.status, "network": self.cfg.get("network"), "reason": self.cfg.get("reason"),
                          "capital": self.cfg.get("capital"), "max_loss": self.cfg.get("max_loss"),
                          "armed_ts": self.cfg.get("armed_ts"), "stopped_ts": self.cfg.get("stopped_ts"),
                          "books": self._book_view(books, None, True) if books else None, "trades": self._trades("live", True),
                          "account": self.account if self.status in LIVE_STATES else None,
                          "orders": [{k: o[k] for k in ("cid", "day", "book", "symbol", "side", "status", "filled_qty", "avg_px", "reason", "ts")}
                                     for o in map(dict, self.db.execute("SELECT * FROM orders ORDER BY rowid DESC LIMIT 60"))]})
        v["controls"] = {"confirm_phrase": CONFIRM, "max_capital": self.max_capital(), "min_book": MIN_BOOK,
                         "keys": {net: bool(self.providers.keys("alpaca", net)) for net in ("mainnet", "testnet")},
                         "mainnet_switch": self.providers.mainnet_switch(), "secure": self.secure,
                         "other_user": {net: self.other_user(net) for net in ("mainnet", "testnet")}}
        v["status"] = self.status                                    # the kill switch reports it
        v["error"] = self.error
        v["journal"] = [dict(r) for r in self.db.execute("SELECT ts,event,detail FROM journal ORDER BY id DESC LIMIT 60")]
        return v
