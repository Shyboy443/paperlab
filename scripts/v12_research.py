"""Renata V12 research: independent strategy-family comparison (docs/V12_RESEARCH.md).

    python scripts/v12_research.py run --window DEV1    # bt_hold2       2021-12-01 .. 2023-07-01
    python scripts/v12_research.py run --window DEV2    # bt_hold_crypto 2023-08-05 .. 2024-07-01
    python scripts/v12_research.py run --window VAL     # bt2y           2024-08-05 .. 2026-09-30
    python scripts/v12_research.py run --window FINAL   # bt_final (Binance public archive, untouched) 2020-01-01 .. 2021-10-01
    python scripts/v12_research.py select --out docs/V12_RESEARCH.json

PRE-REGISTERED (2026-10-10, before any run)
Stage 1 DEV (2021-12 .. 2024-06, two archives pooled): every config of app/lab12/families.configs() (86: 44 rules,
  42 of them also with the C regime overlay; the 2 benchmarks without). A config is a CANDIDATE only if on DEV:
  expectancy > 0 R, daily t >= 2.0, >= 200 trades, expectancy still > 0 with doubled costs, and (long-only momentum)
  its portfolio return beats the equal-weight long benchmark with the same hold.
Stage 2 VALIDATION (2024-08 .. 2026-09, bt2y; the halves split at 2025-07-01): m = number of candidates; a candidate
  PASSES if its validation daily t >= the one-sided Bonferroni z for 5% / m, expectancy > 0 in BOTH halves, profit
  factor >= 1.10, >= 150 trades, and > 0 with doubled costs.
Stage 3 FINAL (2020-01 .. 2021-09, Binance public archive -- data no study has touched): every validated config must
  have expectancy > 0, daily t >= 2.0 and > 0 with doubled costs. Only a config that clears all three stages may become
  Renata V12-RESEARCH (paper). Nothing is re-tuned between stages.
Experiment F (microstructure) is NOT run: there is no historical order-book or trade data. G2 (open interest) exists
only from 2023-07: it uses DEV2 alone for stage 1 and has no final stage.
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.lab12 import engine as E  # noqa: E402
from app.lab12 import families as F  # noqa: E402

COINS = tuple(sorted(json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))["rules"]))
OUT = Path("/data/v12")
WINDOWS = {
    "DEV1": ("/data/bt_hold2", "2021-12-01", "2023-07-01"),
    "DEV2": ("/data/bt_hold_crypto", "2023-08-05", "2024-07-01"),
    "VAL": ("/data/bt2y", "2024-08-05", "2026-09-30"),
    "FINAL": ("/data/bt_final", "2020-01-01", "2021-10-01"),
}
VAL_SPLIT = "2025-07-01"


def ms(d: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def run(args: argparse.Namespace) -> int:
    root, a, b = WINDOWS[args.window]
    t0 = time.time()
    w = E.load_window(args.window, root, ms(a), ms(b), COINS)
    p = F.panel(w)
    print(f"{args.window}: {len(w.coins)} coins, {p.h.shape[1]} hours, loaded in {time.time() - t0:.0f}s", flush=True)
    res: dict[str, Any] = {"window": args.window, "start": w.start, "end": w.end, "syms": p.syms, "configs": {}}
    for cfg in F.configs():
        t1 = time.time()
        ev = F.generate(w, p, cfg, root)
        out = {}
        for mult in (1.0, 2.0):
            tr = E.simulate(w, ev, p.syms, cost_mult=mult)
            tr["ms"] = w.a + tr["t"] * E.BAR if len(tr["t"]) else np.zeros(0)
            tr["regime"] = p.regime[np.clip(tr["t"] // 12, 0, len(p.regime) - 1)] if len(tr["t"]) else np.zeros(0)
            out[mult] = {k: tr[k] for k in ("ms", "coin", "side", "ret", "r", "cost", "funding", "stopped", "regime")}
        res["configs"][cfg["name"]] = {"cfg": cfg, "signals": int(len(ev.t)), "trades": out}
        print(f"  {cfg['name']:52s} signals {len(ev.t):6d} trades {len(out[1.0]['r']):6d} "
              f"E {np.mean(out[1.0]['r']) if len(out[1.0]['r']) else 0:+.3f}R ({time.time() - t1:.0f}s)", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{args.window}.pkl", "wb") as fh:
        pickle.dump(res, fh)
    print(f"{args.window} done in {time.time() - t0:.0f}s", flush=True)
    return 0


# -- pooled statistics ---------------------------------------------------------------------------------------------------
def pooled(parts: list[tuple[dict, dict]], max_pos: int = 6, seed: int = 7) -> dict[str, Any]:
    """Metrics over one or more windows: parts = [(window result, trades at a cost multiple)]."""
    r = np.concatenate([tr["r"] for _, tr in parts]) if parts else np.zeros(0)
    if len(r) == 0:
        return {"trades": 0}
    ret = np.concatenate([tr["ret"] for _, tr in parts])
    msx = np.concatenate([tr["ms"] for _, tr in parts])
    day = (msx // E.DAY).astype(np.int64)
    full = []
    for res, tr in parts:
        d0, d1 = res["start"] // E.DAY, res["end"] // E.DAY
        v = np.zeros(int(d1 - d0))
        dd = (tr["ms"] // E.DAY - d0).astype(int)
        ok = (dd >= 0) & (dd < len(v))
        np.add.at(v, dd[ok], tr["r"][ok])
        full.append(v)
    f = np.concatenate(full)
    sd = f.std(ddof=1)
    t_stat = float(f.mean() / (sd / math.sqrt(len(f)))) if sd > 0 else 0.0
    boots = np.random.default_rng(seed).choice(f, size=(1000, len(f)), replace=True).sum(axis=1) / len(r)
    eq = np.cumsum(r)
    port = np.cumsum(ret / max_pos)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    years: dict[int, Any] = {}
    yr = (msx // (365.2425 * E.DAY) + 1970).astype(int)
    for y in np.unique(yr):
        s = yr == y
        years[int(y)] = {"trades": int(s.sum()), "expectancy_r": round(float(r[s].mean()), 4),
                         "portfolio_pct": round(float(100 * ret[s].sum() / max_pos), 2)}
    coins: dict[str, Any] = {}
    for res, tr in parts:
        for ci in np.unique(tr["coin"]):
            s = tr["coin"] == ci
            name = res["syms"][int(ci)]
            c = coins.setdefault(name, [0, 0.0])
            c[0] += int(s.sum())
            c[1] += float(tr["r"][s].sum())
    reg = np.concatenate([tr["regime"] for _, tr in parts])
    regimes = {nm: {"trades": int((reg == k).sum()), "expectancy_r": round(float(r[reg == k].mean()), 4)}
               for k, nm in ((1, "BTC up"), (-1, "BTC down"), (0, "BTC range")) if (reg == k).sum()}
    return {"trades": int(len(r)), "per_day": round(len(r) / max(1, len(f)), 2), "win_rate": round(float((r > 0).mean()), 3),
            "pf": round(float(wins / losses), 3) if losses else None, "expectancy_r": round(float(r.mean()), 4),
            "ci95_r": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)],
            "t_daily": round(t_stat, 2), "net_r": round(float(r.sum()), 1),
            "max_dd_r": round(float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)), 1),
            "portfolio_pct": round(float(100 * port[-1]), 2),
            "portfolio_max_dd_pct": round(float(100 * np.max(np.maximum.accumulate(np.concatenate([[0], port]))[1:] - port)), 2),
            "avg_cost_bp": round(float(1e4 * np.concatenate([tr["cost"] for _, tr in parts]).mean()), 1),
            "funding_pct": round(float(100 * np.concatenate([tr["funding"] for _, tr in parts]).sum() / max_pos), 3),
            "stopped_share": round(float(np.concatenate([tr["stopped"] for _, tr in parts]).mean()), 3),
            "years": years, "regimes": regimes,
            "coins": {k: {"trades": v[0], "expectancy_r": round(v[1] / v[0], 4)} for k, v in sorted(coins.items())}}


def split(res: dict, tr: dict, cut: int) -> tuple[tuple[dict, dict], tuple[dict, dict]]:
    a = tr["ms"] < cut
    pick = lambda m: {k: v[m] for k, v in tr.items()}  # noqa: E731
    return ({**res, "end": cut}, pick(a)), ({**res, "start": cut}, pick(~a))


def select(args: argparse.Namespace) -> int:
    t0 = time.time()
    load = lambda n: pickle.load(open(OUT / f"{n}.pkl", "rb")) if (OUT / f"{n}.pkl").exists() else None  # noqa: E731
    dev1, dev2, val, fin = load("DEV1"), load("DEV2"), load("VAL"), load("FINAL")
    names = list(dev2["configs"])
    report: dict[str, Any] = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                              "configs_tested": len(names), "dev": {}, "candidates": [], "validation": {}, "final": {}}
    bench = {}
    for n in names:
        parts = [(w, w["configs"][n]["trades"][1.0]) for w in (dev1, dev2) if w and n in w["configs"]]
        if dev2["configs"][n]["cfg"]["family"] == "G2":
            parts = [(dev2, dev2["configs"][n]["trades"][1.0])]
        parts2 = [(w, w["configs"][n]["trades"][2.0]) for w in (dev1, dev2) if w and n in w["configs"]]
        if dev2["configs"][n]["cfg"]["family"] == "G2":
            parts2 = [(dev2, dev2["configs"][n]["trades"][2.0])]
        m1, m2 = pooled(parts), pooled(parts2)
        report["dev"][n] = {"cfg": dev2["configs"][n]["cfg"], **m1, "expectancy_r_2x_costs": m2.get("expectancy_r")}
        if "benchmark" in n:
            bench[dev2["configs"][n]["cfg"]["hold_h"]] = m1.get("portfolio_pct", 0.0)
    for n, m in report["dev"].items():
        cfg = m["cfg"]
        ok = (m.get("trades", 0) >= 200 and (m.get("expectancy_r") or -1) > 0 and m.get("t_daily", 0) >= 2.0
              and (m.get("expectancy_r_2x_costs") or -1) > 0 and "benchmark" not in n)
        if ok and cfg.get("mode") == "long_only":
            ok = m.get("portfolio_pct", 0) > bench.get(cfg["hold_h"], 0)
        if ok:
            report["candidates"].append(n)
    mcount = max(1, len(report["candidates"]))
    from statistics import NormalDist
    z_need = NormalDist().inv_cdf(1 - 0.05 / mcount)
    report["bonferroni_z"] = round(z_need, 2)
    cut = ms(VAL_SPLIT)
    passed = []
    for n in report["candidates"]:
        if not val or n not in val["configs"]:
            continue
        tr1, tr2 = val["configs"][n]["trades"][1.0], val["configs"][n]["trades"][2.0]
        whole, whole2 = pooled([(val, tr1)]), pooled([(val, tr2)])
        h1, h2 = split(val, tr1, cut)
        a, b = pooled([h1]), pooled([h2])
        checks = {"t_bonferroni": whole.get("t_daily", 0) >= z_need, "half1_pos": (a.get("expectancy_r") or -1) > 0,
                  "half2_pos": (b.get("expectancy_r") or -1) > 0, "pf_1_10": (whole.get("pf") or 0) >= 1.10,
                  "trades_150": whole.get("trades", 0) >= 150, "double_cost_pos": (whole2.get("expectancy_r") or -1) > 0}
        report["validation"][n] = {**whole, "half1": a, "half2": b, "expectancy_r_2x_costs": whole2.get("expectancy_r"),
                                   "checks": checks, "pass": all(checks.values())}
        if all(checks.values()):
            passed.append(n)
    for n in passed:
        if not fin or n not in fin["configs"] or report["dev"][n]["cfg"]["family"] == "G2":
            report["final"][n] = {"status": "no final data for this family/config"}
            continue
        f1, f2 = pooled([(fin, fin["configs"][n]["trades"][1.0])]), pooled([(fin, fin["configs"][n]["trades"][2.0])])
        checks = {"pos": (f1.get("expectancy_r") or -1) > 0, "t_2": f1.get("t_daily", 0) >= 2.0,
                  "double_cost_pos": (f2.get("expectancy_r") or -1) > 0}
        report["final"][n] = {**f1, "expectancy_r_2x_costs": f2.get("expectancy_r"), "checks": checks, "pass": all(checks.values())}
    winners = [n for n, x in report["final"].items() if x.get("pass")]
    report["verdict"] = ("V12 CANDIDATES: " + "; ".join(winners)) if winners else \
        "NO STRATEGY CLEARS ALL THREE STAGES: no Renata V12 is built"
    report["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"configs {len(names)}  candidates {len(report['candidates'])}  bonferroni z {z_need:.2f}  validated {len(passed)}", flush=True)
    for n in report["candidates"]:
        v = report["validation"].get(n, {})
        print(f"  {n:55s} DEV {report['dev'][n]['expectancy_r']:+.3f}R t={report['dev'][n]['t_daily']}  "
              f"VAL {v.get('expectancy_r', 0):+.3f}R t={v.get('t_daily')} pass={v.get('pass')}", flush=True)
    print(report["verdict"], flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--window", required=True, choices=list(WINDOWS))
    s = sub.add_parser("select")
    s.add_argument("--out", default=str(PROJECT / "docs" / "V12_RESEARCH.json"))
    args = ap.parse_args()
    return {"run": run, "select": select}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
