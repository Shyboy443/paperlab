"""Trading Desk service: the scheduler, the session flow, direct messages, the hourly ledger update, alerts and the
daily recap. Paper money only -- no exchange account, key or order path exists anywhere in app/desk.

Session (every `schedule_hours`, aligned to UTC multiples of it, two minutes after the candle closes):
  1 Chief: "Session started"            4 Charts: one plan (JSON, code-checked) or "no setup"
  2 Scout: top 3 movers (scan data)     5 Skeptic: objections + VERDICT
  3 News + Sentiment (in parallel)      6 Chief: the decision (code gate by mode), ledger.open(), decision log
Every message goes to the group channel (public read-only feed + the 'desk' push event). Direct messages to an agent
are private (dashboard login) and use tool calls for live data.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.desk import agents as A
from app.desk import news as N
from app.desk import sentiment as S
from app.desk.ledger import Ledger, LedgerError
from app.desk.llm import DeskLLM, LLMError
from app.desk.market import DEFAULT_UNIVERSE, Market

log = logging.getLogger("paperlab.desk")

HOUR_MS = 3_600_000
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True, "schedule_hours": 4, "universe": list(DEFAULT_UNIVERSE), "mode": "cautious", "risk_pct": 2.0,
    "start_balance": 1000.0, "notify": {"in_app": True, "telegram": True, "email": False}, "recap_hour": 21,
    "tz": "Asia/Colombo",
}
SCHEDULES = (1, 2, 4, 6, 8, 12, 24)


def validate_settings(body: Mapping[str, Any], current: Mapping[str, Any], has_trades: bool) -> dict[str, Any]:
    """A cleaned copy of `current` with the allowed changes from `body`; ValueError on a bad value."""
    out = json.loads(json.dumps(current))
    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "schedule_hours" in body:
        v = int(body["schedule_hours"])
        if v not in SCHEDULES:
            raise ValueError(f"schedule_hours must be one of {SCHEDULES}")
        out["schedule_hours"] = v
    if "universe" in body:
        u = body["universe"]
        u = re.split(r"[\s,]+", u) if isinstance(u, str) else list(u)
        u = [str(x).strip().upper().replace("USDT", "") for x in u if str(x).strip()]
        if not 1 <= len(u) <= 20 or not all(re.fullmatch(r"[A-Z0-9]{2,10}", x) for x in u):
            raise ValueError("universe: 1 to 20 tickers like BTC, ETH, SOL")
        out["universe"] = list(dict.fromkeys(u))
    if "mode" in body:
        if body["mode"] not in ("cautious", "aggressive"):
            raise ValueError("mode must be cautious or aggressive")
        out["mode"] = body["mode"]
    if "risk_pct" in body:
        v = float(body["risk_pct"])
        if not 0.25 <= v <= 5:
            raise ValueError("risk_pct must be between 0.25 and 5")
        out["risk_pct"] = v
    if "start_balance" in body:
        v = float(body["start_balance"])
        if not 100 <= v <= 1_000_000:
            raise ValueError("start_balance must be between 100 and 1,000,000")
        if has_trades and v != float(current.get("start_balance", v)):
            raise ValueError("the starting balance can only change before the first trade")
        out["start_balance"] = v
    if "notify" in body and isinstance(body["notify"], Mapping):
        n = body["notify"]
        if n.get("email"):
            raise ValueError("email alerts are not set up on this server (no mail account); use Telegram or in-app")
        out["notify"] = {"in_app": bool(n.get("in_app", True)), "telegram": bool(n.get("telegram", True)), "email": False}
    if "recap_hour" in body:
        v = int(body["recap_hour"])
        if not 0 <= v <= 23:
            raise ValueError("recap_hour must be 0-23")
        out["recap_hour"] = v
    if "tz" in body:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(str(body["tz"]))
        except Exception:
            raise ValueError("unknown time zone") from None
        out["tz"] = str(body["tz"])
    return out


class DeskService:
    def __init__(self, db_path: str, bus: Any = None, env: Mapping[str, str] | None = None,
                 social: Callable[[str], list[dict[str, Any]]] | None = None,
                 telegram: Callable[[], Any] | None = None, market: Market | None = None, llm: DeskLLM | None = None,
                 clock: Callable[[], float] = time.time):
        e = os.environ if env is None else env
        self.enabled = str(e.get("DESK_ENABLED", "true")).strip().lower() not in ("0", "false", "no", "off")
        self.clock = clock
        self.ledger = Ledger(db_path, clock=clock)
        self.market = market or Market(clock=clock)
        self.llm = llm or DeskLLM.from_env(e)
        self.bus = bus
        self.social = social
        self.telegram = telegram
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running: str | None = None              # the session id in progress
        self.last_error: str | None = None
        if self.ledger.kv("settings") is None:
            self.ledger.set_kv("settings", DEFAULT_SETTINGS)

    # -- settings -------------------------------------------------------------------------------------------------------
    @property
    def settings(self) -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **(self.ledger.kv("settings") or {})}

    def update_settings(self, body: Mapping[str, Any]) -> dict[str, Any]:
        has_trades = bool(self.ledger.trades(limit=1))
        new = validate_settings(body, self.settings, has_trades)
        self.ledger.set_kv("settings", new)
        if not has_trades:
            self.ledger.set_kv("start_balance", float(new["start_balance"]))
        return new

    # -- lifecycle ------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="trading-desk", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.ledger.close()

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def next_session_ms(self) -> int:
        step = int(self.settings["schedule_hours"]) * HOUR_MS
        now = self.now_ms()
        slot = now // step * step
        due = slot + 120_000
        last = int(self.ledger.kv("last_slot", 0) or 0)
        return due if last < slot and now < due + step else slot + step + 120_000

    def _loop(self) -> None:
        while not self._stop.wait(20):
            try:
                self._tick()
            except Exception as exc:                  # the loop never dies
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.exception("desk tick failed")

    def _tick(self) -> None:
        now = self.now_ms()
        st = self.settings
        if st["enabled"]:
            step = int(st["schedule_hours"]) * HOUR_MS
            slot = now // step * step
            if now >= slot + 120_000 and int(self.ledger.kv("last_slot", 0) or 0) < slot:
                self.ledger.set_kv("last_slot", slot)
                self.run_session("schedule")
        hour = now // HOUR_MS
        if now % HOUR_MS >= 60_000 and int(self.ledger.kv("last_update_hour", 0) or 0) < hour:
            self.ledger.set_kv("last_update_hour", hour)
            self.update_ledger()
        self._maybe_recap(now)

    # -- posting --------------------------------------------------------------------------------------------------------
    def post(self, author: str, kind: str, text: str, data: Any = None, session_id: str | None = None,
             channel: str = "group") -> dict[str, Any]:
        msg = self.ledger.add_message(channel, author, kind, text, data, session_id)
        if self.bus is not None:
            try:
                self.bus.publish("desk", {"message": msg}, public=channel == "group")
            except Exception:
                pass
        return msg

    def _alert(self, text_html: str) -> None:
        n = self.settings["notify"]
        if n.get("telegram") and self.telegram:
            tg = self.telegram()
            if tg is not None:
                try:
                    tg.alert("🧭 <b>Trading Desk</b> (paper)\n" + text_html)
                except Exception:
                    pass

    # -- one session ----------------------------------------------------------------------------------------------------
    def start_session(self, trigger: str = "manual") -> dict[str, Any]:
        """Run a session in the background; refuses if one is running."""
        if self._run_lock.locked():
            return {"ok": False, "error": "a desk session is already running"}
        threading.Thread(target=self.run_session, args=(trigger,), name="desk-session", daemon=True).start()
        return {"ok": True, "started": True}

    def run_session(self, trigger: str = "schedule") -> dict[str, Any] | None:
        if not self._run_lock.acquire(blocking=False):
            return None
        sid = time.strftime("%Y%m%d-%H%M", time.gmtime(self.clock())) + "-" + uuid.uuid4().hex[:4]
        self.running = sid
        st = self.settings
        mode = st["mode"]
        min_rr = A.MIN_RR[mode]
        row: dict[str, Any] = {"id": sid, "started_ts": self.now_ms(), "mode": mode, "status": "running", "cost_usd": 0.0}
        self.ledger.save_session(row)
        msgs: list[dict[str, Any]] = []
        cost0 = self.llm.cost_total

        def say(author: str, kind: str, text: str, data: Any = None) -> None:
            msgs.append(self.post(author, kind, text, data, sid))

        try:
            say("chief", "session", f"Session started ({trigger}, {mode} mode, min reward:risk {min_rr}). "
                f"Universe: {', '.join(st['universe'])}.")
            self.update_ledger(quiet=False)
            # 2. Scout
            scan = self.market.scan(st["universe"])
            ok = [r for r in scan if r.get("ok")]
            if not ok:
                say("chief", "decision", "No market data from any source right now; no trade this session.",
                    {"errors": {r["coin"]: r.get("reason") for r in scan}})
                row.update(status="no-data", decision="NO TRADE: no market data")
                return row
            top = ok[:3]
            focus = [r["coin"] for r in top]
            scan_data = {"top3_by_relative_volume": [A.compact_scan(r) for r in top],
                         "rest": [{"coin": r["coin"], "rel_volume_1h": round(r["h1"]["rel_volume"] or 0, 2),
                                   "change_24h_pct": round(r["change_24h_pct"] or 0, 2)} for r in ok[3:]]}
            t = self._agent("scout", scan_data, "Post the top 3 movers.")
            say("scout", "take", t, {"table": scan_data["top3_by_relative_volume"]})
            # 3. News + Sentiment in parallel
            with ThreadPoolExecutor(2) as ex:
                f_news = ex.submit(N.news, st["universe"])
                f_sent = ex.submit(S.sentiment, focus, self._social)
                feed, sent = f_news.result(), f_sent.result()
            news_data = {"coins_in_focus": focus, **N.for_coins(feed, focus)}
            sent_data = {"coins_in_focus": focus, **sent}
            with ThreadPoolExecutor(2) as ex:
                f1 = ex.submit(self._agent, "news", news_data, "Why are the coins in focus moving?")
                f2 = ex.submit(self._agent, "sentiment", sent_data, "What is the crowd doing in the coins in focus?")
                news_take, sent_take = f1.result(), f2.result()
            say("news", "take", news_take, {"headlines": sum((len(v) for v in news_data["by_coin"].values()), 0),
                                            "sources": feed.get("sources")})
            say("sentiment", "take", sent_take, {"positioning": {c: v["positioning"] for c, v in sent["coins"].items()},
                                                 "trending": sent.get("coingecko_trending")})
            # 4. Charts
            held = [t_["coin"] for t_ in self.ledger.trades(("open", "pending"))]
            chart_data = {"coins_in_focus": scan_data["top3_by_relative_volume"], "mode": mode, "min_reward_risk": min_rr,
                          "coins_already_held_cannot_trade": held,
                          "colleagues": {"scout": msgs[1]["text"], "news": news_take, "sentiment": sent_take}}
            plan_text = self._agent("charts", chart_data, "Your plan as JSON.", json_mode=True, min_rr=min_rr)
            plan = A.parse_plan(plan_text)
            if plan is None:
                plan_text = self._agent("charts", chart_data, "Your previous answer was not valid JSON. Answer with ONLY the JSON object.",
                                        json_mode=True, min_rr=min_rr)
                plan = A.parse_plan(plan_text)
            checks: dict[str, Any] = {"ok": False, "problems": [], "facts": {}}
            row_by = {r["coin"]: r for r in top}
            if plan is None:
                say("charts", "plan", "I could not produce a readable plan this session.", {"raw": plan_text[:600]})
            elif plan.coin is None:
                say("charts", "plan", "No setup. " + plan.why, {"plan": plan.raw})
            elif plan.coin not in row_by:
                checks["problems"] = [f"{plan.coin} is not one of the coins in focus"]
                say("charts", "plan", plan.why or "(plan)", {"plan": plan.raw, "checks": checks})
            else:
                if plan.coin in held:
                    checks["problems"].append(f"the desk already holds {plan.coin}")
                _, price = self.market.price(plan.coin)
                ok_, problems, facts = A.validate_plan(plan, row_by[plan.coin], price, min_rr)
                checks = {"ok": ok_ and not checks["problems"], "problems": checks["problems"] + problems,
                          "facts": facts, "price": price}
                entry_txt = "market" if plan.entry == "market" else f"limit {plan.entry:g}"
                text = (f"{plan.coin} {plan.side.upper()} · entry {entry_txt} · stop {plan.stop:g} (beyond {plan.stop_level:g}) "
                        f"· target {plan.target:g} · R:R {facts.get('rr', '?')}. {plan.why}")
                if checks["problems"]:
                    text += "\n⚠️ Desk checks failed: " + "; ".join(checks["problems"])
                say("charts", "plan", text, {"plan": plan.raw, "checks": checks})
            # 5. Skeptic
            sk_data = {"plan": plan.raw if plan else None, "code_checks": checks,
                       "coin_data": A.compact_scan(row_by[plan.coin]) if plan and plan.coin in row_by else None,
                       "news_take": news_take, "sentiment_take": sent_take,
                       "headlines": news_data["by_coin"].get(plan.coin) if plan and plan.coin else news_data["market"]}
            sk = self._agent("skeptic", sk_data, "Tear the plan apart, then give your verdict.")
            verdict, breaker = A.parse_verdict(sk)
            say("skeptic", "verdict", sk, {"verdict": verdict, "deal_breaker": breaker})
            # 6. Chief
            go, why = A.decide(mode, bool(plan and plan.coin and checks.get("ok")), verdict, breaker)
            trade = None
            if go:
                try:
                    candles = self.market.candles(plan.coin, "1h", 60)[1]
                    trade = self.ledger.open(plan.coin, plan.side, plan.entry, plan.stop, plan.target,
                                             reason=f"session {sid}: {plan.why}", session_id=sid, price=checks["price"],
                                             chart=[[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in candles])
                except (LedgerError, RuntimeError) as exc:
                    go, why = False, f"the ledger refused: {exc}"
            decision = (f"TRADE: {plan.side.upper()} {plan.coin}" if go else "NO TRADE") + f" -- {why}"
            chief_data = {"decision": decision, "colleague_messages": {m["author"]: m["text"] for m in msgs[1:]}}
            chief = self._agent("chief", chief_data, "Write the decision message.")
            say("chief", "decision", chief, {"decision": decision, "trade": trade})
            if trade:
                say("chief", "trade", f"Opened paper trade #{trade['id']}", {"trade": trade})
                self._alert(self._trade_html(trade, "opened"))
            row.update(status="done", coin=plan.coin if plan else None, verdict=verdict, decision=decision,
                       trade_id=trade["id"] if trade else None)
            return row
        except LLMError as exc:
            say("chief", "error", f"Session stopped: {exc}")
            row.update(status="error", decision=f"NO TRADE -- {exc}")
            self.last_error = str(exc)
            return row
        except Exception as exc:
            log.exception("desk session failed")
            say("chief", "error", f"Session stopped by an error ({type(exc).__name__}). No trade.")
            row.update(status="error", decision=f"NO TRADE -- {type(exc).__name__}")
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return row
        finally:
            row.update(ended_ts=self.now_ms(), cost_usd=round(self.llm.cost_total - cost0, 4),
                       log_md=A.decision_log(row, msgs))
            self.ledger.save_session(row)
            self.running = None
            self._run_lock.release()

    def _agent(self, agent: str, data: Any, ask: str, json_mode: bool = False, min_rr: float = 2.0) -> str:
        out = self.llm.chat(A.system_prompt(agent, min_rr), [{"role": "user", "content": A.data_block(data) + "\n\n" + ask}],
                            json_mode=json_mode)
        return out["text"] or "(no answer)"

    def _social(self, coin: str) -> list[dict[str, Any]]:
        try:
            return list(self.social(coin)) if self.social else []
        except Exception:
            return []

    # -- direct messages ------------------------------------------------------------------------------------------------
    def dm(self, agent: str, text: str) -> dict[str, Any]:
        if agent not in A.AGENTS:
            raise ValueError("unknown agent")
        text = (text or "").strip()
        if not text or len(text) > 2000:
            raise ValueError("message must be 1-2000 characters")
        channel = "dm:" + agent
        self.post("you", "dm", text, channel=channel)
        history = [{"role": "assistant" if m["author"] == agent else "user", "content": m["text"]}
                   for m in self.ledger.messages(channel, limit=12)]
        try:
            out = self.llm.chat(A.system_prompt(agent, dm=True), history, tools=A.TOOLS, run_tool=self.run_tool)
            reply, data = out["text"] or "(no answer)", {"tools": out["tool_calls"], "cost_usd": out["cost_usd"]}
        except LLMError as exc:
            reply, data = f"I can't answer right now: {exc}", None
        return self.post(agent, "dm", reply, data, channel=channel)

    def run_tool(self, name: str, args: dict[str, Any]) -> Any:
        coin = str(args.get("coin") or "").upper().replace("USDT", "")
        if name == "scan_coin":
            return A.compact_scan(self.market.scan_coin(coin))
        if name == "scan_universe":
            return [A.compact_scan(r) for r in self.market.scan(self.settings["universe"])[:5]]
        if name == "news":
            feed = N.news(self.settings["universe"])
            return N.for_coins(feed, [coin]) if coin else {"market": feed["items"][:10]}
        if name == "sentiment":
            return S.sentiment([coin], self._social)
        if name == "ledger":
            return self.report()
        return {"error": "unknown tool"}

    # -- ledger ---------------------------------------------------------------------------------------------------------
    def update_ledger(self, quiet: bool = False) -> list[dict[str, Any]]:
        try:
            changed = self.ledger.update(lambda coin, a, b: self.market.minutes(coin, a, b)[1])
        except Exception as exc:
            self.last_error = f"ledger update: {type(exc).__name__}: {str(exc)[:160]}"
            return []
        for t in changed:
            ev = t.get("event")
            if ev == "closed":
                self.post("chief", "trade", f"Closed paper trade #{t['id']} {t['coin']} at {t['exit']:g} ({t['exit_reason']}): "
                          f"{t['pnl']:+.2f} USD ({t['r']:+.2f}R)", {"trade": t})
                self._alert(self._trade_html(t, "closed"))
            elif ev == "filled":
                self.post("chief", "trade", f"Limit filled: #{t['id']} {t['side']} {t['coin']} at {t['entry']:g}", {"trade": t})
                self._alert(self._trade_html(t, "filled"))
            elif ev == "expired":
                self.post("chief", "trade", f"Limit order #{t['id']} {t['coin']} expired unfilled", {"trade": t})
        return changed

    def marks(self) -> dict[str, float]:
        out = {}
        for t in self.ledger.trades(("open",)):
            try:
                out[t["coin"]] = self.market.price(t["coin"])[1]
            except Exception:
                pass
        return out

    def report(self) -> dict[str, Any]:
        return self.ledger.report(self.marks())

    @staticmethod
    def _trade_html(t: Mapping[str, Any], what: str) -> str:
        e = html.escape
        line = f"#{t['id']} <b>{e(t['side'].upper())} {e(t['coin'])}</b> {what}"
        if what == "closed":
            return line + f" at {t['exit']:g} ({e(str(t['exit_reason']))}): <b>{t['pnl']:+.2f} USD</b> ({t['r']:+.2f}R)"
        entry = t.get("entry") or t.get("entry_req")
        return line + f"\nentry {entry:g} · stop {t['stop']:g} · target {t['target']:g} · risk {t['risk_usd']:.2f} USD"

    # -- daily recap ----------------------------------------------------------------------------------------------------
    def _maybe_recap(self, now_ms: int) -> None:
        from zoneinfo import ZoneInfo
        st = self.settings
        local = datetime.fromtimestamp(now_ms / 1000, timezone.utc).astimezone(ZoneInfo(st["tz"]))
        day = local.strftime("%Y-%m-%d")
        if local.hour != int(st["recap_hour"]) or self.ledger.kv("last_recap") == day:
            return
        self.ledger.set_kv("last_recap", day)
        rep = self.report()
        since = now_ms - 24 * HOUR_MS
        closed = [t for t in rep["closed"] if (t["closed_ts"] or 0) >= since]
        sessions = [s for s in self.ledger.sessions(20) if (s["started_ts"] or 0) >= since]
        text = (f"Daily recap {day}: equity {rep['equity']:.2f} USD ({rep['return_pct']:+.2f}% since start). "
                f"{len(sessions)} sessions, {sum(1 for s in sessions if (s['decision'] or '').startswith('TRADE'))} trades opened, "
                f"{len(closed)} closed today ({sum(t['pnl'] or 0 for t in closed):+.2f} USD). Open: "
                + (", ".join(f"{t['coin']} {t['side']}" for t in rep["open"]) or "none") + ".")
        self.post("chief", "recap", text, {"report": {k: rep[k] for k in ("equity", "return_pct", "win_rate", "avg_r")}})
        self._alert(html.escape(text))

    # -- reads ----------------------------------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "running": self.running, "next_session_ms": self.next_session_ms(),
                "last_error": self.last_error, "llm": self.llm.health(), "data_errors": dict(self.market.errors)}

    def public_state(self, limit: int = 150) -> dict[str, Any]:
        st = self.settings
        return {"ok": True, "paper_only": True, "agents": A.AGENTS, "messages": self.ledger.messages("group", limit),
                "sessions": self.ledger.sessions(30), "ledger": self.report(), "health": self.health(),
                "settings": {k: st[k] for k in ("enabled", "schedule_hours", "universe", "mode", "risk_pct",
                                                "start_balance", "recap_hour", "tz")} | {"notify": st["notify"]}}
