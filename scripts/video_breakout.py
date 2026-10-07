"""Run the strategy from https://www.youtube.com/watch?v=vVT6vzpRB3o.

    python scripts/video_breakout.py backtest --start 2022-01-01 --end 2026-01-01
    python scripts/video_breakout.py paper --once
    python scripts/video_breakout.py paper
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.video_breakout.data import fetch_history, recent_bars, utc_ms
from app.video_breakout.engine import BreakoutBot, Config, ENTRY_BARS, STEP, backtest
from app.video_breakout.runtime import atomic_json, paper_step, state_lock


def write_csv(path: Path, rows: list[dict]) -> None:
    if rows:
        keys = list(dict.fromkeys(k for row in rows for k in row))
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, keys)
            writer.writeheader()
            writer.writerows(rows)
    else:
        path.write_text("", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("backtest", "paper"))
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--allocation", type=float, default=1.0)
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--start", default="2022-01-01", help="Inclusive UTC date")
    ap.add_argument("--end", default="2026-01-01", help="Exclusive UTC date")
    ap.add_argument("--output", type=Path, default=PROJECT / "research/video_breakout/output")
    ap.add_argument("--cache", type=Path, default=PROJECT / "data/video_breakout/archive")
    ap.add_argument("--state", type=Path, default=PROJECT / "data/video_breakout/paper.json")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll-seconds", type=float, default=10.0)
    ap.add_argument("--max-lateness-seconds", type=float, default=30.0)
    args = ap.parse_args()
    cfg = Config(args.symbol.upper(), args.balance, args.allocation, args.fee_bps, args.slippage_bps)
    if args.mode == "backtest":
        start, end = utc_ms(args.start), utc_ms(args.end)
        if end <= start or start % STEP or end % STEP:
            raise ValueError("An increasing UTC-aligned date range is required")
        print("Downloading checksum-verified spot 4h bars (including 7-day warmup)...", flush=True)
        bars, files = fetch_history(cfg.symbol, start - ENTRY_BARS * STEP, end, args.cache)
        frozen = {"strategy": "4h long/cash; prior 42 highs / prior 18 lows; next open",
                  "config": asdict(cfg), "start": args.start, "end_exclusive": args.end,
                  "data_files": files, "engine_sha256": hashlib.sha256(
                      (PROJECT / "app/video_breakout/engine.py").read_bytes()).hexdigest()}
        frozen["source_sha256"] = {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
                                   for name in ("app/video_breakout/engine.py", "app/video_breakout/data.py",
                                                "app/core/types.py", "app/video_breakout/runtime.py", "scripts/video_breakout.py")}
        identity = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()[:16]
        out = args.output / identity
        out.mkdir(parents=True, exist_ok=True)
        # Freeze inputs and source identity BEFORE evaluation.
        atomic_json(out / "manifest.json", frozen)
        bot = backtest(bars, cfg, start_ms=start, end_ms=end)
        summary = {**bot.summary(), "run_id": identity, "start": args.start,
                   "end_exclusive": args.end, "bars": len(bars),
                   "source_video": "https://www.youtube.com/watch?v=vVT6vzpRB3o",
                   "fidelity": "Disclosed rules replicated; undisclosed sizing/costs and video dataset unavailable"}
        atomic_json(out / "summary.json", summary)
        for name in ("fills", "trades", "signals", "curve"):
            write_csv(out / (name + ".csv"), getattr(bot, name))
        report = (f"# Video breakout backtest\n\nRun `{identity}`. {cfg.symbol}, spot, 4h, "
                  f"{args.start} to {args.end} (exclusive).\n\n"
                  f"Net return: **{summary['net_return_pct']:.2f}%**. "
                  f"Maximum sampled drawdown: **{summary['max_drawdown_pct']:.2f}%**. "
                  f"Completed trades: **{len(bot.trades)}**. Verdict: **{summary['verdict']}**.\n\n"
                  "Uses only prior completed highs/lows and next-opening executions, long/cash, no leverage. "
                  f"Sizing assumption: {cfg.allocation:.0%} of cash. Cost assumptions: {cfg.fee_bps:g} bps fee "
                  f"and {cfg.slippage_bps:g} bps adverse slippage on each side.\n\n"
                  "The video's exact dates, exchange, sizing, numeric costs and complete acceptance gates "
                  "are unavailable. This run cannot reproduce or verify its quoted 384% return. "
                  "Known gates only: >40% drawdown fails; <50 completed trades is inconclusive. "
                  "4h close/open equity samples can understate intrabar drawdown. Open positions are marked "
                  "to market; final signals are not filled without a next bar.\n\n"
                  "See manifest.json for frozen source/data checksums, summary.json for metrics, "
                  "and CSVs for every signal, fill, trade and equity observation.\n")
        (out / "REPORT.md").write_text(report, encoding="utf-8")
        print(json.dumps(summary, indent=2))
        print(f"Results: {out}")
    else:
        if not 1 <= args.poll_seconds <= 60 or not 1 <= args.max_lateness_seconds <= 60:
            raise ValueError("Poll and maximum lateness must be 1..60 seconds")
        with state_lock(args.state):
            bot = (BreakoutBot.from_state(json.loads(args.state.read_text()), cfg)
                   if args.state.exists() else BreakoutBot(cfg))
            while True:
                observed, bars = recent_bars(cfg.symbol)
                status = paper_step(bot, observed, bars, int(args.max_lateness_seconds * 1000))
                atomic_json(args.state, bot.to_state())
                print(datetime.fromtimestamp(observed / 1000, timezone.utc).isoformat(),
                      json.dumps(status), flush=True)
                if args.once:
                    break
                time.sleep(args.poll_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Paper bot stopped; the last atomic checkpoint is preserved.")
    except Exception as exc:
        print(f"Stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
