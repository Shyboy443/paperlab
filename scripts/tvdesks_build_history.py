"""Build the HISTORICAL sections of the T3-CCI and CHI desks from the research package's recorded snapshot.

    python scripts/tvdesks_build_history.py <path to the unpacked TradingView_Research folder>

Runs the app's own (verified, see scripts/tvdesks_verify.py) aggregation, indicators and broker over the recorded data
and writes app/tvdesks/history/{t3cci,chi}.json.gz:
- research reproduction (fractional, base and doubled costs): per book metrics, every trade, equity, monthly rows,
  chronological splits (first 18 months / last 6 months, carried positions);
- execution simulation over the same history with TODAY's exchange order filters (quantity step, minimum notional):
  a different experiment, reported separately -- filters were not recorded historically.
Historical results only; nothing here is a forward or guaranteed return.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.tvdesks import broker as B  # noqa: E402
from app.tvdesks.data import aggregate_4h  # noqa: E402
from app.tvdesks.indicators import chi_requests, t3cci_requests  # noqa: E402
from app.tvdesks.service import DESKS  # noqa: E402

START, END, SPLIT = 1728604800000, 1791676800000, 1775865600000
OUT = PROJECT / "app" / "tvdesks" / "history"


def to_ms(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).timestamp() * 1000)


def month(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m")


def load_bars(pkg: Path, row: dict) -> list[dict]:
    with gzip.open(pkg / "data" / Path(row["path"]).name, "rt", newline="") as f:
        hours = [{"t": to_ms(r["time"]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
                  "v": float(r["volume"])} for r in csv.DictReader(f)]
    return aggregate_4h(hours)


def monthly(times, equity, trades, opening):
    out, prior, by = [], opening, {}
    for t, e in zip(times, equity):
        by.setdefault(month(t), []).append(e)
    for m, vals in by.items():
        out.append({"month": m, "opening_equity": prior, "ending_equity": vals[-1], "net_return_pct": (vals[-1] / prior - 1) * 100,
                    "closed_trades": sum(1 for x in trades if month(x["exit_time"]) == m), "partial_month": m in ("2024-10", "2026-10")})
        prior = vals[-1]
    return out


def splits(times, equity, capital):
    i = next(k for k, t in enumerate(times) if t >= SPLIT)
    return {"first_18_months_pct": (equity[i - 1] / capital - 1) * 100, "last_6_months_pct": (equity[-1] / equity[i - 1] - 1) * 100}


def lean_trades(trades, asset):
    keys = ("entry_time", "exit_time", "entry_price", "exit_price", "units", "entry_cash", "exit_cash", "entry_fee", "exit_fee", "pnl", "forced_exit")
    return [{"asset": asset, **{k: t[k] for k in keys}} for t in trades]


def main() -> int:
    pkg = Path(sys.argv[1])
    cov = {r["requested"]: r for r in json.loads((pkg / "data-coverage.json").read_text()) if r["status"] == "full_period"}
    bh = json.loads((pkg / "buy-and-hold-control.json").read_text())
    macd = json.loads((pkg / "macd-control.json").read_text())
    from app.tvdesks.feeds import Feeds
    feeds = Feeds()
    OUT.mkdir(parents=True, exist_ok=True)
    for desk in ("t3cci", "chi"):
        cfg = DESKS[desk]
        books, trades, port = [], [], {1: None, 2: None}
        port_trades = {1: [], 2: []}
        per_equity, monthly_books = {}, {}
        for b in cfg["books"]:
            bars = load_bars(pkg, cov[b["asset"]])
            buy, sell, _ = (t3cci_requests(bars, START, END) if desk == "t3cci" else chi_requests(bars, START, END, islast=True))
            res = {c: B.replay(bars, buy, sell, START, END, cost_multiplier=c) for c in (1, 2)}
            try:
                f = feeds.filters(b["venue"], b["pair"])
                ex = B.replay(bars, buy, sell, START, END, filters=B.Filters(f["step"], f["min_qty"], f["min_notional"], f["source"]))
                exec_sim = {"filters": f, "metrics": ex["metrics"], "skips": ex["skips"], "dust_units": ex["dust"]}
            except Exception as exc:
                exec_sim = {"error": f"{type(exc).__name__}: {exc}"}
            r1 = res[1]
            books.append({**b, "metrics": {str(c): res[c]["metrics"] for c in (1, 2)}, "splits": splits(r1["times"], r1["equity"], 10000.0),
                          "execution_simulation": exec_sim})
            trades += lean_trades(r1["trades"], b["asset"])
            for c in (1, 2):
                rr = res[c]
                if port[c] is None:
                    port[c] = {"times": rr["times"], "equity": list(rr["equity"]), "high": list(rr["high_equity"]), "low": list(rr["low_equity"])}
                else:
                    for k, src in (("equity", "equity"), ("high", "high_equity"), ("low", "low_equity")):
                        port[c][k] = [x + y for x, y in zip(port[c][k], rr[src])]
                port_trades[c] += rr["trades"]
            if desk == "t3cci":
                per_equity[b["asset"]] = {"times": r1["times"], "equity": r1["equity"], "high": r1["high_equity"], "low": r1["low_equity"]}
                monthly_books[b["asset"]] = monthly(r1["times"], r1["equity"], r1["trades"], 10000.0)
            else:
                daily = {}
                for t, e in zip(r1["times"], r1["equity"]):
                    daily[(t // 86_400_000) * 86_400_000] = e
                per_equity[b["asset"]] = sorted(daily.items())
        n = len(cfg["books"])
        cap = 10000.0 * n
        pm = {str(c): B.metrics(port[c]["equity"], port[c]["high"], port[c]["low"], cap, port_trades[c]) for c in (1, 2)}
        endings = {bk["asset"]: bk["metrics"]["1"]["ending_capital"] for bk in books}
        rets = sorted((v / 10000 - 1) * 100 for v in endings.values())
        summary = {
            "desk": desk, "name": cfg["name"], "strategy": cfg["strategy"], "url": cfg["url"], "window_utc": ["2024-10-11 00:00", "2026-10-11 00:00 (exclusive)"],
            "capital_per_book": 10000.0, "books": n, "allocated_capital": cap, "costs": {"commission_per_side": B.FEE, "slippage_per_side": B.SLIP},
            "mode": "research reproduction (fractional units, the package's cash broker)", "portfolio": pm,
            "portfolio_splits": splits(port[1]["times"], port[1]["equity"], cap),
            "positive_books": sum(1 for v in endings.values() if v > 10000), "losing_books": sum(1 for v in endings.values() if v <= 10000),
            "median_book_return_pct": (rets[(n - 1) // 2] + rets[n // 2]) / 2,
            "without_zec_return_pct": ((sum(v for a, v in endings.items() if a != "ZEC") / (10000.0 * (n - 1)) - 1) * 100) if "ZEC" in endings and n > 1 else None,
            "comparisons": {"macd_zec_spot_audited_pct": macd["1"]["net_return_pct"], "equal_weight_buy_and_hold_84_pct": bh["1"]["net_return_pct"],
                            "zec_buy_and_hold_pct": 3503.46},                    # RESEARCH_REPORT.md (same entry/exit costs)
            "verification": "scripts/tvdesks_verify.py -> docs/TVDESKS_AUDIT.md",
            "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "historical_only": True}
        doc = {"summary": summary, "books": books, "trades": trades,
               "portfolio_equity": {"times": port[1]["times"], "equity": port[1]["equity"], "high": port[1]["high"], "low": port[1]["low"]},
               "portfolio_monthly": {str(c): monthly(port[c]["times"], port[c]["equity"], port_trades[c], cap) for c in (1, 2)},
               "book_monthly": monthly_books, "book_equity": per_equity}
        (OUT / f"{desk}.json.gz").write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode()))
        print(desk, "books", n, "return", round(pm["1"]["net_return_pct"], 6), "trades", pm["1"]["trades"],
              "size KB", round((OUT / f"{desk}.json.gz").stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
