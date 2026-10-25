"""Independent reproduction of the MACD (12/26/9) long-only ZEC spot backtest, per the operator's spec."""
import csv, gzip, sys
from datetime import datetime, timezone
import pandas as pd

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
ts = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
H, H4 = 3_600_000, 14_400_000
rows = list(csv.DictReader(gzip.open(sys.argv[1], "rt")))
hours = [dict(t=int(r["open_time"]), o=float(r["open"]), h=float(r["high"]), l=float(r["low"]), c=float(r["close"]), v=float(r["volume"])) for r in rows]
assert all(b["t"] - a["t"] == H for a, b in zip(hours, hours[1:])), "gap in hourly data"
buckets = {}
for b in hours: buckets.setdefault(b["t"] // H4 * H4, []).append(b)
bars = [dict(t=t, o=g[0]["o"], h=max(x["h"] for x in g), l=min(x["l"] for x in g), c=g[-1]["c"], v=sum(x["v"] for x in g))
        for t, g in sorted(buckets.items()) if len(g) == 4]
T0, T1 = ms("2024-10-11T00:00"), ms("2026-10-11T00:00")
trade_idx = [i for i, b in enumerate(bars) if T0 <= b["t"] < T1]
print(f"4h candles {len(bars)}, tradable {len(trade_idx)}, first tradable open {bars[trade_idx[0]]['o']}, final close {bars[trade_idx[-1]]['c']}")

def pine_ema(vals, n):
    out, prev, seed, a = [], None, [], 2 / (n + 1)
    for v in vals:
        if v is None: out.append(None); continue
        if prev is None:
            seed.append(v); prev = sum(seed) / n if len(seed) == n else None; out.append(prev); continue
        prev = a * v + (1 - a) * prev; out.append(prev)
    return out

closes = [b["c"] for b in bars]
fast, slow = pine_ema(closes, 12), pine_ema(closes, 26)
macd = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
sig = pine_ema(macd, 9)
# independent pandas EMA (adjust=False, seeded with the first value)
cs = pd.Series(closes); pm = cs.ewm(span=12, adjust=False).mean() - cs.ewm(span=26, adjust=False).mean(); ps = pm.ewm(span=9, adjust=False).mean()

def crosses(m, s):
    ev = {}
    for i in range(1, len(bars)):
        if None in (m[i], s[i], m[i - 1], s[i - 1]): continue
        if m[i] > s[i] and m[i - 1] <= s[i - 1]: ev[i] = "buy"
        elif m[i] < s[i] and m[i - 1] >= s[i - 1]: ev[i] = "sell"
    return ev
ev = crosses(macd, sig); evp = crosses(list(pm), list(ps))
win = set(trade_idx)
a, b = {i: v for i, v in ev.items() if i in win}, {i: v for i, v in evp.items() if i in win}
print(f"crossovers in window: pine-seeded {len(a)}, pandas {len(b)}, identical {a == b}")

def backtest(f, s, events, verbose=False):
    cash, units, trades, peak, mdd, entry = 10_000.0, 0.0, [], 10_000.0, 0.0, None
    last = trade_idx[-1]
    pending = None
    for i in trade_idx:
        if pending:                                   # fill the previous candle's signal at this candle's open
            e, pending = pending, None
            if e == "buy" and units == 0:
                bp = bars[i]["o"] * (1 + s); units = cash / (bp * (1 + f)); entry = (bars[i]["t"], bp, units, cash); cash = 0.0
            elif e == "sell" and units > 0:
                sp = bars[i]["o"] * (1 - s); cash = units * sp * (1 - f); trades.append((*entry, bars[i]["t"], sp, cash)); units = 0.0
        eq = cash + units * bars[i]["c"]              # mark the position actually held at this candle's close
        peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
        if events.get(i) and i < last:                # a final-candle signal has no next tradable open
            pending = events[i]
    if units > 0:
        sp = bars[last]["c"] * (1 - s); cash = units * sp * (1 - f); trades.append((*entry, bars[last]["t"] + H4, sp, cash)); units = 0.0
    return cash, trades, mdd

for f, s, ref_ret, ref_dd in ((0.001, 0.0002, 2984.902755, 37.310030), (0.002, 0.0004, 1996.185737, 42.427359)):
    cash, trades, mdd = backtest(f, s, a)
    print(f"\nfee {f:.2%} slip {s:.2%}: closed trades {len(trades)}, ending cash {cash:,.6f}, return {100*(cash/1e4-1):+,.6f}% (ref {ref_ret:+,.6f}%), "
          f"max DD {100*mdd:.6f}% (ref {ref_dd:.6f}%)")
    if f == 0.001:
        t = trades[0]
        print(f"  first trade: buy {ts(t[0])} @ {t[1]:.6f} qty {t[2]:.9f} | sell {ts(t[4])} @ {t[5]:.6f} cash {t[6]:,.6f}")
        with open(sys.argv[2], "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["n", "buy_time", "buy_price", "qty", "sell_time", "sell_price", "cash_after"])
            for n, t in enumerate(trades, 1): w.writerow([n, ts(t[0]), f"{t[1]:.6f}", f"{t[2]:.9f}", ts(t[4]), f"{t[5]:.6f}", f"{t[6]:.6f}"])
