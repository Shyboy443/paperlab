"""YouTube-ranking strategies: indicator conventions, the fixed R:R replay (stop / targets / 2-stage / ties settled with
15-minute candles / costs), no look-ahead on random walks, and the read-only endpoint (no network)."""
import numpy as np
import pytest

from app.ytstrats import engine, ind as I, rules


def test_indicator_conventions():
    x = np.arange(1, 31, dtype=float)
    assert np.isnan(I.ema(x, 10)[8]) and I.ema(x, 10)[9] == pytest.approx(5.5)          # seeded with the SMA
    assert I.sma(x, 5)[-1] == pytest.approx(28.0) and np.isnan(I.sma(x, 5)[3])
    assert I.rsi(x, 14)[-1] == 100.0 and I.rsi(x[::-1], 14)[-1] == pytest.approx(0.0)
    y = np.r_[x, np.nan, x]
    assert np.isnan(I.sma(y, 3)[31]) and np.isnan(I.sma(y, 3)[32]) and I.sma(y, 3)[33] == pytest.approx(2.0)
    assert list(I.cross_up(np.array([1., 3, 1]), 2)) == [False, True, False]
    assert list(I.cross_down(np.array([1., 3, 1]), 2)) == [False, False, True]
    h, l, c = x + 1, x - 1, x
    line, d = I.supertrend(h, l, c, 5, 1.0)
    assert d[-1] == 1 and line[-1] < c[-1]                                               # a steady rise is bullish


def bars(rows):
    t = np.arange(len(rows)) * 1_800_000.0
    o, h, l, c = (np.array([r[k] for r in rows], dtype=float) for k in range(4))
    return t, o, h, l, c


def test_replay_target_stop_costs_and_two_stage():
    t, o, h, l, c = bars([(100, 100.2, 99.8, 100), (100, 102.5, 99.6, 102), (102, 102, 102, 102)])
    sig, stop = np.array([1, 0, 0]), np.array([99.0, np.nan, np.nan])
    tr = engine.replay(t, o, h, l, c, sig, stop, ((1.0, 2.0),), 0, 10**12, fee=0, slip=0)
    assert tr[0]["gross_r"] == pytest.approx(2.0) and tr[0]["result"] == "target"
    tr = engine.replay(t, o, h, l, c, sig, stop, ((1.0, 2.0),), 0, 10**12)
    assert tr[0]["cost_r"] == pytest.approx(0.0007 * (100 + 102) / 1.0) and tr[0]["net_r"] == pytest.approx(2 - tr[0]["cost_r"])
    tr = engine.replay(t, o, h, l, c, sig, stop, ((0.5, 1.0), (0.5, 3.0)), 0, 10**12, fee=0, slip=0)
    assert tr[0]["gross_r"] == pytest.approx(0.5 * 1 + 0.5 * 2) and tr[0]["result"] == "end"   # the 2nd half closes at the end
    t, o, h, l, c = bars([(100, 100, 100, 100), (100, 100.2, 99.8, 100), (99, 99, 97, 97.5), (97, 97, 97, 97)])   # gap after entry
    tr = engine.replay(t, o, h, l, c, np.array([1, 0, 0, 0]), np.array([99.5, np.nan, np.nan, np.nan]), ((1.0, 2.0),), 0, 10**12, fee=0, slip=0)
    assert tr[0]["result"] == "stop" and tr[0]["gross_r"] == pytest.approx((99 - 100) / 0.5)


def test_a_candle_touching_both_levels_is_settled_with_15m_candles():
    t, o, h, l, c = bars([(100, 100.2, 99.8, 100), (100, 102.5, 98.5, 100), (100, 100, 100, 100)])
    sig, stop = np.array([1, 0, 0]), np.array([99.0, np.nan, np.nan])
    target_first = (np.array([1_800_000.0, 2_700_000.0]), np.array([102.5, 100.0]), np.array([99.5, 98.5]))
    stop_first = (np.array([1_800_000.0, 2_700_000.0]), np.array([100.0, 102.5]), np.array([98.5, 99.5]))
    win = engine.replay(t, o, h, l, c, sig, stop, ((1.0, 2.0),), 0, 10**12, fee=0, slip=0, fine=target_first, tf_ms=1_800_000)
    loss = engine.replay(t, o, h, l, c, sig, stop, ((1.0, 2.0),), 0, 10**12, fee=0, slip=0, fine=stop_first, tf_ms=1_800_000)
    blind = engine.replay(t, o, h, l, c, sig, stop, ((1.0, 2.0),), 0, 10**12, fee=0, slip=0)
    assert win[0]["gross_r"] == pytest.approx(2.0) and loss[0]["gross_r"] == pytest.approx(-1.0) and blind[0]["gross_r"] == pytest.approx(-1.0)


