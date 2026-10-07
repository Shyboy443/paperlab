"""V9 stock bots: find what makes the trades lose, remove it, and tune stops, targets, holds and session windows
(operator, 2026-10-07: "try different settings with the stops, tps and sessions, find the losing factor, terminate
it and improve this").

    python scripts/v9_signal_study.py capture --data-dir <dir with stocks/>   every setup + its context, 2 years
    python scripts/v9_signal_study.py grid    --data-dir <dir>                exits x filters, chosen on year 1 only
    python scripts/v9_signal_study.py verify  --data-dir <dir>                the chosen bots through the real engine

PRE-REGISTERED (2026-10-07, before the grid or any filter was evaluated)

Setups and context
- Every setup the three V9 families find, 2024-10-01 .. 2026-10-01, captured with the live setup code.
- Captured between the open + 5 minutes and the close - 15 minutes, so session windows can be tested.
- Context recorded at the decision, from closed 5m bars only:
  - minutes since the open;
  - the day's direction (price vs the session's opening price) and the side of the session VWAP;
  - the 15m and 1h trends;
  - ATR(5m) as a share of price;
  - relative volume (the bar vs the median of the 20 before it);
  - the overnight gap.

Exit simulation (signal level, every setup independently)
- Entry at the next 1m bar's open + 1 bp; the stop, target, time limit or session end on the 1m path.
- A stop and a target in the same bar count as the stop. A bar that opens beyond the stop fills at its open.
- Exits pay 1 bp, plus 0.4 bp of fees on the round trip.

Grid (480 exit configs)
- Minimum stop 0.25 / 0.5 / 0.75 / 1.0%.
- Stop buffer 0.2 / 0.5 / 1.0 ATR(5m).
- Maximum stop 1 / 2%.
- Target 0.75 / 1 / 1.5 / 2 / 3 R.
- Time limit 30 / 45 / 90 / 180 minutes. The session end always closes the trade.

Selection (YEAR 1 ONLY; year 2 is never looked at until the end)
1. Exit: per family, the config with the highest year-1 total net R among those keeping >= 50% of the live config's
   year-1 setups (inside the live entry window).
2. Filters, each tested alone on that exit:
   - session windows: skip the first 30 / 60 minutes, morning only, afternoon only, no lunch (2-4 h after the open);
   - with the day's direction; on the right side of the VWAP; 15m and 1h trends both agreeing;
   - relative volume >= 1.2; ATR below / above the year-1 median; overnight gap <= 1%.
   A filter is kept if it raises year-1 net R per trade by >= 0.02 and keeps >= 50% of the setups. The kept filters
   are combined. If the combination keeps under 35% of the base setups, the weakest filter is dropped until it keeps
   enough.
3. Year 2 (holdout):
   - the chosen bot is replayed through the REAL engine for both years (one position at a time, live sizing and costs);
   - it counts as FIXED only if year 2 is profitable: net > 0, profit factor >= 1.1, net R per trade > 0.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import json
import os
import statistics
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.backtest_2y import DAY, END, MID, START, months, stock_tape  # noqa: E402

MIN = 60_000
FAMILIES = ("V9.1", "V9.2", "V9.3")
COST_BP, FEE_BP = 1.0, 0.4
LIVE = {"min_stop": 0.0025, "buffer": 0.2, "max_stop": 0.01, "target": 1.5, "hold": 45}
LIVE_WINDOW = (10, 50)                       # entries from open + 10 min to close - 50 min
GRID = [{"min_stop": a, "buffer": b, "max_stop": m, "target": r, "hold": h}
        for a in (0.0025, 0.005, 0.0075, 0.01) for b in (0.2, 0.5, 1.0) for m in (0.01, 0.02)
        for r in (0.75, 1.0, 1.5, 2.0, 3.0) for h in (30, 45, 90, 180)]
OUT = PROJECT / "docs" / "V9_SIGNAL_STUDY.json"


# -- context at the decision (shared by capture and the final bots, so both see exactly the same thing) -------------
def context(cs: list, c: Any, s: tuple[int, int], t: int) -> dict[str, Any] | None:
    day = [x for x in cs if x.open_time >= s[0] and x.close_time < t]
    if not day:
        return None
    before = [x for x in cs if x.close_time < s[0]]
    vol = sum(x.volume for x in day)
    vwap = sum((x.high + x.low + x.close) / 3.0 * x.volume for x in day) / vol if vol > 0 else c.close
    prior = [x.volume for x in cs[-21:-1]]
    med = statistics.median(prior) if prior else 0.0
    return {"min_open": (t - s[0]) / MIN, "min_close": (s[1] - t) / MIN, "open_px": day[0].open,
            "prev_close": before[-1].close if before else None, "vwap": vwap,
            "relvol": c.volume / med if med > 0 else None}


def passes(f: dict[str, Any], rec: dict[str, Any], atr_median: float | None = None) -> bool:
    """One named filter on one setup (rec: side, close, atr, trend15, trend1h and the context)."""
    long = rec["side"] == "long"
    name = f["name"]
    if name == "SKIP30":
        return rec["min_open"] >= 30
    if name == "SKIP60":
        return rec["min_open"] >= 60
    if name == "MORNING":
        return rec["min_open"] <= 150
    if name == "AFTERNOON":
        return rec["min_open"] >= 150
    if name == "NO_LUNCH":
        return not 120 <= rec["min_open"] <= 240
    if name == "TREND_DAY":
        return rec["close"] > rec["open_px"] if long else rec["close"] < rec["open_px"]
    if name == "VWAP_SIDE":
        return rec["close"] > rec["vwap"] if long else rec["close"] < rec["vwap"]
    if name == "ALIGNED":
        want = "up" if long else "down"
        return rec["trend15"] == want and rec["trend1h"] == want
    if name == "RELVOL":
        return (rec["relvol"] or 0.0) >= 1.2
    if name == "ATR_LOW":
        return rec["atr"] / rec["close"] <= f["median"]
    if name == "ATR_HIGH":
        return rec["atr"] / rec["close"] >= f["median"]
    if name == "SMALL_GAP":
        return rec["prev_close"] is None or abs(rec["open_px"] / rec["prev_close"] - 1.0) <= 0.01
    raise ValueError(name)


FILTERS = ("SKIP30", "SKIP60", "MORNING", "AFTERNOON", "NO_LUNCH", "TREND_DAY", "VWAP_SIDE", "ALIGNED", "RELVOL",
           "ATR_LOW", "ATR_HIGH", "SMALL_GAP")


# -- capture -------------------------------------------------------------------------------------------------------
def capture_class(base: type, records: list) -> type:
    from app.strategies.v8.arena import ScalpV8

    def on_candle(self, c, ctx):
        t = c.close_time + 1
        s = self.session_at(t - 1) if self.session_at is not None else None
        if s is None or not s[0] + 5 * MIN <= t <= s[1] - 15 * MIN:
            return []
        self._session = s
        ScalpV8.on_candle(self, c, ctx)            # runs the live setup; scalp_setup below records it, trades nothing
        return []

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h, _base=base.scalp_setup):
        st = _base(self, ctx, c, cs, atr, trend15, trend1h)
        if st is not None:
            t = c.close_time + 1
            cx = context(list(cs), c, self._session, t)
            if cx is not None:
                records.append({"t": t, "symbol": c.symbol, "side": st.side, "close": c.close, "stop_ref": st.stop_ref,
                                "atr": atr, "trend15": trend15, "trend1h": trend1h, "sess_close": self._session[1], **cx})
        return None
    return type(base.__name__ + "Capture", (base,), {"on_candle": on_candle, "scalp_setup": scalp_setup,
                                                       "__module__": __name__})


def capture_one(job: tuple[str, str, str]) -> list[dict[str, Any]]:
    sid, sym, data_dir = job
    from app.live.stock_engine import StockReplayEngine as ReplayEngine
    from app.competition import v9_config as v9
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v9.stocks import load_v9_stock_scalpers
    rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
    bars, sessions = stock_tape(Path(data_dir), sym)
    records: list = []
    cls = capture_class(load_v9_stock_scalpers()[sid], records).for_class("SCALP", session_at=sessions.at)
    eng = ReplayEngine(v9.settings_v9(), [sym], rules={sym: rules[sym]}, seed=7, funding=None, execution=v9.EXECUTION_V9,
                       fees=v9.FEES_V9, fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
    eng.run(cls, bars, since_ms=START, leverage=v9.LEVERAGE_CAP, signal_tf="5m", only_symbol=sym)
    for r in records:
        r["family"] = sid
    return records


def capture(args: argparse.Namespace) -> int:
    from app.competition import v9_config as v9
    jobs = [(sid, sym, args.data_dir) for sid in FAMILIES for sym in v9.SYMBOLS]
    t0 = time.time()
    out: list = []
    with ProcessPoolExecutor(args.workers) as ex:
        for recs in ex.map(capture_one, jobs):
            out.extend(recs)
    p = Path(args.data_dir) / "v9_setups.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(out, f)
    print(f"{len(out)} setups in {time.time() - t0:.0f}s -> {p}")
    return 0


# -- the exit simulator ----------------------------------------------------------------------------------------------
def load_minutes(data_dir: str, sym: str):
    import numpy as np
    rows = []
    for month in months(START - 30 * DAY, END):
        p = Path(data_dir) / "stocks" / sym / f"{month}.csv"
        if p.exists():
            rows.extend(tuple(map(float, line.split(","))) for line in p.read_text().splitlines())
    a = np.array(sorted(rows), dtype=float)
    return a[:, 0].astype("int64"), a[:, 1], a[:, 2], a[:, 3], a[:, 4]


def simulate(job: tuple[str, str, list]):
    """Net R of every setup of one symbol under every grid config (NaN: refused by the max stop)."""
    import numpy as np
    sym, data_dir, recs = job
    ts, o, h, l, c = load_minutes(data_dir, sym)
    mins = np.array([g["min_stop"] for g in GRID])
    bufs = np.array([g["buffer"] for g in GRID])
    maxs = np.array([g["max_stop"] for g in GRID])
    tgts = np.array([g["target"] for g in GRID])
    holds = np.array([g["hold"] for g in GRID])
    meta, rows = [], []
    for r in recs:
        i0 = int(np.searchsorted(ts, r["t"]))
        iend = int(np.searchsorted(ts, r["sess_close"]))
        if i0 >= iend:
            continue
        n = min(iend, i0 + 240) - i0
        O, H, L, C = o[i0:i0 + n], h[i0:i0 + n], l[i0:i0 + n], c[i0:i0 + n]
        sgn = 1.0 if r["side"] == "long" else -1.0
        entry = O[0] * (1 + sgn * COST_BP / 1e4)
        dist = (r["close"] - r["stop_ref"]) * sgn
        if dist <= 0:
            continue
        raw = (dist + bufs * r["atr"]) / r["close"]
        stop_pct = np.maximum(raw, mins)
        stop_px = entry * (1 - sgn * stop_pct)
        tp_px = entry * (1 + sgn * stop_pct * tgts)
        if sgn > 0:
            adverse, favour = np.minimum.accumulate(L), np.maximum.accumulate(H)
            k_stop = np.searchsorted(-adverse, -stop_px, "left")
            k_tp = np.searchsorted(favour, tp_px, "left")
        else:
            adverse, favour = np.maximum.accumulate(H), np.minimum.accumulate(L)
            k_stop = np.searchsorted(adverse, stop_px, "left")
            k_tp = np.searchsorted(-favour, -tp_px, "left")
        k_time = np.minimum(holds - 1, n - 1)
        stopped = (k_stop <= k_tp) & (k_stop <= k_time) & (k_stop < n)
        target = ~stopped & (k_tp <= k_time) & (k_tp < n)
        k = np.minimum(np.minimum(np.minimum(k_stop, k_tp), k_time), n - 1)
        gap = (O[k] - stop_px) * sgn < 0
        px = np.where(stopped, np.where(gap, O[k], stop_px), np.where(target, tp_px, C[k]))
        exit_px = px * (1 - sgn * COST_BP / 1e4)
        net_r = ((exit_px - entry) * sgn / entry - FEE_BP / 1e4) / stop_pct
        meta.append({k2: r[k2] for k2 in r if k2 != "sess_close"})
        rows.append(np.where(raw <= maxs, net_r, np.nan).astype("float32"))
    return meta, (np.vstack(rows) if rows else np.zeros((0, len(GRID)), dtype="float32"))


def grid(args: argparse.Namespace) -> int:
    import numpy as np
    root = Path(args.data_dir)
    with gzip.open(root / "v9_setups.json.gz", "rt") as f:
        setups = json.load(f)
    by_sym: dict[str, list] = {}
    for r in setups:
        by_sym.setdefault(r["symbol"], []).append(r)
    t0 = time.time()
    metas, mats = [], []
    with ProcessPoolExecutor(args.workers) as ex:
        chunks = [(sym, args.data_dir, rs[i:i + 4000]) for sym, rs in by_sym.items() for i in range(0, len(rs), 4000)]
        for m, R in ex.map(simulate, chunks):
            metas.extend(m)
            mats.append(R)
    R = np.vstack(mats)
    print(f"simulated {len(metas)} setups x {len(GRID)} configs in {time.time() - t0:.0f}s", flush=True)
    live_i = GRID.index(LIVE)
    fam_of = np.array([m["family"] for m in metas])
    t_of = np.array([m["t"] for m in metas])
    in_win = np.array([m["min_open"] >= LIVE_WINDOW[0] and m["min_close"] >= LIVE_WINDOW[1] for m in metas])

    def score(mask, i):
        v = R[mask, i].astype(float)
        v = v[np.isfinite(v)]
        if not len(v):
            return {"n": 0, "net_r": None, "total_r": 0.0, "pf": None}
        return {"n": int(len(v)), "net_r": round(float(v.mean()), 4), "total_r": round(float(v.sum()), 1),
                "pf": round(float(v[v > 0].sum() / -v[v < 0].sum()), 3) if (v < 0).any() else None}
    report: dict[str, Any] = {"setups": len(metas), "families": {}}
    for sid in FAMILIES:
        y1 = (fam_of == sid) & in_win & (t_of < MID)
        y2 = (fam_of == sid) & in_win & (t_of >= MID)
        base_n = int(np.isfinite(R[y1, live_i]).sum())
        tot = np.nansum(R[y1], axis=0)
        cnt = np.isfinite(R[y1]).sum(axis=0)
        best = int(np.argmax(np.where(cnt >= 0.5 * base_n, tot, -np.inf)))
        exit_cfg = GRID[best]
        atr_med = float(np.median([metas[j]["atr"] / metas[j]["close"] for j in np.flatnonzero(y1)]))
        fmask = {}
        for name in FILTERS:
            f = {"name": name, "median": atr_med}
            fmask[name] = np.array([passes(f, m) for m in metas])
        base1 = score(y1, best)
        trials, kept = {}, []
        for name in FILTERS:
            sc = score(y1 & fmask[name], best)
            trials[name] = sc
            if sc["n"] >= 0.5 * base1["n"] and sc["net_r"] is not None and sc["net_r"] >= (base1["net_r"] or 0) + 0.02:
                kept.append((sc["net_r"], name))
        kept.sort(key=lambda x: -x[0])
        chosen = [n for _, n in kept]

        def combined(names):
            m = np.ones(len(metas), dtype=bool)
            for n in names:
                m &= fmask[n]
            return m
        while chosen and score(y1 & combined(chosen), best)["n"] < 0.35 * base_n:
            chosen.pop()                                     # the weakest filter goes first
        cm = combined(chosen)
        # the single best exit tried on each config dimension (how the loss depends on stops, targets, holds)
        dims = {}
        for key in ("min_stop", "buffer", "max_stop", "target", "hold"):
            dims[key] = {str(v): score(y1, max((i for i, g in enumerate(GRID) if g[key] == v), key=lambda i: tot[i]))
                         for v in sorted({g[key] for g in GRID})}
        fam = {"exit": exit_cfg, "atr_median": atr_med, "filters_kept": chosen,
               "live_year1": score(y1, live_i), "exit_year1": base1, "filters_tried_year1": trials,
               "final_year1": score(y1 & cm, best), "final_year2_signal_level": score(y2 & cm, best),
               "live_year2_signal_level": score(y2, live_i), "best_by_dimension_year1": dims}
        report["families"][sid] = fam
        print(f"{sid}: live y1 {fam['live_year1']} | best exit {exit_cfg} y1 {base1} | filters {chosen} -> y1 "
              f"{fam['final_year1']} | y2 holdout {fam['final_year2_signal_level']} vs live {fam['live_year2_signal_level']}",
              flush=True)
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    OUT.write_text(json.dumps({**prev, "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                               "grid": report}, indent=1), encoding="utf-8")
    return 0


# -- the chosen bots through the real engine ------------------------------------------------------------------------
def final_class(base: type, fam: dict[str, Any]) -> type:
    e = fam["exit"]
    params = dataclasses.make_dataclass("FinalParams", [
        ("min_stop_pct", float, dataclasses.field(default=e["min_stop"])),
        ("max_stop_pct", float, dataclasses.field(default=e["max_stop"])),
        ("stop_buffer_atr", float, dataclasses.field(default=e["buffer"])),
        ("target_r", float, dataclasses.field(default=e["target"])),
        ("max_hold_min", float, dataclasses.field(default=float(e["hold"])))], bases=(base.Params,))
    filters = [{"name": n, "median": fam["atr_median"]} for n in fam["filters_kept"]]
    end_buffer = max(LIVE_WINDOW[1], e["hold"] + 5)              # nothing is ever held over the close

    def on_candle(self, c, ctx, _base=base.on_candle):
        t = c.close_time + 1
        s = self.session_at(t - 1) if self.session_at is not None else None
        if s is None or not s[0] + LIVE_WINDOW[0] * MIN <= t <= s[1] - end_buffer * MIN:
            return []
        sigs = _base(self, c, ctx)
        if not sigs or not filters:
            return sigs
        cs = list(ctx.candles(c.symbol, "5m"))
        cx = context(cs, c, s, t)
        if cx is None:
            return []
        atr = self.atr(ctx, c.symbol, "5m") or 0.0
        keep = []
        for x in sigs:
            rec = {"side": x.side, "close": c.close, "atr": atr, "trend15": self.ctx_trend(ctx, c.symbol, "15m"),
                   "trend1h": self.ctx_trend(ctx, c.symbol, "1h"), **cx}
            if all(passes(f, rec) for f in filters):
                keep.append(x)
        return keep
    return type(base.__name__ + "Final", (base,), {"Params": params, "on_candle": on_candle, "__module__": __name__})


def verify_one(job: tuple[str, str, str, dict]) -> dict[str, Any]:
    sid, sym, data_dir, fam = job
    from app.live.stock_engine import StockReplayEngine as ReplayEngine
    from app.competition import v9_config as v9
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v9.stocks import load_v9_stock_scalpers
    rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
    bars, sessions = stock_tape(Path(data_dir), sym)
    base = load_v9_stock_scalpers()[sid]
    cls = (final_class(base, fam) if fam else base).for_class("SCALP", session_at=sessions.at)
    eng = ReplayEngine(dataclasses.replace(v9.settings_v9(), strategy_halt_pct=1.0, daily_halt_pct=1.0), [sym],
                       rules={sym: rules[sym]}, seed=7, funding=None, execution=v9.EXECUTION_V9, fees=v9.FEES_V9,
                       fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bars, since_ms=START, leverage=v9.LEVERAGE_CAP, signal_tf="5m", only_symbol=sym,
                  reset_at=list(range(START + DAY, END, DAY)))
    return {"sid": sid, "symbol": sym, "final": bool(fam),
            "trades": [(t.entry_ts, t.net, t.r_multiple) for t in res.trades if t.entry_ts >= START]}


def verify(args: argparse.Namespace) -> int:
    from app.competition import v9_config as v9
    rep = json.loads(OUT.read_text())
    fams = rep["grid"]["families"]
    jobs = [(sid, sym, args.data_dir, fams[sid] if final else {}) for sid in FAMILIES for sym in v9.SYMBOLS
            for final in (False, True)]
    agg: dict[tuple[str, bool], list] = {}
    per_bot: dict[str, Any] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        for r in ex.map(verify_one, jobs):
            agg.setdefault((r["sid"], r["final"]), []).extend(r["trades"])
            if r["final"]:
                tr = r["trades"]
                per_bot[f"{r['sid']}-{r['symbol']}"] = {
                    "trades": len(tr), "net_y1": round(sum(x[1] for x in tr if x[0] < MID), 2),
                    "net_y2": round(sum(x[1] for x in tr if x[0] >= MID), 2)}

    def summ(tr):
        out = {}
        for label, part in (("year1", [x for x in tr if x[0] < MID]), ("year2", [x for x in tr if x[0] >= MID])):
            n = len(part)
            w = sum(x[1] for x in part if x[1] > 0)
            lo = -sum(x[1] for x in part if x[1] < 0)
            out[label] = {"trades": n, "net_usd": round(sum(x[1] for x in part), 2),
                          "net_r": round(sum(x[2] for x in part) / n, 4) if n else None,
                          "pf": round(w / lo, 3) if lo else None, "per_day": round(n / 365.0 / 10, 2)}
        return out
    result = {}
    for sid in FAMILIES:
        live, fin = summ(agg[(sid, False)]), summ(agg[(sid, True)])
        y2 = fin["year2"]
        fixed = bool(y2["trades"] and y2["net_usd"] > 0 and (y2["pf"] or 0) >= 1.1 and (y2["net_r"] or 0) > 0)
        result[sid] = {"live": live, "final": fin, "verdict": "FIXED" if fixed else "NOT FIXED"}
        print(f"{sid}: live {live} | final {fin} -> {result[sid]['verdict']}", flush=True)
    rep["verify"] = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "families": result, "bots": per_bot}
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("capture", "grid", "verify"))
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    return {"capture": capture, "grid": grid, "verify": verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
