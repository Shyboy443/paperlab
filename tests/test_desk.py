"""Trading Desk: indicators, position sizing, the 1m stop/target replay, the plan checks and the trade gate, settings,
the key's secrecy, read-only public routes, and one full session with a fake LLM and a fake market. No network."""
from __future__ import annotations

import json
import math

import pytest

from app.desk import agents as A
from app.desk import indicators as ind
from app.desk.ledger import Ledger, LedgerError, size_position, walk
from app.desk.llm import DeskLLM, LLMError
from app.desk.service import DeskService, validate_settings, DEFAULT_SETTINGS

H = 3_600_000


def c(t, o, h, l, cl, v=1.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": cl, "v": v}


# -- indicators ---------------------------------------------------------------------------------------------------------
def test_rsi_extremes_and_known_value():
    assert ind.rsi([float(i) for i in range(30)]) == 100.0
    assert ind.rsi([float(30 - i) for i in range(30)]) == 0.0
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert ind.rsi(closes) == pytest.approx(70.46, abs=0.01)        # Wilder: avg gain 3.34/14, avg loss 1.40/14
    assert ind.rsi(closes[:10]) is None


def test_ema_atr_relvol_swings_round_numbers():
    assert ind.ema([1, 1, 1], 3) == [1, 1, 1]
    candles = [c(i, 10, 11, 9, 10) for i in range(20)]
    assert ind.atr(candles, 14) == pytest.approx(2.0)
    vols = [c(i, 1, 1, 1, 1, 10) for i in range(20)] + [c(20, 1, 1, 1, 1, 30)]
    assert ind.relative_volume(vols, 20) == pytest.approx(3.0)
    zig = [c(i, 0, 10 + (5 if i == 5 else 0), 5 - (3 if i == 10 else 0), 0) for i in range(16)]
    highs, lows = ind.swings(zig)
    assert 15 in highs and 2 in lows
    assert ind.round_step(80954) == 1000 and ind.round_step(2.41) == pytest.approx(0.1)
    assert ind.round_numbers(80954) == {"above": [81000, 82000], "below": [80000, 79000]}
    assert ind.near_round(80000.0, 80954) and not ind.near_round(80412.0, 80954)


# -- sizing and replay --------------------------------------------------------------------------------------------------
def test_sizing_caps_risk_including_fees_and_fits_three_trades():
    s = size_position(1000, 100.0, 98.0, 2.0, 0.1, 3)
    loss = s.qty * (100 - 98) + s.qty * 100 * 0.001 + s.qty * 98 * 0.001
    assert loss <= 20.0 + 1e-9                                      # 2% of 1000, fees included
    tight = size_position(1000, 100.0, 99.9, 2.0, 0.1, 3)            # a tight stop wants a huge size ...
    assert tight.capped and tight.notional * 1.001 <= 1000 / 3 + 1e-9  # ... but the notional fits 1/3 of equity


def test_walk_stop_wins_a_tie_and_gaps_fill_at_the_open():
    both = [c(0, 100, 106, 97, 100)]
    assert walk("long", 100, 98, 105, both) == (0, 98, "stop")
    assert walk("long", 100, 98, 105, [c(0, 100, 101, 99, 100), c(1, 106, 107, 105, 106)])[2] == "target"
    assert walk("long", 100, 98, 105, [c(0, 95, 96, 94, 95)]) == (0, 95, "stop")          # gap through the stop
    assert walk("short", 100, 102, 95, [c(0, 100, 103, 94, 100)]) == (0, 102, "stop")
    assert walk("short", 100, 102, 95, [c(0, 100, 101, 94.5, 95)]) == (0, 95, "target")
    assert walk("long", 100, 98, 105, [c(0, 100, 101, 99, 100)]) is None


@pytest.fixture
def ledger(tmp_path):
    clock = {"t": 1_800_000_000.0}
    lg = Ledger(str(tmp_path / "desk.db"), clock=lambda: clock["t"])
    lg.clock_ref = clock
    yield lg
    lg.close()


def test_open_validates_sides_one_per_coin_and_max_three(ledger):
    with pytest.raises(LedgerError):
        ledger.open("SOL", "long", "market", 105, 110, price=100)
    with pytest.raises(LedgerError):
        ledger.open("SOL", "short", "market", 98, 90, price=100)
    t = ledger.open("SOL", "long", "market", 98, 105, price=100)
    assert t["status"] == "open" and t["risk_usd"] <= 20.0 + 1e-9 and t["fees"] > 0
    with pytest.raises(LedgerError):
        ledger.open("SOL", "long", "market", 98, 105, price=100)        # one per coin
    ledger.open("ETH", "short", 2600.0, 2650, 2500)                     # limit: pending
    ledger.open("BTC", "long", "market", 79_000, 84_000, price=80_000)
    with pytest.raises(LedgerError):
        ledger.open("XRP", "long", "market", 2.0, 2.6, price=2.2)       # max 3


def test_update_closes_on_stop_and_books_fees_and_r(ledger):
    t0 = ledger.now_ms()
    t = ledger.open("SOL", "long", "market", 98, 105, price=100)
    ledger.clock_ref["t"] += 3600
    bars = [c(t0 + 60_000 * i, 100, 100.5, 99.5, 100) for i in range(5)] + [c(t0 + 300_000, 100, 106, 97, 99)]
    changed = ledger.update(lambda coin, a, b: [x for x in bars if a <= x["t"] < b])
    assert [x["event"] for x in changed] == ["closed"]
    done = ledger.trade(t["id"])
    assert done["exit_reason"] == "stop" and done["exit"] == 98
    assert done["pnl"] == pytest.approx(-done["risk_usd"], rel=1e-6) and done["r"] == pytest.approx(-1.0)
    assert ledger.equity() == pytest.approx(1000 - done["risk_usd"])


def test_limit_fills_then_targets_or_expires(ledger):
    t0 = ledger.now_ms()
    t = ledger.open("ETH", "long", 99.0, 97.0, 104.0)
    ledger.clock_ref["t"] += 3600
    bars = [c(t0, 100, 100.5, 99.5, 100), c(t0 + 60_000, 99.5, 99.8, 98.9, 99.2), c(t0 + 120_000, 99.2, 104.5, 99.1, 104)]
    ev = [x["event"] for x in ledger.update(lambda coin, a, b: [x for x in bars if a <= x["t"] < b])]
    assert ev == ["filled", "closed"] and ledger.trade(t["id"])["exit_reason"] == "target"
    t2 = ledger.open("BTC", "short", 90_000.0, 91_000.0, 85_000.0)
    ledger.clock_ref["t"] += 25 * 3600
    ev = ledger.update(lambda coin, a, b: [])
    assert ev[0]["event"] == "expired" and ledger.trade(t2["id"])["status"] == "cancelled"


# -- plan checks and the gate ------------------------------------------------------------------------------------------
def scan_row(price=100.0):
    frame = {"close": price, "rsi14": 55.0, "ema20": 99.0, "ema50": 97.0, "trend": "up", "atr14": 1.0, "rel_volume": 2.5,
             "levels": {"support": [98.37, 96.12], "resistance": [103.4, 105.9]}}
    return {"coin": "SOL", "ok": True, "price": price, "change_24h_pct": 3.0, "h1": frame,
            "h4": {**frame, "atr14": 2.5, "levels": {"support": [97.21], "resistance": [106.3]}},
            "round_numbers": ind.round_numbers(price), "flags": ["volume_spike_1h"], "source": "fake"}


def test_validate_plan_accepts_a_stop_just_beyond_a_real_level():
    p = A.parse_plan('```json\n{"coin":"SOL","side":"long","entry":"market","stop":98.1,"stop_level":98.37,"target":103.3,"why":"x"}\n```')
    ok, problems, facts = A.validate_plan(p, scan_row(), 100.0, 1.5)
    assert ok, problems
    assert facts["rr"] == pytest.approx(3.3 / 1.9, abs=0.01)


@pytest.mark.parametrize("plan,needle", [
    ({"stop": 97.0, "stop_level": 97.5, "target": 105}, "not one of the real swing levels"),
    ({"stop": 98.5, "stop_level": 98.37, "target": 105}, "beyond the level"),
    ({"stop": 97.4, "stop_level": 98.37, "target": 105}, "not 'just beyond'"),
    ({"stop": 98.1, "stop_level": 98.37, "target": 101}, "reward:risk"),
    ({"stop": 98.1, "stop_level": 98.37, "target": 99}, "stop < entry < target"),
])
def test_validate_plan_rejects(plan, needle):
    p = A.Plan(coin="SOL", side="long", entry="market", **plan)
    ok, problems, _ = A.validate_plan(p, scan_row(), 100.0, 2.0)
    assert not ok and any(needle in x for x in problems), problems


def test_a_stop_on_a_round_number_is_rejected():
    row = scan_row(100.0)
    row["h1"]["levels"]["support"] = [90.2]
    p = A.Plan(coin="SOL", side="long", entry="market", stop=90.0, stop_level=90.2, target=125.0)
    ok, problems, _ = A.validate_plan(p, row, 100.0, 2.0)
    assert any("round number" in x for x in problems)


def test_verdict_parsing_and_the_mode_gate():
    assert A.parse_verdict("blah\nVERDICT: wait") == ("WAIT", None)
    assert A.parse_verdict("DEAL-BREAKER: CPI in an hour\nVERDICT: **SKIP**") == ("SKIP", "CPI in an hour")
    assert A.parse_verdict("no verdict here") == (None, None)
    assert A.decide("cautious", True, "BUY", None)[0]
    assert not A.decide("cautious", True, "WAIT", None)[0]
    assert A.decide("aggressive", True, "WAIT", None)[0]
    assert A.decide("aggressive", True, "SKIP", None)[0]                  # SKIP without a deal-breaker
    assert not A.decide("aggressive", True, "SKIP", "exchange hack")[0]
    assert not A.decide("aggressive", False, "BUY", None)[0]              # no valid plan never trades


def test_settings_validation():
    s = validate_settings({"schedule_hours": 2, "universe": "btc, eth,SOLUSDT", "mode": "aggressive", "risk_pct": 1},
                          DEFAULT_SETTINGS, has_trades=False)
    assert s["universe"] == ["BTC", "ETH", "SOL"] and s["mode"] == "aggressive" and s["schedule_hours"] == 2
    for bad in ({"schedule_hours": 5}, {"mode": "yolo"}, {"risk_pct": 50}, {"universe": "bad coin!"},
                {"notify": {"email": True}}, {"tz": "Mars/Base"}):
        with pytest.raises(ValueError):
            validate_settings(bad, DEFAULT_SETTINGS, has_trades=False)
    with pytest.raises(ValueError):
        validate_settings({"start_balance": 5000}, DEFAULT_SETTINGS, has_trades=True)


# -- the key ------------------------------------------------------------------------------------------------------------
def test_the_key_never_leaks():
    secret = "sk-or-v1-" + "a" * 40
    seen = {}

    def transport(url, body, headers, timeout):
        seen["auth"] = headers["Authorization"]
        return 401, {}, json.dumps({"error": {"message": f"bad key {secret}"}}).encode()
    llm = DeskLLM(secret, transport=transport)
    assert secret not in repr(llm) and secret not in json.dumps(vars(llm), default=str)
    with pytest.raises(LLMError) as e:
        llm.chat("s", [{"role": "user", "content": "x"}])
    assert secret not in str(e.value) and seen["auth"] == "Bearer " + secret


def test_daily_call_budget():
    llm = DeskLLM("k" * 20, max_calls_per_day=1,
                  transport=lambda *a: (200, {}, json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()))
    assert llm.chat("s", [])["text"] == "hi"
    with pytest.raises(LLMError, match="budget"):
        llm.chat("s", [])


def test_the_daily_budget_survives_a_restart():
    store = {}
    ok = lambda *a: (200, {}, json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {"cost": 0.05}}).encode())
    first = DeskLLM("k" * 20, max_calls_per_day=2, transport=ok, clock=lambda: 86400 * 5 + 10)
    first.attach_store(lambda: store.get("u"), lambda v: store.__setitem__("u", v))
    first.chat("s", [])
    again = DeskLLM("k" * 20, max_calls_per_day=2, transport=ok, clock=lambda: 86400 * 5 + 20)
    again.attach_store(lambda: store.get("u"), lambda v: store.__setitem__("u", v))
    assert again.health()["calls_today"] == 1 and again.health()["cost_total_usd"] == 0.05
    again.chat("s", [])
    with pytest.raises(LLMError, match="budget"):
        again.chat("s", [])
    tomorrow = DeskLLM("k" * 20, max_calls_per_day=2, transport=ok, clock=lambda: 86400 * 6 + 1)
    tomorrow.attach_store(lambda: store.get("u"), lambda v: store.__setitem__("u", v))
    assert tomorrow.health()["calls_today"] == 0 and tomorrow.health()["cost_total_usd"] == 0.1


