"""V8 cost study (operator, 2026-10-02: "do the same analysis for the other bots and improve them").

Live diagnosis (v8x-b335a35a429b, CONTROL bots, 1,860 trades): fees are 0.20 R a trade (stops from 0.4%, a 0.11%
round trip); V8.3 is +2.85 USDT before fees and -16.66 after; V8.1 / V8.2 lose before fees too. The same levers as
Zap's study (docs/V11_ZAP_STUDY.json), on the 60-day window of docs/V8_EXIT_STUDY.json (its cached Bybit bars), CONTROL
bots of every family x coin, one change at a time:

    BASE        live: market exits filled at the bar path's prices (the shared engine), 0.4% minimum stop
    LEVEL       exits fill at their level (V11's engine without its pre-trade gap check): realism, not a tweak
    MAKER_TP    LEVEL + the 1.5R target as a resting LIMIT: maker fee, filled at the target on a 0.5 bp trade-through
    MINSTOP60   a 0.6% minimum stop (the target moves out with it: 1.5R)
    MINSTOP80   a 0.8% minimum stop
    NOHOLD      no time stop (stop / target / the daily reset)

DECISION RULE (fixed before running): per family (V8.1 / V8.2 / V8.3), a change passes only if its net R per trade beats
BASE's in BOTH halves. The passing changes are combined in one more run per family; the combination is adopted if it
beats every passing single change on the whole window, else the best single one is.

    python scripts/v8_cost_study.py
"""
from __future__ import annotations

import dataclasses
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
START, END = 1785369600000, 1790553600000          # the cached window of docs/V8_EXIT_STUDY.json
SINGLES = ("BASE", "LEVEL", "MAKER_TP", "MINSTOP60", "MINSTOP80", "NOHOLD")
TRADE_THROUGH = 0.00005


def engine_cls(level: bool, maker: bool):
    from app.backtest.replay import ReplayEngine
    if not level and not maker:
        return ReplayEngine
    from app.live.scan_engine import MAKER_TP_TRADE_THROUGH, ScanReplayEngine

    class LevelEngine(ScanReplayEngine):
        def _execute_pending(self, bar, meta, res):
            before = len(res.fills)
            ReplayEngine._execute_pending(self, bar, meta, res)           # V8 has no pre-trade gap check
            for f in res.fills[before:]:
                pos = self.portfolio.positions.get(f.position_id) if f.kind == "entry" else None
                if pos is not None and pos.take_profits and "tp1" not in pos.meta:
                    pos.meta.update(tp1=pos.take_profits[0].price, n_tps=len(pos.take_profits), tp1_lock=0.0)
                    if self.maker_tp:
                        d = 1 if pos.side == "long" else -1
                        for tp in pos.take_profits:
                            tp.price *= 1 + d * MAKER_TP_TRADE_THROUGH
    return LevelEngine


def mods_of(name: str) -> tuple[str, ...]:
    return () if name == "BASE" else tuple(name.split("+"))


