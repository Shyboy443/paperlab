from app.macd import model
from app.macd.service import MacdService
from tests.test_donchian import FakeMarket, bars_from

H4 = model.TF_MS


def test_ema_matches_pine_seed_and_recursion():
    e = model.ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0
    assert abs(e[3] - (0.5 * 4 + 0.5 * 2.0)) < 1e-12 and abs(e[4] - (0.5 * 5 + 0.5 * e[3])) < 1e-12


def test_channels_warm_up_and_hist():
    bars = bars_from([100 + (i % 7) for i in range(60)])
    ch = model.channels(bars)
    assert ch[24]["macd"] is None and ch[25]["macd"] is not None
    assert ch[32]["signal"] is None and ch[33]["signal"] is not None
    assert abs(ch[40]["hist"] - (ch[40]["macd"] - ch[40]["signal"])) < 1e-12


def wave():
    return [100 - 0.5 * i for i in range(60)] + [70 + 1.5 * i for i in range(40)] + [130 - 1.5 * i for i in range(40)]


def test_spot_accounting_follows_the_audit_formulas():
    bars = bars_from(wave())
    res = model.simulate(bars, symbol="X")
    ch = model.channels(bars)
    cross = next(i for i in range(1, len(bars)) if ch[i - 1]["hist"] is not None and ch[i - 1]["hist"] <= 0 < ch[i]["hist"])
    out = next(i for i in range(cross + 1, len(bars)) if ch[i - 1]["hist"] >= 0 > ch[i]["hist"])
    t = res["trades"][0]
    bp = bars[cross + 1]["o"] * (1 + model.SLIP)
    units = model.START_EQUITY / (bp * (1 + model.FEE))
    cash = units * bars[out + 1]["o"] * (1 - model.SLIP) * (1 - model.FEE)
    assert t["entry_t"] == bars[cross + 1]["t"] and abs(t["entry"] - bp) < 1e-9 and abs(t["qty"] - units) < 1e-9
    assert t["exit_t"] == bars[out + 1]["t"] and abs(t["notional"] + t["pnl"] - cash) < 1e-6
    assert all(x["side"] == "long" for x in res["trades"]) and res["summary"]["funding"] == 0


def test_close_at_end_sells_and_final_signal_is_ignored():
    bars = bars_from(wave()[:100])                                   # ends while long
    open_ = model.simulate(bars, symbol="X")
    shut = model.simulate(bars, symbol="X", close_at_end=True)
    assert open_["open_trade"] is not None and shut["open_trade"] is None
    last = shut["trades"][-1]
    assert last["reason"].startswith("end of test") and abs(last["exit"] - bars[-1]["c"] * (1 - model.SLIP)) < 1e-9
    assert abs(shut["summary"]["end_equity"] - (last["notional"] + last["pnl"])) < 1e-6


def test_service_is_spot_zec_only_with_the_fixed_window(tmp_path):
    now = model.BACKTEST_END + 30 * 86_400_000
    t0 = model.WARMUP_FROM
    n = (now - t0) // H4
    closes = [100 + 10 * ((i // 60) % 2) + (i % 60) * (0.3 if (i // 60) % 2 == 0 else -0.3) for i in range(n)]
    market = FakeMarket(bars_from(closes, t0))
    venues = []
    get = market.client.get
    market.client.get = lambda venue, *a, **k: venues.append(venue) or get(venue, *a, **k)
    svc = MacdService(tmp_path / "m.db", env={"MACD_ENABLED": "false"}, market=market, clock=lambda: now / 1000)
    assert svc.universe == ["ZECUSDT"]
    svc.cycle()
    assert "spot" in venues and set(venues) <= {"spot", "perp"}     # perp only feeds the separate monthly study
    assert svc.perp_monthly()["read_only"]
    s = svc.summary()
    assert s["rule_version"] == "MACD-2" and s["read_only"] and s["backtest"]["ZECUSDT"]["buy_hold_pct"] is not None
    bt = svc.backtest("ZECUSDT")
    assert bt["start"] == model.BACKTEST_START and bt["end"] == model.BACKTEST_END
    assert all(model.BACKTEST_START <= t["signal_t"] and t["exit_t"] <= model.BACKTEST_END for t in bt["trades"])
    assert s["books"]["ZECUSDT"]["summary"]["start_equity"] == 10_000.0
    ch = svc.chart("ZECUSDT")
    assert ch["market"] == "spot" and {"macd", "signal", "hist", "time"} <= set(ch["channels"][-1])


def _hourly(b4):
    out = []
    for b in b4:
        for k in range(4):
            out.append(dict(t=b["t"] + k * 3_600_000, o=b["o"], h=b["h"], l=b["l"], c=b["c"] if k == 3 else b["o"]))
    return out


def test_perp_monthly_chains_months_and_charges_funding_at_mark():
    from app.macd import perp_monthly as pm
    closes = [100 + 10 * ((i // 60) % 2) + (i % 60) * (0.3 if (i // 60) % 2 == 0 else -0.3) for i in range(1300)]
    b4 = bars_from(closes, pm.WARMUP_FROM)
    h1 = _hourly(b4)
    base = pm.compute(b4, h1, [])
    months = base["months"]
    assert len(months) >= 3 and base["total"]["closed_trades"] > 0
    for a, b in zip(months, months[1:]):
        assert abs(a["end_balance"] - b["start_balance"]) < 1e-12        # one continuing account
    total = 1.0
    for m in months:
        total *= 1 + m["return_pct"] / 100
    assert abs(100 * (total - 1) - base["total"]["return_pct"]) < 1e-9
    # a positive rate at every 8h settlement charges the long; nothing is charged while flat
    fund = [(t, 0.001, 123.0) for t in range(pm.START, h1[-1]["t"], 8 * 3_600_000)]
    charged = pm.compute(b4, h1, fund)
    assert charged["total"]["funding_net"] < 0 and charged["total"]["end_balance"] < base["total"]["end_balance"]
    one = charged["trades"][0]
    paid = [r for r in fund if one["entry_t"] <= r[0] < one["exit_t"]]
    assert abs(one["funding"] - sum(-one["units"] * 123.0 * 0.001 for _ in paid)) < 1e-9   # units x MARK x rate


def test_perp_monthly_marks_an_open_position_without_selling():
    from app.macd import perp_monthly as pm
    import math
    closes = [100 + 20 * math.sin(2 * math.pi * i / 120) for i in range(120 * 7 + 20)]   # ends on a rising leg in 2026
    b4 = bars_from(closes, pm.WARMUP_FROM)
    rep = pm.compute(b4, _hourly(b4), [])
    o = rep["open_position"]
    assert o is not None and o["value_if_sold"] < o["value"]
    assert abs(rep["total"]["end_balance"] - o["value"]) < 1e-9 and rep["months"][-1]["partial"] in (True, False)
