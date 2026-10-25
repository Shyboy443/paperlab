"""Replay the last 30 days of the Divergence Desk with the old rule (DIV-SR-2, frozen copy) and the new one (DIV-SR-3).

    python -m scripts.divergence.compare_sr2_sr3 --end 2026-10-11 --days 30 --old DIV-SR-3              # vs the live rule
    python -m scripts.divergence.compare_sr2_sr3 --end 2026-10-11 --days 30 --old DIV-SR-2 --new DIV-SR-3

Both rules see the same Binance 15m candles (120 days ending at --end, so indicators and the 90-day history are warm).
A setup is in the replay when its divergence became known inside the last --days days. Every confirmed signal is
replayed on its own (backtest(), one signal per book: next 15m open, 0.02% slippage, 0.04% fees, real funding, stop
first, 2R target, open trades marked at the last close) so old and new results are comparable trade by trade.
Writes docs/DIVERGENCE_SR<n>_COMPARISON.md (n = the newer rule) and data/divergence-research/compare-<end>-sr<n>.json.
No broker access; nothing here changes the desk.
"""
from __future__ import annotations

import argparse
import gzip
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.divergence import model
from app.divergence.backtest import backtest
from app.divergence.market import DAY, UNIVERSE, Market
from scripts.divergence import div_sr2_model, div_sr3_model, div_sr4_model, div_sr5_model, div_sr6_model, div_sr7_model, div_sr8_model

OLD = {"DIV-SR-2": div_sr2_model, "DIV-SR-3": div_sr3_model, "DIV-SR-4": div_sr4_model, "DIV-SR-5": div_sr5_model, "DIV-SR-6": div_sr6_model, "DIV-SR-7": div_sr7_model, "DIV-SR-8": div_sr8_model}

CACHE = Path("data/divergence-research")


def load(symbol: str, start: int, end: int, client: Market) -> dict:
    path = CACHE / f"{symbol}-{start}-{end}.json.gz"
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    data = {"symbol": symbol, "start": start, "end": end, "candles": client.bars(symbol, start, end),
            "funding": client.funding(symbol, start, end)}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    return data


def result_r(rows, funding, signal) -> dict:
    out = backtest(rows, [signal], funding, start_equity=1000, start_ms=signal["confirmed_at"])
    if not out["trades"]:
        return {"result": "no fill: " + (out["skipped"][0]["reason"] if out["skipped"] else "unknown")}
    t = out["trades"][0]
    risk = t["quantity"] * abs(t["entry"] - t["stop"])
    return {"entry_t": t["entry_t"], "entry": t["entry"], "stop": t["stop"], "target": t["target"], "exit_t": t["exit_t"],
            "exit": t["exit"], "exit_reason": "open (marked at last close)" if t["reason"] == "period_end" else t["reason"],
            "r": t["net"] / risk if risk else None}


def key(s):
    return (s["symbol"], s["divergence_tf"], s.get("divergence_type"), s["pivot1"]["time"], s["pivot2"]["time"])


def hhmm(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-10-11")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--old", default="DIV-SR-8", choices=sorted(OLD))
    ap.add_argument("--new", default="current", choices=["current", "DIV-SR-3", "DIV-SR-4", "DIV-SR-5", "DIV-SR-6", "DIV-SR-7"])
    args = ap.parse_args()
    old = OLD[args.old]
    new = model if args.new == "current" else OLD[args.new]
    end = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    start, window = end - 120 * DAY, end - args.days * DAY
    CACHE.mkdir(parents=True, exist_ok=True)
    client = Market(threading.Event())
    rows_out = []
    for symbol in UNIVERSE:
        data = load(symbol, start, end, client)
        bars = model.clean_bars(data["candles"])
        merged: dict = {}
        for label, mod in (("old", old), ("new", new)):
            setups, signals, _ = mod._study(bars, symbol)
            for s in setups:
                if not window <= s["divergence_at"] < end:
                    continue
                row = merged.setdefault(key(s), {"coin": symbol, "tf": s["divergence_tf"], "type": s.get("divergence_type"),
                                                 "p1": s["pivot1"]["price"], "p2": s["pivot2"]["price"],
                                                 "rsi1": s["pivot1"]["rsi"], "rsi2": s["pivot2"]["rsi"],
                                                 "t1": s["pivot1"]["time"], "t2": s["pivot2"]["time"],
                                                 "gap_atr": abs(s["pivot2"]["price"] - s["pivot1"]["price"]) / s["atr"]})
                row[label] = {"status": s["status"], "reason": s["reason"]}
                if label == "new":
                    row["checks"] = s.get("checks", [])
                    row["setup"] = s
                if s["status"] == "CONFIRMED":
                    row[label].update(result_r(bars, data["funding"], s))
        rows_out.extend(merged.values())
        print(json.dumps({"symbol": symbol, "rows": len(merged)}), flush=True)
    rows_out.sort(key=lambda r: (r["coin"], r["tf"], r["t2"]))
    write(rows_out, args, end, window, args.old, new.RULE_VERSION)


