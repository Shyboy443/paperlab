"""V6 FORWARD ARENA: frozen identity, features under watermarks, AGGRESSIVE_V6 legal sizing, Jev V6, the live gates
(MISSED / LATE / STALE / recorded replay), the Bybit feed's parsing and funding freeze, the runner end to end with the
real engine and execution model, redeploy continuity (the book is re-derived, the open position is carried, nothing is
recorded twice), storage, the service switch and the public payloads.

No network: the market is fed by hand, Jev is a stub."""
from __future__ import annotations

import dataclasses
import json
import queue
import time

import pytest

from app.ai.jev.models import JevDecision, JevOutcome, SchemaError
from app.competition import v6_config as cfg
from app.competition.v6_features import LiveSeries, MarketContextV6, PositioningFeedV6, regime
from app.core.storage import Storage
from app.core.types import Candle, MarketRules, Signal, TakeProfit
from app.live import v6_gates as gates
from app.strategies.base import P, Strategy
from tests.conftest import T0

HOUR = 3_600_000
MINUTE = 60_000
DAY = 24 * HOUR
SYM = "ENAUSDT"


# ---- the frozen identity -------------------------------------------------------------------------------------------

class TestFreeze:
    def test_the_running_v6_is_exactly_the_frozen_manifest(self):
        man = cfg.load_freeze()
        assert man is not None, "docs/V6_FREEZE.json is missing"
        assert cfg.verify_freeze(man) == [], "V6 changed after it was frozen: re-freeze it as a NEW experiment"
        assert man["protocol"] == cfg.PROTOCOL_VERSION and man["fingerprint"] == cfg.manifest_fingerprint(man)
        assert len(man["universe"]["traded"]) == cfg.TRADED_COINS and "BTCUSDT" in man["breadth_set"]

    def test_any_change_is_detected(self):
        man = json.loads(json.dumps(cfg.load_freeze()))
        man["params"]["V6.1"]["touch_atr"] = 0.61                      # a "small tweak" is still a new V6
        diffs = cfg.verify_freeze(man)
        assert any("parameters" in d for d in diffs) and any("fingerprint" in d for d in diffs)
        man = json.loads(json.dumps(cfg.load_freeze()))
        man["code"]["source:app.live.v6_gates"] = "000000000000"
        assert any("app.live.v6_gates" in d for d in cfg.verify_freeze(man))
        assert cfg.verify_freeze(None) == ["docs/V6_FREEZE.json is missing: V6 is not frozen"]

    def test_the_experiment_resumes_only_under_an_identical_identity(self):
        man = cfg.load_freeze()
        a, ident = cfg.experiment_identity(man, "typesafe/jev-1.13")
        assert a == cfg.experiment_identity(man, "typesafe/jev-1.13")[0] and a.startswith("v6x-")
        assert ident["venue"] == "BYBIT_LINEAR" and ident["manifest"] == man["fingerprint"]
        assert cfg.experiment_identity(man, "another/model")[0] != a            # another Jev = another experiment
        other = {**man, "fingerprint": "0" * 16}
        assert cfg.experiment_identity(other, "typesafe/jev-1.13")[0] != a

    def test_the_field_is_one_bot_one_coin_with_matched_twins(self):
        specs = cfg.field_plan(["ARB", "ENA", "XRP", "DOGE"])
        controls = [s for s in specs if s.role == "CONTROL"]
        assert len(controls) == 28 and len(specs) == 56
        assert {s.horizon for s in controls} == {"HOURLY", "SWING"}
        assert sum(1 for s in controls if s.horizon == "HOURLY") == 24     # hourly is the emphasis
        k = cfg.BotSpecV6("V6.1", "ENA", "HOURLY")
        j = dataclasses.replace(k, role="JEV")
        assert (k.key, j.key, k.symbol, k.timeframe) == ("V6.1-ENA-1H", "V6.1-ENA-1H+JEV", "ENAUSDT", "1h")
        assert k.pair_id == j.pair_id and j.control_key == k.key
        assert cfg.BotSpecV6("V6.1", "ENA", "SWING").key == "V6.1-ENA-4H"
        assert len(cfg.field_plan(["ENA"], jev=False)) == 7

    def test_engine_settings_are_pinned_and_paper(self):
        s = cfg.settings_v6()
        assert s.dry_run is True and s.strategy_starting_balance == 20.0 and s.risk_per_trade_pct == 0.01
        assert s.daily_halt_pct == 0.12 and s.min_stop_bps == 8.0
        # the signal is stamped at the candle's close (H - 1 ms): + window + 1 ms = exactly H + window
        assert cfg.EXECUTION_V6.signal_latency_ms + cfg.EXECUTION_V6.order_latency_ms == cfg.DECISION_WINDOW_MS + 1
        assert cfg.FEES_V6.taker_rate > 0 and cfg.FEES_V6.source.startswith("bybit")   # never a fee-free paper result


# ---- evidence maturity ---------------------------------------------------------------------------------------------

class TestMaturity:
    @pytest.mark.parametrize("age_d,trades,want", [
        (0.1, 50, "TOO EARLY"), (2, 2, "TOO EARLY"), (2, 3, "COLLECTING"), (8, 14, "COLLECTING"),
        (8, 15, "EARLY SIGNAL"), (29, 100, "EARLY SIGNAL"), (30, 29, "EARLY SIGNAL"), (30, 30, "MATURE SAMPLE")])
    def test_thresholds(self, age_d, trades, want):
        assert cfg.maturity(age_d * DAY, trades) == want

    def test_there_is_no_winner(self):
        assert all(cfg.maturity(d * DAY, n) != "WINNER" for d in (0, 1, 7, 30, 365) for n in (0, 3, 15, 30, 999))


# ---- features under watermarks -------------------------------------------------------------------------------------

