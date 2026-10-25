"""Forward PAPER desks for T3-CCI and CHI: live completed candles, the research rules, two accounting modes. Paper only.

Per coin book (10,000 USDT each, its own cash -- no book ever spends another's balance):
- hourly COMPLETED candles from the snapshot's venue (Binance spot / Gate / Bitget), aggregated into UTC 4h candles only
  when all four hours exist; WARM_DAYS of history initialise the indicators and never trade;
- the book starts FLAT at FORWARD_START (the first 4h boundary after the desk was first switched on); only candles that
  open at or after it may request orders;
- a request at a 4h close becomes a PENDING order filled at the next 4h candle's open, as soon as that candle's first
  hour is available (the 4h open is that hour's open); buy only while flat, sell only while held, no pyramiding;
- costs per side: commission 0.10 %, adverse slippage 0.02 % (the research costs); all-in compounding;
- two modes, side by side:
    execution  -- the exchange's quantity step and minimum notional are enforced; an impossible order is SKIPPED and
                  recorded with its reason. This is the desk's paper P&L.
    research   -- fractional units, exactly the research reproduction's accounting (a reference, not a second account).
Persistence: candles, cash, units, the pending order and the last processed candle are stored after every candle, so a
restart reprocesses nothing already processed (no duplicate fills) and never books history as forward profit.

CHI and barstate.islast: the source reads close[1] instead of close on the LAST candle of its data. Forward, the
candle being decided is always the last one, so every forward decision is made with that substitution (as the script
would behave live, using only information available at that close). Later the same candle is no longer the last, and a
re-computation would use its real close; the desk records every candle where that historical-parity request differs
from the decision actually taken ("islast divergences") instead of silently changing the rule.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.divergence.store import Store

from .broker import FEE, SLIP, CAPITAL
from .data import H1, H4, aggregate_4h
from .indicators import CHI_PARAMS, T3_CCI_PARAMS, chi_requests, t3cci_requests

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
DAY = 86_400_000
WARM_DAYS, LOOP_S, STALL_S = 120, 60, 600
MODES = ("execution", "research")

UNIVERSE = json.loads((HERE / "universe.json").read_text(encoding="utf-8"))["assets"]
DESKS: dict[str, dict[str, Any]] = {
    "t3cci": {"name": "T3-CCI Desk", "strategy": "FX Sniper: T3-CCI Strategy", "author": "HPotter", "script_id": "tfkYcvBX",
              "url": "https://www.tradingview.com/script/tfkYcvBX-FX-Sniper-T3-CCI-Strategy-Backtest/", "env": "T3CCI_ENABLED",
              "params": T3_CCI_PARAMS, "rule_version": "T3CCI-R1",
              "books": [a for a in UNIVERSE if a["asset"] in ("ZEC", "CAKE")],
              "rules": ["4h candles; CCI(14) on close; six-stage T3 smoothing (period 5, b 0.618), nz-seeded",
                        "T3 > 0 requests a long entry; T3 < 0 requests a cash exit (the source's short); exactly 0 keeps the previous direction",
                        "no stop-loss, take-profit, ladder, break-even or trailing stop",
                        "fills at the next 4h open; long or cash; no pyramiding"]},
    "chi": {"name": "CHI Desk", "strategy": "CHI (Chandelier + BB + EMAs)", "author": "juanchez", "script_id": "8F6WNLkO",
            "url": "https://www.tradingview.com/script/8F6WNLkO/", "env": "CHI_ENABLED", "params": CHI_PARAMS, "rule_version": "CHI-R1",
            "books": list(UNIVERSE),
            "rules": ["4h candles; EMA20 / EMA50 on close; Bollinger 40 x 2 (population stdev)",
                      "entry: EMA20 > EMA50 and the % change of relative BB width crosses above 9 (not width >= 9 %)",
                      "exit: close crosses below highest(high,22) - 4 x ATR22 (close-confirmed market exit, not an intrabar stop)",
                      "the source's short entry (EMA20 < EMA50 and the width cross) closes an open long",
                      "close[barstate.islast ? 1 : 0] preserved (see the islast note)",
                      "fills at the next 4h open; long or cash; no pyramiding"]},
}


def _new_book(capital: float) -> dict[str, Any]:
    return {"cash": capital, "units": 0.0, "dust": 0.0, "entry": None, "pending": None, "trades": [], "equity": [],
            "skips": [], "fees": 0.0, "peak": capital, "max_dd": 0.0}


class TvDeskService:
    def __init__(self, path: Path | str, desk: str, env: dict | None = None, feeds: Any = None,
                 clock: Callable[[], float] = time.time, telegram: Callable[[], Any] = lambda: None):
        self.desk = desk
        self.cfg = DESKS[desk]
        env = os.environ if env is None else env
        self.enabled = str(env.get(self.cfg["env"], "true")).lower() in ("1", "true", "yes")
        self.store = Store(path)
        self.clock = clock
        self.telegram = telegram
        self._feeds = feeds
        self.books = [dict(b) for b in self.cfg["books"]]
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.health: dict[str, Any] = {"status": "STARTING" if self.enabled else "DISABLED", "enabled": self.enabled,
                                       "heartbeat_ms": None, "last_cycle_ms": None, "error": None, "stalled_alerted": False}
        if not self.store.get("forward_start_ms"):
            now = self.now()
            self.store.set("forward_start_ms", (now // H4 + 1) * H4)
            self.store.set("created_ms", now)
        self._history: dict[str, Any] | None = None

    # -- basics -------------------------------------------------------------------------------------------------------------
    @property
    def feeds(self):
        if self._feeds is None:
            from .feeds import Feeds
            self._feeds = Feeds(clock=self.clock, stop=self.stop_event)
        return self._feeds

    def now(self) -> int:
        return int(self.clock() * 1000)

    @property
    def forward_start(self) -> int:
        return int(self.store.get("forward_start_ms"))

    @property
    def history(self) -> dict[str, Any] | None:
        """The verified historical reproduction, loaded on first use (it is ~1 MB of JSON)."""
        if self._history is None:
            self._history = self._load_history()
        return self._history

    def _load_history(self) -> dict[str, Any] | None:
        p = HERE / "history" / f"{self.desk}.json.gz"
        try:
            return json.loads(gzip.decompress(p.read_bytes()))
        except (OSError, ValueError):
            return None

    def _state(self, mode: str, asset: str) -> dict[str, Any]:
        return self.store.get(f"book:{mode}:{asset}") or _new_book(CAPITAL)

    def _put(self, mode: str, asset: str, st: dict[str, Any]) -> None:
        st["equity"] = st["equity"][-5000:]
        st["trades"] = st["trades"][-2000:]
        st["skips"] = st["skips"][-500:]
        self.store.set(f"book:{mode}:{asset}", st)

    # -- threads ------------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._run, name=f"{self.desk}-paper", daemon=True).start()
        threading.Thread(target=self._watchdog, name=f"{self.desk}-watchdog", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.cycle()
                self.health.update(status="RUNNING", error=None)
            except Exception as exc:
                log.exception("%s cycle", self.desk)
                self.health.update(status="DEGRADED", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            self.health["heartbeat_ms"] = self.now()
            if self.stop_event.wait(LOOP_S):
                break

    def _watchdog(self) -> None:
        while not self.stop_event.wait(60):
            self.check_stall()

    def check_stall(self) -> str | None:
        """Heartbeat-gap alert: a worker silent for STALL_S is reported once (and its recovery once)."""
        hb = self.health.get("heartbeat_ms")
        stalled = hb is not None and self.now() - hb > STALL_S * 1000
        t = self.telegram()
        msg = None
        if stalled and not self.health["stalled_alerted"]:
            self.health["stalled_alerted"] = True
            msg = (f"⚠️ <b>{self.cfg['name']}</b> (paper) worker stalled: no heartbeat for "
                   f"{(self.now() - hb) // 60000} min.")
        elif not stalled and self.health["stalled_alerted"]:
            self.health["stalled_alerted"] = False
            msg = f"✅ <b>{self.cfg['name']}</b> (paper) worker recovered."
        if msg and t is not None and hasattr(t, "live_alert"):
            t.live_alert(msg)
        return msg

    # -- the cycle ------------------------------------------------------------------------------------------------------------
    def _hours(self, b: dict) -> list[dict]:
        key = f"{b['asset']}|1h"
        since = self.forward_start - WARM_DAYS * DAY
        have = self.store.bars(key, since)
        now = self.now()
        if not have or have[-1]["t"] + 2 * H1 <= now:                  # a newer completed hour should exist
            start = have[-1]["t"] + H1 if have else since
            self.store.save_bars(key, self.feeds.hours(b["venue"], b["pair"], start, now))
            have = self.store.bars(key, since)
        if not have:
            raise RuntimeError(f"no hourly candles from {b['venue']} for {b['pair']}")
        return have

    def _filters(self, b: dict) -> dict[str, Any] | None:
        key = f"filters:{b['asset']}"
        f = self.store.get(key)
        if not f or self.now() - f.get("fetched_ms", 0) > DAY:
            try:
                f = {**self.feeds.filters(b["venue"], b["pair"]), "fetched_ms": self.now()}
                self.store.set(key, f)
            except Exception as exc:
                log.warning("%s filters %s: %s", self.desk, b["asset"], exc)
        return f

    def cycle(self) -> None:
        for b in self.books:
            if self.stop_event.is_set():
                return
            try:
                hours = self._hours(b)
                self.process(b, hours)
                if self.store.get(f"error:{b['asset']}"):
                    self.store.set(f"error:{b['asset']}", None)
            except Exception as exc:
                log.warning("%s %s: %s", self.desk, b["asset"], exc)
                self.store.set(f"error:{b['asset']}", {"ts": self.now(), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        self.health["last_cycle_ms"] = self.now()

    def _requests(self, bars: list[dict]):
        if self.desk == "t3cci":
            buy, sell, _ = t3cci_requests(bars)
        else:
            buy, sell, _ = chi_requests(bars, islast=True)
        return bool(buy[-1]), bool(sell[-1])

    def process(self, b: dict, hours: list[dict]) -> None:
        """Advance one coin's books through every newly completed 4h candle (and fill pending orders at 4h opens)."""
        asset = b["asset"]
        with self.lock:
            meta = self.store.get(f"meta:{asset}") or {"last_t": None, "decisions": {}, "divergences": []}
            bars = aggregate_4h(hours)
            hour_open = {h["t"]: h["o"] for h in hours}
            filters = self._filters(b) if self.desk else None
            states = {m: self._state(m, asset) for m in MODES}
            start = self.forward_start
            new = [x for x in bars if x["t"] >= start and (meta["last_t"] is None or x["t"] > meta["last_t"])]
            for x in new:
                k = next(i for i, y in enumerate(bars) if y["t"] == x["t"])
                for m in MODES:
                    st = states[m]
                    self._fill(st, x["t"], x["o"], m, filters)                      # an order pending for this open
                    self._mark(st, x)
                buy, sell = self._requests(bars[:k + 1])                           # decided at this close (islast here)
                meta["decisions"][str(x["t"])] = [buy, sell]
                if self.desk == "chi" and k >= 1 and str(bars[k - 1]["t"]) in meta["decisions"]:
                    pb, ps, _ = chi_requests(bars[:k + 1], islast=True)            # bars[k-1] is no longer the last
                    parity = [bool(pb[k - 1]), bool(ps[k - 1])]
                    taken = meta["decisions"][str(bars[k - 1]["t"])]
                    if parity != taken:
                        meta["divergences"].append({"t": bars[k - 1]["t"], "decided": taken, "historical_parity": parity})
                for m in MODES:
                    st = states[m]
                    held = (st["units"] or 0) > 0
                    if held and sell:
                        st["pending"] = {"kind": "sell", "signal_t": x["t"], "fill_t": x["t"] + H4, "created_ms": self.now()}
                    elif not held and buy:
                        st["pending"] = {"kind": "buy", "signal_t": x["t"], "fill_t": x["t"] + H4, "created_ms": self.now()}
                meta["last_t"] = x["t"]
                meta["decisions"] = {t: v for t, v in meta["decisions"].items() if int(t) >= x["t"] - 30 * H4}
            # a pending order fills as soon as its 4h candle's first hour is known (that hour's open is the 4h open)
            for m in MODES:
                p = states[m].get("pending")
                if p and p["fill_t"] in hour_open:
                    self._fill(states[m], p["fill_t"], hour_open[p["fill_t"]], m, filters)
            for m in MODES:
                self._put(m, asset, states[m])
            meta["divergences"] = meta["divergences"][-200:]
            self.store.set(f"meta:{asset}", meta)

    def _fill(self, st: dict, t: int, open_px: float, mode: str, filters: dict | None) -> None:
        p = st.get("pending")
        if not p or p["fill_t"] != t:
            return
        st["pending"] = None
        if any(tr.get("fill_t") == t for tr in st["trades"]) or (st.get("entry") or {}).get("fill_t") == t:
            return                                                                  # already filled (restart safety)
        step = float((filters or {}).get("step") or 0) if mode == "execution" else 0.0
        min_qty = float((filters or {}).get("min_qty") or 0) if mode == "execution" else 0.0
        min_notional = float((filters or {}).get("min_notional") or 0) if mode == "execution" else 0.0
        if mode == "execution" and not filters:
            st["skips"].append({"time": t, "side": p["kind"], "reason": "exchange order filters unavailable: order not simulated"})
            return
        if p["kind"] == "buy" and st["units"] == 0:
            px = open_px * (1 + SLIP)
            qty = st["cash"] / (px * (1 + FEE))
            if step > 0:
                qty = int(qty / step + 1e-9) * step
                if qty <= 0 or qty < min_qty or qty * px < min_notional:
                    st["skips"].append({"time": t, "side": "buy", "reason": f"order {qty * px:.4f} USDT below the exchange minimum "
                                                                           f"(min qty {min_qty:g}, min notional {min_notional:g} USDT)"})
                    return
            spend = qty * px
            fee = spend * FEE
            st["entry"] = {"fill_t": t, "entry_time": t, "entry_price": px, "units": qty, "entry_cash": st["cash"],
                           "entry_fee": fee, "signal_t": p["signal_t"]}
            st["cash"] = 0.0 if step == 0 else st["cash"] - spend - fee
            st["units"] = qty
            st["fees"] += fee
        elif p["kind"] == "sell" and st["units"] > 0:
            px = open_px * (1 - SLIP)
            qty = st["units"]
            if step > 0:
                qty = int(qty / step + 1e-9) * step
                if qty <= 0 or qty * px < min_notional:
                    st["skips"].append({"time": t, "side": "sell", "reason": f"position {st['units'] * px:.4f} USDT below the "
                                                                            f"exchange minimum {min_notional:g} USDT: cannot be sold"})
                    return
            proceeds = qty * px
            fee = proceeds * FEE
            e = st["entry"]
            frac = qty / e["units"] if e["units"] else 1.0
            st["cash"] += proceeds - fee
            st["dust"] = (st.get("dust") or 0.0) + max(0.0, st["units"] - qty)
            st["trades"].append({"fill_t": t, "entry_time": e["entry_time"], "exit_time": t, "entry_price": e["entry_price"],
                                 "exit_price": px, "units": qty, "entry_cash": e["entry_cash"] * frac, "exit_cash": proceeds - fee,
                                 "entry_fee": e["entry_fee"] * frac, "exit_fee": fee,
                                 "pnl": proceeds - fee - e["entry_cash"] * frac, "signal_t": p["signal_t"]})
            st["units"], st["entry"] = 0.0, None
            st["fees"] += fee

    def _mark(self, st: dict, bar: dict) -> None:
        held = (st["units"] or 0) + (st.get("dust") or 0)
        eq = st["cash"] + held * bar["c"]
        lo = st["cash"] + held * bar["l"]
        st["equity"].append({"t": bar["t"] + H4, "equity": eq, "low": lo})
        st["peak"] = max(st["peak"], eq)
        st["max_dd"] = max(st["max_dd"], 100 * (1 - eq / st["peak"]) if st["peak"] else 0.0)

    # -- read-only views ------------------------------------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        h = dict(self.health)
        hb = h.get("heartbeat_ms")
        h["heartbeat_age_s"] = round((self.now() - hb) / 1000, 1) if hb else None
        if self.enabled and hb and self.now() - hb > STALL_S * 1000:
            h["status"] = "STALLED"
        books, totals = [], {m: {"capital": 0.0, "equity": 0.0, "realized": 0.0, "unrealized": 0.0, "fees": 0.0, "trades": 0,
                                 "open": 0, "pending": 0, "skips": 0} for m in MODES}
        for b in self.books:
            meta = self.store.get(f"meta:{b['asset']}") or {}
            row = {"asset": b["asset"], "venue": b["venue"], "pair": b["pair"], "last_candle": meta.get("last_t"),
                   "divergences": len(meta.get("divergences") or []), "error": self.store.get(f"error:{b['asset']}"),
                   "filters": self.store.get(f"filters:{b['asset']}")}
            for m in MODES:
                st = self._state(m, b["asset"])
                last = st["equity"][-1]["equity"] if st["equity"] else CAPITAL
                realized = sum(t["pnl"] for t in st["trades"])
                e = st.get("entry")
                mark = None
                if e and st["equity"]:
                    mark = (st["equity"][-1]["equity"] - st["cash"]) / st["units"] if st["units"] else None
                unreal = (st["equity"][-1]["equity"] - st["cash"] - e["entry_cash"]) if (e and st["equity"]) else 0.0
                row[m] = {"equity": last, "return_pct": (last / CAPITAL - 1) * 100, "cash": st["cash"], "units": st["units"],
                          "dust": st.get("dust", 0.0), "entry": e, "mark": mark, "unrealized": unreal, "realized": realized,
                          "fees": st["fees"], "max_dd_pct": st["max_dd"], "trades": len(st["trades"]), "pending": st.get("pending"),
                          "skips": st["skips"][-5:], "skips_total": len(st["skips"])}
                tt = totals[m]
                tt["capital"] += CAPITAL
                tt["equity"] += last
                tt["realized"] += realized
                tt["unrealized"] += unreal
                tt["fees"] += st["fees"]
                tt["trades"] += len(st["trades"])
                tt["open"] += 1 if st["units"] else 0
                tt["pending"] += 1 if st.get("pending") else 0
                tt["skips"] += len(st["skips"])
            books.append(row)
        for m in MODES:
            tt = totals[m]
            tt["return_pct"] = (tt["equity"] / tt["capital"] - 1) * 100 if tt["capital"] else 0.0
            tt["max_dd_pct"] = self._portfolio_dd(m)
        return {"desk": self.desk, "name": self.cfg["name"], "strategy": self.cfg["strategy"], "author": self.cfg["author"],
                "url": self.cfg["url"], "rule_version": self.cfg["rule_version"], "rules": self.cfg["rules"], "params": self.cfg["params"],
                "costs": {"commission_per_side": FEE, "slippage_per_side": SLIP}, "capital_per_book": CAPITAL, "books_count": len(self.books),
                "allocated_capital": CAPITAL * len(self.books), "forward_start_ms": self.forward_start, "warmup_days": WARM_DAYS,
                "health": h, "books": books, "totals": totals, "paper_only": True, "read_only": True,
                "history": (self.history or {}).get("summary"),
                "islast_note": ("Forward decisions on CHI use the source's close[1] substitution on the candle being decided (it is "
                                "the last candle at decision time); a later re-computation of the same candle would use its real "
                                "close. Candles where the two differ are listed as islast divergences." if self.desk == "chi" else None),
                "realtime_study": self._realtime_study()}

    def _realtime_study(self) -> dict[str, Any] | None:
        """CHI only: the backtest vs the same rule decided at each candle's close (scripts/tvdesks_chi_realtime_study.py)."""
        if self.desk != "chi":
            return None
        try:
            return json.loads((HERE / "history" / "chi_realtime.json").read_text())
        except (OSError, ValueError):
            return None

    def _portfolio_curve(self, mode: str) -> list[tuple[int, float]]:
        series: dict[int, float] = {}
        count: dict[int, int] = {}
        for b in self.books:
            for p in self._state(mode, b["asset"])["equity"]:
                series[p["t"]] = series.get(p["t"], 0.0) + p["equity"]
                count[p["t"]] = count.get(p["t"], 0) + 1
        n = len(self.books)
        return [(t, v) for t, v in sorted(series.items()) if count[t] == n]

    def _portfolio_dd(self, mode: str) -> float:
        peak, dd = CAPITAL * len(self.books), 0.0
        for _, v in self._portfolio_curve(mode):
            peak = max(peak, v)
            dd = max(dd, 100 * (1 - v / peak))
        return dd

    def forward_monthly(self, mode: str = "execution") -> list[dict[str, Any]]:
        import datetime as dt
        out, prior = [], CAPITAL * len(self.books)
        by: dict[str, float] = {}
        for t, v in self._portfolio_curve(mode):
            by[dt.datetime.fromtimestamp((t - 1) / 1000, dt.timezone.utc).strftime("%Y-%m")] = v
        for m, v in by.items():
            out.append({"month": m, "opening_equity": prior, "ending_equity": v, "net_return_pct": (v / prior - 1) * 100})
            prior = v
        return out

    def forward_rows(self, kind: str, mode: str, asset: str | None) -> list[dict[str, Any]]:
        books = [b for b in self.books if asset in (None, "", "portfolio", b["asset"])]
        if kind == "trades":
            return [{"asset": b["asset"], **t} for b in books for t in self._state(mode, b["asset"])["trades"]]
        if kind == "skips":
            return [{"asset": b["asset"], **s} for b in books for s in self._state(mode, b["asset"])["skips"]]
        if kind == "equity":
            if asset in (None, "", "portfolio"):
                return [{"time": t, "equity": v} for t, v in self._portfolio_curve(mode)]
            return [{"time": p["t"], "equity": p["equity"], "low_equity": p["low"]} for p in self._state(mode, asset)["equity"]]
        if kind == "monthly":
            return self.forward_monthly(mode)
        if kind == "divergences":
            return [{"asset": b["asset"], **d} for b in books for d in (self.store.get(f"meta:{b['asset']}") or {}).get("divergences", [])]
        raise ValueError("unknown table")
