"""V3 aggressive intraday Jev arena: strategy chronology and honesty, AGGRESSIVE_V3 sizing, Jev V2
(questions, strict parsing, deterministic policy, look-ahead guard, gate), the baselines that stand in
for Jev, the Bot Analyzer's root-cause classification, field selection by activity only, storage and
the read-only routes. No network: Jev is a stub and the tape is synthetic."""
from __future__ import annotations

import math
import random

import pytest

from app.ai.jev.models import JevDecision, JevOutcome, SchemaError
from app.ai.jev import v2 as jv2
from app.competition import v3_analyzer as an
from app.competition.v3_arena import V3Arena, action_distribution, select_field
from app.competition.v3_config import (AGGRESSIVE_V3, AttackPolicyV3, V3Config, V3Identity, min_trades,
                                       risk_for_v3)
from app.core.storage import Storage
from app.core.types import Candle, MarketRules, Signal
from tests.conftest import T0, settings_factory

SYM = "SUIUSDT"


def tape(n: int = 9000, seed: int = 3, start: float = 2.0) -> list[Candle]:
    """A seeded random walk with volatility regimes, volume bursts and taker flow."""
    rng = random.Random(seed)
    px, out, vol = start, [], 0.0015
    for i in range(n):
        if i % 700 == 0:
            vol = rng.choice((0.0006, 0.0012, 0.0025))
        burst = rng.random() < 0.02
        ret = rng.gauss(0.0, vol * (3.0 if burst else 1.0))
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


def arena(**over) -> V3Arena:
    cfg = V3Config(months=(), trade_from="2025-11-01", trade_to="2026-04-30", **over)
    return V3Arena(settings_factory(balance=20), cfg, rules())


def run_family(sid: str, tf: str, gate=None, role: str = "CONTROL"):
    from app.live.runner import with_sink
    a = arena()
    ident = V3Identity(sid, "SUI", tf, role)
    cls = a.bound(ident)
    seen: list = []
    live = with_sink(cls, lambda sigs, c: seen.extend((s, c) for s in sigs))
    eng = a.engine(ident, gate)
    bars = tape()
    res = eng.run(live, iter(bars), since_ms=bars[400].close_time, leverage=20, signal_tf=tf, only_symbol=SYM)
    return res, seen, eng


# ---- strategies ------------------------------------------------------------------------------------------

class TestStrategies:
    def test_binding_declares_signal_context_and_execution_timeframes(self):
        from app.strategies.registry import load_v3
        fams = load_v3()
        assert sorted(fams) == ["S31", "S32", "S33", "S34", "S35", "S36"]
        b = fams["S31"].for_timeframe("3m")
        assert b.signal_tf == "3m" and (b.ctx_fast, b.ctx_slow) == ("15m", "1h")
        assert b.timeframes == ("3m", "15m", "1h", "1m") and b.native_timeframe == "3m"
        assert fams["S33"].for_timeframe("15m").timeframes == ("15m", "1h", "4h", "1m")
        with pytest.raises(ValueError):
            fams["S31"].for_timeframe("2h")

    @pytest.mark.parametrize("sid", ["S31", "S32", "S33", "S34", "S35", "S36"])
    def test_every_entry_is_honest_and_never_fills_inside_its_signal_bar(self, sid):
        res, seen, _ = run_family(sid, "3m")
        entries = [f for f in res.fills if f.kind == "entry"]
        for sig, bar in seen:
            m = sig.meta
            assert bar.tf == "3m" and sig.ts == bar.close_time
            assert m["expected_move_pct"] > 0 and m["expected_move_source"].startswith("strategy")
            assert 0.0 <= m["signal_quality"] <= 1.0 and m["quality_factors"]
            assert 0.005 - 1e-12 <= m["stop_pct"] <= 0.02 + 1e-12
            assert m["execution_tf"] == "1m" and m["signal_tf"] == "3m"
        for f in entries:            # an order fills after signal + latency, on the next 1m bar
            assert f.ts >= (f.meta or {}).get("latency_ms", 0) and (f.meta or {}).get("latency_ms", 0) >= 400

    def test_one_minute_signals_fill_on_a_later_bar(self):
        res, seen, _ = run_family("S35", "1m")
        closes = {c.close_time for _, c in seen}
        for f in (f for f in res.fills if f.kind == "entry"):
            assert f.ts not in closes and f.ts > min(closes)

    def test_flow_needs_reported_taker_volume(self):
        from app.strategies.registry import load_v3
        cls = load_v3()["S35"].for_timeframe("5m")
        c = Candle(SYM, "5m", T0, 1, 1.1, 0.9, 1.05, 100.0, T0 + 299_999, True, 100, 10, "backfill", 0.0)
        assert cls().on_candle(c, None) == []           # no taker data -> never inferred


