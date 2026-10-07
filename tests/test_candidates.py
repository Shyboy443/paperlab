"""Frozen v2 candidates: the freeze check, the stress scenarios, the multi-year analysis (windows,
years, regimes, robustness, cost headroom, Monte Carlo, gates), forward-experiment stitching, and
that historical and forward evidence are never summed."""
from __future__ import annotations

import dataclasses
import math

import pytest

from app.competition import candidate_validation as cv
from app.core.storage import Storage
from app.core.storage_candidates import finite
from app.live.analytics import forward_experiment

DAY = cv.DAY
T0 = cv.month_start_ms("2021-01")


# ---- the freeze ---------------------------------------------------------------------------------------

def _test_run(state="ADVANCE", fps=None):
    from app.competition.arena import ArenaConfig
    cfg = ArenaConfig(params_version="v2", cost_gate_min_ratio=2.0, dataset_role="TEST",
                      strategy_fingerprints=tuple(sorted((fps or cv.FROZEN_V2).items()))).to_dict()
    cfg["rules"] = {"XRPUSDT": {"symbol": "XRPUSDT", "tick": 0.0001, "step": 0.1, "min_qty": 0.1,
                                "min_notional": 5.0, "maint_margin_rate": 0.025, "price_precision": 4,
                                "qty_precision": 1}}
    return {"run_id": "8227dfb422cb", "config": cfg,
            "bots": [{"key": "S26-XRPUSDT-30m@20x-v2", "strategy_id": "S26", "symbol": "XRPUSDT",
                      "timeframe": "30m", "state": state}]}


class TestFreeze:
    def test_the_frozen_candidate_verifies(self):
        tr = _test_run()
        m = cv.manifest(tr, "S26-XRPUSDT-30m@20x-v2")
        assert cv.verify(m, tr) == []
        assert m["source_fingerprint"] == {"S26": cv.FROZEN_V2["S26"], "base": cv.FROZEN_V2["base"]}
        assert m["timeframe"] == "30m" and m["symbol"] == "XRPUSDT" and m["context_timeframe"] == "4h"
        assert m["risk_engine"]["risk_per_trade_pct"] == 0.01 and m["risk_engine"]["strategy_halt_pct"] == 0.25
        assert m["cost_gate"]["min_edge_to_cost"] == 2.0 and m["fee_schedule"]["taker_rate"] == 0.0005
        assert m["parameters"]["tp1_r"] == 3.0                     # class defaults, recorded verbatim

    def test_a_changed_source_is_not_v2(self, monkeypatch):
        tr = _test_run(fps={**cv.FROZEN_V2, "S26": "000000000000"})
        m = cv.manifest(tr, "S26-XRPUSDT-30m@20x-v2")
        assert any("TEST run's recorded" in p for p in cv.verify(m, tr))
        monkeypatch.setattr(cv, "FROZEN_V2", {**cv.FROZEN_V2, "S26": "ffffffffffff"})
        m = cv.manifest(_test_run(), "S26-XRPUSDT-30m@20x-v2")
        assert any("v2 freeze" in p for p in cv.verify(m, _test_run()))

    def test_only_test_survivors_are_candidates(self):
        tr = _test_run(state="FAILED")
        assert any("did not ADVANCE" in p for p in cv.verify(cv.manifest(tr, "S26-XRPUSDT-30m@20x-v2"), tr))

    def test_the_manifest_fingerprint_moves_with_any_setting(self):
        a = cv.manifest(_test_run(), "S26-XRPUSDT-30m@20x-v2")
        tr = _test_run()
        tr["config"]["cost_gate_min_ratio"] = 1.5
        b = cv.manifest(tr, "S26-XRPUSDT-30m@20x-v2")
        assert a["manifest_fingerprint"] != b["manifest_fingerprint"]


# ---- stress scenarios -------------------------------------------------------------------------------------