def test_no_look_ahead_on_random_walks():
    zs = []
    for seed in range(4):
        rng = np.random.default_rng(seed)
        n = 6000
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
        o = np.r_[c[0], c[:-1]]
        h, l = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.002, n))), np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.002, n)))
        v, t = rng.uniform(1, 10, n), np.arange(n) * 1_800_000.0
        for st in rules.STRATEGIES:
            sig, stop = st.fn(o, h, l, c, v)
            assert len(sig) == len(stop) == n and not np.isnan(stop[sig != 0]).any()
            r = np.array([x["gross_r"] for x in engine.replay(t, o, h, l, c, sig, stop, st.targets, t[300], t[-1] + 1)])
            if len(r) > 30:
                zs.append(r.mean() / (r.std() / np.sqrt(len(r))))
    assert abs(np.mean(zs)) < 0.5                     # on average no edge where none exists


def test_every_strategy_is_registered_with_its_claim():
    assert len(rules.STRATEGIES) == len(rules.BY_KEY) == 27
    for s in rules.STRATEGIES:
        assert s.tf >= 15 and s.targets and abs(sum(x[0] for x in s.targets) - 1) < 1e-9 and s.rule and s.title


def test_public_endpoint_is_read_only(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core import api_ytstrats
    app = FastAPI()
    app.include_router(api_ytstrats.router)
    cl = TestClient(app)
    monkeypatch.setattr(api_ytstrats, "RESULTS", tmp_path / "none.json")
    assert cl.get("/api/public/yt-strategies").status_code == 503
    f = tmp_path / "r.json"
    f.write_text('{"strategies": [{"key": "a", "verdict": "FAILED", "recent_y2_trades": [1]}, {"key": "b", "verdict": "PROVEN (x)", "recent_y2_trades": []}]}')
    monkeypatch.setattr(api_ytstrats, "RESULTS", f)
    d = cl.get("/api/public/yt-strategies").json()
    assert d["proven"] == ["b"] and "recent_y2_trades" not in d["strategies"][0] and d["read_only"]
    assert cl.post("/api/public/yt-strategies").status_code == 405


def test_gold_signals_only_in_the_sessions_and_on_a_strong_breakout():
    from app.ytstrats import gold
    n = 400
    t = np.array([(6 * 60 + i) * 60_000 for i in range(n)], dtype=float)        # from 06:00 UTC
    c = np.full(n, 2000.0)
    c[:120] = 2000 - 0.01 * np.arange(120)                                        # a slow fall, then a sharp rise at 08:00
    c[120:] = c[119] + 0.4 * np.arange(1, n - 119)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
    sig = gold.signals(t, o, h, l, c)
    hits = np.flatnonzero(sig)
    assert len(hits) and (sig[hits] == 1).all()
    minute = ((t[hits] // 60_000) + 1) % 1440
    assert ((minute > 7 * 60) & (minute <= 10 * 60)).all()                       # inside the London window only


def test_gold_replay_costs_sides_and_the_opposite_signal_exit():
    from app.ytstrats import gold
    t = np.arange(4) * 60_000.0
    o, h = np.array([2000, 2000, 2000.5, 2002.]), np.array([2000, 2000.5, 2003, 2002.])
    l, c = np.array([2000, 1999.9, 2000.4, 2002.]), np.array([2000, 2000.4, 2002.8, 2002.])
    tr = gold.replay(t, o, h, l, c, np.array([1, 0, 0, 0]), 15, 12, 0, 10**12, spread=0.05, commission=0.0225, slippage=0.02)[0]
    assert tr["entry"] == pytest.approx(2000.07) and tr["result"] == "target" and tr["exit"] == pytest.approx(2001.55)
    assert tr["net_usd"] == pytest.approx(1.48 - 0.045) and tr["net_r"] == pytest.approx((1.48 - 0.045) / 1.2)
    # an opposite signal closes at the next open (a short is closed on the ask = bid + spread)
    o2 = np.array([2000, 2000, 2000, 2000, 2000.])
    sig = np.array([-1, 0, 1, 0, 0])
    tr = gold.replay(np.arange(5) * 60_000.0, o2, o2 + 0.1, o2 - 0.1, o2, sig, 15, 12, 0, 10**12, spread=0.05, commission=0, slippage=0)[0]
    assert tr["result"] == "opposite signal" and tr["exit"] == pytest.approx(2000.05) and tr["net_usd"] == pytest.approx(-0.05)


def test_scalp_engine_path_order_stop_entry_half_and_reverse():
    from app.ytstrats import scalp
    t = np.arange(5) * 300_000.0
    # green candle (open-low-high-close): a long with stop 1 below and target 1 above -> the low comes first: stop
    o, h, l, c = (np.array(x, float) for x in ([100, 100, 100, 100, 100], [100, 101.5, 100, 100, 100], [100, 98.5, 100, 100, 100], [100, 100.5, 100, 100, 100]))
    p = scalp.Plan(np.array([1, 0, 0, 0, 0]), stop_dist=1.0, tp_dist=1.0)
    assert scalp.replay(t, o, h, l, c, p, 0, 10**12, 0.0)[0]["result"] == "stop"
    c2 = c.copy()
    c2[1] = 99.5                                                          # red candle: the high comes first: target
    assert scalp.replay(t, o, h, l, c2, p, 0, 10**12, 0.0)[0]["result"] == "target"
    # a stop entry at 100.8 inside a green candle (low first, then up through the entry, then the high)
    pe = scalp.Plan(np.array([1, 0, 0, 0, 0]), stop_dist=0.5, tp_dist=0.5, entry_px=np.array([100.8, np.nan, np.nan, np.nan, np.nan]))
    tr = scalp.replay(t, o, h, l, c, pe, 0, 10**12, 0.0)[0]
    assert tr["entry"] == pytest.approx(100.8) and tr["result"] == "target"
    # half at 1R then breakeven
    o3, h3, l3, c3 = (np.array(x, float) for x in ([100, 100, 100.5, 100, 100], [100, 101.2, 100.6, 100, 100], [100, 99.9, 99.8, 100, 100], [100, 101, 100, 100, 100]))
    ph = scalp.Plan(np.array([1, 0, 0, 0, 0]), stop_dist=1.0, half_at_r=1.0)
    tr = scalp.replay(t, o3, h3, l3, c3, ph, 0, 10**12, 0.0)[0]
    assert tr["pnl_px"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.0) and tr["result"] == "stop"
    # reverse: an opposite signal closes at the next open and opens the other side there
    pr = scalp.Plan(np.array([1, 0, -1, 0, 0]), reverse=True)
    o4 = np.array([100, 100, 101, 102, 101.0])
    trs = scalp.replay(t, o4, o4 + 0.1, o4 - 0.1, o4, pr, 0, 10**12, 0.0)
    assert [x["dir"] for x in trs] == [1, -1] and trs[0]["pnl_px"] == pytest.approx(102 - 100) and trs[1]["entry"] == pytest.approx(102)


def test_scribd_rules_have_no_edge_on_random_walks():
    from app.ytstrats import scalp, scribd
    zs = []
    rng = np.random.default_rng(11)
    n = 12000
    c = 1.1 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0003, n))), np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0003, n)))
    v = rng.uniform(1, 10, n)
    for st in scribd.STRATEGIES:
        t = (1_735_000_000_000 // (st.tf * 60000)) * (st.tf * 60000) + np.arange(n) * st.tf * 60_000.0
        b = scribd.Bars(np.column_stack([t, o, h, l, c, v]), st.tf)
        r = np.array([x["ret_pct"] for x in scalp.replay(b.t, b.o, b.h, b.l, b.c, st.fn(b, 0.0001), t[300], t[-1] + 1, 0.0)])
        if len(r) > 30:
            zs.append(r.mean() / (r.std() / np.sqrt(len(r))))
    assert abs(np.mean(zs)) < 0.8
