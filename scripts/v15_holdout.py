"""V15 holdout: does V15.1 (opening-candle trend) hold up on a year the study never touched?

    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 \
        python scripts/backtest_2y.py fetch9 --data-dir /data/bt_hold          (the holdout tape, once)
    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 \
        python scripts/v15_holdout.py --data-dir /data/bt_hold --workers 10

WHY A HOLDOUT. The pre-registered two-year study (scripts/v15_daytrade_study.py, docs/V15_DAYTRADE_STUDY.json)
failed every family. V15.1 made money in both years (+569 / +444 USD over 10 bots) but missed the bar (profit factor
1.06 / 1.05, 5 of 10 symbols positive). On five symbols it was positive in BOTH years: AMZN, GOOGL, MSFT, NVDA, TSLA.
Choosing those five after seeing both years is selection on the data, so it proves nothing by itself. This test
decides it on data neither year contains.

PRE-REGISTERED (2026-10-08, written and committed before the holdout tape was fetched)

- Candidate: V15.1 exactly as in app/strategies/v15/daytrade.py (no parameter changed), on AMZN, GOOGL, MSFT, NVDA,
  TSLA. The other five symbols are run too, for context only; they do not count.
- Holdout: 2023-10-01 .. 2024-10-01 Alpaca IEX 1m, regular sessions, 30 days of warm-up. The same engine, sizing,
  fees, execution, 3x cap and daily 1000 USD book reset as the study.
- PASS only if, over the five bots: net USD > 0, mean net R per trade > 0, profit factor >= 1.10, and at least 3 of
  the 5 symbols net positive.
- PASS: V15.1 goes live as a PAPER day-trading program on those five symbols. FAIL: no day trader goes live.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.v15_daytrade_study import run, summarize  # noqa: E402

CANDIDATE = ("AMZN", "GOOGL", "MSFT", "NVDA", "TSLA")
CONTEXT = ("SPY", "QQQ", "AAPL", "AMD", "META")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", default=str(PROJECT / "docs" / "V15_HOLDOUT.json"))
    args = ap.parse_args()
    bots: dict[str, list] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(run, ("V15.1", sym), args.data_dir) for sym in CANDIDATE + CONTEXT]
        for f in as_completed(futs):
            r = f.result()
            bots[r["symbol"]] = r["trades"]
            print(f"V15.1-{r['symbol']}: {len(r['trades'])} trades net {summarize(r['trades'])['all']['net_usd']}", flush=True)
    cand = [t for s in CANDIDATE for t in bots[s]]
    s = summarize(cand)["all"]
    positive = sum(1 for sym in CANDIDATE if summarize(bots[sym])["all"]["net_usd"] > 0)
    checks = {"net_usd": s["net_usd"] > 0, "net_r": s["net_r"] > 0, "profit_factor_1_10": (s.get("pf") or 0) >= 1.10,
              "three_of_five_positive": positive >= 3}
    verdict = {"pass": all(checks.values()), "checks": checks, "symbols_positive": positive}
    out = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__, "candidate": summarize(cand),
           "context": summarize([t for sym in CONTEXT for t in bots[sym]]),
           "bots": {sym: summarize(tr)["all"] for sym, tr in bots.items()}, "verdict": verdict}
    print("V15.1 holdout", "PASS" if verdict["pass"] else "FAIL", json.dumps(s), "symbols+", positive, "/5", flush=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