class TestFeatures:
    def test_series_visibility_lag_and_cap(self):
        s = LiveSeries([(1000, 1.0), (2000, 2.0), (3000, 3.0)], lag_ms=500)
        assert s.upto(2499) == 1 and s.upto(2500) == 2
        assert s.last(10_000, cap=2000) == (2000, 2.0)                  # the watermark hides a later point
        assert s.add(2500, 2.5) and not s.add(2500, 2.6) and s.at_or_before(2600) == 2.6
        assert s.window(10_000, 8000, cap=2600) == [2.0, 2.6]                # stamps in (t - lag - span, cap]
        assert s.window(10_000, 9000, cap=2600) == [1.0, 2.0, 2.6]

    def test_a_late_arriving_point_never_changes_a_recorded_decision(self):
        t = T0 + 10 * HOUR
        oi = [(T0 + i * HOUR, 100.0 + i) for i in range(10)]
        f1 = PositioningFeedV6(SYM, oi=oi, funding=[(T0, 0.0001)], caps={t: {"oi": T0 + 9 * HOUR, "funding": T0}})
        snap = f1.snapshot(t)
        f2 = PositioningFeedV6(SYM, oi=oi, funding=[(T0, 0.0001)], caps={t: {"oi": T0 + 9 * HOUR, "funding": T0}})
        f2.oi.add(t, 999.0)                                             # stamped at t, fetched after the decision
        assert f2.snapshot(t) == snap and snap["oi"] == 109.0
        f3 = PositioningFeedV6(SYM, oi=oi + [(t, 999.0)], funding=[(T0, 0.0001)])     # no watermark: all visible
        assert f3.snapshot(t)["oi"] == 999.0

    def test_funding_interval_and_expected_funding_sign(self):
        f = PositioningFeedV6(SYM, funding=[(T0, 0.0001), (T0 + 4 * HOUR, 0.0002)], funding_interval_h=8.0)
        t = T0 + 5 * HOUR
        assert f.funding_interval_h(t) == 4.0
        assert f.expected_funding(t, "long", 8.0) == pytest.approx(0.0004)     # longs pay a positive rate
        assert f.expected_funding(t, "short", 8.0) == pytest.approx(-0.0004)
        assert PositioningFeedV6(SYM, funding_interval_h=8.0).expected_funding(t, "long", 8) is None

    def test_premium_is_visible_only_once_its_hour_closed(self):
        f = PositioningFeedV6(SYM, premium=[(T0, 0.001), (T0 + HOUR, 0.002)])
        assert f.snapshot(T0 + HOUR)["basis"] == 0.001 and f.snapshot(T0 + 2 * HOUR)["basis"] == 0.002

    def test_market_regime_from_btc(self):
        assert regime({"btc": {"trend_1d": "up", "trend_4h": "up"}}, "long") == "WITH"
        assert regime({"btc": {"trend_1d": "down", "trend_4h": "down"}}, "long") == "AGAINST"
        assert regime({}, "long") == "UNKNOWN"
        m = MarketContextV6(["BTCUSDT", "ETHUSDT"])
        assert m.snapshot(T0)["btc"] is None or isinstance(m.snapshot(T0), dict)


# ---- AGGRESSIVE_V6 dynamic legal risk ------------------------------------------------------------------------------

def rules_for_need(need_pct: float, equity: float = 20.0, price: float = 1.0, stop_pct: float = 0.02) -> MarketRules:
    """Market rules whose minimum order makes the legal minimum risk exactly `need_pct` of equity."""
    min_qty = need_pct * equity / (price * stop_pct)
    return MarketRules(SYM, 0.0001, 1e-9, min_qty, 0.0, 0.025, 4, 9)


def sig(quality: float = 0.0, price: float = 1.0, stop_pct: float = 0.02, side: str = "long", ts: int = T0) -> Signal:
    stop = price * (1 - stop_pct) if side == "long" else price * (1 + stop_pct)
    s = Signal("V6.1", SYM, "entry", side, ts, "1h", price, stop=stop, take_profits=[TakeProfit(price * 1.06, 1.0)], reason="t")
    s.meta["signal_quality"] = quality
    return s


HEALTHY = {"equity": 20.0, "peak": 20.0, "drawdown": 0.0, "r": [], "trades": 0}


class TestSizing:
    @pytest.mark.parametrize("need,q,jev,tier,why", [
        (0.008, 0.0, False, "TAKE", None),
        (0.012, 0.70, False, "LEGAL_STRONG", None),
        (0.012, 0.50, False, "SKIP", "min_notional_needs_strong_setup"),
        (0.018, 0.90, False, "LEGAL_CONVICTION", None),
        (0.018, 0.70, False, "SKIP", "min_notional_needs_attack"),
        (0.018, 0.70, True, "ATTACK_ONLY", None),
        (0.025, 0.99, True, "SKIP", "min_notional_above_max_risk")])
    def test_tiers(self, need, q, jev, tier, why):
        mult, info = cfg.SizingV6({SYM: rules_for_need(need)}, jev=jev)(sig(q), HEALTHY)
        assert info["tier"] == tier and info.get("reject_reason") == why
        if tier == "TAKE":
            assert mult == 1.0
        elif why is None:
            assert info["target_risk_pct"] == pytest.approx(need, rel=1e-4)
            assert mult * cfg.AGGRESSIVE_V6.ordinary_risk_pct <= cfg.AGGRESSIVE_V6.max_risk_pct * (1 + 1e-5)
            assert mult == pytest.approx(need / 0.01, rel=1e-5)          # exactly the legal minimum, never more
        else:
            assert mult == 0.0
        assert info.get("attack_only", False) == (tier == "ATTACK_ONLY")

    def test_a_halted_bot_takes_nothing(self):
        mult, info = cfg.SizingV6({SYM: rules_for_need(0.005)}, jev=False)(sig(), {**HEALTHY, "drawdown": 0.31})
        assert mult == 0.0 and info["reject_reason"] == "bot_halted"

    def test_no_rules_no_trade(self):
        mult, info = cfg.SizingV6({}, jev=False)(sig(), HEALTHY)
        assert mult == 0.0 and info["reject_reason"] == "no_market_rules"

    def test_health_states(self):
        assert cfg.health_state({"drawdown": 0.0, "r": []})[0] == "OK"
        assert cfg.health_state({"drawdown": 0.19, "r": []})[0] == "NO_ATTACK"
        assert cfg.health_state({"drawdown": 0.0, "r": [-1.0] * 10})[0] == "NO_ATTACK"
        assert cfg.health_state({"drawdown": 0.30, "r": []})[0] == "HALTED"


