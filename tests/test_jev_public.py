"""CONTROL vs +JEV on the read surfaces: analytics are honest, and nothing public carries a key."""
from __future__ import annotations

import json
import re

import pytest

from app.competition.jev_experiment import analytics, calibration, jev_key, pair_id, sign_test_p
from tests.test_public_api import PUB, SECRET_KEY, anon  # noqa: F401  (fixture reuse)

REPORT = "/public/competition/report"
OR_KEY = "sk-or-v1-" + "f00dfeed" * 8


def _metrics(net, trades, ret=None):
    return {"net_profit": net, "trades": trades, "net_return_pct": net / 20.0 if ret is None else ret,
            "gross_pnl": net + 1.0, "fees_paid": 0.8, "slippage_cost": 0.2, "funding_paid": 0.0,
            "expectancy_r": 0.1 if net > 0 else -0.2, "profit_factor": 1.2 if net > 0 else 0.7,
            "max_drawdown_pct": 0.1, "liquidation_count": 0, "win_rate": 0.45,
            "avg_effective_leverage": 1.2}


def seed_experiment(st, run_id="jx1"):
    """Three pairs: Jev improves one, worsens one (while still making money), leaves one equal."""
    st.start_jev_run({"run_id": run_id, "created_ts": 1, "status": "running", "label": "t",
                      "arena_run_id": "arena1", "config_fingerprint": "cf", "model": "typesafe/jev-1.13",
                      "prompt_version": "JEV_PROMPT_V1", "policy_version": "JEV_POLICY_V1",
                      "first_month": "2026-07", "last_month": "2026-08", "pairs": 3,
                      "config": {"api_key": OR_KEY}, "summary": {}})
    cases = [("S02-SOLUSDT-5m@20x-v1", -3.0, -1.0), ("S04-BNBUSDT-5m@20x-v1", 7.0, 6.0),
             ("S13-XRPUSDT-5m@20x-v1", -2.0, -2.0)]
    for ck, cnet, jnet in cases:
        jk = jev_key(ck, "typesafe/jev-1.13", "JEV_POLICY_V1")
        base = {"strategy_id": ck[:3], "symbol": ck.split("-")[1], "coin": ck.split("-")[1][:-4],
                "timeframe": "5m", "pair_id": pair_id(ck), "control_key": ck, "name": "x"}
        st.save_jev_bot(run_id, {**base, "key": ck, "role": "CONTROL", "state": "FAILED",
                                 "metrics": _metrics(cnet, 50)})
        st.save_jev_bot(run_id, {**base, "key": jk, "role": "JEV", "state": "FAILED",
                                 "metrics": _metrics(jnet, 30)})
        for i, (p, action, kind, net, r) in enumerate([(0.9, "TAKE", "TAKEN", 0.5, 1.0),
                                                       (0.3, "SKIP", "SHADOW", -0.4, -1.0),
                                                       (0.62, "REDUCE", "TAKEN", 0.1, 0.5)]):
            st.save_jev_decision({
                "id": f"{run_id}-{jk}-{i}", "run_id": run_id, "bot_key": jk, "pair_id": pair_id(ck),
                "cache_key": f"k{i}", "signal_ts": 1_780_000_000_000 + i, "side": "long",
                "take_probability": p, "setup_quality": 0.5, "risk_state": "NORMAL", "regime": "RANGE",
                "final_action": action, "final_level": {"TAKE": "NORMAL", "SKIP": "SKIP", "REDUCE": "DEFENSIVE"}[action],
                "risk_multiplier": {"TAKE": 1.0, "SKIP": 0.0, "REDUCE": 0.5}[action],
                "result": "TRADED" if action != "SKIP" else "SKIPPED", "outcome_kind": kind,
                "outcome_net": net, "outcome_r": r, "outcome_exit": "tp", "source": "api",
                "request_latency_ms": 300 + i, "input_tokens": 900, "cost_usd": 0.0000378,
                "model_resolved": "typesafe/jev-1.13-20260917",
                "state_json": json.dumps({"signal": {"side": "long", "stop_distance_pct": 0.01},
                                          "market": {"ret_5": 0.002, "rsi_14": 55.0}})})
    summary = analytics(st, run_id)
    st.update_jev_run(run_id, status="complete", finished_ts=2, summary_json=summary)
    return summary


class TestAnalytics:
    def test_edge_delta_is_jev_minus_control(self, tmp_path):
        from app.core.storage import Storage
        s = seed_experiment(Storage(tmp_path / "j.db"))
        by = {p["control_key"]: p for p in s["pairs_detail"]}
        assert by["S02-SOLUSDT-5m@20x-v1"]["edge_delta_usdt"] == pytest.approx(2.0)
        assert by["S04-BNBUSDT-5m@20x-v1"]["edge_delta_usdt"] == pytest.approx(-1.0)
        assert by["S04-BNBUSDT-5m@20x-v1"]["verdict"] == "WORSENED", \
            "a +JEV bot that made money but less than its control made the system worse"
        assert by["S13-XRPUSDT-5m@20x-v1"]["verdict"] == "UNCHANGED"
        assert (s["improved"], s["worsened"], s["unchanged"]) == (1, 1, 1)

    def test_jev_counts_and_costs(self, tmp_path):
        from app.core.storage import Storage
        s = seed_experiment(Storage(tmp_path / "j.db"))
        assert s["candidate_signals"] == 9 and s["accepted"] == 3 and s["reduced"] == 3 and s["skipped"] == 3
        assert s["skipped_losing"] == 3 and s["skipped_winning"] == 0
        assert s["total_cost_usd"] == pytest.approx(9 * 0.0000378)
        assert s["acceptance_rate"] == pytest.approx(6 / 9)

    def test_calibration_buckets(self):
        rows = calibration([{"take_probability": 0.95, "outcome_r": 1.0, "outcome_kind": "TAKEN"},
                            {"take_probability": 0.3, "outcome_r": -1.0, "outcome_kind": "SHADOW"}])
        top = next(r for r in rows if r["bucket"] == "0.90-1.00")
        low = next(r for r in rows if r["bucket"] == "0.00-0.50")
        assert top["mean_r"] == 1.0 and low["mean_r"] == -1.0 and low["shadow"] == 1

    def test_sign_test(self):
        assert sign_test_p(10, 0) < 0.01
        assert sign_test_p(5, 5) == pytest.approx(1.0)
        assert sign_test_p(0, 0) is None


