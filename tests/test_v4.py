"""V4 INTRADAY SPECIALISTS: earlier programs stay frozen, the tradeability rule never sees PnL, the exit
analyzer judges every exit against a baseline, the seven families are honest three-layer specialists with one
exit, the expected-edge model is causal and shrunk, Jev V4 maps CONTRADICT / SUPPORT / STRONGLY SUPPORT to
SKIP / TAKE / ATTACK and never creates an illegal order, the analyzer finds the ROOT cause, the capacity
diagnostic never qualifies a 20 USDT bot, storage is separate. No network: Jev is a stub, the tape synthetic."""
from __future__ import annotations

import random

import pytest

from app.ai.jev import v4 as jv4
from app.ai.jev.models import SchemaError
from app.competition import exit_analyzer as ex
from app.competition import tradeability as tr
from app.competition import v4_analyzer as an
from app.competition.v4_arena import V4Arena, select_field
from app.competition.v4_config import (COINS_PER_TF, DEV, TEST, EdgeConfigV4, SizingV4, V4Config, V4Identity,
                                       health_state, min_trades)
from app.competition.v4_edge import EdgeModelV4
from app.core.storage import Storage
from app.core.types import Candle
from tests.conftest import T0, settings_factory
from tests.test_v31 import SYM, rules, tape


def arena(evidence=()) -> V4Arena:
    cfg = V4Config(months=(), trade_from="2025-11-01", trade_to="2026-04-30",
                   coins_per_tf=tuple((tf, ("SUI",)) for tf in ("3m", "5m", "15m", "30m")))
    return V4Arena(settings_factory(balance=20), cfg, rules(), evidence)


def run_bot(sid: str, tf: str, role: str = "RAW", gate=None, evidence=(), bars=None):
    from app.live.runner import with_sink
    a = arena(evidence)
    ident = V4Identity(sid, "SUI", tf, role, jev_policy="JEV_POLICY_V4" if role in ("JEV", "TAKE", "RANDOM") else "")
    cls = a.bound(ident)
    seen: list = []
    live = with_sink(cls, lambda sigs, c: seen.extend((s, c) for s in sigs))
    eng, edge_gate, kinds = a.engine(ident, gate)
    bars = bars or tape(n=14000, seed=11)
    res = eng.run(live, iter(bars), since_ms=bars[500].close_time, leverage=20, signal_tf=tf, only_symbol=SYM)
    return res, seen, eng, edge_gate, kinds


def obs(sid="V4.1", tf="15m", coin="SUI", ts=0, exit_ts=1, gross=0.3):
    return {"sid": sid, "tf": tf, "coin": coin, "ts": ts, "exit_ts": exit_ts, "qband": "MID", "regime": "WITH",
            "vol_band": "MID", "gross_r": gross, "net_r": gross - 0.1, "cost_r": 0.1, "tp_hit": False, "stopped": False,
            "sequenced": True}


# ---- earlier programs stay frozen ------------------------------------------------------------------------------

class TestFrozen:
    def test_v31_and_v3_sources_are_unchanged(self):
        from app.strategies.registry import v3_fingerprints, v31_fingerprints
        assert v31_fingerprints() == {"v3_base": "33d10d257921", "base": "0de866f0754c", "S33.1": "05629760d058",
                                      "S35.1": "21ad7d9b13c9", "S37": "4a357437251a"}
        assert v3_fingerprints()["base"] == "33d10d257921"

    def test_v4_is_never_merged_into_other_loaders(self):
        from app.strategies.registry import load_all, load_v3, load_v31, load_v4
        v4 = set(load_v4())
        assert v4 == {"V4.1", "V4.2", "V4.3", "V4.4", "V4.5", "V4.6", "V4.7"}
        assert not v4 & (set(load_all()) | set(load_v3()) | set(load_v31()))

    def test_frozen_results_guard_parses_run_ids(self, tmp_path, monkeypatch):
        import scripts.run_v4_arena as script
        (tmp_path / "V4_RESULTS_FREEZE.md").write_text("| `v4-0123456789` | frozen |\n", encoding="utf-8")
        monkeypatch.setattr(script, "DOCS", tmp_path)
        assert script.frozen_runs() == {"v4-0123456789"}


# ---- tradeability: market structure only -------------------------------------------------------------------------