# ---- sizing ----------------------------------------------------------------------------------------------

class TestAggressiveV3:
    def test_tier_table(self):
        p = AGGRESSIVE_V3
        assert risk_for_v3(p, "NORMAL", "ORDINARY") == 0.01
        assert risk_for_v3(p, "NORMAL", "STRONG") == 0.015
        assert risk_for_v3(p, "NORMAL", "EXCEPTIONAL") == 0.015
        assert risk_for_v3(p, "ATTACK", "EXCEPTIONAL") == 0.02
        assert risk_for_v3(p, "DEFENSIVE", "EXCEPTIONAL") == 0.005
        assert risk_for_v3(p, "HALTED", "STRONG") == 0.0

    def _sig(self, q, e2c):
        return Signal("S31", SYM, "entry", "long", T0, "5m", 1.0, stop=0.99,
                      meta={"signal_quality": q, "edge_to_cost": e2c})

    def test_no_size_up_without_edge_to_cost(self):
        pol = AttackPolicyV3()
        m, info = pol(self._sig(0.9, 2.5), {"r": [], "drawdown": 0.0})
        assert m == 1.0 and info["quality"] == "ORDINARY"
        m, info = pol(self._sig(0.9, 3.5), {"r": [], "drawdown": 0.0})
        assert m == 1.5
        m, _ = pol(self._sig(0.9, 3.5), {"r": [0.5] * 12, "drawdown": 0.02})
        assert m == 2.0

    def test_jev_mode_keeps_only_the_health_factor(self):
        pol = AttackPolicyV3(jev_mode=True)
        assert pol(self._sig(0.95, 9.0), {"r": [0.5] * 12, "drawdown": 0.0})[0] == 1.0
        assert pol(self._sig(0.95, 9.0), {"r": [], "drawdown": 0.20})[0] == 0.5
        assert pol(self._sig(0.95, 9.0), {"r": [], "drawdown": 0.31})[0] == 0.0

    def test_identity_and_participation(self):
        i = V3Identity("S31", "SUI", "5m", "JEV", jev_policy="JEV_POLICY_V2")
        assert i.key == "S31-SUI-5m-JEV2@20x" and i.pair_id == "v3pair:S31-SUI-5m"
        assert V3Identity("S31", "SUI", "5m").key == "S31-SUI-5m-CONTROL@20x"
        assert V3Identity("S31", "SUI", "5m", "RANDOM", seed=7).key == "S31-SUI-5m-RAND7@20x"
        assert i.context_tfs == ("15m", "1h")
        assert [min_trades(tf, 181) for tf in ("3m", "5m", "15m", "30m")] == [302, 181, 91, 61]
        assert min_trades("15m", 30) == 30            # the absolute floor holds on short windows


# ---- Jev V2 ----------------------------------------------------------------------------------------------

def body(choice="NORMAL", win=0.6, probs=None):
    return {"model": "typesafe/jev-1.13-x", "usage": {"input_tokens": 900, "cost": 0.00005},
            "answers": {"win": {"type": "noul", "noul": win},
                        "action": {"type": "choice", "choice": choice,
                                   "probabilities": probs or {"SKIP": 0.1, "DEFENSIVE": 0.2, "NORMAL": 0.5, "ATTACK": 0.2}},
                        "setup_quality": {"type": "score", "score": 3, "probabilities": {"3": 0.7, "2": 0.3}}}}


