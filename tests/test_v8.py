"""V8 SCALP: the freeze, the CONTROL + JEV field, the 60 s scalp execution window, elimination, fast qualification,
the V8 gates (CONTROL and Jev V8), the candles payload and the service switch. No network."""
from __future__ import annotations

import json

import pytest

from app.competition import v8_config as v8
from app.core.storage import Storage
from app.core.types import Signal, TakeProfit
from app.live import v6_gates as gates
from app.live.v8_worker import ForwardGateV8, JevGateV8, load_eliminated
from tests.conftest import T0

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000


class TestFreeze:
    def test_the_running_v8_is_the_frozen_manifest(self):
        man = v8.load_freeze()
        assert man is not None and v8.verify_freeze(man) == []
        assert man["fingerprint"] == v8.fingerprint(man) and len(man["field"]) == 18          # V8.3 x 6 coins x 3 roles
        assert set(man["rules"]) == {c + "USDT" for c in v8.COINS}

    def test_a_change_is_detected(self):
        man = json.loads(json.dumps(v8.load_freeze()))
        man["params"]["V8.3"]["target_r"] = 2.0
        assert any("params" in d for d in v8.verify_freeze(man))
        man = json.loads(json.dumps(v8.load_freeze()))
        man["code"]["app.live.v8_worker"] = "0" * 12
        assert any("code" in d for d in v8.verify_freeze(man))
        assert v8.verify_freeze(None) == ["docs/V8_FREEZE.json is missing: V8 is not frozen"]

    def test_identity_is_v8_and_the_jev_model_is_part_of_it(self):
        man = v8.load_freeze()
        a, ident = v8.experiment_identity(man, "typesafe/jev-1.13")
        assert a.startswith("v8x-") and a != v8.experiment_identity(man, "any/model")[0]
        assert ident["jev_model"] == "typesafe/jev-1.13" and man["profile"]["jev"]["twins"] is True


class TestFieldAndExecution:
    def test_every_control_has_a_jev_twin_and_a_ladder_twin(self):
        specs = v8.field_plan(v8.COINS)                      # V8.3 only since 2026-10-04 (V8.1 / V8.2 retired)
        assert len(specs) == 18 and sum(s.role == "JEV" for s in specs) == 6 and sum(s.role == "LADDER" for s in specs) == 6
        assert {s.strategy_id for s in specs} == {"V8.3"}
        c, j, lad = specs[0], specs[1], specs[2]
        assert c.key == "V8.3-ETH-5M" and c.timeframe == "5m" and c.symbol == "ETHUSDT" and c.role == "CONTROL"
        assert j.key == "V8.3-ETH-5M+JEV" and j.role == "JEV" and j.pair_id == c.pair_id and j.control_key == c.key
        assert lad.key == "V8.3-ETH-5M+LADDER" and lad.control_key == c.key and lad.to_dict()["key"] == lad.key
        assert [s.role for s in v8.field_plan(v8.COINS, jev=False, ladder=False)] == ["CONTROL"] * 6

    def test_v81_and_v82_are_retired_from_the_field_but_still_exist_for_v9(self):
        from app.strategies.v8.arena import MicroBreakoutV8, MicroPullbackV8, load_v8_scalpers
        from app.strategies.v9.stocks import BreakoutV9, PullbackV9
        assert v8.RETIRED == ("V8.1", "V8.2") and list(v8.load_v8()) == ["V8.3"]
        assert {"V8.1", "V8.2", "V8.3"} <= set(load_v8_scalpers())
        assert issubclass(PullbackV9, MicroPullbackV8) and issubclass(BreakoutV9, MicroBreakoutV8)
        assert v8.profile()["retired"] == ["V8.1", "V8.2"] and v8.profile()["families"] == ["V8.3"]

    def test_fills_after_the_60s_window_and_lateness_limit(self):
        e = v8.EXECUTION_V8
        assert e.signal_latency_ms + e.order_latency_ms == v8.DECISION_WINDOW_MS + 1
        assert v8.LATE_AFTER_MS < v8.DECISION_WINDOW_MS

    def test_scalp_exits(self):
        from app.strategies.v8.arena import load_v8_scalpers
        for cls in load_v8_scalpers().values():
            p = cls.Params()
            assert p.min_stop_pct >= 0.0045 and p.max_stop_pct <= 0.012 and p.target_r == 1.5
            assert p.max_hold_min == (180 if cls.id == "V8.3" else 45)          # V8.3: docs/V8_SNAP_HOLD_STUDY.json
            assert cls.for_class("SCALP").signal_tf == "5m"


