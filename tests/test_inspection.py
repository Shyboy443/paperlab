"""PUBLIC INSPECTION (/public/inspection.json, /public/inspection/bot/{id}.json) and the results index.

The contract: a reader without JavaScript, cookies or credentials gets one compact JSON document with
the real current results -- and nothing secret, ever: no API key, exchange secret, dashboard password,
auth header, live order id or exception trace, even when those exist in settings, the environment or
the stored ledgers."""
from __future__ import annotations

import json

import pytest

from app.core import inspection
from app.core import results_index as ri
from tests.test_public_api import PW, SECRET_KEY, SECRET_SECRET, anon  # noqa: F401  (fixture re-export)

FORBIDDEN = ("OPENROUTER", "sk-or-", "api_key", "api_secret", "apikey", "Authorization", "Bearer ",
             "password", "LIVE-ORDER-9911", "exchange_order_id", "Traceback", "RAILWAY", "X-MBX-APIKEY")
FAKE_OPENROUTER = "sk-or-v1-" + "f" * 40


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(inspection, "_CACHE", {"ts": 0.0, "value": None})
    monkeypatch.setattr(ri, "_CACHE", {"key": None, "ts": 0.0, "value": None})
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    monkeypatch.setenv("RAILWAY_TOKEN", "railway-secret-" + "r" * 16)


def _seed_v31(storage) -> None:
    from tests.test_v31 import rec
    cfg = {"trade_from": "2025-11-01", "trade_to": "2026-04-30", "venue": "BYBIT_LINEAR", "dataset_role": "DEVELOPMENT"}
    storage.v31_run_start({"run_id": "v31-insp", "created_ts": 5, "dataset_role": "DEVELOPMENT", "config": cfg,
                           "config_fingerprint": "f"})
    summary = {"passed_all": [], "advanced_set": "NONE",
               "pairs": [{"pair_id": "v31pair:S37-SUI-15m"}],
               "jev_pooled": {"decisions": 45, "final": {"TAKE": 40, "SKIP": 5}, "skip_rate": 0.111, "attack_rate": 0.0,
                              "auc_support": {"auc": 0.52, "low": 0.44, "high": 0.60}, "latency_ms": {"p50": 310}},
               "baselines": {"jev_beats_control": 0, "jev_beats_random_p90": 0, "jev_total": -1.0, "control_total": -0.5,
                             "random_median_total": -0.8},
               "selection_alpha": {"total_usdt": -0.2}, "answers": {"aggressive_edge": "NO"}}
    storage.v31_run_update("v31-insp", status="complete", summary_json=summary)
    c = rec(n=60, net_each=0.3, tf="15m", days=181.0)
    c.update({"balance": 20.0})
    storage.v31_bot_save("v31-insp", c, ts=1)
    storage.v31_bot_analysis("v31-insp", c["key"], {"state": "LOW_ACTIVITY_EDGE", "failure_mode": "TOO_LITTLE_ACTIVITY",
                                                    "concentration": {"net_without_top3": 1.25},
                                                    "gates": [{"name": "participation", "ok": False, "actual": 0.33,
                                                               "threshold": ">= 0.5/day"}]},
                             "LOW_ACTIVITY_EDGE", "TOO_LITTLE_ACTIVITY", None)
    j = rec(role="JEV", n=40, net_each=0.2, tf="15m", days=181.0)
    j.update({"balance": 20.0})
    j["identity"]["jev_policy"] = "JEV_POLICY_V3"
    storage.v31_bot_save("v31-insp", j, ts=1)
    storage.v31_bot_analysis("v31-insp", j["key"], {
        "state": "FAIL", "failure_mode": "JEV_NO_SELECTION_ALPHA",
        "jev": {"decisions": 45, "final": {"TAKE": 40, "SKIP": 5}, "skip_rate": 0.111, "attack_rate": 0.0,
                "auc_support": {"auc": 0.52, "low": 0.44, "high": 0.60}, "latency_ms": {"p50": 310}},
        "baselines": {"control_net": 5.0, "selection": {"alpha_usdt": -0.2}}}, "FAIL", "JEV_NO_SELECTION_ALPHA", None)


def _scan(body: str) -> None:
    for bad in FORBIDDEN + (SECRET_KEY, SECRET_SECRET, PW, FAKE_OPENROUTER):
        assert bad.lower() not in body.lower(), f"inspection output leaks {bad!r}"


