"""Jev decision layer: secrecy, failure handling, no look-ahead, reproducibility, and that the
+JEV bot differs from its control ONLY through Jev's decisions."""
from __future__ import annotations

import json
import logging
import socket

import pytest

from app.ai.jev.client import JevClient, sanitize
from app.ai.jev.gate import CachedDecider, GateVerdict, JevGate, ReproducibilityError, cache_key
from app.ai.jev.models import (QUESTIONS_V1, JevConfig, JevDecision, JevOutcome, SchemaError,
                               parse_decision)
from app.ai.jev.policy import POLICY_V1, JevPolicyConfig, decide
from app.ai.jev.state import JevStateBuilder, LookAheadError, market_features
from app.core.storage import Storage
from app.core.types import Signal, TakeProfit
from tests.conftest import T0, make_candles, settings_factory

KEY = "sk-or-v1-" + "a1b2c3d4" * 8          # looks like a real key; must never surface anywhere
ON = JevConfig(enabled=True, timeout_ms=1000, max_retries=2, backoff_ms=10)


def ok_body(take=0.8, risk="NORMAL", score=2.0, regime="RANGE", model="typesafe/jev-1.13-20260917"):
    return {"id": "gen-dec-1", "model": model, "provider": "TypeSafe",
            "answers": {
                "take": {"type": "noul", "noul": take},
                "risk_state": {"type": "choice", "choice": risk, "confidence": 0.7,
                               "probabilities": {"ATTACK": 0.1, "NORMAL": 0.6, "DEFENSIVE": 0.2, "SKIP": 0.1}},
                "setup_quality": {"type": "score", "score": score, "confidence": 0.8,
                                  "probabilities": {"0": 0.0, "1": 0.1, "2": 0.8, "3": 0.1, "4": 0.0},
                                  "legend": {}},
                "regime": {"type": "choice", "choice": regime, "confidence": 0.9,
                           "probabilities": {"TREND_UP": 0.05, "TREND_DOWN": 0.05, "RANGE": 0.8,
                                             "HIGH_VOL": 0.05, "ABNORMAL": 0.05}}},
            "usage": {"input_tokens": 480, "output_tokens": 70, "cost": 0.0000201}}


