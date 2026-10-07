"""Backtest S15 Liquidation Cascade Fade on real USD-M futures 1m bars.

This drives the REAL strategy class (app.strategies.s15_liq_cascade_fade) over historical candles, so the
entry logic under test is byte-for-byte the one the lab runs. Only the market and the fill model are
simulated here.

Data: https://data.binance.vision public archive (monthly + daily 1m klines, USD-M futures). Read-only,
no API key, no orders. Cached under <project>/data/klines/.

Honest-by-default modelling choices, all of which favour the SKEPTIC:
  * entry fills at the signal bar's close, moved against us by `slip_bps`;
  * exits are checked bar-by-bar on 1m OHLC. If a bar's range contains BOTH the stop and the target we
    assume the STOP (we cannot see intrabar order). This is the standard pessimistic convention;
  * stop / target fills are also moved against us by `slip_bps` - a stop in a cascade does not fill at the
    stop price;
  * taker fee charged on both sides;
  * one position at a time (the strategy's max_positions=1) and the real 120 s per-symbol cooldown.

Everything is reported in R (net PnL / intended risk), so it is independent of account size.

    python scripts/backtest_s15.py                  # default sweep
    python scripts/backtest_s15.py --fetch-only     # just fill the cache
"""
from __future__ import annotations

import argparse
import csv
import io
import statistics
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.core.types import Candle  # noqa: E402
from app.strategies.s15_liq_cascade_fade import LiquidationCascadeFade, Params  # noqa: E402
from tests.conftest import FakeCtx  # noqa: E402

ARCHIVE = "https://data.binance.vision/data/futures/um"
CACHE = PROJECT / "data" / "klines"
TAKER = 0.0004


# ---------------------------------------------------------------- data ----
def _rows_from_zip(blob: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(text)))
    # some months ship a header row; the archive is inconsistent about it
    return [r for r in rows if r and r[0].isdigit()]


