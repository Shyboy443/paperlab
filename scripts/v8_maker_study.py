"""V8 maker study: would LIMIT (maker) orders fix V8's costs? Same signals, different execution.

    python scripts/v8_maker_study.py [--days 60]

1. The V8 signals of every family x coin over the window are recorded from the live engine (a gate that records and
   skips, so every signal is seen).
2. A small execution simulator on the same 1m bars replays them one position per bot at a time (1-bar cooldown after
   an order, the 45-minute time stop), in four ways:

   MKT_MKT   market entry at the first 1m open >= close + 60 s, market exits      (today's rule: the check case)
   LMT_MKT   post-only LIMIT entry at the signal price (or the bid/ask if the market already moved past it), resting
             5 minutes; it fills only when a bar trades THROUGH it by 0.5 bp; unfilled = no trade; market exits
   LMT_LTP   limit entry as above + the 1.5R target as a resting LIMIT (fills only on a trade-through)
   MKT_LTP   market entry + limit target

   Stops and time exits are always market orders. Fees: Bybit linear taker 0.055%, maker 0.020%; every taker fill also
   pays a 1 bp half-spread. A bar that touches both stop and target counts as a STOP (conservative). Results are in R
   of the planned risk (|signal price - stop|).

CHECK: MKT_MKT should land near the engine's own BASE (-0.28 R/trade in docs/V8_EXIT_STUDY.json); if it does not,
the simulator is not trusted and nothing is concluded.
DECISION RULE (fixed before running): a maker variant is worth building only if its net R per trade beats MKT_MKT in
BOTH halves of the window; it makes V8 profitable only if its net R per trade is also > 0 in both halves.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))

DAY = 86_400_000
MIN = 60_000
TAKER, MAKER, HS, THROUGH = 0.00055, 0.00020, 0.0001, 0.00005
REST_MS, HOLD_MS, COOLDOWN_MS = 5 * MIN, 45 * MIN, 5 * MIN
VARIANTS = {"MKT_MKT": (False, False), "LMT_MKT": (True, False), "LMT_LTP": (True, True), "MKT_LTP": (False, True)}


def record_signals(coin: str, start: int, end: int, rules_raw: dict) -> tuple[list, dict]:
    import dataclasses
    from app.ai.jev.gate import GateVerdict
    from app.backtest.replay import ReplayEngine
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.bybit_market import kline_candle
    from app.strategies.v8.arena import load_v8_scalpers
    symbol = coin + "USDT"
    f = PROJECT / "data" / "v8_exit_study" / f"{symbol}-{start}-{end}.json"
    if f.exists():
        raw = json.loads(f.read_text())
    else:
        from app.backtest import bybit_archive as bb
        f.parent.mkdir(parents=True, exist_ok=True)
        raw = bb.klines(symbol, "1", start - v8.WARMUP_DAYS * DAY, end)
        f.write_text(json.dumps(raw))
    tape = [kline_candle(symbol, r, "historical") for r in raw if int(r[0]) + MIN <= end]
    rules = {s: MarketRules(**r) for s, r in rules_raw.items()}
    sigs: dict[str, list] = {}
    for sid, base_cls in load_v8_scalpers().items():
        got: list = []

        def gate(sig, g, got=got):
            got.append((int(g["ts"]), sig.side, float(sig.entry_price), float(sig.stop), float(sig.take_profits[0].price)))
            return GateVerdict(0.0, {"gate_action": "SKIP", "gate_reason": "recorded"})
        settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
        eng = ReplayEngine(settings, [symbol], rules={symbol: rules[symbol]}, seed=7, execution=v8.EXECUTION_V8, fees=FEES_V6,
                           fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                           max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None)
        eng.run(base_cls.for_class("SCALP"), tape, since_ms=start, leverage=20, signal_tf="5m", only_symbol=symbol)
        sigs[f"{sid}-{coin}"] = sorted(got)
    bars = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in raw if start <= int(r[0]) and int(r[0]) + MIN <= end + DAY]
    return bars, sigs


def simulate(bars: list, signals: list, limit_entry: bool, limit_tp: bool) -> list[dict]:
    opens = [b[0] for b in bars]
    out, free_at, cool_until = [], 0, 0
    for ts, side, price, stop, target in signals:
        d = 1 if side == "long" else -1
        risk = abs(price - stop)
        decide = ts + 1 + MIN
        if risk <= 0 or decide < free_at or ts < cool_until:
            continue
        i = bisect.bisect_left(opens, decide)
        if i >= len(bars):
            break
        cool_until = ts + 1 + COOLDOWN_MS                     # one 5m bar after an ORDER (filled or not)
        if not limit_entry:
            fill, fee_in, j = bars[i][1] * (1 + d * HS), TAKER, i
        else:
            o = bars[i][1]
            lim = min(price, o * (1 - HS)) if d == 1 else max(price, o * (1 + HS))   # post-only: never marketable
            fill = None
            for j in range(i, len(bars)):
                if bars[j][0] >= decide + REST_MS:
                    break
                if (d == 1 and bars[j][3] <= lim * (1 - THROUGH)) or (d == -1 and bars[j][2] >= lim * (1 + THROUGH)):
                    fill, fee_in = lim, MAKER
                    break
            if fill is None:
                out.append({"ts": ts, "filled": False})
                continue
        if (fill - stop) * d <= 0:                            # already through the stop
            out.append({"ts": ts, "filled": False})
            continue
        exit_px = exit_fee = kind = None
        k = j + 1
        while k < len(bars):
            _, o, hi, lo, c = bars[k]
            if bars[k][0] >= bars[j][0] + HOLD_MS:
                exit_px, exit_fee, kind = o * (1 - d * HS), TAKER, "time"
                break
            if (d == 1 and lo <= stop) or (d == -1 and hi >= stop):
                px = min(o, stop) if d == 1 else max(o, stop)
                exit_px, exit_fee, kind = px * (1 - d * HS), TAKER, "stop"
                break
            if limit_tp:
                if (d == 1 and hi >= target * (1 + THROUGH)) or (d == -1 and lo <= target * (1 - THROUGH)):
                    exit_px, exit_fee, kind = target, MAKER, "tp"
                    break
            elif (d == 1 and hi >= target) or (d == -1 and lo <= target):
                px = max(o, target) if d == 1 else min(o, target)
                exit_px, exit_fee, kind = px * (1 - d * HS), TAKER, "tp"
                break
            k += 1
        if exit_px is None:
            break
        gross = (exit_px - fill) * d
        cost = fee_in * fill + exit_fee * exit_px
        out.append({"ts": ts, "filled": True, "r": (gross - cost) / risk, "gross_r": gross / risk, "exit": kind})
        free_at = bars[k][0] + MIN
    return out


def run_coin(args):
    coin, start, end, rules_raw = args
    bars, sigs = record_signals(coin, start, end, rules_raw)
    res = []
    for bot, s in sigs.items():
        for name, (le, lt) in VARIANTS.items():
            res.append({"bot": bot, "variant": name, "signals": len(s), "trades": simulate(bars, s, le, lt)})
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    from app.competition import v8_config as v8
    from v8_freeze import rules_and_intervals
    end = int(time.time() * 1000) // DAY * DAY
    start = end - args.days * DAY
    mid = (start + end) // 2
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
        rows = [r for part in pool.map(run_coin, [(c, start, end, rules_raw) for c in v8.COINS]) for r in part]
    report = {"window": {"from": time.strftime("%Y-%m-%d", time.gmtime(start / 1000)),
                         "to": time.strftime("%Y-%m-%d", time.gmtime(end / 1000))}, "variants": {}}
    for name in VARIANTS:
        tr = [t for r in rows if r["variant"] == name for t in r["trades"]]
        done = [t for t in tr if t["filled"]]
        h = [[t["r"] for t in done if (t["ts"] >= mid) == bool(x)] for x in (0, 1)]
        exits: dict[str, int] = {}
        for t in done:
            exits[t["exit"]] = exits.get(t["exit"], 0) + 1
        report["variants"][name] = {
            "orders": len(tr), "fill_rate": round(len(done) / len(tr), 3) if tr else None, "trades": len(done),
            "win_rate": round(sum(1 for t in done if t["r"] > 0) / len(done), 3) if done else None,
            "avg_R": round(statistics.fmean(t["r"] for t in done), 4) if done else None,
            "avg_gross_R": round(statistics.fmean(t["gross_r"] for t in done), 4) if done else None,
            "avg_R_half1": round(statistics.fmean(h[0]), 4) if h[0] else None,
            "avg_R_half2": round(statistics.fmean(h[1]), 4) if h[1] else None,
            "net_R_total": round(sum(t["r"] for t in done), 1), "exits": exits}
    base = report["variants"]["MKT_MKT"]
    better = [n for n, v in report["variants"].items() if n != "MKT_MKT"
              and v["avg_R_half1"] > base["avg_R_half1"] and v["avg_R_half2"] > base["avg_R_half2"]]
    report["beats_market_in_both_halves"] = better
    report["profitable_in_both_halves"] = [n for n in better if report["variants"][n]["avg_R_half1"] > 0 and report["variants"][n]["avg_R_half2"] > 0]
    (PROJECT / "docs" / "V8_MAKER_STUDY.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
