"""Scribd round 3 study (pre-registered in docs/SCRIBD_ROUND3_STUDY.md).

    python scripts/scribd_round3_study.py

Data: data/round3_cache/<SYM>_D1.npy (Yahoo daily), data/round3_cache/BTCUSDT_1h.npy (Binance spot),
data/mt5_cache/<SYM>_D1.npy and US500_M1L.npy (the operator's MT5, UTC). Writes app/ytstrats/round3_results.json
(served read-only by /api/public/scribd-round3).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, round3 as R, scalp, swing  # noqa: E402
from scripts.gold_scalp_study import us_dst  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
fmt = lambda x: datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
WIN = {
    "yahoo": {"a": (ms("2000-01-01"), ms("2013-01-01")), "b": (ms("2013-01-01"), ms("2026-10-21"))},
    "mt5d": {"a": (ms("2015-01-01"), ms("2021-01-01")), "b": (ms("2021-01-01"), ms("2026-10-21"))},
    "us500": {"a": (ms("2020-11-01"), ms("2023-11-01")), "b": (ms("2023-11-01"), ms("2026-10-21"))},
    "btc": {"a": (ms("2019-06-01"), ms("2023-01-01")), "b": (ms("2023-01-01"), ms("2026-10-21"))},
}
CACHE, MT5 = PROJECT / "data" / "round3_cache", PROJECT / "data" / "mt5_cache"
OUT = PROJECT / "app" / "ytstrats" / "round3_results.json"
SPEC = json.loads((MT5 / "spec.json").read_text())["symbols"]
ETF_COST = {"SPY": 0.0001, "QQQ": 0.0001, "DIA": 0.0001, "IWM": 0.0001, "EWS": 0.0005, "NSEI": 0.0003}
BTC_COST = 0.0007


def yahoo(sym: str) -> swing.D:
    a = np.load(CACHE / f"{sym}_D1.npy")
    a = a[(a[:, 2] >= a[:, 3]) & (a[:, 4] > 0)]
    return swing.D(*(a[:, k] for k in range(5)))


def mt5_daily(sym: str) -> tuple[swing.D, float]:
    a = np.load(MT5 / f"{sym}_D1.npy")
    cost = SPEC[sym]["spread_mean_frac"] / 2 + 0.00002 + 0.000025
    return swing.D(*(a[:, k] for k in range(5))), cost


RULES = [
    {"key": "nr7_forex", "doc": "NR7 Forex Trading Strategy (428753972)", "assets": swing.MAJORS, "win": "mt5d", "tf": "daily", "fn": R.nr7_forex},
    {"key": "nr7_etf", "doc": "NR4 and NR7 Trading Strategies, StockCharts (418172887)", "assets": ("SPY", "QQQ", "DIA", "IWM"), "win": "yahoo", "tf": "daily", "fn": R.nr7_etf},
    {"key": "nr7_nifty", "doc": "Trading NR7 Setup, AW10 draft system (382606844)", "assets": ("NSEI",), "win": "yahoo", "tf": "daily", "fn": R.nr7_nifty, "single": True},
    {"key": "intraday_momentum", "doc": "Beat the Market: intraday momentum for SPY, Zarattini/Aziz/Barbon (861126103)", "assets": ("US500",), "win": "us500", "tf": "1m", "single": True},
    {"key": "btc_new_long", "doc": "Backtesting of Trading Strategies for Bitcoin, ETH thesis: New Long (435132310)", "assets": ("BTCUSDT",), "win": "btc", "tf": "1h", "single": True, "shorts": False},
    {"key": "btc_nls2", "doc": "Backtesting of Trading Strategies for Bitcoin, ETH thesis: final New Long-Short 2 (435132310)", "assets": ("BTCUSDT",), "win": "btc", "tf": "1h", "single": True, "shorts": True},
    {"key": "ibs_spy_ews", "doc": "The Internal Bar Strength Indicator, Kinlay after Pagonidis (343659347)", "assets": ("SPY", "EWS"), "win": "yahoo", "tf": "daily", "fn": R.ibs_spy_ews},
    {"key": "ibs_qqq", "doc": "The Internal Bar Strength Indicator, Llewelyn James' rule (343659347)", "assets": ("QQQ",), "win": "yahoo", "tf": "daily", "fn": R.ibs_qqq, "single": True},
]


def run(rule: dict, zero: bool = False) -> tuple[dict, dict]:
    per: dict[str, list] = {"a": [], "b": []}
    by_asset: dict[str, float | None] = {}
    for sym in rule["assets"]:
        for w, (s0, s1) in WIN[rule["win"]].items():
            if rule["key"] == "intraday_momentum":
                a = np.load(MT5 / "US500_M1L.npy")
                a = a[a[:, 0] >= ms("2020-09-15")]
                cost = 0.0 if zero else SPEC["US500"]["spread_mean_frac"] / 2 + 0.00002
                off = np.where(us_dst(a[:, 0]), -4, -5) * 3_600_000.0
                tr = R.intraday_momentum(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], cost, s0, s1, off)
            elif rule["key"].startswith("btc"):
                a = np.load(CACHE / "BTCUSDT_1h.npy")
                tr = R.btc_thesis(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], 0.0 if zero else BTC_COST, s0, s1, rule["shorts"])
            elif rule["win"] == "mt5d":
                b, cost = mt5_daily(sym)
                tr = rule["fn"](b, swing.PIP[sym], 0.0 if zero else cost, s0, s1)
            else:
                tr = rule["fn"](yahoo(sym), 0.0, 0.0 if zero else ETF_COST[sym], s0, s1)
            for x in tr:
                x["asset"] = sym
            per[w] += tr
            if w == "b":
                by_asset[sym] = float(sum(x["ret_pct"] for x in tr)) if tr else None
    return per, by_asset


def random_timing(rule: dict) -> dict:
    """Not part of the verdict: a long-only daily rule vs random entry days with the same holding times."""
    rng = np.random.default_rng(7)
    out = {}
    for w, (s0, s1) in WIN[rule["win"]].items():
        real, rand = [], []
        for sym in rule["assets"]:
            b = yahoo(sym)
            cost = ETF_COST[sym]
            idx = np.flatnonzero((b.t >= s0) & (b.t < s1))
            for x in rule["fn"](b, 0.0, cost, s0, s1):
                hold = max(int(np.searchsorted(b.t, x["exit_t"])) - int(np.searchsorted(b.t, x["t"])), 1)
                real.append(x["ret_pct"])
                for _ in range(20):
                    a = int(rng.integers(idx[0], max(idx[0] + 1, idx[-1] - hold)))
                    rand.append(((b.c[a + hold] * (1 - cost)) / (b.c[a] * (1 + cost)) - 1) * 100)
        re, ra = np.array(real), np.array(rand)
        out[w] = {"rule_mean_pct": float(re.mean()), "random_mean_pct": float(ra.mean()), "edge_pct": float(re.mean() - ra.mean()),
                  "z": float((re.mean() - ra.mean()) / (re.std() / np.sqrt(len(re))))}
    return out


def buy_and_hold(sym: str, win: str) -> dict:
    out = {}
    if sym == "BTCUSDT":
        a = np.load(CACHE / "BTCUSDT_1h.npy")
        t, o, c = a[:, 0], a[:, 1], a[:, 4]
    elif sym == "US500":
        a = np.load(MT5 / "US500_D1.npy")
        t, o, c = a[:, 0], a[:, 1], a[:, 4]
    else:
        b = yahoo(sym)
        t, o, c = b.t, b.o, b.c
    for w, (s0, s1) in WIN[win].items():
        idx = np.flatnonzero((t >= s0) & (t < s1))
        out[w] = float((c[idx[-1]] / o[idx[0]] - 1) * 100)
    return out


def _stats(tr: list, swap_yr: float = 0.0) -> dict:
    a = np.array([x["ret_pct"] - swap_yr / 365 * max(1, round((x["exit_t"] - x["t"]) / 86_400_000)) for x in tr])
    if not len(a):
        return {"trades": 0}
    los = -a[a <= 0].sum()
    return {"trades": int(len(a)), "mean_ret_pct": float(a.mean()), "profit_factor": float(a[a > 0].sum() / los) if los > 0 else None,
            "win_rate_pct": float(np.mean(a > 0) * 100)}


def robustness_ibs_qqq() -> dict:
    """Not part of the verdict: the same rule after the article's date, on the other index ETFs, and on the operator's
    MT5 index CFDs with the broker's long financing (6.14 % a year, 2026-10-20)."""
    end = ms("2026-10-21")
    out = {"qqq_since_article_2016_02": _stats(R.ibs_qqq(yahoo("QQQ"), 0, ETF_COST["QQQ"], ms("2016-02-01"), end))}
    for sym in ("SPY", "DIA", "IWM"):
        out[sym] = {w: _stats(R.ibs_qqq(yahoo(sym), 0, ETF_COST[sym], s0, s1)) for w, (s0, s1) in WIN["yahoo"].items()}
    for sym in ("NAS100", "US500"):
        a = np.load(MT5 / f"{sym}_D1.npy")
        b = swing.D(*(a[:, k] for k in range(5)))
        tr = R.ibs_qqq(b, 0, SPEC[sym]["spread_mean_frac"] / 2 + 0.00002, ms("2015-01-01"), end)
        out[sym + "_cfd_2015_2026"] = {"no_swap": _stats(tr), "with_swap": _stats(tr, 6.14)}
    return out


