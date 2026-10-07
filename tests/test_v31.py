"""V3.1 AGGRESSIVE EDGE: the frozen V3 stays frozen, the continuation families are honest and hold their
runners, the expected-net-edge model is causal and never manufactures confidence, AGGRESSIVE_V31 has no
DEFENSIVE size, Jev V3 (strict parsing, SKIP / TAKE / ATTACK, never an invalid order), the matched
random action, the maker-first experiment, the Bot Analyzer V3.1, storage and the read-only routes.
No network: Jev is a stub and the tape is synthetic."""
from __future__ import annotations

import random

import pytest

from app.ai.jev import v3 as jv3
from app.ai.jev.models import JevOutcome, SchemaError
from app.competition import v31_analyzer as an
from app.competition import v31_edge as ed
from app.competition.v31_arena import V31Arena, select_field
from app.competition.v31_config import (AGGRESSIVE_V31, EdgeConfig, MakerConfig, SizingV31, V31Config, V31Identity,
                                        attack_tier, health_state, min_trades)
from app.competition.v31_diagnostics import Tape
from app.competition.v31_maker import simulate, verdict
from app.core.storage import Storage
from app.core.types import Candle, MarketRules
from tests.conftest import T0, settings_factory

SYM = "SUIUSDT"


def tape(n: int = 12000, seed: int = 5, start: float = 2.0, drift: float = 0.0) -> list[Candle]:
    """A seeded random walk with volatility regimes, trends, volume bursts and taker flow."""
    rng = random.Random(seed)
    px, out, vol, mu = start, [], 0.0015, 0.0
    for i in range(n):
        if i % 700 == 0:
            vol = rng.choice((0.0006, 0.0012, 0.0025))
            mu = rng.choice((-0.0004, 0.0, 0.0004)) + drift
        burst = rng.random() < 0.02
        ret = rng.gauss(mu, vol * (3.0 if burst else 1.0))
        o = px
        c = max(0.01, px * (1.0 + ret))
        hi = max(o, c) * (1.0 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0, vol / 2)))
        v = rng.uniform(800, 1200) * (4.0 if burst else 1.0)
        share = 0.5 + (0.25 if ret > 0 else -0.25) * (1.0 if burst else 0.3)
        t = T0 + i * 60_000
        out.append(Candle(SYM, "1m", t, o, hi, lo, c, v, t + 59_999, True, v * c,
                          int(rng.uniform(80, 120) * (3.0 if burst else 1.0)), "backfill", v * share))
        px = c
    return out


def rules() -> dict[str, MarketRules]:
    return {SYM: MarketRules(SYM, 0.0001, 1.0, 1.0, 5.0, 0.025, 6, 2)}


def arena(evidence=()) -> V31Arena:
    cfg = V31Config(months=(), trade_from="2025-11-01", trade_to="2026-04-30")
    return V31Arena(settings_factory(balance=20), cfg, rules(), evidence)


def run_bot(sid: str, tf: str, role: str = "RAW", gate=None, evidence=(), bars=None):
    from app.live.runner import with_sink
    a = arena(evidence)
    ident = V31Identity(sid, "SUI", tf, role, jev_policy="JEV_POLICY_V3" if role in ("JEV", "TAKE", "RANDOM") else "")
    cls = a.bound(ident)
    seen: list = []
    live = with_sink(cls, lambda sigs, c: seen.extend((s, c) for s in sigs))
    eng, edge_gate, kinds = a.engine(ident, gate)
    bars = bars or tape()
    res = eng.run(live, iter(bars), since_ms=bars[500].close_time, leverage=20, signal_tf=tf, only_symbol=SYM)
    return res, seen, eng, edge_gate, kinds


def obs(sid="S33.1", tf="3m", coin="SUI", ts=0, exit_ts=1, gross=0.3, qb="MID", regime="WITH", vol="MID", tp=False, stop=False):
    return {"sid": sid, "tf": tf, "coin": coin, "ts": ts, "exit_ts": exit_ts, "qband": qb, "regime": regime, "vol_band": vol,
            "gross_r": gross, "net_r": gross - 0.1, "cost_r": 0.1, "tp_hit": tp, "stopped": stop, "sequenced": True}


# ---- V3 stays frozen -------------------------------------------------------------------------------------