class TestElimination:
    ROW = {"key": "V8.1-ETH-5M", "start_equity": 20.0, "equity": 19.5, "trades": 12, "trades_24h": 4, "live": True}

    def test_nobody_is_judged_inactive_before_a_full_day(self):
        assert v8.should_eliminate(self.ROW, 23 * HOUR) is None
        assert v8.should_eliminate(self.ROW, 24 * HOUR) == "INACTIVE_UNDER_5_TRADES_24H"
        assert v8.should_eliminate({**self.ROW, "trades_24h": 5}, 30 * HOUR) is None

    def test_a_jev_twin_is_never_eliminated_for_skipping(self):
        assert v8.should_eliminate({**self.ROW, "role": "JEV", "trades_24h": 0}, 3 * DAY) is None
        assert v8.should_eliminate({**self.ROW, "role": "JEV", "equity": 14.0}, HOUR) == "DRAWDOWN_25PCT"

    def test_a_25pct_loss_eliminates_at_any_time(self):
        assert v8.should_eliminate({**self.ROW, "equity": 15.0, "trades_24h": 30}, HOUR) == "DRAWDOWN_25PCT"
        assert v8.should_eliminate({**self.ROW, "equity": 16.0, "equity_live": 14.9, "trades_24h": 30}, HOUR) == "DRAWDOWN_25PCT"


class TestQualification:
    GOOD = {"trades": 45, "net_now": 1.2, "profit_factor": 1.4, "max_dd": 0.08, "risk_state": "OK"}

    @pytest.mark.parametrize("change,want", [({}, "QUALIFIED"), ({"trades": 39}, "ACTIVE"), ({"net_now": 0.0}, "ACTIVE"),
                                             ({"profit_factor": 1.19}, "ACTIVE"), ({"max_dd": 0.16}, "ACTIVE"),
                                             ({"risk_state": "HALTED"}, "ACTIVE")])
    def test_thresholds(self, change, want):
        assert v8.bot_status({**self.GOOD, **change}, 3 * DAY, None)[0] == want

    def test_too_young_and_eliminated(self):
        assert v8.bot_status(self.GOOD, DAY, None)[0] == "ACTIVE"
        status, detail = v8.bot_status(self.GOOD, 3 * DAY, {"at": T0, "reason": "DRAWDOWN_25PCT"})
        assert status == "ELIMINATED" and detail["reason"] == "DRAWDOWN_25PCT"


def _sig(ts):
    s = Signal("V8.1", "ETHUSDT", "entry", "long", ts, "5m", 2000.0, stop=1990.0, take_profits=[TakeProfit(2015.0, 1.0)], reason="t")
    s.meta["signal_quality"] = 0.5
    return s


def _gate(now, eliminated_at=None, recorded=None, live_from=T0):
    em = []
    g = ForwardGateV8(eliminated_at=eliminated_at or {}, late_after_ms=v8.LATE_AFTER_MS, experiment_id="v8x-t", session_id="s",
                      bot={"key": "V8.1-ETH-5M", "symbol": "ETHUSDT", "pair_id": "v8pair:V8.1-ETH-5M", "horizon": "SCALP",
                           "strategy_id": "V8.1", "timeframe": "5m"},
                      emit=lambda k, d: em.append((k, d)), board=gates.DecisionBoard(lambda s, t: (True, None)),
                      coverage=gates.Coverage([(T0, T0 + DAY)]), recorded=recorded, live_from_ms=live_from, wall=lambda: now / 1000)
    return g, em