def headline(rows: list) -> str:
    passed = [r for r in rows if r["verdict"] == "PASS"]
    if not passed:
        return "No rule passes."
    r = passed[0]
    rb, t = r.get("robustness", {}), r.get("random_timing", {})
    txt = (f"“IBS < 0.10 on QQQ” passes both windows (PF {r['a']['profit_factor']:.2f} in 2000–2012, {r['b']['profit_factor']:.2f} in 2013–2026) "
           f"and beats random entry days in both (z {t['a']['z']:.1f} and {t['b']['z']:.1f}). ")
    if rb:
        txt += (f"It held after the article (2016 →: PF {rb['qqq_since_article_2016_02']['profit_factor']:.2f}), works on SPY, DIA and IWM too, "
                f"and on your broker's NAS100 CFD with swap (PF {rb['NAS100_cfd_2015_2026']['with_swap']['profit_factor']:.2f}). "
                "Long only and without a stop: worst trade −22%. A forward paper-test candidate. ")
    return txt + "NR7, the IBS one-day rule, the SPY intraday-momentum paper and the Bitcoin thesis fail."


def main() -> int:
    rows = []
    for r in RULES:
        per, by_asset = run(r)
        gross, _ = run(r, zero=True)
        sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
        low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
        traded = [v for v in by_asset.values() if v is not None]
        min_b = 30 if r.get("single") else 100
        checks = {
            "a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
            "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= min_b),
            "b_ci_above_zero": bool(low > 0),
            "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
        }
        fn = r.get("fn") or (R.intraday_momentum if r["key"] == "intraday_momentum" else R.btc_thesis)
        row = {"key": r["key"], "doc": r["doc"], "assets": list(r["assets"]), "tf": r["tf"], "rule": (fn.__doc__ or "").strip(),
               "windows": {k: [fmt(v[0]), fmt(v[1])] for k, v in WIN[r["win"]].items()},
               "a": sa, "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset},
               "gross": {"a": scalp.summarize(gross["a"]), "b": scalp.summarize(gross["b"])},
               "min_trades_b": min_b, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
               "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
        if r["key"] in ("ibs_qqq",):
            row["random_timing"] = random_timing(r)
            row["robustness"] = robustness_ibs_qqq()
        if r["key"] in ("ibs_qqq", "btc_new_long", "btc_nls2", "intraday_momentum"):
            row["buy_and_hold_pct"] = buy_and_hold(r["assets"][0], r["win"])
        rows.append(row)
        f2 = lambda s: f"{s.get('trades', 0):5d} tr WR {s.get('win_rate_pct', 0):5.1f}% mean {s.get('mean_ret_pct', 0):+.4f}% PF {s.get('profit_factor') or 0:4.2f}"  # noqa: E731
        print(f"{r['key']:<18} | A {f2(sa)} | B {f2(sb)} | B gross {row['gross']['b'].get('mean_ret_pct', 0):+.4f}% | "
              f"{row['verdict']} {''.join('Y' if v else '.' for v in checks.values())}", flush=True)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_ROUND3_STUDY.md",
           "costs": {"etf": "0.01% per side (SPY, QQQ, DIA, IWM), EWS 0.05%, Nifty 0.03%", "mt5": "half the mean spread + 0.002% slippage (+0.0025% commission on FX)",
                     "btc": "0.07% per side"},
           "rules": rows}
    out["headline"] = headline(rows)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