class TestV3Frozen:
    def test_v3_sources_and_jev_v2_are_unchanged(self):
        import re
        from pathlib import Path
        from app.ai.jev.v2 import v2_fingerprints
        from app.strategies.registry import v3_fingerprints
        doc = Path("docs/V3_FREEZE.md").read_text(encoding="utf-8")
        frozen = dict(re.findall(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*`([0-9a-f]{12,16})`", doc, re.M))
        fps = {**v3_fingerprints(), **{k: v for k, v in v2_fingerprints().items() if k in ("prompt", "policy")}}
        assert fps and all(frozen.get(k) == v for k, v in fps.items()), {k: (v, frozen.get(k)) for k, v in fps.items()}

    def test_the_v3_run_is_refused_by_its_script(self):
        from scripts.run_v3_arena import frozen_runs
        assert "v3-e39e94890b" in frozen_runs()

    def test_the_v31_development_run_is_frozen_and_the_test_is_preregistered(self):
        import json
        from pathlib import Path
        from scripts.run_v31_arena import fingerprints, frozen_runs
        assert {"v31-46d2e9af7d", "v31-04f1291069"} <= frozen_runs()      # DEVELOPMENT and the pre-registered TEST
        pre = json.loads(Path("docs/V31_TEST_PREREGISTRATION.json").read_text(encoding="utf-8"))
        assert pre["fingerprints"] == fingerprints()                     # the frozen sources are the running sources
        assert pre["evidence"]["run_id"] == "v31-46d2e9af7d" and pre["field"]["n"] == len(pre["field"]["pairs"]) == 30
        assert pre["data"]["archive_dir"].endswith("v31test") and pre["data"]["trade_from"] == "2026-05-01"
        assert pre["config"]["dataset_role"] == "TEST" and "dataset_fingerprint" not in pre["config"]

    def test_v31_is_never_merged_into_other_loaders(self):
        from app.strategies.registry import load_all, load_v3, load_v31
        v31 = load_v31()
        assert sorted(v31) == ["S33.1", "S35.1", "S37"]
        assert not set(v31) & set(load_v3()) and not set(v31) & set(load_all())
        assert all(c.version == "v3.1" for c in v31.values())


# ---- strategies ------------------------------------------------------------------------------------------

class TestStrategies:
    def test_binding_and_documentation(self):
        from app.strategies.registry import load_v31
        for sid, cls in load_v31().items():
            for field in ("hypothesis", "why_v31", "expected_hold", "expected_frequency", "thesis"):
                assert getattr(cls, field), (sid, field)
            b = cls.for_timeframe("3m")
            assert b.timeframes == ("3m", "15m", "1h", "4h", "1m") and (b.ctx_fast, b.ctx_slow) == ("15m", "1h")
            assert cls.for_timeframe("30m").timeframes == ("30m", "1h", "4h", "1m")
            with pytest.raises(ValueError):
                cls.for_timeframe("1m")          # no pure 1m micro-scalper in V3.1

    @pytest.mark.parametrize("sid,tf", [("S33.1", "3m"), ("S35.1", "3m"), ("S37", "5m"), ("S33.1", "5m")])
    def test_entries_are_honest_long_hold_runners(self, sid, tf):
        res, seen, _, _, _ = run_bot(sid, tf, "RAW", jv3.ObserverGate())
        for sig, bar in seen:
            m = sig.meta
            assert bar.tf == tf and sig.ts == bar.close_time
            assert 0.006 - 1e-12 <= m["stop_pct"] <= 0.025 + 1e-12
            assert sig.trail is not None and sig.trail.tf == "1h" and sig.trail.kind == "atr"
            assert sig.max_hold_s == 12 * 3600 and sig.be_at_r >= 1.5
            assert len(sig.take_profits) == 1 and sig.take_profits[0].fraction < 0.5      # a partial, then the runner
            assert m["regime"] in ("WITH", "AGAINST", "FLAT", "UNKNOWN") and m["vol_band"] in ("LOW", "MID", "HIGH", "UNKNOWN")
            assert 0.0 <= m["signal_quality"] <= 1.0 and m["expected_move_pct"] > 0
        assert not res.trades                                      # the observer never trades

    def test_flow_needs_reported_taker_volume(self):
        from app.strategies.registry import load_v31
        cls = load_v31()["S35.1"].for_timeframe("5m")
        c = Candle(SYM, "5m", T0, 1, 1.1, 0.9, 1.05, 100.0, T0 + 299_999, True, 100, 10, "backfill", 0.0)
        assert cls().on_candle(c, None) == []

    def test_impulse_must_be_fresh(self):
        """S37 takes the bar that FIRST crosses the stretch, never the second bar of a run."""
        from app.strategies.registry import load_v31
        cls = load_v31()["S37"].for_timeframe("15m")

        class Ctx:
            def __init__(self, closes):
                self.cs = [Candle(SYM, "15m", T0 + i * 900_000, c, c + 0.1, c - 0.1 if i < len(closes) - 1 else c - 0.9, c, 100.0,
                                  T0 + i * 900_000 + 899_999, True, 100.0, 10, "backfill", 50.0) for i, c in enumerate(closes)]

            def candles(self, sym, tf):
                return self.cs if tf == "15m" else []

            def ind(self, sym, tf, name, n=14):
                return {"ema": [10.0] * len(self.cs), "atr": [1.0] * len(self.cs), "rsi": [80.0] * len(self.cs)}[name]

        strat = cls()
        fresh = strat.impulse(Ctx([10.0] * 58 + [10.5, 13.0]), SYM)          # previous bar inside the stretch
        assert fresh is not None and fresh["side"] == "long" and fresh["z"] == pytest.approx(3.0)
        assert strat.impulse(Ctx([10.0] * 58 + [12.6, 13.0]), SYM) is None   # the run had already started


# ---- the expected-net-edge model ---------------------------------------------------------------------------

class TestEdgeModel:
    def test_insufficient_evidence_is_never_a_pass(self):
        m = ed.EdgeModel([obs(exit_ts=i, gross=2.0) for i in range(10)], EdgeConfig(min_family_n=30))
        m.advance(10_000)
        v = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.9, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert v["status"] == "REJECT" and v["reason"] == "INSUFFICIENT_EVIDENCE" and not v["attack_eligible"]

    def test_causal_only_what_exited_before_t(self):
        ev = [obs(exit_ts=100 + i, gross=1.0) for i in range(40)] + [obs(exit_ts=10_000 + i, gross=-5.0) for i in range(40)]
        m = ed.EdgeModel(ev)
        m.advance(5_000)
        assert m.absorbed == 40
        v = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.5, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert v["status"] == "PASS" and v["gross_r"] > 0
        with pytest.raises(ValueError):
            m.advance(4_000)                      # time never goes backwards

    def test_shrinkage_pulls_a_tiny_bucket_to_its_parent_and_zero(self):
        ev = [obs(exit_ts=i, gross=0.2, coin="ZEC") for i in range(200)] + [obs(exit_ts=500 + i, gross=5.0, coin="SUI", qb="HIGH") for i in range(3)]
        m = ed.EdgeModel(ev)
        m.advance(10_000)
        v = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.9, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert v["n"]["exact"] == 3 and v["gross_r"] < 1.0          # 3 lucky trades do not make a 5R edge
        fam_only = sum(o["gross_r"] for o in ev) / (len(ev) + 30)
        assert v["gross_r"] > fam_only * 0.5

    def test_attack_needs_lower_bound_headroom_and_quality(self):
        ev = [obs(exit_ts=i, gross=0.9 + (0.2 if i % 2 else -0.2)) for i in range(2000)]
        m = ed.EdgeModel(ev)
        m.advance(10 ** 9)
        hi = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.9, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert hi["status"] == "PASS" and hi["attack_eligible"] and hi["exceptional"]
        low_q = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.3, regime="WITH", vol_band="MID", cost_r=0.1, stop_pct=0.01)
        assert low_q["status"] == "PASS" and not low_q["attack_eligible"] and "quality" in low_q["attack_block"]
        costly = m.predict(sid="S33.1", tf="3m", coin="SUI", quality=0.9, regime="WITH", vol_band="MID", cost_r=0.6, stop_pct=0.01)
        assert not costly["attack_eligible"]
        assert hi["headroom_bps"] == pytest.approx(hi["gross_bps"] - hi["cost_bps"], abs=0.02)

    def test_cost_in_r_and_bands(self):
        assert ed.cost_r_of(0.00055, 2.0, 0.01) == pytest.approx((0.0011 + 0.0004) / 0.01)
        assert ed.cost_r_of(0.00055, 2.0, 0.0) is None
        assert [ed.qband(x) for x in (0.1, 0.5, 0.7, None)] == ["LOW", "MID", "HIGH", "UNKNOWN"]

    def test_sequence_emulates_an_ungated_bot(self):
        cands = [{"id": "a", "ts": 0, "exit_ts": 100}, {"id": "b", "ts": 50, "exit_ts": 120},
                 {"id": "c", "ts": 100, "exit_ts": 300}, {"id": "d", "ts": 350, "exit_ts": None}, {"id": "e", "ts": 400, "exit_ts": 500}]
        ed.sequence(cands, cooldown_ms=200)
        assert [c["id"] for c in cands if c["sequenced"]] == ["a", "e"]   # b overlaps, c in cooldown, d has no outcome

    def test_calibration_rank_statistics(self):
        rows = [{"net_r": x / 10.0, "realized_r": x / 10.0 + (0.05 if x % 2 else -0.05)} for x in range(-10, 11)]
        c = ed.calibration(rows)
        assert c["n"] == 21 and c["spearman"] > 0.95 and c["slope"] > 0.8
        assert sum(b["n"] for b in c["buckets"]) == 21


# ---- AGGRESSIVE_V31 sizing -----------------------------------------------------------------------------------

class TestAggressiveV31:
    def test_no_defensive_size_and_the_halt(self):
        s, _ = health_state({"drawdown": 0.10, "r": [-1.0] * 5})
        assert s == "OK"
        assert health_state({"drawdown": 0.19, "r": []})[0] == "NO_ATTACK"
        assert health_state({"drawdown": 0.05, "r": [-1.0] * 12})[0] == "NO_ATTACK"
        assert health_state({"drawdown": 0.31, "r": []})[0] == "HALTED"
        assert AGGRESSIVE_V31.defensive_multiplier == 1.0

    def test_attack_tier_rules(self):
        good = {"attack_eligible": True, "exceptional": True}
        assert attack_tier(good, "OK", "STRONG_ATTACK") == ("STRONG_ATTACK", "")
        assert attack_tier({"attack_eligible": True, "exceptional": False}, "OK", "STRONG_ATTACK")[0] == "ATTACK"
        assert attack_tier(good, "NO_ATTACK", "ATTACK")[0] == "TAKE"
        assert attack_tier({"attack_eligible": False}, "OK", "ATTACK")[0] == "TAKE"

    def test_sizing_modes(self):
        class S:
            meta = {"edge": {"attack_eligible": True, "exceptional": False}, "signal_quality": 0.7}
        ctl, info = SizingV31(mode="CONTROL")(S(), {"drawdown": 0.0, "r": []})
        assert ctl == pytest.approx(1.5) and info["tier"] == "ATTACK"
        gated, info = SizingV31(mode="GATED")(S(), {"drawdown": 0.0, "r": []})
        assert gated == 1.0 and info["tier"] == "TAKE"                  # Jev decides ATTACK, not the sizing hook
        halted, _ = SizingV31(mode="CONTROL")(S(), {"drawdown": 0.35, "r": []})
        assert halted == 0.0

    def test_participation_minimums(self):
        assert min_trades("3m", 181) == 362 and min_trades("5m", 181) == 272
        assert min_trades("15m", 181) == 91 and min_trades("30m", 181) == 46
        assert min_trades("30m", 10) == 30                                # adequate sample floor


# ---- Jev V3 --------------------------------------------------------------------------------------------------

def body(choice="TAKE", support=0.6, probs=None):
    return {"model": "typesafe/jev-1.13-x", "answers": {
        "support": {"type": "noul", "noul": support},
        "action": {"type": "choice", "choice": choice, "probabilities": probs or {"SKIP": 0.2, "TAKE": 0.6, "ATTACK": 0.2}}},
        "usage": {"input_tokens": 900, "output_tokens": 10, "cost": 0.00007}}


class TestJevV3:
    def test_strict_parse(self):
        d = jv3.parse_decision_v3(body())
        assert d.risk_state == "TAKE" and d.take_probability == 0.6 and d.risk_probabilities["ATTACK"] == 0.2
        with pytest.raises(SchemaError):
            jv3.parse_decision_v3(body(choice="DEFENSIVE"))              # no DEFENSIVE in V3
        with pytest.raises(SchemaError):
            jv3.parse_decision_v3(body(probs={"SKIP": 0.5, "NORMAL": 0.5}))
        with pytest.raises(SchemaError):
            jv3.parse_decision_v3({"answers": {"support": {"type": "noul", "noul": 0.5}}})

    def test_versions_are_separate_from_v1_and_v2(self):
        from app.ai.jev.v2 import QUESTIONS_V2_FINGERPRINT, POLICY_V2
        assert jv3.QUESTIONS_V3_FINGERPRINT != QUESTIONS_V2_FINGERPRINT
        assert jv3.POLICY_V3.fingerprint() != POLICY_V2.fingerprint()
        assert "DEFENSIVE" not in dict(jv3.POLICY_V3.multipliers)
        text = str(jv3.QUESTIONS_V3)
        assert "already passed deterministic legality, execution-cost and positive-edge checks" in text
        assert "SUPPORT or CONTRADICT" in text

    def test_policy(self):
        edge_ok = {"attack_eligible": True, "exceptional": True}
        take = jv3.decide_v3(jv3.parse_decision_v3(body("TAKE")), edge_ok, "OK")
        assert (take.level, take.multiplier) == ("TAKE", 1.0)
        skip = jv3.decide_v3(jv3.parse_decision_v3(body("SKIP")), edge_ok, "OK")
        assert (skip.level, skip.multiplier) == ("SKIP", 0.0)
        strong = jv3.decide_v3(jv3.parse_decision_v3(body("ATTACK", probs={"SKIP": 0.1, "TAKE": 0.2, "ATTACK": 0.7})), edge_ok, "OK")
        assert (strong.level, strong.multiplier) == ("STRONG_ATTACK", 2.0)
        weak = jv3.decide_v3(jv3.parse_decision_v3(body("ATTACK", probs={"SKIP": 0.1, "TAKE": 0.5, "ATTACK": 0.4})), edge_ok, "OK")
        assert (weak.level, weak.multiplier) == ("ATTACK", 1.5)
        blocked = jv3.decide_v3(jv3.parse_decision_v3(body("ATTACK")), {"attack_eligible": False}, "OK")
        assert blocked.level == "TAKE" and blocked.extra["downgrade"]
        err = jv3.decide_v3(None, edge_ok, "OK", error_code="TIMEOUT")
        assert err.level == "SKIP" and "TIMEOUT" in err.reason

    def test_an_attack_that_is_not_legal_becomes_take_never_a_veto(self):
        lvl, mult, why = jv3._legal_level("ATTACK", 1.5, object(), lambda s, m: (False, "margin"), jv3.POLICY_V3)
        assert (lvl, mult) == ("TAKE", 1.0) and why.startswith("ATTACK_NOT_LEGAL")
        assert jv3._legal_level("TAKE", 1.0, object(), lambda s, m: (False, "x"), jv3.POLICY_V3)[:2] == ("TAKE", 1.0)

    def test_random_matched_action_is_deterministic_and_matches_rates(self):
        class Sig:
            side, meta = "long", {"edge": {"attack_eligible": True, "exceptional": False}}
        dist = {"SKIP": 0.3, "TAKE": 0.5, "ATTACK": 0.2}
        g1 = jv3.PolicyGateV3("RANDOM", "bot", seed=3, distribution=dist)
        g2 = jv3.PolicyGateV3("RANDOM", "bot", seed=3, distribution=dist)
        for i in range(3000):
            ctx = {"ts": T0 + i * 60_000, "health": {"drawdown": 0.0, "r": []}}
            assert g1(Sig(), ctx).multiplier == g2(Sig(), ctx).multiplier
        chosen = [r["chosen"] for r in g1.rows]
        assert abs(chosen.count("SKIP") / 3000 - 0.3) < 0.03 and abs(chosen.count("ATTACK") / 3000 - 0.2) < 0.03
        rates, strong = jv3.action_distribution_v3([{"chosen": "TAKE"}, {"chosen": "ATTACK", "p_attack": 0.7}, {"chosen": None}])
        assert rates == {"TAKE": 1 / 3, "ATTACK": 1 / 3, "SKIP": 1 / 3} and strong == 1.0

    def test_state_v3_adds_thesis_edge_and_the_ladder_without_look_ahead(self):
        from app.ai.jev.state import LookAheadError
        from app.strategies.registry import load_v31
        res, seen, eng, _, _ = run_bot("S33.1", "3m", "RAW", jv3.ObserverGate())
        assert seen
        sig, bar = seen[-1]
        sig.meta["edge"] = {"net_r": 0.2, "lower_r": 0.1, "attack_eligible": False}
        ts = bar.close_time
        series = {tf: [c for c in eng.ctx.candles(SYM, tf) if c.close_time <= ts] for tf in ("3m", "15m", "1h", "4h", "1m")}
        bot = {"strategy_id": "S33.1", "timeframe": "3m", "strategy_name": load_v31()["S33.1"].name, "hypothesis": "h"}
        snap = jv3.JevStateBuilderV3().build(ts=ts, bot=bot, sig=sig, series=series, execution={"taker_fee": 0.00055, "half_spread_bps": 2.0},
                                            health={"drawdown": 0.0}, position={})
        st = snap.state
        assert st["state_version"] == "JEV_STATE_V3" and st["expected_edge"]["predicted_net_r"] == 0.2
        assert "ladder" in st["multi_timeframe"] and st["thesis"]["direction"] == sig.side
        assert "outcome" not in str(st).lower() and "leaderboard" not in str(st).lower()
        late = dict(series)
        late["1h"] = series["1h"] + [Candle(SYM, "1h", ts + 1, 1, 1, 1, 1, 1, ts + 3_600_000, True, 1, 1, "x", 0.5)]
        with pytest.raises(LookAheadError):
            jv3.JevStateBuilderV3().build(ts=ts, bot=bot, sig=sig, series=late, execution={}, health={}, position={})


# ---- the arena pipeline ------------------------------------------------------------------------------------------

class TestArena:
    def test_raw_observes_and_control_is_edge_gated(self):
        from app.competition.v31_arena import observation_rows
        res, seen, eng, _, kinds = run_bot("S33.1", "3m", "RAW", gate := jv3.ObserverGate())
        rows = observation_rows(gate, res, kinds, 0)
        assert rows and all(r["cost_r"] is not None for r in rows)
        assert any(r.get("sequenced") for r in rows)
        rec = {"identity": {"strategy_id": "S33.1", "timeframe": "3m", "coin": "SUI"}, "observations": rows}
        ev = ed.load_evidence([rec])
        res2, _, eng2, edge_gate, _ = run_bot("S33.1", "3m", "CONTROL", evidence=ev * 10)
        assert edge_gate is not None and edge_gate.rows
        for r in edge_gate.rows:
            assert r["status"] in ("PASS", "REJECT") and r["legal"] in (True, False)
        passed = {r["id"] for r in edge_gate.rows if r["status"] == "PASS"}
        assert res2.rejects.get("cost_gate", 0) == sum(1 for r in edge_gate.rows if r["status"] != "PASS")
        assert len([f for f in res2.fills if f.kind == "entry"]) <= len(passed)

    def test_field_selection_is_by_activity_only(self):
        def c(sid, coin, tf, entries, net):
            return {"identity": {"role": "CONTROL", "strategy_id": sid, "coin": coin, "timeframe": tf},
                    "activity": {"entries": entries}, "metrics": {"net_profit": net}}
        rows = [c("S37", "ZEC", "15m", 80, -5.0), c("S37", "SUI", "15m", 60, 9.0), c("S37", "ARB", "15m", 60, 1.0),
                c("S37", "UNI", "15m", 20, 50.0)]
        f = select_field(rows, V31Config())
        assert [p["coin"] for p in f["pairs"]] == ["ZEC", "ARB", "SUI"]     # most active first, ties alphabetical, UNI < 30
        assert {t["timeframe"] for t in f["thin_slots"]} == {"3m", "5m", "30m"} and not f["meets_minimum"]


# ---- maker-first ------------------------------------------------------------------------------------------------

class TestMaker:
    def trade(self, **k):
        t = {"side": "long", "qty": 10.0, "entry": 2.0004, "decision_price": 2.0, "exit": 2.04, "fees": 0.022,
             "net": 0.37, "risk_usd": 0.2, "entry_ts": T0 + 400, "exit_ts": T0 + 3_600_000, "funding": 0.0}
        t.update(k)
        return t

    def tape_of(self, lows):
        cs = [Candle(SYM, "1m", T0 + i * 60_000, 2.0, 2.01, lo, 2.0, 1, T0 + i * 60_000 + 59_999, True, 1, 1, "x", 0.5)
              for i, lo in enumerate(lows)]
        return Tape(cs + [Candle(SYM, "1m", T0 + (len(lows) + i) * 60_000, 2.0, 2.01, 1.999, 2.005, 1, T0 + (len(lows) + i) * 60_000 + 59_999,
                                 True, 1, 1, "x", 0.5) for i in range(120)])

    def test_touch_is_not_a_conservative_fill(self):
        cfg = MakerConfig()
        tp = self.tape_of([2.0] * 20)                    # touches the decision price, never trades through
        opt = simulate(self.trade(), tp, variant="optimistic", wait_min=5, cfg=cfg, taker_fee=0.00055, maker_fee=0.0002)
        con = simulate(self.trade(), tp, variant="conservative", wait_min=5, cfg=cfg, taker_fee=0.00055, maker_fee=0.0002, tick=0.0001)
        assert opt["status"] == "FILLED" and opt["net"] > 0.37           # better price, maker fee
        assert con["status"] in ("CONVERTED", "MISSED")

    def test_runaway_is_missed_and_verdicts(self):
        cfg = MakerConfig()
        cs = [Candle(SYM, "1m", T0 + i * 60_000, 2.0 + i * 0.01, 2.0 + i * 0.01 + 0.005, 2.0 + i * 0.01, 2.0 + i * 0.01 + 0.004, 1,
                     T0 + i * 60_000 + 59_999, True, 1, 1, "x", 0.5) for i in range(60)]
        r = simulate(self.trade(), Tape(cs), variant="conservative", wait_min=5, cfg=cfg, taker_fee=0.00055, maker_fee=0.0002, tick=0.0001)
        assert r["status"] == "MISSED" and r["net"] == 0.0
        assert verdict(-1.0, -0.5, 0.3).startswith("FAIL: profitable only under the optimistic")
        assert verdict(-1.0, -0.5, -0.1).startswith("FAIL: unprofitable")
        assert verdict(1.0, 0.5, 2.0).startswith("TAKER PROFITABLE")


# ---- analyzer ------------------------------------------------------------------------------------------------------

def rec(role="CONTROL", n=60, net_each=0.05, top=(0.0, 0.0, 0.0), tf="15m", days=30.0, decisions=None, halted=False):
    trades = []
    for i in range(n):
        net = top[i] if i < len(top) and top[i] else net_each * (1 if i % 3 else -1)
        trades.append({"pid": f"p{i}", "side": "long", "entry_ts": T0 + i * 3_600_000, "exit_ts": T0 + i * 3_600_000 + 7_200_000,
                       "hold_s": 7200, "qty": 1.0, "entry": 2.0, "exit": 2.0 + net, "gross": net + 0.01, "fees": 0.008,
                       "slippage": 0.002, "funding": 0.0, "net": net, "r": net / 0.2, "exit_kind": "trail", "risk_usd": 0.2,
                       "notional": 20.0, "tier": "TAKE", "risk_pct": 0.01, "jev_level": "TAKE" if role == "JEV" else None,
                       "jev_mult": 1.0 if role == "JEV" else None, "tp_hit": i % 2 == 0})
    nets = [t["net"] for t in trades]
    win = sum(x for x in nets if x > 0)
    loss = -sum(x for x in nets if x < 0)
    return {"identity": {"strategy_id": "S37", "coin": "SUI", "timeframe": tf, "role": role}, "role": role, "key": f"S37-SUI-{tf}-{role}",
            "pair_id": f"v31pair:S37-SUI-{tf}", "window": {"days": days}, "trades": trades, "decisions": decisions or [],
            "metrics": {"net_profit": sum(nets), "gross_pnl": sum(t["gross"] for t in trades), "expectancy_r": sum(t["r"] for t in trades) / n,
                        "profit_factor": win / loss if loss else 999.0, "max_drawdown_pct": 0.05, "liquidation_count": 0,
                        "net_return_pct": sum(nets) / 20.0, "fees_paid": 0.008 * n, "slippage_cost": 0.002 * n, "funding_paid": 0.0,
                        "win_rate": 0.66, "trades": n},
            "activity": {"signals": 3 * n, "halted": halted, "below_exchange_minimum": 0, "edge_rejected": n},
            "funnel": {"raw_setups": 3 * n, "legal": 2 * n, "positive_edge": n, "jev_accepted": None, "executed": n}}


class TestAnalyzer:
    def test_concentration(self):
        c = an.concentration(rec(top=(5.0, 3.0, 2.0))["trades"])
        assert c["best_trade"] == 5.0 and c["net_without_top3"] == pytest.approx(c["net"] - 10.0)
        assert 0 < c["top3_share_of_profit"] <= 1

    def test_a_concentrated_winner_does_not_advance(self):
        a = an.analyze_bot(rec(n=60, net_each=0.001, top=(5.0, 3.0, 2.0)), V31Config())
        assert a["state"] != "ADVANCE" and "PROFIT_CONCENTRATED" in a["diagnoses"]

    def test_positive_edge_with_too_little_activity_is_low_activity_edge(self):
        a = an.analyze_bot(rec(n=40, net_each=0.3, tf="15m", days=181.0), V31Config())
        assert a["activity"]["participation_ok"] is False
        assert a["state"] == "LOW_ACTIVITY_EDGE" and "TOO_LITTLE_ACTIVITY" in a["diagnoses"]

    def test_a_healthy_active_bot_advances_on_this_data(self):
        a = an.analyze_bot(rec(n=60, net_each=0.3, tf="15m", days=30.0), V31Config())
        assert a["state"] == "ADVANCE" and a["failure_mode"] == "ROBUST"

    def test_attack_effectiveness_counterfactual(self):
        trades = [{"net": 0.3, "r": 1.0, "jev_level": "ATTACK", "jev_mult": 1.5}, {"net": -0.15, "r": -0.5, "jev_level": "ATTACK", "jev_mult": 1.5},
                  {"net": 0.1, "r": 0.5, "jev_level": "TAKE", "jev_mult": 1.0}]
        x = an.attack_effectiveness(trades, "JEV")
        assert x["attack_trades"] == 2 and x["counterfactual_normal_net"] == pytest.approx(0.1)
        assert x["attack_added_net"] == pytest.approx(0.05)

    def test_jev_needs_selection_alpha(self):
        ds = [{"p_support": 0.7, "p_skip": 0.2, "chosen": "TAKE", "level": "TAKE", "outcome": "TAKEN", "net": 0.1, "r": 0.5,
               "source": "api", "latency_ms": 300}] * 40
        r = rec(role="JEV", n=60, net_each=0.3, days=30.0, decisions=ds)
        rnd = [{"metrics": {"net_profit": 99.0, "expectancy_r": 0.1}}] * 20
        base = {"control_net": 0.0, "selection": an.selection_alpha(r, rnd, 0.9)}
        a = an.analyze_bot(r, V31Config(), base=base)
        assert a["state"] != "ADVANCE" and "JEV_NO_SELECTION_ALPHA" in a["diagnoses"]


# ---- storage, payloads, routes, report ----------------------------------------------------------------------

class TestStorageAndViews:
    def test_round_trip_payload_and_no_secrets(self, tmp_path):
        from app.core.v31_view import v31_bot_payload, v31_payload
        st = Storage(str(tmp_path / "s.db"))
        st.v31_run_start({"run_id": "v31-x", "created_ts": 1, "dataset_role": "DEVELOPMENT", "config": {"coins": ["SUI"]},
                          "config_fingerprint": "f"})
        r = rec()
        r.update({"balance": 20.0, "equity": [[1, 20.0]], "edge_rows": [{"id": "eg-1", "net_r": 0.1, "realized_r": 0.2}]})
        r["identity"]["role"] = "CONTROL"
        st.v31_bot_save("v31-x", r, ts=5)
        assert st.v31_bot_keys("v31-x") == {r["key"]}
        assert "trades" not in st.v31_bots("v31-x")[0] and st.v31_bots("v31-x", heavy=True, extra=True)[0]["edge_rows"]
        p = v31_payload(st)
        assert p["dev"]["run"]["run_id"] == "v31-x" and p["dev"]["partial"][0]["key"] == r["key"] and p["test"] is None
        assert p["v3"]["run_id"] == "v3-e39e94890b" and p["v3"]["advanced_set"] == "NONE"
        d = v31_bot_payload(st, r["key"])
        assert d["ok"] and len(d["trades"]) == 60
        blob = repr(p) + repr(d)
        assert "state_json" not in blob and "OPENROUTER" not in blob
        assert st.v3_runs() == []                       # V3.1 never writes a V3 table
        st.close()

    def test_routes_are_get_only(self):
        from app.main import create_app
        app = create_app(settings_factory(data_dir="/tmp/v31routes"))
        paths = {path: {method.upper() for method in methods} for path, methods in app.openapi()["paths"].items()}
        for p in ("/api/public/competition/v31", "/api/public/competition/v31/bots/{key}",
                  "/api/competition/v31", "/api/competition/v31/bots/{key}"):
            assert p in paths and set(paths[p]) <= {"GET", "HEAD"}, p

    def test_report_sections_render(self, tmp_path):
        from app.core.report_v31 import v31_data, v31_html, v31_text
        st = Storage(str(tmp_path / "r.db"))
        empty = v31_data(st)
        assert "No V3.1 run yet" in v31_html(empty) and any("v3-e39e94890b" in x for x in v31_text(empty))
        s = {"answers": {"aggressive_edge": "NO", "jev_selection": "x", "small_timeframes": "y", "advanced_set": "NONE"},
             "advanced_set": "NONE", "viability": {"15m": {"raw": {"net_r": -0.1}, "trades": 10, "bots": 3}},
             "leaderboard_field": [{"rank": 1, "key": "S37-SUI-15m-JEV3@20x", "coin": "SUI", "tf": "15m", "mode": "JEV V3", "equity": 19.0,
                                    "jev_actions": {"TAKE": 0.7, "SKIP": 0.3}, "state": "FAIL", "failure_mode": "COST_DESTROYED"}],
             "pairs": [{"strategy_id": "S37", "coin": "SUI", "tf": "15m", "control": {"net": -1}, "jev": {"net": -0.5},
                        "random": {"random_median": -0.8, "alpha_usdt": 0.3}, "jev_actions": {"TAKE": 7, "SKIP": 3}, "state": "FAIL"}],
             "baselines": {"pairs": 1}, "selection_alpha": {"total_usdt": 0.3}, "jev_pooled": {"decisions": 10, "final": {"TAKE": 7, "SKIP": 3}},
             "attack": {"jev": {"attack_trades": 0}}, "calibration": {"n": 0, "buckets": []},
             "maker": {"ALL": {"5m": {"taker": {"net": -1}, "conservative": {"net": -0.8}, "optimistic": {"net": 0.2},
                                      "verdict": "FAIL: profitable only under the optimistic (unrealistic) maker assumption"}}},
             "capacity": {"totals": {"20": {"net": -1.0}, "50": {"net": -2.0}}}, "failure_modes": {"labels": {}}}
        d = {"dev": {"run": {"run_id": "v31-x", "status": "complete"}, "config": {}, "summary": s}, "test": None}
        html_out = v31_html(d)
        for needle in ("Small-timeframe viability", "CONTROL vs +JEV3", "ATTACK effectiveness", "Maker-first", "Capacity",
                       "Participation funnel", "Expected-net-edge calibration", "Holding period"):
            assert needle in html_out, needle
        txt = "\n".join(v31_text(d))
        assert "ADVANCED SET: NONE" in txt and "MAKER 5m" in txt
        st.close()
