"""Public read-only inspection surface.

The contract: an anonymous browser can read the competition, and can change nothing. Both halves
are tested, because either one alone is worthless -- an unreadable page defeats the purpose, and a
writable one is a security hole.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.core.storage import Storage
from tests.conftest import T0, make_candles, settings_factory
from tests.test_competition import Idle, OneShot, big_rules

SYMBOL = "BTCUSDT"
PW = "test-pw"
SECRET_KEY = "k" * 20
SECRET_SECRET = "s" * 20
PUB = "/api/public/competition"


@pytest.fixture
def anon(tmp_path, monkeypatch):
    """The app with NO credentials attached to the client, and real secrets in settings."""
    from app.core import engine_boot
    from tests.conftest import FakeFeed
    monkeypatch.setattr(engine_boot, "MarketFeed", FakeFeed)
    from app.main import create_app
    settings = settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS=SYMBOL,
                                BINANCE_API_KEY=SECRET_KEY, BINANCE_API_SECRET=SECRET_SECRET)
    app = create_app(settings)
    with TestClient(app) as c:
        svc = app.state.competition
        svc.classes = {"T01": OneShot, "T02": Idle}
        svc.rules = big_rules()
        svc.storage.insert_candles(make_candles(
            closes=[100.0 * (1.004 ** i) for i in range(60)], symbol=SYMBOL, tf="1m",
            start_ts=T0, wick=0.0))
        _seed(svc.storage)
        yield c


def _seed(storage: Storage) -> None:
    storage.save_competition(
        {"run_id": "pubrun", "season_id": "s1", "label": "public season", "status": "done",
         "symbols": [SYMBOL], "starting_balance": 20.0, "max_leverage": 20,
         "execution_profile": "realistic", "created_ts": 1, "finished_ts": 2, "bars": 100,
         "summary": {"competitors": 1}},
        [{"strategy_id": "T01", "version": "T01@10x:abc", "name": "one shot", "state": "FAILED",
          "rank": 1, "score": -0.3,
          "metrics": {"trades": 12, "net_profit": -2.0, "ending_equity": 18.0,
                      "starting_equity": 20.0, "net_return_pct": -0.1, "fees_paid": 0.4},
          "qualification": {"state": "FAILED", "reasons": ["min_net_profit -2 >= 0"],
                            "evaluated_stages": ["season"], "gates": []},
          "score_breakdown": {"total": -0.3, "components": {}, "penalties": {}},
          "equity": [[1, 20.0], [2, 18.0]],
          "fills": [
              {"id": "f1", "ts": 1, "symbol": SYMBOL, "side": "BUY", "kind": "entry", "qty": 1.0,
               "price": 100.0, "ref_price": 99.9, "fee": 0.05, "realized_pnl": 0.0,
               "simulated": True, "position_id": "p1", "signal_id": "sig1",
               "exchange_order_id": None, "liquidity_role": "TAKER", "execution_level": 3},
              {"id": "f2", "ts": 2, "symbol": SYMBOL, "side": "SELL", "kind": "stop", "qty": 1.0,
               "price": 98.0, "ref_price": 98.1, "fee": 0.05, "realized_pnl": -2.0,
               "simulated": False, "position_id": "p1", "signal_id": "sig1",
               "exchange_order_id": "LIVE-ORDER-9911", "liquidity_role": "TAKER",
               "execution_level": 3},
          ]}])
    storage.start_validation_run({
        "run_id": "pubval", "created_ts": 1, "status": "done", "stage": "COMPLETE",
        "config_fingerprint": "cfg", "dataset_fingerprint": "ds", "symbols": [SYMBOL],
        "first_month": "2021-01", "last_month": "2026-08", "windows": 61,
        "total_competitors": 1, "starting_balance": 20.0, "leverages": [5, 10, 20]})
    storage.save_validation_competitor("pubval", {
        "key": "T01@10x", "strategy_id": "T01", "leverage": 10, "version": "v", "name": "one shot",
        "state": "FAILED", "score": {"total": -0.4}, "elapsed_s": 1.0,
        "oos_metrics": {"trades": 120, "net_profit": -5.0, "net_return_pct": -0.25},
        "walk_forward": {"total_windows": 61, "active_windows": 40, "profitable_windows": 10,
                         "profitable_ratio": 0.25, "active_ratio": 0.65, "windows": [],
                         "by_symbol": {}},
        "monte_carlo": {"ran": False}, "stress": {"ran": False},
        "qualification": {"state": "FAILED", "reasons": ["x"], "evaluated_stages": ["season"],
                          "gates": []}})


# ---- 1,2,3: public reads work with no credentials at all ------------------------------------

class TestPublicReads:
    def test_overview_without_authentication(self, anon):
        r = anon.get(PUB)
        assert r.status_code == 200
        assert r.json()["ran"] is True

    def test_leaderboard_without_authentication(self, anon):
        r = anon.get(PUB + "/leaderboard")
        assert r.status_code == 200
        assert r.json()["rows"], "expected the seeded competitor"

    def test_bot_detail_without_authentication(self, anon):
        r = anon.get(PUB + "/bots/T01")
        assert r.status_code == 200
        assert r.json()["bot"]["strategy_id"] == "T01"

    def test_validation_without_authentication(self, anon):
        assert anon.get(PUB + "/validation").status_code == 200
        assert anon.get(PUB + "/validation/leaderboard").status_code == 200

    def test_seasons_and_qualification_without_authentication(self, anon):
        assert anon.get(PUB + "/seasons").status_code == 200
        assert anon.get(PUB + "/qualification/T01").status_code == 200

    def test_no_basic_auth_challenge_is_issued(self, anon):
        r = anon.get(PUB)
        assert "www-authenticate" not in {k.lower() for k in r.headers}

    def test_public_page_loads_without_a_login(self, anon):
        r = anon.get("/public/competition")
        assert r.status_code == 200
        assert "READ-ONLY INSPECTION" in r.text
        assert 'id="login"' not in r.text, "the public page must not carry the login overlay"

    def test_deep_links_serve_the_same_shell(self, anon):
        for path in ("/public/competition/validation", "/public/competition/bot/T01",
                     "/public/competition/leaderboard"):
            r = anon.get(path)
            assert r.status_code == 200 and 'id="home-grid"' in r.text and "/static/arena.js" in r.text, path

    def test_public_get_needs_no_csrf_header(self, anon):
        assert anon.get(PUB, headers={}).status_code == 200


# ---- 4,10,11,12: nothing sensitive travels ----------------------------------------------------

SECRET_PATTERNS = (SECRET_KEY, SECRET_SECRET, PW, "api_key", "api_secret", "dashboard_password",
                   "BINANCE_API", "RAILWAY_", "LIVE-ORDER")


class TestNoSecrets:
    def _all_public_payloads(self, anon) -> str:
        blobs = []
        for path in (PUB, PUB + "/leaderboard", PUB + "/seasons", PUB + "/bots/T01",
                     PUB + "/bots/T01/trades", PUB + "/qualification/T01", PUB + "/validation",
                     PUB + "/validation/leaderboard", PUB + "/validation/bots/T01@10x",
                     PUB + "/validation/leverage-comparison"):
            r = anon.get(path)
            if r.status_code == 200:
                blobs.append(json.dumps(r.json()))
        return "\n".join(blobs)

    def test_public_payloads_contain_no_secrets(self, anon):
        blob = self._all_public_payloads(anon)
        for pattern in SECRET_PATTERNS:
            assert pattern not in blob, f"public payload leaked {pattern!r}"

    def test_public_payloads_expose_no_environment_or_paths(self, anon):
        blob = self._all_public_payloads(anon)
        assert "data_dir" not in blob and "db_path" not in blob
        assert not re.search(r"[A-Za-z]:\\\\Users", blob), "a filesystem path leaked"

    def test_trade_ledger_exposes_only_simulated_fills(self, anon):
        rows = anon.get(PUB + "/bots/T01/trades").json()["rows"]
        assert len(rows) == 1, "a live fill was published"
        assert all("exchange_order_id" not in r for r in rows)

    def test_trade_ledger_drops_internal_identifiers(self, anon):
        rows = anon.get(PUB + "/bots/T01/trades").json()["rows"]
        for r in rows:
            for banned in ("position_id", "signal_id", "id", "simulated"):
                assert banned not in r, f"ledger exposed {banned}"

    def test_public_frontend_carries_no_credentials(self):
        """The public entry points must not CONSTRUCT credentials.

        Checked against code with comments stripped: prose that says "no Authorization header" is
        not a credential, and an earlier version of this test failed on exactly that.
        """
        import re
        from pathlib import Path
        for name in ("public.html", "public.js", "home.js"):
            body = (Path("app/dashboard") / name).read_text(encoding="utf-8")
            code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            code = re.sub(r"(?m)^\s*//.*$", "", code)
            code = re.sub(r"(?m)<!--.*?-->", "", code, flags=re.S)
            assert "btoa(" not in code, f"{name} builds a Basic auth header"
            assert "sessionStorage" not in code, f"{name} reads a stored credential"
            assert not re.search(r"Authorization\s*[:=]", code), f"{name} sets an auth header"
            assert not re.search(r"password\s*[:=]", code, re.I), f"{name} carries a password"


# ---- 5,6,7,8,9: mutations stay private --------------------------------------------------------

class TestMutationsRejected:
    def test_unauthenticated_run_is_rejected(self, anon):
        r = anon.post("/api/competition/run", json={}, headers={"X-PaperLab": "1"})
        assert r.status_code == 401

    def test_unauthenticated_cancel_is_rejected(self, anon):
        r = anon.post("/api/competition/cancel", json={}, headers={"X-PaperLab": "1"})
        assert r.status_code == 401

    def test_unauthenticated_promotion_is_rejected(self, anon):
        r = anon.post("/api/strategies/T01/promote", json={}, headers={"X-PaperLab": "1"})
        assert r.status_code == 401

    def test_unauthenticated_live_arm_is_rejected(self, anon):
        r = anon.post("/api/live/arm", json={"phrase": "GO LIVE"}, headers={"X-PaperLab": "1"})
        assert r.status_code == 401

    def test_unauthenticated_kill_and_reset_are_rejected(self, anon):
        for path in ("/api/kill", "/api/reset", "/api/flatten", "/api/engine/stop"):
            r = anon.post(path, json={}, headers={"X-PaperLab": "1"})
            assert r.status_code == 401, path

    def test_private_reads_still_require_auth(self, anon):
        for path in ("/api/state", "/api/strategies", "/api/competition",
                     "/api/competition/leaderboard", "/api/competition/validation"):
            assert anon.get(path).status_code == 401, path

    def test_authenticated_private_routes_still_work(self, anon):
        assert anon.get("/api/state", auth=("admin", PW)).status_code == 200
        assert anon.get("/api/competition", auth=("admin", PW)).status_code == 200

    def test_there_is_no_post_route_under_public(self):
        from app.main import create_app
        app = create_app(settings_factory(data_dir="/tmp/pubroutes"))
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/public/"):
                assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}, path

    def test_public_module_defines_no_mutating_handler(self):
        import ast
        import inspect
        from app.core import api_public
        tree = ast.parse(inspect.getsource(api_public))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    attr = getattr(getattr(dec, "func", dec), "attr", "")
                    assert attr not in ("post", "put", "patch", "delete"), node.name


class TestPublicDiagnostics:
    def test_diagnostics_identify_the_build_without_secrets(self, anon):
        d = anon.get(PUB).json()["diagnostics"]
        assert d["read_only"] is True
        assert d["api_version"] and d["asset_version"] and d["generated_at"]
        blob = json.dumps(d)
        for pattern in SECRET_PATTERNS:
            assert pattern not in blob

    def test_validation_diagnostics_carry_the_fingerprints(self, anon):
        d = anon.get(PUB + "/validation").json()["diagnostics"]
        assert d["dataset_fingerprint"] == "ds"
        assert d["config_fingerprint"] == "cfg"


class TestMissingValues:
    def test_unknown_bot_is_a_clean_404_not_a_blank(self, anon):
        r = anon.get(PUB + "/bots/NOPE")
        assert r.status_code == 404 and r.json()["ok"] is False

    def test_unrun_stages_report_not_run(self, anon):
        stages = {s["name"]: s["status"] for s in anon.get(PUB + "/qualification/T01").json()["stages"]}
        assert stages["walk_forward"] == "NOT_RUN"
        assert stages["monte_carlo"] == "NOT_RUN"
        assert stages["season"] == "RAN"