def run_coin(args):
    coin, rules_raw, names = args
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.bybit_market import kline_candle
    from app.strategies.v8.arena import load_v8_scalpers
    symbol = coin + "USDT"
    raw = json.loads((PROJECT / "data" / "v8_exit_study" / f"{symbol}-{START}-{END}.json").read_text())
    tape = [kline_candle(symbol, r, "historical") for r in raw if int(r[0]) + 60_000 <= END]
    rules = {s: MarketRules(**r) for s, r in rules_raw.items()}
    out = []
    for sid, base_cls in load_v8_scalpers().items():
        for name in names:
            mods = mods_of(name)
            over = {}
            for m in mods:
                if m.startswith("MINSTOP"):
                    over["min_stop_pct"] = int(m[7:]) / 10000.0
            params = dataclasses.replace(base_cls.Params(), **over) if over else None
            nohold = "NOHOLD" in mods

            class Variant(base_cls):
                def __init__(self, p=None, _params=params):
                    super().__init__(p or _params)

                def on_candle(self, c, ctx, _nohold=nohold):
                    sigs = super().on_candle(c, ctx)
                    if _nohold:
                        for s in sigs:
                            s.max_hold_s = None
                    return sigs
            level = "LEVEL" in mods or "MAKER_TP" in mods
            Eng = engine_cls(level, "MAKER_TP" in mods)
            settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
            kw = dict(seed=7, execution=v8.EXECUTION_V8, fees=FEES_V6, fee_source="schedule",
                      sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                      max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
            if level:
                kw["maker_tp"] = "MAKER_TP" in mods
            eng = Eng(settings, [symbol], rules={symbol: rules[symbol]}, **kw)
            res = eng.run(Variant.for_class("SCALP"), tape, since_ms=START, leverage=20, signal_tf="5m",
                          only_symbol=symbol, reset_at=[START + k * DAY for k in range(1, (END - START) // DAY)])
            trades = [t for t in res.trades if t.entry_ts >= START and t.exit_kind not in ("end_of_run", "reset")]
            out.append({"family": sid, "coin": coin, "variant": name,
                        "trades": [{"ts": t.entry_ts, "net": t.net, "r": t.r_multiple, "fees": t.fees, "exit": t.exit_kind,
                                    "risk": abs(t.net / t.r_multiple) if t.r_multiple else 0.0} for t in trades]})
    return out


def summarize(rows: list[dict]) -> dict:
    mid = (START + END) // 2
    tr = [t for r in rows for t in r["trades"]]
    if not tr:
        return {"trades": 0}
    h = [[t["r"] for t in tr if (t["ts"] >= mid) == bool(i)] for i in (0, 1)]
    wins = sum(t["net"] for t in tr if t["net"] > 0)
    losses = -sum(t["net"] for t in tr if t["net"] <= 0)
    exits: dict[str, int] = {}
    for t in tr:
        exits[t["exit"]] = exits.get(t["exit"], 0) + 1
    return {"trades": len(tr), "win": round(sum(1 for t in tr if t["net"] > 0) / len(tr), 3),
            "net_r": round(statistics.fmean(t["r"] for t in tr), 4),
            "net_r_half1": round(statistics.fmean(h[0]), 4) if h[0] else None,
            "net_r_half2": round(statistics.fmean(h[1]), 4) if h[1] else None,
            "fees_r": round(statistics.fmean(t["fees"] / t["risk"] for t in tr if t["risk"] > 0), 4),
            "net_usdt": round(sum(t["net"] for t in tr), 2), "pf": round(wins / losses, 3) if losses > 0 else None,
            "exits": exits}


def passes(r: dict, base: dict) -> bool:
    return r["net_r_half1"] > base["net_r_half1"] and r["net_r_half2"] > base["net_r_half2"]


def main() -> None:
    from app.competition import v8_config as v8
    from v8_freeze import rules_and_intervals
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
        rows = [r for part in pool.map(run_coin, [(c, rules_raw, SINGLES) for c in v8.COINS]) for r in part]
    fams = sorted({r["family"] for r in rows})
    summary = {f: {n: summarize([r for r in rows if r["family"] == f and r["variant"] == n]) for n in SINGLES} for f in fams}
    combos = {}
    for f in fams:
        base = summary[f]["BASE"]
        ok = [n for n in SINGLES if n != "BASE" and passes(summary[f][n], base)]
        ok = [n for n in ok if not (n == "LEVEL" and "MAKER_TP" in ok)]        # MAKER_TP already includes LEVEL
        mins = [n for n in ok if n.startswith("MINSTOP")]
        if len(mins) > 1:                                                     # one minimum stop: the better one
            best = max(mins, key=lambda n: summary[f][n]["net_r"])
            ok = [n for n in ok if not n.startswith("MINSTOP") or n == best]
        summary[f]["_passing"] = ok
        if len(ok) > 1:
            combos[f] = "+".join(ok)
    if combos:
        need = sorted(set(combos.values()))
        with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
            extra = [r for part in pool.map(run_coin, [(c, rules_raw, tuple(need)) for c in v8.COINS]) for r in part]
        rows += extra
    chosen = {}
    for f in fams:
        ok = summary[f]["_passing"]
        if f in combos:
            summary[f][combos[f]] = summarize([r for r in rows if r["family"] == f and r["variant"] == combos[f]])
            best_single = max(ok, key=lambda n: summary[f][n]["net_r"])
            chosen[f] = combos[f] if summary[f][combos[f]]["net_r"] > summary[f][best_single]["net_r"] else best_single
        else:
            chosen[f] = ok[0] if ok else "BASE"
        for n, s in summary[f].items():
            if n.startswith("_"):
                continue
            print(f"{f} {n:30s} trades {s['trades']:5d} win {s['win']} net R h1 {s['net_r_half1']} | h2 {s['net_r_half2']} | "
                  f"all {s['net_r']} (fees {s['fees_r']} R) pf {s['pf']} net {s['net_usdt']} USDT "
                  f"{'PASSES' if n in ok else ''}{' <= ADOPT' if n == chosen[f] else ''}", flush=True)
    print("chosen:", chosen)
    (PROJECT / "docs" / "V8_COST_STUDY.json").write_text(json.dumps({"summary": summary, "chosen": chosen}, indent=1))


def wider() -> None:
    """FOLLOW-UP (fixed before running): 0.8% won among 0.6 / 0.8%, the widest tested -- so each family's adopted
    combination is re-run with a 1.0% and a 1.2% minimum stop. A wider stop replaces 0.8% only if it beats the adopted
    combination's net R per trade in BOTH halves; of two passing ones, the better whole-window net R."""
    from app.competition import v8_config as v8
    from v8_freeze import rules_and_intervals
    prev = json.loads((PROJECT / "docs" / "V8_COST_STUDY.json").read_text())
    rules_raw, _ = rules_and_intervals([c + "USDT" for c in v8.COINS])
    names = sorted({prev["chosen"][f].replace("MINSTOP80", m) for f in prev["chosen"] for m in ("MINSTOP100", "MINSTOP120")})
    with ProcessPoolExecutor(max_workers=len(v8.COINS)) as pool:
        rows = [r for part in pool.map(run_coin, [(c, rules_raw, tuple(names)) for c in v8.COINS]) for r in part]
    for f, cur in prev["chosen"].items():
        base = prev["summary"][f][cur]
        cands = {n: summarize([r for r in rows if r["family"] == f and r["variant"] == n])
                 for n in (cur.replace("MINSTOP80", "MINSTOP100"), cur.replace("MINSTOP80", "MINSTOP120"))}
        ok = [n for n, s in cands.items() if passes(s, base)]
        pick = max(ok, key=lambda n: cands[n]["net_r"]) if ok else cur
        print(f"{f} current {cur}: h1 {base['net_r_half1']} h2 {base['net_r_half2']} all {base['net_r']} net {base['net_usdt']}")
        for n, s in cands.items():
            print(f"{f} {n:32s} h1 {s['net_r_half1']} h2 {s['net_r_half2']} all {s['net_r']} (fees {s['fees_r']} R) "
                  f"pf {s['pf']} net {s['net_usdt']} {'PASSES' if n in ok else ''}")
        print(f"{f} adopt: {pick}", flush=True)
        prev["summary"][f].update(cands)
        prev["chosen"][f] = pick
    (PROJECT / "docs" / "V8_COST_STUDY.json").write_text(json.dumps(prev, indent=1))


if __name__ == "__main__":
    wider() if "--wider" in sys.argv else main()