# ---- Jev V6 -------------------------------------------------------------------------------------------------------

def decision(action: str, p: float = 0.78) -> JevDecision:
    probs = {"SKIP": 0.1, "TAKE": 0.1, "ATTACK": 0.1}
    probs[action] = p
    return JevDecision(take_probability=1 - probs["SKIP"], risk_state=action, risk_probabilities=probs, setup_quality=0.0,
                       quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={},
                       model_resolved="m", provider="p", request_id="r", input_tokens=10, output_tokens=5, cost_usd=0.0001)


class TestJevV6:
    def body(self, choice):
        p = {"CONTRADICT": 0.1, "SUPPORT": 0.1, "STRONGLY_SUPPORT": 0.1}
        p[choice] = 0.8
        return {"answers": {"conditions": {"type": "choice", "choice": choice, "probabilities": p}}}

    def test_parse_and_mapping(self):
        from app.ai.jev.v6 import parse_decision_v6
        assert parse_decision_v6(self.body("CONTRADICT")).risk_state == "SKIP"
        assert parse_decision_v6(self.body("SUPPORT")).risk_state == "TAKE"
        assert parse_decision_v6(self.body("STRONGLY_SUPPORT")).risk_state == "ATTACK"
        with pytest.raises(SchemaError):
            parse_decision_v6({"answers": {"conditions": {"type": "choice", "choice": "DEFENSIVE"}}})

    def test_policy(self):
        from app.ai.jev.v6 import decide_v6
        assert decide_v6(decision("SKIP"), "OK", False).multiplier == 0.0
        assert decide_v6(decision("TAKE"), "OK", False).multiplier == 1.0
        r = decide_v6(decision("ATTACK"), "OK", False)
        assert (r.level, r.multiplier) == ("ATTACK", -1.0)
        r = decide_v6(decision("ATTACK"), "NO_ATTACK", False)
        assert (r.level, r.multiplier) == ("TAKE", 1.0) and "health" in r.extra["downgrade"]
        assert decide_v6(decision("TAKE"), "OK", True).level == "SKIP"          # ATTACK_ONLY needs ATTACK
        assert decide_v6(decision("ATTACK"), "OK", True).level == "ATTACK"
        assert decide_v6(None, "OK", False, error_code="TIMEOUT").level == "SKIP"

    def test_no_defensive_level_and_no_claimed_edge(self):
        from app.ai.jev import v6 as jv6
        assert set(jv6.ACTIONS_V6) == {"SKIP", "TAKE", "ATTACK"}
        text = json.dumps(jv6.QUESTIONS_V6)
        assert "no historical edge is claimed" in text and "DEFENSIVE" not in text
        fp = jv6.v6_fingerprints()
        assert {"prompt", "policy", "state_version", "prompt_version", "state_builder"} <= set(fp)
        assert fp["policy"] == jv6.POLICY_V6.fingerprint() and jv6.POLICY_V6.attack_risk_pct == 0.02


# ---- the gates ------------------------------------------------------------------------------------------------------

BOT = {"key": "V6.1-ENA-1H", "pair_id": "v6pair:V6.1-ENA-1H", "symbol": SYM, "horizon": "HOURLY", "strategy_id": "V6.1",
       "timeframe": "1h"}


def gate(role="CONTROL", live_from=T0, now=None, ok=True, recorded=None, coverage=(), decide=None, emitted=None):
    emitted = emitted if emitted is not None else []
    board = gates.DecisionBoard(lambda s, t: (ok, None if ok else "no kline message for 120s"))
    k = dict(experiment_id="v6x-test", session_id="s1", bot=BOT if role == "CONTROL" else {**BOT, "key": BOT["key"] + "+JEV"},
             emit=lambda kind, d: emitted.append((kind, d)), board=board, coverage=gates.Coverage(coverage),
             recorded=recorded, live_from_ms=live_from, wall=(lambda: (now if now is not None else T0 + HOUR + 5_000) / 1000))
    if role == "CONTROL":
        return gates.ForwardGateV6(**k), emitted
    return gates.JevGateV6(decide=decide, model="m", mid=lambda s: 1.0, spread=lambda s: (0.5, "observed"), **k), emitted


def g_ctx(ts=T0 + HOUR - 1, **extra):
    return {"ts": ts, "sizing": {"tier": "TAKE", "legal_min_risk_pct": 0.004, "quality": 0.3}, **extra}


