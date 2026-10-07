"""V8 exit study: same entries, different exits -- is a TP1 / TP2 / TP3 ladder with stop moves better after costs?

    python scripts/v8_exit_study.py [--days 60]

Replays the last `days` of Bybit 1m bars (plus V8's warm-up) through the live engine for every V8 family x coin
(control bots: no Jev), once per exit variant. Entries are identical in every variant; only the exit changes:

    BASE      the live V8 rule: one target at 1.5R, the structural stop, a 45-minute time stop
    BE75      BASE, but once +0.75R is touched the stop moves to entry + costs (0.15%)
    LADDER3   (the operator's proposal) TP1 0.75R x 1/3 -> stop to entry + costs; TP2 1.5R x 1/3 -> stop to +0.75R;
              TP3 2.5R x 1/3
    LADDER2   TP1 1.0R x 1/2 -> stop to entry + costs; TP2 2.0R x 1/2
    TIGHT3    TP1 0.5R x 1/3 -> stop to entry + costs; TP2 1.0R x 1/3 -> stop to +0.5R; TP3 1.5R x 1/3

All keep the 45-minute time stop. Every exit is a taker fill at the price the engine walks through (with the observed
fee schedule); partial closes pay their own fees.

DECISION RULE (fixed before running): a variant replaces BASE only if its total net R beats BASE's in BOTH halves of
the window AND its profit factor is at least BASE's; if several do, the highest total net R wins. Otherwise BASE
stays. Win rate is reported but never decides.

The engine's 25% halt and daily-loss halt are OFF here (first run: every bot, in every variant, hit the 25% halt
inside the first month, so the variants stopped trading and their totals converged -- no comparison possible).
Every book is also reset to 20 USDT at each UTC midnight, so no variant stops trading or sizes from a shrunken book,
and results are in R (net result / the trade's initial risk).
"""
from __future__ import annotations

import argparse
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
COST_LOCK = 0.0015                    # "entry + costs": 2 x 0.055% taker + a little spread

VARIANTS = {
    "BASE": {"ladder": [(1.5, 1.0)], "locks": []},
    "BE75": {"ladder": [(1.5, 1.0)], "locks": [], "be_at": 0.75},
    "LADDER3": {"ladder": [(0.75, 1 / 3), (1.5, 1 / 3), (2.5, 1 / 3)], "locks": ["cost", 0.75]},
    "LADDER2": {"ladder": [(1.0, 0.5), (2.0, 0.5)], "locks": ["cost"]},
    "TIGHT3": {"ladder": [(0.5, 1 / 3), (1.0, 1 / 3), (1.5, 1 / 3)], "locks": ["cost", 0.5]},
}


def make_variant(base_cls, name: str):
    from app.core.types import ExitUpdate, TakeProfit
    spec = VARIANTS[name]
    ladder, locks, be_at = spec["ladder"], spec["locks"], spec.get("be_at")

    class Variant(base_cls):
        def on_candle(self, c, ctx):
            sigs = super().on_candle(c, ctx)
            for s in sigs:
                risk = abs(s.entry_price - s.stop)
                d = 1 if s.side == "long" else -1
                s.take_profits = [TakeProfit(s.entry_price + d * r * risk, f) for r, f in ladder]
                if be_at:
                    s.be_at_r = be_at
                elif locks:
                    s.be_at_r = ladder[0][0]            # the engine locks the instant TP1 prints; manage() adds costs
            return sigs

        def manage(self, pos, c, ctx):
            if pos.qty_initial <= 0:
                return None
            per = pos.initial_risk_usd / pos.qty_initial
            d = 1 if pos.side == "long" else -1
            done = len(ladder) - len(pos.take_profits)
            lock = None
            if (be_at and pos.be_done) or (locks and done >= 1):
                lock = pos.entry_price * (1 + d * COST_LOCK)
            if len(locks) > 1 and done >= 2:
                lock = pos.entry_price + d * locks[1] * per
            if lock is not None and (lock - pos.stop) * d > 0:
                return ExitUpdate(stop=lock, reason="be")
            return None
    Variant.__name__ = f"{base_cls.__name__}_{name}"
    return Variant