def test_public_desk_routes_are_get_only():
    from app.core import api_desk
    routes = [r for r in api_desk.public_router.routes]
    assert routes and all(set(r.methods) <= {"GET", "HEAD"} for r in routes)
    assert all(r.path.startswith("/api/desk") for r in api_desk.router.routes)


# -- one full session ---------------------------------------------------------------------------------------------------
class FakeMarket:
    errors: dict = {}

    def scan(self, universe):
        return [scan_row()] + [{"coin": "BTC", "ok": False, "reason": "no data"}]

    def scan_coin(self, coin):
        return scan_row()

    def price(self, coin, fresh=False):
        return "fake", 100.0

    def candles(self, coin, tf, limit=60, end_ms=None, closed_only=True):
        return "fake", [c(i * H, 100, 101, 99, 100) for i in range(limit)]

    def minutes(self, coin, a, b):
        return "fake", []


class FakeLLM:
    configured = True
    cost_total = 0.0

    def __init__(self, verdict="BUY"):
        self.verdict = verdict
        self.prompts = []

    def health(self):
        return {"model": "fake"}

    def chat(self, system, messages, tools=None, run_tool=None, json_mode=False, **kw):
        self.prompts.append(system)
        if "Charts" in system.split("\n")[0]:
            text = json.dumps({"coin": "SOL", "side": "long", "entry": "market", "stop": 98.1, "stop_level": 98.37,
                               "target": 104.5, "why": "breakout over 103.4 on 2.5x volume"})
            if "the Skeptic has attacked your plan" in system:
                text = "Skeptic, the volume is real; I keep it.\n" + text
        elif "Skeptic" in system.split("\n")[0]:
            text = f"Volume is real but RSI 55 leaves room.\nVERDICT: {self.verdict}"
        else:
            text = "take"
        return {"text": text, "tool_calls": [], "cost_usd": 0.0}


