"""V8.3 +LADDER audit study (operator review, 2026-10-12). Research only: nothing here changes a frozen bot.

PRE-REGISTERED (written before any result was seen):
  Every variant replays the SAME live engine (LevelMakerEngineV8, EXECUTION_V8 60 s fills, maker TPs, taker stops,
  SizingV6, frozen rules) on 1m bars, V8.3 VWAP snap-back entries, 6 coins (ARB DOGE ENA ETH SOL XRP), one position
  per coin book, daily book reset (as v8_snap_hold_study), engine halts off. 1m bars are walked adverse-first
  (a bar touching both the stop and a target fills the STOP first): the conservative handling of ambiguous bars.

  Exit variants (identical entries):
    E0  LADDER3   the live +LADDER: 1/3 at 0.75R (stop -> entry+0.15%), 1/3 at 1.5R (stop -> +0.75R), 1/3 at 2.5R
    E1  SINGLE15  the live CONTROL: one 1.5R target, no stop moves
    E2  TWO       1/2 at 1.0R (stop -> entry+0.15%), 1/2 at 2.0R
  Entry / risk variants (on top of E0):
    F1  CD60      no new entry within 60 min after a STOP exit on that coin
    F2  VWAPFLAT  enter only when |VWAP(48) now - VWAP(48) 12 bars ago| / ATR14 <= 0.5 (the reversion target is not
                  running away)
    F3  HTFFLAT   enter only when the 1h trend (EMA20/50 + slope, ctx_trend) is "flat"
    P2  CAP2      the six coins as ONE book that holds at most 2 trades at once (E0 trades accepted in time order;
                  closed-trade approximation: a skipped trade does not free the coin for a different entry)
  Windows: W1 = 2026-06-26 .. 2026-10-02 (market.duckdb), split in halves; W2 = 2025-01-01 .. 2026-06-25
  (history_v8.duckdb), independent. Metrics: trades, R/trade after costs (fees in R), total R, profit factor, max
  drawdown in R of the six coins' combined closed-trade R curve.
  PASS = beats E0 on R/trade AND total R, with max DD (R) no more than 10% worse, in BOTH W1 halves AND in W2.
  A variant that fails stays a research note; nothing is deployed from this study.

REVISION 2 (2026-10-12, after the operator's review; the rule and the variants above are unchanged):
  - "PASS" is reported as "RELATIVE IMPROVEMENT" (vs E0) -- it never meant profitable; every variant loses.
  - Two different questions are separated: (a) COMPLETE STRATEGY comparison (each variant's own trades: filters and
    cooldowns remove entries, and exit variants change which later signals are free to fill, so trade counts differ);
    (b) MATCHED-ENTRY exit comparison: only the entries that E0, E1 and E2 all took, so the exits alone differ.
  - Drawdown is also measured on ACCOUNT EQUITY at EVERY 1m update (wallet + open positions marked at each close; the
    hourly thinning only shapes what is kept for charts): the six coins' books summed, fixed daily capital (each
    coin's book restarts at its start equity every UTC day and the day's P&L is banked), in USDT and in % of the
    of the starting capital (it can exceed 100 % because every day is re-funded) and of the running peak.

    python scripts/v8_ladder_audit_study.py            # both windows, 4 processes
"""
from __future__ import annotations

import dataclasses
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DAY = 86_400_000
WINDOWS = {"W1": PROJECT / "data/research/market.duckdb", "W2": PROJECT / "data/research/history_v8.duckdb"}
VARIANTS = ("E0", "E1", "E2", "F1", "F2", "F3")
OUT = PROJECT / "docs" / "V8_LADDER_AUDIT_STUDY.json"