class TestInspection:
    def test_snapshot_shape_size_and_plain_get(self, anon):  # noqa: F811
        _seed_v31(anon.app.state.competition.storage)
        r = anon.get("/public/inspection.json", follow_redirects=False)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        assert "set-cookie" not in {k.lower() for k in r.headers}
        assert len(r.content) < 500_000
        d = r.json()
        for k in ("generated_at", "system", "summary", "top_bots", "top_jev_bots", "leaderboard", "experiments",
                  "forward_shadow", "jev", "qualification", "root_causes", "decomposition", "links"):
            assert k in d, k
        for k in ("competition_status", "ws_status", "jev_status", "venue", "execution", "real_orders_placed"):
            assert k in d["system"], k
        assert d["system"]["real_orders_placed"] == 0
        assert d["system"]["execution"].startswith(("PAPER", "UNKNOWN", "LIVE"))
        top = d["top_bots"]
        assert top and top[0]["bot_id"] == "v31d:S37-SUI-15m-CONTROL"
        for f in ("strategy", "coin", "timeframe", "mode", "equity", "return_pct", "trades", "trades_per_day",
                  "gross_pnl", "fees", "slippage", "funding", "net_pnl", "expectancy_r", "profit_factor",
                  "max_drawdown", "cost_to_edge", "status", "failure_reason", "qualified"):
            assert f in top[0], f
        assert top[0]["failure_reason"] == "LOW_ACTIVITY" and top[0]["qualified"] is False
        jev = d["top_jev_bots"][0]["jev"]
        for f in ("jev_policy", "decisions", "skip_rate", "take_rate", "attack_rate", "auc", "selection_alpha",
                  "avg_latency_ms"):
            assert f in jev, f
        assert jev["jev_policy"] == "JEV_POLICY_V3" and jev["selection_alpha"] == -0.2
        assert d["summary"]["qualified"] == 0 and d["summary"]["advanced_set"] == "NONE"
        assert d["summary"]["jev_helping"].startswith("NO")
        _scan(r.text)

    def test_get_only(self, anon):  # noqa: F811
        for path in ("/public/inspection.json", "/public/inspection/bot/v31d:x.json"):
            assert anon.post(path, json={}, headers={"X-PaperLab": "1"}).status_code in (401, 405)
            assert anon.delete(path).status_code in (401, 405)
        assert anon.head("/public/inspection.json").status_code == 200

    def test_bot_detail(self, anon):  # noqa: F811
        _seed_v31(anon.app.state.competition.storage)
        r = anon.get("/public/inspection/bot/v31d:S37-SUI-15m-CONTROL.json")
        assert r.status_code == 200
        d = r.json()
        assert d["bot_id"] == "v31d:S37-SUI-15m-CONTROL" and d["trades"] == 60
        assert d["analysis"]["gates"][0]["name"] == "participation"
        assert d["evaluations"] and d["evaluations"][0]["stage"] == "DEVELOPMENT"
        _scan(r.text)
        miss = anon.get("/public/inspection/bot/nope.json")
        assert miss.status_code == 404 and miss.json()["ok"] is False
        _scan(miss.text)

    def test_the_seeded_live_order_id_and_secrets_never_leak(self, anon):  # noqa: F811
        """The season fixture has a fill with exchange_order_id LIVE-ORDER-9911 and real-looking keys in
        settings; the snapshot must carry neither."""
        _seed_v31(anon.app.state.competition.storage)
        body = anon.get("/public/inspection.json").text
        _scan(body)
        assert "v1s:" in body or "v1v:" in body          # the V1 season / validation rows are indexed


