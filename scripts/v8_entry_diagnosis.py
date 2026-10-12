"""V8.3 entry diagnosis (2026-10-12): where does the loss come from? Research only, on the ALREADY INSPECTED windows
(W1 2026-06-26..10-02 with recorded funding, W2 2025-01-01..2026-06-25 without funding data), CONTROL exits (one 1.5R
target), the live engine. Every trade's net result is split into:

    gross       price move from the SIGNAL close to the exit, at mid (what the entry idea earns before any cost)
    latency     the 60 s between the signal and the fill (price drift, part of slippage)
    spread      half-spreads paid on taker fills (entry, stop, time exit)
    impact      size impact (zero at these sizes)
    fees        exchange fees (taker entry/stop/time, maker target)
    funding     settlements while held (W1 only)
    net = gross - latency - spread - impact - fees + funding        (all in R of the trade's initial risk)

It also records, at each signal, the distance from the close to the rolling 4h VWAP (the reversion target) relative to
the 1.5R target distance, to see whether entries whose expected reversion covers the target and the costs do better.
The new entry hypothesis is chosen from this and frozen BEFORE any untouched data is read (docs/V8_COSTGATE_FREEZE.json).

    python scripts/v8_entry_diagnosis.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DAY = 86_400_000
WINDOWS = {"W1": PROJECT / "data/research/market.duckdb", "W2": PROJECT / "data/research/history_v8.duckdb"}
OUT = PROJECT / "docs" / ("V8_ENTRY_DIAGNOSIS.json" if not __import__("os").environ.get("DIAG_SIGNAL_LATENCY_MS")
                          else f"V8_ENTRY_DIAGNOSIS_latency{__import__('os').environ['DIAG_SIGNAL_LATENCY_MS']}ms.json")


def window(db: Path) -> tuple[int, int]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    lo, hi = con.execute("SELECT max(lo), min(hi) FROM (SELECT symbol, min(ts) lo, max(ts) hi FROM candles_1m "
                         f"WHERE symbol IN {COINS} GROUP BY 1)").fetchone()
    con.close()
    return (int(lo) // DAY + 1) * DAY, (int(hi) // DAY) * DAY


def run(args: tuple) -> dict:
    wname, coin = args
    import duckdb

    from app.backtest.funding import FundingSchedule
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import Candle, MarketRules
    from app.live.v8_engine import LevelMakerEngineV8
    db = WINDOWS[wname]
    start, end = window(db)
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? AND ts >= ? "
                       "AND ts < ? ORDER BY ts", [coin, start, end]).fetchall()
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    fund = None
    if "funding" in tabs:
        cols = [r[0] for r in con.execute("DESCRIBE funding").fetchall()]
        tcol = "ts" if "ts" in cols else cols[1]
        rcol = next(c for c in cols if "rate" in c)
        fund = FundingSchedule({coin: [(int(t), float(r)) for t, r in con.execute(
            f"SELECT {tcol}, {rcol} FROM funding WHERE symbol = ? ORDER BY 1", [coin]).fetchall()]})
    con.close()
    rules = {s: MarketRules(**r) for s, r in v8.load_freeze()["rules"].items()}
    base = v8.load_v8()["V8.3"]
    seen: dict = {}

    class Probe(base):                                    # records the VWAP distance at each signal (no behaviour change)
        def on_candle(self, c, ctx):
            sigs = super().on_candle(c, ctx)
            if sigs:
                cs = self.series(ctx, c.symbol, "5m")[-48:]
                vol = sum(x.volume for x in cs)
                vwap = sum((x.high + x.low + x.close) / 3 * x.volume for x in cs) / vol if vol > 0 else None
                for s in sigs:
                    stop_pct = abs(s.entry_price - s.stop) / s.entry_price
                    seen[(c.close_time, s.side)] = {"vwap_dist_pct": abs(vwap - c.close) / c.close if vwap else None,
                                                     "target_pct": base.Params().target_r * stop_pct,
                                                     "signal_px": c.close}
            return sigs
    since = start + 3 * DAY
    settings = __import__("dataclasses").replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    import dataclasses
    import os
    lat = os.environ.get("DIAG_SIGNAL_LATENCY_MS")       # what-if: a faster entry (default: the live 59.6 s window)
    execution = dataclasses.replace(v8.EXECUTION_V8, signal_latency_ms=int(lat)) if lat else v8.EXECUTION_V8
    eng = LevelMakerEngineV8(settings, [coin], rules={coin: rules[coin]}, seed=7, execution=execution,
                             fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False), funding=fund,
                             leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True)
    tape = (Candle(coin, "1m", int(t), o, h, lo_, c, v, int(t) + 59_999, True, tv or 0.0, 0, "historical", 0.0)
            for t, o, h, lo_, c, v, tv in rows)
    res = eng.run(Probe.for_class("SCALP"), tape, since_ms=since, leverage=20, signal_tf="5m", only_symbol=coin,
                  reset_at=[since + k * DAY for k in range(1, (end - since) // DAY)])
    last = int(rows[-1][0]) if rows else end
    fills = defaultdict(list)
    for f in res.fills:
        fills[f.position_id].append(f)
    out = []
    for t in res.trades:
        if t.entry_ts < since or t.exit_kind == "reset" or (t.exit_kind == "time" and t.exit_ts >= last):
            continue
        fl = fills[t.position_id]
        entry = next(f for f in fl if f.kind == "entry")
        risk = float((entry.meta or {}).get("risk_usd") or 0) or abs(t.entry_price - float((entry.meta or {}).get("stop") or t.entry_price)) * t.qty
        if risk <= 0:
            continue
        g = lambda key: sum(float((f.meta or {}).get(key) or 0.0) for f in fl if f.kind != "funding")
        funding = sum(f.realized_pnl for f in fl if f.kind == "funding")
        fees = sum(f.fee for f in fl)
        lat, spr, imp = g("latency_cost"), g("spread_cost"), g("impact_cost")
        net = t.net + funding
        gross = net + lat + spr + imp + fees - funding
        sig = seen.get((t.entry_ts - 60_001, t.side))   # fill = open of the 1m bar starting 60 s after the 5m close
        if sig is None:                                  # fallback: the nearest earlier signal on that side (<= 10 min)
            near = [k for k in seen if k[1] == t.side and 0 <= t.entry_ts - k[0] <= 600_000]
            sig = seen[max(near)] if near else {}
        out.append({"coin": coin, "side": t.side, "entry": t.entry_ts, "exit": t.exit_kind, "r_net": net / risk,
                    "r_gross": gross / risk, "r_latency": lat / risk, "r_spread": spr / risk, "r_impact": imp / risk,
                    "r_fees": fees / risk, "r_funding": funding / risk, "usd_net": net, "usd_gross": gross,
                    "usd_fees": fees, "usd_spread": spr, "usd_latency": lat, "usd_funding": funding,
                    "vwap_cover": (sig.get("vwap_dist_pct") / sig["target_pct"]) if sig.get("vwap_dist_pct") and sig.get("target_pct") else None})
    return {"window": wname, "coin": coin, "trades": out}


def summarize(tr: list[dict]) -> dict:
    n = len(tr)
    if not n:
        return {"trades": 0}
    m = lambda k: round(sum(t[k] for t in tr) / n, 4)
    s = lambda k: round(sum(t[k] for t in tr), 2)
    return {"trades": n, "R_per_trade": {k: m("r_" + k) for k in ("gross", "latency", "spread", "impact", "fees", "funding", "net")},
            "usdt_total": {k: s("usd_" + k) for k in ("gross", "latency", "spread", "fees", "funding", "net")},
            "win_rate_gross": round(sum(t["r_gross"] > 0 for t in tr) / n, 3)}


def main() -> int:
    jobs = [(w, c) for w in WINDOWS for c in COINS]
    with ProcessPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(run, jobs))
    report: dict = {"doc": __doc__, "windows": {}}
    for w in WINDOWS:
        tr = [t for r in results if r["window"] == w for t in r["trades"]]
        buckets = {}
        for lo, hi in ((0, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.25), (1.25, 9e9)):
            b = [t for t in tr if t["vwap_cover"] is not None and lo <= t["vwap_cover"] < hi]
            buckets[f"vwap_dist/target {lo}-{hi if hi < 9e9 else 'inf'}"] = summarize(b)
        report["windows"][w] = {"all": summarize(tr), "by_side": {s: summarize([t for t in tr if t["side"] == s]) for s in ("long", "short")},
                                "by_coin": {c: summarize([t for t in tr if t["coin"] == c]) for c in COINS},
                                "by_vwap_cover": buckets}
    OUT.write_text(json.dumps(report, indent=1))
    for w, x in report["windows"].items():
        a = x["all"]
        print(f"== {w}: {a['trades']} trades, R/trade {a['R_per_trade']} | USDT {a['usdt_total']} | gross win rate {a['win_rate_gross']}")
        for k, v in x["by_vwap_cover"].items():
            if v.get("trades"):
                print(f"   {k:28s} n {v['trades']:6d}  gross {v['R_per_trade']['gross']:+.4f}  costs {v['R_per_trade']['gross'] - v['R_per_trade']['net']:+.4f}  net {v['R_per_trade']['net']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