class TestGate:
    def test_takes_a_timely_candidate(self):
        ts = T0 + 5 * MINUTE - 1
        g, em = _gate(ts + 1 + 10_000)
        assert g(_sig(ts), {"ts": ts, "sizing": {}}).multiplier == 1.0 and em[0][1]["reason"] == "CONTROL"

    def test_late_after_55s(self):
        ts = T0 + 5 * MINUTE - 1
        g, em = _gate(ts + 1 + v8.LATE_AFTER_MS + 1)
        assert g(_sig(ts), {"ts": ts, "sizing": {}}).multiplier == 0.0 and em[0][1]["reason"] == "LATE_DECISION"

    def test_an_eliminated_bot_takes_nothing_after_its_elimination(self):
        ts = T0 + 10 * MINUTE - 1
        g, em = _gate(ts + 1000, eliminated_at={"V8.1-ETH-5M": T0 + 5 * MINUTE})
        assert g(_sig(ts), {"ts": ts, "sizing": {}}).multiplier == 0.0 and em[0][1]["reason"] == "ELIMINATED"
        early = T0 + 4 * MINUTE - 1
        g, em = _gate(early + 1000, eliminated_at={"V8.1-ETH-5M": T0 + 5 * MINUTE})
        assert g(_sig(early), {"ts": early, "sizing": {}}).multiplier == 1.0

    def test_a_recorded_decision_wins_over_everything(self):
        ts = T0 + 10 * MINUTE - 1
        rec = {("V8.1-ETH-5M", ts, "long"): {"id": "d", "final_action": "TAKE", "final_level": "TAKE", "risk_multiplier": 1.0}}
        g, em = _gate(ts + 10 * HOUR, eliminated_at={"V8.1-ETH-5M": T0}, recorded=rec, live_from=T0 + 5 * HOUR)
        assert g(_sig(ts), {"ts": ts, "sizing": {}}).multiplier == 1.0 and em == []


def _jev_decision(action):
    from app.ai.jev.models import JevDecision
    probs = {"SKIP": 0.1, "TAKE": 0.1, "ATTACK": 0.1}
    probs[action] = 0.8
    return JevDecision(take_probability=1 - probs["SKIP"], risk_state=action, risk_probabilities=probs, setup_quality=0.0,
                       quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={}, model_resolved="m",
                       provider="p", request_id="r", input_tokens=10, output_tokens=5, cost_usd=0.0001)


def _candles(ts, n=80):
    from app.core.types import Candle
    return [Candle(symbol="ETHUSDT", tf="5m", open_time=ts + 1 - (n - i) * 5 * MINUTE, open=2000.0 + i,
                   high=2002.0 + i, low=1999.0 + i, close=2001.0 + i, volume=10.0 + i % 3,
                   close_time=ts - (n - 1 - i) * 5 * MINUTE) for i in range(n)]


def _jev_gate(decide, now=None, eliminated_at=None, clock=None):
    from app.ai.jev.v8 import JevStateBuilderV8
    em = []
    g = JevGateV8(eliminated_at=eliminated_at or {}, late_after_ms=v8.LATE_AFTER_MS, window_ms=v8.DECISION_WINDOW_MS,
                  margin_ms=v8.JEV_DEADLINE_MARGIN_MS, decide=decide, model="m", mid=lambda s: 2000.0,
                  spread=lambda s: (0.5, "observed"), builder=JevStateBuilderV8(), experiment_id="v8x-t", session_id="s",
                  bot={"key": "V8.1-ETH-5M+JEV", "symbol": "ETHUSDT", "pair_id": "v8pair:V8.1-ETH-5M", "horizon": "SCALP",
                       "strategy_id": "V8.1", "timeframe": "5m"},
                  emit=lambda k, d: em.append((k, d)), board=gates.DecisionBoard(lambda s, t: (True, None)),
                  coverage=gates.Coverage([(T0, T0 + DAY)]), recorded=None, live_from_ms=T0,
                  wall=clock or (lambda: now / 1000))
    return g, em


