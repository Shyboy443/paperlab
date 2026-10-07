"""Zap (V11.3, capitulation snap-back) improvement study (operator, 2026-10-02: "zap is performing good but its making
low profits and losses are high -- analyse it and improve it").

Live, its first 24 h (91 trades): 78% wins but PF 0.70. Gross +2.20 USDT, fees 2.32 USDT -> net -1.13. Wins average
+0.21 R (TP1 then the after-TP1 lock), losses -0.99 R; fees are 0.14 R per trade (tight 0.6%+ stops, 0.11% round trip);
up to 8 trades open on one 5m bar (a market-wide flush: they win or lose together).

Same live configuration (25/50/25 ladder, T33 lock, level fills, gap check, 20 USDT books), the cached 90 days, one
change at a time:

    BASE       live
    MAKER_TP   the take-profits rest on the book as LIMIT orders: filled at the TP price at the MAKER fee (0.020%
               instead of 0.055%, no spread), and only when price trades THROUGH the TP by 0.5 bp (a touch is not a
               fill). Stops and time exits stay market orders.
    MINSTOP10  the minimum stop 1.0% instead of 0.6% (fees become a smaller share of R; the TPs move out with it)
    STRICT5    only flushes of >= 5 ATR (instead of 4)
    TOP2       at most the 2 best candidates per decision bar (instead of 8): fewer trades on one market-wide flush

and MAKER_TP is also tried on the other three scanners (their TPs are the same kind of resting order).

DECISION RULE (fixed before running): per scanner, a variant replaces BASE only if its net R per trade beats BASE's in
BOTH halves. For V11.3, the passing variants are then combined in one more run (COMBO), which is adopted only if it in
turn beats every single passing variant on the whole window; otherwise the best single one is adopted.

    python scripts/v11_zap_study.py            (V11_STUDY_WORKERS, default 7)
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
TRADE_THROUGH = 0.00005            # 0.5 bp beyond the TP before a resting limit counts as filled
WORKERS = int(os.environ.get("V11_STUDY_WORKERS", "7"))
ZAP_VARIANTS = ("BASE", "MAKER_TP", "MINSTOP10", "STRICT5", "TOP2")


def classes(fid: str, mods: tuple[str, ...]):
    from app.core.types import TakeProfit
    from app.strategies.v11.ladder import LOCK_AFTER_TP1, SHARES
    from v11_tp1_study import study_classes
    Study, Engine = study_classes(fid, SHARES[fid], LOCK_AFTER_TP1[fid], None, True)
    base_params = Study.Params()
    over = {}
    for m in mods:                                    # MINSTOP10 / 15 / 20: a 1.0 / 1.5 / 2.0% minimum stop
        if m.startswith("MINSTOP"):
            over["min_stop_pct"] = int(m[7:]) / 1000.0
    if "TOP2" in mods:
        over["max_signals"] = 2
    params = dataclasses.replace(base_params, **over) if over else base_params

    class Variant(Study):
        def __init__(self, p=None):
            super().__init__(p or params)

        def scan(self, ctx, t):
            out = super().scan(ctx, t)
            if "STRICT5" in mods:                 # score = scale(size, 4, 8): size >= 5 ATR <=> score >= 0.25
                out = [x for x in out if x.score >= 0.25 - 1e-9]
            return out

    maker = "MAKER_TP" in mods

    class VEngine(Engine):
        def _execute_pending(self, bar, meta, res):
            before = len(res.fills)
            super()._execute_pending(bar, meta, res)
            if not maker:
                return
            for f in res.fills[before:]:
                pos = self.portfolio.positions.get(f.position_id) if f.kind == "entry" else None
                if pos is not None and pos.take_profits and not pos.meta.get("tp_shifted"):
                    d = 1 if pos.side == "long" else -1
                    pos.take_profits = [TakeProfit(tp.price * (1 + d * TRADE_THROUGH), tp.fraction) for tp in pos.take_profits]
                    pos.meta["tp_shifted"] = True

        def _close(self, pos, position_id, fraction, ref_price, kind, reason, bar, ts):
            if maker and kind == "tp":
                d = 1 if pos.side == "long" else -1
                px = ref_price / (1 + d * TRADE_THROUGH)           # the resting limit's own price
                return self.portfolio.close_position(position_id, fraction, px, kind, True, reason, ts=ts, fill_price=px,
                                                     maker=True, fill_meta={"liquidity_role": "MAKER",
                                                                            "execution_level": "resting_limit_tp"})
            return super()._close(pos, position_id, fraction, ref_price, kind, reason, bar, ts)
    return Variant, VEngine


def run(args: tuple) -> dict:
    fid, name = args
    mods = tuple(name.split("+")) if name != "BASE" else ()
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS
    from app.strategies.v11.scan import ANCHOR
    from v11_activity_check import DATA, tape
    Variant, VEngine = classes(fid, mods)
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    k = Variant.for_universe(syms)
    eng = VEngine(settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule",
                  sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                  be_cover_bps=BE_COVER_BPS)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(k, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=k.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out: dict = {"family": fid, "variant": name}
    for half, lo, hi in (("half1", since, mid), ("half2", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind != "reset"]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        risk = np.array([abs(t.net / t.r_multiple) if t.r_multiple else 0.0 for t in tr]) if tr else np.zeros(0)
        fees_r = float(np.mean([t.fees / rk for t, rk in zip(tr, risk) if rk > 0])) if tr else None
        pos_, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[half] = {"trades": len(tr), "per_day": round(len(tr) / ((hi - lo) / DAY), 1),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "fees_r": round(fees_r, 4) if fees_r is not None else None,
                     "avg_win_r": round(float(r[r > 0].mean()), 3) if (r > 0).any() else None,
                     "avg_loss_r": round(float(r[r <= 0].mean()), 3) if (r <= 0).any() else None,
                     "pf": round(float(pos_ / neg), 3) if neg > 0 else None,
                     "net_usdt": round(float(sum(t.net for t in tr)), 2),
                     "exits": dict(Counter(t.exit_kind for t in tr))}
    return out


def _line(r: dict, tag: str = "") -> str:
    a = r["all"]
    return (f"{r['family']} {r['variant']:28s} {a['per_day']:5.1f}/d win {a['win']} net R h1 {r['half1']['net_r']} | h2 "
            f"{r['half2']['net_r']} | all {a['net_r']} (fees {a['fees_r']} R) win {a['avg_win_r']} loss {a['avg_loss_r']} "
            f"pf {a['pf']} net {a['net_usdt']} USDT {tag}")


def passes(r: dict, base: dict) -> bool:
    return r["half1"]["net_r"] > base["half1"]["net_r"] and r["half2"]["net_r"] > base["half2"]["net_r"]


def main() -> None:
    jobs = [("V11.3", v) for v in ZAP_VARIANTS] + [(f, v) for f in ("V11.1", "V11.2", "V11.4") for v in ("BASE", "MAKER_TP")]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    chosen = {}
    for f in ("V11.1", "V11.2", "V11.3", "V11.4"):
        mine = [r for r in rows if r["family"] == f]
        base = next(r for r in mine if r["variant"] == "BASE")
        ok = [r for r in mine if r is not base and passes(r, base)]
        for r in mine:
            print(_line(r, "PASSES" if r in ok else ""), flush=True)
        chosen[f] = {"passing": [r["variant"] for r in ok]}
    zap_ok = chosen["V11.3"]["passing"]
    if len(zap_ok) > 1:
        combo = run(("V11.3", "+".join(zap_ok)))
        rows.append(combo)
        best_single = max((r for r in rows if r["family"] == "V11.3" and r["variant"] in zap_ok), key=lambda r: r["all"]["net_r"])
        win = combo if combo["all"]["net_r"] > best_single["all"]["net_r"] else best_single
        print(_line(combo, "(combo)"), flush=True)
        chosen["V11.3"]["adopt"] = win["variant"]
    else:
        chosen["V11.3"]["adopt"] = zap_ok[0] if zap_ok else "BASE"
    for f in ("V11.1", "V11.2", "V11.4"):
        chosen[f]["adopt"] = "MAKER_TP" if "MAKER_TP" in chosen[f]["passing"] else "BASE"
    print("chosen:", json.dumps(chosen))
    (PROJECT / "docs" / "V11_ZAP_STUDY.json").write_text(json.dumps({"runs": rows, "chosen": chosen,
                                                                     "trade_through": TRADE_THROUGH}, indent=1))


def wider() -> None:
    """FOLLOW-UP (fixed before running): V11.3 with MAKER_TP and a 1.5% / 2.0% minimum stop. A wider stop replaces 1.0%
    only if its net R per trade beats MAKER_TP+MINSTOP10's in BOTH halves; the widest passing one is not preferred, the
    best whole-window net R among the passing ones is."""
    prev = json.loads((PROJECT / "docs" / "V11_ZAP_STUDY.json").read_text())
    base = next(r for r in prev["runs"] if r["family"] == "V11.3" and r["variant"] == "MAKER_TP+MINSTOP10")
    with ProcessPoolExecutor(2) as ex:
        rows = list(ex.map(run, [("V11.3", "MAKER_TP+MINSTOP15"), ("V11.3", "MAKER_TP+MINSTOP20")]))
    print(_line(base, "(current pick)"))
    ok = [r for r in rows if passes(r, base)]
    for r in rows:
        print(_line(r, "PASSES" if r in ok else ""))
    pick = max(ok, key=lambda r: r["all"]["net_r"])["variant"] if ok else base["variant"]
    print("V11.3 adopt:", pick)
    prev["runs"] += rows
    prev["chosen"]["V11.3"]["wider_stop_followup"] = {"tested": [r["variant"] for r in rows], "adopt": pick}
    prev["chosen"]["V11.3"]["adopt"] = pick
    (PROJECT / "docs" / "V11_ZAP_STUDY.json").write_text(json.dumps(prev, indent=1))


if __name__ == "__main__":
    wider() if "--wider" in sys.argv else main()