class TestJevV2:
    def test_v2_is_a_new_version_and_v1_is_untouched(self):
        from app.ai.jev.models import PROMPT_VERSION, QUESTIONS_FINGERPRINT
        from app.ai.jev.policy import POLICY_V1
        assert PROMPT_VERSION == "JEV_PROMPT_V1" and POLICY_V1.version == "JEV_POLICY_V1"
        assert POLICY_V1.skip_below == 0.55 and dict(POLICY_V1.multipliers)["ATTACK"] == 1.5
        assert jv2.PROMPT_VERSION_V2 == "JEV_PROMPT_V2" and jv2.POLICY_V2.version == "JEV_POLICY_V2"
        assert jv2.QUESTIONS_V2_FINGERPRINT != QUESTIONS_FINGERPRINT

    def test_parse_is_strict(self):
        d = jv2.parse_decision_v2(body())
        assert d.take_probability == 0.6 and d.risk_state == "NORMAL" and d.setup_quality == 0.75
        with pytest.raises(SchemaError):
            jv2.parse_decision_v2(body(choice="YOLO"))
        bad = body()
        bad["answers"]["win"]["noul"] = 1.4
        with pytest.raises(SchemaError):
            jv2.parse_decision_v2(bad)

    def test_policy_is_deterministic(self):
        mk = lambda choice, pa=0.2: jv2.parse_decision_v2(body(choice, probs={"SKIP": 0.1, "DEFENSIVE": 0.1, "NORMAL": 0.8 - pa, "ATTACK": pa}))  # noqa: E731
        assert jv2.decide_v2(mk("SKIP"), 5.0).multiplier == 0.0
        assert jv2.decide_v2(mk("DEFENSIVE"), 5.0).multiplier == 0.5
        assert jv2.decide_v2(mk("NORMAL"), 5.0).multiplier == 1.0
        assert jv2.decide_v2(mk("ATTACK", 0.4), 5.0).multiplier == 1.5
        assert jv2.decide_v2(mk("ATTACK", 0.7), 5.0).multiplier == 2.0
        demoted = jv2.decide_v2(mk("ATTACK", 0.7), 2.5)
        assert demoted.level == "NORMAL" and demoted.multiplier == 1.0
        err = jv2.decide_v2(None, 5.0, error_code="TIMEOUT")
        assert err.level == "SKIP" and err.multiplier == 0.0

    def test_state_refuses_look_ahead_and_holds_no_dates_or_prices(self):
        bars = tape(400)
        sig = Signal("S31", SYM, "entry", "long", bars[299].close_time, "3m", bars[299].close, stop=bars[299].close * 0.99,
                     meta={"family": "MOMENTUM_BREAKOUT", "expected_move_pct": 0.01, "stop_pct": 0.01, "context_tfs": ["15m", "1h"]})
        b = jv2.JevStateBuilderV2()
        bot = {"strategy_id": "S31", "timeframe": "3m", "symbol": SYM}
        ex = {"taker_fee": 0.00055, "half_spread_bps": 0.5, "expected_slippage_bps": 0.5}
        snap = b.build(ts=bars[299].close_time, bot=bot, sig=sig, series={"1m": bars[:300]}, execution=ex,
                       health={}, position={})
        blob = repr(snap.state)
        assert str(bars[299].close) not in blob and "2025" not in blob and "2026" not in blob
        assert snap.state["execution"]["round_trip_cost_bps"] == pytest.approx(12.0)
        assert snap.state["derivatives"]["open_interest"] == "not available"
        with pytest.raises(Exception):
            b.build(ts=bars[299].close_time, bot=bot, sig=sig, series={"1m": bars[:310]}, execution=ex,
                    health={}, position={})

    def test_the_gate_asks_only_real_candidates_and_applies_the_policy(self):
        class Stub:
            calls = 0

            def decide(self, key, snap):
                Stub.calls += 1
                dec = jv2.parse_decision_v2(body("ATTACK", 0.7, {"SKIP": 0.0, "DEFENSIVE": 0.1, "NORMAL": 0.2, "ATTACK": 0.7}))
                return JevOutcome(True, decision=dec, latency_ms=310), "api"
        gate = jv2.JevGateV2(Stub(), None, "run", {"key": "S31-SUI-3m-JEV2@20x", "strategy_id": "S31", "params_version": "v3",
                                                   "symbol": SYM, "timeframe": "3m"}, "v3pair:x", "m", persist=False)
        res, seen, eng = run_family("S31", "3m", gate=gate, role="JEV")
        assert Stub.calls == gate.decisions == len(gate.rows)
        assert Stub.calls <= res.signals                       # never more than the candidates
        for r in gate.rows:
            assert r["level"] in ("ATTACK", "NORMAL") and r["latency_ms"] == 310
            assert r["mult"] == (2.0 if r["level"] == "ATTACK" else 1.0)


