"""Scribd round-2 simulators (app/ytstrats/swing.py): mechanics and the random-walk check."""
import numpy as np

from app.ytstrats import scalp, swing

DAY = 86_400_000


def _walk(n, sub, sigma, p0, seed, t0, step):
    rng = np.random.default_rng(seed)
    x = p0 + np.cumsum(rng.normal(0, sigma * p0 / np.sqrt(sub), n * sub)).reshape(n, sub)   # a price martingale
    o = np.r_[p0, x[:-1, -1]]
    p = np.column_stack([o, x])
    return (t0 + np.arange(n) * step).astype(float), p[:, 0], p.max(1), p.min(1), p[:, -1]


def test_turtle_adds_units_and_raises_stops():
    # flat for 60 days, then a steady climb: System 2 enters at the 55-day high and pyramids to 4 units
    n = 120
    c = np.r_[np.full(60, 100.0), 100 + np.arange(1, n - 59) * 1.0]
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5
    t = 1420070400000 + np.arange(n) * DAY
    tr = swing.turtle(swing.D(t.astype(float), o, h, l, c), 2, 0.0, 0, 10**14)
    assert tr and tr[0]["dir"] == 1 and tr[0]["units"] == 4 and tr[0]["ret_pct"] > 0


def test_london_breakout_first_fill_cancels_the_other():
    # one London day (winter, London = UTC): quiet Asia, then a rally through the Asian high
    t0 = 1736121600000                                   # 2025-01-06 00:00 UTC
    t = t0 + np.arange(16 * 60) * 60_000
    c = np.full(len(t), 1.2500)
    c[8 * 60:] = 1.2500 + np.arange(len(t) - 8 * 60) * 0.00002
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.00002, np.minimum(o, c) - 0.00002
    m = swing.M(t.astype(float), o, h, l, c, tf=1)
    tr = swing.london_bo(m, 0.0001, 0.0, "GBPUSD", 0, 10**14)
    assert len(tr) == 1 and tr[0]["dir"] == 1 and tr[0]["result"] == "target"
    assert abs(tr[0]["entry"] - (1.25002 + 0.0005)) < 1e-9


def test_random_walk_daily_rules_are_unbiased():
    tr = {k: [] for k in ("turtle1", "rsi2", "holy")}
    for seed in range(8):
        t, o, h, l, c = _walk(1500, 200, 0.004, 100.0, seed, 1420070400000, DAY)
        b = swing.D(t, o, h, l, c)
        tr["turtle1"] += swing.turtle(b, 1, 0.0, 0, 10**14)
        tr["rsi2"] += scalp.replay(t, o, h, l, c, swing.rsi2_15_85(b, 0.01), 0, 10**14, 0.0)
        tr["holy"] += scalp.replay(t, o, h, l, c, swing.holy_grail(b, 0.01), 0, 10**14, 0.0)
    for k, v in tr.items():
        r = np.array([x["ret_pct"] for x in v])
        assert len(r) > 30, k
        assert abs(r.mean() / (r.std() / np.sqrt(len(r)))) < 3.0, k


def test_round3_ibs_qqq_and_nr7_mechanics():
    from app.ytstrats import round3
    t = (1420070400000 + np.arange(12) * DAY).astype(float)
    # IBS rule: day 2 closes at its low (IBS 0) -> buy; day 4 closes above day 3's high -> exit there
    o = np.array([100, 100, 100, 99, 99.5, 101, 101, 101, 101, 101, 101, 101.0])
    h = np.array([101, 101, 101, 100, 100.0, 102, 102, 102, 102, 102, 102, 102.0])
    l = np.array([99, 99, 98, 98.5, 99, 100, 100, 100, 100, 100, 100, 100.0])
    c = np.array([100, 100, 98, 99, 99.6, 101.5, 101, 101, 101, 101, 101, 101.0])
    tr = round3.ibs_qqq(swing.D(t, o, h, l, c), 0.0, 0.0, 0, 10**14)
    assert tr[0]["entry"] == 98 and tr[0]["t"] == t[2] and tr[0]["exit_t"] == t[5]
    flags = round3.nr7(h, l)
    assert flags[:6].sum() == 0 and flags.dtype == bool


def test_random_walk_round3_daily_rules_are_unbiased():
    from app.ytstrats import round3
    res = {k: [] for k in ("nr7_etf", "ibs_qqq")}
    for seed in range(6):
        t, o, h, l, c = _walk(1500, 1000, 0.004, 100.0, seed, 1420070400000, DAY)
        b = swing.D(t, o, h, l, c)
        res["nr7_etf"] += round3.nr7_etf(b, 0.0, 0.0, 0, 10**14)
        res["ibs_qqq"] += round3.ibs_qqq(b, 0.0, 0.0, 0, 10**14)
    for k, v in res.items():
        r = np.array([x["ret_pct"] for x in v])
        assert len(r) > 30 and abs(r.mean() / (r.std() / np.sqrt(len(r)))) < 3.0, k


def test_scalp2_vwap_resets_at_each_anchor():
    from app.ytstrats import scalp2
    n = 6
    v = np.array([1.0, 3.0, 2.0, 1.0, 1.0, 4.0])
    px = np.array([10.0, 12.0, 11.0, 20.0, 22.0, 21.0])
    b = scalp2.Bars(np.arange(n) * 60_000.0, px, px, px, px, v, np.arange(n), np.arange(n), 1)
    vw, sd = scalp2.vwap_bands(b, np.array([0, 0, 0, 1, 1, 1]))
    assert abs(vw[2] - (10 + 36 + 22) / 6) < 1e-12 and abs(vw[3] - 20.0) < 1e-12 and sd[3] == 0.0
