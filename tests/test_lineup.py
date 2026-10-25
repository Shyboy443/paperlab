"""The line-up (app/core/lineup.py): only the Ensemble and the IBS bot run; removed desks are switched off safely."""
from __future__ import annotations

import base64

from app.core.lineup import Lineup


class _Svc:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1


class _Live(_Svc):
    def __init__(self, st):
        super().__init__()
        self.st, self.offs = st, []

    def state(self):
        return dict(self.st)

    def _off(self, st, reason):
        self.offs.append(reason)
        self.st = {**st, "status": "OFF"}


def test_lean_starts_only_the_kept_strategies():
    lu, a, b = Lineup("lean"), _Svc(), _Svc()
    assert lu.start("ensemble", a) and not lu.start("v6", b)
    assert (a.started, b.started) == (1, 0)
    assert lu.health() == {"mode": "lean", "started": ["ensemble"], "skipped": ["v6"]}


def test_full_starts_everything():
    lu, b = Lineup("full"), _Svc()
    assert lu.start("v6", b) and b.started == 1


def test_a_removed_desk_that_is_armed_and_flat_is_switched_off_and_not_started():
    live = _Live({"status": "ARMED", "position": None, "account_id": "a1"})
    assert Lineup("lean").retire_desk_live("zec_emarsi_live", live) is False
    assert live.offs and live.st["status"] == "OFF" and live.started == 0


def test_a_removed_desk_holding_a_position_keeps_running():
    live = _Live({"status": "ARMED", "position": {"side": 1, "qty": 0.1}, "account_id": "a1"})
    assert Lineup("lean").retire_desk_live("zec_emarsi_live", live) is True
    assert live.started == 1 and not live.offs


def test_a_removed_desk_that_is_off_is_left_alone():
    live = _Live({"status": "OFF"})
    assert Lineup("lean").retire_desk_live("macd_live", live) is False
    assert not live.offs and live.started == 0


def test_the_kept_ensemble_switch_always_starts():
    live = _Live({"status": "OFF"})
    assert Lineup("lean").retire_desk_live("ensemble_live", live) is True and live.started == 1


def test_boot_runs_only_the_line_up_and_the_kill_switch_skips_removed_desks(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERLAB_LINEUP", "lean")
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import settings_factory
    app = create_app(settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="pw-123456"))
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:pw-123456").decode(), "X-PaperLab": "1"}
    with TestClient(app) as c:
        h = app.state.lineup.health()
        assert h["mode"] == "lean"
        assert {"ensemble", "ensemble_live", "worker2", "worker2_live", "ibs_bot"} >= set(h["started"])
        assert {"desk", "divergence", "zec_emarsi_live", "macd_stx_live", "pumpfun", "stock_trend"} <= set(h["skipped"])
        assert app.state.zec_emarsi_live.loop is None
        out = c.post("/api/kill", headers=auth).json()
        assert "zec_emarsi_live" not in out and "macd_live" not in out
        assert c.get("/api/public/lineup").json()["kept"] == ["ensemble", "worker2", "ibs_bot"]