class TestGates:
    def test_control_takes_a_live_candidate_and_records_it(self):
        g, em = gate()
        v = g(sig(ts=T0 + HOUR - 1), g_ctx())
        assert v.multiplier == 1.0 and v.info["gate_action"] == "TAKE"
        row = em[0][1]
        assert em[0][0] == "decision" and row["reason"] == "CONTROL" and row["signal_ts"] == T0 + HOUR - 1
        assert row["decision_instant_ms"] == T0 + HOUR and row["id"] == gates.decision_id("v6x-test", BOT["key"], T0 + HOUR - 1, "long")

    def test_a_late_decision_is_never_traded(self):
        g, em = gate(now=T0 + HOUR + cfg.LATE_AFTER_MS + 1)
        v = g(sig(), g_ctx())
        assert v.multiplier == 0.0 and em[0][1]["reason"] == "LATE_DECISION"

    def test_stale_data_pauses_decisions(self):
        g, em = gate(ok=False)
        v = g(sig(), g_ctx())
        assert v.multiplier == 0.0 and em[0][1]["reason"].startswith("DATA_STALE:")

    def test_the_verdict_is_shared_by_a_pair(self):
        calls = []
        b = gates.DecisionBoard(lambda s, t: calls.append((s, t)) or (True, None))
        assert b.verdict(SYM, T0) == b.verdict(SYM, T0) == (True, None) and calls == [(SYM, T0)]

    def test_a_recorded_decision_is_replayed_never_redecided(self):
        rec = {(BOT["key"], T0 + HOUR - 1, "long"): {"id": "d1", "final_action": "SKIP", "final_level": "SKIP",
                                                    "risk_multiplier": 0.0, "reason": "DATA_STALE:x"}}
        g, em = gate(live_from=T0 + 5 * HOUR, recorded=rec, coverage=[(T0, T0 + 2 * HOUR)])
        v = g(sig(), g_ctx())
        assert v.multiplier == 0.0 and v.info["jev_source"] == "recorded" and em == []

    def test_downtime_is_missed_for_control_and_twin(self):
        for role in ("CONTROL", "JEV"):
            g, em = gate(role=role, live_from=T0 + 5 * HOUR, coverage=[(T0 + 3 * HOUR, T0 + 4 * HOUR)],
                         decide=lambda s: pytest.fail("Jev must never be asked about the past"))
            v = g(sig(), g_ctx())
            assert v.multiplier == 0.0 and em[0][1]["reason"] == "MISSED_DOWNTIME"

    def test_observed_but_unrecorded_follows_the_control(self):
        g, em = gate(live_from=T0 + 5 * HOUR, coverage=[(T0, T0 + 4 * HOUR)])
        v = g(sig(), g_ctx())
        assert v.multiplier == 1.0 and em[0][1]["reason"] == "NOT_RECORDED" and em[0][1]["rederived"] is True
        j, em = gate(role="JEV", live_from=T0 + 5 * HOUR, coverage=[(T0, T0 + 4 * HOUR)], decide=lambda s: pytest.fail())
        v = j(sig(), g_ctx(sizing={"tier": "ATTACK_ONLY", "attack_only": True}))
        assert v.multiplier == 0.0                                     # ATTACK_ONLY without an answer: SKIP

    def test_latency_move_is_signed_against_the_trade(self):
        assert gates.latency_move_bps("long", 100.0, 100.1) == pytest.approx(10.0)
        assert gates.latency_move_bps("short", 100.0, 100.1) == pytest.approx(-10.0)
        assert gates.latency_move_bps("long", None, 100.0) is None

    def test_coverage(self):
        c = gates.Coverage([(10, 20), (30, 40), (0, None)])
        assert c.observed(15) and c.observed(30) and not c.observed(25) and not c.observed(41)


# ---- the Bybit feed --------------------------------------------------------------------------------------------

def market(traded=(SYM,), clock=None, stored=None):
    from app.live.bybit_market import BybitLiveMarket, LiveFundingV6
    feeds = {s: PositioningFeedV6(s, funding_interval_h=8.0) for s in traded}
    ctx = MarketContextV6(["BTCUSDT", "ETHUSDT"])
    store = stored if stored is not None else []
    return BybitLiveMarket(list(traded), ["BTCUSDT", "ETHUSDT"], feeds, ctx, LiveFundingV6(), lambda k, p: store.append((k, p)),
                           T0, {SYM: 480}, fetch=lambda u: pytest.fail("no network in tests"),
                           fast_fetch=lambda u: pytest.fail("no network in tests"), clock=clock or (lambda: T0 / 1000))


class Inbox:
    def __init__(self):
        self.items = []

    def put_nowait(self, x):
        self.items.append(x)


def kbar(i, px=1.0, sym=SYM, source="live"):
    o = T0 + i * MINUTE
    return Candle(sym, "1m", o, px, px * 1.001, px * 0.999, px, 10.0, o + MINUTE - 1, True, 10.0, 0, source, 0.0)


