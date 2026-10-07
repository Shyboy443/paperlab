"""Execution timing, causality, cash reconciliation and restart safety."""
from dataclasses import replace
import json
import math

import pytest

from app.core.types import Candle
from app.video_breakout.data import bar_from_row
from app.video_breakout.engine import BreakoutBot, Config, STEP, backtest
from scripts.video_breakout import paper_step, state_lock


def bar(i, close=100.0, *, open=100.0, high=None, low=None, closed=True):
    return Candle("BTCUSDT", "4h", i * STEP, open,
                  high if high is not None else max(open, close) + 1,
                  low if low is not None else min(open, close) - 1,
                  close, 10, (i + 1) * STEP, closed)


def seeded(config=Config()):
    bot = BreakoutBot(config)
    bot.warmup([bar(i) for i in range(42)])
    return bot


def test_prior_high_excludes_signal_and_fills_next_open_with_gap():
    bot = seeded()
    signal = bot.on_close(bar(42, close=102, high=500))
    assert signal["entry_high"] == 101
    assert bot.qty == 0 and not bot.fills
    assert bot.on_open(42 * STEP, 100) is None
    fill = bot.on_open(43 * STEP, 110)
    assert fill["price"] == pytest.approx(110 * 1.0002)
    assert fill["fill_time"] == 43 * STEP
    assert bot.cash == pytest.approx(0, abs=1e-10)
    assert len(bot.fills) == 1
    assert bot.on_open(43 * STEP, 110) is None


@pytest.mark.parametrize("close", [100, 101])
def test_no_breakout_or_equality_means_no_entry(close):
    bot = seeded()
    assert bot.on_close(bar(42, close=close)) is None


def test_requires_full_42_bar_warmup_and_completed_candle():
    bot = BreakoutBot()
    bot.warmup([bar(i) for i in range(41)])
    assert bot.on_close(bar(41, close=102)) is None
    with pytest.raises(ValueError, match="completed"):
        seeded().on_close(bar(42, close=102, closed=False))


def test_exact_42_bar_entry_window():
    bot = BreakoutBot()
    # An older high outside the seven-day window must not suppress the entry.
    bot.warmup([bar(0, high=1000)] + [bar(i) for i in range(1, 43)])
    assert bot.on_close(bar(43, close=102))["side"] == "BUY"


def test_exit_uses_only_prior_18_lows_and_returns_to_cash():
    bot = BreakoutBot(Config(fee_bps=0, slippage_bps=0))
    bot.warmup([bar(0, low=10)] + [bar(i) for i in range(1, 42)])
    bot.on_close(bar(42, close=102))
    bot.on_open(43 * STEP, 110)
    exit_signal = bot.on_close(bar(43, close=98, low=1))
    assert exit_signal["exit_low"] == 99
    assert exit_signal["side"] == "SELL"
    assert bot.qty > 0  # Closing signal is not an immediate stop fill.
    bot.on_open(44 * STEP, 90)
    assert bot.qty == 0
    assert len(bot.trades) == 1
    assert bot.cash == pytest.approx(1000 * 90 / 110)


def test_exit_equality_no_shorting_and_no_pyramiding():
    bot = seeded()
    bot.on_close(bar(42, close=102))
    bot.on_open(43 * STEP, 103)
    assert bot.on_close(bar(43, close=99)) is None
    assert bot.on_close(bar(44, close=104)) is None
    flat = seeded()
    assert flat.on_close(bar(42, close=98)) is None


def test_independent_cost_and_cash_reconciliation():
    cfg = Config(allocation=0.8, fee_bps=10, slippage_bps=2)
    bot = seeded(cfg)
    bot.on_close(bar(42, close=102))
    bot.on_open(43 * STEP, 110)
    bot.on_close(bar(43, close=98))
    bot.on_open(44 * STEP, 90)
    qty = 800 / (110 * 1.0002 * 1.001)
    expected_cash = 200 + qty * 90 * 0.9998 * 0.999
    assert bot.cash == pytest.approx(expected_cash)
    assert bot.trades[0]["pnl"] == pytest.approx(expected_cash - 1000)
    assert sum(f["fee"] for f in bot.fills) == pytest.approx(bot.trades[0]["fees"])


def test_future_price_mutation_cannot_change_past_signals_or_fills():
    tape = [bar(i) for i in range(42)] + [bar(42, close=102), bar(43, open=110, close=111)]
    a = backtest(tape + [bar(44, close=98), bar(45, open=90)], Config())
    b = backtest(tape + [bar(44, close=1000), bar(45, open=900, close=900)], Config())
    assert a.fills[0] == b.fills[0]
    assert a.signals[0] == b.signals[0]
    assert a.curve[:44] == b.curve[:44]


def test_end_of_data_does_not_invent_fill_or_liquidation():
    tape = [bar(i) for i in range(42)] + [bar(42, close=102)]
    bot = backtest(tape, Config())
    assert not bot.fills and bot.pending is not None
    bot = backtest(tape + [bar(43, close=110, open=103)], Config())
    assert bot.qty > 0 and not bot.trades


