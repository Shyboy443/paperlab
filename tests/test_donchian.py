from app.donchian import model
from app.donchian.service import DonchianService

H4 = model.TF_MS


def bars_from(closes, t0=0):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(dict(t=t0 + i * H4, o=o, h=max(o, c) * 1.001, l=min(o, c) * 0.999, c=c, v=1.0))
    return out


def test_channels_use_prior_bars_only():
    bars = bars_from([100 + i for i in range(30)])
    ch = model.channels(bars)
    assert ch[19]["up"] is None and ch[20]["up"] == max(b["h"] for b in bars[:20])
    assert ch[25]["xlo"] == min(b["l"] for b in bars[15:25])


def test_breakout_fills_next_open_then_trailing_exit():
    closes = [100.0] * 25 + [101 + i for i in range(10)] + [110 - 2 * i for i in range(15)]
    bars = bars_from(closes)
    res = model.simulate(bars, symbol="X")
    first = res["trades"][0]
    signal = next(i for i, c in enumerate(model.channels(bars)) if c["up"] is not None and bars[i]["c"] > c["up"])
    assert first["side"] == "long" and first["entry_t"] == bars[signal + 1]["t"]
    assert abs(first["entry"] - bars[signal + 1]["o"] * (1 + model.SLIP)) < 1e-9
    assert first["reason"] in ("exit", "reverse") and first["exit_t"] > first["entry_t"]
    assert res["summary"]["trades"] == len(res["trades"]) and res["summary"]["fees"] > 0


def test_short_reverses_on_opposite_breakout_and_funding_charges_longs():
    closes = [100.0] * 25 + [102.0, 104.0, 106.0] + [90.0, 80.0, 70.0] + [70.0] * 5
    bars = bars_from(closes)
    funding = [(bars[27]["t"] + 1, 0.001, 0)]
    res = model.simulate(bars, funding, symbol="X")
    sides = [t["side"] for t in res["trades"]] + ([res["open_trade"]["side"]] if res["open_trade"] else [])
    assert sides[:2] == ["long", "short"]
    assert res["trades"][0]["reason"] == "reverse" and res["trades"][0]["funding"] < 0


def test_start_ms_only_counts_later_trades():
    closes = [100.0] * 25 + [101 + i for i in range(10)] + [110 - 2 * i for i in range(15)] + [90 + 3 * i for i in range(20)]
    bars = bars_from(closes)
    full = model.simulate(bars)
    start = full["trades"][0]["entry_t"] + 1                 # mid-trade in the full run
    cut = model.simulate(bars, start_ms=start)
    # warm-up bars never trade: the book starts flat at the start equity and every entry comes from a signal at/after start
    assert cut["equity_curve"][0]["value"] == model.START_EQUITY
    assert all(t["signal_t"] >= start for t in cut["trades"])
    late = model.simulate(bars, start_ms=bars[-1]["t"] + 1)
    assert late["trades"] == [] and late["open_trade"] is None


class FakeClient:
    def __init__(self, bars):
        self.bars = bars

    def get(self, market, path, params, weight=1):
        rows = [b for b in self.bars if params["startTime"] <= b["t"] <= params["endTime"]][: params["limit"]]
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in rows]


class FakeMarket:
    def __init__(self, bars):
        self.client = FakeClient(bars)

    def funding(self, symbol, start, end):
        return []


def test_service_cycle_and_read_only_views(tmp_path):
    now = 800 * 86_400_000
    t0 = now - 4500 * H4
    closes = [100 + 10 * ((i // 60) % 2) + (i % 60) * (0.3 if (i // 60) % 2 == 0 else -0.3) for i in range(4500)]
    svc = DonchianService(tmp_path / "d.db", env={"DONCHIAN_ENABLED": "false"}, market=FakeMarket(bars_from(closes, t0)),
                          clock=lambda: now / 1000)
    svc.universe = ["XRPUSDT"]
    svc.cycle()
    s = svc.summary()
    assert s["read_only"] and s["execution"] == "PAPER_ONLY" and "XRPUSDT" in s["backtest"]
    bt = svc.backtest("XRPUSDT")
    assert bt["summary"]["trades"] > 0 and bt["trades"]
    ch = svc.chart("XRPUSDT", around=bt["trades"][0]["entry_t"])
    assert ch["candles"] and len(ch["channels"]) == len(ch["candles"])
    try:
        svc.chart("FAKEUSDT")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