class TestScenarios:
    def base(self):
        from app.competition.arena import ArenaConfig
        return ArenaConfig(params_version="v2", cost_gate_min_ratio=2.0)

    def test_each_scenario_changes_only_what_it_names(self):
        b = self.base()
        f = cv.stressed(b, cv.SCENARIOS["FEES_150"])
        assert f.fees.taker_rate == pytest.approx(b.fees.taker_rate * 1.5) and f.execution == b.execution
        s = cv.stressed(b, cv.SCENARIOS["SLIP_200"])
        assert s.execution.slippage_mult == 2.0 and s.fees == b.fees
        lat = cv.stressed(b, cv.SCENARIOS["LATENCY_2X"])
        assert lat.execution.signal_latency_ms == 2 * b.execution.signal_latency_ms
        bar = cv.stressed(b, cv.SCENARIOS["LATENCY_1BAR"])
        assert bar.execution.signal_latency_ms == b.execution.signal_latency_ms + 60_000
        assert cv.stressed(b, cv.SCENARIOS["ALL_TAKER"]).execution.force_taker is True
        assert cv.stressed(b, cv.SCENARIOS["VENUE_BYBIT_FEES"]).fees.taker_rate == 0.00055
        normal = cv.stressed(b, cv.SCENARIOS["NORMAL"])
        assert normal == b

    def test_the_combined_stress_stacks(self):
        b = self.base()
        a = cv.stressed(b, cv.SCENARIOS["ALL_STRESS"])
        assert a.fees.taker_rate == pytest.approx(0.00075) and a.execution.slippage_mult == 2.0
        assert a.execution.force_taker and a.execution.order_latency_ms == 2 * b.execution.order_latency_ms

    def test_adverse_funding_always_charges_the_position(self):
        from app.backtest.funding import FundingSchedule
        from tests.test_competition import SYMBOL, OneShot, big_rules, rising
        from app.backtest.replay import ReplayEngine
        from tests.conftest import settings_factory
        bars = rising()
        settle = bars[10].close_time
        sched = FundingSchedule({SYMBOL: [(settle, -0.001)]})          # a long RECEIVES at -0.1%
        runs = {}
        for mode in ("normal", "adverse"):
            eng = ReplayEngine(settings_factory(balance=20), [SYMBOL], rules=big_rules(), seed=1, funding=sched)
            eng.funding_mode = mode
            res = eng.run(OneShot, bars, leverage=10)
            runs[mode] = sum(f.realized_pnl for f in res.fills if f.kind == "funding")
        assert runs["normal"] > 0 > runs["adverse"]
        assert runs["adverse"] == pytest.approx(-runs["normal"])


# ---- analysis ---------------------------------------------------------------------------------------------

def trade(day, net, r=None, notional=10.0, pnl=None, eq=20.0, qty=10.0):
    ts = T0 + day * DAY
    return {"entry_ts": ts, "exit_ts": ts + 3_600_000, "net": net, "r": r if r is not None else net / 0.2,
            "notional": notional, "pnl": pnl if pnl is not None else net + 0.01, "equity_at_entry": eq, "qty": qty,
            "fees": 0.01, "side": "long"}