def test_warmup_does_not_generate_trades_or_result_equity_samples():
    tape = [bar(i) for i in range(42)] + [bar(42, close=102), bar(43, open=105, close=105)]
    bot = backtest(tape, Config(), start_ms=42 * STEP, end_ms=44 * STEP)
    assert len(bot.fills) == 1
    assert bot.curve[0]["ts"] == 43 * STEP


@pytest.mark.parametrize("invalid", [
    lambda: [bar(0), bar(2)],
    lambda: [bar(0), bar(0)],
    lambda: [bar(1), bar(0)],
    lambda: [replace(bar(0), close=math.nan)],
    lambda: [replace(bar(0), low=-1)],
    lambda: [replace(bar(0), tf="1h")],
    lambda: [replace(bar(0), close_time=STEP - 1)],
    lambda: [bar(0, closed=False)],
])
def test_invalid_tape_refused(invalid):
    with pytest.raises(ValueError):
        backtest(invalid(), Config())


def test_drawdown_gate_rejects_but_does_not_change_strategy():
    bot = seeded()
    bot.mark(0, 100)
    bot.cash = 590
    bot.mark(STEP, 100)
    summary = bot.summary()
    assert summary["verdict"] == "FAIL"
    assert summary["max_drawdown_pct"] == pytest.approx(41)
    assert len(bot.trades) == 0  # Failure takes priority over small sample.
    bot.cash = 1000
    assert bot.on_close(bar(42, close=102))["side"] == "BUY"
    assert seeded().summary()["verdict"] == "INCONCLUSIVE"


def test_atomic_state_roundtrip_pending_and_open_position():
    cfg = Config()
    bot = seeded(cfg)
    bot.on_close(bar(42, close=102))
    resumed = BreakoutBot.from_state(json.loads(json.dumps(bot.to_state())), cfg)
    assert resumed.on_open(43 * STEP, 110) == bot.on_open(43 * STEP, 110)
    resumed = BreakoutBot.from_state(json.loads(json.dumps(resumed.to_state())), cfg)
    resumed.on_close(bar(43, close=98))
    resumed.on_open(44 * STEP, 90)
    assert resumed.qty == 0 and len(resumed.trades) == 1
    with pytest.raises(ValueError, match="mismatch"):
        BreakoutBot.from_state(bot.to_state(), Config(fee_bps=5))


def test_late_paper_signal_expires_and_never_retroactively_fills():
    bot = seeded()
    bot.on_close(bar(42, close=102))
    assert bot.on_open(43 * STEP, 110, observed_ms=43 * STEP + 31_000) is None
    assert bot.pending is None and bot.qty == 0 and not bot.fills
    assert bot.signals[-1]["expired"]


def test_paper_warmup_next_close_and_duplicate_poll():
    bot = BreakoutBot()
    tape = [bar(i) for i in range(42)] + [bar(42, closed=False)]
    status = paper_step(bot, 42 * STEP + 5000, tape, 30_000)
    assert status["status"] == "WARMED_UP" and not bot.fills
    tape = [bar(i) for i in range(42)] + [bar(42, close=102), bar(43, close=110, closed=False)]
    paper_step(bot, 43 * STEP + 5000, tape, 30_000)
    assert len(bot.fills) == 1
    assert bot.fills[0]["reference_open"] == 110  # Observed quote, not stale candle open.
    paper_step(bot, 43 * STEP + 10000, tape, 30_000)
    assert len(bot.fills) == 1


def test_paper_missed_candles_refused():
    bot = seeded()
    tape = [bar(i) for i in range(44)] + [bar(44, closed=False)]
    with pytest.raises(ValueError, match="Missed"):
        paper_step(bot, 44 * STEP + 5000, tape, 30_000)


def test_state_lock_prevents_double_runner(tmp_path):
    path = tmp_path / "state.json"
    with state_lock(path):
        with pytest.raises(OSError):
            with state_lock(path):
                pass
    with state_lock(path):
        pass


def test_archive_millisecond_and_microsecond_timestamps():
    ts = 1_735_689_600_000
    row = [ts, 100, 101, 99, 100, 10, ts + STEP - 1]
    a = bar_from_row(row, "BTCUSDT")
    b = bar_from_row([ts * 1000, *row[1:6], (ts + STEP) * 1000 - 1], "BTCUSDT")
    assert a == b and a.close_time == ts + STEP


def test_incorrect_source_interval_refused():
    with pytest.raises(ValueError, match="full 4h"):
        bar_from_row([0, 100, 101, 99, 100, 10, 60_000], "BTCUSDT")


def test_paper_rejects_bad_forming_bar_and_gapped_warmup():
    tape = [bar(i) for i in range(42)] + [bar(42, closed=False)]
    with pytest.raises(ValueError, match="OHLCV"):
        paper_step(BreakoutBot(), 42 * STEP + 5000,
                   tape[:-1] + [replace(tape[-1], close=math.nan)], 30_000)
    with pytest.raises(ValueError, match="Non-contiguous"):
        paper_step(BreakoutBot(), 42 * STEP + 5000, tape[:5] + tape[6:], 30_000)
