"""Reconcile the app's T3-CCI and CHI desks (app/tvdesks) with the research package, field by field.

    python scripts/tvdesks_verify.py <path to the unpacked TradingView_Research folder> [--out docs/tvdesks]

Uses the RECORDED snapshot in the package (never a fresh download). Steps:
 1. every SHA-256 in package-manifest.json;
 2. 4h candles: app aggregation vs the package's pandas aggregation, every candle and field;
 3. raw requests: app indicators vs the saved PineTS request indices (signals/<asset>-4h.json.gz), every index;
 4. fills: app broker vs the package's own screen.signal_path + screen.replay AND vs the shipped CSVs -- every trade
    field, every equity / high / low value by UTC timestamp, monthly rows, summary rows; base and doubled costs;
 5. the operator's checkpoints.
Stops nothing on the first mismatch: it records the first one per comparison (UTC time, expected, actual) and carries on.
Writes <out>/audit.json (summarised in docs/TVDESKS_AUDIT.md). Exit code 1 if anything mismatched.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.tvdesks import broker as B  # noqa: E402
from app.tvdesks.data import aggregate_4h, contiguous  # noqa: E402
from app.tvdesks.indicators import chi_requests, t3cci_requests  # noqa: E402

START = 1728604800000            # 2024-10-11 00:00 UTC
END = 1791676800000              # 2026-10-11 00:00 UTC (exclusive)
SPLIT = 1775865600000            # 2026-04-11 00:00 UTC
REL = 1e-12
H4 = 14_400_000
CHECKPOINTS = {
    "T3-CCI ZEC": {"trades": 174, "ending_capital": 242415.359096, "net_return_pct": 2324.153591, "max_drawdown_pct": 48.492574,
                   "ohlc_drawdown_bound_pct": 51.465752, "doubled_cost_return_pct": 1496.608370},
    "T3-CCI CAKE": {"net_return_pct": 409.133158, "max_drawdown_pct": 33.506498, "ohlc_drawdown_bound_pct": 39.506146},
    "CHI 84": {"trades": 1860, "ending_capital": 986992.007519, "net_return_pct": 17.499049, "max_drawdown_pct": 29.100771,
               "ohlc_drawdown_bound_pct": 30.210689, "doubled_cost_return_pct": 11.385354, "without_zec_return_pct": 13.530781,
               "positive_coins": 39},
}


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).timestamp() * 1000)


def close(a: float, b: float, rel: float = REL, abs_: float = 1e-9) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_)


class Report:
    def __init__(self):
        self.checks, self.first = [], {}

    def add(self, name: str, ok: bool, compared: int, first: dict | None = None, **extra):
        self.checks.append({"check": name, "passed": bool(ok), "compared": compared, "first_mismatch": first, **extra})
        if not ok and first:
            self.first.setdefault(name, first)

    @property
    def ok(self) -> bool:
        return all(c["passed"] for c in self.checks)


def load_hours(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as f:
        rows = list(csv.DictReader(f))
    return [{"t": to_ms(r["time"]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
             "v": float(r["volume"])} for r in rows]


def package_screen(pkg: Path):
    sys.path.insert(0, str(pkg))
    spec = importlib.util.spec_from_file_location("pkg_screen", pkg / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compare_trades(rep: Report, name: str, mine: list[dict], ref: list[dict]):
    fields = ["entry_price", "exit_price", "units", "entry_cash", "exit_cash", "entry_fee", "exit_fee", "pnl"]
    first = None
    if len(mine) != len(ref):
        first = {"what": "trade count", "expected": len(ref), "actual": len(mine)}
    for k, (a, e) in enumerate(zip(mine, ref)):
        if first:
            break
        if a["entry_time"] != to_ms(e["entry_time"]) or a["exit_time"] != to_ms(e["exit_time"]):
            first = {"trade": k + 1, "utc": iso(to_ms(e["entry_time"])), "field": "times",
                     "expected": [e["entry_time"], e["exit_time"]], "actual": [iso(a["entry_time"]), iso(a["exit_time"])]}
            break
        if str(a["forced_exit"]) != str(e["forced_exit"]):
            first = {"trade": k + 1, "utc": iso(a["entry_time"]), "field": "forced_exit", "expected": e["forced_exit"], "actual": a["forced_exit"]}
            break
        for f in fields:
            if not close(a[f], float(e[f])):
                first = {"trade": k + 1, "utc": iso(a["entry_time"]), "field": f, "expected": float(e[f]), "actual": a[f]}
                break
    rep.add(name, first is None, len(ref), first)


def compare_equity(rep: Report, name: str, times: list[int], cols: dict[str, list[float]], ref_rows: list[dict]):
    first = None
    if len(times) != len(ref_rows):
        first = {"what": "row count", "expected": len(ref_rows), "actual": len(times)}
    for i, r in enumerate(ref_rows):
        if first:
            break
        if to_ms(r["time"]) != times[i]:
            first = {"row": i, "utc": r["time"], "field": "time", "actual": iso(times[i])}
            break
        for f in ("equity", "high_equity", "low_equity"):
            if not close(cols[f][i], float(r[f]), rel=1e-11):
                first = {"row": i, "utc": iso(times[i]), "field": f, "expected": float(r[f]), "actual": cols[f][i]}
                break
    rep.add(name, first is None, len(ref_rows) * 3, first)


def monthly(times: list[int], equity: list[float], trades: list[dict], opening: float) -> list[dict]:
    out, prior, by = [], opening, {}
    for t, e in zip(times, equity):
        by.setdefault(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m"), []).append(e)
    for m, vals in by.items():
        closed = sum(1 for tr in trades if dt.datetime.fromtimestamp(tr["exit_time"] / 1000, dt.timezone.utc).strftime("%Y-%m") == m)
        out.append({"month": m, "opening_equity": prior, "ending_equity": vals[-1], "net_return_pct": (vals[-1] / prior - 1) * 100,
                    "closed_trades": closed, "partial_month": m in ("2024-10", "2026-10")})
        prior = vals[-1]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--out", default=str(PROJECT / "docs" / "tvdesks"))
    args = ap.parse_args()
    pkg, out = Path(args.package), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()

    # 1. hashes
    man = json.loads((pkg / "package-manifest.json").read_text())
    bad = [r for r, h in man["files"].items() if hashlib.sha256((pkg / r).read_bytes()).hexdigest() != h]
    rep.add("package SHA-256 manifest", not bad, len(man["files"]), {"files": bad[:5]} if bad else None)

    cov = json.loads((pkg / "data-coverage.json").read_text())
    full = [r for r in cov if r["status"] == "full_period"]
    scr = package_screen(pkg)
    import pandas as pd

    def pkg_frame(row):
        return scr.aggregate(scr.read_data(row), "4h")

    results = {"T3-CCI": {}, "CHI": {}}
    chi_books = {}
    for row in full:
        asset = row["requested"]
        hours = load_hours(pkg / "data" / Path(row["path"]).name)
        bars = aggregate_4h(hours)
        df = pkg_frame(row)
        # 2. candles
        first = None
        if len(bars) != len(df):
            first = {"what": "candle count", "expected": len(df), "actual": len(bars)}
        else:
            for i, (b, (ts, r)) in enumerate(zip(bars, df.iterrows())):
                if b["t"] != int(ts.value // 10**6) or any(b[k] != float(r[f]) for k, f in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))):
                    first = {"utc": iso(int(ts.value // 10**6)), "expected": [float(r.open), float(r.high), float(r.low), float(r.close)],
                             "actual": [b["o"], b["h"], b["l"], b["c"]]}
                    break
        rep.add(f"4h candles {asset}", first is None and contiguous(bars), len(df), first)
        saved = json.loads(gzip.decompress((pkg / "signals" / f"{asset}-4h.json.gz").read_bytes()))["strategies"]
        desks = [("CHI", "8F6WNLkO")] + ([("T3-CCI", "tfkYcvBX")] if asset in ("ZEC", "CAKE") else [])
        for desk, sid in desks:
            # 3. raw requests (CHI exit ungated as in the saved instrumentation)
            if desk == "T3-CCI":
                buy, sell, _ = t3cci_requests(bars, START, END)
            else:
                buy, sell, _ = chi_requests(bars, START, END, islast=True, gate_chi_exit=False)
            mine = {"buy_indices": [i for i, x in enumerate(buy) if x], "sell_indices": [i for i, x in enumerate(sell) if x]}
            first = None
            for k in ("buy_indices", "sell_indices"):
                a, e = mine[k], saved[sid][k]
                if a != e:
                    j = next((n for n, (x, y) in enumerate(zip(a, e)) if x != y), min(len(a), len(e)))
                    ea = e[j] if j < len(e) else None
                    aa = a[j] if j < len(a) else None
                    first = {"requests": k, "position": j, "expected_index": ea, "actual_index": aa,
                             "utc": iso(bars[min(x for x in (ea, aa) if x is not None)]["t"])}
                    break
            rep.add(f"{desk} requests {asset}", first is None, len(saved[sid]["buy_indices"]) + len(saved[sid]["sell_indices"]), first)
            # 4. fills: app broker vs the package broker on the saved requests
            path = scr.signal_path(df, saved[sid], scr.START, scr.END)
            for cost in (1, 2):
                r = B.replay(bars, buy, sell, START, END, cost_multiplier=cost)
                curve, ledger, _ = scr.replay(df, path, scr.START, scr.END, cost)
                ref_trades = [{k: (str(v) if k in ("entry_time", "exit_time") else v) for k, v in t.items()} for t in ledger]
                compare_trades(rep, f"{desk} trades {asset} cost x{cost} vs package broker", r["trades"], ref_trades)
                ref_rows = [{"time": str(ts), "equity": e, "high_equity": h, "low_equity": l}
                            for ts, e, h, l in zip(curve.index, curve.equity, curve.high_equity, curve.low_equity)]
                compare_equity(rep, f"{desk} equity {asset} cost x{cost} vs package broker", r["times"],
                               {"equity": r["equity"], "high_equity": r["high_equity"], "low_equity": r["low_equity"]}, ref_rows)
                results[desk].setdefault(asset, {})[cost] = r
            if desk == "CHI":
                chi_books[asset] = results[desk][asset]

    # 4b. shipped files: T3-CCI ZEC (winner) and CHI portfolio
    def rows(name):
        with open(pkg / name, newline="") as f:
            return list(csv.DictReader(f))

    for cost in (1, 2):
        z = results["T3-CCI"]["ZEC"][cost]
        compare_trades(rep, f"T3-CCI ZEC trades cost x{cost} vs winner-trades-cost-{cost}.csv", z["trades"], rows(f"winner-trades-cost-{cost}.csv"))
        compare_equity(rep, f"T3-CCI ZEC equity cost x{cost} vs winner-equity-cost-{cost}.csv", z["times"],
                       {"equity": z["equity"], "high_equity": z["high_equity"], "low_equity": z["low_equity"]}, rows(f"winner-equity-cost-{cost}.csv"))
        # CHI portfolio: trades in the package's order (coverage order, then entry), equity summed per timestamp
        led = [dict(t, asset=a) for a in (r["requested"] for r in full) for t in chi_books[a][cost]["trades"]]
        ref = rows(f"portfolio-leader-trades-cost-{cost}.csv")
        ok_assets = [r["asset"] for r in ref] == [t["asset"] for t in led]
        compare_trades(rep, f"CHI 84-book trades cost x{cost} vs portfolio-leader-trades-cost-{cost}.csv", led, ref)
        if not ok_assets:
            rep.add(f"CHI trade asset order cost x{cost}", False, len(ref), {"what": "asset sequence differs"})
        times = chi_books["BTC"][cost]["times"]
        tot = {f: [0.0] * len(times) for f in ("equity", "high_equity", "low_equity")}
        for a in (r["requested"] for r in full):
            for f in tot:
                tot[f] = [x + y for x, y in zip(tot[f], chi_books[a][cost][f])]
        compare_equity(rep, f"CHI portfolio equity cost x{cost} vs portfolio-leader-equity-cost-{cost}.csv", times, tot,
                       rows(f"portfolio-leader-equity-cost-{cost}.csv"))
        results.setdefault("CHI_portfolio", {})[cost] = {"times": times, **tot, "trades": led}

    # monthly rows
    for label, mine_rows, ref_name, cost in (
            ("T3-CCI ZEC monthly", monthly(results["T3-CCI"]["ZEC"][1]["times"], results["T3-CCI"]["ZEC"][1]["equity"],
                                           results["T3-CCI"]["ZEC"][1]["trades"], 10000), "winner-monthly-results.csv", 1),
            ("CHI portfolio monthly", monthly(results["CHI_portfolio"][1]["times"], results["CHI_portfolio"][1]["equity"],
                                              results["CHI_portfolio"][1]["trades"], 840000), "portfolio-leader-monthly-results.csv", 1)):
        ref = [r for r in rows(ref_name) if r["cost_multiplier"] == str(cost)]
        first = None
        if len(ref) != len(mine_rows):
            first = {"what": "month count", "expected": len(ref), "actual": len(mine_rows)}
        for a, e in zip(mine_rows, ref):
            if first:
                break
            for f in ("opening_equity", "ending_equity", "net_return_pct"):
                if not close(a[f], float(e[f]), rel=1e-10):
                    first = {"month": e["month"], "field": f, "expected": float(e[f]), "actual": a[f]}
                    break
            if not first and "closed_trades" in e and int(e["closed_trades"]) != a["closed_trades"]:
                first = {"month": e["month"], "field": "closed_trades", "expected": int(e["closed_trades"]), "actual": a["closed_trades"]}
        rep.add(label + f" cost x{cost} vs {ref_name}", first is None, len(ref), first)

    # summary rows: individual-results (T3 ZEC/CAKE, CHI x84) and cohort-results (CHI)
    ind = [r for r in rows("individual-results.csv") if r["timeframe"] == "4h" and r["period"] == "full"]
    def ind_row(sid, asset, cost):
        return next(r for r in ind if r["strategy_id"] == sid and r["asset"] == asset and r["cost_multiplier"] == str(cost))
    first, n = None, 0
    for desk, sid, assets in (("T3-CCI", "tfkYcvBX", ["ZEC", "CAKE"]), ("CHI", "8F6WNLkO", [r["requested"] for r in full])):
        for a in assets:
            for cost in (1, 2):
                m, e = results[desk][a][cost]["metrics"], ind_row(sid, a, cost)
                n += 1
                for f_m, f_e in (("net_return_pct", "net_return_pct"), ("max_drawdown_pct", "max_drawdown_pct"),
                                 ("ohlc_drawdown_bound_pct", "intrabar_drawdown_upper_bound_pct"), ("ending_capital", "ending_capital"),
                                 ("trades", "trades"), ("fees", "fees")):
                    if not close(m[f_m], float(e[f_e]), rel=1e-10):
                        first = first or {"row": f"{desk} {a} cost x{cost}", "field": f_e, "expected": float(e[f_e]), "actual": m[f_m]}
    rep.add("summary rows vs individual-results.csv (T3-CCI ZEC/CAKE, CHI x84, both costs)", first is None, n, first)

    port = {}
    for cost in (1, 2):
        p = results["CHI_portfolio"][cost]
        m = B.metrics(p["equity"], p["high_equity"], p["low_equity"], 840000.0, p["trades"])
        e = next(r for r in rows("cohort-results.csv") if r["strategy_id"] == "8F6WNLkO" and r["timeframe"] == "4h"
                 and r["cost_multiplier"] == str(cost) and r["period"] == "full")
        first = None
        for f_m, f_e in (("net_return_pct", "net_return_pct"), ("max_drawdown_pct", "max_drawdown_pct"),
                         ("ohlc_drawdown_bound_pct", "intrabar_drawdown_upper_bound_pct"), ("ending_capital", "ending_capital"),
                         ("trades", "trades")):
            if not close(m[f_m], float(e[f_e]), rel=1e-10):
                first = first or {"field": f_e, "expected": float(e[f_e]), "actual": m[f_m]}
        rep.add(f"CHI portfolio summary cost x{cost} vs cohort-results.csv", first is None, 5, first)
        port[cost] = m

    # 5. checkpoints
    z1, z2 = results["T3-CCI"]["ZEC"][1]["metrics"], results["T3-CCI"]["ZEC"][2]["metrics"]
    c1 = results["T3-CCI"]["CAKE"][1]["metrics"]
    endings = {a: results["CHI"][a][1]["metrics"]["ending_capital"] for a in results["CHI"]}
    no_zec = (sum(v for a, v in endings.items() if a != "ZEC") / (10000 * (len(endings) - 1)) - 1) * 100
    positive = sum(1 for v in endings.values() if v > 10000)
    split_i = next(i for i, t in enumerate(results["CHI_portfolio"][1]["times"]) if t >= SPLIT)
    pe = results["CHI_portfolio"][1]["equity"]
    actual = {
        "T3-CCI ZEC": {"trades": z1["trades"], "ending_capital": z1["ending_capital"], "net_return_pct": z1["net_return_pct"],
                       "max_drawdown_pct": z1["max_drawdown_pct"], "ohlc_drawdown_bound_pct": z1["ohlc_drawdown_bound_pct"],
                       "doubled_cost_return_pct": z2["net_return_pct"]},
        "T3-CCI CAKE": {"net_return_pct": c1["net_return_pct"], "max_drawdown_pct": c1["max_drawdown_pct"],
                        "ohlc_drawdown_bound_pct": c1["ohlc_drawdown_bound_pct"]},
        "CHI 84": {"trades": port[1]["trades"], "ending_capital": port[1]["ending_capital"], "net_return_pct": port[1]["net_return_pct"],
                   "max_drawdown_pct": port[1]["max_drawdown_pct"], "ohlc_drawdown_bound_pct": port[1]["ohlc_drawdown_bound_pct"],
                   "doubled_cost_return_pct": port[2]["net_return_pct"], "without_zec_return_pct": no_zec, "positive_coins": positive},
    }
    for group, want in CHECKPOINTS.items():
        for k, v in want.items():
            a = actual[group][k]
            ok = a == v if isinstance(v, int) else abs(a - v) < 5e-7 * max(1.0, abs(v))
            rep.add(f"checkpoint {group} {k}", ok, 1, None if ok else {"expected": v, "actual": a})
    extra = {"chi_first_18_months_pct": (pe[split_i - 1] / 840000 - 1) * 100, "chi_last_6_months_pct": (pe[-1] / pe[split_i - 1] - 1) * 100,
             "chi_losing_coins": sum(1 for v in endings.values() if v <= 10000), "chi_median_coin_pct": sorted((v / 10000 - 1) * 100 for v in endings.values())[len(endings) // 2 - 1:len(endings) // 2 + 1]}

    audit = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "package_manifest_hash": man.get("hash"),
             "passed": rep.ok, "checks": rep.checks, "first_mismatches": rep.first, "checkpoints": {"expected": CHECKPOINTS, "actual": actual},
             "extra": extra}
    (out / "audit.json").write_text(json.dumps(audit, indent=1, default=str))
    passed = sum(c["passed"] for c in rep.checks)
    print(f"{passed}/{len(rep.checks)} checks passed")
    for name, f in rep.first.items():
        print("FIRST MISMATCH", name, json.dumps(f, default=str))
    print(json.dumps(actual, indent=1))
    print(json.dumps(extra, indent=1))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