class TestResultsIndex:
    def test_failure_derivation_order(self):
        base = {"trades": 50, "gross_pnl": 1.0, "net_pnl": 0.5, "fees": 0.3, "slippage": 0.2, "max_drawdown": 0.1,
                "liquidations": 0}
        assert ri.derive_failure(base, True) is None
        assert ri.derive_failure({**base, "liquidations": 1}, False) == "LIQUIDATION"
        assert ri.derive_failure({**base, "trades": 0}, False) == "NO_TRADES"
        assert ri.derive_failure({**base, "gross_pnl": -1.0}, False) == "NO_GROSS_EDGE"
        assert ri.derive_failure({**base, "net_pnl": -0.1}, False) == "FEE_DESTROYED"
        assert ri.derive_failure({**base, "net_pnl": -0.1, "slippage": 0.5}, False) == "SLIPPAGE_DESTROYED"
        assert ri.derive_failure({**base, "trades": 10}, False) == "LOW_ACTIVITY"
        assert ri.derive_failure(base, False, reasons=["net without top3 <= 0"]) == "PROFIT_CONCENTRATION"

    def test_current_row_is_the_most_advanced_evaluation_and_keeps_pipeline_flags(self):
        dev = {"bot_id": "a", "stage": "DEVELOPMENT", "window_to": "2026-04", "_identity": ("X", "k"),
               "passed_discovery": True}
        test = {"bot_id": "b", "stage": "TEST", "window_to": "2026-06", "_identity": ("X", "k"), "passed_holdout": False}
        cur = ri.current_rows([dev, test])
        assert len(cur) == 1 and cur[0]["bot_id"] == "b"
        assert cur[0]["passed_discovery"] is True and cur[0]["passed_holdout"] is False

    def test_ranking_needs_a_sample_and_excludes_jev_and_live(self):
        rows = [{"bot_id": "few", "trades": 5, "expectancy_r": 2.0, "mode": "CONTROL", "stage": "TEST"},
                {"bot_id": "ok", "trades": 40, "expectancy_r": 0.1, "mode": "CONTROL", "stage": "TEST"},
                {"bot_id": "jev", "trades": 40, "expectancy_r": 0.5, "mode": "JEV", "stage": "TEST"},
                {"bot_id": "live", "trades": 400, "expectancy_r": 0.9, "mode": "CONTROL", "stage": "LIVE_PAPER"}]
        assert [r["bot_id"] for r in ri.ranked(rows)] == ["ok"]
        assert [r["bot_id"] for r in ri.ranked(rows, jev=True)] == ["jev"]

    def test_decomposition_diagnoses(self):
        rows = [{"trades": 10, "gross_pnl": -1.0, "fees": 0.2, "slippage": 0.1, "funding": 0.0, "net_pnl": -1.3, "g": "a"},
                {"trades": 10, "gross_pnl": 1.0, "fees": 0.8, "slippage": 0.4, "funding": 0.0, "net_pnl": -0.2, "g": "b"},
                {"trades": 10, "gross_pnl": 2.0, "fees": 0.5, "slippage": 0.1, "funding": 0.0, "net_pnl": 1.4, "g": "c"}]
        d = {x["group"]: x["diagnosis"] for x in ri.decomposition(rows, lambda r: r["g"])}
        assert d == {"a": "NO RAW EDGE", "b": "EDGE DESTROYED BY COSTS", "c": "EDGE SURVIVES COSTS"}


