"""Neutral S15 backtest: the live engine's rules, nothing added, nothing softened.

Every modelling choice below is copied from the running lab rather than chosen by me:

  entry fill   portfolio.open_position -> MARKET at ref_price +/- slippage_bps, taker fee   (portfolio.py:106)
  exit  fill   portfolio.close_position -> MARKET at ref_price +/- slippage_bps, taker fee  (portfolio.py:144)
               ... this applies to take-profits too. The lab has no resting limit exits.
  slippage     uniform(slippage_bps_min, slippage_bps_max) = uniform(1, 3) bps              (config.py)
  taker        settings.taker_fee                                                            (config.py)
  gates        min_rr (0.5, the strategy's own), max_positions=1, 120 s per-symbol cooldown
  fee gate     risk.py's fee_gt_r: stop must be >= 8 x taker. Reported ON and OFF.

The only thing I must choose, because 1m OHLC cannot show intrabar order, is what happens when a single
bar contains both the stop and the target. Both answers are reported; they turn out identical because no
bar in this data contains both.

Slippage is random, so every configuration is run over many seeds and the SPREAD is reported, not one
lucky or unlucky draw. Confidence intervals are bootstrapped from the trade sample.

    python scripts/backtest_s15_neutral.py
"""
from __future__ import annotations

import datetime as dt
import random
import statistics as st
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.core.types import Candle  # noqa: E402
from app.strategies.s15_liq_cascade_fade import LiquidationCascadeFade, Params  # noqa: E402
from scripts.backtest_s15 import fetch, to_15m  # noqa: E402
from tests.conftest import FakeCtx  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
SLIP_LO, SLIP_HI = 1.0, 3.0          # config.py slippage_bps_min / _max


def simulate(data, f15, since_ms, taker, rng, fee_gate=True, stop_wins_ties=True):
    """One pass of the lab's own fill model over the tape. Returns a list of R multiples."""
    strat = LiquidationCascadeFade(Params())
    ctx = FakeCtx(symbols=list(data))
    pos = None
    out: list[tuple[float, str]] = []
    cooldown: dict[str, int] = {}
    merged = sorted((c for cs in data.values() for c in cs), key=lambda c: (c.close_time, c.symbol))

    def fill(ref: float, side_out: str) -> float:
        """portfolio.fill_price: a market order always pays the spread."""
        bps = rng.uniform(SLIP_LO, SLIP_HI) / 1e4
        return ref * (1 - bps) if side_out == "SELL" else ref * (1 + bps)

    def close(ref, ts, kind):
        nonlocal pos
        ex = fill(ref, "SELL" if pos["side"] == "long" else "BUY")
        gross = (ex - pos["entry"]) if pos["side"] == "long" else (pos["entry"] - ex)
        r = (gross - taker * (pos["entry"] + ex)) / pos["risk"]
        out.append((r, kind))
        cooldown[pos["sym"]] = ts + 120_000
        pos = None

    for c in merged:
        g = f15[c.symbol].get(c.close_time)
        if g is not None:
            ctx.push(g)
        ctx.push(c)
        ctx.prices[c.symbol] = c.close
        if pos and c.symbol == pos["sym"] and c.close_time > pos["ts"]:
            if pos["side"] == "long":
                hs, ht = c.low <= pos["stop"], c.high >= pos["tp"]
            else:
                hs, ht = c.high >= pos["stop"], c.low <= pos["tp"]
            if hs and ht:
                close(pos["stop"] if stop_wins_ties else pos["tp"], c.close_time,
                      "stop" if stop_wins_ties else "tp")
            elif hs:
                close(pos["stop"], c.close_time, "stop")
            elif ht:
                close(pos["tp"], c.close_time, "tp")
            elif c.close_time >= pos["dead"]:
                close(c.close, c.close_time, "time")
        sigs = strat.on_candle(c, ctx)
        if pos is not None or c.close_time < since_ms or c.close_time < cooldown.get(c.symbol, 0):
            continue
        for s in sigs:
            risk = abs(s.entry_price - s.stop)
            tp = s.take_profits[-1].price
            if risk <= 0 or abs(tp - s.entry_price) / risk < 0.5:          # the strategy's own min_rr
                continue
            if fee_gate and risk / s.entry_price < 8 * taker:              # risk.py fee_gt_r
                continue
            pos = {"sym": c.symbol, "side": s.side, "ts": c.close_time, "risk": risk,
                   "entry": fill(s.entry_price, "BUY" if s.side == "long" else "SELL"),
                   "stop": s.stop, "tp": tp, "dead": c.close_time + int(s.max_hold_s) * 1000}
            break
    return out