def _k(n, close=10.0, rng=0.012, turnover=5e7):
    return [[str(T0 + i * 86_400_000), str(close), str(close * (1 + rng / 2)), str(close * (1 - rng / 2)), str(close), "1",
             str(turnover)] for i in range(n)]


class TestTradeability:
    FILT = {"tick": "0.001", "step": "0.1", "min_qty": "0.1", "min_notional": "5", "max_leverage": "25"}

    def test_measure_and_gates(self):
        rule = tr.TradeabilityRule()
        m = tr.measure(_k(30), _k(1440, rng=0.01), self.FILT, rule)
        m.update({"available": True, "listed_long_enough": True})
        assert m["range_30m_bps"] == pytest.approx(100.0) and m["half_spread_bps"] == pytest.approx(0.5)
        assert m["round_trip_cost_bps"] == pytest.approx(12.0) and tr.gates(m, rule) == []
        thin = dict(m, turnover_usdt=1e6)
        assert "LOW_LIQUIDITY" in tr.gates(thin, rule)
        coarse = tr.measure(_k(30, close=2000.0), _k(1440, close=2000.0, rng=0.01),
                            {**self.FILT, "step": "0.01", "min_qty": "0.02"}, rule)
        coarse.update({"available": True, "listed_long_enough": True})
        fails = tr.gates(coarse, rule)                    # a 1% stop at 1% risk = 20 USDT; the minimum is 0.02 x 2000 = 40
        assert "BELOW_EXCHANGE_MINIMUM_AT_20_USDT" in fails and "QUANTITY_STEP_TOO_COARSE" in fails
        assert tr.gates({"available": False}, rule) == ["NO_PRICE_TAPE"]

    def test_score_uses_no_pnl_and_is_deterministic(self):
        rule = tr.TradeabilityRule(universe_size=2)
        rows = {}
        for i, s in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT")):
            m = tr.measure(_k(30, turnover=(i + 1) * 3e7), _k(1440, rng=0.01 + 0.002 * i), self.FILT, rule)
            m.update({"available": True, "listed_long_enough": True})
            rows[s] = m
        out = tr.score(rows, rule)
        assert out["universe"] == out["ranked"][:2] and out["eligible"] == 3
        assert tr.score(rows, rule)["ranked"] == out["ranked"]
        import inspect
        src = inspect.getsource(tr)
        assert "pnl" not in src.lower().replace("never a strategy's pnl", "").replace("no pnl", "")

    def test_pool_and_speed_split(self):
        assert tr.pool({"A": 3.0, "B": None, "C": 5.0}, tr.TradeabilityRule(pool_size=1)) == ["C"]
        u = ["A", "B", "C", "D", "E", "F", "G"]
        split = tr.split_by_speed(u, COINS_PER_TF)
        assert split["3m"] == ["A", "B", "C"] and split["15m"] == u


# ---- the exit analyzer --------------------------------------------------------------------------------------------

def _bars(prices):
    return [Candle(SYM, "1m", T0 + i * 60_000, p, p + 0.2, p - 0.2, p, 1.0, T0 + i * 60_000 + 59_999) for i, p in enumerate(prices)]


