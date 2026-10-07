"""V11.3 "Widget, the crash catcher": can it be made profitable without losing its pace? (operator, 2026-10-06:
"this bot is doing very well, trades taken is good, how to improve this ... make it more profitable, I like how it's
aggressive").

    python scripts/v11_crash_study.py --data-dir /data/bt2y --workers 8     (on the server: the two-year archive)

PRE-REGISTERED (2026-10-06, before any variant was run):

Data
- The two-year Bybit 1m archive of scripts/backtest_2y.py, 30 coins.
- Window: 2024-10-01 .. 2026-10-01, with 46 days of warm-up.
- Year 1 (to 2025-10-01) and year 2 are the two halves.

Book
- 20 USDT, reset every day, so every day starts equal and every trade counts.
- Halts off. Live fees, funding, sizing, 60 s latency, the 25/50/25 take-profit ladder and maker take-profits.

Each variant changes ONE thing in the live V11.3:
- BASE      the live bot, unchanged.
- MAKER0    enter with a post-only LIMIT at the signal bar's close (maker fee, no spread), expires after 15 minutes.
            A post-only order that would cross is a missed trade, never a market buy.
- MAKER1    the same limit 0.3 ATR(5m) beyond the close (a little lower for a long).
- CALM_BTC  skip a long when BTC itself fell >= 2 ATR in the same hour (a market-wide crash, not one coin's flush);
            the mirror for shorts.
- CAP3      at most 3 open positions in the same direction (the live cap is 8 of anything; crashes are correlated).
- CONFIRM   the reversal bar must also close beyond the previous bar's high (long) / low (short).
- DEEP5     the flush must be >= 5 ATR instead of 4.

Verdict
- A variant IMPROVES only if both its net USDT and its net R per trade beat BASE in BOTH years.
- Also reported: trades per day (the operator wants it to stay aggressive), win rate, profit factor and fees.
- The best improving variants may then be combined ONCE, and that combination must pass the same test.

COMBINATION (registered 2026-10-07 after the single changes, before it was run). MAKER0, MAKER1 and CONFIRM improved;
MAKER0 and MAKER1 are alternatives, and MAKER1 had the better net R in both years.
- COMBO     MAKER1 + CONFIRM.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.backtest_2y import DAY, END, MID, START, WARM_MULTI, freeze, funding_rows, tape, thin  # noqa: E402

MIN = 60_000
OUT = PROJECT / "docs" / "V11_CRASH_STUDY.json"
VARIANTS: dict[str, dict[str, Any]] = {
    "BASE": {},
    "MAKER0": {"limit_offset_atr": 0.0},
    "MAKER1": {"limit_offset_atr": 0.3},
    "CALM_BTC": {"btc_calm_atr": 2.0},
    "CAP3": {"side_cap": 3},
    "CONFIRM": {"confirm": True},
    "DEEP5": {"fall_atr": 5.0},
    "COMBO": {"limit_offset_atr": 0.3, "confirm": True},
}


def study_class(opts: dict[str, Any]) -> type:
    """V11.3 with one pre-registered change. BASE is the live class itself (no copy)."""
    from app.strategies.v6.base import close_location, scale
    from app.strategies.v11.scan import ANCHOR, Cand, CapitulationV11
    if not opts:
        return CapitulationV11
    fall_atr = float(opts.get("fall_atr", 4.0))
    confirm = bool(opts.get("confirm"))
    btc_calm = opts.get("btc_calm_atr")
    side_cap = opts.get("side_cap")
    offset = opts.get("limit_offset_atr")

    def flush(self, ctx, sym, t):
        cs = self.current(ctx, sym, "5m", t, 290)
        atr = self.atr(ctx, sym, "5m") if cs else None
        if not cs or not atr:
            return None
        prev = cs[-2]
        window = cs[-13:-1]
        return cs, atr, (prev.close - max(x.high for x in window)) / atr, (prev.close - min(x.low for x in window)) / atr

    def scan(self, ctx, t):                      # CapitulationV11.scan, with the variant's one change
        btc = flush(self, ctx, ANCHOR, t) if btc_calm is not None else None
        out = []
        for sym in self.universe:
            got = flush(self, ctx, sym, t)
            if got is None:
                continue
            cs, atr, fall, rise = got
            c, prev = cs[-1], cs[-2]
            med = self.median_volume(cs, 288)
            if med <= 0:
                continue
            climax = sum(1 for x in cs[-5:-1] if x.volume >= 2.5 * med) >= 2
            loc = close_location(c)
            if not climax:
                continue
            if fall <= -fall_atr and c.close > c.open and c.close > prev.close and loc >= 0.6 \
                    and (not confirm or c.close > prev.high):
                side, ext, size = "long", min(x.low for x in cs[-4:]), -fall
            elif rise >= fall_atr and c.close < c.open and c.close < prev.close and loc <= 0.4 \
                    and (not confirm or c.close < prev.low):
                side, ext, size = "short", max(x.high for x in cs[-4:]), rise
            else:
                continue
            if btc is not None and ((side == "long" and btc[2] <= -btc_calm) or (side == "short" and btc[3] >= btc_calm)):
                continue
            f = {"stretch": scale(size, 4.0, 8.0)}
            out.append(Cand(sym, side, ext, atr, f["stretch"], f, f"{size:.1f} ATR 1h move on climax volume, reversal bar"))
        return out

    body: dict[str, Any] = {"scan": scan, "__module__": __name__}
    if side_cap is not None:
        def on_candle(self, c, ctx):
            if not self.bound(c) or c.symbol != self.anchor:
                return []
            held = Counter(str(getattr(p.side, "value", p.side)) for p in ctx.positions_of(self.id))
            found = [x for x in self.scan(ctx, c.close_time) if x.score >= self.params.min_score]
            found.sort(key=lambda x: (-x.score, x.symbol))
            out = []
            for x in found:
                if len(out) >= self.params.max_signals:
                    break
                if held[x.side] >= side_cap:
                    continue
                sig = self._signal(ctx, x, c.close_time)
                if sig is not None:
                    out.append(sig)
                    held[x.side] += 1
            return out
        body["on_candle"] = on_candle
    if offset is not None:
        def _signal(self, ctx, x, t):
            p = self.params
            c = ctx.candles(x.symbol, self.signal_tf)[-1]
            limit = c.close - offset * x.atr if x.side == "long" else c.close + offset * x.atr
            dist = (limit - x.extreme) if x.side == "long" else (x.extreme - limit)
            raw = (dist + p.stop_buffer_atr * x.atr) / limit if limit > 0 else 0.0
            if dist <= 0 or raw <= 0 or raw > p.max_stop_pct:
                return None
            stop_pct = max(raw, p.min_stop_pct)
            sig = self.entry(c=c, ctx=ctx, side=x.side, price=limit, stop_pct=stop_pct, tps_r=[(p.target_r, 1.0)],
                             trail_atr=None, be_at_r=None, expected_move_pct=p.target_r * stop_pct,
                             expected_move_source=f"{p.target_r:g}R scanner target", factors=x.factors, reason=x.note,
                             extra={"setup": x.note, "horizon": "SCAN", "order": "POST_ONLY_LIMIT", "limit": limit,
                                    "expire_ms": t + MIN + 15 * MIN, "target_r": p.target_r,
                                    "time_stop_h": p.max_hold_min / 60.0, "stop_pct": round(stop_pct, 5)})
            sig.max_hold_s = int(p.max_hold_min * 60)
            sig.id = f"{self.id}:LMT:{x.symbol}:{t}:{x.side}"
            return sig
        body["_signal"] = _signal
    return type("CrashStudy", (CapitulationV11,), body)


def run(name: str, data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    import dataclasses
    from app.backtest.funding import FundingSchedule
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
    from app.live.v13_engine import LimitEntryEngineV13
    from app.strategies.v11.ladder import ladder_class
    root = Path(data_dir)
    t0 = time.time()
    opts = VARIANTS[name]
    universe = list(v11.UNIVERSE)
    rules = {s: MarketRules(**r) for s, r in freeze("v11")["rules"].items()}
    cls = ladder_class(study_class(opts)).for_universe(universe)
    Engine = thin(LimitEntryEngineV13 if "limit_offset_atr" in opts else ScanReplayEngine)
    eng = Engine(dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0), universe,
                 rules=rules, seed=7, funding=FundingSchedule({s: funding_rows(root, s, START - WARM_MULTI, END) for s in universe}),
                 execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                 leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None,
                 be_cover_bps=BE_COVER_BPS, maker_tp=True)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, tape(root, universe, v11.ANCHOR, START - WARM_MULTI, END), since_ms=START,
                  leverage=v11.LEVERAGE_CEILING, signal_tf="5m", reset_at=list(range(START + DAY, END, DAY)))
    risk, funding = {}, {}
    for f in res.fills:
        if f.kind == "entry" and f.position_id not in risk:
            risk[f.position_id] = f.qty * abs(f.price - f.meta["stop"])
        elif f.kind == "funding":
            funding[f.position_id] = funding.get(f.position_id, 0.0) + f.realized_pnl
    pos: dict[str, dict[str, Any]] = {}
    for t in res.trades:
        if t.entry_ts < START:
            continue
        p = pos.setdefault(t.position_id, {"entry": t.entry_ts, "exit": t.exit_ts, "symbol": t.symbol, "side": t.side,
                                            "net": funding.get(t.position_id, 0.0), "fees": 0.0, "kinds": []})
        p["net"] += t.net
        p["fees"] += t.fees
        p["exit"] = max(p["exit"], t.exit_ts)
        p["kinds"].append(t.exit_kind)
    rows = [{**p, "r": p["net"] / risk[k] if risk.get(k) else 0.0} for k, p in pos.items()]
    return {"variant": name, "opts": opts, "elapsed_s": round(time.time() - t0), "rejects": dict(res.rejects),
            "signals": res.signals, "positions": rows}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, part in (("all", rows), ("year1", [r for r in rows if r["entry"] < MID]),
                        ("year2", [r for r in rows if r["entry"] >= MID])):
        n = len(part)
        if not n:
            out[label] = {"trades": 0}
            continue
        wins = sum(r["net"] for r in part if r["net"] > 0)
        losses = -sum(r["net"] for r in part if r["net"] < 0)
        days = (MID - START if label == "year1" else END - MID if label == "year2" else END - START) / DAY
        out[label] = {"trades": n, "per_day": round(n / days, 2), "net_usdt": round(sum(r["net"] for r in part), 3),
                      "net_r": round(sum(r["r"] for r in part) / n, 4), "win": round(sum(r["net"] > 0 for r in part) / n, 3),
                      "pf": round(wins / losses, 3) if losses else None, "fees": round(sum(r["fees"] for r in part), 3)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    names = [n for n in VARIANTS if not args.only or n in args.only.split(",")]
    results: dict[str, Any] = {}
    if args.only and OUT.exists():                  # a later variant joins the earlier ones (same data, same BASE)
        results.update(json.loads(OUT.read_text(encoding="utf-8")).get("results") or {})
    with ProcessPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run, n, args.data_dir): n for n in names}
        for f in as_completed(futs):
            r = f.result()
            results[r["variant"]] = {"opts": r["opts"], "elapsed_s": r["elapsed_s"], "rejects": r["rejects"],
                                     "signals": r["signals"], **summarize(r["positions"])}
            s = results[r["variant"]]
            print(f"{r['variant']:9s} {r['elapsed_s']:5d}s  " + "  ".join(
                f"{k}: {s[k].get('trades', 0)} tr ({s[k].get('per_day')}/d) net {s[k].get('net_usdt')} R {s[k].get('net_r')} "
                f"pf {s[k].get('pf')}" for k in ("year1", "year2")), flush=True)
    base = results.get("BASE")
    verdict = {}
    for n, s in results.items():
        if n == "BASE" or base is None:
            continue
        ok = all(s[y].get("trades") and s[y]["net_usdt"] > base[y]["net_usdt"] and s[y]["net_r"] > base[y]["net_r"]
                 for y in ("year1", "year2"))
        verdict[n] = "IMPROVES" if ok else "DOES NOT IMPROVE"
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "results": results, "verdict": verdict},
                              indent=1), encoding="utf-8")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
