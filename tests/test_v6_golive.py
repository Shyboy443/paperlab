"""GO LIVE for V6.2 / V6.6: which bots are eligible (a profitable two-year backtest, CONTROL, not halted), the mirror
treats exactly those as QUALIFIED, and the public row carries the backtest numbers. No network, no money."""
from __future__ import annotations

import asyncio

import pytest

from app.live import v6_golive as g
from app.live.mirror import MirrorError, MirrorService
from app.live.providers import Providers
from app.core.storage import Storage
from tests.test_mirror import KEY, SECRET, FakeExchange


def row(key, role="CONTROL", risk="OK"):
    sid = key.split("-")[0]
    return {"key": key, "strategy_id": sid, "role": role, "risk_state": risk, "coin": key.split("-")[1],
            "symbol": key.split("-")[1] + "USDT", "open_positions": []}


def test_summary_covers_the_v6_bots():
    for key in ("V6.6-XRP-1H", "V6.6-ARB-1H", "V6.2-ARB-1H", "V6.2-XRP-1H"):
        assert g.backtest(key) is not None


@pytest.mark.parametrize("r,ok,why", [
    (row("V6.6-XRP-1H"), True, "two-year backtest +"),
    (row("V6.2-ARB-1H"), True, "two-year backtest +"),
    (row("V6.2-XRP-1H"), False, "lost money"),                      # its backtest lost: no button
    (row("V6.1-XRP-4H"), False, "only V6.2 and V6.6"),               # profitable, but not a chosen strategy
    (row("V6.6-XRP-1H+JEV", role="JEV"), False, "CONTROL"),
    (row("V6.6-XRP-1H", risk="HALTED"), False, "HALTED"),
])
def test_eligibility(r, ok, why):
    got, reason = g.eligible(r)
    assert got is ok and why in reason


def test_annotate_and_mirror_view():
    r = g.annotate(row("V6.6-ARB-1H"))
    assert r["golive"]["eligible"] and r["golive"]["backtest"]["return_pct"] > 0
    assert g.mirror_view(row("V6.6-ARB-1H"))["program_status"] == "QUALIFIED"
    assert g.mirror_view(row("V6.2-XRP-1H"))["program_status"] == "ACTIVE"
    assert g.mirror_view(None) is None


def test_the_mirror_arms_an_eligible_v6_bot_and_refuses_the_rest(tmp_path):
    rows = {k: row(k) for k in ("V6.6-XRP-1H", "V6.2-XRP-1H")}
    svc = MirrorService(Storage(str(tmp_path / "m.db")),
                        Providers({"BYBIT_TESTNET_API_KEY": KEY, "BYBIT_TESTNET_API_SECRET": SECRET},
                                  client_factory=lambda ex, net: FakeExchange(price=2.5)),
                        lambda prog, key: g.mirror_view(rows.get(key)) if prog == "v6" else None)
    req = {"program": "v6", "exchange": "bybit", "network": "testnet", "amount_usdt": 50, "risk_pct": 0.01,
           "max_daily_loss": 5, "max_total_loss": 12}
    m = asyncio.run(svc.arm({**req, "bot_key": "V6.6-XRP-1H", "confirm": "GO LIVE V6.6-XRP-1H"}))
    assert m["status"] == "ARMED" and m["program"] == "v6" and m["symbol"] == "XRPUSDT"
    with pytest.raises(MirrorError, match="only QUALIFIED"):
        asyncio.run(svc.arm({**req, "bot_key": "V6.2-XRP-1H", "confirm": "GO LIVE V6.2-XRP-1H"}))


def test_mirror_row_reads_the_v6_payload_from_a_real_storage(tmp_path):
    """Regression: GO LIVE on a V6 bot returned HTTP 500 (the bot view asked V6ForwardService for a .storage it does not
    have). The row now comes from the main storage, exactly as the public V6 page builds it."""
    st = Storage(str(tmp_path / "paperlab.db"))
    assert g.mirror_row(st, None, "V6.6-XRP-1H") is None          # no V6 experiment in this database: no row, no crash