class TestBybitFeed:
    def test_only_confirmed_klines_become_bars_with_the_live_quote(self):
        m = market()
        inbox = Inbox()
        m._on_message({"topic": f"tickers.{SYM}", "type": "snapshot", "data": {
            "bid1Price": "0.9999", "ask1Price": "1.0001", "markPrice": "1.0", "indexPrice": "1.0",
            "fundingRate": "0.0001", "nextFundingTime": str(T0 + 8 * HOUR), "openInterest": "5000"}}, inbox)
        m._on_message({"topic": f"tickers.{SYM}", "type": "delta", "data": {"bid1Price": "0.9998"}}, inbox)
        k = {"start": T0, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "5", "turnover": "5", "confirm": False}
        m._on_message({"topic": f"kline.1.{SYM}", "data": [k]}, inbox)
        m._on_message({"topic": f"kline.1.{SYM}", "data": [{**k, "confirm": True}]}, inbox)
        assert len(inbox.items) == 1
        bar, quote = inbox.items[0]
        assert bar.open_time == T0 and bar.source == "live" and quote == (0.9998, 1.0001)
        assert m.tickers[SYM]["oi"] == 5000.0 and m.mid(SYM) == pytest.approx(0.99995)
        assert m.funding.by_symbol[SYM] == [(T0 + 8 * HOUR, 0.0001)]     # the predicted rate for the next settlement

    def test_delivery_is_once_in_order_and_records_the_observed_spread(self):
        stored = []
        m = market(stored=stored)
        q = m.subscribe(SYM)
        for i in (1, 2, 2, 1, 3):
            m._deliver(kbar(i), "live", (0.999, 1.001))
        assert [q.get_nowait().open_time for _ in range(q.qsize())] == [kbar(i).open_time for i in (1, 2, 3)]
        bars = [p for k, p in stored if k == "bar"]
        assert len(bars) == 3 and bars[0]["half_spread_bps"] == pytest.approx(10.0)
        assert m.quote_half_spread(SYM, kbar(2).close_time) == pytest.approx(10.0)
        assert m.quote_half_spread(SYM, kbar(3).close_time + 10 * MINUTE) is None     # too old: modelled

    def test_funding_charged_is_frozen_at_settlement_and_recorded(self):
        stored = []
        m = market(stored=stored)
        m.funding.upsert(SYM, T0 + 8 * HOUR, 0.0001)
        m._deliver(kbar(8 * 60 - 1), "live")                          # the bar before the settlement
        m.funding.upsert(SYM, T0 + 8 * HOUR, 0.0003)                  # still predicted: may change
        m._deliver(kbar(8 * 60), "live")                              # the first bar at the settlement freezes it
        m.funding.upsert(SYM, T0 + 8 * HOUR, 0.0009)                  # a later value never changes the charge
        m.funding.merge_settled(SYM, [(T0 + 8 * HOUR, 0.0007)])
        assert m.funding.by_symbol[SYM] == [(T0 + 8 * HOUR, 0.0003)]
        assert ("funding_charged", {"symbol": SYM, "ts": T0 + 8 * HOUR, "rate": 0.0003}) in stored

    def test_stream_and_positioning_staleness(self):
        now = [T0 / 1000]
        m = market(clock=lambda: now[0])
        assert m.stream_ok(SYM) == (False, "websocket down")
        m.stats["ws"]["connected"] = True
        m.stats["last_kline_ts"][SYM] = m.stats["last_ticker_ts"][SYM] = now[0]
        assert m.stream_ok(SYM) == (True, None)
        now[0] += cfg.STALE_STREAM_S + 1
        assert m.stream_ok(SYM)[0] is False
        f = m.feeds[SYM]
        f.oi.extend([(T0 - HOUR, 100.0)])
        f.funding.extend([(T0 - 8 * HOUR, 0.0001)])
        assert m.positioning_ok(SYM, T0)[0] is True
        assert m.positioning_ok(SYM, T0 + 5 * HOUR)[0] is False           # open interest 6 h old

    def test_rest_kline_rows(self):
        from app.live.bybit_market import kline_candle
        c = kline_candle(SYM, [str(T0), "1", "2", "0.5", "1.5", "10", "15"], "backfill")
        assert (c.open_time, c.close_time, c.high, c.quote_volume, c.source) == (T0, T0 + MINUTE - 1, 2.0, 15.0, "backfill")


# ---- the runner end to end: real engine, V6 execution, sizing and gates ---------------------------------------------

class HourlyOnce(Strategy):
    """Long at the close of the `at`-th 1h candle (counted from the tape's start), 2% stop, 2R target."""
    id = "V6.1"
    name = "test hourly once"
    timeframes = ("1h",)
    min_rr = 0.0
    warmup_bars = 0
    max_positions = 1
    default_leverage = 5

    @dataclasses.dataclass
    class Params:
        at: int = P(6, min=1, max=10_000)

    def __init__(self, params=None):
        super().__init__(params)
        self._fired = False

    def on_candle(self, c, ctx):
        if self._fired or c.tf != "1h" or len(ctx.candles(c.symbol, "1h")) < self.params.at:
            return []
        self._fired = True
        s = self.make_entry(symbol=c.symbol, side="long", ts=c.close_time, tf="1h", price=c.close,
                            stop=c.close * 0.98, tps_r=[(2.0, 1.0)], reason="test")
        s.meta["signal_quality"] = 0.3
        return [s]


def tape(hours=16, start=1.0, per_min=0.0001):
    """A clean 1m uptrend: the entry at the 6th hour reaches its 2R target (+4%) in about 6.5 h, never its stop."""
    out = []
    for i in range(hours * 60):
        px = start * (1 + per_min) ** i
        o = T0 + i * MINUTE
        out.append(Candle(SYM, "1m", o, px, px * 1.0002, px * 0.9998, px, 1000.0, o + MINUTE - 1, True, 1000.0, 0, "live", 0.0))
    return out


ENA_RULES = {SYM: MarketRules(SYM, 0.0001, 1.0, 1.0, 5.0, 0.025, 4, 0)}


