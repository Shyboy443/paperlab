"""One fee schedule per venue, shared by the live paper engine, replays, competitions, the arena and
Jev. A replay of a venue must charge exactly what the live engine charges on that venue."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import load_settings
from app.core.portfolio import Portfolio
from app.execution.config import (BINANCE_SPOT, BINANCE_USDM, BYBIT_LINEAR, LEGACY_PRE_2026_09_23,
                                  FeeSchedule, fee_schedule_for)
from tests.conftest import RULES

VENUES = [({"MODE": "FUTURES_TESTNET"}, BINANCE_USDM),
          ({"MODE": "FUTURES_DEMO"}, BINANCE_USDM),
          ({"MODE": "SPOT_TESTNET"}, BINANCE_SPOT),
          ({"MODE": "BYBIT_TESTNET"}, BYBIT_LINEAR),
          ({"MODE": "LIVE_OVERRIDE_I_UNDERSTAND", "EXCHANGE": "binance"}, BINANCE_USDM),
          ({"MODE": "LIVE_OVERRIDE_I_UNDERSTAND", "EXCHANGE": "bybit"}, BYBIT_LINEAR)]


def settings_for_venue(env, tmp_path, **extra):
    return load_settings({**env, "DASHBOARD_PASSWORD": "x", "DATA_DIR": str(tmp_path / "d"), **extra})


class TestOneSchedule:
    def test_published_rates(self):
        assert (BINANCE_USDM.maker_rate, BINANCE_USDM.taker_rate) == (0.0002, 0.0005)
        assert (BYBIT_LINEAR.maker_rate, BYBIT_LINEAR.taker_rate) == (0.0002, 0.00055)
        assert (BINANCE_SPOT.maker_rate, BINANCE_SPOT.taker_rate) == (0.001, 0.001)

    @pytest.mark.parametrize("env,sched", VENUES)
    def test_live_settings_use_the_venue_schedule(self, env, sched, tmp_path):
        s = settings_for_venue(env, tmp_path)
        assert (s.taker_fee, s.maker_fee) == (sched.taker_rate, sched.maker_rate)
        assert s.fee_source == sched.source

    @pytest.mark.parametrize("env,sched", VENUES)
    def test_replay_taker_fee_equals_live_taker_fee(self, env, sched, tmp_path):
        """The regression this file exists for: same venue configuration, same charge."""
        from app.backtest.replay import ReplayEngine
        s = settings_for_venue(env, tmp_path)
        live = Portfolio(s, RULES, lambda: 0)
        replay = ReplayEngine(s, ["BTCUSDT"], rules=RULES)
        for maker in (False, True):
            assert replay.portfolio.fee_for(1000.0, maker=maker) == pytest.approx(live.fee_for(1000.0, maker=maker))
        assert replay.risk.settings.taker_fee == live.settings.taker_fee == sched.taker_rate

    def test_an_explicit_env_override_wins_and_is_labelled(self, tmp_path):
        s = settings_for_venue({"MODE": "FUTURES_TESTNET"}, tmp_path, TAKER_FEE="0.0003")
        assert s.taker_fee == 0.0003 and s.fee_source == "env override"

    def test_the_arena_and_competition_default_to_binance_usdm(self):
        from app.competition.arena import ArenaConfig
        from app.competition.config import FeeSchedule as CompFees
        assert ArenaConfig().fees == BINANCE_USDM
        assert (CompFees().maker_rate, CompFees().taker_rate) == (0.0002, 0.0005)

    def test_the_legacy_switch_reproduces_old_runs_only_on_request(self, tmp_path):
        from app.backtest.replay import ReplayEngine
        s = settings_for_venue({"MODE": "FUTURES_TESTNET"}, tmp_path)
        assert ReplayEngine(s, ["BTCUSDT"], rules=RULES, fee_source="legacy").portfolio.fee_for(1000.0) \
            == pytest.approx(1000 * LEGACY_PRE_2026_09_23.taker_rate)
        assert ReplayEngine(s, ["BTCUSDT"], rules=RULES).portfolio.fee_for(1000.0) == pytest.approx(0.5)

    def test_fee_schedule_for(self):
        assert fee_schedule_for("binance", "futures") is BINANCE_USDM
        assert fee_schedule_for("binance", "spot") is BINANCE_SPOT
        assert fee_schedule_for("bybit", "futures") is BYBIT_LINEAR
        assert FeeSchedule().taker_rate == BINANCE_USDM.taker_rate

    def test_no_module_carries_its_own_fee_constant(self):
        """Fee literals live in app/execution/config.py and nowhere else in app/."""
        root = Path(__file__).resolve().parents[1] / "app"
        pat = re.compile(r"(taker|maker)_(fee|rate)\w*\s*[:=]\s*(float\s*=\s*)?0\.0*[1-9]")
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel in ("execution/config.py", "config.py"):
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        assert offenders == []


from tests.test_public_api import anon  # noqa: E402,F401  (fixture reuse)


class TestFeeModelIsPublished:
    def test_diagnostics_state_the_paper_engine_fees(self, anon):
        d = anon.get("/api/public/competition").json()["diagnostics"]["paper_engine_fees"]
        assert (d["source"], d["taker"], d["maker"]) == ("binance_usdm", 0.0005, 0.0002)

    def test_report_labels_legacy_runs(self, anon):
        html = anon.get("/public/competition/report").text
        assert "Fee model" in html and "legacy: charged 0.040% taker" in html
        assert "FEE MODEL" in anon.get("/public/competition/report.txt").text

    def test_the_boot_journals_the_schedule(self, anon):
        st = anon.app.state.competition.storage
        assert st.get_meta("fee_schedule").startswith("binance_usdm maker 0.0200% taker 0.0500%")