class TestAnalysis:
    def test_windows_and_years_on_the_continuous_ledger(self):
        eq = [(T0 + d * DAY, 20.0 + d * 0.01) for d in range(0, 200)]
        periods = cv._period_stats(eq, 20.0)
        trades = [trade(d, 0.1) for d in range(0, 190, 10)]
        months = cv.months_between("2021-01", "2021-06")
        w = cv.windows(trades, periods, months, 5)
        assert [r["window"] for r in w["rows"]] == ["2021-Q1", "2021-Q2"]
        assert w["active"] == 2 and w["profitable"] == 2 and w["profitable_ratio"] == 1.0
        q1 = w["rows"][0]
        assert q1["return"] == pytest.approx(periods["2021-Q1"]["end_equity"] / 20.0 - 1)
        yrs = cv.years(trades, periods, months)
        assert yrs[0]["year"] == "2021" and yrs[0]["months"] == 6 and yrs[0]["trades"] == len(trades)

    def test_period_drawdown_comes_from_the_full_curve(self):
        eq = [(T0, 20.0), (T0 + DAY, 25.0), (T0 + 2 * DAY, 20.0), (T0 + 3 * DAY, 26.0)]
        p = cv._period_stats(eq, 20.0)
        assert p["2021"]["max_dd"] == pytest.approx(0.2)

    def test_robustness_flags_concentration(self):
        trades = [trade(0, 5.0)] + [trade(d, -0.1) for d in range(1, 30)]
        rb = cv.robustness(trades, [{"year": "2021", "net": 2.1}])
        assert rb["concentration_risk"] and rb["net_without_best"] < 0
        assert rb["largest_winner_share_of_net"] == pytest.approx(5.0 / 2.1)
        even = [trade(d, 0.1) for d in range(30)]
        assert not cv.robustness(even, [{"year": "2021", "net": 3.0}])["concentration_risk"]

    def test_break_even_cost_and_the_bybit_tick(self):
        trades = [trade(d, 0.05, notional=100.0, pnl=0.15, qty=0.1) for d in range(10)]
        normal = {"metrics": {"slippage_cost": 0.02, "fees_paid": 1.0}, "trades": trades, "funding_total": 0.0,
                  "fees_by_role": {"taker": 1.0, "maker": 0.0}}
        h = cv.headroom(normal, "BNBUSDT")
        assert h["breakeven_cost_bps"] == pytest.approx((1.5 + 0.02) / 1000 * 1e4)
        assert h["binance_cost_bps"] == pytest.approx(1.02 / 1000 * 1e4)
        assert h["bybit_extra_tick_bps"] == pytest.approx(10 * 0.1 * 0.09 / 1000 * 1e4)
        assert h["bybit_cost_bps"] > h["binance_cost_bps"] + h["bybit_extra_tick_bps"] - 1e-9
        assert cv.headroom(normal, "XRPUSDT")["bybit_extra_tick_bps"] == 0.0

    def test_regimes_use_only_the_previous_close(self):
        daily = [(T0 + d * DAY, 100.0 * (1.01 ** d)) for d in range(200)]
        proto = cv.Protocol()
        table = cv.regime_table(daily, proto)
        assert table[daily[150][0]]["trend"] == "TREND_UP"
        changed = daily[:150] + [(daily[150][0], 1.0)] + daily[151:]
        assert cv.regime_table(changed, proto)[daily[150][0]] == table[daily[150][0]]   # no look-ahead
        down = [(T0 + d * DAY, 100.0 * (0.99 ** d)) for d in range(200)]
        assert cv.regime_table(down, proto)[down[150][0]]["trend"] == "TREND_DOWN"

    def test_monte_carlo_is_seeded_and_ruin_includes_the_floor(self):
        proto = dataclasses.replace(cv.Protocol(), mc_paths=500)
        winners = [trade(d, 0.2, eq=20.0) for d in range(100)]
        a = cv.monte_carlo(winners, 20.0, proto)
        assert a["ruin_probability"] == 0.0 and a["median_ending_equity"] > 20.0
        assert a == cv.monte_carlo(winners, 20.0, proto)                  # same seed, same paths
        losers = [trade(d, -0.4, eq=20.0) for d in range(100)]           # -2% a trade
        b = cv.monte_carlo(losers, 20.0, proto)
        assert b["ruin_probability"] == 1.0
        assert b["median_ending_equity"] <= 20.0 * 0.75 + 1e-9 and b["p_dd_over_50"] > 0.9
        assert "75%" in b["ruin_definition"] and "30%" in b["ruin_definition"]

    def test_gates_are_the_pre_registered_ones(self):
        res = {"metrics": {"trades": 300, "net_profit": 5.0, "expectancy_r": 0.1, "profit_factor": 1.3,
                           "max_drawdown_pct": 0.12, "liquidation_count": 0},
               "halted": False, "windows": {"profitable_ratio": 0.6, "profitable": 12, "active": 20},
               "monte_carlo": {"ran": True, "ruin_probability": 0.01},
               "robustness": {"net_without_best3": 1.0, "net_without_best_year": 0.5},
               "stress": [{"scenario": s, "survives": True} for s in cv.GateConfig().stress_required]}
        gs = cv.gates(res, cv.GateConfig())
        assert all(g["ok"] for g in gs) and cv.stage_status(gs)["VERDICT"] == "PASS"
        res["windows"]["profitable_ratio"] = 0.5                          # a tie is not a majority
        assert cv.stage_status(cv.gates(res, cv.GateConfig()))["MULTI_YEAR"] == "FAIL"
        res["windows"]["profitable_ratio"] = 0.6
        res["stress"][0]["survives"] = False
        st = cv.stage_status(cv.gates(res, cv.GateConfig()))
        assert st["STRESS"] == "FAIL" and st["VERDICT"] == "FAIL" and st["MULTI_YEAR"] == "PASS"
        res["halted"] = True
        assert not next(g for g in cv.gates(res, cv.GateConfig()) if g["name"].startswith("max drawdown"))["ok"]

    def test_stored_results_have_no_infinity(self):
        assert finite({"pf": float("inf"), "x": [float("nan"), -float("inf")]}) == {"pf": 999.0, "x": [None, -999.0]}