def session(bars, since_ms, live_from_ms, role="CONTROL", recorded=None, coverage=None, stop_after=False,
            decide=None, recorded_decisions=None):
    """One worker session of one V6 bot over `bars`. `stop_after`: stopped like a deploy once the bars are consumed."""
    from app.backtest.replay import ReplayEngine
    from app.live.v6_runner import V6Bot
    spec = cfg.BotSpecV6("V6.1", "ENA", "HOURLY", role)
    events: list = []
    holder: dict = {}
    wall = lambda: (holder["bot"].last_bar_close + 1_000) / 1000        # noqa: E731  (1 s after the bar decided on)
    kw = dict(experiment_id="v6x-test", session_id="s", bot={**spec.to_dict()}, emit=lambda k, d: events.append((k, d)),
              board=gates.DecisionBoard(lambda s, t: (True, None)),
              coverage=gates.Coverage(coverage if coverage is not None else [(live_from_ms, bars[-1].close_time + 1)]),
              recorded=recorded_decisions, live_from_ms=live_from_ms, wall=wall)
    g = gates.JevGateV6(decide=decide, model="m", mid=lambda s: None, spread=lambda s: (None, "modelled"), **kw) \
        if role == "JEV" else gates.ForwardGateV6(**kw)
    eng = ReplayEngine(cfg.settings_v6(), [SYM], rules=ENA_RULES, seed=7, execution=cfg.EXECUTION_V6, fees=cfg.FEES_V6,
                       fee_source="schedule", sizing=cfg.SizingV6(ENA_RULES, jev=role == "JEV"), leverage_policy="needed",
                       max_risk_pct=0.02, gate=g, cost_gate=None)
    q: queue.Queue = queue.Queue()
    bot = V6Bot(spec=spec, cls=HourlyOnce, engine=eng, bars=q, since_ms=since_ms, live_from_ms=live_from_ms,
                emit=lambda k, d: events.append((k, d)), recorded=recorded, family="TEST")
    holder["bot"] = bot
    for b in bars:
        q.put(b)
    if not stop_after:
        q.put(None)
    bot.start()
    if stop_after:
        deadline = time.time() + 20
        while (bot.last_bar_close < bars[-1].close_time or not q.empty()) and time.time() < deadline:
            time.sleep(0.005)
        bot.stop()
    bot.thread.join(20)
    return bot, events


def recorded_from(events):
    from app.live import continuity as cont
    rec = cont.Recorded()
    for k, d in events:
        if k == "candidate":
            rec.seen.add(cont.cand_ident(d["bot_key"], d["side"], d["signal_ts"]))
        elif k == "open":
            rec.seen.add(cont.open_ident(d["bot_key"], d["side"], d["ts"]))
        elif k == "closed":
            i = cont.trade_ident(d["bot_key"], d["side"], d["entry_ts"], d["counterfactual"])
            rec.seen.add(i)
            rec.nets[i] = d["net"]
        elif k == "decision":
            rec.decisions[(d["bot_key"], int(d["signal_ts"]), d["side"])] = {
                x: d.get(x) for x in ("id", "final_action", "final_level", "risk_multiplier", "reason", "error_code")}
    return rec


def kinds(events, only=("candidate", "decision", "open", "closed")):
    return [k for k, _ in events if k in only]


class TestRunner:
    def test_warmup_never_trades_and_the_fill_is_after_the_decision_window(self):
        bars = tape()
        t0 = bars[4 * 60].open_time                                    # forward start: after 4 h of warm-up
        bot, ev = session(bars, t0, t0)
        assert kinds(ev) == ["decision", "candidate", "open", "closed"]
        cand = next(d for k, d in ev if k == "candidate")
        opened = next(d for k, d in ev if k == "open")
        closed = next(d for k, d in ev if k == "closed")
        assert cand["outcome"] == "ORDERED" and cand["tier"] == "TAKE" and cand["signal_ts"] >= t0
        H = cand["signal_ts"] + 1
        fill_bar = next(b for b in bars if b.open_time == H + cfg.DECISION_WINDOW_MS)
        assert opened["ts"] == H + cfg.DECISION_WINDOW_MS                          # after every decision in the window
        assert fill_bar.open < opened["price"] < fill_bar.open * 1.001            # that bar's OPEN plus the spread
        assert opened["risk_pct"] == pytest.approx(0.01, rel=0.2) and opened["fee"] > 0
        assert closed["exit_kind"] == "tp" and closed["fees"] > 0 and closed["net"] < closed["gross"]
        assert closed["funding"] == 0.0 and closed["r_net"] == pytest.approx(closed["r"], rel=1e-6)
        assert 1.5 < closed["r_net"] < 2.0                     # a 2R target, less the fees and the spread paid
        assert bot.warm_bars == 4 * 60 and bot.status["trades"] == 1 and bot.status["live"] is True
        assert bot.error is None

    def test_a_skipping_jev_twin_trades_nothing_and_keeps_the_counterfactual(self):
        bars = tape()
        t0 = bars[4 * 60].open_time
        asked = []

        def decide(state):
            asked.append(state)
            return JevOutcome(True, decision("SKIP"), latency_ms=300)
        bot, ev = session(bars, t0, t0, role="JEV", decide=decide)
        assert len(asked) == 1 and "no historical edge" not in json.dumps(asked[0])[:0]
        dec = next(d for k, d in ev if k == "decision")
        assert dec["final_level"] == "SKIP" and dec["source"] == "api" and dec["p_support"] == pytest.approx(0.22)
        closed = [d for k, d in ev if k == "closed"]
        assert closed and all(d["counterfactual"] for d in closed)
        assert bot.status["trades"] == 0 and bot.status["equity"] == pytest.approx(20.0)

    def test_an_attacking_twin_sizes_up_to_two_percent(self):
        bars = tape()
        t0 = bars[4 * 60].open_time
        bot, ev = session(bars, t0, t0, role="JEV", decide=lambda s: JevOutcome(True, decision("ATTACK"), latency_ms=250))
        opened = next(d for k, d in ev if k == "open")
        assert opened["jev_level"] == "ATTACK" and opened["risk_pct"] == pytest.approx(0.02, rel=0.1)
        assert opened["risk_pct"] <= 0.02 * 1.001

    def test_a_restart_carries_the_open_position_and_records_nothing_twice(self):
        bars = tape()
        t0 = bars[4 * 60].open_time
        full, ev_full = session(bars, t0, t0)
        want = next(d for k, d in ev_full if k == "closed")
        entry_i = next(i for i, b in enumerate(bars) if b.close_time >= want["entry_ts"])
        # session A: live from T0, stopped by a deploy two hours after the entry -- the position is open
        a, ev_a = session(bars[:entry_i + 120], t0, t0, stop_after=True)
        assert kinds(ev_a) == ["decision", "candidate", "open"]                     # a stop never invents an exit
        assert a.status["open_positions"] and a.status["open_positions"][0]["entry_ts"] == want["entry_ts"]
        # session B: back 20 minutes later; the book is re-derived from T0 with A's recorded decision
        live_b = bars[entry_i + 140].close_time + 1
        b, ev_b = session(bars, t0, live_b, recorded=recorded_from(ev_a),
                          recorded_decisions=recorded_from(ev_a).decisions,
                          coverage=[(t0, bars[entry_i + 119].close_time), (live_b, bars[-1].close_time + 1)])
        assert kinds(ev_b) == ["closed"]                                            # candidate / decision / open recorded
        got = ev_b[-1][1]
        assert (got["entry_ts"], got["exit_ts"], got["exit_kind"]) == (want["entry_ts"], want["exit_ts"], want["exit_kind"])
        assert got["net"] == pytest.approx(want["net"]) and not got.get("rederived")
        assert b.status["equity"] == pytest.approx(full.status["equity"])
        assert b.status["trades"] == full.status["trades"] == 1                     # the book was not reset

    def test_a_candidate_during_downtime_is_missed_not_traded_late(self):
        bars = tape()
        t0 = bars[60].open_time
        live = bars[8 * 60].close_time + 1                                          # down from T0 + 1 h until 8 h
        b, ev = session(bars, t0, live, coverage=[(t0, bars[2 * 60].close_time), (live, bars[-1].close_time + 1)])
        dec = next(d for k, d in ev if k == "decision")
        assert dec["reason"] == "MISSED_DOWNTIME" and dec["final_action"] == "SKIP"
        assert b.status["trades"] == 0 and not b.status["open_positions"]


