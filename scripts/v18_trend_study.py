"""V18 trend study: five new long-hold families, a fresh untouched holdout for V17's four passing families, the
families on 20 coins none of them was judged on, and a portfolio rule for the correlated drawdown.
(operator, 2026-10-08: "needs better improved strategies, keep going", after V17 passed 4 of 5 swing families.)

    BT_START=2024-10-01 BT_END=2026-10-01 BT_MID=2025-10-01 python scripts/v18_trend_study.py run --data-dir /data/bt2y
    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 python scripts/v18_trend_study.py run --data-dir /data/bt_hold_crypto
    BT_START=2022-01-01 BT_END=2023-07-01 BT_MID=2022-10-01 python scripts/v18_trend_study.py run --data-dir /data/bt_hold2
    python scripts/v18_trend_study.py analyze --main /data/bt2y --hold /data/bt_hold_crypto --hold2 /data/bt_hold2

PRE-REGISTERED (2026-10-08, written before any V18 run and before any run of anything on HOLD2; one shot, nothing is
re-tuned after the results)

Families: V17.1 Donchian 20/10, V17.2 trend MA, V17.3 7/3 breakout, V17.4 weekly momentum (unchanged from the V17
study) and V18.1 Turtle 55/20, V18.2 Donchian + BTC regime gate, V18.3 multi-horizon momentum vote, V18.4 Donchian
chandelier, V18.5 trend pullback -- exactly as in app/strategies/v17/swing.py and app/strategies/v18/trend.py.

Coins: the 10 MAJORS judged in V17 (BTC ETH SOL XRP DOGE ADA LINK AVAX LTC BNB) and the 20 OTHER coins of the /data/bt2y
universe (breadth check; a coin without data in a window simply has no trades there).

Book and costs exactly as V17: one continuous 1000 USDT book per coin and family and window, 1% risk per trade (V6
sizing), V6 execution and taker fees, real funding on every open position, halts off, 92 days of warm-up.

Windows: DEV 2024-10..2025-10 and TEST 2025-10..2026-10 (one book), HOLD1 2023-10..2024-10 (used by V16/V17 already)
and HOLD2 2022-01-01..2023-07-01 -- the 2022 bear market (LUNA, FTX). HOLD2 data was fetched for this study and no
strategy of this lab has ever been run on it.

Verdicts (sums over the 10 majors unless stated):
- V18 family PASS only if net > 0 in DEV, TEST, HOLD1 and HOLD2; profit factor >= 1.20 over all four; >= 6 of 10 majors
  net positive over all four; >= 100 trades; still net positive with the fees paid twice.
- V17 family CONFIRMED only if its HOLD2 net > 0 (its first truly out-of-sample year).
- BROAD (reported for every family): net > 0 over all four windows summed over the 20 OTHER coins.
- Portfolio: book A = every PASSING or CONFIRMED family on the 10 majors in one shared account at 0.25% risk per bot;
  book B = book A, but a new entry is skipped while 12 or more of its positions are already open on the same side.
  For each window the average daily return, max drawdown and worst day are reported. Book B becomes the live design
  only if its max drawdown is lower than book A's in at least 2 of the 3 windows (main, HOLD1, HOLD2) and its return
  in HOLD2 is not more than 25% below book A's; otherwise book A.
Families that pass become paper bots; the others do not run.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

V17 = ("V17.1", "V17.2", "V17.3", "V17.4")
V18 = ("V18.1", "V18.2", "V18.3", "V18.4", "V18.5")
FAMILIES = V17 + V18
MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "BNBUSDT")
BOOK = 1000.0
DAY = 86_400_000
RISK_SHARED = 0.0025
SAME_SIDE_CAP = 12


def universe(root: Path) -> list[str]:
    for p in (root / "archive" / "dataset.json", root / "universe.json"):
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return list(d.get("symbols") or d.get("universe"))
    return list(MAJORS)


def btc_daily(root: Path, a: int, b: int) -> dict[int, float]:
    """UTC day number -> BTC's last 1m close of that day, from the window's own archive."""
    import scripts.backtest_2y as bt
    out: dict[int, float] = {}
    for month in bt.months(a, b):
        d = bt.load_month(root, "BTCUSDT", month)
        if d is None:
            continue
        for t, c in zip(d["ts"], d["cols"][3]):
            out[t // DAY] = c
    return out


def run_one(job: tuple[str, str], data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    sid, sym = job
    import scripts.backtest_2y as bt
    from app.backtest.funding import FundingSchedule
    from app.backtest.replay import ReplayEngine
    from app.competition.v6_config import AGGRESSIVE_V6, EXECUTION_V6, FEES_V6, settings_v6
    from app.core.types import MarketRules
    from app.strategies.v17.swing import load_v17
    from app.strategies.v18.trend import load_v18
    root = Path(data_dir)
    v11 = json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))
    rules = {s: MarketRules(**r) for s, r in v11["rules"].items()}
    t0 = time.time()
    base = {**load_v17(), **load_v18()}[sid]
    cls = base.for_class("SWING")
    if sid == "V18.2":
        daily = {int(k): v for k, v in json.loads((root / "v18_btc_daily.json").read_text(encoding="utf-8")).items()}
        cls = type(cls.__name__, (cls,), {"btc_daily": daily, "__module__": cls.__module__})
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0,
                                   strategy_starting_balance=BOOK)
    eng = bt.thin(ReplayEngine)(settings, [sym], rules={sym: rules[sym]},
                                funding=FundingSchedule({sym: bt.funding_rows(root, sym, bt.START - bt.WARM_SINGLE, bt.END)}),
                                execution=EXECUTION_V6, sizing=bt.sizing_v6(rules), max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                                fees=FEES_V6, fee_source="schedule", leverage_policy="needed", cost_gate=None, seed=7)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bt.tape(root, [sym], None, bt.START - bt.WARM_SINGLE, bt.END), since_ms=bt.START, leverage=20,
                  signal_tf="4h", only_symbol=sym)
    paid: dict[str, float] = {}
    for f in res.fills:
        if f.kind == "funding":
            paid[f.position_id] = paid.get(f.position_id, 0.0) + f.realized_pnl
    trades = [(t.entry_ts, t.exit_ts, round(t.net + paid.get(t.position_id, 0.0), 6), round(t.r_multiple, 4), t.side,
               round(t.fees, 6), t.exit_kind) for t in res.trades if t.entry_ts >= bt.START]
    eq = [(t, round(e, 4)) for t, e in res.equity if t >= bt.START]
    daily = [(t, e) for i, (t, e) in enumerate(eq) if i == len(eq) - 1 or eq[i + 1][0] // DAY != t // DAY]
    out = {"family": sid, "symbol": sym, "start": bt.START, "end": bt.END, "mid": bt.MID, "trades": trades,
           "equity_daily": daily, "rejects": dict(res.rejects), "elapsed_s": round(time.time() - t0, 1)}
    p = root / "v18_results" / f"{sid}__{sym}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out), encoding="utf-8")
    return {"family": sid, "symbol": sym, "trades": len(trades), "net": round(sum(t[2] for t in trades), 2),
            "elapsed": out["elapsed_s"]}


def run(args: argparse.Namespace) -> int:
    import scripts.backtest_2y as bt
    root = Path(args.data_dir)
    bd = btc_daily(root, bt.START - bt.WARM_SINGLE - 60 * DAY, bt.END)
    (root / "v18_btc_daily.json").write_text(json.dumps({str(k): v for k, v in bd.items()}), encoding="utf-8")
    coins = [s for s in universe(root) if (root / "archive" / "klines" / s).exists()]
    todo = [(sid, sym) for sym in coins for sid in FAMILIES
            if args.force or not (root / "v18_results" / f"{sid}__{sym}.json").exists()]
    print(f"{len(todo)} runs over {len(coins)} coins, {args.workers} workers, {len(bd)} BTC days", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run_one, j, str(root)): j for j in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
                print(f"[{i}/{len(todo)}] {r['family']} {r['symbol']}: {r['trades']} trades net {r['net']:+.2f} "
                      f"({r['elapsed']:.0f}s, total {time.time() - t0:.0f}s)", flush=True)
            except Exception as exc:
                print(f"[{i}/{len(todo)}] {futs[f]} FAILED {type(exc).__name__}: {exc}", flush=True)
    return 0


def stats(trades: list) -> dict[str, Any]:
    wins = sum(t[2] for t in trades if t[2] > 0)
    losses = -sum(t[2] for t in trades if t[2] < 0)
    hold = [(t[1] - t[0]) / 3_600_000 for t in trades]
    return {"trades": len(trades), "net": round(sum(t[2] for t in trades), 2), "fees": round(sum(t[5] for t in trades), 2),
            "pf": round(wins / losses, 3) if losses else None,
            "win": round(sum(t[2] > 0 for t in trades) / len(trades), 3) if trades else None,
            "avg_hold_h": round(sum(hold) / len(hold), 1) if hold else None}


def load(root: Path) -> dict:
    out = {}
    for p in (root / "v18_results").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[(d["family"], d["symbol"])] = d
    return out


def portfolio(results: dict, families: list[str], cap: int | None) -> dict[str, Any]:
    """One shared account: every (family, major) bot risks RISK_SHARED of the account per trade (its 1000 USDT book's
    daily P&L scaled), and with `cap` a trade is skipped when `cap` positions are already open on its side. A skipped
    trade's P&L (marked daily) is removed; the bot then waits for its next signal."""
    scale = (RISK_SHARED / 0.01) / BOOK
    trades, day_pnl = [], {}
    for (sid, sym), d in results.items():
        if sid not in families or sym not in MAJORS:
            continue
        prev, deltas = BOOK, {}
        for t, e in d["equity_daily"]:
            deltas[t // DAY] = deltas.get(t // DAY, 0.0) + (e - prev)
            prev = e
        for tr in d["trades"]:
            trades.append((tr[0], tr[1], tr[4], (sid, sym)))
        day_pnl[(sid, sym)] = deltas
    trades.sort()
    kept: dict[tuple, list] = {}
    open_: list = []
    skipped = 0
    for a, b, side, bot in trades:
        open_ = [o for o in open_ if o[1] > a]
        if cap is not None and sum(1 for o in open_ if o[2] == side) >= cap:
            skipped += 1
            continue
        open_.append((a, b, side))
        kept.setdefault(bot, []).append((a // DAY, b // DAY))
    total: dict[int, float] = {}
    for bot, deltas in day_pnl.items():
        spans = kept.get(bot, [])
        for day, x in deltas.items():
            if any(lo <= day <= hi for lo, hi in spans):
                total[day] = total.get(day, 0.0) + x * scale
    days = sorted(total)
    if not days:
        return {"days": 0}
    full = range(days[0], days[-1] + 1)
    eq, peak, dd, worst = 1.0, 1.0, 0.0, 0.0
    for d in full:
        r = total.get(d, 0.0)
        worst = min(worst, r)
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    n = len(full)
    return {"days": n, "avg_daily_pct": round(100 * (eq ** (1 / n) - 1), 4), "total_pct": round(100 * (eq - 1), 1),
            "max_dd_pct": round(100 * dd, 1), "worst_day_pct": round(100 * worst, 2), "skipped_trades": skipped,
            "bots": len(day_pnl)}


def analyze(args: argparse.Namespace) -> int:
    M, H1, H2 = load(Path(args.main)), load(Path(args.hold)), load(Path(args.hold2))
    report: dict[str, Any] = {}
    for sid in FAMILIES:
        per = {"dev": [], "test": [], "hold1": [], "hold2": []}
        coins: dict[str, Any] = {}
        other_net = 0.0
        for sym in sorted({s for (f, s) in list(M) + list(H1) + list(H2) if f == sid}):
            m, h1, h2 = M.get((sid, sym)), H1.get((sid, sym)), H2.get((sid, sym))
            parts = {"dev": [t for t in m["trades"] if t[0] < m["mid"]] if m else [],
                     "test": [t for t in m["trades"] if t[0] >= m["mid"]] if m else [],
                     "hold1": h1["trades"] if h1 else [], "hold2": h2["trades"] if h2 else []}
            every = [t for v in parts.values() for t in v]
            if sym in MAJORS:
                for k, v in parts.items():
                    per[k] += v
                coins[sym] = {k: stats(v) for k, v in parts.items()} | {"all": stats(every)}
            else:
                other_net += sum(t[2] for t in every)
        every = [t for v in per.values() for t in v]
        s = {k: stats(v) for k, v in per.items()} | {"all": stats(every)}
        positive = sum(1 for c in coins.values() if c["all"]["net"] > 0)
        double_cost = round(s["all"]["net"] - s["all"]["fees"], 2)
        checks = {"dev_net": s["dev"]["net"] > 0, "test_net": s["test"]["net"] > 0, "hold1_net": s["hold1"]["net"] > 0,
                  "hold2_net": s["hold2"]["net"] > 0, "pf_1_20": (s["all"]["pf"] or 0) >= 1.20,
                  "six_coins": positive >= 6, "100_trades": s["all"]["trades"] >= 100,
                  "double_fees_positive": double_cost > 0}
        verdict = ("CONFIRMED" if checks["hold2_net"] else "NOT CONFIRMED") if sid in V17 else \
                  ("PASS" if all(checks.values()) else "FAIL")
        report[sid] = {"verdict": verdict, "checks": checks, "coins_positive": positive, **s,
                       "net_with_double_fees": double_cost, "broad": other_net > 0, "other_coins_net": round(other_net, 2),
                       "coins": coins}
        print(f"{sid} {verdict:13s} dev {s['dev']['net']:+6.0f} test {s['test']['net']:+6.0f} hold1 {s['hold1']['net']:+6.0f} "
              f"hold2 {s['hold2']['net']:+6.0f}  pf {s['all']['pf']}  trades {s['all']['trades']}  "
              f"hold {s['all']['avg_hold_h']}h  majors+ {positive}/10  2x fees {double_cost:+.0f}  "
              f"other20 {other_net:+.0f}", flush=True)
    live = [sid for sid in FAMILIES if report[sid]["verdict"] in ("PASS", "CONFIRMED")]
    books: dict[str, Any] = {}
    for name, cap in (("A", None), ("B", SAME_SIDE_CAP)):
        books[name] = {w: portfolio(R, live, cap) for w, R in (("main", M), ("hold1", H1), ("hold2", H2))}
        for w, r in books[name].items():
            print(f"book {name} {w:5s}: {r}", flush=True)
    lower_dd = sum(1 for w in ("main", "hold1", "hold2")
                   if books["B"][w].get("max_dd_pct", 99) < books["A"][w].get("max_dd_pct", 99))
    ra, rb = books["A"]["hold2"].get("avg_daily_pct", 0), books["B"]["hold2"].get("avg_daily_pct", 0)
    choose = "B" if lower_dd >= 2 and (ra <= 0 or rb >= 0.75 * ra) else "A"
    print(f"live families: {live}; book {choose} (B lower DD in {lower_dd}/3 windows)", flush=True)
    Path(args.out).write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                                          "families": report, "live_families": live, "books": books,
                                          "chosen_book": choose}, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data-dir", required=True)
    r.add_argument("--workers", type=int, default=24)
    r.add_argument("--force", action="store_true")
    a = sub.add_parser("analyze")
    a.add_argument("--main", required=True)
    a.add_argument("--hold", required=True)
    a.add_argument("--hold2", required=True)
    a.add_argument("--out", default=str(PROJECT / "docs" / "V18_TREND_STUDY.json"))
    args = ap.parse_args()
    return {"run": run, "analyze": analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
