from types import SimpleNamespace

from app.core import api_desks
from app.donchian import model as dmodel
from tests.test_donchian import bars_from


class KV:
    def __init__(self, d): self.d = d
    def get(self, k, default=None): return self.d.get(k, default)


def test_drawdown_helpers():
    assert api_desks._dd([1000, 1100, 880, 1200], 1000) == 100 * (1 - 880 / 1100)
    # two books: one dips while the other is flat -> the combined dip is half as deep
    dd = api_desks._portfolio([[(1, 1000.0), (2, 800.0), (3, 1000.0)], [(1, 1000.0), (3, 1000.0)]], 1000.0)
    assert abs(dd - 10.0) < 1e-9
    # closed-trade path: +2R then -1R at 0.5% risk
    x = api_desks._r_books({"A": [(0, 10, 2.0, 1, 100.0, 99.0), (11, 20, -1.0, 1, 100.0, 99.0)]})
    assert x["n"] == 2 and x["wins"] == 1 and abs(x["pnl"] - (1000 * 1.01 * 0.995 - 1000)) < 1e-9
    assert abs(x["dd_closed"] - 0.5) < 1e-9 and x["dd_mtm"] is None


def test_mark_to_market_drawdown_counts_open_gains_and_losses():
    # a long risking 1 point: it runs to +3R (close 103), falls back to +1R (101) and is closed there
    bars = [dict(t=t, c=c) for t, c in ((0, 101.0), (1, 103.0), (2, 101.0))]
    x = api_desks._r_books({"A": [(0, 3, 1.0, 1, 100.0, 99.0)]}, {"A": bars}, step_ms=1)
    assert x["dd_closed"] == 0                                     # closed trades never dipped
    peak, low = 1000 * (1 + 0.005 * 3), 1000 * (1 + 0.005 * 1)
    assert abs(x["dd_mtm"] - 100 * (1 - low / peak)) < 1e-9          # the giveback from the open +3R peak is real drawdown
    assert abs(x["dd_stress"] - 0.5) < 1e-9                          # the stress scenario misses it: it is not a bound
    assert x["dd_mtm"] > x["dd_stress"]


def test_overlapping_trades_are_marked_together():
    bars = [dict(t=t, c=c) for t, c in ((0, 100.0), (1, 98.0), (2, 100.0))]
    # two longs open at once, each risking 0.5% of 1,000: at close 98 both are at -2R
    x = api_desks._r_books({"A": [(0, 3, 0.0, 1, 100.0, 99.0), (0, 3, 0.0, 1, 100.0, 99.0)]}, {"A": bars}, step_ms=1)
    assert abs(x["dd_mtm"] - 2.0) < 1e-9 and x["n"] == 2
    # counted only when entered and exited inside the window
    y = api_desks._r_books({"A": [(1, 3, 2.0, 1, 1, 0), (4, 5, -1.0, 1, 1, 0), (8, 12, 1.0, 1, 1, 0)]}, start_ms=2, end_ms=9)
    assert y["n"] == 1 and abs(y["pnl"] - (-5.0)) < 1e-9


def test_live_rows_carry_drawdown_and_configuration():
    ai = SimpleNamespace(report=lambda: {"start_balance": 1000.0, "equity": 1050.0, "open": [{}], "rules": {"risk_pct": 2.0},
                                         "equity_curve": [{"ts": 1, "equity": 1000.0}, {"ts": 2, "equity": 900.0}],
                                         "closed": [{"pnl": 30, "created_ts": 5}, {"pnl": -10, "created_ts": 7}]})
    nw = SimpleNamespace(summary=lambda: {"started_ms": 9, "universe": ["A", "B"], "books": {
        "5m": {"start_equity": 1000.0, "equity": 1004.0, "closed": 2, "wins": 1, "open": 0, "equity_curve": []}}})
    live = api_desks.build_live(SimpleNamespace(desk=ai, nowick=nw))
    rows = {r["desk"]: r for r in live}
    assert rows["AI agents"]["pnl"] == 50 and rows["AI agents"]["win_rate"] == 0.5 and abs(rows["AI agents"]["max_dd_pct"] - 10) < 1e-9
    assert rows["No-Wick"]["coins"] == 2 and rows["No-Wick"]["sizing"].startswith("0.5%")


