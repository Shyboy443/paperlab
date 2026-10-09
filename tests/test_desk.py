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

    def price(self, coin):
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
        elif "Skeptic" in system.split("\n")[0]:
            text = f"Volume is real but RSI 55 leaves room.\nVERDICT: {self.verdict}"
        else:
            text = "take"
        return {"text": text, "tool_calls": [], "cost_usd": 0.0}


@pytest.mark.parametrize("verdict,trades", [("BUY", 1), ("WAIT", 0)])
def test_a_full_session_with_fakes(tmp_path, monkeypatch, verdict, trades):
    from app.desk import news as N, sentiment as S
    monkeypatch.setattr(N, "news", lambda universe, **k: {"items": [], "sources": {}})
    monkeypatch.setattr(S, "sentiment", lambda coins, social=None, **k: {"coins": {x: {"positioning": {}} for x in coins},
                                                                         "coingecko_trending": []})
    published = []

    class Bus:
        def publish(self, ch, data, public=False, replay=True):
            published.append((ch, public))
    desk = DeskService(str(tmp_path / "d.db"), bus=Bus(), env={"DESK_ENABLED": "false"}, market=FakeMarket(),
                       llm=FakeLLM(verdict))
    row = desk.run_session("test")
    assert row["status"] == "done" and row["verdict"] == verdict
    assert len(desk.ledger.trades(("open",))) == trades
    authors = [m["author"] for m in desk.ledger.messages("group")]
    assert authors[:6] == ["chief", "scout", "news", "sentiment", "charts", "skeptic"] and "chief" == authors[6]
    assert all(p for ch, p in published if ch == "desk")
    log = desk.ledger.session(row["id"])["log_md"]
    assert "## Skeptic" in log and ("TRADE: LONG SOL" if trades else "NO TRADE") in log
    desk.ledger.close()
