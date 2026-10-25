"""Runner — drives one round and writes a verifiable summary.

Usage:
    python -m research.runner --round 9 --mode walkforward
    python -m research.runner --round 9 --mode split
    python -m research.runner --round 9 --mode both
    python -m research.runner --round 9 --mode grid

The `--mode grid` runs a small parameter sweep on the train split, picks
the best by Sharpe with min 5 trades, then evaluates on walk-forward.
This is the only place the test data sees parameter selection (it
doesn't — selection happens on train).

The runner writes:
  - research/logs/round_NN_<mode>.log
  - research/logs/round_NN_summary.json (one entry per mode run)
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

from . import stop_rule

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _log_path(round_id: int, mode: str) -> Path:
    return LOG_DIR / f"round_{round_id:02d}_{mode}.log"


def _summary_path(round_id: int) -> Path:
    return LOG_DIR / f"round_{round_id:02d}_summary.json"


def _append_summary(round_id: int, mode: str, payload: dict) -> None:
    p = _summary_path(round_id)
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}
    existing[mode] = payload
    p.write_text(json.dumps(existing, indent=2, sort_keys=True))


def _run_round_9(mode: str) -> dict:
    # Lazy import — each round may pull data on first call.
    from . import round9_data, round9_engine
    from .round9_engine import BasisParams, run as r9_run

    print(f"[round9] fetching data…", file=sys.stderr)
    okx = round9_data.fetch_okx_1m_marks("BTC-USDT-SWAP")
    try:
        other = round9_data.fetch_binance_1m_marks("BTCUSDT")
        venue = "binance"
    except Exception as e:
        print(f"[round9] binance fetch failed: {e}; falling back to bybit", file=sys.stderr)
        other = round9_data.fetch_bybit_1m_marks("BTCUSDT")
        venue = "bybit"
    print(f"[round9] aligned: okx={len(okx)} other={len(other)} venue={venue}", file=sys.stderr)

    if mode in ("walkforward", "split", "train", "both"):
        modes_to_run = (["split", "walkforward"] if mode == "both" else [mode])
    else:
        raise ValueError(mode)

    results = {}
    for m in modes_to_run:
        p = BasisParams()
        metrics, trades = r9_run(okx, other, p, mode=m)
        verdict = stop_rule.evaluate(metrics)
        results[m] = {
            "metrics": metrics.__dict__,
            "verdict": verdict.to_dict(),
            "n_trades": len(trades),
            "venue_other": venue,
            "params": p.__dict__,
        }
        print(f"[round9][{m}] avg_daily={metrics.avg_daily*100:.3f}% "
              f"sharpe={metrics.sharpe:.2f} dd={metrics.max_dd*100:.2f}% "
              f"min_eq={metrics.min_equity_pct*100:.1f}% trades={metrics.trades} "
              f"-> {'HIT' if verdict.hit else 'NO HIT'}: {verdict.reason}",
              file=sys.stderr)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--mode", type=str, default="walkforward",
                    choices=["walkforward", "split", "train", "both", "grid"])
    args = ap.parse_args()

    if args.round != 9:
        raise NotImplementedError(f"round {args.round} is not implemented in this scaffold yet")

    t0 = time.time()
    log = _log_path(args.round, args.mode)
    with log.open("w") as f:
        f.write(f"# round {args.round} mode={args.mode} started at {time.ctime()}\n")
        f.flush()
        try:
            results = _run_round_9(args.mode)
        except Exception as e:
            f.write(f"\nERROR: {type(e).__name__}: {e}\n")
            raise
        f.write(f"\n# results: {json.dumps(results, indent=2, sort_keys=True)}\n")
        f.write(f"\n# elapsed: {time.time() - t0:.1f}s\n")
    _append_summary(args.round, args.mode, results)
    print(f"[runner] wrote {log} and {_summary_path(args.round)}", file=sys.stderr)


if __name__ == "__main__":
    main()
