"""Donchian (DTR-1) 1x perp liquidation audit with Binance mark-price candles, published maintenance-margin brackets, and
collateral updated for the entry fee and every funding settlement. Paper research; reads the local perp snapshot."""
import json, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.backtest import brackets as BR
from app.divergence.market import UNIVERSE
from app.divergence.store import Store
from app.donchian import model
ts = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
snap, out_dir = sys.argv[1], Path(sys.argv[2])
st = Store(snap)
table = BR.load(list(UNIVERSE), out_dir, refresh=True)
print(f"brackets: source {table.source}, retrieved {table.retrieved_at} (current tiers; historical tiers may have differed)")

def mark_klines(sym, start, end):
    cache = out_dir / f"mark-{sym}.json"
    if cache.exists(): return json.loads(cache.read_text())
    out, cur = [], start
    while cur < end:
        url = f"https://fapi.binance.com/fapi/v1/markPriceKlines?symbol={sym}&interval=4h&startTime={cur}&endTime={end - 1}&limit=1500"
        part = json.load(urllib.request.urlopen(url, timeout=30))
        if not part: break
        out += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in part]
        cur = int(part[-1][0]) + model.TF_MS; time.sleep(0.2)
        if len(part) < 1500: break
    cache.write_text(json.dumps(out)); return out

rows, worst = [], []
for sym in UNIVERSE:
    bars = st.bars(sym, 0); fund = st.funding(sym, 0)
    end = bars[-1]["t"] + model.TF_MS
    res = model.simulate(bars, fund, sym, start_ms=end - 730 * 86_400_000)
    marks = {r[0]: r for r in mark_klines(sym, bars[0]["t"], end)}
    missing = 0
    for t in res["trades"] + ([res["open_trade"]] if res["open_trade"] else []):
        side = 1 if t["side"] == "long" else -1
        qty, entry, wb = t["qty"], t["entry"], t["notional"]          # 1x: the wallet behind the position = notional after the entry fee
        stop = t["exit_t"] or end
        held = [b for b in bars if t["entry_t"] <= b["t"] < stop]
        worst_ratio, worst_at = 0.0, None
        for b in held:
            mk = marks.get(b["t"])
            if mk is None: missing += 1; continue
            pays = [-side * qty * (f[2] or b["c"]) * f[1] for f in fund if b["t"] <= f[0] < b["t"] + model.TF_MS]
            wb_low = wb + sum(p for p in pays if p < 0)                # charges inside the bar may land before the extreme
            px = mk[3] if side == 1 else mk[2]                          # worst mark price in the bar (low for a long, high for a short)
            notional = qty * px
            maint = table.maintenance_margin(sym, notional)
            balance = wb_low + side * qty * (px - entry)               # margin balance = wallet + unrealized PnL at mark
            ratio = maint / balance if balance > 0 else float("inf")
            if ratio > worst_ratio: worst_ratio, worst_at = ratio, (b["t"], px, balance, maint, table.mmr(sym, notional))
            wb += sum(pays)                                            # all settlements of this bar
        worst.append((worst_ratio, sym, t["side"], t["entry_t"], entry, worst_at))
    rows.append((sym, len(res["trades"]), missing))
worst.sort(key=lambda w: -w[0])
liquidated = [w for w in worst if w[0] >= 1]
print(f"trades checked: {len(worst)} over {len(UNIVERSE)} coins; mark candles missing: {sum(r[2] for r in rows)}")
print(f"liquidations under these assumptions: {len(liquidated)}")
print("highest margin ratios (maintenance margin / margin balance; Binance liquidates at >= 100%):")
for r, sym, side, et, entry, at in worst[:6]:
    print(f"  {sym:9s} {side:5s} entry {ts(et)} @ {entry:.6g}: {100 * r:.2f}% at {ts(at[0])}, mark {at[1]:.6g} ({100 * (at[1] / entry - 1):+.1f}%), "
          f"balance {at[2]:.2f}, maintenance {at[3]:.2f} (MMR {100 * at[4]:.2f}%)")