def _jev_ctx(ts):
    from types import SimpleNamespace
    return {"ts": ts, "sizing": {"tier": "TAKE", "legal_min_risk_pct": 0.004},
            "health": {"r": [], "drawdown": 0.0, "peak": 20.0}, "equity": 20.0, "start_equity": 20.0, "available": 20.0,
            "taker_fee": 0.00055, "half_spread_bps": 0.5, "trades": [], "candles": _candles(ts),
            "decision": SimpleNamespace(risk_usd=0.2, leverage=2.0, notional=40.0)}


class TestJevGate:
    TS = T0 + 10 * HOUR - 1

    def test_jev_can_skip_take_or_attack_the_controls_candidate(self):
        from app.ai.jev.models import JevOutcome
        seen = []
        for action, want in (("SKIP", 0.0), ("TAKE", 1.0)):
            g, em = _jev_gate(lambda st, a=action: seen.append(st) or JevOutcome(True, _jev_decision(a), latency_ms=900),
                              now=self.TS + 3000)
            v = g(_sig(self.TS), _jev_ctx(self.TS))
            assert v.multiplier == want and em[0][1]["final_level"] == action
            assert em[0][1]["prompt_version"] == "JEV_PROMPT_V8_SCALP"
        g, em = _jev_gate(lambda st: JevOutcome(True, _jev_decision("ATTACK"), latency_ms=900), now=self.TS + 3000)
        assert g(_sig(self.TS), _jev_ctx(self.TS)).multiplier == pytest.approx(2.0)   # 2% of 20 USDT / 0.20 risk
        st = seen[0]
        assert st["state_version"] == "JEV_STATE_V8" and st["setup"]["max_hold_minutes"] == 45
        assert len(st["price_action"]["last_hour_5m"]) == 12 and "5m" in st["trend"]["ladder"]

    def test_an_answer_after_h_plus_55s_is_skip(self):
        from app.ai.jev.models import JevOutcome
        t = {"now": self.TS + 2000}

        def slow(st):
            t["now"] = self.TS + 1 + v8.DECISION_WINDOW_MS - v8.JEV_DEADLINE_MARGIN_MS + 500
            return JevOutcome(True, _jev_decision("TAKE"), latency_ms=50_000)
        g, em = _jev_gate(slow, clock=lambda: t["now"] / 1000)
        v = g(_sig(self.TS), _jev_ctx(self.TS))
        assert v.multiplier == 0.0 and em[0][1]["error_code"] == "DEADLINE" and em[0][1]["timed_out"] == 1

    def test_a_failure_is_skip_and_an_eliminated_twin_asks_nothing(self):
        from app.ai.jev.models import JevOutcome
        g, em = _jev_gate(lambda st: JevOutcome(False, error_code="TIMEOUT"), now=self.TS + 3000)
        assert g(_sig(self.TS), _jev_ctx(self.TS)).multiplier == 0.0 and em[0][1]["final_level"] == "SKIP"
        g, em = _jev_gate(lambda st: pytest.fail("an eliminated twin never asks Jev"), now=self.TS + 3000,
                          eliminated_at={"V8.1-ETH-5M+JEV": T0})
        assert g(_sig(self.TS), _jev_ctx(self.TS)).multiplier == 0.0 and em[0][1]["reason"] == "ELIMINATED"

    def test_the_state_never_sees_the_future(self):
        from app.ai.jev.state import LookAheadError
        from app.ai.jev.v8 import JevStateBuilderV8
        with pytest.raises(LookAheadError):
            JevStateBuilderV8().build(t=self.TS + 1, bot={}, sig=_sig(self.TS), series={"5m": _candles(self.TS + 5 * MINUTE)},
                                      execution={}, sizing={}, health={}, position={})


