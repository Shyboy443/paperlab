"""V11 TP1 study (operator, 2026-10-01): "make TP1 close 30% so it pays the fees, or after TP1 move the SL not to entry
but slightly towards the TP so the fees are paid -- do the analysis and find the one that suits".

    python scripts/v11_tp1_study.py

The live V11 configuration (each scanner's equal ladder, the pre-trade gap check) on the cached 90 days, 20 USDT books,
halts off, books reset daily -- varying only what happens at TP1:

    how much TP1 closes            F25: 25 / 50 / 25 (live)        F30: 30 / 50 / 20        F35: 35 / 45 / 20
    where the stop goes after TP1  FEES: entry + 0.15% (live, pays the round-trip fees)
                                   T33: entry + a third of the way to TP1 (never less than FEES)
                                   T50: entry + half the way to TP1 (never less than FEES)

The stop moves on the first 1m bar after TP1 prints (the engine's instant move covers FEES; the larger locks are applied
at the end of that bar). After TP2 the stop sits on TP1, as live.

Measured per variant: net R per trade, win rate, profit factor, and -- the operator's question -- how many trades that
HIT TP1 still ended below zero after fees.
DECISION RULE (fixed before running): per scanner, the variant with the best net R per trade on the DISCOVERY half among
those where no more than 2% of TP1-hit trades end negative; the confirmation half is reported, not used to choose.

SECOND RUN (2026-10-01, operator: "use the best settings and do it") -- the engine now fills exits AT THEIR LEVEL
(app/live/scan_engine.py, level_fills) and moves the ladder's stop the moment a TP fills:

    python scripts/v11_tp1_study.py --level          stage 1: TP1 share (adds F40, F50) x stop after TP1, per scanner,
                                                       plus the previous picks on the old path-point fills (reference)
    python scripts/v11_tp1_study.py --spacing        stage 2: with each scanner's stage-1 pick, TP spacing k in
                                                       {0.5, 0.67, 0.75, 1.0} R

DECISION RULES (fixed before running): stage 1 as above. Stage 2: the same constraint; a k other than the scanner's
current one is adopted only if it beats the current k by at least 0.005 R per trade on the DISCOVERY half.
A TP1-hit trade is one with any take-profit fill.
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
SPLITS = {"F25": (0.25, 0.50, 0.25), "F30": (0.30, 0.50, 0.20), "F35": (0.35, 0.45, 0.20),
          "F40": (0.40, 0.40, 0.20), "F50": (0.50, 0.30, 0.20)}
SPACINGS = (0.5, 2 / 3, 0.75, 1.0)
WORKERS = int(__import__("os").environ.get("V11_STUDY_WORKERS", "12"))     # each replay holds ~1.5 GB at its peak
LOCKS = {"FEES": 0.0, "T33": 1 / 3, "T50": 0.5}
COVER = 0.0015


def study_classes(fid: str, fractions: tuple, lock_frac: float, k: float | None = None, level: bool = True):
    """The live scanner + ladder, with TP1's share and the after-TP1 stop as study parameters."""
    from app.core.types import ExitUpdate, TakeProfit
    from app.live.scan_engine import ScanReplayEngine
    from app.strategies.v11.ladder import RUNGS, ladder_class
    from app.strategies.v11.scan import load_v11_scanners
    base = ladder_class(load_v11_scanners()[fid])
    rungs = RUNGS[fid] if k is None else (k, 2 * k, 3 * k)

    def lock_after_tp1(pos) -> float:
        d = 1 if pos.side == "long" else -1
        tp1 = pos.meta.get("tp1")
        dist = max(pos.entry_price * COVER, lock_frac * abs(tp1 - pos.entry_price)) if tp1 else pos.entry_price * COVER
        return pos.entry_price + d * dist

    class Study(base):
        def _signal(self, ctx, x, t):
            s = super()._signal(ctx, x, t)
            if s is None:
                return None
            risk = abs(s.entry_price - s.stop)
            d = 1 if s.side == "long" else -1
            s.take_profits = [TakeProfit(s.entry_price + d * r * risk, f) for r, f in zip(rungs, fractions)]
            s.meta.update(shares=list(fractions), tp1_lock=lock_frac)   # the live engine's after-TP1 lock reads this
            return s

        def manage(self, pos, c, ctx):
            done = 3 - len(pos.take_profits)
            if done < 1:
                return None
            d = 1 if pos.side == "long" else -1
            lock = lock_after_tp1(pos)
            if done >= 2 and pos.take_profits:
                lock = pos.entry_price + (pos.take_profits[-1].price - pos.entry_price) / 3.0
            return ExitUpdate(stop=lock, reason="be") if (lock - pos.stop) * d > 0 else None

    class Engine(ScanReplayEngine):
        level_fills = level
    return Study, Engine