def run_coin(args):
    coin, start, end, rules_raw = args
    from app.backtest import bybit_archive as bb
    from app.backtest.replay import ReplayEngine
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.bybit_market import kline_candle
    from app.strategies.v8.arena import load_v8_scalpers
    symbol = coin + "USDT"
    cache = PROJECT / "data" / "v8_exit_study"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{symbol}-{start}-{end}.json"
    if f.exists():
        raw = json.loads(f.read_text())
    else:
        raw = bb.klines(symbol, "1", start - v8.WARMUP_DAYS * DAY, end)
        f.write_text(json.dumps(raw))
    tape = [kline_candle(symbol, r, "historical") for r in raw if int(r[0]) + 60_000 <= end]
    rules = {s: MarketRules(**r) for s, r in rules_raw.items()}
    out = []
    for sid, base_cls in load_v8_scalpers().items():
        for name in VARIANTS:
            cls = make_variant(base_cls, name)
            import dataclasses
            settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
            eng = ReplayEngine(settings, [symbol], rules={symbol: rules[symbol]}, seed=7, execution=v8.EXECUTION_V8,
                               fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                               max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
            res = eng.run(cls.for_class("SCALP"), tape, since_ms=start, leverage=20, signal_tf="5m", only_symbol=symbol,
                          reset_at=[start + k * DAY for k in range(1, (end - start) // DAY)])
            trades = [t for t in res.trades if t.entry_ts >= start and t.exit_kind != "end_of_run"]
            out.append({"bot": f"{sid}-{coin}", "variant": name,
                        "trades": [{"ts": t.entry_ts, "net": t.net, "r": t.r_multiple, "fees": t.fees, "exit": t.exit_kind}
                                   for t in trades]})
    return out


def summarize(rows: list[dict], mid: int) -> dict:
    res = {}
    for name in VARIANTS:
        tr = [t for r in rows if r["variant"] == name for t in r["trades"]]
        wins = [t["net"] for t in tr if t["net"] > 0]
        losses = [t["net"] for t in tr if t["net"] <= 0]
        halves = [sum(t["r"] for t in tr if (t["ts"] >= mid) == bool(h)) for h in (0, 1)]
        wr = [t["r"] for t in tr if t["r"] > 0]
        lr = [t["r"] for t in tr if t["r"] <= 0]
        exits: dict[str, int] = {}
        for t in tr:
            exits[t["exit"]] = exits.get(t["exit"], 0) + 1
        res[name] = {"trades": len(tr), "win_rate": round(len(wins) / len(tr), 3) if tr else None,
                     "net_R_total": round(sum(t["r"] for t in tr), 2), "avg_R": round(statistics.fmean(t["r"] for t in tr), 4) if tr else None,
                     "net_R_half1": round(halves[0], 2), "net_R_half2": round(halves[1], 2),
                     "avg_win_R": round(statistics.fmean(wr), 3) if wr else None, "avg_loss_R": round(statistics.fmean(lr), 3) if lr else None,
                     "net_usdt": round(sum(t["net"] for t in tr), 3), "fees": round(sum(t["fees"] for t in tr), 3),
                     "avg_win": round(statistics.fmean(wins), 4) if wins else None,
                     "avg_loss": round(statistics.fmean(losses), 4) if losses else None,
                     "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
                     "exits": exits}
    base = res["BASE"]
    ok = [n for n, r in res.items() if n != "BASE" and r["net_R_half1"] > base["net_R_half1"] and r["net_R_half2"] > base["net_R_half2"]
          and (r["profit_factor"] or 0) >= (base["profit_factor"] or 0)]
    return {"variants": res, "passing": ok, "decision": max(ok, key=lambda n: res[n]["net_R_total"]) if ok else "BASE"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    from app.competition import v8_config as v8
    from v8_freeze import rules_and_intervals
    end = int(time.time() * 1000) // DAY * DAY
    start = end - args.days * DAY
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
        rows = [r for part in pool.map(run_coin, [(c, start, end, rules_raw) for c in v8.COINS]) for r in part]
    report = {"window": {"from": time.strftime("%Y-%m-%d", time.gmtime(start / 1000)),
                         "to": time.strftime("%Y-%m-%d", time.gmtime(end / 1000)), "days": args.days},
              **summarize(rows, (start + end) // 2),
              "per_family": {sid: summarize([r for r in rows if r["bot"].startswith(sid + "-")], (start + end) // 2)["variants"]
                             for sid in ("V8.1", "V8.2", "V8.3")},
              "note": "control bots only (no Jev); same entries in every variant; net after fees, spread and funding"}
    (PROJECT / "docs" / "V8_EXIT_STUDY.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("window", "variants", "passing", "decision")}, indent=1))


if __name__ == "__main__":
    main()