class TestPayloads:
    def test_candles_aggregate_and_mark_trades(self, tmp_path):
        from app.core.arena_live_view import candles
        st = Storage(str(tmp_path / "c.db"))
        st.fwd6_bars_save([{"symbol": "ETHUSDT", "open_time": T0 + i * MINUTE, "open": 100 + i, "high": 101 + i, "low": 99 + i,
                            "close": 100.5 + i, "volume": 1.0, "source": "live"} for i in range(10)])
        st.fwd6_trade_save("v8x-c", "s", {"bot_key": "V8.1-ETH-5M", "symbol": "ETHUSDT", "side": "long", "entry_ts": T0 + MINUTE,
                                          "exit_ts": T0 + 7 * MINUTE, "entry_price": 101, "exit_price": 106, "net": 0.2,
                                          "counterfactual": False, "role": "CONTROL"})
        d = candles(st, {"experiment_id": "v8x-c"}, "ETHUSDT", "5m", 10, now_ms=T0 + 10 * MINUTE)
        assert [c[0] for c in d["candles"]] == [T0, T0 + 5 * MINUTE]
        assert d["candles"][0][1:5] == [100, 105, 99, 104.5] and d["candles"][0][5] == 5.0
        assert [m["kind"] for m in d["markers"]] == ["entry", "exit"]
        assert candles(st, None, "ETHUSDT", "7m")["ok"] is False
        st.close()

    def test_position_boxes_carry_stop_and_target_and_merge_a_pair(self, tmp_path):
        from app.core.arena_live_view import candles
        st = Storage(str(tmp_path / "b.db"))
        st.fwd6_bars_save([{"symbol": "ETHUSDT", "open_time": T0 + i * MINUTE, "open": 100.0, "high": 101.0, "low": 99.0,
                            "close": 100.5, "volume": 1.0, "source": "live"} for i in range(10)])
        st.fwd6_event_add("v8x-b", "s", T0 + MINUTE + 5, "open", {"bot_key": "V8.1-ETH-5M", "side": "long", "ts": T0 + MINUTE,
                                                                  "position_id": "p1", "stop": 100.0, "target": 102.5, "qty": 1})
        for key, pid, net in (("V8.1-ETH-5M", "p1", 0.2), ("V8.1-ETH-5M+JEV", "p9", 0.4)):
            st.fwd6_trade_save("v8x-b", "s", {"bot_key": key, "symbol": "ETHUSDT", "side": "long", "entry_ts": T0 + MINUTE,
                                              "exit_ts": T0 + 7 * MINUTE, "entry_price": 101.0, "exit_price": 102.5, "net": net,
                                              "stop_pct": 0.0099, "target_r": 1.5, "position_id": pid, "counterfactual": False})
        box, = candles(st, {"experiment_id": "v8x-b"}, "ETHUSDT", "5m", 10, now_ms=T0 + 10 * MINUTE)["positions"]
        assert (box["entry"], box["stop"], box["target"], box["exit"]) == (101.0, 100.0, 102.5, 102.5)
        assert box["bots"] == ["V8.1-ETH-5M", "V8.1-ETH-5M+JEV"] and box["net"] == [0.2, 0.4]
        st.close()

    def test_eliminations_are_recorded_and_reloaded(self, tmp_path):
        st = Storage(str(tmp_path / "e.db"))
        st.fwd6_event_add("v8x-e", "s", T0 + DAY, "eliminated", {"bot_key": "V8.2-ENA-5M", "at": T0 + DAY, "reason": "INACTIVE_UNDER_5_TRADES_24H"})
        assert load_eliminated(st, "v8x-e") == {"V8.2-ENA-5M": {"at": T0 + DAY, "reason": "INACTIVE_UNDER_5_TRADES_24H"}}
        st.close()

    def test_service_is_off_by_default_and_routes_are_get_only(self, tmp_path):
        from app.core import api_public
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "x.db"), env={}, program="V8")
        svc.start()
        assert svc.enabled is False and svc.health()["status"] == "DISABLED"
        assert V6ForwardService(str(tmp_path / "x.db"), env={"V8_FORWARD_ENABLED": "true"}, program="V8").enabled
        routes = [r for r in api_public.router.routes if r.path.endswith("/v8") or r.path.endswith("/candles")]
        assert len(routes) == 2 and all(set(r.methods) <= {"GET", "HEAD"} for r in routes)