class TestSimplifiedUIRoutes:
    """The SYSTEM page, the V4 arena and a trader's trades: public, GET only, allow-listed, never a secret."""

    def test_system_payload_is_safe_and_shows_safety(self, anon):  # noqa: F811
        r = anon.get("/api/public/competition/system")
        assert r.status_code == 200
        d = r.json()
        for k in ("safety", "stream", "jev", "forward_shadow", "data", "database", "environment", "research", "developer"):
            assert k in d, k
        assert "execution" in d["safety"] and "kill_switch" in d["safety"]
        assert d["database"]["schema_version"] == 17
        _scan(r.text)

    def test_v4_payload_without_runs(self, anon):  # noqa: F811
        d = anon.get("/api/public/competition/v4").json()
        assert d["ok"] and d["dev"] is None and d["test"] is None and d["note"] == "no V4 run yet"

    def test_v5_is_the_current_arena(self, anon):  # noqa: F811
        """V5 has its own route, its official controls reach the traders / leaderboard, inspection.json carries a V5
        block, a V5 bot's detail carries the money waterfall, and the superseded / capacity books do not rank."""
        from app.competition import v5_run as vr
        from app.competition.v5_config import V5Config
        from tests.test_v5 import pool, rec
        st = anon.app.state.competition.storage
        assert anon.get("/api/public/competition/v5").json()["note"] == "no V5 run yet"
        cfg = V5Config(trade_from="2025-03-01", trade_to="2025-05-29", months=(), coins=("A",))
        run_id = vr.new_run(st, cfg, "t")
        control = rec(pool(0.3, n=40, seed=3), coin="A")
        st.v5_bot_save(run_id, control)
        st.v5_bot_save(run_id, rec(pool(0.3, n=40, seed=4), coin="A", role="CAPACITY", balance=100.0))
        a = vr.an.analyze_bot(control, cfg)
        st.v5_bot_analysis(run_id, control["key"], a, a["state"], a["failure_mode"], None)
        summary = {"run_id": run_id, "dataset_role": "DEVELOPMENT", "advanced_set": "NONE", "passed_all": [],
                   "answers": {"raw_edge": "NO family x class has a raw edge"}, "families": [], "pairs": [],
                   "viability": [{"horizon": "HOURLY", "bots": 1}], "costs": {"controls_20": {"net": 1.0}},
                   "leaderboard_controls": [{"key": control["key"], "rank": 1}], "counts": {"controls": 1}}
        st.v5_run_update(run_id, summary_json=summary, status="complete", stage="ANALYZED")
        v5 = anon.get("/api/public/competition/v5").json()
        assert v5["dev"]["run"]["run_id"] == run_id and v5["dev"]["answers"]["raw_edge"].startswith("NO")
        snap = anon.get("/public/inspection.json").json()
        assert snap["v5"]["runs"]["development"]["run_id"] == run_id and snap["v5"]["advanced_set"] == "NONE"
        ids = [r["bot_id"] for r in snap["leaderboard"]] + [r["bot_id"] for r in snap["top_bots"]]
        assert f"v5d:{control['key']}" in ids and not any("CAP100" in i for i in ids)
        det = anon.get(f"/public/inspection/bot/v5d:{control['key']}.json").json()
        assert det["analysis"]["money"]["maker_fees"] == 0.0 and det["analysis"]["label"]
        t = anon.get(f"/api/public/competition/trader/v5d:{control['key']}/trades?limit=3").json()
        assert t["stored"] and t["shown"] == 3 and "funding_paid" in t["trades"][0]
        _scan(str(snap))

    def test_trader_trades(self, anon):  # noqa: F811
        _seed_v31(anon.app.state.competition.storage)
        r = anon.get("/api/public/competition/trader/v31d:S37-SUI-15m-CONTROL/trades?limit=5")
        assert r.status_code == 200
        d = r.json()
        assert d["stored"] and d["total"] == 60 and d["shown"] == 5
        assert set(d["trades"][0]) <= {"entry_ts", "exit_ts", "side", "entry", "exit", "qty", "notional", "gross", "fees",
                                       "slippage", "funding", "net", "r", "exit_kind", "hold_s", "tier", "risk_pct",
                                       "jev_level", "jev_mult",
                                       # V5 trades also carry their entry context and the funding split
                                       "signal_ts", "maker_fees", "taker_fees", "funding_paid", "funding_received",
                                       "setup", "stop_pct", "expected_move_pct", "expected_funding_pct", "regime",
                                       "vol_band", "trend_c1", "trend_c2", "positioning"}
        assert not {"pid", "decision_id", "order_id", "client_order_id"} & set(d["trades"][0])
        other = anon.get("/api/public/competition/trader/v1s:T01/trades").json()
        assert other["stored"] is False
        _scan(r.text)

    def test_new_routes_are_get_only(self, anon):  # noqa: F811
        for path in ("/api/public/competition/system", "/api/public/competition/v4",
                     "/api/public/competition/trader/v31d:x/trades"):
            assert anon.post(path, json={}, headers={"X-PaperLab": "1"}).status_code in (401, 405), path


def test_every_program_speaks_one_root_cause_taxonomy():
    """V4's analyzer labels map onto the same causes as V1-V3.1 (a NO_RAW_EDGE bot is a NO_GROSS_EDGE bot)."""
    from app.competition.v4_analyzer import LABEL
    for mode in LABEL:
        if mode in ("ROBUST",):
            continue
        assert mode in ri.ROOT_CAUSE, f"V4 failure mode {mode} has no place in the unified taxonomy"
    assert ri.ROOT_CAUSE["NO_RAW_EDGE"] == "NO_GROSS_EDGE"
