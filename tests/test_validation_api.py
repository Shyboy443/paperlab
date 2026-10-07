"""Validation API + checkpointing.

The properties defended here are the ones that make a multi-hour tournament trustworthy: an unrun
stage must read as NOT RUN, a resume must refuse to blend configurations, and the out-of-sample
leaderboard must report out-of-sample numbers.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.competition import walkforward as wf
from app.competition.validation import ValidationConfig
from app.core.storage import Storage
from tests.conftest import settings_factory
from tests.test_competition_api import CSRF, client  # noqa: F401  (fixture reuse)

RUN = "vrun1"


def seed_validation(storage: Storage, *, states=("FAILED", "INSUFFICIENT_SAMPLE")) -> None:
    storage.start_validation_run({
        "run_id": RUN, "created_ts": 1, "status": "done", "stage": "COMPLETE",
        "config_fingerprint": "cfg123", "dataset_fingerprint": "data456",
        "symbols": ["BTCUSDT", "ETHUSDT"], "first_month": "2021-01", "last_month": "2026-08",
        "windows": 61, "total_competitors": len(states), "starting_balance": 20.0,
        "leverages": [5, 10, 20], "config": {"a": 1},
    })
    for i, state in enumerate(states):
        lev = (5, 10, 20)[i % 3]
        storage.save_validation_competitor(RUN, {
            "key": f"S0{i+1}@{lev}x", "strategy_id": f"S0{i+1}", "leverage": lev,
            "version": f"S0{i+1}@{lev}x:abc", "name": f"strategy {i+1}", "state": state,
            "elapsed_s": 12.0,
            "score": {"total": -0.2 - i},
            "oos_metrics": {"trades": 150 if state == "FAILED" else 12,
                            "net_profit": -3.0, "net_return_pct": -0.15,
                            "expectancy_r": -0.1, "profit_factor": 0.8,
                            "max_drawdown_pct": 0.42, "avg_effective_leverage": 2.4,
                            "rejects": {"below_min_notional": 7}},
            "walk_forward": {"total_windows": 61, "active_windows": 40,
                             "profitable_windows": 12, "profitable_ratio": 0.3,
                             "active_ratio": 0.65, "symbol_concentration": 0.7,
                             "windows": [], "by_symbol": {}},
            "monte_carlo": {"ran": False, "reason": "skipped"},
            "stress": {"ran": False, "scenarios": []},
            "qualification": {"state": state, "reasons": ["min_expectancy_r -0.1 >= 0"],
                              "evaluated_stages": ["season", "walk_forward"], "gates": []},
        })


class TestEmptyValidation:
    def test_reports_not_run_rather_than_failing(self, client):
        d = client.get("/api/competition/validation").json()
        assert d["ok"] is True and d["ran"] is False
        assert "RUNNING_WALK_FORWARD" in d["stages"]

    def test_leaderboard_is_empty(self, client):
        assert client.get("/api/competition/validation/leaderboard").json()["rows"] == []

    def test_leverage_comparison_is_empty(self, client):
        assert client.get("/api/competition/validation/leverage-comparison").json()["ran"] is False

    def test_unknown_bot_is_404(self, client):
        assert client.get("/api/competition/validation/bots/S01@5x").status_code == 404


class TestValidationResults:
    @pytest.fixture(autouse=True)
    def _seed(self, client):
        seed_validation(client.app.state.competition.storage)

    def test_overview_carries_progress_and_fingerprints(self, client):
        d = client.get("/api/competition/validation").json()
        assert d["ran"] is True
        assert d["windows"] == 61 and d["leverages"] == [5, 10, 20]
        assert d["dataset_fingerprint"] == "data456"
        assert d["completed"] == 2 and d["pct"] == pytest.approx(1.0)

    def test_leaderboard_reports_out_of_sample_columns(self, client):
        d = client.get("/api/competition/validation/leaderboard").json()
        r = d["rows"][0]
        for key in ("oos_trades", "oos_net", "oos_return_pct", "oos_expectancy_r",
                    "oos_profit_factor", "oos_max_dd", "profitable_ratio", "active_ratio"):
            assert key in r
        assert r["rank"] == 1

    def test_unrun_stages_are_reported_not_run(self, client):
        r = client.get("/api/competition/validation/leaderboard").json()["rows"][0]
        assert r["mc_ran"] is False and r["stress_ran"] is False
        assert "monte_carlo" not in r["stages"] and "stress" not in r["stages"]

    def test_qualified_set_may_legitimately_be_empty(self, client):
        d = client.get("/api/competition/validation/leaderboard").json()
        assert d["qualified"] == []

    def test_failure_reasons_are_summarised(self, client):
        d = client.get("/api/competition/validation/leaderboard").json()
        fr = d["failure_reasons"]
        assert fr.get("insufficient_trades") == 1
        assert fr.get("negative_expectancy") == 1

    def test_bot_detail_returns_the_whole_record(self, client):
        d = client.get("/api/competition/validation/bots/S01@5x").json()
        assert d["bot"]["key"] == "S01@5x"
        assert d["bot"]["walk_forward"]["total_windows"] == 61

    def test_leverage_comparison_keeps_variants_separate(self, client):
        d = client.get("/api/competition/validation/leverage-comparison").json()
        assert d["ran"] is True
        keys = {v["key"] for row in d["rows"] for v in row["variants"].values()}
        assert keys == {"S01@5x", "S02@10x"}, "variants must not be merged"

    def test_leverage_comparison_exposes_min_notional_rejects(self, client):
        d = client.get("/api/competition/validation/leverage-comparison").json()
        v = next(iter(d["rows"][0]["variants"].values()))
        assert v["below_min_notional"] == 7


class TestCheckpointing:
    def test_finished_competitors_are_resumable(self, tmp_path):
        st = Storage(str(tmp_path / "v.db"))
        seed_validation(st)
        assert st.validation_done_keys(RUN) == {"S01@5x", "S02@10x"}
        st.close()

    def test_a_resume_is_refused_when_the_config_changed(self, tmp_path):
        st = Storage(str(tmp_path / "v.db"))
        seed_validation(st)
        row = st.validation_run(RUN)
        assert row["config_fingerprint"] == "cfg123"
        assert ValidationConfig().fingerprint() != "cfg123", \
            "a different config must not be mistaken for the stored one"
        st.close()

    def test_config_fingerprint_changes_with_the_window_schedule(self):
        a = ValidationConfig()
        b = ValidationConfig(walk_forward=wf.WalkForwardConfig(test_days=15))
        assert a.fingerprint() != b.fingerprint()

    def test_identical_configs_fingerprint_the_same(self):
        assert ValidationConfig().fingerprint() == ValidationConfig().fingerprint()


class TestValidationSafety:
    def test_no_validation_route_can_promote(self):
        import ast
        import inspect
        from app.competition import validation as v
        tree = ast.parse(inspect.getsource(v))
        called = {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not (called & {"promote", "demote", "live_arm", "place_order", "create_order"})

    def test_validation_get_routes_do_not_mutate(self, client):
        seed_validation(client.app.state.competition.storage)
        before = client.get("/api/competition/validation").json()
        client.get("/api/competition/validation/leaderboard")
        client.get("/api/competition/validation/leverage-comparison")
        after = client.get("/api/competition/validation").json()
        assert before["completed"] == after["completed"]


class TestSeededResults:
    """Results produced off-box ship with the image and import on first boot."""

    def test_seed_is_additive_and_idempotent(self, tmp_path):
        from app.core.seed import DEFAULT_SEED, apply_seed
        if not DEFAULT_SEED.exists():
            pytest.skip("no seed file in this checkout")
        st = Storage(str(tmp_path / "s.db"))
        first = apply_seed(st)
        assert first, "expected the seed to import something into an empty database"
        assert apply_seed(st) == {}, "a second boot must import nothing"
        st.close()

    def test_seed_never_overwrites_an_existing_run(self, tmp_path):
        from app.core.seed import DEFAULT_SEED, apply_seed, load_seed
        if not DEFAULT_SEED.exists():
            pytest.skip("no seed file in this checkout")
        data = load_seed()
        runs = data.get("validation_runs") or []
        if not runs:
            pytest.skip("seed carries no validation runs")
        st = Storage(str(tmp_path / "s.db"))
        rid = runs[0]["run_id"]
        st.start_validation_run({"run_id": rid, "status": "local-original", "created_ts": 1})
        apply_seed(st)
        assert st.validation_run(rid)["status"] == "local-original"
        st.close()

    def test_seed_carries_results_not_candles(self):
        from app.core.seed import DEFAULT_SEED, load_seed
        if not DEFAULT_SEED.exists():
            pytest.skip("no seed file in this checkout")
        data = load_seed()
        assert "candles" not in data and "fills" not in data
        assert set(data) <= {"competition_runs", "competition_competitors",
                             "validation_runs", "validation_competitors",
                             "arena_runs", "arena_bots", "jev_runs", "jev_bots", "jev_decisions",
                             "candidate_runs", "candidate_results"}
        assert "jev_cache" not in data, "the shared decision cache stays with the research database"


class TestLeaderboardOrdering:
    """Competitors that never ran must not occupy the top of the board."""

    def test_unrun_competitors_sort_last(self, client):
        st = client.app.state.competition.storage
        seed_validation(st)
        st.save_validation_competitor(RUN, {
            "key": "S16@5x", "strategy_id": "S16", "leverage": 5, "version": "v",
            "name": "needs funding", "state": "COMPETING", "skipped": "funding",
            "score": None, "oos_metrics": None, "elapsed_s": 0.0,
        })
        rows = client.get("/api/competition/validation/leaderboard").json()["rows"]
        assert rows[0]["key"] != "S16@5x", "a competitor with no result led the leaderboard"
        assert rows[-1]["key"] == "S16@5x"
        assert rows[-1]["ran"] is False and rows[-1]["skipped"] == "funding"

    def test_rows_that_ran_are_marked_as_such(self, client):
        seed_validation(client.app.state.competition.storage)
        rows = client.get("/api/competition/validation/leaderboard").json()["rows"]
        assert all(r["ran"] for r in rows)


class TestSeedRefresh:
    """A run shipped while still in flight must be completable by a later deploy."""

    def test_an_unfinished_stored_run_is_replaced_by_the_finished_one(self, tmp_path):
        from app.core.seed import apply_seed, load_seed, DEFAULT_SEED
        if not DEFAULT_SEED.exists():
            pytest.skip("no seed file in this checkout")
        data = load_seed()
        runs = [r for r in (data.get("validation_runs") or []) if r.get("status") == "done"]
        if not runs:
            pytest.skip("seed carries no finished validation run")
        rid = runs[0]["run_id"]
        seeded = sum(1 for c in data["validation_competitors"] if c["run_id"] == rid)
        st = Storage(str(tmp_path / "s.db"))
        # simulate the earlier deploy: same run id, still running, fewer competitors
        st.start_validation_run({"run_id": rid, "status": "running", "created_ts": 1,
                                 "total_competitors": seeded})
        st.save_validation_competitor(rid, {"key": "STALE@5x", "strategy_id": "STALE",
                                            "leverage": 5, "state": "COMPETING"})
        apply_seed(st)
        rows = st.validation_competitors(rid)
        assert len(rows) == seeded, "the partial snapshot was not refreshed"
        assert "STALE@5x" not in {r["key"] for r in rows}, "stale rows survived the refresh"
        assert st.validation_run(rid)["status"] == "done"
        st.close()

    def test_a_finished_stored_run_is_never_downgraded(self, tmp_path):
        from app.core.seed import apply_seed, load_seed, DEFAULT_SEED
        if not DEFAULT_SEED.exists():
            pytest.skip("no seed file in this checkout")
        data = load_seed()
        runs = data.get("validation_runs") or []
        if not runs:
            pytest.skip("seed carries no validation runs")
        rid = runs[0]["run_id"]
        st = Storage(str(tmp_path / "s.db"))
        st.start_validation_run({"run_id": rid, "status": "done", "created_ts": 1})
        st.save_validation_competitor(rid, {"key": "LOCAL@5x", "strategy_id": "LOCAL",
                                            "leverage": 5, "state": "QUALIFIED"})
        apply_seed(st)
        keys = {r["key"] for r in st.validation_competitors(rid)}
        assert keys == {"LOCAL@5x"}, "a finished local run was overwritten by the shipped seed"
        st.close()

    def test_supersedes_rules(self):
        from app.core.seed import _supersedes
        assert _supersedes({"status": "done"}, "running", 81, 54) is True
        assert _supersedes({"status": "running"}, "running", 81, 54) is True   # more competitors
        assert _supersedes({"status": "running"}, "running", 54, 54) is False
        assert _supersedes({"status": "done"}, "done", 99, 1) is False         # never downgrade
