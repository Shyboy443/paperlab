"""Scribd round 2: swing / trend / session-breakout study (pre-registered in docs/SCRIBD_SWING_STUDY.md).

    python scripts/scribd_swing_study.py [--only key,key]

Data:
- MT5 daily: data/mt5_cache/<SYM>_D1.npy;
- MT5 1-minute since 2015: data/mt5_cache/<SYM>_M1L.npy (scripts/mt5_export_tf.py --tf M1 --tag L), all UTC; the
  London breakout and 20-pips rules run on 1m candles, Big Ben on 5m, London Breakfast on 15m;
- stocks: Yahoo Finance daily, cached in data/stocks_cache/<SYM>_D1.npy.
Writes app/ytstrats/swing_results.json (served read-only by /api/public/scribd-swing).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, scalp, swing  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
WIN = {"a": (ms("2015-01-01"), ms("2021-01-01")), "b": (ms("2021-01-01"), ms("2026-10-21"))}
# the broker's 1-minute history starts in October 2020: the session rules split what exists in two halves
WIN_SESSION = {"a": (ms("2020-11-01"), ms("2023-11-01")), "b": (ms("2023-11-01"), ms("2026-10-21"))}
MT5 = PROJECT / "data" / "mt5_cache"
STOCKS = PROJECT / "data" / "stocks_cache"
OUT = PROJECT / "app" / "ytstrats" / "swing_results.json"
SLIP, COMM, STOCK_COST = 0.00002, 0.000025, 0.0003
US500_SWAP_LONG_PCT_YEAR = 6.14
INDICES = ("US30", "US500", "NAS100", "UK100", "GER40")
SPEC = json.loads((MT5 / "spec.json").read_text())["symbols"]


def mt5_cost(sym: str) -> float:
    if sym in SPEC:
        spread = SPEC[sym]["spread_mean_frac"]
    else:
        for tf in ("M15", "M1L", "M5", "D1"):
            f = MT5 / f"{sym}_{tf}.npy"
            if f.exists():
                a = np.load(f)
                spread = float(np.mean(a[:, 6] / a[:, 4]))
                break
    return spread / 2 + SLIP + (0.0 if sym in INDICES else COMM)


def daily(sym: str) -> tuple[swing.D, float]:
    if sym in swing.DOW30:
        f = STOCKS / f"{sym}_D1.npy"
        if not f.exists():
            import yfinance as yf
            STOCKS.mkdir(parents=True, exist_ok=True)
            df = yf.download(sym, start="2014-01-01", end="2026-10-21", auto_adjust=True, progress=False)
            df = df.dropna()
            t = (df.index.tz_localize("UTC").asi8 // 1_000_000).astype(float) if df.index.tz is None else df.index.asi8 / 1e6
            col = lambda k: np.asarray(df[k]).reshape(-1).astype(float)  # noqa: E731
            np.save(f, np.column_stack([t, col("Open"), col("High"), col("Low"), col("Close"), col("Volume")]))
        a = np.load(f)
        return swing.D(*(a[:, k] for k in range(5))), STOCK_COST
    a = np.load(MT5 / f"{sym}_D1.npy")
    a = a[a[:, 2] > a[:, 3] - 1e-12]
    return swing.D(*(a[:, k] for k in range(5))), mt5_cost(sym)


TF = {"london_bo": 1, "pips20_daily": 1, "big_ben_fade": 5, "london_breakfast": 15}


def intraday(sym: str, tf: int) -> swing.M | None:
    """Candles of tf minutes built from the 1-minute export since 2015 (data/mt5_cache/<SYM>_M1L.npy)."""
    f = MT5 / f"{sym}_M1L.npy"
    if not f.exists():
        return None
    a = np.load(f)[:, :5]
    if tf != 1:
        step = tf * 60_000
        key = (a[:, 0] // step).astype(np.int64)
        starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        ends = np.r_[starts[1:], len(a)]
        a = np.column_stack([key[starts] * step, a[starts, 1], np.maximum.reduceat(a[:, 2], starts),
                             np.minimum.reduceat(a[:, 3], starts), a[ends - 1, 4]]).astype(float)
    return swing.M(*(a[:, k] for k in range(5)), tf=tf)


def run_rule(r: swing.Rule, tp_pips: float | None = None, zero_cost: bool = False):
    per = {"a": [], "b": []}
    by_asset: dict[str, float | None] = {}
    missing = []
    wins = WIN_SESSION if r.kind == "session" else WIN
    for sym in r.assets:
        if r.kind in ("plan", "turtle"):
            b, cost = daily(sym)
        else:
            b = intraday(sym, TF[r.key])
            if b is None:
                missing.append(sym)
                continue
            cost = mt5_cost(sym)
        cost = 0.0 if zero_cost else cost
        pip = 0.10 if sym in swing.DOW30 else swing.PIP.get(sym, 0.0)
        for w, (s0, s1) in wins.items():
            if r.kind == "plan":
                tr = scalp.replay(b.t, b.o, b.h, b.l, b.c, r.fn(b, pip), s0, s1, cost)
            elif r.kind == "turtle":
                tr = r.fn(b, cost, s0, s1)
            elif r.key == "pips20_daily":
                tr = swing.pips20_daily(b, pip, cost, sym, s0, s1, daily(sym)[0])
            elif r.key == "london_breakfast" and tp_pips is not None:
                tr = swing.london_breakfast(b, pip, cost, sym, s0, s1, tp_pips=tp_pips)
            else:
                tr = r.fn(b, pip, cost, sym, s0, s1)
            for x in tr:
                x["asset"] = sym
            per[w] += tr
            if w == "b":
                by_asset[sym] = float(sum(x["ret_pct"] for x in tr)) if tr else None
    return per, by_asset, missing


def buy_and_hold(sym: str) -> dict[str, float]:
    b, _ = daily(sym)
    out = {}
    for w, (s0, s1) in WIN.items():
        idx = np.flatnonzero((b.t >= s0) & (b.t < s1))
        if len(idx) > 1:
            out[w] = float((b.c[idx[-1]] / b.o[idx[0]] - 1) * 100)
    return out


def random_timing(r: swing.Rule) -> dict[str, dict[str, float]]:
    """Not part of the verdict: a long-only rule's trades vs random entry days with the same holding times on the same
    markets and window (20 draws per trade). Separates timing skill from simply being long a rising market."""
    rng = np.random.default_rng(7)
    out = {}
    for w, (s0, s1) in WIN.items():
        real, rand = [], []
        for sym in r.assets:
            b, cost = daily(sym)
            pip = 0.10 if sym in swing.DOW30 else swing.PIP.get(sym, 0.0)
            idx = np.flatnonzero((b.t >= s0) & (b.t < s1))
            for x in scalp.replay(b.t, b.o, b.h, b.l, b.c, r.fn(b, pip), s0, s1, cost):
                hold = max(int(np.searchsorted(b.t, x["exit_t"])) - int(np.searchsorted(b.t, x["t"])), 1)
                real.append(x["ret_pct"])
                for _ in range(20):
                    a = int(rng.integers(idx[0], max(idx[0] + 1, idx[-1] - hold)))
                    rand.append(((b.o[a + hold] * (1 - cost)) / (b.o[a] * (1 + cost)) - 1) * 100)
        if real:
            re, ra = np.array(real), np.array(rand)
            out[w] = {"rule_mean_pct": float(re.mean()), "random_mean_pct": float(ra.mean()),
                      "edge_pct": float(re.mean() - ra.mean()), "z": float((re.mean() - ra.mean()) / (re.std() / np.sqrt(len(re))))}
    return out


def headline(rows: list[dict]) -> str:
    passed = [r for r in rows if r["verdict"] == "PASS"]
    if not passed:
        return "No rule passes. Trend following (Turtles), the Holy Grail and every London-session breakout lose after costs."
    parts = []
    for r in passed:
        t, sw = r.get("random_timing", {}), r.get("swap_sensitivity", {})
        txt = f"“{r['doc'].split(' (')[0]}” passes the fixed test (PF {r['a']['profit_factor']:.2f} in 2015–2020, {r['b']['profit_factor']:.2f} in 2021–2026)"
        if sw.get("a"):
            txt += f", but with your broker's overnight financing 2015–2020 turns negative (PF {sw['a']['profit_factor']:.2f})"
        if t.get("a"):
            txt += f" and in 2015–2020 it did {t['a']['edge_pct']:+.2f}% per trade versus random entry days"
        parts.append(txt + ".")
    return " ".join(parts) + " Not a consistent winner: at most a forward paper-test candidate. Turtles, Holy Grail and every London-session breakout lose after costs."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    rows = []
    fmt = lambda x: datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    for r in swing.RULES:
        if only and r.key not in only:
            continue
        per, by_asset, missing = run_rule(r)
        gross, _, _ = run_rule(r, zero_cost=True)
        sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
        low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
        traded = [v for v in by_asset.values() if v is not None]
        min_b = 30 if r.single_market else 100
        checks = {
            "a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
            "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= min_b),
            "b_ci_above_zero": bool(low > 0),
            "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
        }
        first_t = min((x["t"] for x in per["a"] + per["b"]), default=None)
        row = {"key": r.key, "doc": r.doc, "assets": [s for s in r.assets if s not in missing], "missing_data": missing,
               "kind": r.kind, "note": r.note, "rule": (r.fn.__doc__ or "").strip() if r.kind != "turtle" else (swing.turtle.__doc__ or "").strip(),
               "windows": {k: [fmt(v[0]), fmt(v[1])] for k, v in (WIN_SESSION if r.kind == "session" else WIN).items()}, "first_trade": fmt(first_t) if first_t else None,
               "a": sa, "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset},
               "gross": {"a": scalp.summarize(gross["a"]), "b": scalp.summarize(gross["b"])},
               "min_trades_b": min_b, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
               "tf": {"plan": "daily", "turtle": "daily"}.get(r.kind, f"{TF.get(r.key, 5)}m"),
               "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
        if r.key.startswith(("rsi2", "r2_")):
            row["buy_and_hold_pct"] = buy_and_hold("US500")
        if r.key.startswith(("rsi2", "r2_")):
            # not part of the verdict: the broker's long US500 financing (MT5 2026-10-20: -6.14 % a year of the price,
            # charged per night held), applied to every window at today's rate
            sw = {}
            for w in ("a", "b"):
                adj = np.array([x["ret_pct"] - US500_SWAP_LONG_PCT_YEAR / 365 * max(1, round((x["exit_t"] - x["t"]) / 86_400_000))
                                for x in per[w]])
                if len(adj):
                    los = -adj[adj <= 0].sum()
                    sw[w] = {"mean_ret_pct": float(adj.mean()), "profit_factor": float(adj[adj > 0].sum() / los) if los > 0 else None}
            row["swap_sensitivity"] = {**sw, "text": "With the broker's current long financing (6.14% a year, per night held): window A "
                                       + f"{sw['a']['mean_ret_pct']:+.3f}% per trade (PF {sw['a']['profit_factor']:.2f}), window B "
                                       + f"{sw['b']['mean_ret_pct']:+.3f}% (PF {sw['b']['profit_factor']:.2f}). Not part of the verdict."}
        if r.key.startswith(("rsi2", "r2_")) or r.key == "triple_ma":
            row["random_timing"] = random_timing(r)
        if r.assets == swing.DOW30 and row["verdict"] == "PASS":
            row["verdict"] = "PASS (not counted: needs a survivorship-free re-test)"
        if r.key == "london_breakfast":
            row["sensitivity_tp"] = {}
            for tp in (30, 50):
                p2, _, _ = run_rule(r, tp_pips=tp)
                row["sensitivity_tp"][str(tp)] = {"a": scalp.summarize(p2["a"]), "b": scalp.summarize(p2["b"])}
        rows.append(row)
        f2 = lambda s: f"{s.get('trades', 0):5d} tr WR {s.get('win_rate_pct', 0):5.1f}% mean {s.get('mean_ret_pct', 0):+.4f}% PF {s.get('profit_factor') or 0:4.2f}"  # noqa: E731
        print(f"{r.key:<17} | A {f2(sa)} | B {f2(sb)} | B gross {row['gross']['b'].get('mean_ret_pct', 0):+.4f}% | "
              f"{row['verdict']} {''.join('Y' if v else '.' for v in checks.values())} {('missing ' + ','.join(missing)) if missing else ''}", flush=True)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_SWING_STUDY.md",
           "costs": {"mt5": "half the mean spread + 0.002% slippage + 0.0025% commission (not on indices) per side; swaps not modelled",
                     "stocks": "0.03% per side"},
           "rules": rows}
    out["headline"] = headline(out["rules"])
    if only and OUT.exists():
        old = {x["key"]: x for x in json.loads(OUT.read_text()).get("rules", [])}
        old.update({x["key"]: x for x in rows})
        out["rules"] = [old[r.key] for r in swing.RULES if r.key in old]
        out["headline"] = headline(out["rules"])
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
