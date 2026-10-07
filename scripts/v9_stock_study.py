"""V9 stock bots: why they lose over two years, and do any pre-registered fixes help? (operator, 2026-10-06:
"analyze them as well, what happened during each, why did long term it got bad, and improve them").

    python scripts/v9_stock_study.py --data-dir /data/bt2y --workers 8     (on the server: the two-year archive)

THE DIAGNOSIS, measured before any fix was tried, on the two-year replay of all 30 bots without the 30% stop
(41,454 trades):
- All three families lose in every quarter, on both sides, at every time of day and on every symbol. It is not
  a market regime; the setups have no edge on stocks.
- Costs are not the cause: commission is about 18% of the loss.
- The loss is made in the first minutes:
  - trades stopped within 10 minutes: -0.66 to -0.79 R each, 17-23% of them winners;
  - trades that survive 30-45 minutes: +0.06 to +0.11 R each.
  The 0.25% minimum stop sits inside a large stock's normal 5-minute noise.
- Stops also cost more than 1 R (-1.14 to -1.19 R on average): the price gaps through them.

PRE-REGISTERED (2026-10-06, before any variant was run)

Data
- The Alpaca IEX 1m tape of scripts/backtest_2y.py, regular sessions.
- Window: 2024-10-01 .. 2026-10-01, with 30 days of warm-up. Year 1 ends at 2025-10-01.

Book
- 1000 USD, reset every day. Halts off. The live V9 sizing, fees, execution and 3x cap. Nothing held overnight.

Each variant changes ONE thing in all 30 live bots (the three families x 10 symbols):
- BASE      the live bots.
- STOP50    minimum stop 0.50% instead of 0.25% (out of the 5-minute noise; the size halves, the risk stays 1%).
- BUF50     the stop 0.5 ATR(5m) beyond the setup's extreme instead of 0.2.
- TGT1      take profit at 1.0 R instead of 1.5 R.
- HOLD90    a 90-minute time stop instead of 45; entries end 95 minutes before the close, so nothing is held overnight.
- TREND_DAY trade only with the session: a long only above the session's opening price, a short only below it.

Verdict
- Per family, a variant IMPROVES only if its net USD and its net R per trade beat BASE in BOTH years.
- An improving family stays aggressive only if it keeps at least half of BASE's trades.
- Improving changes may then be combined ONCE per family, and that combination must pass the same test.

COMBINATIONS (registered 2026-10-07 after the single changes, before they were run): each family's improving changes
together. TREND_DAY is left out of V9.3 because it kept under half the trades.
- COMBO_V9.1   STOP50 + BUF50 + TGT1 + HOLD90 + TREND_DAY
- COMBO_V9.2   STOP50 + TREND_DAY
- COMBO_V9.3   STOP50 + BUF50 + TGT1
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

from scripts.backtest_2y import DAY, END, MID, START, stock_tape  # noqa: E402

MIN = 60_000
OUT = PROJECT / "docs" / "V9_STOCK_STUDY.json"
VARIANTS: dict[str, dict[str, Any]] = {
    "BASE": {},
    "STOP50": {"params": {"min_stop_pct": 0.005}},
    "BUF50": {"params": {"stop_buffer_atr": 0.5}},
    "TGT1": {"params": {"target_r": 1.0}},
    "HOLD90": {"params": {"max_hold_min": 90.0}, "close_buffer_min": 95},
    "TREND_DAY": {"trend_day": True},
    "COMBO_V9.1": {"params": {"min_stop_pct": 0.005, "stop_buffer_atr": 0.5, "target_r": 1.0, "max_hold_min": 90.0},
                   "close_buffer_min": 95, "trend_day": True},
    "COMBO_V9.2": {"params": {"min_stop_pct": 0.005}, "trend_day": True},
    "COMBO_V9.3": {"params": {"min_stop_pct": 0.005, "stop_buffer_atr": 0.5, "target_r": 1.0}},
}


def variant_class(base: type, opts: dict[str, Any]) -> type:
    if not opts:
        return base
    body: dict[str, Any] = {"__module__": __name__}
    if opts.get("params"):
        body["Params"] = dataclasses.make_dataclass(
            "StudyParams", [(k, type(v), dataclasses.field(default=v)) for k, v in opts["params"].items()],
            bases=(base.Params,))
    buffer_min = opts.get("close_buffer_min")
    trend_day = bool(opts.get("trend_day"))

    def on_candle(self, c, ctx, _base=base.on_candle):
        t = c.close_time + 1
        s = self.session_at(t - 1) if self.session_at is not None else None
        if buffer_min is not None and (s is None or t > s[1] - buffer_min * MIN):
            return []
        sigs = _base(self, c, ctx)
        if trend_day and sigs:
            first = next((x for x in ctx.candles(c.symbol, "5m") if s is not None and x.open_time >= s[0]), None)
            if first is None:
                return []
            sigs = [x for x in sigs if getattr(x, "kind", "entry") != "entry"
                    or (x.side == "long" and c.close > first.open) or (x.side == "short" and c.close < first.open)]
        return sigs
    if buffer_min is not None or trend_day:
        body["on_candle"] = on_candle
    return type(base.__name__ + "Study", (base,), body)


def run(job: tuple[str, str, str], data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    name, sid, sym = job
    from app.live.stock_engine import StockReplayEngine as ReplayEngine
    from app.competition import v9_config as v9
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v9.stocks import load_v9_stock_scalpers
    root = Path(data_dir)
    rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
    bars, sessions = stock_tape(root, sym)
    cls = variant_class(load_v9_stock_scalpers()[sid], VARIANTS[name]).for_class("SCALP", session_at=sessions.at)
    eng = ReplayEngine(dataclasses.replace(v9.settings_v9(), strategy_halt_pct=1.0, daily_halt_pct=1.0), [sym],
                       rules={sym: rules[sym]}, seed=7, funding=None, execution=v9.EXECUTION_V9, fees=v9.FEES_V9,
                       fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bars, since_ms=START, leverage=v9.LEVERAGE_CAP, signal_tf="5m", only_symbol=sym,
                  reset_at=list(range(START + DAY, END, DAY)))
    eng.portfolio.closed_trades = deque()                       # free memory: only the summary is returned
    trades = [(t.entry_ts, t.net, t.r_multiple, t.fees, t.exit_kind) for t in res.trades if t.entry_ts >= START]
    return {"variant": name, "sid": sid, "symbol": sym, "trades": trades}


def summarize(trades: list[tuple]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, part in (("all", trades), ("year1", [t for t in trades if t[0] < MID]),
                        ("year2", [t for t in trades if t[0] >= MID])):
        n = len(part)
        if not n:
            out[label] = {"trades": 0}
            continue
        wins = sum(t[1] for t in part if t[1] > 0)
        losses = -sum(t[1] for t in part if t[1] < 0)
        out[label] = {"trades": n, "net_usd": round(sum(t[1] for t in part), 2), "net_r": round(sum(t[2] for t in part) / n, 4),
                      "win": round(sum(t[1] > 0 for t in part) / n, 3), "pf": round(wins / losses, 3) if losses else None,
                      "fees": round(sum(t[3] for t in part), 2)}
    return out


def main() -> int:
    from app.competition import v9_config as v9
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    names = [n for n in VARIANTS if not args.only or n in args.only.split(",")]
    jobs = [(n, sid, sym) for n in names for sid in ("V9.1", "V9.2", "V9.3") for sym in v9.SYMBOLS
            if not n.startswith("COMBO_") or n == f"COMBO_{sid}"]
    got: dict[tuple[str, str], list] = {}
    bots: dict[str, dict[str, Any]] = {}
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(run, j, args.data_dir) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            got.setdefault((r["variant"], r["sid"]), []).extend(r["trades"])
            bots[f"{r['variant']}|{r['sid']}-{r['symbol']}"] = summarize(r["trades"])["all"]
            if i % 30 == 0:
                print(f"{i}/{len(jobs)} ({time.time() - t0:.0f}s)", flush=True)
    results = {f"{n}|{sid}": summarize(tr) for (n, sid), tr in got.items()}
    if args.only and OUT.exists():                  # later variants join the earlier ones (same data, same BASE)
        prior = json.loads(OUT.read_text(encoding="utf-8"))
        results = {**(prior.get("results") or {}), **results}
        bots = {**(prior.get("bots") or {}), **bots}
    verdict = {}
    for sid in ("V9.1", "V9.2", "V9.3"):
        base = results[f"BASE|{sid}"]
        for n in VARIANTS:
            if n == "BASE" or f"{n}|{sid}" not in results:
                continue
            s = results[f"{n}|{sid}"]
            better = all(s[y].get("trades") and s[y]["net_usd"] > base[y]["net_usd"] and s[y]["net_r"] > base[y]["net_r"]
                         for y in ("year1", "year2"))
            pace = s["all"]["trades"] >= 0.5 * base["all"]["trades"]
            verdict[f"{n}|{sid}"] = ("IMPROVES" if better else "DOES NOT IMPROVE") + ("" if pace else " (under half the trades)")
    for k in sorted(results):
        s = results[k]
        print(f"{k:16s} " + "  ".join(f"{y}: {s[y].get('trades')} tr net {s[y].get('net_usd')} R {s[y].get('net_r')} "
                                      f"pf {s[y].get('pf')}" for y in ("year1", "year2")))
    print(json.dumps(verdict, indent=1))
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                               "results": results, "verdict": verdict, "bots": bots}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