def run(args: tuple) -> dict:
    fid, split, lock = args[:3]
    k = args[3] if len(args) > 3 else None
    level = args[4] if len(args) > 4 else True
    from collections import deque
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS
    from app.strategies.v11.scan import ANCHOR
    from v11_activity_check import DATA, tape
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    Study, Engine = study_classes(fid, SPLITS[split], LOCKS[lock], k, level)
    cls = Study.for_universe(syms)
    eng = Engine(settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule",
                 sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                 be_cover_bps=BE_COVER_BPS)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=cls.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    tp1_hit = {f.position_id for f in res.fills if f.kind == "tp"}
    out: dict = {"family": fid, "split": split, "lock": lock, "k": round(k, 4) if k else None, "level_fills": level}
    for name, lo, hi in (("discovery", since, mid), ("confirmation", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind != "reset"]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        hit = [t for t in tr if t.position_id in tp1_hit]
        lost_after_tp1 = sum(1 for t in hit if t.net < 0)
        pos_, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[name] = {"trades": len(tr), "per_day": round(len(tr) / ((hi - lo) / DAY), 1),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "pf": round(float(pos_ / neg), 3) if neg > 0 else None,
                     "tp1_hits": len(hit), "tp1_then_loss": lost_after_tp1,
                     "tp1_then_loss_pct": round(lost_after_tp1 / len(hit), 4) if hit else 0.0}
    return out


def _pick(mine: list[dict]) -> dict:
    ok = [r for r in mine if r["discovery"]["tp1_then_loss_pct"] <= 0.02 and r["discovery"]["net_r"] is not None]
    return max(ok or mine, key=lambda r: r["discovery"]["net_r"])


def _line(r: dict, tag: str = "") -> str:
    d, c, a = r["discovery"], r["confirmation"], r["all"]
    k = f" k{r['k']}" if r.get("k") else ""
    old = " OLD-FILLS" if r.get("level_fills") is False else ""
    return (f"{r['family']} {r['split']}/{r['lock']:4s}{k}{old} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']}"
            f" | conf {c['net_r']} | all {a['net_r']} pf {a['pf']} | TP1 hit then loss {a['tp1_then_loss']}/"
            f"{a['tp1_hits']} ({a['tp1_then_loss_pct']:.1%}) {tag}")


def level_main() -> None:
    """Stage 1 on the level-accurate engine; the heaviest scanner (V11.3) first so the pool stays full."""
    from app.strategies.v11 import ladder
    fams = ("V11.3", "V11.1", "V11.2", "V11.4")
    lock_name = {v: n for n, v in LOCKS.items()}
    split_name = {v: n for n, v in SPLITS.items()}
    jobs = [(f, sp, lk) for f in fams for sp in SPLITS for lk in LOCKS]
    jobs += [(f, split_name[tuple(ladder.SHARES[f])], lock_name[ladder.LOCK_AFTER_TP1[f]], None, False) for f in fams]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    picks = {}
    for f in sorted(fams):
        mine = [r for r in rows if r["family"] == f and r["level_fills"]]
        pick = _pick(mine)
        picks[f] = f"{pick['split']}/{pick['lock']}"
        for r in [x for x in rows if x["family"] == f]:
            print(_line(r, "<= pick" if r is pick else ""), flush=True)
    (PROJECT / "docs" / "V11_TP1_LEVEL_STUDY.json").write_text(json.dumps(
        {"engine": "level fills", "splits": SPLITS, "locks": LOCKS, "runs": rows, "picks": picks}, indent=1))
    print("picks:", picks)


def spacing_main() -> None:
    from app.strategies.v11 import ladder
    picks = json.loads((PROJECT / "docs" / "V11_TP1_LEVEL_STUDY.json").read_text())["picks"]
    fams = ("V11.3", "V11.1", "V11.2", "V11.4")
    jobs = [(f, *picks[f].split("/"), k) for f in fams for k in SPACINGS]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    chosen = {}
    for f in sorted(fams):
        mine = [r for r in rows if r["family"] == f]
        cur = min(mine, key=lambda r: abs(r["k"] - ladder.SPACING[f]))
        best = _pick(mine)
        win = best if best is not cur and best["discovery"]["net_r"] >= cur["discovery"]["net_r"] + 0.005 else cur
        chosen[f] = {"split": win["split"], "lock": win["lock"], "k": win["k"]}
        for r in mine:
            print(_line(r, "<= chosen" if r is win else ("(current k)" if r is cur else "")), flush=True)
    (PROJECT / "docs" / "V11_SPACING_LEVEL_STUDY.json").write_text(json.dumps({"runs": rows, "chosen": chosen}, indent=1))
    print("chosen:", chosen)


def main() -> None:
    if "--level" in sys.argv:
        return level_main()
    if "--spacing" in sys.argv:
        return spacing_main()
    fams = ("V11.1", "V11.2", "V11.3", "V11.4")
    jobs = [(f, s, lk) for f in fams for s in SPLITS for lk in LOCKS]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    picks = {}
    for f in fams:
        mine = [r for r in rows if r["family"] == f]
        ok = [r for r in mine if r["discovery"]["tp1_then_loss_pct"] <= 0.02 and r["discovery"]["net_r"] is not None]
        pick = max(ok or mine, key=lambda r: r["discovery"]["net_r"])
        picks[f] = f"{pick['split']}/{pick['lock']}"
        for r in mine:
            d, c, a = r["discovery"], r["confirmation"], r["all"]
            print(f"{f} {r['split']}/{r['lock']:4s} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf "
                  f"{c['net_r']} | all {a['net_r']} pf {a['pf']} | TP1 hit then loss {a['tp1_then_loss']}/{a['tp1_hits']} "
                  f"({a['tp1_then_loss_pct']:.1%}) {'<= pick' if r is pick else ''}", flush=True)
    (PROJECT / "docs" / "V11_TP1_STUDY.json").write_text(json.dumps({"splits": SPLITS, "locks": LOCKS, "runs": rows,
                                                                    "picks": picks}, indent=1))
    print("picks:", picks)


if __name__ == "__main__":
    main()