def strategy(variant: str, eng_ref: dict):
    from app.core.types import ExitUpdate, TakeProfit
    from app.competition import v8_config as v8
    from app.strategies.v8.ladder import ladder_class
    base = v8.load_v8()["V8.3"]
    if variant == "E1":
        return base
    if variant == "E2":
        class Two(base):
            LEGS = ((1.0, 0.5), (2.0, 0.5))

            def on_candle(self, c, ctx):
                sigs = super().on_candle(c, ctx)
                for s in sigs:
                    risk, d = abs(s.entry_price - s.stop), (1 if s.side == "long" else -1)
                    s.take_profits = [TakeProfit(s.entry_price + d * r * risk, f) for r, f in self.LEGS]
                    s.be_at_r = self.LEGS[0][0]
                return sigs

            def manage(self, pos, c, ctx):
                if pos.qty_initial <= 0 or len(pos.take_profits) >= len(self.LEGS):
                    return None
                d = 1 if pos.side == "long" else -1
                lock = pos.entry_price * (1 + d * 0.0015)
                return ExitUpdate(stop=lock, reason="be") if (lock - pos.stop) * d > 0 else None
        return Two
    ladder = ladder_class(base)
    if variant == "E0":
        return ladder

    class Filtered(ladder):
        def on_candle(self, c, ctx):
            sigs = super().on_candle(c, ctx)
            if not sigs:
                return sigs
            if variant == "F1":
                eng = eng_ref.get("eng")
                last = next((t for t in reversed(eng.portfolio.closed_trades) if t.symbol == c.symbol), None) if eng else None
                if last is not None and last.exit_kind == "stop" and c.close_time - last.exit_ts < 60 * 60_000:
                    return []
            if variant == "F2":
                cs = self.series(ctx, c.symbol, "5m")
                atr = self.atr(ctx, c.symbol, "5m")
                if len(cs) < 61 or not atr:
                    return []

                def vwap(w):
                    v = sum(x.volume for x in w)
                    return sum((x.high + x.low + x.close) / 3 * x.volume for x in w) / v if v > 0 else None
                a, b = vwap(cs[-48:]), vwap(cs[-60:-12])
                if a is None or b is None or abs(a - b) / atr > 0.5:
                    return []
            if variant == "F3" and self.ctx_trend(ctx, c.symbol, "1h") != "flat":
                return []
            return sigs
    return Filtered


