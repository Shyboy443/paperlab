"""Retired programs and families (operator decision 2026-10-08, after the two-year backtest): retired programs never
start, and retired bots are out of the roster, the V6 feed and Telegram -- while the two proven V6 families stay."""
from __future__ import annotations

from app.core.programs import RETIRED_PROGRAMS, bot_retired, program_retired
from app.live.v6_service import V6ForwardService


def test_only_v6_2_and_v6_6_stay_in_the_arena():
    assert not program_retired("v6") and all(program_retired(p) for p in ("v7", "V8", "v9", "v11", "v12", "v13", "v14"))
    for key in ("V6.6-XRP-1H", "V6.6-XRP-1H+JEV", "V6.2-DOGE-1H"):
        assert not bot_retired("v6", key)
    for key in ("V6.1-ARB-1H", "V6.1-ARB-4H", "V6.3-ENA-1H+JEV", "V6.4-XRP-1H", "V6.5-DOGE-1H"):
        assert bot_retired("v6", key)
    assert bot_retired("v8", "V8.3-ETH-5M") and not bot_retired("video", "BTC-4H-BREAKOUT")


def test_a_retired_program_never_starts_whatever_its_switch(tmp_path):
    for program in sorted(RETIRED_PROGRAMS):
        svc = V6ForwardService(str(tmp_path / "x.db"), env={program.upper() + "_FORWARD_ENABLED": "true"},
                               program=program.upper())
        svc.start()
        assert not svc.enabled and svc.health()["status"] == "RETIRED", program
    assert V6ForwardService(str(tmp_path / "x.db"), env={"V6_FORWARD_ENABLED": "true"}).enabled


def test_the_v6_feed_drops_retired_families_and_recounts():
    from app.core.api_public import _drop_retired_v6
    rows = [{"key": k, "role": "JEV" if k.endswith("+JEV") else "CONTROL", "net_now": n, "trades_24h": 1,
             "equity_now": 20 + n, "start_equity": 20}
            for k, n in (("V6.6-XRP-1H", 1.0), ("V6.6-XRP-1H+JEV", 1.0), ("V6.1-ARB-1H", -3.0), ("V6.5-ENA-1H", 0.0))]
    out = _drop_retired_v6({"leaderboard": rows, "hero": {"bots": 4, "net_pnl": -1.0},
                            "positions": [{"bot_key": "V6.1-ARB-1H"}], "activity": [{"bot_key": "V6.4-XRP-1H"}],
                            "pairs": [{"control_key": "V6.3-ENA-1H"}, {"control_key": "V6.6-XRP-1H"}],
                            "families": [{"family": "V6.1 SWING"}, {"family": "V6.6 HOURLY"}]})
    assert [r["key"] for r in out["leaderboard"]] == ["V6.6-XRP-1H", "V6.6-XRP-1H+JEV"]
    assert out["positions"] == [] and out["activity"] == [] and [p["control_key"] for p in out["pairs"]] == ["V6.6-XRP-1H"]
    assert [f["family"] for f in out["families"]] == ["V6.6 HOURLY"]
    assert out["hero"]["bots"] == 2 and out["hero"]["controls"] == 1 and out["hero"]["net_pnl"] == 2.0


def test_telegram_ignores_retired_bots():
    from app.live.telegram import TelegramNotifier
    tg = TelegramNotifier.__new__(TelegramNotifier)
    import queue
    tg.q, tg.rules, tg.programs, tg.roles, tg.stats = queue.Queue(), None, None, None, {"dropped": 0}
    tg.on_event("v6", {"type": "open", "bot_key": "V6.1-ARB-1H", "role": "CONTROL"})
    tg.on_event("v8", {"type": "open", "bot_key": "V8.3-ETH-5M", "role": "CONTROL"})
    assert tg.q.empty()
    tg.on_event("v6", {"type": "open", "bot_key": "V6.6-XRP-1H", "role": "CONTROL"})
    assert tg.q.qsize() == 1