class TestExitAnalyzer:
    def test_excursion_uses_the_exact_stop_distance(self):
        tape_ = ex.Tape(_bars([100, 100.5, 101, 102, 103, 102, 101, 99.5, 99.0] + [101 + 0.01 * i for i in range(800)]))
        t = {"side": "long", "entry": 100.0, "exit": 99.0, "qty": 2.0, "net": -2.0, "r": -1.0, "entry_ts": T0,
             "exit_ts": T0 + 8 * 60_000, "exit_kind": "stop", "gross": -2.0, "fees": 0.0, "slippage": 0.0,
             "notional": 200.0, "hold_s": 480, "risk_usd": 5.0}
        assert ex.stop_distance(t) == pytest.approx(1.0)                      # net / R / qty, not the sizing budget
        e = ex.excursion(t, tape_)
        assert e["mfe_r"] == pytest.approx(3.2) and e["mae_r"] == pytest.approx(1.2)
        assert e["realised_r"] == pytest.approx(-1.0) and e["eventual_r"] > 2.0 and "post_exit_r" in e

    def test_flags_need_to_beat_their_baseline(self):
        base = {"stopped_then_2r_share": 0.55, "random_stopped_then_2r_share": 0.52, "winners_mae_r_p75": 0.8,
                "winners_post_exit_4h_r_mean": 0.05, "winner_hold_min_p50": 40, "loser_hold_min_p50": 20,
                "edge": {"gross": -1.0}}
        assert ex.flags(base) == ["NO EXIT FLAG (GROSS <= 0: LOOK AT ENTRIES)"]
        assert "STOPS TOO TIGHT" in ex.flags({**base, "stopped_then_2r_share": 0.70})
        assert "WINNERS CUT EARLY" in " ".join(ex.flags({**base, "winners_post_exit_4h_r_mean": 0.5}))
        assert "LOSERS HELD TOO LONG" in ex.flags({**base, "loser_hold_min_p50": 90})

    def test_hold_buckets_and_edge_quality(self):
        trades = [{"net": 0.1, "gross": 0.15, "fees": 0.04, "slippage": 0.01, "notional": 50.0, "risk_usd": 0.2, "r": 0.5,
                   "hold_s": s} for s in (100, 400, 1000, 2000, 4000, 8000, 20000)]
        s = ex.summarize(trades, [None] * 7, days=10.0)
        assert [s["hold_buckets"][b]["n"] for b, _, _ in ex.HOLD_BUCKETS] == [1] * 7
        e = s["edge"]
        assert e["gross_bps_of_turnover"] == pytest.approx(30.0) and e["gross_per_fee_dollar"] == pytest.approx(3.75)
        assert e["net_per_day"] == pytest.approx(0.07)

    def test_random_baseline_is_seeded(self):
        tape_ = ex.Tape(tape(n=3000, seed=2))
        a = ex.stopped_then_2r_baseline(tape_, 0.01, T0 + 1500 * 60_000, random.Random(1), draws=5)
        b = ex.stopped_then_2r_baseline(tape_, 0.01, T0 + 1500 * 60_000, random.Random(1), draws=5)
        assert a == b and all(isinstance(x, bool) for x in a)


# ---- the seven families ----------------------------------------------------------------------------------------------

class TestStrategies:
    def test_documentation_and_binding(self):
        from app.strategies.registry import load_v4
        for sid, cls in load_v4().items():
            for f in ("hypothesis", "thesis", "evidence", "expected_hold", "expected_frequency", "family"):
                assert getattr(cls, f), (sid, f)
            b3 = cls.for_timeframe("3m")
            assert b3.structure_tf == "15m" and b3.timeframes == ("3m", "15m", "1h", "4h", "1m")
            assert cls.for_timeframe("30m").structure_tf == "30m"
            with pytest.raises(ValueError):
                cls.for_timeframe("1m")
        assert [c.trend_aligned for c in load_v4().values()].count(False) == 1       # only the failed breakout

    @pytest.mark.parametrize("sid,tf", [("V4.1", "15m"), ("V4.4", "5m"), ("V4.5", "15m"), ("V4.6", "3m"), ("V4.3", "30m")])
    def test_one_uniform_exit_and_honest_stops(self, sid, tf):
        res, seen, _, _, _ = run_bot(sid, tf, "RAW", jv4.ObserverGate())
        for sig, bar in seen:
            m = sig.meta
            assert bar.tf == tf and sig.ts == bar.close_time
            assert 0.006 - 1e-12 <= m["stop_pct"] <= 0.025 + 1e-12
            assert sig.trail is not None and sig.trail.tf == m["structure_tf"] and sig.trail.mult == 2.0
            assert sig.max_hold_s == (180 if tf in ("3m", "5m") else 240) * 60
            assert sig.be_at_r == 1.5 and len(sig.take_profits) == 1 and sig.take_profits[0].fraction == 0.5
            assert m["regime"] in ("WITH", "AGAINST", "FLAT", "UNKNOWN") and 0.0 <= m["signal_quality"] <= 1.0
        assert not res.trades

    def test_trend_aligned_families_follow_the_1h_trend(self):
        seen_any = False
        for sid in ("V4.1", "V4.4", "V4.6"):
            _, seen, _, _, _ = run_bot(sid, "15m", "RAW", jv4.ObserverGate())
            for sig, _ in seen:
                seen_any = True
                assert sig.reason.endswith("1h up") == (sig.side == "long")
                assert sig.reason.endswith("1h down") == (sig.side == "short")
        assert seen_any

    def test_a_small_timeframe_setup_is_traded_once_and_only_while_fresh(self):
        from app.strategies.registry import load_v4
        cls = load_v4()["V4.1"].for_timeframe("3m")
        strat = cls()
        s15 = [Candle(SYM, "15m", T0 + i * 900_000, 10, 10.1, 9.9, 10, 1.0, T0 + i * 900_000 + 899_999) for i in range(80)]

        class Ctx:
            def candles(self, sym, tf):
                return s15 if tf == "15m" else []

            def ind(self, sym, tf, name, **k):
                return [0.1] * 80

            def last_price(self, sym):
                return 10.0

        called = []
        strat.setup = lambda *a, **k: called.append(1) or None           # noqa: E731
        late = Candle(SYM, "3m", T0, 10, 10, 10, 10, 1.0, s15[-1].close_time + 2 * 900_000)
        assert strat.on_candle(late, Ctx()) == [] and not called        # the structure bar is stale: not even evaluated


