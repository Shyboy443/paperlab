"""500 USDT size check + slippage estimate for ~100 USDT market orders (public, key-less Binance USD-M endpoints only).
Writes size_cost.json."""
import json, time, urllib.request
from collections import Counter
from datetime import datetime, timezone
import numpy as np
from core import *

P = dict(SEL, sig_ref=0.01154)
EQ = 500.0
out = {}
# order sizes a 500 USDT account would send (TEST window and last 12 months), from the simulated trades
c, tr, _, rows = sim(P, dT, d1)
frac = np.array([t[5] for t in tr])
nday = Counter(r[0] for r in rows)
out["test_trades"] = len(tr)
out["signals_per_day"] = {"days_with_signal": len(nday), "mean": round(float(np.mean(list(nday.values()))), 2),
                          "max": int(max(nday.values())), "share_days_gt5": round(float(np.mean([v > 5 for v in nday.values()])), 3)}
out["order_notional_at_500_usdt"] = {q: round(float(np.percentile(frac, q)) * EQ, 2) for q in (0, 1, 5, 25, 50, 75, 100)}
out["share_orders_below_5_usdt"] = round(float(np.mean(frac * EQ < 5)), 4)
out["share_orders_below_20_usdt"] = round(float(np.mean(frac * EQ < 20)), 4)
coins = Counter(syms[t[1]] for t in tr)
out["most_traded_test"] = coins.most_common(25)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research"}), timeout=20) as r:
        return json.loads(r.read())


try:
    info = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    filt = {}
    for s in info["symbols"]:
        if s.get("contractType") == "PERPETUAL" and s["quoteAsset"] == "USDT":
            f = {x["filterType"]: x for x in s["filters"]}
            filt[s["symbol"]] = {"status": s["status"], "min_notional": float(f.get("MIN_NOTIONAL", {}).get("notional", "nan")),
                                 "step": float(f.get("MARKET_LOT_SIZE", f.get("LOT_SIZE"))["stepSize"])}
    traded = sorted({syms[t[1]] for t in tr})
    out["min_notional_traded_test"] = {"listed_now": sum(s in filt for s in traded), "n_coins": len(traded),
                                       "distinct_min_notional": dict(Counter(filt[s]["min_notional"] for s in traded if s in filt)),
                                       "not_listed_now": [s for s in traded if s not in filt or filt[s]["status"] != "TRADING"]}
    # slippage of a 100 USDT market buy and sell vs mid, top 25 coins by TEST trade count that still trade
    sl = {}
    for s, _ in coins.most_common(25):
        if s not in filt or filt[s]["status"] != "TRADING":
            continue
        ob = get(f"https://fapi.binance.com/fapi/v1/depth?symbol={s}&limit=20")
        bids = [(float(p), float(q)) for p, q in ob["bids"]]; asks = [(float(p), float(q)) for p, q in ob["asks"]]
        mid = (bids[0][0] + asks[0][0]) / 2

        def fill(levels, usdt):
            got_q, spent = 0.0, 0.0
            for p, q in levels:
                take = min(q, (usdt - spent) / p)
                got_q += take; spent += take * p
                if spent >= usdt - 1e-9:
                    break
            return spent / got_q if got_q else float("nan")
        b, a_ = fill(asks, 100), fill(bids, 100)
        sl[s] = {"spread_bps": round((asks[0][0] - bids[0][0]) / mid * 1e4, 2),
                 "buy100_bps": round((b / mid - 1) * 1e4, 2), "sell100_bps": round((1 - a_ / mid) * 1e4, 2),
                 "top5_depth_usdt": round(min(sum(p * q for p, q in bids[:5]), sum(p * q for p, q in asks[:5])), 0)}
        time.sleep(0.25)
    out["slippage_100usdt_snapshot"] = sl
    v = np.array([(x["buy100_bps"] + x["sell100_bps"]) / 2 for x in sl.values()])
    out["slippage_summary_bps_per_side"] = {"median": round(float(np.median(v)), 2), "max": round(float(v.max()), 2),
                                            "model_bps_per_side": 2.0}
    out["snapshot_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
except Exception as e:  # network blocked etc.
    out["public_api_error"] = repr(e)
json.dump(out, open("size_cost.json", "w"), indent=1, default=float)
print(json.dumps(out, indent=1, default=float))