def summary(rows, label):
    traded = [r[label] for r in rows if r.get(label, {}).get("status") == "CONFIRMED"]
    rs = [t["r"] for t in traded if t.get("r") is not None]
    return {"signals": len(traded), "with_result": len(rs), "wins": sum(r > 0 for r in rs),
            "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else None, "total_r": sum(rs), "avg_r": sum(rs) / len(rs) if rs else None}


RULES = ["Stop beyond", "Swing gap", "Prior", "Support", "Resistance", "At a major", "With the EMA50", "Buying volume", "Selling volume", "Lower-timeframe trigger", "Trigger not",
         "Structure break", "Break volume", "Entry within", "No opposite"]
SHORT = {"Stop beyond": "stop", "Swing gap": "gap", "Prior": "prior trend", "Support": "S/R", "Resistance": "S/R", "At a major": "major S/R", "Buying volume": "volume", "Selling volume": "volume", "With the EMA50": "trend",
         "Lower-timeframe trigger": "LTF swing", "Trigger not": "not broken", "Structure break": "break",
         "Break volume": "volume", "Entry within": "<=1.5 ATR", "No opposite": "room to 2R"}


def checks_text(row):
    out = []
    for c in row.get("checks", []):
        name = next((SHORT[k] for k in RULES if c["rule"].startswith(k)), c["rule"])
        out.append(f"{name} {'✓' if c['passed'] else ('–' if c['passed'] is None else '✗')}")
    return ", ".join(out)


def fmt_r(x):
    return "" if x is None else f"{x:+.2f}R"


def write(rows, args, end, window, old_name="DIV-SR-2", new_name="DIV-SR-3"):
    o, n = summary(rows, "old"), summary(rows, "new")
    removed = sum(1 for r in rows if r.get("old", {}).get("status") == "CONFIRMED" and r.get("new", {}).get("status") != "CONFIRMED")
    added = sum(1 for r in rows if r.get("new", {}).get("status") == "CONFIRMED" and r.get("old", {}).get("status") != "CONFIRMED")
    pct = lambda v: "–" if v is None else f"{100 * v:.0f}%"  # noqa: E731
    lines = [f"# Divergence Desk: {old_name} vs {new_name}, last {args.days} days",
             "",
             f"Window: divergences known {hhmm(window)} .. {hhmm(end)} UTC (2026), 10 coins, 4h->1h and 1h->15m. Same "
             "Binance 15m candles for both rules. Each confirmed signal replayed alone: next 15m open, 0.02% slippage per "
             "side, 0.04% fees per side, real funding, stop first, 2R target. R is after costs; open trades are marked "
             "at the last close. Produced by `python -m scripts.divergence.compare_sr2_sr3`.",
             "",
             f"| | {old_name} (old) | {new_name} (new) |", "|---|---|---|",
             f"| Divergences recorded | {sum(1 for r in rows if 'old' in r)} | {sum(1 for r in rows if 'new' in r)} |",
             f"| Confirmed signals | {o['signals']} | {n['signals']} |",
             f"| Win rate | {pct(o['win_rate'])} ({o['wins']}/{o['with_result']}) | {pct(n['win_rate'])} ({n['wins']}/{n['with_result']}) |",
             f"| Total R | {o['total_r']:+.2f} | {n['total_r']:+.2f} |",
             f"| Average R | {fmt_r(o['avg_r'])} | {fmt_r(n['avg_r'])} |",
             "",
             f"{new_name} removes {removed} of the {o['signals']} old signals and adds {added}. A sample this small says "
             "nothing about edge either way; it shows what the new rules reject.",
             "",
             f"| Coin | TF | Type | Swing 1 | Swing 2 | RSI 1 | RSI 2 | Gap (ATR) | {new_name} rules | Old | Old R | New | New R |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        old_s, new_s = r.get("old", {}), r.get("new", {})
        lines.append(f"| {r['coin'].replace('USDT', '')} | {r['tf']} | {(r['type'] or '').replace('_', ' ')} | "
                     f"{r['p1']:.6g} ({hhmm(r['t1'])}) | {r['p2']:.6g} ({hhmm(r['t2'])}) | {r['rsi1']:.1f} | {r['rsi2']:.1f} | "
                     f"{r['gap_atr']:.2f} | {checks_text(r)} | {old_s.get('status', 'not detected')} | {fmt_r(old_s.get('r'))} | "
                     f"{new_s.get('status', 'not detected')}{(': ' + new_s['reason']) if new_s.get('status') not in (None, 'CONFIRMED') else ''} | "
                     f"{fmt_r(new_s.get('r'))} |")
    lines += ["", "✓ passed, ✗ failed, – not reached. The lower-timeframe rules are evaluated for filtered setups too, "
              "so every rule shows; a filtered setup never trades."]
    tag = new_name.split("-")[-1]
    Path(f"docs/DIVERGENCE_SR{tag}_COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    slim = [{k: v for k, v in r.items()} for r in rows]
    (CACHE / f"compare-{end}-sr{tag}.json").write_text(json.dumps(slim, default=str), encoding="utf-8")
    print(json.dumps({"old": o, "new": n, "removed": removed, "added": added, "rows": len(rows)}), flush=True)


if __name__ == "__main__":
    main()