# ---- the expected-edge model ---------------------------------------------------------------------------------------------

class TestEdgeModel:
    def test_insufficient_evidence_is_never_a_pass(self):
        m = EdgeModelV4([obs(exit_ts=i, gross=1.0) for i in range(20)])
        m.advance(100)
        v = m.predict(sid="V4.1", tf="15m", coin="SUI", quality=0.5, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert v["status"] == "REJECT" and v["reason"] == "INSUFFICIENT_EVIDENCE" and not v["attack_eligible"]

    def test_causal_and_shrunk_toward_zero(self):
        m = EdgeModelV4([obs(exit_ts=10 + i, gross=0.5) for i in range(60)] + [obs(exit_ts=10_000, gross=-50.0)])
        m.advance(5)
        assert m.absorbed == 0
        m.advance(1000)
        assert m.absorbed == 60
        v = m.predict(sid="V4.1", tf="15m", coin="ARB", quality=None, regime="X", vol_band="Y", cost_r=0.1, stop_pct=0.01)
        assert v["gross_r"] == pytest.approx(0.5 * 60 / 90, abs=1e-4)         # 30 pseudo-trades of zero edge
        assert v["net_r"] == pytest.approx(v["gross_r"] - 0.1, abs=1e-4) and v["status"] == "PASS"
        with pytest.raises(ValueError):
            m.advance(10)

    def test_rolling_window_and_other_cells(self):
        cfg = EdgeConfigV4(window=50)
        m = EdgeModelV4([obs(exit_ts=i, gross=1.0) for i in range(100)] + [obs(exit_ts=200 + i, gross=-1.0) for i in range(50)], cfg)
        m.advance(10 ** 6)
        assert m.cell("V4.1", "15m")["raw_mean"] == pytest.approx(-1.0)        # only the last 50 count
        assert m.cell("V4.2", "15m")["n"] == 0

    def test_attack_needs_the_lower_bound(self):
        m = EdgeModelV4([obs(exit_ts=i, gross=0.6 + (0.01 if i % 2 else -0.01)) for i in range(300)])
        m.advance(10 ** 6)
        v = m.predict(sid="V4.1", tf="15m", coin="SUI", quality=0.5, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert v["attack_eligible"] and v["lower_r"] >= 0.05
        v2 = m.predict(sid="V4.1", tf="15m", coin="SUI", quality=0.5, regime="WITH", vol_band="MID", cost_r=0.5, stop_pct=0.01)
        assert not v2["attack_eligible"]


# ---- sizing -----------------------------------------------------------------------------------------------------------------

class TestSizing:
    class Sig:
        def __init__(self, edge):
            self.meta = {"edge": edge, "signal_quality": 0.5}

    def test_control_attacks_only_when_the_edge_model_and_health_allow(self):
        s = SizingV4(mode="CONTROL")
        assert s(self.Sig({"attack_eligible": True}), {"drawdown": 0.0, "r": []})[0] == 2.0
        assert s(self.Sig({"attack_eligible": False}), {"drawdown": 0.0, "r": []})[0] == 1.0
        assert s(self.Sig({"attack_eligible": True}), {"drawdown": 0.2, "r": []})[0] == 1.0      # NO_ATTACK
        assert s(self.Sig({"attack_eligible": True}), {"drawdown": 0.31, "r": []})[0] == 0.0     # HALTED
        assert SizingV4(mode="GATED")(self.Sig({"attack_eligible": True}), {"drawdown": 0.0, "r": []})[0] == 1.0

    def test_health_and_participation(self):
        assert health_state({"drawdown": 0.0, "r": [-1.0] * 12})[0] == "NO_ATTACK"
        assert min_trades("15m", 181.0) == 73 and min_trades("30m", 20.0) == 30


# ---- Jev V4 --------------------------------------------------------------------------------------------------------------------

def body(choice="SUPPORT", probs=None):
    return {"model": "typesafe/jev", "answers": {"conditions": {
        "type": "choice", "choice": choice, "probabilities": probs or {"CONTRADICT": 0.2, "SUPPORT": 0.6, "STRONGLY_SUPPORT": 0.2}}},
        "usage": {"input_tokens": 900, "output_tokens": 10, "cost": 0.00007}}


class TestJevV4:
    def test_the_question_and_its_mapping(self):
        text = str(jv4.QUESTIONS_V4)
        assert "already passed legality, cost and expected-edge checks" in text
        assert "CONTRADICT, SUPPORT or STRONGLY SUPPORT" in text
        assert jv4.ACTION_FOR == {"CONTRADICT": "SKIP", "SUPPORT": "TAKE", "STRONGLY_SUPPORT": "ATTACK"}
        from app.ai.jev.v3 import POLICY_V3, QUESTIONS_V3_FINGERPRINT
        assert jv4.QUESTIONS_V4_FINGERPRINT != QUESTIONS_V3_FINGERPRINT and jv4.POLICY_V4.fingerprint() != POLICY_V3.fingerprint()

    def test_strict_parse(self):
        d = jv4.parse_decision_v4(body("STRONGLY_SUPPORT"))
        assert d.risk_state == "ATTACK" and d.take_probability == pytest.approx(0.8)
        assert d.risk_probabilities == {"SKIP": 0.2, "TAKE": 0.6, "ATTACK": 0.2}
        with pytest.raises(SchemaError):
            jv4.parse_decision_v4(body("TAKE"))                       # actions are not answers
        with pytest.raises(SchemaError):
            jv4.parse_decision_v4(body(probs={"CONTRADICT": 0.5, "NEUTRAL": 0.5}))
        with pytest.raises(SchemaError):
            jv4.parse_decision_v4({"answers": {}})

    def test_policy(self):
        assert jv4.decide_v4(jv4.parse_decision_v4(body("SUPPORT")), "OK").level == "TAKE"
        skip = jv4.decide_v4(jv4.parse_decision_v4(body("CONTRADICT")), "OK")
        assert (skip.level, skip.multiplier) == ("SKIP", 0.0)
        att = jv4.decide_v4(jv4.parse_decision_v4(body("STRONGLY_SUPPORT")), "OK")
        assert (att.level, att.multiplier) == ("ATTACK", 2.0)
        sick = jv4.decide_v4(jv4.parse_decision_v4(body("STRONGLY_SUPPORT")), "NO_ATTACK")
        assert sick.level == "TAKE" and sick.extra["downgrade"]
        err = jv4.decide_v4(None, "OK", error_code="TIMEOUT")
        assert err.level == "SKIP" and "TIMEOUT" in err.reason

    def test_an_illegal_attack_becomes_take(self):
        lvl, mult, why = jv4._legal("ATTACK", 2.0, object(), lambda s, m: (False, "margin"), jv4.POLICY_V4)
        assert (lvl, mult) == ("TAKE", 1.0) and why.startswith("ATTACK_NOT_LEGAL")

    def test_random_matches_the_jev_distribution(self):
        class Sig:
            side, meta = "long", {"edge": {}}
        dist = {"SKIP": 0.3, "TAKE": 0.5, "ATTACK": 0.2}
        g1, g2 = jv4.PolicyGateV4("RANDOM", "bot", 3, dist), jv4.PolicyGateV4("RANDOM", "bot", 3, dist)
        for i in range(3000):
            ctx = {"ts": T0 + i * 60_000, "health": {"drawdown": 0.0, "r": []}}
            assert g1(Sig(), ctx).multiplier == g2(Sig(), ctx).multiplier
        chosen = [r["chosen"] for r in g1.rows]
        assert abs(chosen.count("SKIP") / 3000 - 0.3) < 0.03 and abs(chosen.count("ATTACK") / 3000 - 0.2) < 0.03
        assert jv4.action_distribution_v4([{"chosen": "TAKE"}, {"chosen": "ATTACK"}, {"chosen": None}]) == \
            {"TAKE": 1 / 3, "ATTACK": 1 / 3, "SKIP": 1 / 3}

    def test_state_v4_carries_the_v4_edge_block(self):
        from app.strategies.registry import load_v4
        res, seen, eng, _, _ = run_bot("V4.1", "15m", "RAW", jv4.ObserverGate())
        assert seen
        sig, bar = seen[-1]
        sig.meta["edge"] = {"net_r": 0.12, "lower_r": 0.02, "gross_r": 0.3, "cost_r": 0.18, "n": {"family": 77}}
        ts = bar.close_time
        series = {tf: [c for c in eng.ctx.candles(SYM, tf) if c.close_time <= ts] for tf in ("15m", "1h", "4h", "1m")}
        bot = {"strategy_id": "V4.1", "timeframe": "15m", "strategy_name": load_v4()["V4.1"].name, "hypothesis": "h"}
        st = jv4.JevStateBuilderV4().build(ts=ts, bot=bot, sig=sig, series=series,
                                           execution={"taker_fee": 0.00055, "half_spread_bps": 2.0},
                                           health={"drawdown": 0.0}, position={}).state
        assert st["state_version"] == "JEV_STATE_V4" and st["expected_edge"]["predicted_net_r"] == 0.12
        assert st["expected_edge"]["evidence_setups"] == 77 and "outcome" not in str(st).lower()


# ---- arena, field, analyzer ------------------------------------------------------------------------------------------------

class TestArenaAndAnalyzer:
    def test_raw_observes_and_control_is_edge_gated(self):
        from app.competition.v31_arena import observation_rows
        from app.competition.v31_edge import load_evidence
        res, seen, eng, _, kinds = run_bot("V4.1", "15m", "RAW", gate := jv4.ObserverGate())
        rows = observation_rows(gate, res, kinds, 0)
        assert rows and all(r["cost_r"] is not None for r in rows)
        rec = {"identity": {"strategy_id": "V4.1", "timeframe": "15m", "coin": "SUI"}, "observations": rows}
        ev = load_evidence([rec])
        res2, _, _, edge_gate, _ = run_bot("V4.1", "15m", "CONTROL", evidence=ev * 10)
        assert edge_gate is not None and edge_gate.rows
        assert all(r["status"] in ("PASS", "REJECT") for r in edge_gate.rows)
        assert res2.rejects.get("cost_gate", 0) == sum(1 for r in edge_gate.rows if r["status"] != "PASS")

    def test_field_is_by_activity_only(self):
        def c(coin, entries, net):
            return {"identity": {"role": "CONTROL", "strategy_id": "V4.1", "coin": coin, "timeframe": "15m"},
                    "activity": {"entries": entries}, "metrics": {"net_profit": net}}
        f = select_field([c("HYPE", 80, -5.0), c("ARB", 60, 9.0), c("ENA", 60, 1.0), c("TAO", 20, 50.0)], V4Config())
        assert [p["coin"] for p in f["pairs"]] == ["HYPE", "ARB"]

    def test_the_root_cause_comes_first(self):
        r = vrec(n=60, net_each=-0.05, gross_each=-0.02)
        a = an.analyze_bot(r, V4Config())
        assert a["failure_mode"] == "NO_RAW_EDGE" and a["state"] == "FAIL"
        raw_gate = next(g for g in a["gates"] if g["name"] == "raw edge (gross expectancy)")
        assert raw_gate["ok"] is False
        costly = an.analyze_bot(vrec(n=60, net_each=-0.01, gross_each=0.02), V4Config())
        assert costly["failure_mode"] == "COST_DESTROYED"

    def test_a_healthy_significant_bot_advances(self):
        a = an.analyze_bot(vrec(n=80, net_each=0.3, gross_each=0.32, days=30.0), V4Config())
        assert a["state"] == "ADVANCE", [g for g in a["gates"] if not g["ok"]]
        assert a["p_mean_le_0"] <= 0.10

    def test_noise_is_not_distinguishable_from_zero(self):
        a = an.analyze_bot(vrec(n=40, net_each=0.3, gross_each=0.31, days=30.0, pattern=(1, -1, 1, -1, 1)), V4Config())
        g = next(x for x in a["gates"] if x["name"] == "distinguishable from zero")
        assert g["ok"] is False

    def test_attack_test_counterfactual(self):
        trades = [{"net": 0.4, "r": 1.0, "jev_level": "ATTACK", "jev_mult": 2.0}, {"net": -0.2, "r": -0.5, "jev_level": "ATTACK", "jev_mult": 2.0},
                  {"net": 0.1, "r": 0.5, "jev_level": "TAKE", "jev_mult": 1.0}]
        x = an.attack_test(trades, "JEV")
        assert x["attack_trades"] == 2 and x["same_trades_at_normal_size"] == pytest.approx(0.1)
        assert x["attack_added_net"] == pytest.approx(0.1) and x["attack_win_rate"] == 0.5

    def test_capacity_never_qualifies_and_separates_bad_from_constrained(self):
        good = {"metrics": {"net_profit": 3.0, "gross_pnl": 4.0, "trades": 60}}
        bad = {"metrics": {"net_profit": -3.0, "gross_pnl": -1.0, "trades": 60}}
        assert an.capacity_verdict(bad, {"state": "FAIL", "diagnoses": ["NO_RAW_EDGE"]}, [bad, bad]) == "BAD"
        assert an.capacity_verdict(bad, {"state": "FAIL", "diagnoses": ["MIN_NOTIONAL_LIMITED"]}, [good]) == "ACCOUNT_SIZE_CONSTRAINED"
        assert an.capacity_verdict(good, {"state": "ADVANCE", "diagnoses": []}, [bad]) == "OK_AT_20"

    def test_raw_edge_is_measured_before_any_gate(self):
        raw = [{"identity": {"strategy_id": "V4.1", "timeframe": "15m"}, "observations": [
            {"sequenced": True, "net_r": 0.4, "cost_r": 0.1}, {"sequenced": True, "net_r": -1.1, "cost_r": 0.1},
            {"sequenced": False, "net_r": 9.0, "cost_r": 0.1}, {"sequenced": True, "net_r": -0.3, "cost_r": 0.1}]}]
        t = an.raw_edge_table(raw, lambda r: r["identity"]["strategy_id"])
        assert t[0]["setups"] == 3                                   # an unsequenced (overlapping) setup never counts
        assert t[0]["gross_r"] == pytest.approx((0.5 - 1.0 - 0.2) / 3, abs=1e-4)
        assert t[0]["diagnosis"] == "NO RAW EDGE"

    def test_edge_quality_table(self):
        rows = an.edge_quality_table([vrec(n=10, net_each=-0.05, gross_each=0.01)], lambda r: "g")
        assert rows[0]["diagnosis"] == "EDGE DESTROYED BY COSTS" and rows[0]["gross_per_fee_dollar"] is not None


def vrec(role="CONTROL", n=60, net_each=0.05, gross_each=0.06, tf="15m", days=30.0, pattern=(1, 1, -1)):
    trades = []
    for i in range(n):
        s = pattern[i % len(pattern)] if net_each > 0 else 1
        net = net_each * s
        gross = gross_each * s if net_each > 0 else gross_each
        trades.append({"pid": f"p{i}", "side": "long", "entry_ts": T0 + i * 3_600_000, "exit_ts": T0 + i * 3_600_000 + 7_200_000,
                       "hold_s": 7200, "qty": 1.0, "entry": 2.0, "exit": 2.0 + gross, "gross": gross, "fees": 0.008,
                       "slippage": gross - net - 0.008, "funding": 0.0, "net": net, "r": net / 0.2, "exit_kind": "trail",
                       "risk_usd": 0.2, "notional": 20.0, "tier": "TAKE", "risk_pct": 0.01})
    nets = [t["net"] for t in trades]
    win, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    return {"identity": {"strategy_id": "V4.1", "coin": "SUI", "timeframe": tf, "role": role}, "role": role,
            "key": f"V4.1-SUI-{tf}-{role}", "pair_id": f"v4pair:V4.1-SUI-{tf}", "window": {"days": days}, "trades": trades,
            "decisions": [], "metrics": {"net_profit": sum(nets), "gross_pnl": sum(t["gross"] for t in trades),
                                         "expectancy_r": sum(t["r"] for t in trades) / n,
                                         "profit_factor": win / loss if loss else 999.0, "max_drawdown_pct": 0.05,
                                         "liquidation_count": 0, "net_return_pct": sum(nets) / 20.0, "fees_paid": 0.008 * n,
                                         "slippage_cost": sum(t["slippage"] for t in trades), "funding_paid": 0.0,
                                         "win_rate": 0.66, "trades": n},
            "activity": {"signals": 3 * n, "halted": False, "below_exchange_minimum": 0, "edge_rejected": n},
            "funnel": {"raw_setups": 3 * n, "legal": 2 * n, "positive_edge": n, "jev_accepted": None, "executed": n}}


# ---- configuration and storage ---------------------------------------------------------------------------------------------

class TestConfigAndStorage:
    def test_allocation_windows_and_identity(self):
        total = 7 * sum(COINS_PER_TF.values())
        shares = {tf: 7 * n / total for tf, n in COINS_PER_TF.items()}
        assert total == 140 and shares == pytest.approx({"3m": 0.15, "5m": 0.20, "15m": 0.35, "30m": 0.30})
        assert not set(DEV.months) & set(TEST.months)
        assert not set(TEST.months) & {"2026-05", "2026-06", "2026-07", "2026-08"}     # the consumed V3.1 TEST window
        assert TEST.trade_to < DEV.trade_from
        i = V4Identity("V4.3", "HYPE", "5m", "RANDOM", seed=4, jev_policy="JEV_POLICY_V4")
        assert i.key == "V4.3-HYPE-5m-RAND4@20x" and i.pair_id == "v4pair:V4.3-HYPE-5m" and i.context_tfs == ("15m", "1h")
        assert V4Config().fingerprint() == V4Config().fingerprint()

    def test_report_and_payload_render_a_finished_run(self, tmp_path):
        from app.core.report_v4 import v4_html, v4_text
        from app.core.system_view import v4_payload
        st = Storage(str(tmp_path / "r.db"))
        st.v4_run_start({"run_id": "v4-aaaaaaaaaa", "created_ts": 1, "dataset_role": "DEVELOPMENT", "config": {},
                         "config_fingerprint": "f"})
        summary = {"window": {"from": "2025-11-01", "to": "2026-04-30"}, "universe": {"15m": ["HYPE"]},
                   "counts": {"controls": 1, "jev_bots": 0}, "answers": {"intraday_edge": "NO", "jev_selection": "NOT TESTED"},
                   "advanced_set": "NONE", "passed": {"controls": [], "jev_bots": []},
                   "raw_edge": {"by_tf": [{"group": "15m", "setups": 10, "gross_r": -0.05, "t": -1.2, "cost_r": 0.12,
                                           "net_r": -0.17, "diagnosis": "NO RAW EDGE"}], "by_family": []},
                   "edge_quality": {"by_tf": [{"group": "15m", "bots": 1, "trades": 3, "gross": -0.1, "fees": 0.05,
                                               "slippage": 0.01, "net": -0.16, "gross_bps_of_turnover": -4.0,
                                               "diagnosis": "NO RAW EDGE"}]},
                   "exits": {"by_tf": {"15m": {"trades": 3, "flags": ["NO EXIT FLAG"]}}},
                   "failure_modes": {"controls": {"NO_RAW_EDGE": 1}}, "jev_pooled": {"decisions": 0}}
        st.v4_run_update("v4-aaaaaaaaaa", status="complete", summary_json=summary)
        d = v4_payload(st)
        assert d["dev"]["advanced_set"] == "NONE" and d["dev"]["raw_edge"]["by_tf"][0]["group"] == "15m"
        html, text = v4_html(d), "\n".join(v4_text(d))
        assert "ADVANCED SET: NONE" in html and "Raw edge before any gate" in html
        assert "ADVANCED SET NONE" in text and "raw 15m" in text
        st.close()

    def test_storage_is_separate(self, tmp_path):
        st = Storage(str(tmp_path / "s.db"))
        st.v4_run_start({"run_id": "v4-x", "created_ts": 1, "dataset_role": "DEVELOPMENT", "config": {}, "config_fingerprint": "f"})
        r = vrec()
        r.update({"balance": 20.0, "equity": [[1, 20.0]]})
        st.v4_bot_save("v4-x", r, ts=5)
        assert st.v4_bot_keys("v4-x") == {r["key"]} and st.v4_bots("v4-x", heavy=True)[0]["trades"]
        assert st.v31_runs() == [] and st.v3_runs() == []
        st.close()