# ---- storage, service, public payloads ---------------------------------------------------------------------------

class TestStorageAndService:
    def test_the_forward_start_is_written_once(self, tmp_path):
        st = Storage(str(tmp_path / "v6.db"))
        row = {"experiment_id": "v6x-a", "created_ts": T0, "forward_start_ms": T0, "warmup_from_ms": T0 - 40 * DAY,
               "manifest_fingerprint": "f", "identity": {"m": 1}, "config": {"coins": ["ENA"]}}
        assert st.fwd6_experiment_create(row) is True
        assert st.fwd6_experiment_create({**row, "forward_start_ms": T0 + DAY}) is False
        assert st.fwd6_experiment("v6x-a")["forward_start_ms"] == T0
        st.fwd6_session_start("s1", "v6x-a", T0, {"x": 1})
        st.fwd6_session_update("s1", live_ts=T0 + MINUTE, heartbeat_ts=T0 + HOUR, status="LIVE")
        st.fwd6_session_start("s2", "v6x-a", T0 + 2 * HOUR, {"x": 1})             # never went live: no coverage
        assert st.fwd6_coverage("v6x-a") == [(T0 + MINUTE, T0 + HOUR)]
        st.fwd6_watermark_save([{"symbol": SYM, "t": T0 + HOUR, "caps": {"oi": T0}, "barrier": {"complete": True}}])
        assert st.fwd6_watermarks(0)[SYM][T0 + HOUR] == {"oi": T0}
        st.close()

    def test_schema_and_decision_columns(self, tmp_path):
        from app.core.storage import SCHEMA_VERSION
        from app.core.storage_fwd6 import DECISION_COLS
        assert SCHEMA_VERSION == 17
        st = Storage(str(tmp_path / "v6.db"))
        tables = {r[0] for r in st.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"fwd6_experiments", "fwd6_sessions", "fwd6_bots", "fwd6_events", "fwd6_trades", "fwd6_decisions",
                "fwd6_bars", "fwd6_positioning", "fwd6_watermarks", "fwd6_funding_charged"} <= tables
        assert {"latency_ms", "move_bps", "final_level", "state_json", "legal_min_risk_pct"} <= set(DECISION_COLS)
        st.close()

    def test_the_service_is_off_unless_enabled(self, tmp_path):
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "x.db"), env={})
        svc.start()                                                              # no process, no network
        assert svc.enabled is False and svc._proc is None and svc.health()["status"] == "DISABLED"
        assert V6ForwardService(str(tmp_path / "x.db"), env={"V6_FORWARD_ENABLED": "true"}).enabled is True

    def test_the_worker_holds_no_order_path_and_runs_no_backtest(self):
        import inspect

        from app.live import v6_runner, v6_worker
        src = inspect.getsource(v6_worker) + inspect.getsource(v6_runner)
        for banned in ("place_order", "create_order", "ccxt", "api_secret", "run_v5", "run_v6", "walkforward", "monte"):
            assert banned not in src.lower(), banned


