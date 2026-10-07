"""Freeze V6 BEFORE its live forward experiment starts: docs/V6_FREEZE.json (+ docs/V6_UNIVERSE.json).

    python scripts/v6_freeze.py [--as-of 2026-09-24]

What is frozen (docs/V6_PROTOCOL.md §9):

    code       sha256 of every V6 strategy module, the V6 base, the V3 base, the V6 feature definitions, the V6
               configuration (field, execution, AGGRESSIVE_V6 sizing, maturity), Jev V6 and the live gates
    params     every family's parameters
    config     execution (decision window, fill timing, spread), fees, risk profile, engine guards, warm-up windows
    universe   the V6 UNIVERSE RULE on the 30 days before the freeze -- the V5 liquidity / cost / legality rule
               (unchanged, fingerprint in the file) applied to STANDARD CRYPTO contracts only (Bybit symbolType
               empty: no tokenized stocks, ETFs, commodities or Innovation-Zone listings); the top 4 are traded,
               BTC, ETH and the 14 most liquid other candidates form the observed BREADTH SET
    rules      Bybit instrument filters of the traded coins (tick, step, minimum quantity and notional)
    jev        the Jev V6 prompt, state builder and policy fingerprints

Public Bybit data only. The live worker refuses to trade while the running code differs from this file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.v6 import v6_fingerprints as jev_fingerprints  # noqa: E402
from app.backtest import bybit_archive as bb  # noqa: E402
from app.competition import v6_config as c6  # noqa: E402
from app.competition.v5_universe import UniverseRuleV5, measure, score  # noqa: E402

DOCS = PROJECT / "docs"
DAY = 86_400_000


def ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat()


def instruments() -> dict[str, dict]:
    out, cursor = {}, None
    while True:
        res = bb.get(f"{bb.BYBIT}/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))
        for i in res.get("list") or []:
            if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT" and i.get("status") == "Trading":
                out[i["symbol"]] = {"tick": i["priceFilter"]["tickSize"], "step": i["lotSizeFilter"]["qtyStep"],
                                    "min_qty": i["lotSizeFilter"]["minOrderQty"],
                                    "min_notional": i["lotSizeFilter"].get("minNotionalValue") or "5",
                                    "max_leverage": (i.get("leverageFilter") or {}).get("maxLeverage") or "20",
                                    "funding_interval_min": int(i.get("fundingInterval") or 480),
                                    "launch_ts": int(i.get("launchTime") or 0), "symbol_type": i.get("symbolType") or "",
                                    "full_name": i.get("fullName") or ""}
        cursor = res.get("nextPageCursor")
        if not cursor:
            return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    out = DOCS / "V6_FREEZE.json"
    if out.exists() and not args.force:
        print(f"{out} exists: V6 is already frozen (use --force only to start a NEW, re-frozen experiment)")
        return 2
    rule = UniverseRuleV5()
    w0 = ms(args.as_of)
    s0, s1 = w0 - 30 * DAY, w0
    warm = w0 - 45 * DAY
    t0 = time.time()
    inst = instruments()
    crypto = {s: f for s, f in inst.items() if f["symbol_type"] == ""}
    listed = {s: f for s, f in crypto.items() if f["launch_ts"] and f["launch_ts"] <= w0 - rule.min_listing_days * DAY}
    print(f"instruments {len(inst)}; standard crypto {len(crypto)}; listed >= {rule.min_listing_days}d: {len(listed)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        daily = dict(zip(listed, ex.map(lambda s: bb.klines(s, "D", s0, s1), listed)))
    turn = {s: statistics.median([float(k[6]) for k in d]) for s, d in daily.items() if d}
    pool = [s for s, _ in sorted(turn.items(), key=lambda kv: (-kv[1], kv[0]))[:rule.pool_size]]

    def probe(s: str) -> dict:
        return {"tape": bool(bb.klines(s, "1", warm, warm + 60 * bb.MIN)),
                "funding": bool(bb.funding(s, warm, warm + 3 * DAY)),
                "open_interest": bool(bb.open_interest(s, warm, warm + DAY))}
    with ThreadPoolExecutor(8) as ex:
        b4 = dict(zip(pool, ex.map(lambda s: bb.klines(s, "240", s0, s1), pool)))
        data = dict(zip(pool, ex.map(probe, pool)))
    measured = {}
    for s in pool:
        m = measure(daily[s], b4[s], listed[s], rule)
        m.update({"listed_long_enough": True, "data": data[s], "filters": listed[s]})
        measured[s] = m
    res = score(measured, rule)
    traded = [s for s in res["ranked"]][:c6.TRADED_COINS]
    others = [s for s in pool if s not in ("BTCUSDT", "ETHUSDT") and all((data.get(s) or {}).values())]
    breadth = ["BTCUSDT", "ETHUSDT"] + others[:c6.BREADTH_SET - 2]
    for s in traded:
        if s not in breadth:
            breadth.append(s)
    rules = {}
    for s in traded:
        f = listed[s]
        dec = lambda x: max(0, len(str(x).rstrip("0").split(".")[1]) if "." in str(x) else 0)  # noqa: E731
        rules[s] = {"symbol": s, "tick": float(f["tick"]), "step": float(f["step"]), "min_qty": float(f["min_qty"]),
                    "min_notional": float(f["min_notional"]), "maint_margin_rate": 0.025,
                    "price_precision": dec(f["tick"]) + 2, "qty_precision": dec(f["step"]) + 2}
    universe = {"as_of": args.as_of, "scoring_period": {"from": iso(s0), "to": iso(s1 - 1)},
                "rule": rule.to_dict(), "rule_fingerprint": rule.fingerprint(),
                "asset_filter": "Bybit symbolType == '' (standard crypto; excludes stock, ETF, commodity, innovation)",
                "traded": [s[:-4] for s in traded], "traded_symbols": traded, "ranked": res["ranked"],
                "eligible": res["eligible"], "candidates": res["candidates"], "failures": res["failures"]}
    man = {"protocol": c6.PROTOCOL_VERSION, "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "code": c6.code_fingerprints(), "params": c6.params_snapshot(),
           "config": json.loads(json.dumps(c6.config_snapshot(), default=str)), "universe": universe,
           "breadth_set": breadth, "rules": rules,
           "funding_interval_min": {s: int(inst[s]["funding_interval_min"]) for s in breadth if s in inst},
           "jev": jev_fingerprints(), "field": [s.key for s in c6.field_plan([s[:-4] for s in traded])],
           "note": "V6 is evaluated on LIVE FORWARD data only; nothing here was fitted to V1-V5 results"}
    man["fingerprint"] = c6.manifest_fingerprint(man)
    out.write_text(json.dumps(man, indent=1), encoding="utf-8")
    (DOCS / "V6_UNIVERSE.json").write_text(json.dumps({**universe, "coins": res["coins"]}, indent=1, default=str),
                                           encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "traded": universe["traded"], "breadth_set": breadth,
                      "field": len(man["field"]), "rules": rules}, indent=1))
    print(f"frozen in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