class TestBaselineGates:
    def test_take_is_constant_normal(self):
        g = jv2.PolicyGate("TAKE", "k")
        sig = Signal("S31", SYM, "entry", "long", T0, "5m", 1.0, stop=0.99, meta={"edge_to_cost": 9.0})
        assert all(g(sig, {"ts": T0 + i}).multiplier == 1.0 for i in range(20))

    def test_random_is_seeded_and_follows_the_distribution(self):
        dist = {"SKIP": 0.5, "DEFENSIVE": 0.1, "NORMAL": 0.3, "ATTACK": 0.1}
        sig = Signal("S31", SYM, "entry", "long", T0, "5m", 1.0, stop=0.99, meta={"edge_to_cost": 9.0})
        a = [jv2.PolicyGate("RANDOM", "k", seed=3, distribution=dist)(sig, {"ts": T0 + i}).multiplier for i in range(2000)]
        b = [jv2.PolicyGate("RANDOM", "k", seed=3, distribution=dist)(sig, {"ts": T0 + i}).multiplier for i in range(2000)]
        c = [jv2.PolicyGate("RANDOM", "k", seed=4, distribution=dist)(sig, {"ts": T0 + i}).multiplier for i in range(2000)]
        assert a == b and a != c
        assert 0.45 < sum(1 for m in a if m == 0.0) / len(a) < 0.55

    def test_action_distribution_counts_errors_as_skips(self):
        ds = [{"chosen": "NORMAL", "level": "NORMAL", "mult": 1.0}, {"chosen": None, "level": "SKIP", "mult": 0.0},
              {"chosen": "ATTACK", "level": "ATTACK", "mult": 2.0}, {"chosen": "ATTACK", "level": "ATTACK", "mult": 1.5}]
        dist, strong = action_distribution(ds)
        assert dist == {"NORMAL": 0.25, "SKIP": 0.25, "ATTACK": 0.5} and strong == 0.5


# ---- analyzer ----------------------------------------------------------------------------------------------

def rec(role="CONTROL", tf="5m", trades=200, gross=5.0, net=2.0, dd=0.1, halted=False, fees=2.0, slip=1.0,
        decisions=None, liq=0, signals=400, below=0):
    rows = []
    for i in range(trades):
        g = gross / trades
        n = net / trades
        rows.append({"entry_ts": T0 + i * 3_600_000, "exit_ts": T0 + i * 3_600_000 + 600_000, "hold_s": 600,
                     "gross": g, "net": n + (0.5 if i % 2 else -0.5), "fees": fees / trades, "slippage": slip / trades,
                     "r": (n + (0.5 if i % 2 else -0.5)) / 0.2, "risk_usd": 0.2, "notional": 20.0, "quality": 0.5,
                     "e2c": 5.0})
    return {"identity": {"strategy_id": "S31", "coin": "SUI", "timeframe": tf, "role": role}, "role": role,
            "key": f"S31-SUI-{tf}-{role}", "pair_id": "v3pair:S31-SUI-5m", "experimental": False,
            "window": {"days": 181}, "trades": rows, "decisions": decisions or [],
            "activity": {"signals": signals, "below_exchange_minimum": below, "cost_gate_rejected": 0, "halted": halted},
            "metrics": {"gross_pnl": gross, "net_profit": net, "net_return_pct": net / 20, "expectancy_r": net / trades / 0.2,
                        "profit_factor": 1.3 if net > 0 else 0.8, "max_drawdown_pct": dd, "liquidation_count": liq,
                        "fees_paid": fees, "slippage_cost": slip, "funding_paid": 0.0, "win_rate": 0.5, "trades": trades}}