class TestLadder:
    def _pos(self, side="long"):
        from app.core.types import TakeProfit, VirtualPosition
        from app.strategies.v8.ladder import LADDER
        d = 1 if side == "long" else -1
        entry, stop = 100.0, 100.0 - d * 1.0                                  # R = 1.0 price unit
        return VirtualPosition(id="p", strategy_id="V8.1", symbol="ETHUSDT", side=side, qty=3.0, qty_initial=3.0,
                               entry_price=entry, entry_ts=T0, leverage=5, margin=60.0, stop=stop,
                               take_profits=[TakeProfit(entry + d * r, f) for r, f in LADDER], trail=None, be_at_r=LADDER[0][0],
                               max_hold_deadline=None, initial_risk_usd=3.0, extreme_price=entry)

    def test_signals_carry_the_ladder(self):
        from app.strategies.v8.arena import load_v8_scalpers
        from app.strategies.v8.ladder import LADDER, ladder_class

        class Fake(load_v8_scalpers()["V8.1"]):
            def on_candle(self, c, ctx):
                return [_sig(T0)]
        s = ladder_class(Fake)().on_candle(None, None)[0]
        assert [(round(tp.price, 4), round(tp.fraction, 4)) for tp in s.take_profits] == [(2007.5, 0.3333), (2015.0, 0.3333), (2025.0, 0.3333)]
        assert s.be_at_r == LADDER[0][0] and s.meta["exits"] == "LADDER"

    @pytest.mark.parametrize("side", ["long", "short"])
    def test_tp1_moves_the_stop_to_entry_plus_costs_and_tp2_locks_profit(self, side):
        from app.core.positions import ExitEngine
        from app.strategies.v8.arena import load_v8_scalpers
        from app.strategies.v8.ladder import COST_LOCK, ladder_class
        d = 1 if side == "long" else -1
        pos, ex = self._pos(side), ExitEngine({})
        strat = ladder_class(load_v8_scalpers()["V8.1"])()
        assert strat.manage(pos, None, None) is None                          # nothing before TP1
        intents = ex.on_price(pos, 100.0 + d * 0.8, None, T0 + 60_000)        # TP1 prints
        assert intents[0].kind == "tp" and round(intents[0].fraction, 4) == 0.3333
        pos.qty = 2.0                                                          # the engine closed 1/3
        ex.apply_update(pos, strat.manage(pos, None, None), 100.0 + d * 0.8)
        assert pos.stop == pytest.approx(100.0 * (1 + d * COST_LOCK))
        ex.on_price(pos, 100.0 + d * 1.6, None, T0 + 120_000)                 # TP2 prints
        pos.qty = 1.0
        ex.apply_update(pos, strat.manage(pos, None, None), 100.0 + d * 1.6)
        assert pos.stop == pytest.approx(100.0 + d * 0.75)                    # +0.75R locked
        assert strat.manage(pos, None, None) is None                          # never moves back

    def test_ladder_bots_cannot_go_live_yet(self, tmp_path):
        import asyncio
        from app.live.mirror import MirrorError, MirrorService
        from app.live.providers import Providers
        st = Storage(str(tmp_path / "m.db"))
        svc = MirrorService(st, Providers({}), lambda prog, key: {"key": key, "program_status": "QUALIFIED"})
        with pytest.raises(MirrorError, match="LADDER"):
            asyncio.run(svc.arm({"program": "v8", "bot_key": "V8.1-ETH-5M+LADDER", "exchange": "bybit", "network": "testnet",
                                 "amount_usdt": 50, "risk_pct": 0.01, "max_daily_loss": 5, "max_total_loss": 10,
                                 "confirm": "GO LIVE V8.1-ETH-5M+LADDER"}))
        st.close()