# ---- forward experiments --------------------------------------------------------------------------------

BASE_CFG = {"source_run_id": "8227dfb422cb", "strategy_fingerprints": dict(cv.FROZEN_V2),
            "fees": {"maker_rate": 0.0002, "taker_rate": 0.0005}, "execution": {"signal_latency_ms": 250},
            "cost_gate_min_ratio": 2.0, "profile": "AGGRESSIVE", "starting_balance": 20.0, "leverage_ceiling": 20,
            "market_data": "Binance USD-M public",
            "jev": {"model": "typesafe/jev-1.13", "prompt_version": "JEV_PROMPT_V1", "policy_version": "JEV_POLICY_V1",
                    "state": "READY", "timeout_ms": 4000}}


class TestForwardExperiment:
    def test_compatible_sessions_share_an_experiment(self):
        a, _ = forward_experiment(BASE_CFG, "fp1")
        b, _ = forward_experiment({**BASE_CFG, "warmup_hours": 30, "go_live_ms": 5,
                                   "jev": {**BASE_CFG["jev"], "state": "DISABLED"}}, "fp1")
        assert a == b and a.startswith("fx-")

    @pytest.mark.parametrize("change", [
        {"fees": {"maker_rate": 0.0002, "taker_rate": 0.00055}},
        {"strategy_fingerprints": {**cv.FROZEN_V2, "S26": "x"}},
        {"venue": "BYBIT_LINEAR"},
        {"execution": {"signal_latency_ms": 500}},
        {"jev": {**BASE_CFG["jev"], "policy_version": "JEV_POLICY_V2"}},
        {"cost_gate_min_ratio": 1.5},
    ])
    def test_any_real_change_starts_a_new_experiment(self, change):
        assert forward_experiment(BASE_CFG, "fp1")[0] != forward_experiment({**BASE_CFG, **change}, "fp1")[0]
        assert forward_experiment(BASE_CFG, "fp1")[0] != forward_experiment(BASE_CFG, "fp2")[0]

    def test_sessions_stitch_and_incompatible_ones_stay_apart(self, tmp_path):
        from app.core.shadow_view import experiment_summary, experiments
        st = Storage(str(tmp_path / "s.db"))
        st.shadow_session_start("s1", 1_000, "8227dfb422cb", BASE_CFG)             # an old session: no id yet
        st.shadow_session_update("s1", live_ts=2_000, ended_ts=10_000)
        fid, ident = forward_experiment(BASE_CFG, None)
        st.shadow_session_start("s2", 20_000, "8227dfb422cb", BASE_CFG, experiment_id=fid, experiment=ident)
        st.shadow_session_update("s2", live_ts=21_000)
        other = {**BASE_CFG, "fees": {"maker_rate": 0.0002, "taker_rate": 0.00055}}
        oid, oident = forward_experiment(other, None)
        st.shadow_session_start("s3", 30_000, "8227dfb422cb", other, experiment_id=oid, experiment=oident)
        for sid, key in (("s1", "A"), ("s2", "A"), ("s3", "A")):
            st.shadow_trade_save(sid, {"bot_key": key, "role": "CONTROL", "position_id": sid, "net": 1.0, "r": 1.0,
                                       "exit_ts": 5})
            st.shadow_event_add(sid, 5, "candidate", {"bot_key": key})
        sessions = experiments(st)
        assert {x["session_id"]: x["experiment_id"] for x in sessions}["s1"] == fid      # backfilled
        s = experiment_summary(st, fid, sessions, live_session="s2", now_ms=41_000)
        assert s["sessions"] == 2 and s["closed_trades"] == 2 and s["signals"] == 2
        assert s["started_ts"] == 2_000 and s["elapsed_ms"] == 39_000
        assert s["observed_ms"] == (10_000 - 2_000) + (41_000 - 21_000)
        o = experiment_summary(st, oid, sessions, live_session=None, now_ms=41_000)
        assert o["sessions"] == 1 and o["closed_trades"] == 1                             # never merged
        st.close()


