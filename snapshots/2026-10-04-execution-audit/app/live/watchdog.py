"""WATCHDOG: health of every paper program -> operator alerts (Telegram), the live mirror's SAFE MODE, and a deep health
endpoint for an external monitor (Uptime Kuma, UptimeRobot, ...).

Every CHECK_S seconds it reads each forward service's health (the same compact, public-safe dict the dashboard shows):

    problem   worker not LIVE (RESTARTING / ERROR / DEGRADED / FROZEN_MISMATCH / ENDED / NO_DATA_KEYS), still warming up
              after WARMUP_GRACE_S, market data older than STALE_S, or the websocket disconnected
    debounce  a problem must be seen on CONFIRM checks in a row before it alerts (no flapping); recovery likewise
    effects   Telegram "🔴 V8 DOWN: ..." / "🟢 V8 recovered"; the live mirror stops opening NEW positions for that program
              (SAFE MODE) until it recovers; open positions keep their exchange stop and keep being managed

Bot-level alerts (only for bots whose trades the operator receives, the Telegram filter): ELIMINATED, QUALIFIED and a
FLOOR_HALT (the book hit its drawdown floor). A worker restart and the app's own restart are announced once. A daily
digest is posted after 00:05 UTC. Nothing here changes any paper decision: it only reads.
"""
from __future__ import annotations

import html
import json
import logging
import threading
import time
from typing import Any, Callable, Mapping

log = logging.getLogger("paperlab.live.watchdog")

CHECK_S = 30.0
CONFIRM = 2                      # consecutive checks before an alert / a recovery
STALE_S = 180.0                  # market data older than this is stale
WARMUP_GRACE_S = 900.0           # a worker may warm up this long after (re)starting before it counts as a problem
ALERT_COOLDOWN_S = 1800.0        # a program that flaps alerts at most once per 30 min (SAFE MODE still follows it)
WS_REQUIRED = {"v6", "v7", "v8"}  # Bybit-websocket programs; V9 trades on Alpaca's REST fallback, V11/V12 poll REST
OK_STATUSES = {"LIVE", "MARKET_CLOSED"}
WARMING = {"STARTING", "WARMING_UP", "STARTING_BOTS"}
WATCHED_BOT_STATES = {"ELIMINATED": "❌ eliminated", "QUALIFIED": "✅ QUALIFIED", "FLOOR_HALT": "🧯 hit its drawdown floor"}


def problem_of(h: Mapping[str, Any], up_s: float, ws_required: bool = True) -> str | None:
    """One program's health -> the problem in words, or None when healthy."""
    st = str(h.get("status") or "DISABLED")
    if st in WARMING:
        return f"still {st.lower().replace('_', ' ')} after {up_s / 60:.0f} min" if up_s > WARMUP_GRACE_S else None
    if st not in OK_STATUSES:
        err = h.get("error")
        return st.lower().replace("_", " ") + (f" ({str(err)[:80]})" if err else "")
    if st == "LIVE":
        age = h.get("klines_age_s")
        if age is not None and float(age) > STALE_S:
            return f"market data stale ({float(age):.0f} s old)"
        if ws_required and h.get("ws") is False:
            return "market websocket disconnected"
    return None


