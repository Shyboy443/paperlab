"""V8.1 (micro pullback) and V8.2 (micro breakout): can one change fix them? (operator, 2026-10-04: "fix the V8.1 and
V8.2 bots")

    python scripts/v8_trend_fix_study.py           (needs the research store: research/ingest.py)

Why: in the V8 run started 2026-10-03 the V8.1 controls won 2 of 12 trades and V8.2 0 of 14 (a weekend; small), and in
the 2026-10-02 cost study both still lost ~0.1 R a trade after every fix adopted then (resting targets, 1.0 / 1.2%
minimum stops, held to the UTC day close).

PRE-REGISTERED (2026-10-04, before the first run):
  Data: the research store's Bybit 1m bars (data/research/market.duckdb, 2026-06-26 .. 2026-10-02) for V8's six coins,
  the first 14 days warm-up (the HTF variant needs 300 hourly candles; every variant uses the same window), the rest
  split into a first and a second half.
  Bot: V8.1 / V8.2 exactly as they run live (VwapSnap's siblings in app/strategies/v8/arena.py, the live engine
  LevelMakerEngineV8 with resting targets, SizingV6 1% risk, 20 USDT reset daily) = BASE.
  One change at a time:
    T10    target 1.0 R (BASE 1.5 R)            T20    target 2.0 R
    H180   time limit 3 h (BASE: the day close) H360   time limit 6 h
    HTF    V14's higher-timeframe rule (app/strategies/v14/htf.py: only with the "4h" and daily trend of 1h candles)
    BE1    stop to break-even (+ 15 bp, the fees) once the trade is 1 R in profit
  Per family, a change PASSES only if its net R per trade beats BASE in BOTH halves. If several pass, their combination
  is run too (of T10 / T20 only the better one, likewise H180 / H360); the combination is adopted if it beats the best
  single passing change on the whole window, else that single change. Nothing passes -> no change for that family.
  Reported for information (no selection): BASE by side (long / short) and by exit kind.

RESULT of the run above (2026-10-04): NO change passed for either family (docs/V8_TREND_FIX_STUDY.json).

CONFIRMATION (pre-registered 2026-10-04 after that result, BEFORE any run on the new data; `--confirm`):
  The one pattern in that run -- shorts lost far more than longs in BOTH families and BOTH halves (V8.1 -0.158 vs
  -0.113 R, V8.2 -0.171 vs -0.037 R) -- was found on the same data, so it is a HYPOTHESIS. It is tested on an
  INDEPENDENT window that no V8 decision has used: Bybit 1m bars 2025-01-01 .. 2026-06-26 for the same six coins
  (data/research/history_v8.duckdb, research/ingest_history.py), 14 days warm-up, split into halves. That window holds
  falling as well as rising markets, so a long-only rule cannot pass on a bull market alone.
    LONGONLY   the family takes long set-ups only            (both families)
    BE1        V8.1 only (its near miss above)               HTF   V8.2 only (its near miss above)
  ADOPT a candidate for a family only if its net R per trade beats BASE in BOTH halves of the independent window
  (if two pass for a family: the better whole-window net R). Otherwise the family stays as it is.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
DB = PROJECT / "data" / "research" / "market.duckdb"
CONFIRM_DB = PROJECT / "data" / "research" / "history_v8.duckdb"
CONFIRM_OUT = PROJECT / "docs" / "V8_TREND_FIX_CONFIRM.json"
CONFIRM_WINDOW = (1735689600000, 1782432000000)          # 2025-01-01 .. 2026-06-26 UTC
CONFIRM = {"V8.1": ("BASE", "LONGONLY", "BE1"), "V8.2": ("BASE", "LONGONLY", "HTF")}
OUT = PROJECT / "docs" / "V8_TREND_FIX_STUDY.json"
DAY = 86_400_000
COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
FAMILIES = ("V8.1", "V8.2")
SINGLES = ("BASE", "T10", "T20", "H180", "H360", "HTF", "BE1")
WARMUP_DAYS = 14


def window(db: Path = DB) -> tuple[int, int]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    lo, hi = con.execute("SELECT max(lo), min(hi) FROM (SELECT symbol, min(ts) lo, max(ts) hi FROM candles_1m "
                         f"WHERE symbol IN {COINS} GROUP BY 1)").fetchone()
    con.close()
    return (int(lo) // DAY + 1) * DAY, (int(hi) // DAY) * DAY


def variant_class(fid: str, name: str):
    from app.strategies.v8.arena import load_v8_scalpers
    from app.strategies.v14.htf import htf_single
    base = load_v8_scalpers()[fid]
    mods = () if name == "BASE" else tuple(name.split("+"))
    over: dict = {}
    for m in mods:
        if m in ("T10", "T20"):
            over["target_r"] = int(m[1:]) / 10.0
        if m in ("H180", "H360"):
            over.update(hold_to_day_close=False, max_hold_min=float(m[1:]))
    params = dataclasses.replace(base.Params(), **over) if over else None
    be = "BE1" in mods
    long_only = "LONGONLY" in mods

    class Variant(base):
        def __init__(self, p=None, _params=params):
            super().__init__(p or _params)

        def on_candle(self, c, ctx, _be=be, _lo=long_only):
            sigs = super().on_candle(c, ctx)
            if _lo:
                sigs = [s for s in sigs if getattr(s, "kind", "entry") != "entry" or s.side == "long"]
            if _be:
                for s in sigs:
                    if getattr(s, "kind", "entry") == "entry":
                        s.be_at_r = 1.0
            return sigs
    cls = Variant.for_class("SCALP")
    return htf_single(cls, fid, base.name) if "HTF" in mods else cls


def run(args: tuple) -> list[dict]:
    coin, names, start, end = args[:4]
    db = args[4] if len(args) > 4 else DB
    fams = args[5] if len(args) > 5 else {f: names for f in FAMILIES}
    import duckdb

    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import Candle, MarketRules
    from app.live.v8_engine import LevelMakerEngineV8
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? AND ts >= ? "
                       "AND ts < ? ORDER BY ts", [coin, start, end]).fetchall()
    con.close()
    rules = {s: MarketRules(**r) for s, r in v8.load_freeze()["rules"].items()}
    since = start + WARMUP_DAYS * DAY
    last = int(rows[-1][0])
    out = []
    for fid in FAMILIES:
        for name in fams.get(fid, ()):
            settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
            eng = LevelMakerEngineV8(settings, [coin], rules={coin: rules[coin]}, seed=7, execution=v8.EXECUTION_V8,
                                     fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                                     leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True,
                                     be_cover_bps=15.0 if "BE1" in name else None)
            tape = (Candle(coin, "1m", int(t), o, h, lo, c, v, int(t) + 59_999, True, tv or 0.0, 0, "historical", 0.0)
                    for t, o, h, lo, c, v, tv in rows)
            res = eng.run(variant_class(fid, name), tape, since_ms=since, leverage=20, signal_tf="5m", only_symbol=coin,
                          reset_at=[since + k * DAY for k in range(1, (end - since) // DAY)])
            tr = [t for t in res.trades if t.entry_ts >= since and t.exit_kind != "reset"
                  and not (t.exit_kind == "time" and t.exit_ts >= last)]
            out.append({"family": fid, "variant": name, "coin": coin, "since": since, "end": end,
                        "trades": [{"ts": t.entry_ts, "r": t.r_multiple, "net": t.net, "exit": t.exit_kind,
                                    "side": t.side} for t in tr]})
    return out


def summarize(trades: list[dict], mid: int, days: float) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    h = [np.array([t["r"] for t in trades if (t["ts"] >= mid) == bool(i)]) for i in (0, 1)]
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": len(trades), "per_coin_day": round(len(trades) / days / len(COINS), 2),
            "win": round(float(np.mean([t["net"] > 0 for t in trades])), 3), "net_r": round(float(r.mean()), 4),
            "net_r_half1": round(float(h[0].mean()), 4) if len(h[0]) else None,
            "net_r_half2": round(float(h[1].mean()), 4) if len(h[1]) else None,
            "pf": round(float(pos / neg), 3) if neg else None, "net_usdt": round(float(sum(t["net"] for t in trades)), 2),
            "exits": dict(Counter(t["exit"] for t in trades))}


def main() -> None:
    t0 = time.time()
    start, end = window()
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [x for part in pool.map(run, [(c, SINGLES, start, end) for c in COINS]) for x in part]
    since = rows[0]["since"]
    mid, days = since + (end - since) // 2, (end - since) / DAY

    def summ(fid, name):
        return summarize([t for r in rows if r["family"] == fid and r["variant"] == name for t in r["trades"]], mid, days)
    summary = {f: {n: summ(f, n) for n in SINGLES} for f in FAMILIES}
    combos = {}
    for f in FAMILIES:
        b = summary[f]["BASE"]
        ok = [n for n in SINGLES if n != "BASE" and summary[f][n].get("trades")
              and summary[f][n]["net_r_half1"] > b["net_r_half1"] and summary[f][n]["net_r_half2"] > b["net_r_half2"]]
        for pair in (("T10", "T20"), ("H180", "H360")):
            both = [n for n in ok if n in pair]
            if len(both) == 2:
                worse = min(both, key=lambda n: summary[f][n]["net_r"])
                ok.remove(worse)
        summary[f]["_passing"] = ok
        if len(ok) > 1:
            combos[f] = "+".join(ok)
    if combos:
        with ProcessPoolExecutor(max_workers=6) as pool:
            extra = [x for part in pool.map(run, [(c, tuple(sorted(set(combos.values()))), start, end) for c in COINS])
                     for x in part]
        rows += extra
    chosen = {}
    for f in FAMILIES:
        ok = summary[f]["_passing"]
        if f in combos:
            summary[f][combos[f]] = summ(f, combos[f])
            best = max(ok, key=lambda n: summary[f][n]["net_r"])
            chosen[f] = combos[f] if summary[f][combos[f]]["net_r"] > summary[f][best]["net_r"] else best
        else:
            chosen[f] = ok[0] if ok else "BASE"
        base_tr = [t for r in rows if r["family"] == f and r["variant"] == "BASE" for t in r["trades"]]
        summary[f]["_base_by_side"] = {s: summarize([t for t in base_tr if t["side"] == s], mid, days)
                                       for s in ("long", "short")}
        summary[f]["_base_r_by_exit"] = {k: round(float(np.mean([t["r"] for t in base_tr if t["exit"] == k])), 3)
                                         for k in {t["exit"] for t in base_tr}}
        for n, s in summary[f].items():
            if n.startswith("_"):
                continue
            print(f"{f} {n:18s} trades {s.get('trades', 0):5d} ({s.get('per_coin_day')}/coin-day) win {s.get('win')} net R "
                  f"{s.get('net_r')} (h1 {s.get('net_r_half1')} h2 {s.get('net_r_half2')}) pf {s.get('pf')} "
                  f"net {s.get('net_usdt')} USDT{'  PASSES' if n in ok else ''}{'  <= ADOPT' if n == chosen[f] and n != 'BASE' else ''}",
                  flush=True)
        print(f"{f} BASE by side: " + ", ".join(f"{k} {v.get('trades')} trades R {v.get('net_r')} (h1 {v.get('net_r_half1')} "
                                                f"h2 {v.get('net_r_half2')})" for k, v in summary[f]["_base_by_side"].items()))
        print(f"{f} BASE mean R by exit: {summary[f]['_base_r_by_exit']}")
    print("chosen:", chosen)
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "summary": summary, "chosen": chosen},
                              indent=1, default=str))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


def confirm() -> None:
    t0 = time.time()
    start, end = window(CONFIRM_DB)
    start, end = max(start, CONFIRM_WINDOW[0]), min(end, CONFIRM_WINDOW[1])
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [x for part in pool.map(run, [(c, (), start, end, CONFIRM_DB, CONFIRM) for c in COINS]) for x in part]
    since = rows[0]["since"]
    mid, days = since + (end - since) // 2, (end - since) / DAY
    out, chosen = {}, {}
    for f, names in CONFIRM.items():
        out[f] = {n: summarize([t for r in rows if r["family"] == f and r["variant"] == n for t in r["trades"]], mid, days)
                  for n in names}
        b = out[f]["BASE"]
        ok = [n for n in names if n != "BASE" and out[f][n].get("trades")
              and out[f][n]["net_r_half1"] > b["net_r_half1"] and out[f][n]["net_r_half2"] > b["net_r_half2"]]
        chosen[f] = max(ok, key=lambda n: out[f][n]["net_r"]) if ok else "BASE"
        base_tr = [t for r in rows if r["family"] == f and r["variant"] == "BASE" for t in r["trades"]]
        out[f]["_base_by_side"] = {sd: summarize([t for t in base_tr if t["side"] == sd], mid, days) for sd in ("long", "short")}
        for n in names:
            s = out[f][n]
            print(f"{f} {n:9s} trades {s.get('trades', 0):6d} win {s.get('win')} net R {s.get('net_r')} "
                  f"(h1 {s.get('net_r_half1')} h2 {s.get('net_r_half2')}) pf {s.get('pf')} net {s.get('net_usdt')} USDT"
                  f"{'  PASSES' if n in ok else ''}{'  <= ADOPT' if n == chosen[f] and n != 'BASE' else ''}", flush=True)
        print(f"{f} BASE by side: " + ", ".join(f"{k} {v.get('trades')} trades R {v.get('net_r')} (h1 {v.get('net_r_half1')} "
                                                f"h2 {v.get('net_r_half2')})" for k, v in out[f]["_base_by_side"].items()))
    print("chosen:", chosen)
    CONFIRM_OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "window": [since, end],
                                       "rules": __doc__.split("CONFIRMATION")[1], "results": out, "chosen": chosen},
                                      indent=1, default=str))
    print(f"wrote {CONFIRM_OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    confirm() if "--confirm" in sys.argv else main()
