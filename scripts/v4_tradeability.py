"""Compute a window's V4 coin universe with the frozen TRADEABILITY rule (app/competition/tradeability.py).

Public, read-only data only: Bybit v5 instruments-info and klines (no key), and the existence of the
Binance USD-M 1m archive files (their .CHECKSUM, a few bytes each). No PnL anywhere.

    python scripts/v4_tradeability.py --window-from 2025-11-01 --window-to 2026-04-30 --out docs/V4_UNIVERSE_DEV.json

Known limit, stated in every snapshot: the candidate list is today's Bybit instrument list, so a contract
delisted before today cannot be a candidate (survivorship), and today's tick / step filters are used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest.archive import _url, months_between  # noqa: E402
from app.competition.tradeability import TradeabilityRule, measure, pool, score  # noqa: E402

BYBIT = "https://api.bybit.com/v5/market"
DAY_MS = 86_400_000


def get(url: str, tries: int = 4) -> dict | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paperlab-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            if d.get("retCode") == 0:
                return d.get("result") or {}
        except Exception:
            pass
        time.sleep(1.0 + i)
    return None


def exists(url: str) -> bool:
    try:
        req = urllib.request.Request(url + ".CHECKSUM", headers={"User-Agent": "paperlab-research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def instruments() -> dict[str, dict]:
    out, cursor = {}, None
    while True:
        res = get(f"{BYBIT}/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else "")) or {}
        for i in res.get("list") or []:
            if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT" and i.get("status") == "Trading":
                out[i["symbol"]] = {"tick": i["priceFilter"]["tickSize"], "step": i["lotSizeFilter"]["qtyStep"],
                                    "min_qty": i["lotSizeFilter"]["minOrderQty"],
                                    "min_notional": i["lotSizeFilter"].get("minNotionalValue") or "5",
                                    "max_leverage": (i.get("leverageFilter") or {}).get("maxLeverage") or "20",
                                    "launch_ts": int(i.get("launchTime") or 0)}
        cursor = res.get("nextPageCursor")
        if not cursor:
            return out


def klines(symbol: str, interval: str, a: int, b: int) -> list[list]:
    """[start, open, high, low, close, volume, turnover], ascending, a <= start < b."""
    out: dict[int, list] = {}
    end = b - 1
    while end >= a:
        res = get(f"{BYBIT}/kline?category=linear&symbol={symbol}&interval={interval}&start={a}&end={end}&limit=1000")
        rows = (res or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            if a <= int(r[0]) < b:
                out[int(r[0])] = r
        oldest = min(int(r[0]) for r in rows)
        if oldest <= a or len(rows) < 1000:
            break
        end = oldest - 1
    return [out[k] for k in sorted(out)]


def ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-from", required=True)
    ap.add_argument("--window-to", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rule = TradeabilityRule()
    w0 = ms(args.window_from)
    s0, s1 = w0 - 30 * DAY_MS, w0
    first = dt.datetime.fromtimestamp(s0 / 1000, dt.timezone.utc).strftime("%Y-%m")
    last = args.window_to[:7]
    months = months_between(first, last)
    t0 = time.time()
    inst = instruments()
    listed = {s: f for s, f in inst.items() if f["launch_ts"] and f["launch_ts"] <= w0 - rule.min_listing_days * DAY_MS}
    print(f"instruments {len(inst)}, listed >= {rule.min_listing_days}d before the window {len(listed)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        daily = dict(zip(listed, ex.map(lambda s: klines(s, "D", s0, s1), listed)))
    turn = {s: (sorted(float(k[6]) for k in d)[len(d) // 2] if d else None) for s, d in daily.items()}
    cands = pool(turn, rule)
    print(f"pool {len(cands)} ({time.time() - t0:.0f}s)", flush=True)
    with ThreadPoolExecutor(8) as ex:
        b30 = dict(zip(cands, ex.map(lambda s: klines(s, "30", s0, s1), cands)))
        avail = dict(zip(cands, ex.map(lambda s: all(exists(_url("klines", s, "1m", m)) for m in months), cands)))
    measured = {}
    for s in cands:
        m = measure(daily[s], b30[s], listed[s], rule)
        m.update({"available": avail[s], "listed_long_enough": True, "filters": listed[s]})
        measured[s] = m
    res = score(measured, rule)
    snap = {"window": {"from": args.window_from, "to": args.window_to},
            "scoring_period": {"from": dt.datetime.fromtimestamp(s0 / 1000, dt.timezone.utc).date().isoformat(),
                               "to": dt.datetime.fromtimestamp((s1 - 1) / 1000, dt.timezone.utc).date().isoformat()},
            "tape_months_required": months,
            "sources": {"liquidity_volatility": "Bybit v5 kline (linear) D and 30", "filters": "Bybit v5 instruments-info",
                        "availability": "data.binance.vision USD-M 1m monthly archive"},
            "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limits": ["candidates are today's Bybit instruments (a contract delisted before today cannot be one)",
                       "today's tick / step / minimum filters are applied to the scoring-period price"],
            **res}
    Path(args.out).write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    print("universe", res["universe"])
    print("eligible", res["eligible"], "of", res["candidates"], "failures", res["failures"])
    for s in res["ranked"][:15]:
        r = res["coins"][s]
        print(f"  {s:14s} score {r['score']:.3f} turnover {r['turnover_usdt'] / 1e6:8.1f}M  range30m {r['range_30m_bps']:6.1f}bps "
              f"cost_ratio {r['cost_ratio']:.3f} half_spread {r['half_spread_bps']:.2f} step_share {r['step_share']:.4f}")
    for s in cands:
        r = res["coins"][s]
        if r["fail"]:
            print(f"  x {s:14s} {','.join(r['fail'])}  turnover {((r.get('turnover_usdt') or 0) / 1e6):.1f}M")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