# ---- historical and forward are never summed ------------------------------------------------------------

class TestCandidatesPayload:
    def test_historical_and_forward_stay_separate(self, tmp_path):
        from app.core.candidates_view import candidates_payload
        st = Storage(str(tmp_path / "c.db"))
        st.candidate_run_start({"run_id": "r1", "created_ts": 1, "status": "running", "protocol": {"version": "V"}})
        st.candidate_run_update("r1", status="complete")
        result = {"key": cv.CANDIDATES[0], "verdict": "FAIL", "manifest": {"manifest_fingerprint": "m"},
                  "stages": {"MULTI_YEAR": "FAIL", "MONTE_CARLO": "PASS", "STRESS": "FAIL", "VERDICT": "FAIL"},
                  "metrics": {"net_profit": -3.0, "profit_factor": float("inf")}, "daily_equity": [[1, 20.0], [2, 17.0]]}
        st.candidate_result_save("r1", cv.CANDIDATES[0], result, 2)
        shadow = {"bots": [{"key": cv.CANDIDATES[0], "live": True, "mode": "NORMAL", "equity": 21.0, "net": 1.0,
                            "forward": {"trades": 3, "net": 1.5}}],
                  "forward_experiment": {"experiment_id": "fx-1", "sessions": 2}}
        p = candidates_payload(st, shadow)
        c = p["candidates"][0]
        assert c["historical"]["metrics"]["net_profit"] == -3.0 and c["forward"]["net"] == 1.5
        assert c["historical"]["metrics"]["profit_factor"] == 999.0             # stored finite
        stages = {x["stage"]: x["status"] for x in c["pipeline"]}
        assert stages["MULTI-YEAR"] == "FAIL" and stages["QUALIFICATION"] == "NOT ELIGIBLE"
        assert stages["TEST"] == "PASS" and stages["FORWARD"] == "COLLECTING"
        assert c["venues"]["historical"]["venue"] == "BINANCE_USDM" and c["venues"]["target_live"]["venue"] == "BYBIT_LINEAR"
        assert "never added" in p["note"]
        assert "ledger" not in c["historical"]                                  # heavy data only on detail
        st.close()

    def test_public_candidates_route_is_get_only(self):
        from app.main import create_app
        from tests.conftest import settings_factory
        app = create_app(settings_factory(data_dir="/tmp/candroutes"))
        paths = {path: {method.upper() for method in methods} for path, methods in app.openapi()["paths"].items()}
        assert set(paths["/api/public/competition/candidates"]) <= {"GET", "HEAD"}