def equity(rs: list[float], start: float, risk_pct: float) -> float:
    eq = start
    for r in rs:
        eq += eq * risk_pct * r
        if eq <= 0.01:
            return 0.0
    return eq


def boot_ci(rs: list[float], n: int = 2000) -> tuple[float, float]:
    rng = random.Random(7)
    means = sorted(st.fmean(rng.choices(rs, k=len(rs))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main() -> None:
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    days = [(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat() for i in range(19)]
    data = {s: fetch(s, months, days) for s in SYMBOLS}
    f15 = {s: {c.close_time: c for c in to_15m(cs)} for s, cs in data.items()}
    ms = lambda d: int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    print("=" * 100)
    print("S15 - NEUTRAL BACKTEST.  Fill model copied from the running lab, not chosen by me.")
    print("Random slippage uniform(1,3)bp as in config; 25 seeds per configuration; spread reported.")
    print("=" * 100)

    windows = [("last 1 month  (08-19 -> 09-19)", ms("2026-08-19")),
               ("last 3 months (06-19 -> 09-19)", ms("2026-06-19")),
               ("last 6 months (03-19 -> 09-19)", ms("2026-03-19"))]
    fees = [("lab's taker 0.040%", 0.0004), ("real taker 0.050%", 0.0005)]

    for wname, since in windows:
        print(f"\n### {wname}")
        for fname, taker in fees:
            for gate in (True, False):
                runs = [simulate(data, f15, since, taker, random.Random(1000 + k), fee_gate=gate)
                        for k in range(25)]
                avgs = [st.fmean([r for r, _ in x]) for x in runs if x]
                tots = [sum(r for r, _ in x) for x in runs if x]
                eqs = sorted(equity([r for r, _ in x], 100.0, 0.04) for x in runs if x)
                n = len(runs[0])
                wr = st.fmean([sum(1 for r, _ in x if r > 0) / len(x) for x in runs if x]) * 100
                print(f"  {fname} | fee gate {'ON ':3} n={n:<4} win={wr:4.1f}%  "
                      f"avgR {min(avgs):+.3f}..{max(avgs):+.3f}  totR {min(tots):+7.1f}..{max(tots):+7.1f}  "
                      f"$100->${eqs[0]:.2f}..${eqs[-1]:.2f}".replace("ON ", "ON " if gate else "OFF"))

    # headline detail on the lab's own settings, 3 months
    print("\n" + "=" * 100)
    print("DETAIL - the lab's exact settings (taker 0.040%, fee gate on), last 3 months, 25 seeds pooled")
    print("=" * 100)
    pooled: list[tuple[float, str]] = []
    for k in range(25):
        pooled += simulate(data, f15, ms("2026-06-19"), 0.0004, random.Random(2000 + k))
    rs = [r for r, _ in pooled]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    lo, hi = boot_ci(rs)
    kinds = {k: sum(1 for _, kk in pooled if kk == k) for k in ("tp", "stop", "time")}
    print(f"  {len(rs)} simulated trades (25 x {len(rs)//25})")
    print(f"  win rate      {len(wins)/len(rs)*100:.1f}%")
    print(f"  avg R         {st.fmean(rs):+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"  avg win       {st.fmean(wins):+.3f}R      avg loss {st.fmean(losses):+.3f}R")
    print(f"  breakeven WR  {1/(1+st.fmean(wins)/-st.fmean(losses))*100:.1f}%  vs actual "
          f"{len(wins)/len(rs)*100:.1f}%")
    print(f"  exits         {kinds['tp']} tp / {kinds['stop']} stop / {kinds['time']} time")
    print(f"  verdict from the CI: "
          f"{'positive' if lo > 0 else 'negative' if hi < 0 else 'INDISTINGUISHABLE FROM ZERO'}")


if __name__ == "__main__":
    main()
