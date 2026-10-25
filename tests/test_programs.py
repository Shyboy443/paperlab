"""Retired programs and families: retired programs never start, and retired bots are out of the roster, the V6 feed and
Telegram. Since 2026-10-14 every bot program is retired (operator: "remove the bots, keep it empty for now")."""
from __future__ import annotations

from app.core.programs import RETIRED_PROGRAMS, bot_retired, program_retired
from app.live.v6_service import V6ForwardService


def test_every_bot_program_is_retired():
    programs = ("v6", "v7", "v8", "v9", "v11", "v12", "v13", "v14", "v16", "v17")
    assert all(program_retired(p) for p in programs) and set(programs) <= RETIRED_PROGRAMS
    for key in ("V6.6-XRP-1H", "V6.6-XRP-1H+JEV", "V6.2-DOGE-1H", "V6.1-ARB-1H"):
        assert bot_retired("v6", key)
    assert bot_retired("v12", "V12.1-ETH-15M") and bot_retired("v8", "V8.3-ETH-5M") and bot_retired("v11", "V11.3-SCAN")
    assert not bot_retired("video", "BTC-4H-BREAKOUT")                  # not a bot program


def test_a_retired_program_never_starts_whatever_its_switch(tmp_path):
    for program in sorted(RETIRED_PROGRAMS):
        svc = V6ForwardService(str(tmp_path / "x.db"), env={program.upper() + "_FORWARD_ENABLED": "true"},
                               program=program.upper())
        svc.start()
        assert not svc.enabled and svc.health()["status"] == "RETIRED", program
    assert not V6ForwardService(str(tmp_path / "x.db"), env={"V6_FORWARD_ENABLED": "true"}).enabled


def test_the_v6_feed_drops_retired_families_and_recounts(programs_running):
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
    tg.on_event("v12", {"type": "open", "bot_key": "V12.1-ETH-15M", "role": "CONTROL"})
    assert tg.q.empty()
    tg.on_event("v6", {"type": "open", "bot_key": "V6.6-XRP-1H", "role": "CONTROL"})   # retired too since 2026-10-14
    assert tg.q.empty()