@pytest.fixture
def jev_client(anon):
    seed_experiment(anon.app.state.competition.storage)
    return anon


class TestJevPublicSurface:
    def test_public_api_carries_real_values(self, jev_client):
        d = jev_client.get(PUB + "/jev").json()
        assert d["ran"] and d["summary"]["pairs"] == 3 and d["summary"]["worsened"] == 1
        assert {p["verdict"] for p in d["pairs"]} == {"IMPROVED", "WORSENED", "UNCHANGED"}
        pid = pair_id("S02-SOLUSDT-5m@20x-v1")
        pr = jev_client.get(PUB + "/jev/pairs/" + pid).json()
        assert pr["pair"]["edge_delta_usdt"] == pytest.approx(2.0) and len(pr["decisions"]) == 3
        assert pr["decisions"][0]["state"]["market"]["rsi_14"] == 55.0

    def test_report_shows_the_experiment_without_javascript(self, jev_client):
        html = re.sub(r'<script type="application/json".*?</script>', "", jev_client.get(REPORT).text, flags=re.S)
        for s in ("CONTROL vs +JEV", "JEV EDGE DELTA", "JEV API HEALTH", "DOES JEV ADD A MEASURABLE AFTER-COST EDGE?",
                  "S04-BNBUSDT-5m@20x-v1", "WORSENED", "IMPROVED", "typesafe/jev-1.13"):
            assert s in html, s
        txt = jev_client.get(REPORT + ".txt").text
        assert "CONTROL vs +JEV" in txt and "JEV EDGE DELTA" in txt

    def test_pair_page_renders(self, jev_client):
        pid = pair_id("S04-BNBUSDT-5m@20x-v1")
        r = jev_client.get(f"{REPORT}/jev/pair/{pid}")
        assert r.status_code == 200 and "JEV EDGE DELTA" in r.text and "Decision inspector" in r.text
        assert jev_client.get(f"{REPORT}/jev/pair/pair:NOPE").status_code == 404

    def test_no_public_output_contains_anything_key_like(self, jev_client):
        pid = pair_id("S02-SOLUSDT-5m@20x-v1")
        paths = [PUB + "/jev", PUB + "/jev/pairs/" + pid, PUB + "/jev/health", REPORT, REPORT + ".txt",
                 f"{REPORT}/jev/pair/{pid}", PUB, PUB + "/arena"]
        pattern = re.compile(r"sk-or-v1-[A-Za-z0-9]{8,}|Bearer\s+\S{8,}|OPENROUTER_API_KEY=")
        for path in paths:
            body = jev_client.get(path).text
            assert OR_KEY not in body and SECRET_KEY not in body, path
            assert not pattern.search(body), path


class TestVerdict:
    """Beating a losing control is not an edge: never trading beats it too."""

    def base(self, **over):
        s = {"improved": 27, "worsened": 2, "sign_test_p": 1.6e-6, "mean_edge_delta_usdt": 2.89,
             "null_mean_edge_delta_usdt": 2.92, "total_jev_net": -0.73, "pairs_jev_profitable": 2,
             "pairs_jev_losing": 25, "accepted": 1, "reduced": 29,
             "auc": {"auc": 0.512, "low": 0.498, "high": 0.526}}
        s.update(over)
        return s

    def test_refusing_to_trade_losing_strategies_is_not_an_edge(self):
        from app.competition.jev_experiment import verdict
        v, detail = verdict(self.base())
        assert v == "NO" and "NOT TRADING" in detail

    def test_an_edge_needs_both_baselines_and_discrimination(self):
        from app.competition.jev_experiment import verdict
        good = self.base(total_jev_net=12.0, pairs_jev_profitable=20, pairs_jev_losing=5,
                         auc={"auc": 0.61, "low": 0.57, "high": 0.65})
        assert verdict(good)[0] == "YES"
        assert verdict({**good, "auc": {"auc": 0.51, "low": 0.49, "high": 0.53}})[0] == "NOT DEMONSTRATED"

    def test_auc_is_half_for_an_uninformative_score(self):
        from app.competition.jev_experiment import auc_with_ci
        flat = [(0.3, i % 2) for i in range(200)]
        assert auc_with_ci(flat)["auc"] == pytest.approx(0.5)
        perfect = [(i / 100, 1 if i >= 50 else 0) for i in range(100)]
        assert auc_with_ci(perfect)["auc"] == pytest.approx(1.0)
