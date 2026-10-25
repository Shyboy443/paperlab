"""Ensemble paper book: once a day (just after 00:00 UTC) refresh the public data, run the ML leg, and replay the frozen
r4_07 book (research/monthly20/r4_07_ensemble/PREREG.md: rotation + ML + 60-day momentum, inverse-vol weights, 1x) on
the server's own Binance data. The replay is deterministic, so the paper book is the research book run on live data.

- The standalone legs run from LEGS_FROM, the ensemble from BOOK_FROM (the weights need 60 days of leg returns).
- Days before the server's first day are a replay (marked "replay"); every day after is forward paper.
- The day's paper trades (coins whose units changed at today's open) and the target units go to the live switch,
  which copies them onto a linked account, scaled to that account's equity (app/ensemble/live.py).
Public data only; no keys, no orders here.
"""
from __future__ import annotations

import logging
import pickle
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import engine as E
from . import ml
from .data import DAY, EXCL_DAILY, EXCL_ML, H4, Rest, Store

log = logging.getLogger(__name__)

DATA_FROM = 1767225600000      # 2026-01-01: daily bars kept from here (60-day momentum + 30-day universes + warm-up)
LEGS_FROM = 1777593600000      # 2026-05-01: standalone legs (inverse-vol weights need 60 days of their returns)
BOOK_FROM = 1782864000000      # 2026-07-01: the paper book
H4_LOOKBACK = 230 * H4         # 180-bar features + margin
TICK_S, AFTER_OPEN_MS = 60, 90_000
VERSION = "ensemble-r4_07-v1"