def _num(v: Any) -> float | None:
    """JSON-safe number: None for missing, NaN or infinite values (a JSON response refuses NaN)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 1) if f == f and abs(f) != float("inf") else None


class Watchdog:
    def __init__(self, services: Mapping[str, Any], mirror: Any = None, notifier: Any = None,
                 names: Callable[[], Mapping[str, Any]] | None = None, clock: Callable[[], float] = time.time):
        self.services = dict(services)            # program ("v8") -> V6ForwardService
        self.mirror = mirror
        self.notifier = notifier
        self.names_fn = names
        self.clock = clock
        self.started = clock()
        self.state: dict[str, dict[str, Any]] = {}
        self.bot_states: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        self.announced_boot = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(CHECK_S):
            try:
                self.check()
            except Exception as exc:              # the watchdog must never die
                log.warning("watchdog check failed: %s: %s", type(exc).__name__, str(exc)[:160])

    def _send(self, text: str) -> None:
        if self.notifier is not None:
            self.notifier.alert(text)

    def _name(self, program: str, key: str) -> str:
        try:
            n = (self.names_fn() or {}).get(f"{program}|{key}") if self.names_fn else None
            return f"{n['name']} ({key})" if n and n.get("name") else key
        except Exception:
            return key

    # -- one pass ------------------------------------------------------------------------------------------------------
    def check(self) -> None:
        now = self.clock()
        for prog, svc in self.services.items():
            if not getattr(svc, "enabled", False):
                continue
            h = svc.health()
            up_s = now - float(getattr(svc, "started_at", None) or self.started)
            prob = problem_of(h, up_s, prog in WS_REQUIRED)
            s = self.state.setdefault(prog, {"bad": 0, "good": 0, "down": None, "restarts": h.get("restarts") or 0,
                                             "since": None, "alerted": False, "last_alert": 0.0})
            if (h.get("restarts") or 0) > s["restarts"]:
                self._send(f"♻️ <b>{prog.upper()} worker restarted</b> (restart #{h.get('restarts')}); it resumes its "
                           "experiment and re-derives the books.")
            s["restarts"] = h.get("restarts") or 0
            if prob:
                s["bad"], s["good"] = s["bad"] + 1, 0
                if s["down"] is None and s["bad"] >= CONFIRM:
                    s["down"], s["since"], s["alerted"] = prob, now, False
                if s["down"] is not None:
                    s["down"] = prob
                    lasting = now - s["since"] >= ALERT_COOLDOWN_S
                    if not s["alerted"] and (now - s["last_alert"] >= ALERT_COOLDOWN_S or lasting):
                        s["alerted"], s["last_alert"] = True, now
                        self._send(f"🔴 <b>{prog.upper()} DOWN</b>: {html.escape(prob)}. No new live entries while it "
                                   "lasts; open positions keep their stops.")
            else:
                s["good"], s["bad"] = s["good"] + 1, 0
                if s["down"] is not None and s["good"] >= CONFIRM:
                    mins = (now - (s["since"] or now)) / 60
                    if s["alerted"]:
                        self._send(f"🟢 <b>{prog.upper()} recovered</b> after {mins:.0f} min ({html.escape(s['down'])}).")
                    s["down"], s["since"], s["alerted"] = None, None, False
            if self.mirror is not None:
                self.mirror.set_program_safe(prog, s["down"])
            self._bots(prog, svc)
        self._boot_notice(now)
        self._digest(now)

    def _bots(self, prog: str, svc: Any) -> None:
        for b in (getattr(svc, "status", None) or {}).get("bots") or []:
            key = str(b.get("key") or "")
            cur = (b.get("program_status"), b.get("risk_state"))
            prev = self.bot_states.get((prog, key))
            self.bot_states[(prog, key)] = cur
            if prev is None or self.notifier is None or not self.notifier.wants(prog, str(b.get("role") or "CONTROL")):
                continue                          # first sight (boot) never alerts
            for new, old in ((cur[0], prev[0]), (cur[1], prev[1])):
                if new != old and new in WATCHED_BOT_STATES:
                    self._send(f"{WATCHED_BOT_STATES[new].split(' ', 1)[0]} <b>{html.escape(self._name(prog, key))}</b> "
                               f"({prog.upper()}) {WATCHED_BOT_STATES[new].split(' ', 1)[1]} · net "
                               f"{float(b.get('net') or 0):+.2f} after {int(b.get('trades') or 0)} trades")

    def _boot_notice(self, now: float) -> None:
        if self.announced_boot:
            return
        states = {p: str(s.health().get("status")) for p, s in self.services.items() if getattr(s, "enabled", False)}
        settled = all(v in OK_STATUSES for v in states.values())
        if settled or now - self.started > WARMUP_GRACE_S:
            self.announced_boot = True
            self._send("♻️ <b>PaperLab restarted</b> (a deploy or a crash). " +
                       " · ".join(f"{p.upper()} {v}" for p, v in states.items()))

    def _digest(self, now: float) -> None:
        if self.notifier is None:
            return
        t = time.gmtime(now)
        day = time.strftime("%Y-%m-%d", t)
        if t.tm_hour == 0 and t.tm_min < 5:
            return
        if self.notifier.kv("digest_day") == day:
            return
        prev = json.loads(self.notifier.kv("digest_snapshot") or "{}")
        snap, lines = {}, [f"📊 <b>Daily summary</b> {day} (paper; change since the last summary)"]
        for prog, svc in self.services.items():
            bots = [b for b in (getattr(svc, "status", None) or {}).get("bots") or []
                    if self.notifier.wants(prog, str(b.get("role") or "CONTROL"))]
            if not bots:
                continue
            net = sum(float(b.get("net") or 0) for b in bots)
            trades = sum(int(b.get("trades") or 0) for b in bots)
            p = prev.get(prog) or {}
            snap[prog] = {"net": net, "trades": trades}
            d_net = net - float(p.get("net", net)) if p else None
            d_tr = trades - int(p.get("trades", trades)) if p else None
            best = sorted(bots, key=lambda b: float(b.get("net") or 0), reverse=True)
            lines.append(f"<b>{prog.upper()}</b> {len(bots)} bots · net {net:+.2f}"
                         + (f" ({d_net:+.2f} today, {d_tr} trades)" if d_net is not None else f" · {trades} trades")
                         + f" · best {html.escape(self._name(prog, str(best[0].get('key'))))} {float(best[0].get('net') or 0):+.2f}"
                         + f" · worst {html.escape(self._name(prog, str(best[-1].get('key'))))} {float(best[-1].get('net') or 0):+.2f}")
        if self.mirror is not None:
            mh = self.mirror.health()
            lines.append(f"Live mirrors: {mh['armed']} armed, {mh['with_position']} with a position"
                         + (f" · SAFE MODE: {html.escape(', '.join(mh['safe_mode']))}" if mh["safe_mode"] else ""))
        self.notifier.kv("digest_day", day)
        self.notifier.kv("digest_snapshot", json.dumps(snap))
        if prev:                                  # the very first run only takes the snapshot
            self._send("\n".join(lines))

    # -- deep health for an external monitor ----------------------------------------------------------------------------
    def deep_health(self) -> dict[str, Any]:
        """Public-safe: statuses and problems only (no keys, no ids, no balances)."""
        progs = {}
        for prog, svc in self.services.items():
            if not getattr(svc, "enabled", False):
                continue
            h = svc.health()
            s = self.state.get(prog) or {}
            progs[prog] = {"status": str(h.get("status")), "problem": s.get("down"), "restarts": _num(h.get("restarts")),
                           "klines_age_s": _num(h.get("klines_age_s"))}
        mh = self.mirror.health() if self.mirror is not None else {"armed": 0, "safe_mode": {}}
        tg = self.notifier.health() if self.notifier is not None else {}
        down = [p for p, v in progs.items() if v["problem"]]
        return {"ok": not down and not mh.get("safe_mode"), "down": down, "programs": progs,
                "live_mirrors": {"armed": mh.get("armed", 0), "safe_mode": list((mh.get("safe_mode") or {}).keys())},
                "telegram": {"linked": bool(tg.get("linked")), "errors": int(tg.get("errors") or 0)},
                "checked_s_ago": None if self.state else round(self.clock() - self.started, 0)}