class TestAnalyzer:
    cfg = V3Config()

    def test_root_cause_beats_the_symptom(self):
        a = an.analyze_bot(rec(gross=-3.0, net=-6.0, dd=0.26, halted=True, trades=50), self.cfg)
        assert a["failure_mode"] == "NO_GROSS_EDGE" and a["state"] == "INSUFFICIENT_AGGRESSIVE_PARTICIPATION"
        assert an.analyze_bot(rec(gross=2.0, net=-1.0), self.cfg)["failure_mode"] == "FEE_DESTROYED"
        assert an.analyze_bot(rec(gross=2.0, net=-1.0, fees=0.5, slip=2.5), self.cfg)["failure_mode"] == "SLIPPAGE_DESTROYED"
        assert an.analyze_bot(rec(gross=9.0, net=4.0, dd=0.35), self.cfg)["failure_mode"] == "DRAWDOWN_FAILURE"
        assert an.analyze_bot(rec(liq=1), self.cfg)["failure_mode"] == "LIQUIDATION"
        assert an.analyze_bot(rec(trades=100, gross=3.0, net=1.0), self.cfg)["failure_mode"] == "TOO_LOW_ACTIVITY"
        assert an.analyze_bot(rec(trades=40, signals=400, below=300), self.cfg)["failure_mode"] == "MIN_NOTIONAL_CONSTRAINED"

    def test_a_clean_bot_is_robust_and_advances(self):
        a = an.analyze_bot(rec(trades=400, gross=12.0, net=6.0, dd=0.12), self.cfg)
        assert a["failure_mode"] == "ROBUST" and a["state"] == "ADVANCE"
        assert a["summary"]["result"] == "PASS" and "HOLDOUT TEST" in a["summary"]["hypothesis"][0]

    def test_jev_over_filtering_is_a_participation_failure(self):
        ds = [{"chosen": "SKIP", "level": "SKIP", "mult": 0.0, "p_win": 0.2, "outcome": "SHADOW", "net": 0.1, "r": 0.5}] * 95 + \
             [{"chosen": "NORMAL", "level": "NORMAL", "mult": 1.0, "p_win": 0.6, "outcome": "TAKEN", "net": 0.1, "r": 0.5}] * 5
        a = an.analyze_bot(rec(role="JEV", trades=400, gross=12, net=6, decisions=ds), self.cfg,
                           base={"control_net": 3.0, "random": {"p90": 1.0}})
        assert a["failure_mode"] == "JEV_OVER_FILTERING" and a["state"] == "INSUFFICIENT_AGGRESSIVE_PARTICIPATION"
        assert a["jev"]["skip_rate"] == 0.95 and a["jev"]["skipped"]["winners"] == 95

    def test_random_baseline_and_permutation(self):
        rb = an.random_baseline(2.0, [0.1 * i for i in range(20)], 0.9)
        assert rb["p90"] < 2.0 and rb["p_value"] == pytest.approx(1 / 21, abs=1e-3)
        ds = [{"mult": 1.0 if r > 0 else 0.0, "r": r} for r in [1, -1] * 30]
        p = an.permutation_p(ds, 500, 7)
        assert p["p"] < 0.05 and an.permutation_p(ds, 500, 7) == p

    def test_score_never_lets_a_catastrophe_rank_first(self):
        rows = [{"tf": "5m", "edge": {"net_return_pct": 3.0, "max_drawdown_pct": 0.6, "net_expectancy_r": 0.9, "net_pf": 2.5},
                 "cost": {"realized_edge_to_cost": 4.0}, "activity": {"trades_per_30d": 80}, "liquidations": 1},
                {"tf": "5m", "edge": {"net_return_pct": 0.1, "max_drawdown_pct": 0.08, "net_expectancy_r": 0.1, "net_pf": 1.2},
                 "cost": {"realized_edge_to_cost": 1.5}, "activity": {"trades_per_30d": 40}}]
        s = an.scores(rows, self.cfg)
        assert s[0] <= 0.25 < s[1]