class TestCostSettings:
    """2026-10-02 (scripts/v8_cost_study.py, docs/V8_COST_STUDY.json): wider minimum stops, V8.1 / V8.2 held to the
    stop, the target or the UTC day close, and the target resting as a LIMIT order filled at its level."""

    def test_family_settings(self):
        from app.strategies.v8.arena import MicroBreakoutV8, MicroPullbackV8, VwapSnapV8
        p1, p2, p3 = MicroPullbackV8.Params(), MicroBreakoutV8.Params(), VwapSnapV8.Params()
        assert (p1.min_stop_pct, p1.hold_to_day_close) == (0.010, True)
        assert (p2.min_stop_pct, p2.hold_to_day_close) == (0.012, True)
        assert (p3.min_stop_pct, p3.hold_to_day_close, p3.max_hold_min) == (0.012, False, 180.0)
        assert all(p.max_stop_pct == 0.012 and p.target_r == 1.5 for p in (p1, p2, p3))

    def test_the_target_is_a_resting_limit_filled_at_its_level(self):
        from app.core.types import Candle, MarketRules
        from app.live.v8_engine import LevelMakerEngineV8
        from app.strategies.v8.arena import VwapSnapV8
        T0 = 1_790_000_000_000 // 300_000 * 300_000

        class OneShot(VwapSnapV8.for_class("SCALP")):
            def on_candle(self, c, ctx):
                if c.tf != "5m" or c.close_time != T0 + 10 * 60_000 - 1:
                    return []
                return [self.make_entry(symbol="ETHUSDT", side="long", ts=c.close_time, tf="5m", price=c.close,
                                        stop=c.close * 0.988, tps_r=[(1.5, 1.0)])]
        rules = {"ETHUSDT": MarketRules("ETHUSDT", 0.01, 0.01, 0.01, 5.0)}
        eng = LevelMakerEngineV8(v8.settings_v8() if hasattr(v8, "settings_v8") else __import__(
            "app.competition.v6_config", fromlist=["x"]).settings_v6(), ["ETHUSDT"], rules=rules, seed=7,
            execution=v8.EXECUTION_V8, maker_tp=True)

        def tape():
            for m in range(40):
                t = T0 + m * 60_000
                hi = 102.5 if m == 20 else 100.05                    # the target: 100 + 1.5 x 1.2% = 101.8
                yield Candle("ETHUSDT", "1m", t, 100.0, hi, 99.95, 100.0, 5.0, t + 59_999)
        res = eng.run(OneShot, tape(), since_ms=T0, leverage=20, signal_tf="5m", only_symbol="ETHUSDT")
        tp = [f for f in res.fills if f.kind == "tp"]
        assert len(tp) == 1 and tp[0].price == pytest.approx(101.8, abs=1e-6)          # at the target, not the high
        assert tp[0].fee == pytest.approx(tp[0].qty * 101.8 * 0.0002, rel=1e-6)         # maker fee



def test_v83_has_its_own_inactivity_bar_after_the_3_hour_hold():
    from app.competition import v8_config as v8
    HOUR = 3_600_000
    row = {"key": "V8.3-ETH-5M", "strategy_id": "V8.3", "role": "CONTROL", "trades_24h": 3, "equity": 20.0,
           "start_equity": 20.0}
    assert v8.should_eliminate(row, 30 * HOUR) is None                      # 3 trades: fine for V8.3 (bar 2)
    assert v8.should_eliminate({**row, "trades_24h": 1}, 30 * HOUR) == "INACTIVE_UNDER_2_TRADES_24H"
    other = {**row, "key": "V8.1-ETH-5M", "strategy_id": "V8.1"}
    assert v8.should_eliminate(other, 30 * HOUR) == "INACTIVE_UNDER_5_TRADES_24H"
    assert v8.min_trades_24h({"key": "V8.3-SOL-5M+LADDER"}) == 2              # from the key when no strategy_id