@pytest.mark.parametrize("verdict,trades", [("BUY", 1), ("WAIT", 0)])
def test_a_full_session_with_fakes(tmp_path, monkeypatch, verdict, trades):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})
    published = []

    class Bus:
        def publish(self, ch, data, public=False, replay=True):
            published.append((ch, public))
    desk = DeskService(str(tmp_path / "d.db"), bus=Bus(), env={"DESK_ENABLED": "false"}, market=FakeMarket(),
                       llm=FakeLLM(verdict))
    row = desk.run_session("test")
    assert row["status"] == "done" and row["verdict"] == f"SOL:{verdict}"
    assert len(desk.ledger.trades(("open",))) == trades
    authors = [m["author"] for m in desk.ledger.messages("group")]
    assert authors[:9] == ["chief", "scout", "news", "sentiment", "charts", "skeptic", "charts", "skeptic", "chief"]
    kinds = [m["kind"] for m in desk.ledger.messages("group")]
    assert kinds[6] == "reply" and "Final plan" in desk.ledger.messages("group")[6]["text"]
    assert all(p for ch, p in published if ch == "desk")
    log = desk.ledger.session(row["id"])["log_md"]
    assert "## Skeptic" in log and ("TRADE: LONG SOL" if trades else "NO TRADE") in log
    desk.ledger.close()