class TestField:
    def test_activity_only_distinct_coins_and_empty_slots(self):
        cfg = V3Config(field_timeframes=("3m", "5m"))
        scan = []
        for coin, n3, n5 in (("ZEC", 900, 800), ("SUI", 700, 500), ("ARB", 100, 50)):
            for tf, n in (("3m", n3), ("5m", n5)):
                scan.append({"identity": {"role": "CONTROL", "strategy_id": "S31", "coin": coin, "timeframe": tf},
                             "activity": {"entries": n}})
        f = select_field(scan, cfg)
        assert [(p["coin"], p["timeframe"]) for p in f["pairs"]] == [("ZEC", "3m"), ("SUI", "5m")]
        scan2 = [s for s in scan if s["identity"]["coin"] == "ARB"]
        f2 = select_field(scan2, cfg)
        assert f2["pairs"] == [] and len(f2["empty_slots"]) == 2


# ---- storage and routes ---------------------------------------------------------------------------------------

class TestStorageAndRoutes:
    def test_round_trip_and_payload(self, tmp_path):
        from app.core.v3_view import v3_bot_payload, v3_payload
        st = Storage(str(tmp_path / "s.db"))
        st.v3_run_start({"run_id": "v3-x", "created_ts": 1, "config": {"coins": ["SUI"]}, "config_fingerprint": "f"})
        r = rec()
        r["equity"] = [[1, 20.0]]
        st.v3_bot_save("v3-x", r, ts=5)
        assert st.v3_bot_keys("v3-x") == {r["key"]}
        light = st.v3_bots("v3-x")[0]
        assert "trades" not in light and st.v3_bots("v3-x", heavy=True)[0]["trades"]
        p = v3_payload(st)
        assert p["run"]["run_id"] == "v3-x" and p["partial"][0]["key"] == r["key"]
        d = v3_bot_payload(st, r["key"])
        assert d["ok"] and len(d["trades"]) == 200 and d["bot"]["identity"]["coin"] == "SUI"
        blob = repr(p) + repr(d)
        assert "state_json" not in blob and "OPENROUTER" not in blob
        st.close()

    def test_v3_routes_are_get_only(self):
        from app.main import create_app
        app = create_app(settings_factory(data_dir="/tmp/v3routes"))
        paths = {path: {method.upper() for method in methods} for path, methods in app.openapi()["paths"].items()}
        for p in ("/api/public/competition/v3", "/api/public/competition/v3/bots/{key}",
                  "/api/competition/v3", "/api/competition/v3/bots/{key}"):
            assert p in paths and set(paths[p]) <= {"GET", "HEAD"}, p


