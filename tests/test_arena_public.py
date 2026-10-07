"""The specialist arena on every read surface: public API, private API, static report, bot page.

Same contract as the rest of the public surface: anonymous readers see real values without
JavaScript, and nothing they can reach mutates anything or exposes a secret.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.test_public_api import PUB, SECRET_KEY, SECRET_SECRET, anon  # noqa: F401  (fixture reuse)

REPORT = "/public/competition/report"
KEY_A = "S26-SOLUSDT-15m@20x-v1"
KEY_B = "S02-ETHUSDT-5m@20x-v1"


def visible(html: str) -> str:
    return re.sub(r'<script type="application/json".*?</script>', "", html, flags=re.S)


def _bot(key, sid, sym, tf, rank, state, net, trades, reasons):
    return {"key": key, "strategy_id": sid, "name": sid + " name", "symbol": sym,
            "coin": sym[:-4], "timeframe": tf, "max_leverage": 20, "profile": "AGGRESSIVE",
            "params_version": "v1", "version": key + ":abcd1234", "state": state, "rank": rank,
            "reasons": reasons, "signals": 500, "entry_states": {"NORMAL": trades},
            "entry_quality": {"ORDINARY": trades}, "avg_risk_pct": 0.0098, "max_risk_pct": 0.01,
            "avg_position_leverage": 2.4, "max_position_leverage": 3, "partial_fills": 0,
            "rejects": {"below_min_notional": 7, "fee_gt_r": 3}, "halted": False,
            "fee_source": "schedule", "elapsed_s": 1.0,
            "gates": [{"name": "profit_factor", "ok": state == "ADVANCE", "actual": 1.3,
                       "threshold": 1.1, "comparison": ">="}],
            "metrics": {"trades": trades, "net_profit": net, "gross_pnl": net + 1.5,
                        "fees_paid": 1.2, "slippage_cost": 0.25, "funding_paid": -0.05,
                        "net_return_pct": net / 20.0, "expectancy_r": 0.12 if net > 0 else -0.3,
                        "profit_factor": 1.3 if net > 0 else 0.6, "max_drawdown_pct": 0.12,
                        "liquidation_count": 0, "avg_effective_leverage": 1.4,
                        "ending_equity": 20.0 + net, "starting_equity": 20.0},
            "equity": [[1_780_000_000_000, 20.0], [1_780_000_060_000, 20.0 + net]],
            "trades_ledger": [{"side": "long", "qty": 0.3, "entry": 150.0, "exit": 153.0,
                               "entry_ts": 1_780_000_000_000, "exit_ts": 1_780_000_060_000,
                               "pnl": 0.9, "fees": 0.05, "net": 0.85, "r": 1.7,
                               "exit_kind": "tp"}]}


def _seed_arena(storage) -> None:
    storage.start_arena_run({
        "run_id": "arena1", "created_ts": 1_780_000_000_000, "status": "running",
        "label": "Specialist Arena - test", "config_fingerprint": "cfgarena",
        "first_month": "2026-07", "last_month": "2026-08", "symbols": ["ETHUSDT", "SOLUSDT"],
        "timeframes": ["5m", "15m"], "active_bots": 2, "not_entered": 3, "advanced": 0,
        "min_active_bots": 10,
        "config": {"arena_version": "arena-2", "months": ["2026-07", "2026-08"],
                   "min_active_bots": 10, "max_bots": 30, "starting_balance": 20.0,
                   "profile": "AGGRESSIVE", "leverage_ceiling": 20, "leverage_policy": "needed",
                   "fee_source": "schedule", "fees": {"maker_rate": 0.0002, "taker_rate": 0.0005},
                   "min_profit_factor": 1.1, "max_drawdown_pct": 0.35, "max_liquidations": 0,
                   "min_trades": {"5m": 40, "15m": 20, "30m": 12},
                   "min_notional_safety_multiplier": 1.0, "max_fee_share_of_r": 0.25,
                   "risk_profile": {"ordinary_risk_pct": 0.01, "strong_risk_pct": 0.015,
                                    "exceptional_risk_pct": 0.02, "max_risk_pct": 0.02,
                                    "attack_multiplier": 1.5, "defensive_multiplier": 0.5},
                   "api_key": SECRET_KEY},                       # must never be published
        "summary": {},
        "preflight": {
            "symbols": [
                {"symbol": "BTCUSDT", "min_notional": 50.0, "min_qty": 0.001, "reference_price": 64230.6,
                 "exchange_min": 64.23, "floor": 64.23, "ceiling": 50.0, "min_stop_pct": 0.004,
                 "max_stop_pct": 0.0031, "min_risk_pct_needed": 0.0128, "tradeable": False,
                 "reason": "smallest legal order 64.23 USDT (minQty) > fee-gate cap 50.00 USDT"},
                {"symbol": "ETHUSDT", "min_notional": 20.0, "min_qty": 0.001, "reference_price": 1884.0,
                 "exchange_min": 20.72, "floor": 20.72, "ceiling": 50.0, "min_stop_pct": 0.004,
                 "max_stop_pct": 0.0097, "min_risk_pct_needed": 0.0041, "tradeable": True,
                 "reason": ""}],
            "strategies": [{"strategy_id": "S08", "name": "ORB", "native_timeframe": "1m",
                            "supported_timeframes": [], "declared_timeframes": ["1m"],
                            "context_timeframes": []}],
            "not_entered": [
                {"key": "S08-SOLUSDT-1m@20x-v1", "reason": "native signal timeframe is 1m"},
                {"key": "S08-ETHUSDT-1m@20x-v1", "reason": "native signal timeframe is 1m"},
                {"key": "S02-BTCUSDT-5m@20x-v1", "reason": "unreachable order size: minQty"}],
            "eligible_not_selected": ["S02-SOLUSDT-5m@20x-v1"]}})
    storage.save_arena_bot("arena1", _bot(KEY_A, "S26", "SOLUSDT", "15m", 1, "FAILED", -1.25, 51,
                                          ["net_profit_after_costs -1.25 > 0 failed"]))
    storage.save_arena_bot("arena1", _bot(KEY_B, "S02", "ETHUSDT", "5m", 2, "INSUFFICIENT_SAMPLE",
                                          -2.5, 34, ["34 trades < 40 required for 5m"]))
    storage.update_arena_run("arena1", status="complete", finished_ts=1_780_000_900_000,
                             summary_json={"active_bots": 2, "min_active_bots": 10,
                                           "not_entered": 3, "eligible_not_selected": 1,
                                           "coins": ["ETH", "SOL"], "timeframes": ["5m", "15m"],
                                           "strategies": ["S02", "S26"], "advanced": 0,
                                           "advanced_keys": [], "bots_that_traded": 2,
                                           "profitable_after_costs": 0, "liquidated": 0,
                                           "total_trades": 85, "total_fees": 2.4,
                                           "total_slippage": 0.5, "total_funding": -0.1,
                                           "total_gross_pnl": -0.75, "total_net_pnl": -3.75,
                                           "best_bot": KEY_A, "worst_bot": KEY_B})


@pytest.fixture
def arena_client(anon):
    anon.app.state.competition.storage and _seed_arena(anon.app.state.competition.storage)
    return anon


class TestArenaPublicApi:
    def test_arena_is_readable_anonymously(self, arena_client):
        r = arena_client.get(PUB + "/arena")
        assert r.status_code == 200
        d = r.json()
        assert d["ran"] and d["read_only"]
        assert d["run"]["run_id"] == "arena1" and d["summary"]["active_bots"] == 2
        assert [b["key"] for b in d["bots"]] == [KEY_A, KEY_B]
        assert d["matrix"]["strategies"] == ["S02", "S26"]
        assert d["matrix"]["cells"]["S26"]["SOLUSDT"][0]["trades"] == 51
        assert d["not_entered"][0]["count"] == 2
        assert d["filters"]["timeframes"] == ["5m", "15m"]
        assert "ADVANCE" in d["pipeline"] and "QUALIFIED" in d["pipeline"]

    def test_the_config_is_allow_listed(self, arena_client):
        body = arena_client.get(PUB + "/arena").text
        assert SECRET_KEY not in body and SECRET_SECRET not in body
        assert "api_key" not in body

    def test_bot_detail_carries_the_ledger(self, arena_client):
        d = arena_client.get(PUB + "/arena/bots/" + KEY_A).json()["bot"]
        assert d["key"] == KEY_A and d["trades"][0]["net"] == 0.85
        assert d["metrics"]["fees_paid"] == 1.2 and len(d["equity"]) == 2

    def test_unknown_bot_is_a_404(self, arena_client):
        assert arena_client.get(PUB + "/arena/bots/NOPE").status_code == 404

    def test_no_arena_yet_is_explicit(self, anon):
        d = anon.get(PUB + "/arena").json()
        assert d["ran"] is False

    def test_private_arena_still_requires_auth(self, arena_client):
        assert arena_client.get("/api/competition/arena").status_code == 401


class TestArenaInTheStaticReport:
    def test_active_count_and_minimum_are_in_the_html(self, arena_client):
        body = visible(arena_client.get(REPORT).text)
        assert "ACTIVE BOTS: 2" in body and "MINIMUM REQUIRED: 10" in body
        assert "INSUFFICIENT_COMPETITORS" in body

    def test_bots_symbols_and_reasons_are_in_the_html(self, arena_client):
        body = visible(arena_client.get(REPORT).text)
        for s in (KEY_A, KEY_B, "arena1", "ETH, SOL", "5m, 15m",
                  "native signal timeframe is 1m", "unreachable order size: minQty",
                  "smallest legal order 64.23 USDT", "ADVANCED TO MULTI-YEAR VALIDATION: NONE",
                  "-1.25", "51", "S02-SOLUSDT-5m@20x-v1"):
            assert s in body, s

    def test_arena_bot_pages_are_linked_and_render(self, arena_client):
        body = visible(arena_client.get(REPORT).text)
        assert f"href='{REPORT}/arena/bot/{KEY_A}'" in body
        page = arena_client.get(f"{REPORT}/arena/bot/{KEY_A}")
        assert page.status_code == 200
        html = page.text
        assert KEY_A in html and "profit_factor" in html and "FAIL" in html
        assert "below_min_notional" in html and "0.85" in html
        assert arena_client.get(f"{REPORT}/arena/bot/NOPE").status_code == 404

    def test_text_report_has_the_arena(self, arena_client):
        body = arena_client.get(REPORT + ".txt").text
        assert "SPECIALIST BOT ARENA - DISCOVERY" in body
        assert "ACTIVE BOTS      2   (MINIMUM REQUIRED 10)" in body
        assert KEY_A in body and "native signal timeframe is 1m" in body

    def test_embedded_json_carries_the_arena(self, arena_client):
        html = arena_client.get(REPORT).text
        blob = re.search(r'<script type="application/json" id="inspection-data">(.*?)</script>',
                         html, flags=re.S).group(1)
        data = json.loads(blob)
        assert data["arena"]["run"]["run_id"] == "arena1"
        assert len(data["arena"]["bots"]) == 2

    def test_report_leaks_nothing(self, arena_client):
        for path in (REPORT, REPORT + ".txt", f"{REPORT}/arena/bot/{KEY_A}"):
            body = arena_client.get(path).text
            assert SECRET_KEY not in body and SECRET_SECRET not in body
