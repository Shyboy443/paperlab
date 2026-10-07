"""V11 ladder study: where should the operator's 25 / 50 / 25 take-profit ladder sit? (docs/V11_PROTOCOL.md)

    python scripts/v11_ladder_study.py

The ladder itself is the operator's (2026-09-30): TP1 closes 25% and moves the stop to entry, TP2 closes 50% and moves
the stop to TP1, TP3 closes the last 25%. Only the TP DISTANCES are chosen here, from four candidates in R of the planned
risk, against each scanner's current single target (BASE):

    L050  0.5 / 1.0 / 2.0      L075  0.75 / 1.5 / 2.5      L100  1.0 / 1.5 / 2.0      L100W  1.0 / 2.0 / 3.0

Every V11 scanner through the live engine on the cached 90 days (data/v11_scan), 20 USDT books, AGGRESSIVE_V6 legal sizing,
8 slots (V11.4: 4), halts off, every book reset at UTC midnight. R = the trade's net result over its planned risk.

DECISION RULE (fixed before running): per family, the rungs with the best net R per trade on the DISCOVERY half; the
confirmation half is reported, not used to choose. The ladder is adopted because the operator asked for it; this study only
places it, and says honestly whether it beats BASE.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
VARIANTS = {"BASE": None, "L050": (0.5, 1.0, 2.0), "L075": (0.75, 1.5, 2.5), "L100": (1.0, 1.5, 2.0),
            "L100W": (1.0, 2.0, 3.0)}


def run(args: tuple) -> dict:
    fid, variant = args[0], args[1]
    guard = len(args) > 2 and args[2]
    exits = args[3] if len(args) > 3 else {}
    from collections import deque
    from app.backtest.replay import ReplayEngine
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v11.ladder import ladder_class
    from app.strategies.v11.scan import ANCHOR, load_v11_scanners
    from v11_activity_check import DATA, tape
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    base = load_v11_scanners()[fid]
    rungs = VARIANTS[variant] if variant in VARIANTS else EQUAL[variant]
    cls = (ladder_class(base, rungs) if rungs else base).for_universe(syms)
    from app.live.scan_engine import ScanReplayEngine
    if exits:                                      # the study-only exit options (not part of the live engine)
        ScanReplayEngine = _exit_fix_engine()[0]
    extra = {"anchor_tps": bool(exits.get("anchor")), "be_cover_bps": exits.get("be_cover")} if guard and exits else {}
    eng = (ScanReplayEngine if guard else ReplayEngine)(
        settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule", **extra,
                       sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=cls.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out: dict = {"family": fid, "variant": variant, "rungs": rungs, "guard": bool(guard), "exits": exits,
                 "skipped": {k: v for k, v in res.rejects.items() if k.startswith("gap_")}}
    for name, lo, hi in (("discovery", since, mid), ("confirmation", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind != "reset"]
        # one position can close in parts (TP1 / TP2 / TP3): a trade = one position, its parts summed
        by_pos: dict[str, list] = {}
        for t in tr:
            by_pos.setdefault(t.position_id, []).append(t)
        nets, risks = [], []
        for parts in by_pos.values():
            net = sum(p.net for p in parts)
            risk = sum(abs(p.net / p.r_multiple) for p in parts if abs(p.r_multiple) > 1e-9 and abs(p.net) > 1e-12)
            risk = risk / len(parts) if parts else 0.0          # every part carries the position's planned risk
            if risk > 0:
                nets.append(net)
                risks.append(risk)
        r = np.array(nets) / np.array(risks) if nets else np.zeros(0)
        pos, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[name] = {"trades": len(r), "per_day": round(len(r) / ((hi - lo) / DAY), 1),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "pf": round(float(pos / neg), 3) if neg > 0 else None,
                     "net_usdt_per_day": round(float(np.sum(nets)) / ((hi - lo) / DAY), 3) if nets else 0.0}
    return out


def guard_check() -> None:
    """The pre-trade gap check (app/live/scan_engine.py) on the chosen ladder: same 90 days, with and without it."""
    jobs = [(f, "L050", g) for f in ("V11.1", "V11.2", "V11.3", "V11.4") for g in (False, True)]
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(run, jobs))
    for r in rows:
        d, c, a = r["discovery"], r["confirmation"], r["all"]
        print(f"{r['family']} guard={'ON ' if r['guard'] else 'off'} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} "
              f"| conf {c['net_r']} | all {a['net_r']} pf {a['pf']} skipped {r['skipped']}", flush=True)
    (PROJECT / "docs" / "V11_GAP_GUARD.json").write_text(json.dumps(rows, indent=1))


EQUAL = {"E050": (0.5, 1.0, 1.5), "E067": (2 / 3, 4 / 3, 2.0), "E075": (0.75, 1.5, 2.25), "E100": (1.0, 2.0, 3.0)}


def equal_check() -> None:
    """Operator, 2026-09-30: the three TPs must be EQUALLY spaced (TP1, 2 x TP1, 3 x TP1). Which spacing, per scanner,
    chosen on the DISCOVERY half; the pre-trade gap check on."""
    VARIANTS.update(EQUAL)
    jobs = [(f, v, True) for f in ("V11.1", "V11.2", "V11.3", "V11.4") for v in EQUAL]
    with ProcessPoolExecutor(12) as ex:
        rows = list(ex.map(run, jobs))
    picks = {}
    for f in ("V11.1", "V11.2", "V11.3", "V11.4"):
        mine = [r for r in rows if r["family"] == f and r["discovery"]["net_r"] is not None]
        pick = max(mine, key=lambda r: r["discovery"]["net_r"])
        picks[f] = pick["variant"]
        for r in mine:
            d, c, a = r["discovery"], r["confirmation"], r["all"]
            print(f"{f} {r['variant']} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf {c['net_r']} "
                  f"| all {a['net_r']} pf {a['pf']} {'<= pick' if r is pick else ''}", flush=True)
    (PROJECT / "docs" / "V11_EQUAL_LADDER.json").write_text(json.dumps({"variants": EQUAL, "runs": rows, "picks": picks},
                                                                      indent=1))


def _exit_fix_engine():
    """The scanners' live engine plus two STUDY-ONLY exit options (2026-10-01; tested, not adopted):
    `anchor_tps` re-places every take-profit at the same R multiple from the FILL (TP_i = fill + d r_i |fill - stop|),
    `be_cover_bps` makes the break-even after TP1 pay the fees (the shared engine's default buffer is 6 bp)."""
    from app.core.types import TakeProfit
    from app.live.scan_engine import ScanReplayEngine

    def anchored(tps, signal_price, stop, fill, side):
        r0, r1 = abs(signal_price - stop), abs(fill - stop)
        if r0 <= 0 or r1 <= 0:
            return list(tps)
        d = 1 if side == "long" else -1
        return [TakeProfit(fill + d * (abs(tp.price - signal_price) / r0) * r1, tp.fraction) for tp in tps]

    class ExitFixEngine(ScanReplayEngine):
        def __init__(self, *args, anchor_tps=False, be_cover_bps=None, **kw):
            super().__init__(*args, **kw)
            self.anchor_tps = bool(anchor_tps)
            if be_cover_bps is not None:
                for ex in (self.exits, self.shadow_exits):
                    if ex is not None:
                        ex.fee_buffer_bps = float(be_cover_bps)

        def _execute_pending(self, bar, meta, res):
            sigs = {getattr(s, "id", None): s for _, s, _ in self._pending} if self.anchor_tps else {}
            before = len(res.fills)
            super()._execute_pending(bar, meta, res)
            for f in (res.fills[before:] if self.anchor_tps else []):
                pos = self.portfolio.positions.get(f.position_id) if (f.kind == "entry" or f.is_open) else None
                sig = sigs.get(getattr(pos, "signal_id", None)) if pos is not None else None
                if pos is not None and sig is not None and pos.take_profits and sig.stop:
                    pos.take_profits = anchored(pos.take_profits, float(sig.entry_price), float(sig.stop),
                                                float(pos.entry_price), pos.side)
    return ExitFixEngine, anchored


EXIT_VARIANTS = {"BASE": {}, "BEF": {"be_cover": 15.0}, "ANCH": {"anchor": True}, "BOTH": {"anchor": True, "be_cover": 15.0}}
PICKED = {"V11.1": "E067", "V11.2": "E050", "V11.3": "E050", "V11.4": "E050"}


def exits_check() -> None:
    """Operator, 2026-10-01 (a DOGE short hit TP1, missed TP2 by 0.02%, came back to entry and lost on fees): the chosen
    equal ladders with (BEF) a break-even that pays the fees, (ANCH) take-profits measured from the fill, or both."""
    jobs = [(f, PICKED[f], True, EXIT_VARIANTS[v]) for f in PICKED for v in EXIT_VARIANTS]
    with ProcessPoolExecutor(12) as ex:
        rows = list(ex.map(run, jobs))
    names = {json.dumps(v, sort_keys=True): k for k, v in EXIT_VARIANTS.items()}
    for r in rows:
        r["exit_variant"] = names[json.dumps(r["exits"], sort_keys=True)]
        d, c, a = r["discovery"], r["confirmation"], r["all"]
        print(f"{r['family']} {r['exit_variant']:4s} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf "
              f"{c['net_r']} | all {a['net_r']} pf {a['pf']} usdt/day {a['net_usdt_per_day']}", flush=True)
    (PROJECT / "docs" / "V11_EXIT_FIXES.json").write_text(json.dumps(rows, indent=1))


def final_check() -> None:
    """The live configuration as deployed (chosen equal ladders, gap check, break-even that pays the fees) on 90 days."""
    from app.live.scan_engine import BE_COVER_BPS
    jobs = [(f, PICKED[f], True, {"be_cover": BE_COVER_BPS}) for f in PICKED]
    with ProcessPoolExecutor(4) as ex:
        rows = list(ex.map(run, jobs))
    for r in rows:
        d, c, a = r["discovery"], r["confirmation"], r["all"]
        print(f"{r['family']} LIVE {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf {c['net_r']} "
              f"| all {a['net_r']} pf {a['pf']}", flush=True)
    (PROJECT / "docs" / "V11_LIVE_EXITS.json").write_text(json.dumps(rows, indent=1))


def main() -> None:
    if "--final" in sys.argv:
        return final_check()
    if "--exits" in sys.argv:
        return exits_check()
    if "--guard" in sys.argv:
        return guard_check()
    if "--equal" in sys.argv:
        return equal_check()
    jobs = [(f, v) for f in ("V11.1", "V11.2", "V11.3", "V11.4") for v in VARIANTS]
    with ProcessPoolExecutor(12) as ex:
        rows = list(ex.map(run, jobs))
    report: dict = {"variants": {k: v for k, v in VARIANTS.items()}, "families": {}}
    for f in ("V11.1", "V11.2", "V11.3", "V11.4"):
        mine = [r for r in rows if r["family"] == f]
        ladders = [r for r in mine if r["variant"] != "BASE" and r["discovery"]["net_r"] is not None]
        pick = max(ladders, key=lambda r: r["discovery"]["net_r"]) if ladders else None
        report["families"][f] = {"runs": mine, "pick": pick["variant"] if pick else None}
        for r in mine:
            d, c, a = r["discovery"], r["confirmation"], r["all"]
            print(f"{f} {r['variant']:6s} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf {c['net_r']} "
                  f"| all {a['net_r']} pf {a['pf']} {'<= pick' if pick and r is pick else ''}", flush=True)
    (PROJECT / "docs" / "V11_LADDER_STUDY.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
