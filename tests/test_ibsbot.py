"""The IBS bot's frozen rule must reproduce round 3's backtest exactly."""
import numpy as np

from app.ibsbot import rule
from app.ytstrats import round3, swing

DAY = 86_400_000


def test_decide_matches_the_backtest_rule():
    rng = np.random.default_rng(3)
    n = 600
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 1, n)
    l = np.minimum(o, c) - rng.uniform(0, 1, n)
    t = (1420070400000 + np.arange(n) * DAY).astype(float)
    bt = round3.ibs_qqq(swing.D(t, o, h, l, c), 0.0, 0.0, 0, 10**14)
    live, holding, entry_t = [], False, None
    for i in range(0, n):
        s = rule.Session(h[i], l[i], c[i], h[i - 1] if i else float("inf"))
        action, _ = rule.decide(holding, s)
        if action == "exit":
            live.append((entry_t, t[i]))
            holding = False
            action, _ = rule.decide(False, s)            # the same close may start a new trade (as in the backtest)
        if action == "enter":
            holding, entry_t = True, t[i]
    assert [(x["t"], x["exit_t"]) for x in bt if x["result"] != "end"] == live


def test_flat_ibs_threshold_and_degenerate_bar():
    assert rule.decide(False, rule.Session(10, 9, 9.05, 11))[0] == "enter"
    assert rule.decide(False, rule.Session(10, 9, 9.2, 11))[0] == "wait"
    assert rule.decide(False, rule.Session(10, 10, 10, 11))[0] == "wait"
    assert rule.decide(True, rule.Session(10, 9, 10.5, 10.4))[0] == "exit"
    assert rule.decide(True, rule.Session(10, 9, 10.0, 10.4))[0] == "hold"
