"""V13 SNAPBACK study: limit-order (maker) mean reversion, the exact strategy through the exact engine (docs/V13_PROTOCOL.md).

    python scripts/v13_snapback_study.py            (V11_STUDY_WORKERS, default 7)

PRE-REGISTERED (2026-10-03, written before the first run; nothing below is changed after seeing a result):

Data: the V11 cache (data/v11_scan: 30 Bybit perps, 1m, 95 days to 2026-09-29), the first 5 days warm-up. DEV = the first
half of the remaining 90 days, TEST = the second half. Book: 20 USDT, SizingV6 tiers (1% base risk, legal minimums,
hard cap 2%), max 4 positions, reset daily (each day an independent book, as the V11 studies). Costs: Bybit fees by the
role achieved (limit entries and take-profits maker 0.020%; stops and time exits taker 0.055% + the modelled spread),
funding, 60 s decision latency; entries fill only on a 0.5 bp trade-through and are rejected if they would cross when
placed (app/live/v13_engine.py).

Grid (54 variants): threshold {0.80, 0.95, 1.10} x offset_atr {0.25, 0.50, 1.00} x target_r {0.75, 1.00, 1.50}
x max_hold_min {120, 240}. Fixed: expiry 15 min, stop max(1.0%, 2.5 ATR) capped at 3%, at most 4 orders a decision.

SELECTION (DEV only): among variants with >= 150 DEV trades, the highest SMOOTHED DEV net R per trade = the mean of its
own DEV net R and that of its grid neighbours (one step in one parameter), so a lucky corner cannot win alone.
TEST VERDICT (the selected variant, once):
    PASS   TEST net R per trade > 0, day-clustered t >= 2.0, profit factor >= 1.10, >= 100 TEST trades
    WEAK   TEST net R per trade > 0 but a t / PF / count condition fails
    FAIL   TEST net R per trade <= 0
Diagnostics for the selected variant (no selection): TAKER (the same set-ups bought at market, no limit) and TOUCH
(limits filled on a touch, no trade-through) -- how much the maker entry adds, and how sensitive it is to the fill rule.
The forward paper bot runs the SELECTED variant whatever the verdict (the operator asked for the bot); its label is
the verdict, and it never goes live on a paper result alone.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import math
import os
import sys
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
OUT = PROJECT / "docs" / "V13_SNAPBACK_STUDY.json"
GRID = {"threshold": (0.80, 0.95, 1.10), "offset_atr": (0.25, 0.50, 1.00), "target_r": (0.75, 1.00, 1.50),
        "max_hold_min": (120.0, 240.0)}
MIN_DEV_TRADES = 150


def variant_class(over: dict, mode: str = "LIMIT"):
    from app.strategies.v13.snapback import SnapParams, SnapbackV13
    fields = [(k, type(v), dataclasses.field(default=v)) for k, v in over.items()]
    P = dataclasses.make_dataclass("SnapStudyParams", fields, bases=(SnapParams,))
    body = {"Params": P, "__module__": __name__}
    if mode == "TAKER":
        def limit_signal(self, ctx, c, st, atr, vwap, t, _base=SnapbackV13.limit_signal):
            sig = _base(self, ctx, c, st, atr, vwap, t)
            if sig is None:
                return None
            d = sig.entry_price - c.close                               # move the whole set-up to the close: market
            sig.entry_price = c.close
            sig.stop += d
            for tp in sig.take_profits:
                tp.price += d
            sig.meta.pop("limit", None)
            sig.meta.pop("expire_ms", None)
            return sig
        body["limit_signal"] = limit_signal
    return type("SnapStudy", (SnapbackV13,), body)


def run(args: tuple) -> dict:
    name, over, mode = args
    from app.competition import v11_config as v11
    from app.competition import v13_config as v13
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.v13_engine import LimitEntryEngineV13
    from app.strategies.v13.snapback import ANCHOR
    from v11_activity_check import DATA, tape
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v13.settings_v13(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    k = variant_class(over, mode).for_universe(syms)
    eng = LimitEntryEngineV13(settings, syms, rules=rules, seed=7, execution=v13.EXECUTION_V13, fees=FEES_V6,
                              fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                              max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True,
                              trade_through=0.0 if mode == "TOUCH" else 0.00005)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(k, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v13.LEVERAGE_CEILING,
                  signal_tf=k.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out: dict = {"variant": name, "mode": mode, "params": over, "rejects": dict(res.rejects), "signals": res.signals}
    for half, lo, hi in (("dev", since, mid), ("test", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind not in ("reset", "end_of_run")]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        days = {}
        for t in tr:
            days.setdefault(t.entry_ts // DAY, []).append(t.r_multiple)
        dm = np.array([np.mean(v) for v in days.values()]) if days else np.zeros(0)
        tstat = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(len(dm)))) if len(dm) > 2 and dm.std(ddof=1) > 0 else None
        risk = [abs(t.net / t.r_multiple) for t in tr if t.r_multiple]
        pos_, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[half] = {"trades": len(tr), "per_day": round(len(tr) / ((hi - lo) / DAY), 2),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None, "day_t": round(tstat, 2) if tstat else None,
                     "pf": round(float(pos_ / neg), 3) if neg > 0 else None,
                     "net_usdt": round(float(sum(t.net for t in tr)), 2),
                     "fees_r": round(float(np.mean([t.fees / rk for t, rk in zip([x for x in tr if x.r_multiple], risk) if rk > 0])), 4) if risk else None,
                     "avg_win_r": round(float(r[r > 0].mean()), 3) if (r > 0).any() else None,
                     "avg_loss_r": round(float(r[r <= 0].mean()), 3) if (r <= 0).any() else None,
                     "exits": dict(Counter(t.exit_kind for t in tr))}
    return out


def neighbours(over: dict) -> list[dict]:
    out = []
    for k, vals in GRID.items():
        i = vals.index(over[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                out.append({**over, k: vals[j]})
    return out


def key(over: dict) -> str:
    return " ".join(f"{k}={v:g}" for k, v in over.items())


def main() -> None:
    t0 = time.time()
    workers = int(os.environ.get("V11_STUDY_WORKERS") or 7)
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    jobs = [(key(o), o, "LIMIT") for o in combos]
    rows = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(run, jobs):
            rows[r["variant"]] = r
            d, t = r["dev"], r["test"]
            print(f"{r['variant']:62s} DEV n {d['trades']:4d} R {d['net_r']} pf {d['pf']} | TEST n {t['trades']:4d} "
                  f"R {t['net_r']} ({time.time() - t0:.0f}s)", flush=True)
    eligible = [o for o in combos if (rows[key(o)]["dev"]["trades"] or 0) >= MIN_DEV_TRADES
                and rows[key(o)]["dev"]["net_r"] is not None]
    smooth = {}
    for o in eligible:
        vals = [rows[key(x)]["dev"]["net_r"] for x in [o] + neighbours(o) if rows[key(x)]["dev"]["net_r"] is not None]
        smooth[key(o)] = float(np.mean(vals))
    if not smooth:
        raise SystemExit("no variant has enough DEV trades")
    best = max(eligible, key=lambda o: (smooth[key(o)], rows[key(o)]["dev"]["net_r"]))
    sel = rows[key(best)]
    t = sel["test"]
    if (t["net_r"] or 0) <= 0:
        verdict = "FAIL"
    elif (t["day_t"] or 0) >= 2.0 and (t["pf"] or 0) >= 1.10 and t["trades"] >= 100:
        verdict = "PASS"
    else:
        verdict = "WEAK"
    print(f"\nSELECTED (DEV, smoothed {smooth[key(best)]:+.4f}): {key(best)}")
    print(f"  DEV  {json.dumps(sel['dev'])}")
    print(f"  TEST {json.dumps(sel['test'])}")
    print(f"  VERDICT: {verdict}", flush=True)
    with ProcessPoolExecutor(max_workers=2) as pool:
        diag = {r["mode"]: r for r in pool.map(run, [(key(best), best, "TAKER"), (key(best), best, "TOUCH")])}
    for m, r in diag.items():
        print(f"  {m:6s} DEV R {r['dev']['net_r']} n {r['dev']['trades']} | TEST R {r['test']['net_r']} n {r['test']['trades']} "
              f"pf {r['test']['pf']}")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "grid": GRID, "selected": best,
                               "smoothed_dev_net_r": round(smooth[key(best)], 4), "verdict": verdict,
                               "selected_result": sel, "diagnostics": diag, "smoothed": smooth,
                               "variants": list(rows.values())}, indent=1))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


def finish(log_path: str) -> None:
    """The grid ran to its verdict but the diagnostics crashed before the JSON was written (2026-10-03: the TAKER
    variant dropped expire_ms, which the strategy read). Rebuild the record from that run's log -- every variant's DEV /
    TEST line, the selection and the verdict, nothing re-selected -- re-run the SELECTED variant for its full detail,
    and run the two diagnostics."""
    import re
    rows, sel_key, verdict = {}, None, None
    pat = re.compile(r"^(threshold=\S+ offset_atr=\S+ target_r=\S+ max_hold_min=\S+)\s+DEV n\s+(\d+) R (\S+) pf (\S+) \| "
                     r"TEST n\s+(\d+) R (\S+)")
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            f = lambda v: None if v == "None" else float(v)  # noqa: E731
            rows[m.group(1)] = {"variant": m.group(1), "dev": {"trades": int(m.group(2)), "net_r": f(m.group(3)),
                                                               "pf": f(m.group(4))},
                                "test": {"trades": int(m.group(5)), "net_r": f(m.group(6))}}
        elif line.startswith("SELECTED"):
            sel_key = line.split(": ", 1)[1].strip()
        elif "VERDICT:" in line:
            verdict = line.split("VERDICT:")[1].strip()
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    best = next(o for o in combos if key(o) == sel_key)
    smooth = {}
    for o in combos:
        if rows[key(o)]["dev"]["trades"] >= MIN_DEV_TRADES and rows[key(o)]["dev"]["net_r"] is not None:
            smooth[key(o)] = float(np.mean([rows[key(x)]["dev"]["net_r"] for x in [o] + neighbours(o)
                                            if rows[key(x)]["dev"]["net_r"] is not None]))
    assert max(smooth, key=lambda k: (smooth[k], rows[k]["dev"]["net_r"])) == sel_key, "selection does not reproduce"
    with ProcessPoolExecutor(max_workers=3) as pool:
        out = {r["mode"]: r for r in pool.map(run, [(sel_key, best, m) for m in ("LIMIT", "TAKER", "TOUCH")])}
    sel = out.pop("LIMIT")
    print(f"selected {sel_key}: DEV {sel['dev']['net_r']} TEST {sel['test']['net_r']} -> {verdict}")
    for m, r in out.items():
        print(f"  {m:6s} DEV R {r['dev']['net_r']} n {r['dev']['trades']} | TEST R {r['test']['net_r']} n {r['test']['trades']} "
              f"pf {r['test']['pf']}")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "grid": GRID, "selected": best,
                               "smoothed_dev_net_r": round(smooth[sel_key], 4), "verdict": verdict,
                               "selected_result": sel, "diagnostics": out, "smoothed": smooth,
                               "variants": list(rows.values()),
                               "note": "variants: the grid run's DEV / TEST summary lines (the full per-variant detail was "
                                       "lost when the diagnostics crashed); selected_result: the selected variant re-run "
                                       "(deterministic, seed 7)"}, indent=1))
    print(f"wrote {OUT.relative_to(PROJECT)}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--finish":
        finish(sys.argv[2])
    else:
        main()