class TestReport:
    def test_sections_render_with_and_without_a_run(self, tmp_path):
        from app.core.report_v3 import v3_html, v3_text
        empty = {"run": None, "v2": {"passed": 0, "candidates": 3, "run_id": "9688867c33bc"}}
        assert "V2 MULTI-YEAR 0 / 3 PASSED" in v3_html(empty) and "No V3 discovery run" in v3_html(empty)
        assert any("V2 MULTI-YEAR 0 / 3 PASSED" in x for x in v3_text(empty))
        row = {"key": "S31-SUI-5m-JEV2@20x", "role": "JEV", "coin": "SUI", "tf": "5m", "net_pnl": -1.2, "net_return_pct": -0.06,
               "trades_per_day": 1.1, "exp_r": -0.05, "pf": 0.9, "max_dd": 0.12, "rt_cost_bps": 12.9, "jev_take": 0.4,
               "state": "FAIL", "failure_mode": "FEE_DESTROYED", "gross_exp_r": 0.02, "edge_to_cost": 0.4, "jev_auc": 0.52}
        s = {"counts": {"active_jev_bots": 1, "matched_controls": 1, "field_by_tf": {"5m": 1}, "controls_scanned": 240},
             "answers": {"aggressive_edge": "NO: none", "jev_makes_it_better": "NO: none", "advanced_set": "NONE"},
             "advanced_set": "NONE", "leaderboard_field": [row], "leaderboard_scan": [row],
             "failure_modes": {"jev_bots": {"FEE_DESTROYED": 1}, "labels": {"FEE_DESTROYED": "FEE DESTROYED"}},
             "pairs": [{"strategy_id": "S31", "coin": "SUI", "tf": "5m", "control": {"net": -0.5}, "jev": {"net": -1.2},
                        "always_take": {"net": -0.7}, "random": {"p50": -0.9, "p90": 0.1, "p_value": 0.6},
                        "jev_accepted": {"winners": 3, "losers": 5}, "jev_skipped": {"winners": 2, "losers": 4},
                        "jev_auc": {"auc": 0.52, "low": 0.41, "high": 0.63}, "state": "FAIL"}],
             "jev_pooled": {"decisions": 14, "final": {"SKIP": 6, "NORMAL": 8}, "auc": {"auc": 0.52}, "latency_ms": {"p50": 300},
                            "calibration": [{"bucket": "0.0-0.2", "n": 3, "predicted": 0.1, "realized_win_rate": 0.33, "mean_r": 0.1}]},
             "baselines": {"pairs": 1}, "matrix_strategy_tf": {"S31 momentum breakout": {"5m": {"net_return_mean": -0.06, "bots": 1,
                                                                                               "trades": 200, "profitable": 0}}},
             "matrix_coin_tf": {}, "timeframe_verdict": {"S31": {"best_tf": "5m", "by_tf": {"5m": {"mean_net_return": -0.06, "profitable_active": 0}}}}}
        d = {"run": {"run_id": "v3-x", "status": "complete", "stage": "ANALYZED", "config_fingerprint": "f"}, "summary": s,
             "config": {"coins": ["SUI"]}, "venue_label": "BYBIT LINEAR", "v2": {"passed": 0, "candidates": 3}}
        html_out = v3_html(d)
        for needle in ("Leaderboard", "FEE DESTROYED", "ALWAYS-TAKE", "Strategy × timeframe", "Bot diagnostics", "ADVANCED SET"):
            assert needle in html_out, needle
        txt = "\n".join(v3_text(d))
        assert "ADVANCED SET: NONE" in txt and "S31-SUI-5m" in txt and "JEV V2: 14 decisions" in txt

    def test_the_full_report_includes_v3_first(self, tmp_path):
        from app.core.report_v3 import v3_data
        st = Storage(str(tmp_path / "r.db"))
        d = v3_data(st)
        assert d["run"] is None and "v2" in d
        st.close()


def test_a_refusal_after_jevs_resize_is_not_a_jev_skip():
    ds = ([{"chosen": "SKIP", "level": "SKIP", "mult": 0.0, "p_win": 0.2, "outcome": "SHADOW", "result": "SKIPPED", "net": -0.1, "r": -0.5}] * 3
          + [{"chosen": "DEFENSIVE", "level": "DEFENSIVE", "mult": 0.5, "p_win": 0.3, "outcome": "SHADOW",
              "result": "RISK_REJECTED:below_min_notional", "net": 0.1, "r": 0.4}] * 5
          + [{"chosen": "DEFENSIVE", "level": "DEFENSIVE", "mult": 0.5, "p_win": 0.4, "outcome": "TAKEN", "result": "TRADED", "net": 0.1, "r": 0.5}] * 2)
    j = an.jev_block({"role": "JEV", "decisions": ds})
    assert j["skipped"]["n"] == 3 and j["refused_after_resize"]["n"] == 5 and j["accepted"]["n"] == 2
    assert j["skip_rate"] == 0.3 and j["not_traded_rate"] == 0.8