def window(db: Path) -> tuple[int, int]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    lo, hi = con.execute("SELECT max(lo), min(hi) FROM (SELECT symbol, min(ts) lo, max(ts) hi FROM candles_1m "
                         f"WHERE symbol IN {COINS} GROUP BY 1)").fetchone()
    con.close()
    return (int(lo) // DAY + 1) * DAY, (int(hi) // DAY) * DAY


def run(args: tuple) -> dict:
    wname, coin, variant = args
    import duckdb

    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import Candle, MarketRules
    from app.live.v8_engine import LevelMakerEngineV8
    db = WINDOWS[wname]
    start, end = window(db)
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? AND ts >= ? "
                       "AND ts < ? ORDER BY ts", [coin, start, end]).fetchall()
    con.close()
    rules = {s: MarketRules(**r) for s, r in v8.load_freeze()["rules"].items()}
    since = start + 3 * DAY
    ref: dict = {}
    cls = strategy(variant, ref)
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    from array import array
    eq_t, eq_v = array("q"), array("d")

    class Thin(LevelMakerEngineV8):                  # every 1m equity update is recorded compactly for drawdown;
        def _sample_leverage(self, res, sid, eq, _base=LevelMakerEngineV8._sample_leverage):
            _base(self, res, sid, eq)                # the chart series below keeps one point per hour
            e = res.equity
            eq_t.append(int(e[-1][0]))
            eq_v.append(float(e[-1][1]))
            if len(e) >= 2 and e[-1][0] // 3_600_000 == e[-2][0] // 3_600_000:
                e[-2] = e[-1]
                e.pop()
    eng = Thin(settings, [coin], rules={coin: rules[coin]}, seed=7, execution=v8.EXECUTION_V8,
                             fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                             leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True)
    ref["eng"] = eng
    tape = (Candle(coin, "1m", int(t), o, h, lo_, c, v, int(t) + 59_999, True, tv or 0.0, 0, "historical", 0.0)
            for t, o, h, lo_, c, v, tv in rows)
    res = eng.run(cls.for_class("SCALP"), tape, since_ms=since, leverage=20, signal_tf="5m", only_symbol=coin,
                  reset_at=[since + k * DAY for k in range(1, (end - since) // DAY)])
    last = int(rows[-1][0]) if rows else end
    tps: dict = {}
    for f in res.fills:
        if f.kind == "tp":
            tps[f.position_id] = tps.get(f.position_id, 0) + 1
    tr = [t for t in res.trades if t.entry_ts >= since and t.exit_kind != "reset"
          and not (t.exit_kind == "time" and t.exit_ts >= last)]
    # fixed-capital account: each UTC day's book restarts at its start equity; bank the day's P&L at the reset
    start_eq = float(settings.strategy_starting_balance)
    acct_t, acct_v = array("q"), array("f")
    banked, last_v, day = 0.0, start_eq, None
    for t, v in zip(eq_t, eq_v):
        if t < since:
            continue
        d = (t - since) // DAY
        if day is not None and d != day:
            banked += last_v - start_eq
        day, last_v = d, v
        acct_t.append(t)
        acct_v.append(banked + v)
    return {"window": wname, "coin": coin, "variant": variant, "since": since, "end": end, "start_equity": start_eq,
            "acct_t": acct_t.tobytes(), "acct_v": acct_v.tobytes(),
            "trades": [{"entry": t.entry_ts, "exit_ts": t.exit_ts, "side": t.side, "r": t.r_multiple, "net": t.net,
                        "fees": t.fees, "exit": t.exit_kind, "tps": tps.get(t.position_id, 0)} for t in tr]}


def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    curve, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda t: t["exit_ts"]):
        curve += t["r"]
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    win, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {"trades": len(rs), "r_per_trade": round(sum(rs) / len(rs), 4), "total_r": round(sum(rs), 2),
            "pf": round(win / loss, 3) if loss else None, "max_dd_r": round(dd, 2), "win_rate": round(sum(r > 0 for r in rs) / len(rs), 3),
            "net_usdt": round(sum(t["net"] for t in trades), 2)}


def account_dd(rs: list[dict], a: int, b: int) -> dict:
    # % figures use the FIXED starting capital (sum of the coins' start equities), never the window's first value,
    # which is negative by the second half of a losing window
    """Max drawdown of the summed six-coin fixed-capital account at every 1m update inside [a, b)."""
    import numpy as np
    curves = []
    for r in rs:
        t = np.frombuffer(r["acct_t"], dtype=np.int64)
        v = np.frombuffer(r["acct_v"], dtype=np.float32).astype(np.float64)
        m = (t >= a) & (t < b)
        if m.any():
            curves.append((t[m], v[m]))
    if not curves:
        return {}
    grid = np.unique(np.concatenate([t for t, _ in curves]))
    total = np.zeros(len(grid))
    for t, v in curves:                               # carry each coin's last value forward onto the common grid
        idx = np.searchsorted(t, grid, side="right") - 1
        total += np.where(idx >= 0, v[np.clip(idx, 0, None)], v[0])
    peak = np.maximum.accumulate(total)
    dd_usd, dd_pct = peak - total, (peak - total) / peak
    i = int(np.argmax(dd_usd))
    return {"updates": int(len(grid)), "start_usdt": round(float(total[0]), 2), "end_usdt": round(float(total[-1]), 2),
            "max_dd_usdt": round(float(dd_usd.max()), 2),
            # % of the starting capital (6 x 20 USDT): with each day re-funded, cumulative losses can pass 100 %
            "max_dd_pct_of_start": round(100 * float(dd_usd.max()) / sum(r["start_equity"] for r in rs), 2),
            "max_dd_pct_of_peak": round(100 * float(dd_pct.max()), 2),
            "max_dd_at": int(grid[i])}


def cap2(trades: list[dict]) -> list[dict]:
    out, open_ = [], []
    for t in sorted(trades, key=lambda t: t["entry"]):
        open_ = [x for x in open_ if x > t["entry"]]
        if len(open_) < 2:
            out.append(t)
            open_.append(t["exit_ts"])
    return out


def main() -> int:
    jobs = [(w, c, v) for w in WINDOWS for c in COINS for v in VARIANTS]
    with ProcessPoolExecutor(max_workers=4) as ex:      # 12 ran out of memory on the 18-month window
        results = list(ex.map(run, jobs))
    report: dict = {"preregistered": __doc__, "windows": {}}
    for w in WINDOWS:
        rs = [r for r in results if r["window"] == w]
        since, end = rs[0]["since"], rs[0]["end"]
        mid = since + (end - since) // 2
        parts = {"full": (since, end), "half1": (since, mid), "half2": (mid, end)}
        table: dict = {}
        for v in VARIANTS + ("P2",):
            src = "E0" if v == "P2" else v
            tr = [t for r in rs if r["variant"] == src for t in r["trades"]]
            if v == "P2":
                tr = cap2(tr)
            table[v] = {p: stats([t for t in tr if a <= t["entry"] < b]) for p, (a, b) in parts.items()}
            if v != "P2":
                for p, (a, b) in parts.items():
                    table[v][p]["account"] = account_dd([r for r in rs if r["variant"] == src], a, b)
            table[v]["by_coin"] = {r["coin"]: stats(r["trades"]) for r in rs if r["variant"] == src}
        e0 = [t for r in rs if r["variant"] == "E0" for t in r["trades"]]
        table["E0_legs"] = {f"{k}tp_{e}": len([t for t in e0 if t["tps"] == k and t["exit"] == e])
                            for k in range(4) for e in ("tp", "be", "time", "stop") if [t for t in e0 if t["tps"] == k and t["exit"] == e]}
        e1 = {(r["coin"], t["entry"]): t for r in rs if r["variant"] == "E1" for t in r["trades"]}
        paired = [(t["r"], e1[(r["coin"], t["entry"])]["r"]) for r in rs if r["variant"] == "E0" for t in r["trades"] if (r["coin"], t["entry"]) in e1]
        table["paired_E0_vs_E1"] = {"pairs": len(paired), "mean_E0": round(sum(a for a, _ in paired) / len(paired), 4),
                                    "mean_E1": round(sum(b for _, b in paired) / len(paired), 4)} if paired else {}
        # (b) MATCHED-ENTRY exit comparison: entries all three exit variants took (same coin, time and side)
        keyed = {v: {(r["coin"], t["entry"], t["side"]): t for r in rs if r["variant"] == v for t in r["trades"]}
                 for v in ("E0", "E1", "E2")}
        common = set(keyed["E0"]) & set(keyed["E1"]) & set(keyed["E2"])
        table["matched_entries"] = {v: {p: stats([keyed[v][k] for k in common if a <= k[1] < b]) for p, (a, b) in parts.items()}
                                    for v in ("E0", "E1", "E2")}
        table["matched_entries"]["entries"] = len(common)
        report["windows"][w] = {"since": since, "mid": mid, "end": end, "variants": table}
    verdict = {}
    for v in ("E1", "E2", "F1", "F2", "F3", "P2"):
        ok = True
        for w, p in (("W1", "half1"), ("W1", "half2"), ("W2", "full")):
            a, b = report["windows"][w]["variants"][v][p], report["windows"][w]["variants"]["E0"][p]
            ok &= bool(a.get("trades")) and a["r_per_trade"] > b["r_per_trade"] and a["total_r"] > b["total_r"] \
                and a["max_dd_r"] <= 1.10 * b["max_dd_r"]
        verdict[v] = "RELATIVE IMPROVEMENT vs E0 (still loses)" if ok else "no relative improvement"
    report["verdict"] = verdict
    report["note"] = "Every variant loses money in every window; a relative improvement is not profitability."
    OUT.write_text(json.dumps(report, indent=1))
    for w, x in report["windows"].items():
        print(f"== {w}")
        for v in VARIANTS + ("P2",):
            s = x["variants"][v]
            print(f"  {v:3s} " + " | ".join(f"{p}: n {s[p].get('trades', 0):5d} R/t {s[p].get('r_per_trade', 0):+.4f} tot {s[p].get('total_r', 0):+8.1f} DD {s[p].get('max_dd_r', 0):6.1f} PF {s[p].get('pf')}"
                                       for p in ("half1", "half2", "full")))
        print("  E0 legs:", x["variants"]["E0_legs"], "| paired E0 vs E1:", x["variants"]["paired_E0_vs_E1"])
        for v in VARIANTS:
            acc = x["variants"][v]
            print(f"  account {v:3s} " + " | ".join(f"{p}: DD {acc[p].get('account', {}).get('max_dd_usdt')} USDT "
                                                   f"({acc[p].get('account', {}).get('max_dd_pct_of_start')}% of start), end {acc[p].get('account', {}).get('end_usdt')}"
                                                   for p in ("half1", "half2", "full")))
        me = x["variants"]["matched_entries"]
        print(f"  matched entries ({me['entries']}): " + " | ".join(
            f"{v} R/t {me[v]['full'].get('r_per_trade')} DD(R) {me[v]['full'].get('max_dd_r')}" for v in ("E0", "E1", "E2")))
    print("verdict:", verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