def _download(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return r.read()
    except Exception:
        return None


def fetch(symbol: str, months: list[str], days: list[str]) -> list[Candle]:
    """1m candles for a symbol, monthly archives plus trailing daily files, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[list[str]] = []
    for kind, keys in (("monthly", months), ("daily", days)):
        for key in keys:
            cached = CACHE / f"{symbol}-1m-{key}.zip"
            if not cached.exists():
                blob = _download(f"{ARCHIVE}/{kind}/klines/{symbol}/1m/{symbol}-1m-{key}.zip")
                if blob is None:
                    print(f"  ! missing {symbol} {key}")
                    continue
                cached.write_bytes(blob)
            rows.extend(_rows_from_zip(cached.read_bytes()))
    rows.sort(key=lambda r: int(r[0]))
    out, seen = [], set()
    for r in rows:
        ot = int(r[0])
        if ot in seen:
            continue
        seen.add(ot)
        # archive close_time is ot+59999; the lab's Candle.close_time is the bar's close instant
        out.append(Candle(symbol, "1m", ot, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                          float(r[5]), ot + 60_000, "backfill"))
    return out


def to_15m(ones: list[Candle]) -> list[Candle]:
    """Resample closed 1m bars into closed 15m bars (only complete 15-bar windows)."""
    out, bucket = [], []
    for c in ones:
        bucket.append(c)
        if (c.open_time // 60_000) % 15 == 14:
            if len(bucket) == 15 and bucket[0].open_time + 14 * 60_000 == c.open_time:
                out.append(Candle(c.symbol, "15m", bucket[0].open_time, bucket[0].open,
                                  max(b.high for b in bucket), min(b.low for b in bucket), c.close,
                                  sum(b.volume for b in bucket), c.close_time, "backfill"))
            bucket = []
    return out


# ------------------------------------------------------------ backtest ----
@dataclass
class Trade:
    symbol: str
    side: str
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    stop: float
    tp: float
    risk_per_unit: float
    kind: str
    r: float
    size_pct: float
    vol_mult: float

    @property
    def hold_s(self) -> int:
        return (self.exit_ts - self.entry_ts) // 1000


class Book:
    """One open position at a time, exits resolved on 1m OHLC."""

    def __init__(self, slip_bps: float, taker: float = TAKER):
        self.slip = slip_bps / 1e4
        self.taker = taker
        self.open: dict | None = None
        self.trades: list[Trade] = []
        self.cooldown: dict[str, int] = {}

    def enter(self, sig, cascade_meta: dict) -> None:
        side = sig.side
        ref, stop = sig.entry_price, sig.stop
        tp = sig.take_profits[-1].price
        fill = ref * (1 + self.slip) if side == "long" else ref * (1 - self.slip)  # pay up to get in
        self.open = {"symbol": sig.symbol, "side": side, "ts": sig.ts, "entry": fill, "stop": stop, "tp": tp,
                     "risk": abs(ref - stop), "meta": cascade_meta,
                     "deadline": sig.ts + int(sig.max_hold_s) * 1000}

    def _close(self, price: float, ts: int, kind: str) -> None:
        o = self.open
        assert o is not None
        exit_fill = price * (1 - self.slip) if o["side"] == "long" else price * (1 + self.slip)
        gross = (exit_fill - o["entry"]) if o["side"] == "long" else (o["entry"] - exit_fill)
        fees = self.taker * (o["entry"] + exit_fill)          # per unit, both sides
        r = (gross - fees) / o["risk"] if o["risk"] > 0 else 0.0
        self.trades.append(Trade(o["symbol"], o["side"], o["ts"], ts, o["entry"], exit_fill, o["stop"],
                                 o["tp"], o["risk"], kind, r, o["meta"].get("size_pct", 0.0),
                                 o["meta"].get("vol_mult", 0.0)))
        self.cooldown[o["symbol"]] = ts + 120_000
        self.open = None

    def step(self, c: Candle) -> None:
        """Resolve the open position against one 1m bar. Stop wins any ambiguous bar."""
        o = self.open
        if o is None or c.symbol != o["symbol"] or c.close_time <= o["ts"]:
            return
        if o["side"] == "long":
            hit_stop, hit_tp = c.low <= o["stop"], c.high >= o["tp"]
        else:
            hit_stop, hit_tp = c.high >= o["stop"], c.low <= o["tp"]
        if hit_stop:                                  # pessimistic: stop before target within a bar
            self._close(o["stop"], c.close_time, "stop")
        elif hit_tp:
            self._close(o["tp"], c.close_time, "tp")
        elif c.close_time >= o["deadline"]:
            self._close(c.close, c.close_time, "time")


def run(data: dict[str, list[Candle]], params: Params, slip_bps: float,
        fee_gate: bool = True, min_rr: float = 0.5, taker: float = TAKER,
        since_ms: int = 0) -> list[Trade]:
    """Walk every symbol's 1m tape in one merged timeline, exactly as the live engine sees it."""
    strat = LiquidationCascadeFade(params)
    ctx = FakeCtx(symbols=list(data))
    book = Book(slip_bps, taker)
    fifteens = {s: {c.close_time: c for c in to_15m(cs)} for s, cs in data.items()}
    merged = sorted((c for cs in data.values() for c in cs), key=lambda c: (c.close_time, c.symbol))

    for c in merged:
        f = fifteens[c.symbol].get(c.close_time)
        if f is not None:
            ctx.push(f)                                # the 15m gate bar closes at the same instant
        ctx.push(c)
        ctx.prices[c.symbol] = c.close
        book.step(c)
        if book.open is not None or c.close_time < book.cooldown.get(c.symbol, 0):
            continue
        if c.close_time < since_ms:          # warm the indicators, but do not trade the warm-up window
            strat.on_candle(c, ctx)
            continue
        for sig in strat.on_candle(c, ctx):
            risk = abs(sig.entry_price - sig.stop)
            reward = abs(sig.take_profits[-1].price - sig.entry_price)
            if risk <= 0 or reward / risk < min_rr:
                continue
            # the lab's fee gate: 2 x taker x notional <= 0.25 x risk  ->  stop must be >= 8 x taker (32 bps)
            if fee_gate and risk / sig.entry_price < 8 * TAKER:
                continue
            book.enter(sig, sig.meta)
            break
    return book.trades


# ------------------------------------------------------------- report ----
def summarise(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp, gl = sum(wins), -sum(losses)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in rs:                                     # flat-stake R curve
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": len(rs), "win_rate": len(wins) / len(rs), "total_r": sum(rs),
        "avg_r": statistics.fmean(rs), "median_r": statistics.median(rs),
        "avg_win": statistics.fmean(wins) if wins else 0.0,
        "avg_loss": statistics.fmean(losses) if losses else 0.0,
        "worst": min(rs), "best": max(rs),
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "max_dd_r": dd,
        "stops": sum(1 for t in trades if t.kind == "stop"),
        "tps": sum(1 for t in trades if t.kind == "tp"),
        "times": sum(1 for t in trades if t.kind == "time"),
        "breakeven_wr": 1 / (1 + statistics.fmean(wins) / -statistics.fmean(losses)) if wins and losses else None,
    }


def line(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"{label:<26} no trades"
    return (f"{label:<26} n={s['n']:<5} wr={s['win_rate']*100:5.1f}%  avgR={s['avg_r']:+.3f}  "
            f"totR={s['total_r']:+8.1f}  PF={s['profit_factor']:5.2f}  maxDD={s['max_dd_r']:7.1f}R  "
            f"stop/tp/time={s['stops']}/{s['tps']}/{s['times']}")


def equity_curve(trades: list[Trade], start: float, risk_pct: float,
                 halt_at: float | None = None) -> dict:
    """Compound a real account: each trade risks `risk_pct` of CURRENT equity, so PnL = risk x R."""
    eq, peak, dd, halted_at = start, start, 0.0, None
    lo = start
    for i, t in enumerate(trades, 1):
        eq += eq * risk_pct * t.r
        lo = min(lo, eq)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        if halt_at is not None and eq <= halt_at and halted_at is None:
            halted_at = i
            break
        if eq <= 1.0:
            halted_at = i
            break
    return {"final": eq, "low": lo, "max_dd": dd, "halted_after": halted_at, "n": len(trades)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--months", default="2026-04,2026-05,2026-06,2026-07,2026-08")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--split", default="2026-08-01", help="UTC date where the holdout starts")
    args = ap.parse_args()

    symbols = args.symbols.split(",")
    months = args.months.split(",")
    last_month = datetime.strptime(months[-1], "%Y-%m").date()
    nxt = date(last_month.year + (last_month.month == 12), (last_month.month % 12) + 1, 1)
    days = [(nxt + timedelta(days=i)).isoformat() for i in range((date.today() - nxt).days)]

    data: dict[str, list[Candle]] = {}
    for s in symbols:
        print(f"fetching {s} ...")
        data[s] = fetch(s, months, days)
        if data[s]:
            a, b = data[s][0].open_time, data[s][-1].open_time
            fmt = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
            print(f"  {len(data[s]):,} 1m bars  {fmt(a)} -> {fmt(b)}")
    if args.fetch_only:
        return

    split_ms = int(datetime.strptime(args.split, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    print("\n" + "=" * 118)
    print("S15 LIQUIDATION CASCADE FADE - real USD-M futures 1m bars, real strategy code")
    print("stop wins any bar containing both stop and target; slippage applied against us on entry AND exit")
    print("=" * 118)

    configs = [
        ("testnet 0.45%/1.8x", Params()),
        ("live-ish 0.70%/3.0x", Params(move_pct=0.70, volume_mult=3.0)),
    ]
    results: dict[tuple[str, float], list[Trade]] = {}
    for name, p in configs:
        print(f"\n--- {name} " + "-" * (110 - len(name)))
        for slip, tag in ((2.0, "2bp  (the lab's model)"), (8.0, "8bp  (realistic taker)"),
                          (20.0, "20bp (cascade fill)")):
            trades = run(data, p, slip)
            results[(name, slip)] = trades
            print(line(f"  slippage {tag}", summarise(trades)))

    base = results[("testnet 0.45%/1.8x", 8.0)]
    print("\n" + "=" * 118)
    print("DETAIL - testnet thresholds @ 8bp slippage (the closest analogue to the live lab)")
    print("=" * 118)
    s = summarise(base)
    if s["n"]:
        print(f"  trades {s['n']}   win rate {s['win_rate']*100:.1f}%   avg win {s['avg_win']:+.3f}R   "
              f"avg loss {s['avg_loss']:+.3f}R   worst {s['worst']:+.2f}R")
        print(f"  breakeven win rate needed: {s['breakeven_wr']*100:.1f}%  ->  "
              f"{'PROFITABLE' if s['win_rate'] > s['breakeven_wr'] else 'LOSES MONEY'}")
        print(f"  exits: {s['stops']} stops / {s['tps']} targets / {s['times']} time")
        print(f"  total {s['total_r']:+.1f}R, max drawdown {s['max_dd_r']:.1f}R")
        ins = [t for t in base if t.entry_ts < split_ms]
        out = [t for t in base if t.entry_ts >= split_ms]
        print(f"\n  in-sample  (< {args.split}): {line('', summarise(ins)).strip()}")
        print(f"  OUT-OF-SAMPLE (>= {args.split}): {line('', summarise(out)).strip()}")
        for sym in symbols:
            print(f"  {sym:<10} {line('', summarise([t for t in base if t.symbol == sym])).strip()}")


if __name__ == "__main__":
    main()
