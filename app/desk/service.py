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
from app.desk.claude import make_llm
from app.desk.llm import DeskLLM, LLMError
from app.desk.market import DEFAULT_UNIVERSE, Market

log = logging.getLogger("paperlab.desk")

HOUR_MS = 3_600_000
UPDATE_MS = 300_000
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True, "schedule_hours": 4, "universe": list(DEFAULT_UNIVERSE), "mode": "cautious", "risk_pct": 2.0,
    "start_balance": 1000.0, "notify": {"in_app": True, "telegram": True, "email": False}, "recap_hour": 21,
    "tz": "Asia/Colombo", "max_plans": 3, "max_open": 3, "max_total_risk_pct": 6.0, "max_same_side": 3,
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
    if "max_plans" in body:
        v = int(body["max_plans"])
        if not 1 <= v <= 3:
            raise ValueError("max_plans must be 1-3")
        out["max_plans"] = v
    if "max_open" in body:
        v = int(body["max_open"])
        if not 1 <= v <= 6:
            raise ValueError("max_open must be 1-6")
        out["max_open"] = v
    if "max_total_risk_pct" in body:
        v = float(body["max_total_risk_pct"])
        if not 1 <= v <= 20:
            raise ValueError("max_total_risk_pct must be 1-20")
        out["max_total_risk_pct"] = v
    if "max_same_side" in body:
        v = int(body["max_same_side"])
        if not 1 <= v <= 6:
            raise ValueError("max_same_side must be 1-6")
        out["max_same_side"] = v
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
        self.llm = llm or make_llm(e)
        self.bus = bus
        self.social = social
        self.telegram = telegram
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running: str | None = None              # the session id in progress
        self.last_error: str | None = None
        self._news: tuple[float, dict[str, Any]] | None = None
        if hasattr(self.llm, "attach_store"):          # the daily AI cap survives restarts and deploys
            self.llm.attach_store(self._usage, lambda v: self.ledger.set_kv("llm_usage", v))
        if self.ledger.kv("settings") is None:
            self.ledger.set_kv("settings", DEFAULT_SETTINGS)

    def _usage(self) -> dict[str, Any]:
        """Stored AI usage, never below what the session history shows (sessions log their own cost)."""
        u = dict(self.ledger.kv("llm_usage") or {})
        day = int(self.clock() // 86400)
        rows = self.ledger.sessions(5000)
        total = sum(r.get("cost_usd") or 0.0 for r in rows)
        today = [r for r in rows if (r.get("started_ts") or 0) // 86_400_000 == day]
        if int(u.get("day", -1)) != day:
            u = {"day": day, "calls_today": 0, "cost_today": 0.0, "cost_total": float(u.get("cost_total", 0.0))}
        u["cost_total"] = max(float(u.get("cost_total", 0.0)), total)
        u["cost_today"] = max(float(u.get("cost_today", 0.0)), sum(r.get("cost_usd") or 0.0 for r in today))
        u["calls_today"] = max(int(u.get("calls_today", 0)), 7 * len(today))
        return u

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
        bucket = now // UPDATE_MS                       # stops / targets / limit fills checked every 5 minutes
        if now % UPDATE_MS >= 30_000 and int(self.ledger.kv("last_update_bucket", 0) or 0) < bucket:
            self.ledger.set_kv("last_update_bucket", bucket)
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
            # 3. News + Sentiment in parallel; both hear the Scout, and News reads the articles, not just headlines
            scout_take = t
            with ThreadPoolExecutor(2) as ex:
                f_news = ex.submit(self.news_feed)
                f_sent = ex.submit(S.sentiment, focus, self._social)
                feed, sent = f_news.result(), f_sent.result()
            moves = {r["coin"]: {"price": A.compact_scan(r)["price"], "change_24h_pct": round(r["change_24h_pct"] or 0, 2),
                                 "rel_volume_1h": round(r["h1"]["rel_volume"] or 0, 2)} for r in top}
            picked = N.for_coins(feed, focus, per_coin=4, general=3)
            read = N.read_articles([i for c in focus for i in picked["by_coin"][c][:3]] + picked["market"][:2], limit=11)
            arts = {i["url"]: i.get("article") or "" for i in read}

            def enrich(items: list) -> list:
                return [{**i, "article": arts.get(i["url"], "")} for i in items]
            news_data = {"coins_in_focus": focus, "moves": moves, "scout_said": scout_take,
                         "by_coin": {c: enrich(v) for c, v in picked["by_coin"].items()}, "market": enrich(picked["market"])}
            sent_data = {"coins_in_focus": focus, "scout_said": scout_take, **sent}
            with ThreadPoolExecutor(2) as ex:
                f1 = ex.submit(self._agent, "news", news_data,
                               "Why are the coins in focus moving? Read the articles, and react to what Scout said.")
                f2 = ex.submit(self._agent, "sentiment", sent_data,
                               "What is the crowd doing in the coins in focus? React to what Scout said.")
                news_take, sent_take = f1.result(), f2.result()
            say("news", "take", news_take, {"headlines": sum((len(v) for v in news_data["by_coin"].values()), 0),
                                            "articles_read": sum(1 for v in arts.values() if v), "sources": feed.get("sources")})
            say("sentiment", "take", sent_take, {"positioning": {c: v["positioning"] for c, v in sent["coins"].items()},
                                                 "trending": sent.get("coingecko_trending")})
            # 4. Charts: up to max_plans setups on ANY coins of the universe, each tested with the desk's checks
            held = [t_["coin"] for t_ in self.ledger.trades(("open", "pending"))]
            free = max(0, int(st["max_open"]) - len(held))
            max_plans = max(1, min(int(st["max_plans"]), free)) if free else 1
            row_by = {r["coin"]: r for r in ok}
            chart_data = {"coins_in_focus": scan_data["top3_by_relative_volume"],
                          "other_coins": [A.compact_scan(r) for r in ok[3:]], "mode": mode, "min_reward_risk": min_rr,
                          "max_plans": max_plans, "coins_already_held_cannot_trade": held,
                          "open_trade_slots_left": free,
                          "colleagues": {"scout": scout_take, "news": news_take, "sentiment": sent_take}}
            plan_text = self._charts(chart_data, min_rr, f"Find up to {max_plans} plans on different coins. Test each with "
                                     "check_plan (adjust and re-test if it fails), then answer with ONLY the JSON object.",
                                     max_plans=max_plans)
            plans = A.parse_plans(plan_text)
            if plans is None:                          # Charts stopped mid-way: one last turn for just the JSON
                plan_text = self._agent("charts", {**chart_data, "your_unfinished_answer": plan_text[:1500]},
                                        'Finish now: reply with ONLY the final {"plans": [...]} JSON (plans you already '
                                        'checked, or an empty list with "why").', json_mode=True, min_rr=min_rr,
                                        max_plans=max_plans)
                plans = A.parse_plans(plan_text)
            checks = {pl.coin: self._check(pl, row_by, held, min_rr) for pl in plans or []}
            unread = A.unreadable_plans(plan_text)
            failing = {c: ch["problems"] for c, ch in checks.items() if not ch.get("ok")}
            if failing or unread:                      # one fix-and-retest turn before the Skeptic sees anything
                fix = self._charts({**chart_data, "your_plans": [pl.raw for pl in plans or []], "failed_checks": failing,
                                    "unreadable_plan_entries": unread}, min_rr,
                                   "Some plans failed the desk checks or were missing fields (every plan needs coin, side, "
                                   "entry, stop, stop_level and target). Fix each one and re-test it with check_plan now, "
                                   'or drop it. Reply with ONLY the final {"plans": [...]} JSON.', max_plans=max_plans)
                fixed = A.parse_plans(fix)
                if fixed is not None:
                    plans, plan_text = fixed, fix
                    checks = {pl.coin: self._check(pl, row_by, held, min_rr) for pl in plans}
            say("charts", "plan", self._plans_text(plans, checks, plan_text),
                {"plans": [pl.raw for pl in plans or []], "checks": checks})

            # 5. Skeptic -> Charts answers -> Skeptic's final verdict per plan
            def coin_data(pls: Any) -> Any:
                got = [A.compact_scan(row_by[pl.coin]) for pl in pls or [] if pl.coin in row_by]
                return got or scan_data["top3_by_relative_volume"]
            sk_data = {"plans": [pl.raw for pl in plans or []], "code_checks": checks, "coin_data": coin_data(plans),
                       "news_take": news_take, "sentiment_take": sent_take,
                       "headlines": {pl.coin: news_data["by_coin"].get(pl.coin) for pl in plans or []
                                     if pl.coin in news_data["by_coin"]} or news_data["market"]}
            sk = self._agent("skeptic", sk_data, "Tear each of Charts' plans apart (address Charts by name), then give one "
                             "verdict line per plan.")
            verdicts = self._need_verdicts(sk, sk_data, [pl.coin for pl in plans or []])
            say("skeptic", "verdict", sk, {"verdicts": verdicts})
            if plans:
                reply = self._charts({**chart_data, "your_plans": [pl.raw for pl in plans], "code_checks": checks,
                                      "skeptic_said": sk}, min_rr,
                                     "Skeptic answered. Reply to Skeptic by name: concede, defend or revise. Re-test any "
                                     'change with check_plan. End with the final {"plans": [...]} JSON.',
                                     role="charts_reply", max_plans=max_plans)
                revised = A.parse_plans(reply)
                if revised is not None:
                    plans = revised
                    checks = {pl.coin: self._check(pl, row_by, held, min_rr) for pl in plans}
                said = re.sub(r"\{.*\}", "", reply, flags=re.S).strip() or "(plans below)"
                said += ("\nFinal plans:\n" + self._plans_text(plans, checks, "")) if plans else "\nAll plans withdrawn."
                say("charts", "reply", said, {"plans": [pl.raw for pl in plans], "checks": checks})
                if plans:
                    sk2 = self._agent("skeptic", {"plans_now": [pl.raw for pl in plans], "code_checks": checks,
                                                  "coin_data": coin_data(plans), "your_first_take": sk, "charts_reply": said},
                                      "Charts answered you. Give your FINAL verdict on each plan as it stands now: a "
                                      "sentence or two to Charts, then one VERDICT line per plan.")
                    verdicts = self._need_verdicts(sk2, {"plans_now": [pl.raw for pl in plans], "your_final_take": sk2},
                                                   [pl.coin for pl in plans])      # missing -> no verdict -> no trade
                    say("skeptic", "verdict", sk2, {"verdicts": verdicts, "final": True})
            # 6. Chief: a trade per plan that passes the gate, within the ledger's limits
            trades, lines = [], []
            for pl in plans or []:
                v, b = verdicts.get(pl.coin, (None, None))
                go, why = A.decide(mode, bool(checks.get(pl.coin, {}).get("ok")), v, b)
                if go:
                    try:
                        candles = self.market.candles(pl.coin, "1h", 60)[1]
                        trades.append(self.ledger.open(pl.coin, pl.side, pl.entry, pl.stop, pl.target,
                                                       reason=f"session {sid}: {pl.why or self._why(pl, msgs)}",
                                                       session_id=sid, price=self.market.price(pl.coin, fresh=True)[1],
                                                       chart=[[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in candles]))
                    except (LedgerError, RuntimeError) as exc:
                        go, why = False, f"the ledger refused: {exc}"
                lines.append((f"TRADE: {pl.side.upper()} {pl.coin}" if go else f"NO TRADE {pl.coin}") + f" -- {why}")
            decision = "; ".join(lines) if lines else "NO TRADE -- no valid plan"
            chief_data = {"decision": decision, "trades_opened": len(trades),
                          "colleague_messages": {f"{m['author']}#{i}": m["text"] for i, m in enumerate(msgs[1:])}}
            chief = self._agent("chief", chief_data, "Write the decision message.")
            say("chief", "decision", chief, {"decision": decision, "trades": trades})
            for trade in trades:
                say("chief", "trade", f"Opened paper trade #{trade['id']}", {"trade": trade})
                self._alert(self._trade_html(trade, "opened"))
            row.update(status="done", coin=",".join(t_["coin"] for t_ in trades) or (",".join(pl.coin for pl in plans or []) or None),
                       verdict=",".join(f"{c}:{v[0] or 'NONE'}" for c, v in verdicts.items())
                       or f"ALL:{A.parse_verdict(sk)[0] or 'NONE'}", decision=decision,
                       trade_id=trades[0]["id"] if trades else None)
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

    def _agent(self, agent: str, data: Any, ask: str, json_mode: bool = False, min_rr: float = 2.0,
               max_plans: int = 1) -> str:
        out = self.llm.chat(A.system_prompt(agent, min_rr, max_plans=max_plans),
                            [{"role": "user", "content": A.data_block(data) + "\n\n" + ask}], json_mode=json_mode,
                            deep=agent in ("charts", "charts_reply", "skeptic"))
        return out["text"] or "(no answer)"

    def _charts(self, data: Any, min_rr: float, ask: str, role: str = "charts", max_plans: int = 1) -> str:
        """Charts with its two session tools (scan_coin, check_plan), so it tests every plan before posting it."""
        tools = [t for t in A.TOOLS + A.ACTION_TOOLS if t["function"]["name"] in ("scan_coin", "check_plan")]
        out = self.llm.chat(A.system_prompt(role, min_rr, max_plans=max_plans),
                            [{"role": "user", "content": A.data_block(data) + "\n\n" + ask}],
                            tools=tools, run_tool=lambda n, a: self.run_tool(n, a, "charts"), max_rounds=8, deep=True)
        return out["text"] or ""

    def _check(self, plan: Any, row_by: dict[str, Any], held: list[str], min_rr: float) -> dict[str, Any]:
        if plan is None or plan.coin is None:
            return {"ok": False, "problems": [], "facts": {}}
        if plan.coin not in row_by:
            return {"ok": False, "problems": [f"{plan.coin} is not in the desk's universe scan"], "facts": {}}
        problems = [f"the desk already holds {plan.coin}"] if plan.coin in held else []
        _, price = self.market.price(plan.coin)
        ok_, more, facts = A.validate_plan(plan, row_by[plan.coin], price, min_rr)
        return {"ok": ok_ and not problems, "problems": problems + more, "facts": facts, "price": price}

    @staticmethod
    def _plan_text(plan: Any, checks: dict[str, Any], raw: str) -> str:
        if plan is None:
            return "I could not produce a readable plan this session." + (f" ({raw[:200]})" if raw else "")
        if plan.coin is None:
            return "No setup. " + plan.why
        entry_txt = "market" if plan.entry == "market" else f"limit {plan.entry:g}"
        text = (f"{plan.coin} {plan.side.upper()} · entry {entry_txt} · stop {plan.stop:g} (beyond {plan.stop_level:g}) "
                f"· target {plan.target:g} · R:R {checks.get('facts', {}).get('rr', '?')}. {plan.why}")
        if checks.get("problems"):
            text += "\n⚠️ Desk checks failed: " + "; ".join(checks["problems"])
        return text

    def _need_verdicts(self, text: str, data: Any, coins: list[str]) -> dict[str, tuple[str | None, str | None]]:
        """Per-coin verdicts; if any coin has none, the Skeptic is asked once for just the verdict lines. A coin still
        without a verdict stays None, which the gate treats as no trade."""
        got = A.parse_verdicts(text, coins)
        missing = [c for c, (v, _) in got.items() if v is None]
        if missing:
            again = self._agent("skeptic", {**(data if isinstance(data, dict) else {"data": data}), "your_message": text},
                                "You did not give a verdict line for: " + ", ".join(missing) + ". Reply with ONLY the "
                                "lines VERDICT <COIN>: BUY|WAIT|SKIP (and DEAL-BREAKER <COIN>: ... where it applies).")
            more = A.parse_verdicts(again, missing)
            got.update({c: more[c] for c in missing if more[c][0]})
        return got

    @staticmethod
    def _why(plan: Any, msgs: list[dict[str, Any]]) -> str:
        for m in reversed(msgs):
            if m["author"] == "charts" and plan.coin in m["text"]:
                return m["text"].split("\n")[0][:300]
        return "Charts' plan"

    def _plans_text(self, plans: Any, checks: dict[str, Any], raw: str) -> str:
        if plans is None:
            return "I could not produce a readable plan this session." + (f" ({raw[:200]})" if raw else "")
        if not plans:
            why = ""
            try:
                why = str((json.loads(re.search(r"\{.*\}", raw, re.S).group(0)) or {}).get("why") or "")
            except Exception:
                pass
            return "No setup. " + why
        return "\n".join(f"{i}. " + self._plan_text(pl, checks.get(pl.coin, {}), "") for i, pl in enumerate(plans, 1))

    def news_feed(self) -> dict[str, Any]:
        """The RSS feed, cached for 10 minutes (sessions and chat tools share it)."""
        now = self.clock()
        if self._news and now - self._news[0] < 600:
            return self._news[1]
        feed = N.news(self.settings["universe"])
        self._news = (now, feed)
        return feed

    def _social(self, coin: str) -> list[dict[str, Any]]:
        try:
            return list(self.social(coin)) if self.social else []
        except Exception:
            return []

    # -- talking to the agents -----------------------------------------------------------------------------------------
    def _history(self, channel: str, agent: str, limit: int = 16) -> list[dict[str, Any]]:
        """The channel's recent messages as a conversation from `agent`'s point of view."""
        out = []
        for m in self.ledger.messages(channel, limit=limit):
            if m["author"] == agent:
                out.append({"role": "assistant", "content": m["text"]})
            else:
                who = "Operator" if m["author"] == "you" else A.AGENTS.get(m["author"], {}).get("name", m["author"])
                out.append({"role": "user", "content": f"{who}: {m['text']}"})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def _reply(self, agent: str, channel: str, history: list[dict[str, Any]] | None = None,
               colleagues: bool = True) -> tuple[str, Any]:
        tools = A.TOOLS + A.ACTION_TOOLS + (A.COLLAB_TOOLS if colleagues else [])
        try:
            out = self.llm.chat(A.system_prompt(agent, dm=True), history or self._history(channel, agent), tools=tools,
                                run_tool=lambda name, args: {"ok": False, "error": "answer yourself; no further hand-offs"}
                                if name == "ask_agent" and not colleagues else self.run_tool(name, args, agent, channel),
                                max_rounds=5, deep=True)
            return out["text"] or "(no answer)", {"tools": [c["name"] for c in out["tool_calls"]], "cost_usd": out["cost_usd"]}
        except LLMError as exc:
            return f"I can't answer right now: {exc}", None
        except Exception as exc:                       # the operator always gets an answer, never a silent 500
            log.exception("desk reply failed")
            return f"I hit an error while working on that ({type(exc).__name__}). Nothing was changed after the error.", None

    @staticmethod
    def _clean(text: str) -> str:
        text = (text or "").strip()
        if not text or len(text) > 2000:
            raise ValueError("message must be 1-2000 characters")
        return text

    def chat(self, text: str, agent: str | None = None) -> dict[str, Any]:
        """The operator writes in the GROUP chat; the @mentioned agent (or the Chief) answers there and can act."""
        text = self._clean(text)
        agent = agent if agent in A.AGENTS else (A.mention(text) or "chief")
        self.post("you", "chat", text)
        reply, data = self._reply(agent, "group")
        return self.post(agent, "chat", reply, data)

    def dm(self, agent: str, text: str) -> dict[str, Any]:
        if agent not in A.AGENTS:
            raise ValueError("unknown agent")
        text = self._clean(text)
        channel = "dm:" + agent
        self.post("you", "dm", text, channel=channel)
        reply, data = self._reply(agent, channel)
        return self.post(agent, "dm", reply, data, channel=channel)

    def run_tool(self, name: str, args: dict[str, Any], agent: str = "chief", channel: str = "group") -> Any:
        coin = str(args.get("coin") or "").upper().replace("USDT", "")
        if name == "ask_agent":
            other = str(args.get("agent") or "").lower()
            question = str(args.get("question") or "").strip()[:1500]
            if other not in A.AGENTS or other == agent or not question:
                return {"ok": False, "error": "ask a different desk agent a non-empty question"}
            asker = A.AGENTS[agent]["name"]
            history = self._history(channel, other, limit=10) + [{"role": "user", "content": f"{asker} asks you: {question}"}]
            text, data = self._reply(other, channel, history=history, colleagues=False)
            self.post(other, "chat", f"@{asker} {text}", data, channel=channel)
            return {"ok": True, "agent": A.AGENTS[other]["name"], "answer": text}
        if name == "scan_coin":
            return A.compact_scan(self.market.scan_coin(coin))
        if name == "scan_universe":
            return [A.compact_scan(r) for r in self.market.scan(self.settings["universe"])[:5]]
        if name == "news":
            feed = self.news_feed()
            if not coin:
                return {"market": N.read_articles(feed["items"][:6], limit=6)}
            got = N.for_coins(feed, [coin], per_coin=5)
            return {"by_coin": {coin: N.read_articles(got["by_coin"][coin], limit=5)}}
        if name == "sentiment":
            return S.sentiment([coin], self._social)
        if name == "ledger":
            return self.report()
        if name == "check_plan":
            plan = A.parse_plan(json.dumps(args))
            if plan is None or plan.coin is None:
                return {"ok": False, "problems": ["unreadable plan"]}
            _, price = self.market.price(plan.coin)
            ok, problems, facts = A.validate_plan(plan, self.market.scan_coin(plan.coin), price,
                                                  A.MIN_RR[self.settings["mode"]])
            return {"ok": ok, "problems": problems, "facts": facts, "price": price}
        if name == "run_session":
            return self.start_session(f"asked via {A.AGENTS[agent]['name']}")
        if name == "open_trade":
            _, price = self.market.price(coin)
            entry = args.get("entry")
            entry = "market" if entry is None or str(entry).strip().lower() == "market" else float(entry)
            candles = self.market.candles(coin, "1h", 60)[1]
            try:
                t = self.ledger.open(coin, str(args.get("side") or "").lower(), entry, float(args["stop"]), float(args["target"]),
                                     reason=f"operator request via {A.AGENTS[agent]['name']}: {args.get('reason') or ''}",
                                     price=price, chart=[[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in candles])
            except (LedgerError, KeyError, TypeError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}
            self.post(agent, "trade", f"Opened paper trade #{t['id']} on the operator's request", {"trade": t})
            self._alert(self._trade_html(t, "opened"))
            return {"ok": True, "trade": t}
        if name == "close_trade":
            t = self.ledger.trade(int(args.get("trade_id") or 0))
            if t is None:
                return {"ok": False, "error": "no such trade"}
            try:
                price = self.market.price(t["coin"])[1] if t["status"] == "open" else 0.0
                done = self.ledger.close_now(t["id"], price, "manual (operator)")
            except (LedgerError, RuntimeError) as exc:
                return {"ok": False, "error": str(exc)}
            what = "Cancelled" if done["status"] == "cancelled" else f"Closed at {done['exit']:g}: {done['pnl']:+.2f} USD"
            self.post(agent, "trade", f"{what} -- paper trade #{t['id']} {t['coin']} on the operator's request", {"trade": done})
            if done["status"] == "closed":
                self._alert(self._trade_html(done, "closed"))
            return {"ok": True, "trade": done}
        if name == "update_settings":
            body = {k: args[k] for k in ("mode", "schedule_hours", "universe", "risk_pct", "enabled", "max_plans",
                                         "max_open") if k in args}
            try:
                new = self.update_settings(body)
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
            self.post(agent, "chat", "Settings changed on the operator's request: " + ", ".join(f"{k} = {new[k]}" for k in body))
            return {"ok": True, "settings": {k: new[k] for k in ("mode", "schedule_hours", "universe", "risk_pct", "enabled",
                                                                 "max_plans", "max_open")}}
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

    def marks(self) -> dict[str, dict[str, Any]]:
        """Live price, its source and when it was taken, for every coin with an open trade."""
        out = {}
        for t in self.ledger.trades(("open",)):
            try:
                tk = self.market.ticker(t["coin"])
                out[t["coin"]] = {"price": tk["price"], "source": tk["source"], "ts": tk["ts"]}
            except Exception:
                pass
        return out

    def report(self) -> dict[str, Any]:
        info = self.marks()
        rep = self.ledger.report({c: v["price"] for c, v in info.items()})
        for row in rep["open"]:
            m = info.get(row["coin"])
            if m:
                row["mark_source"], row["mark_ts"] = m["source"], m["ts"]
        rep["checked_every_s"] = UPDATE_MS // 1000
        return rep

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

    @staticmethod
    def _lean(obj: Any) -> Any:
        """Drop the stored candle snapshots: the page loads a trade's chart only when it is opened."""
        if isinstance(obj, dict):
            return {k: DeskService._lean(v) for k, v in obj.items() if k != "chart"}
        if isinstance(obj, list):
            return [DeskService._lean(v) for v in obj]
        return obj

    def public_state(self, limit: int = 150) -> dict[str, Any]:
        st = self.settings
        return {"ok": True, "paper_only": True, "agents": A.AGENTS, "messages": self._lean(self.ledger.messages("group", limit)),
                "sessions": self.ledger.sessions(30), "ledger": self._lean(self.report()), "health": self.health(),
                "settings": {k: st[k] for k in ("enabled", "schedule_hours", "universe", "mode", "risk_pct",
                                                "start_balance", "recap_hour", "tz", "max_plans", "max_open",
                                                "max_total_risk_pct", "max_same_side")}
                | {"notify": st["notify"]}}
