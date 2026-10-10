"""V11.5 LIMIT-ENTRY study: does Renata's 1-hour reversal signal pay once the entry is a resting LIMIT (maker) order?
(operator, 2026-10-10: "test Renata's signal with limit order entries", after docs/V115_REVERSAL_STUDY.json found that
every taker variant loses to the 15 bp round trip.)

    python scripts/v115_limit_study.py --dir /data/v115 --out docs/V115_LIMIT_STUDY.json

Uses the candidate trades and 120-minute 1m paths built by scripts/v115_reversal_study.py (same windows, same coins).

PRE-REGISTERED (2026-10-10, before any run; selection on DEV only)

Entry: at the decision (the hour close) a post-only LIMIT is placed `offset` x ATR14(1h) better than the decision
price (below it for a long, above for a short; offset 0 = at the decision price). It FILLS only if a later minute
trades THROUGH it (a long fills when a minute's low is below the limit, a short when the high is above it -- a touch
is not enough, the queue may not reach us), at the limit price, paying the MAKER fee (2 bp) and no slippage. It is
cancelled after `ttl` minutes; an unfilled order is no trade and costs nothing.
Stop: 2 x ATR14(1h) (clamped 1.5-8%) from the FILL; the fill minute itself may already stop the trade (stop first).
Exits from the fill: time exit after `hold` minutes (taker 5.5 bp + 2 bp), a fixed 1R or 2R target (maker, resting),
or the operator's ladder (maker take-profits); stops are taker fills at the stop or the gap open.
Signals: V11.4's own (2 per side, every hour) and the strength filter (only moves >= 1.5 or >= 2.5 ATR units).

DEV grid: signal {V11.4, z>=1.5, z>=2.5} x offset {0, 0.25, 0.5} x ttl {15, 30} x exit {time, 1R, 2R, ladder} x
hold {30, 58, 90} -- the best DEV expectancy with >= 150 DEV trades is THE candidate, plus the best with offset 0
(the plain "post at the price" version) for comparison.
PASS: expectancy (R/trade, costs included) > 0 in TEST, HOLD1 and HOLD2; profit factor over the three >= 1.10;
>= 150 trades in each; still > 0 with every cost doubled. If nothing passes: Renata stays V11.4.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.v115_reversal_study import COL, EPS, H, MAKER, SLIP, TAKER, load, select, split_dev_test, stats  # noqa: E402


def fills(paths: np.ndarray, side: np.ndarray, limit: np.ndarray, ttl: int) -> np.ndarray:
    """The minute each limit fills (trades THROUGH it), or -1. `limit` is relative to the path's reference (<0 for a
    long below the price, >0 for a short above it)."""
    if len(side) == 0:
        return np.zeros(0, int)
    lo, hi = paths[:, :ttl, 2], paths[:, :ttl, 1]
    through = np.where(side[:, None] > 0, lo < limit[:, None] - EPS, hi > limit[:, None] + EPS)
    return np.where(through.any(1), through.argmax(1), -1)


def simulate_limit(paths: np.ndarray, side: np.ndarray, atr_pct: np.ndarray, offset: float, ttl: int, hold: int,
                   target: str, stop_atr: float = 2.0, cost_mult: float = 1.0) -> tuple[np.ndarray, int]:
    """(R per FILLED trade after costs, orders placed). Prices re-based to the fill."""
    n = len(side)
    limit = -side * offset * atr_pct                                    # better than the reference, in the trade's favour
    f = fills(paths, side, limit, ttl)
    ok = f >= 0
    if not ok.any():
        return np.zeros(0), n
    p, s, fi, lim = paths[ok], side[ok], f[ok], limit[ok]
    stop = np.clip(stop_atr * atr_pct[ok], 0.015, 0.08)
    m = len(s)
    # the path from the fill minute on, re-based to the fill price; past the stored 120 minutes the last minute repeats
    cols = np.minimum(fi[:, None] + np.arange(hold)[None, :], H - 1)
    seg = p[np.arange(m)[:, None], cols, :]                              # (m, hold, 4) relative to the reference
    reb = (1.0 + seg) / (1.0 + lim[:, None, None]) - 1.0                 # relative to the fill price
    o, hi_, lo, cl = (reb[:, :, k] for k in range(4))
    o = o.copy()
    o[:, 0] = 0.0                                                        # we are filled inside the fill minute
    fav = np.where(s[:, None] > 0, hi_, -lo)
    adv = np.where(s[:, None] > 0, lo, -hi_)
    o_dir = o * s[:, None]
    cl_dir = cl * s[:, None]
    entry_cost = MAKER * cost_mult
    if target == "ladder":
        from scripts.v115_reversal_study import _ladder
        return _ladder(o_dir, fav, adv, cl_dir, stop, hold, cost_mult) - entry_cost / stop, n
    tgt = {"time": None, "1R": 1.0, "2R": 2.0}[target]
    stop_hit = adv <= -stop[:, None]
    first_stop = np.where(stop_hit.any(1), stop_hit.argmax(1), hold)
    if tgt is not None:
        t_hit = fav >= tgt * stop[:, None] - EPS
        first_tgt = np.where(t_hit.any(1), t_hit.argmax(1), hold)
    else:
        first_tgt = np.full(m, hold)
    stop_first = (first_stop <= first_tgt) & (first_stop < hold)
    tgt_first = (first_tgt < first_stop) & (first_tgt < hold)
    timed = ~(stop_first | tgt_first)
    r = np.empty(m)
    rows = np.arange(m)
    so = o_dir[rows, np.minimum(first_stop, hold - 1)]
    stop_px = np.minimum(-stop, so)
    r[stop_first] = (stop_px[stop_first] - (TAKER + SLIP) * cost_mult) / stop[stop_first]
    if tgt is not None:
        r[tgt_first] = (tgt * stop[tgt_first] - MAKER * cost_mult) / stop[tgt_first]
    r[timed] = (cl_dir[timed, hold - 1] - (TAKER + SLIP) * cost_mult) / stop[timed]
    return r - entry_cost / stop, n


SIGNALS = {"V11.4 (2 per side)": {"per_side": 2}, "z>=1.5": {"per_side": 2, "z_min": 1.5},
           "z>=2.5": {"per_side": 2, "z_min": 2.5}}


def run_config(d: dict, sig: str, offset: float, ttl: int, hold: int, target: str, cost_mult: float = 1.0):
    idx = select(d["meta"], SIGNALS[sig])
    if len(idx) == 0:
        return np.zeros(0), 0
    m = d["meta"][idx]
    return simulate_limit(d["paths"][idx], m[:, COL["side"]], m[:, COL["atr_pct"]], offset, ttl, hold, target,
                          cost_mult=cost_mult)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=str(PROJECT / "docs" / "V115_LIMIT_STUDY.json"))
    args = ap.parse_args()
    t0 = time.time()
    root = Path(args.dir)
    dev, test = split_dev_test(load(root / "main.npz"))
    wins = {"DEV": dev, "TEST": test, "HOLD1": load(root / "hold1.npz"), "HOLD2": load(root / "hold2.npz")}
    grid = list(itertools.product(SIGNALS, (0.0, 0.25, 0.5), (15, 30), (30, 58, 90), ("time", "1R", "2R", "ladder")))
    dev_rows = []
    for sig, off, ttl, hold, tgt in grid:
        r, placed = run_config(dev, sig, off, ttl, hold, tgt)
        dev_rows.append({"signal": sig, "offset_atr": off, "ttl_min": ttl, "hold_min": hold, "exit": tgt,
                         "dev": {**stats(r, dev["days"]), "fill_rate": round(len(r) / placed, 3) if placed else None}})
    eligible = [x for x in dev_rows if x["dev"].get("trades", 0) >= 150]
    best = max(eligible, key=lambda x: x["dev"]["expectancy_r"])
    best_at_price = max([x for x in eligible if x["offset_atr"] == 0.0], key=lambda x: x["dev"]["expectancy_r"])
    report: dict[str, Any] = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                              "dev_grid": dev_rows, "candidates": {}}
    for label, cfg in (("best on DEV", best), ("best on DEV at the decision price (offset 0)", best_at_price)):
        args_ = (cfg["signal"], cfg["offset_atr"], cfg["ttl_min"], cfg["hold_min"], cfg["exit"])
        rows, oos, oos2 = {}, [], []
        for w, d in wins.items():
            r, placed = run_config(d, *args_)
            rows[w] = {**stats(r, d["days"]), "fill_rate": round(len(r) / placed, 3) if placed else None}
            if w != "DEV":
                oos.append(r)
                oos2.append(run_config(d, *args_, cost_mult=2.0)[0])
        oo, oo2 = np.concatenate(oos), np.concatenate(oos2)
        checks = {"test_pos": rows["TEST"].get("expectancy_r", -1) > 0, "hold1_pos": rows["HOLD1"].get("expectancy_r", -1) > 0,
                  "hold2_pos": rows["HOLD2"].get("expectancy_r", -1) > 0, "pf_1_10": (stats(oo, 1).get("pf") or 0) >= 1.10,
                  "trades_150": all(rows[w].get("trades", 0) >= 150 for w in ("TEST", "HOLD1", "HOLD2")),
                  "double_cost_pos": len(oo2) > 0 and oo2.mean() > 0}
        report["candidates"][label] = {"config": {k: cfg[k] for k in ("signal", "offset_atr", "ttl_min", "hold_min", "exit")},
                                       "windows": rows, "out_of_sample": stats(oo, 1), "checks": checks,
                                       "pass": all(checks.values())}
        print(f"{label}: {report['candidates'][label]['config']}", flush=True)
        for w, x in rows.items():
            print(f"   {w:5s} {x.get('expectancy_r', 0):+.3f} R/trade  n={x.get('trades', 0)}  fill {x.get('fill_rate')}  "
                  f"win {x.get('win_rate')}  PF {x.get('pf')}", flush=True)
        print(f"   PASS={all(checks.values())} {checks}", flush=True)
    # how much of the old loss the cheaper entry removes: V11.4's signal, decision-price limit, its own 58-minute ladder
    same = {}
    for w, d in wins.items():
        r, placed = run_config(d, "V11.4 (2 per side)", 0.0, 30, 58, "ladder")
        same[w] = {**stats(r, d["days"]), "fill_rate": round(len(r) / placed, 3) if placed else None}
    report["v11_4_with_limit_entry_only"] = same
    print("V11.4 unchanged except a limit entry at the decision price (30 min):",
          {w: (x.get("expectancy_r"), x.get("fill_rate")) for w, x in same.items()}, flush=True)
    passing = [k for k, x in report["candidates"].items() if x["pass"]]
    report["verdict"] = ("PASS: " + passing[0]) if passing else "NONE PASSES: limit entries do not rescue Renata's signal; she stays V11.4"
    report["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(report["verdict"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
