"""V9 STOCKS: the trading calendar, the regular-session tape (flat fills, no overnight), the entry window, the field,
session-based elimination and qualification, the Alpaca feed's delivery and gap repair, Jev V9 and the wiring. No
network, no keys."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.competition import v9_config as v9
from app.core.types import Candle
from app.live import alpaca_market as am
from app.strategies.v9.stocks import entry_allowed, load_v9_stock_scalpers

MINUTE = 60_000
CAL = [{"date": "2026-09-25", "open": "09:30", "close": "16:00"}, {"date": "2026-09-28", "open": "09:30", "close": "16:00"},
       {"date": "2026-11-27", "open": "09:30", "close": "13:00"}]
FRI_OPEN = 1790343000000          # 2026-09-25 09:30 New York (EDT) = 13:30 UTC
MON_OPEN = FRI_OPEN + 3 * 86_400_000


def bar(sym, t, c=100.0, v=10.0, src="backfill"):
    return Candle(sym, "1m", t, c, c + 0.1, c - 0.1, c, v, t + MINUTE - 1, True, 0.0, 0, src, 0.0)


class TestCalendar:
    def test_sessions_follow_new_york_time_and_half_days(self):
        s = am.Sessions.from_calendar(CAL)
        assert s.iv[0] == (FRI_OPEN, FRI_OPEN + 390 * MINUTE)
        assert s.iv[2][1] - s.iv[2][0] == 210 * MINUTE                      # the day after Thanksgiving closes 13:00
        assert s.is_open(FRI_OPEN) and not s.is_open(FRI_OPEN - 1) and not s.is_open(FRI_OPEN + 390 * MINUTE)
        assert s.next_open(FRI_OPEN + 400 * MINUTE) == MON_OPEN
        assert s.closed_between(FRI_OPEN - 1, MON_OPEN + 391 * MINUTE) == 2

    def test_timestamps(self):
        assert am.parse_ts("2026-09-25T13:30:00Z") == FRI_OPEN
        assert am.parse_ts("2026-09-25T13:30:00.123456789Z") == FRI_OPEN + 123


class TestSessionTape:
    def test_drops_extended_hours_and_fills_silent_minutes_flat(self):
        s = am.Sessions.from_calendar(CAL)
        raw = [bar("SPY", FRI_OPEN - 5 * MINUTE, 99.0), bar("SPY", FRI_OPEN, 100.0), bar("SPY", FRI_OPEN + 3 * MINUTE, 101.0)]
        tape = am.session_tape("SPY", raw, s)
        assert [b.open_time for b in tape] == [FRI_OPEN + i * MINUTE for i in range(4)]
        assert [b.source for b in tape] == ["backfill", "filled", "filled", "backfill"]
        assert tape[1].close == tape[1].open == 100.0 and tape[1].volume == 0.0

    def test_never_fills_across_the_night(self):
        s = am.Sessions.from_calendar(CAL)
        raw = [bar("SPY", FRI_OPEN + 389 * MINUTE), bar("SPY", MON_OPEN + 2 * MINUTE)]
        tape = am.session_tape("SPY", raw, s)
        assert [b.open_time for b in tape] == [FRI_OPEN + 389 * MINUTE, MON_OPEN + 2 * MINUTE]


class TestStrategies:
    def test_entry_window_leaves_room_for_the_time_stop(self):
        s = am.Sessions.from_calendar(CAL)
        assert not entry_allowed(FRI_OPEN + 5 * MINUTE, s.at)
        assert entry_allowed(FRI_OPEN + 10 * MINUTE, s.at)
        assert entry_allowed(FRI_OPEN + 340 * MINUTE, s.at) and not entry_allowed(FRI_OPEN + 341 * MINUTE, s.at)
        half = s.iv[2]
        assert not entry_allowed(half[1] - 49 * MINUTE, s.at) and entry_allowed(half[1] - 50 * MINUTE, s.at)
        assert not entry_allowed(FRI_OPEN + 20 * 60 * MINUTE, s.at)            # the evening

    def test_families_are_v8_setups_with_stock_stops(self):
        fams = load_v9_stock_scalpers()
        assert list(fams) == ["V9.1", "V9.2", "V9.3"]
        for cls in fams.values():
            p = cls.Params()
            assert p.min_stop_pct == 0.0025 and p.max_stop_pct == 0.010 and p.max_hold_min == 45 and p.target_r == 1.5
            k = cls.for_class("SCALP", session_at=lambda t: None)
            assert k.signal_tf == "5m" and k.day_bars == 78 and k.session_at(0) is None


class TestFieldAndRules:
    def test_every_control_has_a_twin_and_trades_its_ticker(self):
        specs = v9.field_plan(v9.SYMBOLS)
        assert len(specs) == 60 and sum(s.role == "JEV" for s in specs) == 30
        c, j = specs[0], specs[1]
        assert (c.key, c.symbol, c.timeframe) == ("V9.1-SPY-5M", "SPY", "5m") and j.key == "V9.1-SPY-5M+JEV"
        assert j.pair_id == c.pair_id == "v9pair:V9.1-SPY-5M"

    def test_costs_and_books(self):
        assert v9.FEES_V9.maker_rate == v9.FEES_V9.taker_rate <= 0.00005
        assert v9.STARTING_BALANCE == 1000.0 and v9.LEVERAGE_CAP == 3
        mmr = v9.load_freeze()["rules"]["SPY"]["maint_margin_rate"]
        assert 1.0 / v9.LEVERAGE_CAP - mmr > 0.05          # a paper liquidation never sits at (or near) the entry
        assert all(r["step"] == 1.0 and r["tick"] == 0.01 for r in v9.RULES.values())
        assert v9.settings_v9().strategy_starting_balance == 1000.0


class TestElimination:
    ROW = {"key": "V9.1-SPY-5M", "start_equity": 1000.0, "equity": 990.0, "trades": 3}

    def test_judged_on_the_last_three_sessions_never_on_a_weekend(self):
        assert v9.should_eliminate(self.ROW, 5, None) is None                    # between sessions: drawdown only
        assert v9.should_eliminate(self.ROW, 2, 0) is None                       # not enough sessions to judge yet
        assert v9.should_eliminate(self.ROW, 3, 2) == "INACTIVE_UNDER_3_TRADES_IN_3_SESSIONS"
        assert v9.should_eliminate(self.ROW, 3, 3) is None
        assert v9.should_eliminate({**self.ROW, "role": "JEV"}, 3, 0) is None
        assert v9.should_eliminate({**self.ROW, "equity": 750.0}, 0, None) == "DRAWDOWN_25PCT"

    def test_qualification_counts_sessions(self):
        good = {"trades": 21, "net_now": 12.0, "profit_factor": 1.5, "max_dd": 0.05, "risk_state": "OK"}
        assert v9.bot_status(good, 4, None)[0] == "QUALIFIED" and v9.bot_status(good, 3, None)[0] == "ACTIVE"
        assert v9.bot_status(good, 5, {"at": 1, "reason": "x"})[0] == "ELIMINATED"


class FakeClient:
    def __init__(self, pages, down=False):
        self.pages = list(pages)
        self.calls = []
        self.down = down

    async def request(self, method, path, body=None, data=False):
        self.calls.append(path)
        if path.startswith("/v2/calendar"):
            return CAL
        if self.down:
            raise RuntimeError("REST unreachable")
        if path.startswith("/v2/stocks/quotes/latest"):
            return {"quotes": {"SPY": {"bp": 100.0, "ap": 100.02, "t": am.iso(FRI_OPEN)}}}
        return self.pages.pop(0) if self.pages else {"bars": {}}


def _raw(t, c):
    return {"t": am.iso(t), "o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 100, "n": 3, "vw": c}


class TestFeed:
    def make(self, pages, now, down=False):
        stored = []
        m = am.AlpacaLiveMarket(["SPY"], FakeClient(pages, down), lambda k, p: stored.append((k, p)), FRI_OPEN - 86_400_000,
                                clock=lambda: now / 1000)
        m.sessions = am.Sessions.from_calendar(CAL)
        return m, m.subscribe("SPY"), stored

    def test_backfill_delivers_the_session_tape_and_stores_it(self):
        now = FRI_OPEN + 9 * MINUTE + 30_000
        page = {"bars": {"SPY": [_raw(FRI_OPEN - 60 * MINUTE, 99.0), _raw(FRI_OPEN, 100.0), _raw(FRI_OPEN + 4 * MINUTE, 101.0),
                                 _raw(FRI_OPEN + 9 * MINUTE, 102.0)]}}
        m, q, stored = self.make([page], now)
        asyncio.run(m._catch_up(["SPY"], "backfill"))
        got = [q.get_nowait() for _ in range(q.qsize())]
        assert [b.open_time for b in got] == [FRI_OPEN + i * MINUTE for i in range(5)]    # 09:39 is still printing
        assert [p["source"] for k, p in stored] == ["backfill", "filled", "filled", "filled", "backfill"]

    def test_a_live_gap_is_repaired_from_rest_then_filled(self):
        now = FRI_OPEN + 20 * MINUTE
        m, q, _ = self.make([{"bars": {"SPY": [_raw(FRI_OPEN + 2 * MINUTE, 100.5)]}}], now)
        m._deliver(bar("SPY", FRI_OPEN), "live")
        asyncio.run(m._deliver_live(bar("SPY", FRI_OPEN + 5 * MINUTE, 101.0, src="live")))
        times = [q.get_nowait().open_time for _ in range(q.qsize())]
        assert times == [FRI_OPEN + i * MINUTE for i in range(6)] and m.stats["gaps_repaired"] == 1

    def test_silent_minutes_fill_only_while_the_stream_is_healthy(self):
        now = FRI_OPEN + 3 * MINUTE + am.FILL_GRACE_MS
        m, q, _ = self.make([], now, down=True)
        m.stats["calendar_ts"] = now / 1000
        m._deliver(bar("SPY", FRI_OPEN), "live")
        asyncio.run(m._tick())
        assert q.qsize() == 1                                            # stream AND REST down: nothing invented
        m.stats["ws"].update(connected=True, last_msg_ts=now / 1000)
        asyncio.run(m._tick())
        assert [q.get_nowait().open_time for _ in range(q.qsize())] == [FRI_OPEN + i * MINUTE for i in range(3)]

    def test_bars_outside_the_session_are_ignored_and_quotes_capped(self):
        m, q, _ = self.make([], FRI_OPEN + 500 * MINUTE)
        asyncio.run(m._deliver_live(bar("SPY", FRI_OPEN + 450 * MINUTE, src="live")))
        assert q.qsize() == 0
        inbox = asyncio.Queue()
        m._on_message({"T": "q", "S": "SPY", "bp": 100.0, "ap": 101.0}, inbox)
        assert m.mid("SPY") == 100.5 and m.live_half_spread_bps("SPY") == am.MAX_HALF_SPREAD_BPS == 1.0
        m._on_message({"T": "q", "S": "SPY", "bp": 100.00, "ap": 100.01}, inbox)       # a 1-cent book: kept as observed
        assert m.live_half_spread_bps("SPY") == pytest.approx(0.5, abs=0.01)
        m._on_message({"T": "b", "S": "SPY", **_raw(FRI_OPEN, 100.0)}, inbox)
        assert inbox.qsize() == 1

    def test_rest_polling_takes_over_when_the_stream_is_refused(self):
        now = FRI_OPEN + 6 * MINUTE + 25_000
        m, q, _ = self.make([{"bars": {"SPY": [_raw(FRI_OPEN + i * MINUTE, 100.0 + i) for i in (0, 1, 2, 4)]}}], now)
        m.stats["calendar_ts"] = now / 1000
        m.warm = True
        assert not m.ready()
        asyncio.run(m._tick())
        times = [q.get_nowait().open_time for _ in range(q.qsize())]
        assert times == [FRI_OPEN + i * MINUTE for i in range(6)]          # 09:33 filled between bars, 09:35 by grace
        assert m.stats["rest_mode"] and m.ready() and m.data_ok() and m.health()["source"] == "rest polling"
        assert m.mid("SPY") == pytest.approx(100.01)
        assert m.stream_ok("SPY") == (True, None)                        # polled bars just arrived

    def test_stream_freshness(self):
        m, _, _ = self.make([], FRI_OPEN)
        assert m.stream_ok("SPY")[0] is False and "no market data" in m.stream_ok("SPY")[1]
        m.stats["ws"]["connected"] = True
        m.stats["last_quote_ts"]["SPY"] = FRI_OPEN / 1000 - 10
        assert m.stream_ok("SPY") == (True, None)


class TestJevV9:
    def test_state_speaks_about_a_stock_session(self):
        from app.ai.jev.v9 import STATE_VERSION_V9, JevStateBuilderV9, minutes_to_close
        from app.core.types import Signal, TakeProfit
        t = FRI_OPEN + 60 * MINUTE
        cs = [Candle("SPY", "5m", t - (80 - i) * 5 * MINUTE, 100.0 + i * 0.01, 100.1 + i * 0.01, 99.9 + i * 0.01, 100.0 + i * 0.01,
                     10.0, t - (79 - i) * 5 * MINUTE - 1, True, 0.0, 0, "x", 0.0) for i in range(80)]
        sig = Signal("V9.1", "SPY", "entry", "long", t - 1, "5m", 100.8, stop=100.5, take_profits=[TakeProfit(101.25, 1.0)], reason="t")
        sig.meta.update(stop_pct=0.003, target_r=1.5)
        snap = JevStateBuilderV9().build(t=t, bot={"symbol": "SPY"}, sig=sig, series={"5m": cs}, execution={}, sizing={},
                                         health={}, position={})
        assert snap.state["state_version"] == STATE_VERSION_V9 and snap.state["session"]["minutes_to_close"] == 330.0
        assert "chg_24h" not in snap.state["price_action"] and minutes_to_close(t) == 330.0

    def test_the_gate_records_the_v9_prompt(self):
        from app.ai.jev.models import JevDecision, JevOutcome
        from app.live import v6_gates as gates
        from app.live.v9_worker import JevGateV9
        em = []
        dec = JevDecision(take_probability=0.9, risk_state="TAKE", risk_probabilities={"SKIP": 0.1, "TAKE": 0.8, "ATTACK": 0.1},
                          setup_quality=0.0, quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={},
                          model_resolved="m", provider="p", request_id="r", input_tokens=1, output_tokens=1, cost_usd=0.0)
        g = JevGateV9(eliminated_at={}, late_after_ms=v9.LATE_AFTER_MS, window_ms=v9.DECISION_WINDOW_MS,
                      margin_ms=v9.JEV_DEADLINE_MARGIN_MS, decide=lambda s: JevOutcome(True, dec, latency_ms=300), model="m",
                      mid=lambda s: 100.0, spread=lambda s: (0.5, "observed"), experiment_id="v9x-t", session_id="s",
                      bot={"key": "V9.1-SPY-5M+JEV", "symbol": "SPY", "pair_id": "v9pair:V9.1-SPY-5M", "horizon": "SCALP",
                           "strategy_id": "V9.1", "timeframe": "5m"},
                      emit=lambda k, d: em.append((k, d)), board=gates.DecisionBoard(lambda s, t: (True, None)),
                      coverage=gates.Coverage([(0, FRI_OPEN * 2)]), recorded=None, live_from_ms=0,
                      wall=lambda: (FRI_OPEN + 60 * MINUTE + 2000) / 1000)
        from app.ai.jev.v9 import JevStateBuilderV9
        g.builder = JevStateBuilderV9()
        from app.core.types import Signal, TakeProfit
        sig = Signal("V9.1", "SPY", "entry", "long", FRI_OPEN + 60 * MINUTE - 1, "5m", 100.0, stop=99.7,
                     take_profits=[TakeProfit(100.45, 1.0)], reason="t")
        ts = FRI_OPEN + 60 * MINUTE - 1
        g(sig, {"ts": ts, "sizing": {"tier": "TAKE"}, "health": {"r": [], "drawdown": 0.0, "peak": 1000.0}, "equity": 1000.0,
                "start_equity": 1000.0, "available": 1000.0, "taker_fee": 0.00002, "half_spread_bps": 0.5, "trades": [],
                "candles": [], "decision": SimpleNamespace(risk_usd=10.0, leverage=3.0, notional=3000.0)})
        assert em[0][1]["prompt_version"] == "JEV_PROMPT_V9_STOCKS" and em[0][1]["final_level"] == "TAKE"


class TestWiring:
    def test_service_knows_v9_and_it_is_off_by_default(self, tmp_path):
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "v9.db"), env={}, program="V9")
        assert svc.enabled is False and svc.cfg["jev"] is True and svc.health()["status"] == "DISABLED"
        assert V6ForwardService(str(tmp_path / "v9.db"), env={"V9_FORWARD_JEV": "false"}, program="V9").cfg["jev"] is False

    def test_public_routes_are_get_only_and_take_stock_tickers(self):
        from app.core import api_public
        routes = {(r.path, tuple(sorted(r.methods))) for r in api_public.router.routes}
        assert ("/api/public/competition/v9", ("GET",)) in routes
        cand = next(r for r in api_public.router.routes if r.path.endswith("/{program}/candles"))
        sym = next(p for p in cand.dependant.query_params if p.name == "symbol")
        assert all(getattr(m, "min_length", 1) <= 3 for m in sym.field_info.metadata if hasattr(m, "min_length"))

    def test_the_runner_takes_a_leverage_ceiling(self):
        import inspect
        from app.live.v6_runner import V6Bot
        assert inspect.signature(V6Bot).parameters["leverage"].default == 20


class TestFreeze:
    def test_the_running_v9_is_the_frozen_manifest(self):
        man = v9.load_freeze()
        assert man is not None and v9.verify_freeze(man) == []
        assert man["fingerprint"] == v9.fingerprint(man) and len(man["field"]) == 60 and set(man["rules"]) == set(v9.SYMBOLS)

    def test_a_change_is_detected_and_identity_is_v9(self):
        import json as _json
        man = _json.loads(_json.dumps(v9.load_freeze()))
        man["params"]["V9.1"]["min_stop_pct"] = 0.001
        assert any("params" in d for d in v9.verify_freeze(man))
        a, ident = v9.experiment_identity(v9.load_freeze(), "m")
        assert a.startswith("v9x-") and ident["venue"] == "ALPACA_IEX_US_EQUITY"
        assert v9.verify_freeze(None) == ["docs/V9_FREEZE.json is missing: V9 is not frozen"]


class TestCandlesOverTheWeekend:
    def test_a_closed_market_shows_its_last_bars_not_an_empty_clock_window(self, tmp_path):
        from app.core.arena_live_view import candles
        from app.core.storage import Storage
        st = Storage(str(tmp_path / "c.db"))
        st.fwd6_bars_save([{"symbol": "SPY", "open_time": FRI_OPEN + i * MINUTE, "open": 100.0, "high": 100.5, "low": 99.5,
                            "close": 100.2, "volume": 5.0, "source": "live"} for i in range(30)])
        sunday = MON_OPEN - 20 * 3_600_000
        d = candles(st, {"experiment_id": "v9x-c"}, "SPY", "5m", 300, now_ms=sunday)
        assert [c[0] for c in d["candles"]] == [FRI_OPEN + i * 5 * MINUTE for i in range(6)]
        st.close()


def test_stock_bots_keep_their_own_stops_and_hours_when_v8_changes():
    """V9's families subclass V8's (app/strategies/v8/arena.py). V8's 2026-10-02 settings (1.0-1.2% minimum stops, no
    time stop) must NOT leak into the stock bots: they keep 0.25% stops, the 45-minute hold, and are flat by the close."""
    from app.strategies.v9.stocks import BreakoutV9, PullbackV9, VwapSnapV9
    for cls in (PullbackV9, BreakoutV9, VwapSnapV9):
        p = cls.Params()
        assert p.min_stop_pct == 0.0025 and p.max_hold_min == 45.0 and p.hold_to_day_close is False