def test_backtests_use_one_common_window_and_scale_to_1000_per_coin():
    day = api_desks.DAY
    closes = [100 + 10 * ((i // 60) % 2) + (i % 60) * (0.3 if (i // 60) % 2 == 0 else -0.3) for i in range(1500)]
    bars = bars_from(closes, 0)
    W = (bars[900]["t"], bars[1400]["t"])
    h4 = dmodel.TF_MS
    data_start = W[0] - api_desks.NOWICK_WARMUP + 1                   # warm-up pushes the start to the boundary after W[0]
    trade = dict(side="long", entry=100.0, stop=99.0)
    nw_store = SimpleNamespace(get=KV({"backtest:5m": {"status": "READY", "start": data_start, "end": W[1], "per_symbol": {"BTCUSDT": {}}},
                   "backtest:5m:BTCUSDT": [dict(trade, r=-1.0, entry_t=W[0] + h4 - 10, exit_t=W[0] + h4 + 10),   # spans W0 + 4h
                                           dict(trade, r=2.0, entry_t=W[0] + 2 * h4 + 1, exit_t=W[0] + 2 * h4 + 2),
                                           dict(trade, r=-1.0, entry_t=W[0] - 2 * day, exit_t=W[0] - day)]}).get,
                               bars=lambda s, since: [])
    full_bt = dmodel.simulate(bars, (), "XRPUSDT", start_ms=bars[200]["t"])
    dc_store = SimpleNamespace(get=KV({"backtest_index": {"XRPUSDT": full_bt["summary"]},
                                       "backtest:XRPUSDT": {**full_bt, "start": bars[200]["t"], "end": bars[-1]["t"]}}).get,
                               bars=lambda s, since: [b for b in bars if b["t"] >= since], funding=lambda s, since: [])
    out = api_desks.build_backtests(SimpleNamespace(nowick=SimpleNamespace(store=nw_store),
                                                    donchian=SimpleNamespace(store=dc_store, MODEL=dmodel)))
    # start: first 4h boundary >= data start + warm-up (= W0 + 4h) is spanned by a trade, so it moves on to W0 + 8h
    W0 = W[0] + 2 * h4
    assert out["common_window"] == [W0, W[1]] and out["common_window_rule"]["start"] == W0
    common = {r["desk"] + r["book"]: r for r in out["common"]}
    nwc = common["No-Wick5m"]
    assert nwc["trades"] == 1 and abs(nwc["pnl"] - 10.0) < 1e-9 and nwc["window"] == [W0, W[1]]
    dc = common["Donchian" + dmodel.RULE_VERSION]
    assert dc["window"] == [W0, W[1]] and dc["capital"] == 1000.0 and dc["max_dd_pct"] is not None
    full = {r["desk"]: r for r in out["full"]}
    assert abs(full["Donchian"]["roi_pct"] - full_bt["summary"]["return_pct"]) < 1e-9
    assert full["Donchian"]["window"] == [bars[200]["t"], bars[-1]["t"]]


def test_one_broken_desk_does_not_hide_the_others():
    bad = SimpleNamespace(report=lambda: 1 / 0)
    assert api_desks.build_live(SimpleNamespace(desk=bad)) == [{"error": "ai_live: ZeroDivisionError"}]


def test_a_new_trade_is_not_marked_with_a_stale_price():
    # first trade closes at t=2 with the coin at 100; months later a short opens at 50 with no candle close yet
    bars = [dict(t=0, c=100.0), dict(t=1, c=100.0), dict(t=1000, c=50.5)]
    trades = [(0, 2, 0.0, 1, 100.0, 99.0), (500, 1002, 0.0, -1, 50.0, 51.0)]
    x = api_desks._r_books({"A": trades}, {"A": bars}, step_ms=1)
    assert abs(x["dd_mtm"] - 0.25) < 1e-9          # only the real -0.5R mark at t=1001 (0.5% x 0.5), not (100-50)/1 = 50R

def test_a_switched_off_desk_is_not_ranked():
    """No-Wick was switched off by the operator on 2026-10-18: no live row, and the backtest window no longer anchors on it."""
    nw = SimpleNamespace(enabled=False, summary=lambda: {"started_ms": 9, "universe": ["A"], "books": {"5m": {"equity": 1100.0}}},
                         store=SimpleNamespace(get=lambda k: None))
    assert [r for r in api_desks.build_live(SimpleNamespace(nowick=nw)) if r.get("tab") == "nowick"] == []
    out = api_desks.build_backtests(SimpleNamespace(nowick=nw))
    assert not [r for r in out.get("common", []) + out.get("full", []) if r.get("tab") == "nowick"]