# -- talking to the desk and giving it tasks ------------------------------------------------------------------------------
class ToolLLM:
    """Calls the scripted tools through run_tool (as a real model's tool calls would) and reports what came back."""
    configured = True
    cost_total = 0.0

    def __init__(self, script):
        self.script = script
        self.systems = []

    def health(self):
        return {"model": "fake"}

    def chat(self, system, messages, tools=None, run_tool=None, **kw):
        self.systems.append(system.split("\n")[0])
        results = [run_tool(name, args) for name, args in self.script] if run_tool else []
        return {"text": "done: " + json.dumps(results, default=str)[:300], "tool_calls": [{"name": n, "args": a} for n, a in self.script],
                "cost_usd": 0.0}


def desk_with(tmp_path, script):
    return DeskService(str(tmp_path / "d.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=ToolLLM(script))


def test_close_now_and_cancel(ledger):
    t = ledger.open("SOL", "long", "market", 98, 105, price=100)
    done = ledger.close_now(t["id"], 101.0)
    assert done["status"] == "closed" and done["exit_reason"] == "manual" and done["pnl"] > 0
    p = ledger.open("ETH", "long", 99.0, 97.0, 104.0)
    assert ledger.close_now(p["id"], 0)["status"] == "cancelled"
    with pytest.raises(LedgerError):
        ledger.close_now(t["id"], 101.0)                                 # already closed


def test_group_chat_routes_to_the_mentioned_agent_and_it_can_open_a_trade(tmp_path):
    desk = desk_with(tmp_path, [("open_trade", {"coin": "SOL", "side": "long", "entry": "market", "stop": 98.1,
                                                "target": 104.5, "reason": "operator asked"})])
    reply = desk.chat("@Charts open a long on SOL with a stop under support")
    assert reply["author"] == "charts" and reply["data"]["tools"] == ["open_trade"]
    assert desk.llm.systems[0].startswith("You are Charts")
    trades = desk.ledger.trades(("open",))
    assert len(trades) == 1 and trades[0]["coin"] == "SOL" and "operator request via Charts" in trades[0]["reason"]
    kinds = [(m["author"], m["kind"]) for m in desk.ledger.messages("group")]
    assert kinds == [("you", "chat"), ("charts", "trade"), ("charts", "chat")]
    desk.ledger.close()


def test_chief_answers_by_default_and_tasks_respect_the_ledger_rules(tmp_path):
    desk = desk_with(tmp_path, [("open_trade", {"coin": "SOL", "side": "long", "entry": "market", "stop": 105, "target": 110})])
    desk.chat("open a long on SOL")
    assert desk.llm.systems[0].startswith("You are Chief")
    assert desk.ledger.trades(("open",)) == []                           # stop above entry: refused by the ledger
    desk.ledger.close()


def test_close_trade_and_settings_tasks(tmp_path):
    desk = desk_with(tmp_path, [])
    t = desk.ledger.open("SOL", "long", "market", 98, 105, price=100)
    out = desk.run_tool("close_trade", {"trade_id": t["id"]}, "chief")
    assert out["ok"] and out["trade"]["status"] == "closed" and out["trade"]["exit"] == 100.0
    out = desk.run_tool("update_settings", {"mode": "aggressive", "schedule_hours": 2}, "chief")
    assert out["ok"] and desk.settings["mode"] == "aggressive" and desk.settings["schedule_hours"] == 2
    assert not desk.run_tool("update_settings", {"risk_pct": 50}, "chief")["ok"]
    assert desk.run_tool("check_plan", {"coin": "SOL", "side": "long", "entry": "market", "stop": 98.1, "stop_level": 98.37,
                                        "target": 104.5}, "charts")["ok"]
    desk.ledger.close()


def test_dm_is_private_and_can_act(tmp_path):
    published = []

    class Bus:
        def publish(self, ch, data, public=False, replay=True):
            published.append(public)
    desk = desk_with(tmp_path, [("ledger", {})])
    desk.bus = Bus()
    msg = desk.dm("skeptic", "how is the book doing?")
    assert msg["channel"] == "dm:skeptic" and msg["data"]["tools"] == ["ledger"]
    assert published == [False, False] and desk.ledger.messages("group") == []
    desk.ledger.close()


def test_the_desk_defaults_to_a_cheap_model():
    from app.desk.llm import DEFAULT_MODEL
    assert "haiku" in DEFAULT_MODEL and "opus" not in DEFAULT_MODEL
    assert DeskLLM.from_env({"OPENROUTER_API_KEY": "k"}).model == DEFAULT_MODEL


# -- audit fixes ---------------------------------------------------------------------------------------------------------
def test_sessions_use_the_mode_setting(tmp_path, monkeypatch):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})
    desk = DeskService(str(tmp_path / "d.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=FakeLLM("WAIT"))
    desk.update_settings({"mode": "aggressive"})
    row = desk.run_session("test")
    assert row["mode"] == "aggressive" and "aggressive mode" in desk.ledger.messages("group")[0]["text"]
    assert row["trade_id"]                                  # aggressive: a WAIT without a deal-breaker still trades
    desk.ledger.close()


def test_the_cost_counter_counts_the_session_history(tmp_path):
    lg = Ledger(str(tmp_path / "d.db"))
    lg.save_session({"id": "a", "started_ts": int(__import__("time").time() * 1000), "cost_usd": 0.19})
    lg.close()
    llm = DeskLLM("k" * 20, transport=lambda *a: (200, {}, b"{}"))
    desk = DeskService(str(tmp_path / "d.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=llm)
    h = llm.health()
    assert h["cost_total_usd"] >= 0.19 and h["cost_today_usd"] >= 0.19 and h["calls_today"] >= 7
    desk.ledger.close()


def test_a_cut_off_reply_ends_at_a_full_sentence():
    from app.desk.llm import complete_sentences
    assert complete_sentences("BTC holds. ETH is weak and SOL leads the board. Both trade below their") == \
        "BTC holds. ETH is weak and SOL leads the board."
    assert complete_sentences("no full stop here").endswith("…")
    answers = iter([{"choices": [{"message": {"content": "Done. Then cut"}, "finish_reason": "length"}]},
                    {"choices": [{"message": {"content": "off here, now finished."}, "finish_reason": "stop"}]}])
    llm = DeskLLM("k" * 20, transport=lambda *a: (200, {}, json.dumps(next(answers)).encode()))
    assert llm.chat("s", [])["text"] == "Done. Then cut off here, now finished."      # one continuation call


def test_levels_are_measured_from_the_live_price():
    from app.desk.market import Market
    import time as _t
    now = int(_t.time() * 1000) // 3_600_000 * 3_600_000

    def kl(n, step, last):
        out, p = [], 100.0
        for i in range(n):
            t = last - (n - i) * step
            hi = p + (3 if i % 10 == 5 else 1)
            out.append([t, p, hi, p - 1, p, 10.0, t + step - 1])
            p += 0.02
        return out

    def fetch(url):
        if "interval=1m" in url:
            return [[now - 60_000, 90.0, 90.0, 90.0, 90.0, 1.0, now]]       # live price far below every swing
        step = 3_600_000 if "interval=1h" in url else 14_400_000
        return kl(201, step, now)
    r = Market(fetch=fetch).scan_coin("SOL")
    assert r["price"] == 90.0
    for f in (r["h1"], r["h4"]):
        assert all(x < 90.0 for x in f["levels"]["support"]) and all(x > 90.0 for x in f["levels"]["resistance"])


def test_agents_can_ask_each_other_in_chat(tmp_path):
    desk = desk_with(tmp_path, [("ask_agent", {"agent": "skeptic", "question": "what could go wrong with a SOL long?"})])
    desk.chat("team, should we go long SOL?")
    msgs = desk.ledger.messages("group")
    assert [m["author"] for m in msgs] == ["you", "skeptic", "chief"]
    assert msgs[1]["text"].startswith("@Chief ") and msgs[2]["data"]["tools"] == ["ask_agent"]


def test_a_stop_inside_one_hourly_atr_is_rejected():
    row = scan_row(100.0)
    row["h1"]["levels"]["support"] = [99.53]                 # a real level just under the price
    p = A.Plan(coin="SOL", side="long", entry="market", stop=99.41, stop_level=99.53, target=103.4)
    ok, problems, _ = A.validate_plan(p, row, 100.0, 1.5)
    assert not ok and any("inside one 1h ATR" in x for x in problems)


def test_a_session_can_open_several_trades_and_each_plan_gets_its_own_verdict(tmp_path, monkeypatch):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})

    class TwoCoins(FakeMarket):
        def scan(self, universe):
            return [scan_row(), {**scan_row(), "coin": "ETH"}, {**scan_row(), "coin": "ADA"}]

        def scan_coin(self, coin):
            return {**scan_row(), "coin": coin}

    plan = {"side": "long", "entry": "market", "stop": 98.1, "stop_level": 98.37, "target": 104.5, "why": "x"}

    class MultiLLM(FakeLLM):
        def chat(self, system, messages, **kw):
            head = system.split("\n")[0]
            if "Charts" in head:
                return {"text": json.dumps({"plans": [{**plan, "coin": c} for c in ("SOL", "ETH", "ADA")]}),
                        "tool_calls": [], "cost_usd": 0.0}
            if "Skeptic" in head:
                return {"text": "Charts, ADA is fine.\nVERDICT SOL: BUY\nVERDICT ETH: WAIT\nDEAL-BREAKER ADA: unlock\nVERDICT ADA: SKIP",
                        "tool_calls": [], "cost_usd": 0.0}
            return {"text": "take", "tool_calls": [], "cost_usd": 0.0}
    desk = DeskService(str(tmp_path / "d.db"), env={"DESK_ENABLED": "false"}, market=TwoCoins(), llm=MultiLLM())
    desk.update_settings({"mode": "aggressive"})
    row = desk.run_session("test")
    opened = sorted(t["coin"] for t in desk.ledger.trades(("open",)))
    assert opened == ["ETH", "SOL"]                       # aggressive: BUY and WAIT trade, SKIP + deal-breaker does not
    assert "NO TRADE ADA" in row["decision"] and row["decision"].count("TRADE: LONG") == 2
    desk.update_settings({"mode": "cautious"})
    desk.ledger.close()


def test_new_settings_validate():
    s = validate_settings({"max_plans": 2, "max_open": 5}, DEFAULT_SETTINGS, has_trades=True)
    assert s["max_plans"] == 2 and s["max_open"] == 5
    for bad in ({"max_plans": 4}, {"max_open": 9}):
        with pytest.raises(ValueError):
            validate_settings(bad, DEFAULT_SETTINGS, has_trades=False)


# -- second audit -----------------------------------------------------------------------------------------------------
def test_total_risk_and_one_side_caps(ledger):
    ledger.set_kv("settings", {"max_open": 6, "max_same_side": 2, "max_total_risk_pct": 6.0})
    ledger.open("SOL", "short", "market", 102, 90, price=100)
    ledger.open("ETH", "short", "market", 2550, 2300, price=2500)
    with pytest.raises(LedgerError, match="on one side"):
        ledger.open("XRP", "short", "market", 1.5, 1.2, price=1.4)
    ledger.set_kv("settings", {"max_open": 6, "max_same_side": 6, "max_total_risk_pct": 1.0})   # 10 USD
    with pytest.raises(LedgerError, match="total open risk"):
        ledger.open("ADA", "long", "market", 0.23, 0.26, price=0.24)


def test_a_stop_sitting_on_its_level_is_rejected():
    p = A.Plan(coin="SOL", side="long", entry="market", stop=98.36, stop_level=98.37, target=104.5)
    ok, problems, _ = A.validate_plan(p, scan_row(), 100.0, 1.5)
    assert not ok and any("beyond the level" in x for x in problems)


def test_a_missing_verdict_is_asked_for_once_and_otherwise_means_no_trade(tmp_path):
    class Answers(FakeLLM):
        def __init__(self, replies):
            super().__init__()
            self.replies = iter(replies)

        def chat(self, system, messages, **kw):
            return {"text": next(self.replies), "tool_calls": [], "cost_usd": 0.0}
    desk = DeskService(str(tmp_path / "a.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(),
                       llm=Answers(["VERDICT ETH: BUY"]))
    assert desk._need_verdicts("Charts, ETH looks ok.", {}, ["ETH"]) == {"ETH": ("BUY", None)}
    desk.llm = Answers(["still no verdict"])
    assert desk._need_verdicts("Charts, ETH looks ok.", {}, ["ETH"]) == {"ETH": (None, None)}
    assert not A.decide("aggressive", True, None, None)[0]
    desk.ledger.close()


def test_the_public_feed_leaves_trade_charts_out(tmp_path):
    desk = desk_with(tmp_path, [])
    t = desk.ledger.open("SOL", "long", "market", 98, 105, price=100, chart=[[0, 1, 2, 0, 1]] * 60)
    desk.post("chief", "trade", "opened", {"trade": t})
    state = json.dumps(desk.public_state())
    assert '"chart"' not in state and '"SOL"' in state
    desk.ledger.close()


def test_open_trades_show_where_and_when_the_price_came_from(tmp_path):
    class Live(FakeMarket):
        def ticker(self, coin, fresh=False):
            return {"source": "binance", "price": 101.0, "change_24h_pct": 1.0, "ts": 1_800_000_000_000}
    desk = DeskService(str(tmp_path / "m.db"), env={"DESK_ENABLED": "false"}, market=Live(), llm=FakeLLM())
    desk.ledger.open("SOL", "long", "market", 98, 105, price=100)
    row = desk.report()["open"][0]
    assert row["mark"] == 101.0 and row["mark_source"] == "binance" and row["mark_ts"] == 1_800_000_000_000
    assert desk.report()["checked_every_s"] == 300
    desk.ledger.close()


def test_charts_gets_one_more_turn_when_it_stops_without_a_plan(tmp_path, monkeypatch):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})

    class Forgetful(FakeLLM):
        def chat(self, system, messages, json_mode=False, **kw):
            if "Charts" in system.split("\n")[0] and not json_mode and "attacked" not in system:
                return {"text": "I left out the target, so I'm re-running the check.", "tool_calls": [], "cost_usd": 0.0}
            return super().chat(system, messages, json_mode=json_mode, **kw)
    desk = DeskService(str(tmp_path / "f.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=Forgetful("BUY"))
    row = desk.run_session("test")
    assert row["trade_id"] and "TRADE: LONG SOL" in row["decision"]
    desk.ledger.close()


def test_a_plan_missing_a_field_gets_a_fix_turn_and_can_still_trade(tmp_path, monkeypatch):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})
    good = {"coin": "SOL", "side": "long", "entry": "market", "stop": 98.1, "stop_level": 98.37, "target": 104.5, "why": "x"}

    class NoTarget(FakeLLM):
        calls = 0

        def chat(self, system, messages, **kw):
            head = system.split("\n")[0]
            ask = messages[-1]["content"]
            if "Charts" in head and "failed the desk checks" in ask:
                return {"text": json.dumps({"plans": [good]}), "tool_calls": [], "cost_usd": 0.0}
            if "Charts" in head and "attacked" not in system:
                bad = {k: v for k, v in good.items() if k != "target"}
                return {"text": json.dumps({"plans": [bad]}), "tool_calls": [], "cost_usd": 0.0}
            return super().chat(system, messages, **kw)
    desk = DeskService(str(tmp_path / "n.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=NoTarget("BUY"))
    row = desk.run_session("test")
    assert row["trade_id"] and row["verdict"] == "SOL:BUY"
    desk.ledger.close()


def test_a_session_without_plans_saves_its_verdict_in_the_same_format(tmp_path, monkeypatch):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(N, "read_articles", lambda items, **k: [dict(i, article="") for i in items])
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})

    class Nothing(FakeLLM):
        def chat(self, system, messages, **kw):
            head = system.split("\n")[0]
            if "Charts" in head:
                return {"text": json.dumps({"plans": [], "why": "quiet"}), "tool_calls": [], "cost_usd": 0.0}
            if "Skeptic" in head:
                return {"text": "Nothing to trade.\nVERDICT: SKIP", "tool_calls": [], "cost_usd": 0.0}
            return super().chat(system, messages, **kw)
    desk = DeskService(str(tmp_path / "e.db"), env={"DESK_ENABLED": "false"}, market=FakeMarket(), llm=Nothing())
    assert desk.run_session("test")["verdict"] == "ALL:SKIP"
    desk.ledger.close()


# -- Claude (operator's Anthropic key) --------------------------------------------------------------------------------
from types import SimpleNamespace as NS


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records every messages.create call and replays scripted responses."""

    def __init__(self, responses, fail=None):
        self.calls, self.responses, self.fail = [], list(responses), fail
        self.messages = NS(create=self.create)

    def create(self, **params):
        if self.fail:
            raise self.fail
        self.calls.append(params)
        return self.responses.pop(0)


def resp(blocks, stop="end_turn", tin=1000, tout=500):
    return NS(content=blocks, stop_reason=stop, usage=NS(input_tokens=tin, output_tokens=tout))


def test_claude_runs_tools_keeps_blocks_and_prices_by_model():
    from app.desk.claude import ClaudeLLM
    thinking = NS(type="thinking", thinking="", signature="sig")
    use = NS(type="tool_use", id="tu1", name="scan_coin", input={"coin": "ETH"})
    fake = FakeAnthropic([resp([thinking, use], stop="tool_use"), resp([NS(type="text", text="ETH holds 2484.")])])
    llm = ClaudeLLM("sk-ant-test-" + "x" * 30, client_factory=lambda key: fake)
    seen = []
    out = llm.chat("sys", [{"role": "user", "content": "q"}, {"role": "user", "content": "more"}], tools=TOOLS_FOR_TEST,
                   run_tool=lambda n, a: seen.append((n, a)) or {"price": 2490}, deep=True)
    assert out["text"] == "ETH holds 2484." and seen == [("scan_coin", {"coin": "ETH"})]
    first, second = fake.calls
    assert first["model"] == "claude-sonnet-5-5" and first["messages"] == [{"role": "user", "content": "q\n\nmore"}]
    assert first["tools"][0]["input_schema"]["properties"]["coin"]["type"] == "string"
    assert second["messages"][1] == {"role": "assistant", "content": [thinking, use]}          # blocks passed back as-is
    assert second["messages"][2]["content"][0]["tool_use_id"] == "tu1"
    assert "temperature" not in first                                                          # rejected by 5.5 models
    assert out["cost_usd"] == pytest.approx(2 * (1000 * 2.0 + 500 * 10.0) / 1e6)
    fake.responses.append(resp([NS(type="text", text="ok")]))
    llm.chat("sys", [{"role": "user", "content": "q"}])                                     # not deep: the fast model
    assert fake.calls[-1]["model"] == "claude-haiku-5-5"


TOOLS_FOR_TEST = [{"type": "function", "function": {"name": "scan_coin", "description": "x",
                                                     "parameters": {"type": "object", "properties": {"coin": {"type": "string"}}}}}]


def test_claude_daily_usd_cap_and_secret_safe_errors():
    from app.desk.claude import ClaudeLLM
    secret = "sk-ant-api03-" + "s" * 40
    fake = FakeAnthropic([resp([NS(type="text", text="a")], tin=1_000_000, tout=0)] * 3)
    llm = ClaudeLLM(secret, client_factory=lambda key: fake, max_usd_per_day=1.0, model_deep="claude-sonnet-5-5")
    llm.chat("s", [{"role": "user", "content": "x"}], deep=True)                    # $2 spent
    with pytest.raises(LLMError, match="spend limit"):
        llm.chat("s", [{"role": "user", "content": "x"}])
    boom = ClaudeLLM(secret, client_factory=lambda key: FakeAnthropic([], fail=RuntimeError(f"bad key {secret}")))
    with pytest.raises(LLMError) as e:
        boom.chat("s", [{"role": "user", "content": "x"}])
    assert secret not in str(e.value) and secret not in repr(boom)


def test_the_desk_uses_the_dashboard_key_live_and_falls_back_to_openrouter():
    from app.desk.claude import make_llm
    vault = {"key": None}
    llm = make_llm({"OPENROUTER_API_KEY": "o" * 30}, anthropic_key=lambda: vault["key"])
    assert llm.provider == "openrouter" and llm.health()["model"] == "anthropic/claude-haiku-5.5"
    vault["key"] = "sk-ant-api03-" + "d" * 30                      # saved under Providers & Live: no restart needed
    h = llm.health()
    assert llm.provider == "anthropic" and h["model"] == "claude-haiku-5-5" and h["model_deep"] == "claude-sonnet-5-5"
    vault["key"] = None                                           # removed again: back to OpenRouter
    assert llm.provider == "openrouter"
    env_only = make_llm({"ANTHROPIC_API_KEY": "k" * 30})
    assert env_only.provider == "anthropic" and env_only.max_usd_per_day == 4.0
    dash_wins = make_llm({"ANTHROPIC_API_KEY": "env-key-" + "e" * 20}, anthropic_key=lambda: "dash-key-" + "d" * 20)
    assert dash_wins._ak().startswith("dash-key-")
    assert "dash-key" not in repr(dash_wins) and "env-key" not in repr(dash_wins)


def test_anthropic_is_a_vault_provider_with_no_secret():
    from app.live.providers import PROVIDERS, Providers
    p = PROVIDERS[("anthropic", "data")]
    assert p["optional_secret"] and p["market"] == "data" and p["env"][0] == "ANTHROPIC_API_KEY"

    class Vault:
        enabled = True

        def get(self, pid):
            return ("sk-ant-api03-" + "v" * 30, "") if pid == "anthropic:data" else None
    prov = Providers(env={}, vault=Vault())
    assert prov.keys("anthropic", "data")[0].startswith("sk-ant-api03-") and prov.source("anthropic", "data") == "dashboard"

def test_a_key_check_error_stays_readable_when_the_secret_is_empty():
    import asyncio
    from app.live.providers import Providers
    key = "sk-ant-api03-" + "z" * 30

    class Bad:
        async def fetch_balance(self):
            raise RuntimeError(f"invalid x-api-key {key}")

        async def close(self):
            return None
    r = asyncio.run(Providers(env={}, client_factory=lambda ex, net: Bad())._balance_with("anthropic", "data", (key, "")))
    assert r["error"] == "RuntimeError: invalid x-api-key ***"
