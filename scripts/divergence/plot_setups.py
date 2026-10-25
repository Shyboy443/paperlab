"""Static pictures of Divergence Desk setups (DIV-SR-3) for checking by eye: candles of the divergence timeframe, EMA50,
both swings with price and RSI, the support/resistance swings the filter used, the lower-timeframe trigger level,
entry / stop / 2R target / exit with the result in R, and every rule passed or failed.

    python -m scripts.divergence.plot_setups --out data/divergence-research/shots

Accepted: the most recent CONFIRMED setups (the last 30 days have only one, so older ones from the two-year cache
fill the five). Rejected: the operator's SOL double bottom plus recent old-rule signals that DIV-SR-3 rejects.
"""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.divergence import model as M  # noqa: E402
from app.divergence.backtest import backtest  # noqa: E402
from app.divergence.market import UNIVERSE  # noqa: E402

CACHE = Path("data/divergence-research")
TWO_YEARS = "1728518400000-1791590400000"
RECENT = "1781308800000-1791676800000"          # 120 days to 2026-10-11, written by compare_sr2_sr3
DUR = {"1h": M.HOUR, "4h": 4 * M.HOUR}


def hhmm(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def load(symbol, span):
    with gzip.open(CACHE / f"{symbol}-{span}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def trade(rows, funding, s):
    out = backtest(rows, [s], funding, start_equity=1000, start_ms=s["confirmed_at"])
    if not out["trades"]:
        return None
    t = out["trades"][0]
    risk = t["quantity"] * abs(t["entry"] - t["stop"])
    return dict(t, r=t["net"] / risk if risk else None)


def draw(ax, ar, s, rows, funding, title):
    tf, dur = s["divergence_tf"], DUR[s["divergence_tf"]]
    bars = M.aggregate(rows, dur)
    rsi = M.rsi_series(bars, dur)
    ema = M.ema_series(bars, M.TREND_EMA, dur)
    idx = {b["t"]: i for i, b in enumerate(bars)}
    i1, i2 = idx[s["pivot1"]["time"]], idx[s["pivot2"]["time"]]
    tr = trade(rows, funding, s) if s["status"] == "CONFIRMED" else None
    hyp = s.get("hypothetical") or {}
    end_t = (tr["exit_t"] if tr else hyp.get("confirmed_at") or s["divergence_at"]) + 12 * dur
    lo = max(0, i1 - 45)
    hi = min(len(bars), max(i2 + 20, next((k for k, b in enumerate(bars) if b["t"] >= end_t), len(bars))))
    x = lambda t: (t - bars[lo]["t"]) / dur  # noqa: E731
    for k in range(lo, hi):
        b = bars[k]
        c = "#1d9e75" if b["c"] >= b["o"] else "#d64545"
        ax.plot([k - lo] * 2, [b["l"], b["h"]], color=c, lw=0.7)
        ax.plot([k - lo] * 2, [b["o"], b["c"]], color=c, lw=2.6)
    ax.plot(range(hi - lo), [ema[k] for k in range(lo, hi)], color="#e0a020", lw=1.2, label="EMA50")
    col = "#1d9e75" if s["side"] == "long" else "#d64545"
    ax.plot([i1 - lo, i2 - lo], [s["pivot1"]["price"], s["pivot2"]["price"]], color=col, lw=2.4, marker="o")
    for n, p in ((1, s["pivot1"]), (2, s["pivot2"])):
        ax.annotate(f"{n}: {p['price']:.6g}\nRSI {p['rsi']:.1f}", (x(p["time"]), p["price"]), fontsize=7.5, color=col,
                    xytext=(0, -26 if s["side"] == "long" else 10), textcoords="offset points", ha="center")
    for t in (s.get("zone") or {}).get("touches", []):
        ax.plot(x(t["time"]), t["price"], marker="s", color="#3a7bd5", ms=6)
        ax.annotate("S/R", (x(t["time"]), t["price"]), fontsize=7, color="#3a7bd5", xytext=(4, 4), textcoords="offset points")
    if s.get("trigger_level") is not None:
        a = x(s["divergence_at"])
        b_ = x((s.get("confirmed_at") or hyp.get("confirmed_at") or s["expires_at"]))
        ax.hlines(s["trigger_level"], a, b_, colors="#3a7bd5", linestyles=":", lw=1.4)
        ax.annotate(f"{s['trigger_tf']} trigger {s['trigger_level']:.6g}", (a, s["trigger_level"]), fontsize=7, color="#3a7bd5",
                    xytext=(0, 4), textcoords="offset points")
    if tr:
        a, b_ = x(tr["entry_t"]), x(tr["exit_t"])
        for lvl, c, name in ((tr["entry"], "#333", "entry"), (tr["stop"], "#d64545", "stop"), (tr["target"], "#1d9e75", "2R target")):
            ax.hlines(lvl, a, b_, colors=c, linestyles="--", lw=1.2)
            ax.annotate(f"{name} {lvl:.6g}", (b_, lvl), fontsize=7, color=c, xytext=(3, -3), textcoords="offset points")
        ax.plot(b_, tr["exit"], marker="X", color="black", ms=9)
        ax.annotate(f"exit {tr['exit']:.6g}  {tr['r']:+.2f}R ({tr['reason']})", (b_, tr["exit"]), fontsize=8, weight="bold",
                    xytext=(6, 8), textcoords="offset points")
    elif hyp:
        ax.annotate(f"would-be entry {hyp['entry_reference']:.6g} (not traded)", (x(hyp["confirmed_at"]), hyp["entry_reference"]),
                    fontsize=7, color="#777", xytext=(4, 6), textcoords="offset points")
        ax.plot(x(hyp["confirmed_at"]), hyp["entry_reference"], marker="o", mfc="none", color="#777")
    ax.set_xlim(-1, hi - lo)
    ax.set_title(title, fontsize=9.5, loc="left")
    ax.legend(loc="upper left", fontsize=7)
    ar.plot(range(hi - lo), [rsi[k] for k in range(lo, hi)], color="#8a63d2", lw=1)
    ar.plot([x(s["pivot1"]["rsi_time"]), x(s["pivot2"]["rsi_time"])], [s["pivot1"]["rsi"], s["pivot2"]["rsi"]],
            color=col, lw=2.2, marker="o")
    for v in (30, 70):
        ar.axhline(v, ls="--", c="gray", lw=0.5)
    ar.set_xlim(-1, hi - lo)
    ar.set_ylim(10, 90)
    text = "\n".join(f"{'PASS' if c['passed'] else ('----' if c['passed'] is None else 'FAIL')}  {c['rule']}: {c['detail']}"
                     for c in s.get("checks", []))
    ax.text(1.005, 1.0, text, transform=ax.transAxes, fontsize=6.6, va="top", family="monospace",
            bbox=dict(fc="#f7f7f7", ec="#ccc"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/divergence-research/shots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pool = {}
    for sym in UNIVERSE:
        for span in (RECENT, TWO_YEARS):
            d = load(sym, span)
            rows = M.clean_bars(d["candles"])
            setups, _, _ = M._study(rows, sym)
            pool.setdefault(sym, []).append((span, rows, d["funding"], setups))
    accepted, rejected = [], []
    for sym, parts in pool.items():
        span, rows, funding, setups = parts[0]
        for s in setups:
            if s["status"] == "CONFIRMED" and s["confirmed_at"] >= 1791676800000 - 30 * 86_400_000:
                accepted.append((s["confirmed_at"], sym, s, rows, funding, "last 30 days"))
        span, rows, funding, setups = parts[1]
        for s in setups:
            if s["status"] == "CONFIRMED" and s["confirmed_at"] < 1791676800000 - 30 * 86_400_000:
                accepted.append((s["confirmed_at"], sym, s, rows, funding, "older (2-year cache)"))
    accepted = sorted(accepted, key=lambda a: -a[0])[:5]
    compare = json.loads((CACHE / "compare-1791676800000.json").read_text(encoding="utf-8"))
    wanted = [r for r in compare if r["coin"] == "SOLUSDT" and r["p1"] == 118.81]
    wanted += [r for r in compare if r.get("old", {}).get("status") == "CONFIRMED" and r.get("new", {}).get("status") != "CONFIRMED"
               and (r["old"].get("r") or 0) > 0][:2]
    wanted += [r for r in compare if r.get("old", {}).get("status") == "CONFIRMED" and r.get("new", {}).get("status") != "CONFIRMED"
               and (r["old"].get("r") or 0) < 0][:1]
    wanted += [r for r in compare if (r.get("new", {}).get("reason") or "").startswith("No trend to reverse")][:1]
    span, rows_by = RECENT, {sym: pool[sym][0] for sym in pool}
    for r in wanted:
        _, rows, funding, setups = rows_by[r["coin"]]
        s = next(x for x in setups if x["pivot1"]["time"] == r["t1"] and x["pivot2"]["time"] == r["t2"] and x["divergence_tf"] == r["tf"])
        old = r.get("old", {})
        rejected.append((s, rows, funding, f"old rule: {old.get('status', 'not detected')} {('%+.2fR' % old['r']) if old.get('r') is not None else ''}"))
    for name, items in (("accepted", accepted), ("rejected", rejected)):
        fig = plt.figure(figsize=(19, 6.2 * len(items)))
        gs = fig.add_gridspec(len(items) * 2, 1, height_ratios=[3, 1.1] * len(items), hspace=0.45, right=0.66)
        for n, it in enumerate(items):
            ax, ar = fig.add_subplot(gs[2 * n]), fig.add_subplot(gs[2 * n + 1])
            if name == "accepted":
                _, sym, s, rows, funding, origin = it
            else:
                s, rows, funding, origin = it
                sym = s["symbol"]
            title = (f"{sym} {s['divergence_tf']} {s['divergence_type'].replace('_', ' ')} · {s['status']}"
                     f"{(': ' + s['reason']) if s['status'] != 'CONFIRMED' else ''} · swing 2 {hhmm(s['pivot2']['time'])} UTC · {origin}")
            draw(ax, ar, s, rows, funding, title)
        fig.savefig(out / f"div-sr3-{name}.png", dpi=72, bbox_inches="tight")
        plt.close(fig)
        print(out / f"div-sr3-{name}.png", len(items))


if __name__ == "__main__":
    main()