def ymd(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class EnsembleService:
    def __init__(self, db_path: Path | str, model_dir: Path | str | None = None, rest: Rest | None = None,
                 clock: Callable[[], float] = time.time, telegram: Callable[[], Any] = lambda: None):
        self.store = Store(db_path)
        self.model_dir = Path(model_dir or Path(db_path).parent)
        self.stop_event = threading.Event()
        self.rest = rest or Rest(self.stop_event)
        self.clock, self.telegram = clock, telegram
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.models: dict[str, ml.Model] = {}
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.status: dict[str, Any] = {"phase": "starting"}
        self._meta = ml.seed_meta()

    # -- lifecycle -------------------------------------------------------------------------------------------------------
    def now(self) -> int:
        return int(self.clock() * 1000)

    def start(self) -> None:
        import os
        if os.environ.get("ENSEMBLE_PAPER_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
            self.status = {"phase": "disabled (ENSEMBLE_PAPER_ENABLED=false)"}
            return
        self.thread = threading.Thread(target=self._run, name="ensemble-paper", daemon=True)
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
                log.exception("ensemble cycle")
                self.status = {"phase": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "ts": self.now()}
            self.stop_event.wait(TICK_S)

    def cycle(self) -> bool:
        """Run the day once its open is known. True when a new day was processed."""
        now = self.now()
        day = now // DAY * DAY
        if now < day + AFTER_OPEN_MS or self.store.get("done_day") == day:
            if self.status.get("phase") == "starting" and self.store.get("done_day") is not None:   # restarted after today's run
                book = self.store.get("book") or {}
                self.status = {"phase": "ok", "day": ymd(self.store.get("done_day")), "ts": book.get("computed_ts")}
            return False
        with self.lock:
            self.status = {"phase": "running", "day": ymd(day), "ts": now}
            if self.store.get("first_day") is None:
                self.store.set("first_day", day)
            self.refresh_daily(day, now)
            self.ensure_preds(day, now)
            out = self.replay(day, now)
            self.store.set("book", out)
            self.store.set("done_day", day)
            self.status = {"phase": "ok", "day": ymd(day), "ts": self.now(), "took_s": round((self.now() - now) / 1000)}
        for fn in list(self.listeners):
            try:
                fn(out["today"])
            except Exception:
                log.exception("ensemble listener")
        return True

    # -- data --------------------------------------------------------------------------------------------------------------
    def refresh_daily(self, day: int, now: int) -> None:
        perps = self.rest.perps()
        info = {s["symbol"]: s for s in perps}
        self.store.set("symbols", {s: {"status": x.get("status"), "coin": x.get("underlyingType") == "COIN"}
                                   for s, x in info.items()})
        for s, x in info.items():
            if x.get("status") != "TRADING":
                continue
            last = self.store.last_d1(s)
            since = DATA_FROM if last is None else last + DAY
            if since > day:
                continue
            rows = self.rest.klines(s, "1d", since, day + DAY)
            if rows:
                self.store.save_d1(s, rows, now)

    def daily_symbols(self) -> list[str]:
        meta = self.store.get("symbols") or {}
        return [s for s in self.store.d1_symbols() if s not in EXCL_DAILY and meta.get(s, {}).get("coin", True)]

    def refresh_funding(self, syms: list[str], end: int) -> None:
        for s in syms:
            last = self.store.mark(f"fund:{s}")
            start = DATA_FROM if last is None else last + 1
            if start >= end:
                continue
            rows = self.rest.funding(s, start, end)
            self.store.save_funding(s, rows)
            self.store.set_mark(f"fund:{s}", max([t for t, _ in rows], default=end - 1))

    def refresh_h4(self, syms: list[str], start: int, end: int, now: int) -> None:
        for s in syms:
            last = self.store.last_h4(s)
            since = start if last is None or last < start else last + H4
            if since >= end:
                continue
            rows = self.rest.klines(s, "4h", since, end)
            if rows:
                self.store.save_h4(s, rows, now)

    # -- ML leg --------------------------------------------------------------------------------------------------------------
    def universe(self, month: str) -> list[str]:
        """The ML leg's month universe: research months ship with the seed; later months are computed at the month start
        from daily quote volume (top 40 over the 30 days before, >= 24 days traded)."""
        seed = self._meta["universes"]
        if month in seed:
            return seed[month]
        saved = self.store.get("universes") or {}
        if month in saved:
            return saved[month]
        m0 = int(datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        meta = self.store.get("symbols") or {}
        syms = [s for s in self.store.d1_symbols() if s not in EXCL_ML and meta.get(s, {}).get("coin", True)]
        names, _, A, _ = self.store.d1_panel(m0 - 30 * DAY, m0, syms)
        qv = np.nan_to_num(A["QV"]).sum(0)
        n = np.isfinite(A["C"]).sum(0)
        ranked = sorted([(qv[j], s) for j, s in enumerate(names) if n[j] >= 24 and qv[j] > 0], reverse=True)
        saved[month] = [s for _, s in ranked[:40]]
        self.store.set("universes", saved)
        return saved[month]

    def model_for(self, month: str, now: int) -> ml.Model:
        if month in self.models:
            return self.models[month]
        path = self.model_dir / f"ensemble-ml-{month}.pkl"
        if path.exists():
            try:
                m = pickle.loads(path.read_bytes())
                if getattr(m, "month", None) == month:
                    self.models = {month: m}
                    return m
            except Exception:
                log.warning("ensemble: could not load %s, re-training", path.name)
        X, y, t = ml.seed_rows()
        m0 = int(datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        if m0 > self._meta["cutoff"]:                                   # add the server's own rows since the seed
            after = int(t.max())
            months, cur = [], datetime.fromtimestamp(self._meta["cutoff"] / 1000, timezone.utc)
            while int(cur.timestamp() * 1000) < m0:
                months.append(cur.strftime("%Y-%m"))
                cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 else cur.replace(month=cur.month + 1)
            uni = {k: self.universe(k) for k in months}
            syms = sorted(set(s for v in uni.values() for s in v) | {"BTCUSDT"})
            start = self._meta["cutoff"] - 30 * DAY - H4_LOOKBACK
            self.refresh_h4(syms, start, m0, now)
            self.refresh_funding(syms, m0)
            times, A, F = self.store.h4_panel(syms, start, m0)
            valid, ranks, reg, names = ml.features(times, A, F, syms, uni)
            Xn, yn, tn = ml.new_rows(times, A, valid, ranks, reg, names, after, m0)
            X, y = np.vstack([X, Xn]), np.concatenate([y, yn])
        m = ml.Model(self._meta["names"], self._meta["rank_features"]).fit(X, y, month)
        try:
            path.write_bytes(pickle.dumps(m))
        except OSError:
            pass
        self.models = {month: m}
        self._alert(f"🧠 Ensemble: ML model for {month} trained on {m.rows:,} rows.")
        return m

    def predict(self, day: int, now: int) -> dict[str, float]:
        month = ml.mkey(day)
        uni = {month: self.universe(month)}
        syms = sorted(set(uni[month]) | {"BTCUSDT"})
        start = day - H4_LOOKBACK
        self.refresh_h4(syms, start, day, now)
        self.refresh_funding(syms, day)
        times, A, F = self.store.h4_panel(syms, start, day + H4)        # + the 00:00 bar (open only)
        _, _, D1, _ = self.store.d1_panel(day, day + DAY, syms)
        A["o"][-1] = D1["O"][0]                                          # its open = the day's open
        for k in ("h", "l", "c", "qv", "tbq"):
            A[k][-1] = np.nan
        valid, ranks, reg, names = ml.features(times, A, F, syms, uni)
        t = len(times) - 2                                               # the 20:00 bar
        js = np.where(valid[t])[0]
        if len(js) < 10:
            return {}
        model = self.model_for(month, now)
        p = model.predict(ml.design(ranks, reg, names, np.full(len(js), t), js))
        return {syms[j]: float(v) for j, v in zip(js, p)}

    def ensure_preds(self, day: int, now: int) -> None:
        have = self.store.pred_src()
        if not have:
            for d, sc in ml.seed_preds().items():
                self.store.save_preds(d, "research", sc)
            have = self.store.pred_src()
        d = max(LEGS_FROM, max(have) + DAY if have else LEGS_FROM)
        while d <= day:
            if d not in have:
                self.store.save_preds(d, "server", self.predict(d, now))
            d += DAY

    # -- the book ------------------------------------------------------------------------------------------------------------
    def data(self, day: int) -> E.Data:
        syms, days, A, F = self.store.d1_panel(DATA_FROM, day + DAY, self.daily_symbols())
        return E.Data(syms, days, A["O"], A["H"], A["L"], A["C"], A["QV"], F)

    def legs(self, D: E.Data) -> list[tuple]:
        preds = self.store.preds()
        out = []
        for k in E.SEL["legs"]:
            if k == "R":
                F = E.leg_rotation(D, **E.LEGP["R"])
            elif k == "M":
                F = E.leg_ml(D, preds, E.LEGP["M"]["k"])
            else:
                F = E.leg_mom(D, **E.LEGP["T"])
            out.append((k, F, E.trade_mask(D, F, k)))
        return out

    def replay(self, day: int, now: int) -> dict[str, Any]:
        D = self.data(day)
        legs = self.legs(D)
        held = sorted({D.syms[j] for _, F, _ in legs for j in np.where((F[D.idx(LEGS_FROM):] != 0).any(0))[0]})
        self.refresh_funding(held, now)                                  # funding of every coin a leg ever held
        D = self.data(day)
        legs = self.legs(D)
        end = day + DAY
        SR, alone = [], {}
        for leg in legs:
            r = E.simulate(D, [leg], LEGS_FROM, end)
            o = np.full(D.T, np.nan)
            o[r["i0"]:r["i0"] + len(r["ret"])] = r["ret"]
            SR.append(o)
            alone[leg[0]] = [[ymd(t), round(e / 100 - 100, 3)] for t, e in r["curve"][::7]]
        SR = np.column_stack(SR)
        wp = E.weight_path(D, SR, E.SEL["scheme"], E.SEL["wlook"], E.SEL["wevery"], start_i=D.idx(E.ANCHOR))
        prev = E.simulate(D, legs, BOOK_FROM, day, wpath=wp)
        cur = E.simulate(D, legs, BOOK_FROM, end, wpath=wp, record=True)
        u0, u1 = prev["state"]["units"], cur["state"]["units"]
        i = D.idx(day)
        o = D.O[i]
        touched = [j for j in range(D.N) if abs(u1[j] - u0[j]) > 1e-12 * max(abs(u0[j]), abs(u1[j]), 1e-12)]
        eq = float(cur["state"]["equity"])
        first = self.store.get("first_day") or day
        today = {"day": day, "date": ymd(day), "equity": eq,
                 "weights": dict(zip(E.SEL["legs"], [round(float(x), 4) for x in cur["state"]["w"]])),
                 "targets": {D.syms[j]: {"units": float(u1[j]), "price": float(o[j]), "w": float(u1[j] * o[j] / eq)}
                             for j in np.where(u1 != 0)[0]},
                 "trades": {D.syms[j]: {"from": float(u0[j]), "to": float(u1[j]), "price": float(o[j])} for j in touched},
                 "version": VERSION}
        curve = [[ymd(t - 1), round(e, 2)] for t, e in cur["curve"][1:]]
        months: dict[str, list[float]] = {}
        for (t, e) in cur["curve"][1:]:
            months.setdefault(ymd(t - 1)[:7], []).append(e)
        mrows, prev_e = [], 10_000.0
        for k, v in months.items():
            mrows.append([k, round((v[-1] / prev_e - 1) * 100, 2)])
            prev_e = v[-1]
        gross = sum(abs(x["w"]) for x in today["targets"].values())
        return {"today": today, "curve": curve, "months": mrows, "first_forward_day": ymd(first),
                "legs_alone": alone, "n_positions": len(today["targets"]), "gross": round(gross, 3),
                "longs": sum(1 for x in today["targets"].values() if x["units"] > 0),
                "shorts": sum(1 for x in today["targets"].values() if x["units"] < 0),
                "liquidations": cur["liquidations"], "net_pct": round(eq / 100 - 100, 2),
                "pred_src": {"research": sum(1 for v in self.store.pred_src().values() if v == "research"),
                             "server": sum(1 for v in self.store.pred_src().values() if v == "server")},
                "universe_size": int(D.N), "computed_ts": self.now(), "version": VERSION}

    # -- views ------------------------------------------------------------------------------------------------------------------
    def latest(self) -> dict[str, Any] | None:
        b = self.store.get("book")
        return b["today"] if b else None

    def public(self) -> dict[str, Any]:
        b = self.store.get("book") or {}
        return {"status": self.status, "book": {k: v for k, v in b.items() if k != "today"},
                "today": {k: b["today"][k] for k in ("date", "equity", "weights")} if b.get("today") else None,
                "positions": sorted(([s.replace("USDT", ""), round(x["w"] * 100, 2)] for s, x in
                                     (b.get("today") or {}).get("targets", {}).items()), key=lambda r: -abs(r[1])),
                "rules": "Rotation (14-day winners/losers, top 40, weekly) + ML (3 best / 3 worst, daily) + 60-day "
                         "momentum (top 50, monthly); inverse-volatility weights re-set weekly; 1x; all fees, "
                         "slippage and funding. Frozen: research/monthly20/r4_07_ensemble/PREREG.md."}

    def _alert(self, text: str) -> None:
        t = self.telegram()
        if t is not None and hasattr(t, "live_alert"):
            try:
                t.live_alert(text)
            except Exception:
                pass