class FakeTransport:
    """Scripted responses. Records every request so tests can inspect what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, body, headers, timeout_s):
        self.requests.append({"url": url, "body": json.loads(body), "headers": dict(headers),
                              "timeout": timeout_s})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, BaseException):
            raise r
        status, payload, *hdrs = r
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, (hdrs[0] if hdrs else {}), raw


def client(transport, cfg=ON, key=KEY):
    return JevClient(cfg, key, transport=transport, sleep=lambda s: None)


# ---- the client --------------------------------------------------------------------------------

class TestClient:
    def test_request_matches_the_decisions_api(self):
        t = FakeTransport((200, ok_body()))
        out = client(t).decide({"x": 1})
        req = t.requests[0]
        assert req["url"] == "https://openrouter.ai/api/alpha/decisions"
        assert req["body"]["model"] == "typesafe/jev-1.13"
        assert req["body"]["state"] == {"x": 1}
        assert set(req["body"]["questions"]) == {"take", "risk_state", "setup_quality", "regime"}
        assert {q["type"] for q in req["body"]["questions"].values()} == {"noul", "choice", "score"}
        assert req["headers"]["Authorization"] == "Bearer " + KEY
        assert out.ok and out.decision.take_probability == 0.8
        assert out.decision.model_resolved == "typesafe/jev-1.13-20260917"
        assert out.decision.input_tokens == 480 and out.decision.cost_usd == pytest.approx(0.0000201)

    def test_missing_key_is_not_configured_and_makes_no_request(self):
        t = FakeTransport((200, ok_body()))
        out = client(t, key="").decide({"x": 1})
        assert out.error_code == "NOT_CONFIGURED" and t.requests == []

    def test_kill_switch_makes_no_request(self):
        t = FakeTransport((200, ok_body()))
        out = client(t, cfg=JevConfig(enabled=False)).decide({"x": 1})
        assert out.error_code == "DISABLED" and t.requests == []

    def test_rate_limit_is_retried_with_backoff(self):
        waits = []
        t = FakeTransport((429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "2"}),
                          (200, ok_body()))
        c = JevClient(ON, KEY, transport=t, sleep=waits.append)
        out = c.decide({"x": 1})
        assert out.ok and out.attempts == 2 and waits and waits[0] >= 2.0

    def test_server_errors_stop_after_the_retry_budget(self):
        t = FakeTransport((502, {"error": {"code": 502, "message": "Provider returned error"}}))
        out = client(t).decide({"x": 1})
        assert out.error_code == "SERVER" and out.attempts == 3 and len(t.requests) == 3

    @pytest.mark.parametrize("status,code", [(401, "AUTH"), (402, "CREDITS"), (400, "BAD_REQUEST"),
                                             (404, "MODEL_UNAVAILABLE"), (413, "PAYLOAD_TOO_LARGE")])
    def test_permanent_errors_are_not_retried(self, status, code):
        t = FakeTransport((status, {"error": {"code": status, "message": "no"}}))
        out = client(t).decide({"x": 1})
        assert out.error_code == code and len(t.requests) == 1

    def test_timeouts_and_network_failures_are_coded(self):
        assert client(FakeTransport(socket.timeout())).decide({}).error_code == "TIMEOUT"
        assert client(FakeTransport(ConnectionResetError())).decide({}).error_code == "NETWORK"

    def test_invalid_json_and_bad_schema_are_errors_not_guesses(self):
        assert client(FakeTransport((200, b"<html>"))).decide({}).error_code == "INVALID_JSON"
        body = ok_body()
        del body["answers"]["regime"]
        assert client(FakeTransport((200, body))).decide({}).error_code == "MISSING_ANSWER"
        body = ok_body(take=1.7)
        assert client(FakeTransport((200, body))).decide({}).error_code == "SCHEMA_MISMATCH"
        body = ok_body(risk="YOLO")
        assert client(FakeTransport((200, body))).decide({}).error_code == "SCHEMA_MISMATCH"

    def test_the_key_never_leaves_in_errors_repr_or_logs(self, caplog):
        caplog.set_level(logging.DEBUG)
        t = FakeTransport((401, {"error": {"code": 401, "message": f"bad key {KEY} Bearer {KEY}"}}))
        c = client(t)
        out = c.decide({"x": 1})
        assert KEY not in out.error_message and KEY not in json.dumps(out.to_dict())
        assert KEY not in repr(c) and KEY not in str(c) and KEY not in repr(vars(c))
        assert KEY not in caplog.text

    def test_sanitize_strips_anything_key_shaped(self):
        assert "sk-or-" not in sanitize("oops sk-or-v1-deadbeefcafe")
        assert "abc.def" not in sanitize("Authorization: Bearer abc.def")

    def test_smoke_reports_only_non_secret_facts(self):
        t = FakeTransport((200, {"id": "g", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
                                 "answers": {"ok": {"type": "noul", "noul": 0.99}},
                                 "usage": {"input_tokens": 60, "output_tokens": 5, "cost": 0.0000025}}))
        check = client(t).smoke()
        assert check["ok"] and check["model_resolved"].startswith("typesafe/jev-1.13")
        assert KEY not in json.dumps(check)


class TestParsing:
    def test_quality_is_scaled_to_unit_interval(self):
        d = parse_decision(ok_body(score=3.0))
        assert d.setup_quality == pytest.approx(0.75) and d.quality_score == 3.0

    def test_unknown_probability_keys_are_rejected(self):
        body = ok_body()
        body["answers"]["risk_state"]["probabilities"]["LEVERAGE_MAX"] = 0.5
        with pytest.raises(SchemaError):
            parse_decision(body)


# ---- policy ------------------------------------------------------------------------------------

def dec(take, risk="ATTACK"):
    return JevDecision(take, risk, {}, 0.5, 2.0, {}, "RANGE", {})


class TestPolicy:
    @pytest.mark.parametrize("p,level", [(0.10, "SKIP"), (0.549, "SKIP"), (0.55, "DEFENSIVE"),
                                         (0.69, "DEFENSIVE"), (0.70, "NORMAL"), (0.849, "NORMAL"),
                                         (0.85, "ATTACK"), (0.99, "ATTACK")])
    def test_probability_thresholds(self, p, level):
        assert decide(dec(p)).level == level

    def test_multipliers(self):
        assert [decide(dec(p)).multiplier for p in (0.2, 0.6, 0.75, 0.9)] == [0.0, 0.5, 1.0, 1.5]
        assert [decide(dec(p)).action for p in (0.2, 0.6, 0.75, 0.9)] == ["SKIP", "REDUCE", "TAKE", "TAKE"]

    def test_risk_state_can_only_reduce(self):
        assert decide(dec(0.95, "NORMAL")).level == "NORMAL"
        assert decide(dec(0.95, "SKIP")).level == "SKIP"
        assert decide(dec(0.60, "ATTACK")).level == "DEFENSIVE", "a confident risk_state cannot lift a weak probability"

    def test_any_error_is_skip_not_control_behaviour(self):
        r = decide(None, error_code="TIMEOUT")
        assert r.action == "SKIP" and r.multiplier == 0.0 and "JEV_ERROR" in r.reason

    def test_a_new_threshold_is_a_new_policy_version(self):
        assert JevPolicyConfig(skip_below=0.6, version="JEV_POLICY_V2").fingerprint() != POLICY_V1.fingerprint()


# ---- the state -------------------------------------------------------------------------------------

def sig(entry=100.0, stop=99.0, tp=103.0, side="long"):
    return Signal("S02", "SOLUSDT", "entry", side, T0, "5m", entry, stop=stop,
                  take_profits=[TakeProfit(tp, 1.0)], reason="rsi extreme")


def build(candles, ts, funding=(), **kw):
    return JevStateBuilder().build(
        ts=ts, bot={"strategy_id": "S02", "strategy_name": "RSI", "params_version": "v1",
                    "symbol": "SOLUSDT", "timeframe": "5m"},
        sig=kw.get("signal", sig()), candles=candles,
        execution={"taker_fee": 0.0005, "half_spread_bps": 0.4, "expected_slippage_bps": 0.5},
        health={"drawdown": 0.0}, position={"open_positions": 0}, funding=funding)


class TestStateHasNoLookAhead:
    def candles(self, n=80):
        return make_candles(n=n, symbol="SOLUSDT", tf="5m", start_ts=T0, seed=3)

    def test_a_future_candle_is_refused(self):
        cs = self.candles()
        ts = cs[59].close_time
        with pytest.raises(LookAheadError):
            build(cs, ts)                              # bars 60.. close after the decision
        build(cs[:60], ts)                             # exactly what existed is fine

    def test_future_funding_is_refused(self):
        cs = self.candles(60)
        ts = cs[-1].close_time
        with pytest.raises(LookAheadError):
            build(cs, ts, funding=[(ts + 1, 0.0001)])

    def test_adding_future_data_cannot_change_a_past_decision(self):
        cs = self.candles(80)
        ts = cs[59].close_time
        a = build(cs[:60], ts)
        b = build([c for c in cs if c.close_time <= ts], ts)
        assert a.fingerprint == b.fingerprint

    def test_the_fingerprint_is_deterministic_and_sensitive(self):
        cs = self.candles(60)
        ts = cs[-1].close_time
        assert build(cs, ts).fingerprint == build(cs, ts).fingerprint
        assert build(cs, ts).fingerprint != build(cs, ts, signal=sig(stop=98.0)).fingerprint

    def test_no_dates_no_absolute_prices_no_outcomes(self):
        cs = make_candles(closes=[123.4567 * (1.001 ** i) for i in range(60)], symbol="SOLUSDT",
                          tf="5m", start_ts=T0)
        ts = cs[-1].close_time
        snap = build(cs, ts, signal=sig(entry=cs[-1].close, stop=cs[-1].close * 0.99,
                                        tp=cs[-1].close * 1.03))
        text = json.dumps(snap.state)
        assert str(T0) not in text and str(ts) not in text
        assert "2025" not in text and "2026" not in text
        assert f"{cs[-1].close:.2f}" not in text, "absolute price levels must not be sent"
        for banned in ("outcome", "pnl", "future", "control", "leaderboard", "qualif", "won"):
            assert banned not in text.lower(), banned

    def test_the_state_is_small(self):
        cs = self.candles(200)
        snap = build(cs, cs[-1].close_time)
        assert snap.size_bytes() < 3000
        assert snap.meta["bars_used"] == 120

    def test_market_features_use_only_the_bars_given(self):
        cs = self.candles(60)
        f = market_features(cs)
        assert f["bars"] == 60 and f["ret_1"] == pytest.approx(cs[-1].close / cs[-2].close - 1, abs=1e-5)


# ---- ledger, cache, budget --------------------------------------------------------------------------

class CountingDecider:
    def __init__(self, out=None):
        self.calls = 0
        self.out = out or JevOutcome(True, decision=dec(0.75, "NORMAL"))

    def decide(self, state):
        self.calls += 1
        return self.out


class _Snap:
    fingerprint = "fp"
    state = {"a": 1}


class TestCacheAndLedger:
    def test_cache_hit_makes_no_call(self, tmp_path):
        st = Storage(tmp_path / "c.db")
        inner = CountingDecider()
        a = CachedDecider(st, inner, "run1", "botA", "typesafe/jev-1.13")
        out1, src1 = a.decide("k1", _Snap())
        out2, src2 = CachedDecider(st, inner, "run2", "botA", "typesafe/jev-1.13").decide("k1", _Snap())
        assert (src1, src2) == ("api", "cache") and inner.calls == 1
        assert out2.decision.take_probability == 0.75

    def test_errors_are_not_cached(self, tmp_path):
        st = Storage(tmp_path / "c.db")
        inner = CountingDecider(JevOutcome(False, error_code="TIMEOUT"))
        CachedDecider(st, inner, "run1", "botA", "m").decide("k1", _Snap())
        CachedDecider(st, inner, "run2", "botA", "m").decide("k1", _Snap())
        assert inner.calls == 2

    def test_replay_only_refuses_to_call(self, tmp_path):
        st = Storage(tmp_path / "c.db")
        with pytest.raises(ReproducibilityError):
            CachedDecider(st, CountingDecider(), "run1", "botA", "m", replay_only=True).decide("k9", _Snap())

    def test_budget_stops_calls(self, tmp_path):
        st = Storage(tmp_path / "c.db")
        st.save_jev_decision({"id": "d1", "run_id": "run1", "bot_key": "b", "cache_key": "x",
                              "source": "api", "cost_usd": 0.5})
        inner = CountingDecider()
        out, src = CachedDecider(st, inner, "run1", "botA", "m", max_calls=1).decide("k1", _Snap())
        assert out.error_code == "BUDGET" and inner.calls == 0

    def test_cache_key_covers_what_changes_the_answer(self):
        base = dict(model="typesafe/jev-1.13", bot_version="v:1", strategy_id="S02", params_version="v1",
                    symbol="SOLUSDT", timeframe="5m", signal_ts=1, state_fingerprint="fp")
        k = cache_key(**base)
        for field_, val in (("model", "typesafe/jev-latest"), ("signal_ts", 2), ("state_fingerprint", "fp2"),
                            ("symbol", "ETHUSDT"), ("params_version", "v2"), ("prompt_version", "JEV_PROMPT_V2")):
            assert cache_key(**{**base, field_: val}) != k, field_


# ---- the replay: +JEV differs from CONTROL only through Jev ----------------------------------------

from tests.test_competition import OneShot, big_rules, rising  # noqa: E402


def run(gate=None, cls=OneShot, bars=None, max_risk=None):
    from app.backtest.replay import ReplayEngine
    eng = ReplayEngine(settings_factory(balance=20), ["BTCUSDT"], rules=big_rules(), seed=1,
                       gate=gate, max_risk_pct=max_risk)
    return eng.run(cls, bars or rising(), leverage=10)


class FixedGate:
    def __init__(self, m):
        self.m = m
        self.calls = []

    def __call__(self, sig, g):
        self.calls.append(g)
        return GateVerdict(self.m, {"decision_id": f"d{len(self.calls)}"})


class TestReplayGate:
    def test_a_pass_through_gate_reproduces_the_control_exactly(self):
        control = run()
        jev = run(FixedGate(1.0))
        assert [(t.entry_price, t.exit_price, t.net) for t in jev.trades] == \
               [(t.entry_price, t.exit_price, t.net) for t in control.trades]
        assert jev.equity == control.equity

    def test_skip_trades_nothing_but_shadows_the_candidate(self):
        control = run()
        jev = run(FixedGate(0.0))
        assert jev.trades == [] and control.trades
        assert len(jev.shadow_trades) == len(control.trades)
        assert jev.shadow_trades[0].net == pytest.approx(control.trades[0].net, rel=1e-6)
        assert jev.gate_events[0]["result"] == "SKIPPED"
        assert jev.shadow_links and set(jev.shadow_links.values()) == {"d1"}

    def test_reduce_halves_the_risk_through_the_risk_manager(self):
        control, jev = run(), run(FixedGate(0.5))
        assert jev.trades[0].qty == pytest.approx(control.trades[0].qty * 0.5, rel=0.02)

    def test_the_gate_sees_only_the_past(self):
        gate = FixedGate(1.0)
        run(gate)
        g = gate.calls[0]
        assert all(c.close_time <= g["ts"] for c in g["candles"])
        assert all(ts <= g["ts"] for ts, _ in g["funding"])

    def test_attack_cannot_exceed_the_hard_ceiling(self):
        jev = run(FixedGate(1.5), max_risk=0.02)
        fill = next(f for f in jev.fills if f.kind == "entry")
        risk = fill.qty * abs(fill.price - fill.meta["stop"])
        assert risk <= 0.02 * 20.0 * 1.01


# ---- the service and its routes ------------------------------------------------------------------

class TestService:
    def test_boot_without_a_key_is_not_configured(self, monkeypatch, tmp_path):
        from app.ai.jev.service import JevService
        svc = JevService.from_env(env={"JEV_ENABLED": "true"})
        assert svc.health()["status"] == "NOT_CONFIGURED"

    def test_disabled_is_reported(self):
        from app.ai.jev.service import JevService
        svc = JevService.from_env(env={"OPENROUTER_API_KEY": KEY, "JEV_ENABLED": "false"})
        h = svc.health()
        assert h["status"] == "DISABLED" and KEY not in json.dumps(h)

    def test_pinned_model_by_default(self):
        assert JevConfig.from_env({}).model == "typesafe/jev-1.13"
        assert JevConfig.from_env({"JEV_MODEL": "typesafe/jev-1.13"}).model == "typesafe/jev-1.13"


class TestRoutes:
    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from app.core import engine_boot
        from tests.conftest import FakeFeed
        monkeypatch.setattr(engine_boot, "MarketFeed", FakeFeed)
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
        monkeypatch.setenv("JEV_ENABLED", "false")
        from app.main import create_app
        app = create_app(settings_factory(data_dir=str(tmp_path / "d")))
        with TestClient(app) as c:
            yield c

    def test_operator_routes_need_auth(self, app_client):
        assert app_client.get("/api/jev/health").status_code == 401
        assert app_client.post("/api/jev/smoke", headers={"X-PaperLab": "1"}).status_code == 401
        assert app_client.post("/api/jev/decide", json={"state": {"a": 1}},
                               headers={"X-PaperLab": "1"}).status_code == 401

    def test_public_health_is_sanitized(self, app_client):
        r = app_client.get("/api/public/competition/jev/health")
        assert r.status_code == 200 and r.json()["health"]["status"] == "DISABLED"
        assert KEY not in r.text and "sk-or-" not in r.text

    def test_authenticated_decide_respects_the_kill_switch(self, app_client):
        r = app_client.post("/api/jev/decide", json={"state": {"a": 1}, "prompt_version": "JEV_PROMPT_V1"},
                            headers={"X-PaperLab": "1"}, auth=("admin", "test-pw"))
        assert r.status_code == 200 and r.json()["outcome"]["error_code"] == "DISABLED"
        assert KEY not in r.text

    def test_nothing_public_carries_the_key(self, app_client):
        for path in ("/api/public/competition/jev/health", "/api/public/competition",
                     "/public/competition/report", "/public/competition/report.txt",
                     "/api/public/competition/arena"):
            body = app_client.get(path).text
            assert KEY not in body and "sk-or-v1-" not in body, path