def _payload_storage(tmp_path):
    st = Storage(str(tmp_path / "p.db"))
    st.fwd6_experiment_create({"experiment_id": "v6x-p", "created_ts": T0, "forward_start_ms": T0, "warmup_from_ms": T0 - DAY,
                               "manifest_fingerprint": "f", "identity": {}, "config": {"coins": ["ENA"]}})
    st.fwd6_session_start("s1", "v6x-p", T0, {})
    st.fwd6_session_update("s1", live_ts=T0, heartbeat_ts=T0 + HOUR, status="LIVE")
    bots = [{"key": "V6.1-ENA-1H", "role": "CONTROL", "pair_id": "v6pair:V6.1-ENA-1H", "control_key": "V6.1-ENA-1H",
             "strategy_id": "V6.1", "coin": "ENA", "symbol": SYM, "horizon": "HOURLY", "live": True, "equity": 20.4,
             "start_equity": 20.0, "trades": 1, "open_positions": [{"side": "long", "qty": 10, "entry": 1.0, "mark": 1.01,
                                                                     "upnl": 0.1, "entry_ts": T0 + HOUR, "risk_pct": 0.01}]},
            {"key": "V6.1-ENA-1H+JEV", "role": "JEV", "pair_id": "v6pair:V6.1-ENA-1H", "control_key": "V6.1-ENA-1H",
             "strategy_id": "V6.1", "coin": "ENA", "symbol": SYM, "horizon": "HOURLY", "live": True, "equity": 20.0,
             "start_equity": 20.0, "trades": 0, "open_positions": []}]
    st.fwd6_bots_save("v6x-p", "s1", bots, T0 + HOUR)
    st.fwd6_event_add("v6x-p", "s1", T0 + HOUR, "decision", {"bot_key": "V6.1-ENA-1H+JEV", "role": "JEV", "source": "api",
                                                            "final_level": "SKIP", "p_support": 0.2, "latency_ms": 300,
                                                            "state_json": "{\"secret\": 1}"})
    st.fwd6_decision_save({"id": "d1", "experiment_id": "v6x-p", "bot_key": "V6.1-ENA-1H+JEV", "role": "JEV",
                           "pair_id": "v6pair:V6.1-ENA-1H", "signal_ts": T0 + HOUR - 1, "side": "long", "source": "api",
                           "final_level": "SKIP", "final_action": "SKIP", "latency_ms": 300, "state_json": "{}"})
    st.fwd6_trade_save("v6x-p", "s1", {"bot_key": "V6.1-ENA-1H", "role": "CONTROL", "pair_id": "v6pair:V6.1-ENA-1H",
                                       "symbol": SYM, "side": "long", "entry_ts": T0 + 2 * HOUR, "exit_ts": T0 + 5 * HOUR,
                                       "gross": 0.5, "fees": 0.05, "slippage": 0.02, "funding": -0.01, "net": 0.42,
                                       "r": 2.1, "r_net": 2.0, "exit_kind": "tp", "tier": "TAKE", "counterfactual": False})
    return st


class TestPublicPayload:
    def test_home_payload_hero_and_no_private_fields(self, tmp_path):
        from app.core.v6_view import inspection_block, v6_payload
        st = _payload_storage(tmp_path)
        p = v6_payload(st, None, now_ms=T0 + 2 * DAY)
        h = p["hero"]
        assert h["forward_age"] == "2d 0h" and h["maturity"] == "TOO EARLY" and h["bots"] == 2
        assert h["active_bots"] == 0                                   # saved books are not live books
        assert h["positions_open"] == 1 and p["positions"][0]["hold_s"] == (2 * DAY - HOUR) // 1000
        assert p["totals"]["controls"]["trades"] == 1 and p["totals"]["controls"]["fees"] == 0.05
        assert p["pairs"][0]["jev_key"] == "V6.1-ENA-1H+JEV" and p["jev"]["decisions"] == 1
        blob = json.dumps(p)
        assert "state_json" not in blob and "secret" not in blob
        insp = inspection_block(st, None)
        for k in ("forward_start", "forward_age", "active_bots", "open_positions", "candidates_24h", "trades_24h",
                  "gross_pnl", "fees", "slippage", "funding_paid", "net_pnl", "top_bots", "jev_pairs",
                  "risk_distribution", "stream_health", "maturity"):
            assert k in insp, k
        st.close()

    def test_age_text(self):
        from app.core.v6_view import age_text
        assert [age_text(x) for x in (0, 5 * MINUTE, 4 * HOUR + 23 * MINUTE, DAY + 8 * HOUR, 17 * DAY)] == \
            ["0m", "5m", "4h 23m", "1d 8h", "17d"]

    def test_no_experiment_yet(self, tmp_path):
        from app.core.v6_view import v6_payload
        st = Storage(str(tmp_path / "e.db"))
        p = v6_payload(st, None)
        assert p["experiment"] is None and "V6_FORWARD_ENABLED" in p["note"]
        st.close()

    def test_public_routes_are_get_only_and_the_home_has_no_backtest_control(self):
        from pathlib import Path

        from app.core import api_public
        routes = [r for r in api_public.router.routes if "/v6" in getattr(r, "path", "")]
        assert routes and all(set(r.methods) <= {"GET", "HEAD"} for r in routes)
        js = (Path(__file__).resolve().parents[1] / "app" / "dashboard" / "v6.js").read_text(encoding="utf-8")
        assert "post(" not in js and "method: 'POST'" not in js and "backtest" not in js.lower().replace("no backtest control", "")


class TestEquityCurve:
    def test_realized_steps_then_the_live_point(self):
        from app.core.v6_view import equity_curve
        trades = [{"exit_ts": T0 + 3 * HOUR, "net": -0.2}, {"exit_ts": T0 + HOUR, "net": 0.5}]
        pts = equity_curve(trades, 40.0, T0, T0 + 5 * HOUR, 40.1)
        assert pts == [[T0, 40.0], [T0 + HOUR, 40.5], [T0 + 3 * HOUR, 40.3], [T0 + 5 * HOUR, 40.1]]
        assert equity_curve([], 20.0, T0, T0 + HOUR, None) == [[T0, 20.0]]

    def test_long_histories_are_thinned_keeping_both_ends(self):
        from app.core.v6_view import equity_curve
        trades = [{"exit_ts": T0 + i * MINUTE, "net": 0.01} for i in range(1, 1001)]
        pts = equity_curve(trades, 20.0, T0, T0 + DAY, 30.0, max_points=100)
        assert len(pts) == 101 and pts[0] == [T0, 20.0] and pts[-2] == [T0 + 1000 * MINUTE, 30.0]
        assert pts[-1] == [T0 + DAY, 30.0]
