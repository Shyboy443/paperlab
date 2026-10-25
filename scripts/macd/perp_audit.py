"""MACD-1 (retired) ZEC perp reconciliation: exact windows, and a per-settlement funding ledger. Reads the local snapshot."""
import csv, sys
from datetime import datetime, timezone
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.divergence.store import Store
from app.donchian import model as engine
from app.macd.model import channels
H4 = 14_400_000
ts = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
st = Store(sys.argv[1]); out_dir = sys.argv[2]
bars_all = st.bars("ZECUSDT", 0); fund = st.funding("ZECUSDT", 0)

def run(start, end, taker=0.0005, slip=0.0002, use_funding=True):
    bars = [b for b in bars_all if b["t"] < end]
    ch = channels(bars)
    def decide(i, b, held):
        if i == 0 or ch[i]["hist"] is None or ch[i - 1]["hist"] is None: return None
        if held == 0 and ch[i - 1]["hist"] <= 0 < ch[i]["hist"]: return ("enter", 1)
        if held == 1 and ch[i - 1]["hist"] >= 0 > ch[i]["hist"]: return ("exit", 0)
    old = engine.TAKER, engine.SLIP; engine.TAKER, engine.SLIP = taker, slip
    r = engine.run(bars, fund if use_funding else [], "ZECUSDT", start, 1000.0, "MACD-1", decide, ch)
    engine.TAKER, engine.SLIP = old
    return r, bars

configs = {
    "A fixed window (audit table +4,451%)": (ms("2024-10-11T00:00"), ms("2026-10-11T00:00")),
    "B rolling 730 d (summary +4,642%)": (bars_all[-1]["t"] + H4 - 730 * 86_400_000, bars_all[-1]["t"] + H4),
}
for name, (a, b) in configs.items():
    r, bars = run(a, b); m = r["summary"]
    nf, _ = run(a, b, use_funding=False)
    first = r["trades"][0]
    print(f"{name}: start {ts(a)} end {ts(b)} (exclusive) | return {m['return_pct']:+.2f}% (no funding {nf['summary']['return_pct']:+.2f}%) "
          f"DD {m['max_drawdown_pct']:.2f}% closed {m['trades']} open {bool(r['open_trade'])} | first entry {ts(first['entry_t'])} @ {first['entry']:.4f} "
          f"| last bar {ts(bars[-1]['t'])} close {bars[-1]['c']} | funding total {m['funding'] + (r['open_trade'] or {}).get('funding', 0):+.2f}")

# funding ledger for config A: replay the engine's position path and log every settlement while holding
a, b = configs["A fixed window (audit table +4,451%)"]
r, bars = run(a, b)
legs = r["trades"] + ([r["open_trade"]] if r["open_trade"] else [])
by_bar = {x["t"]: x for x in bars}
rows, tot_engine, tot_mark = [], 0.0, 0.0
for t, rate, mark in fund:
    leg = next((x for x in legs if x["entry_t"] <= t < (x["exit_t"] or b)), None)
    if not leg or t < a or t >= b: continue
    bar = by_bar[t // H4 * H4]
    pay_engine = -leg["qty"] * bar["c"] * rate                     # what the engine booked (long: dir = +1)
    pay_mark = -leg["qty"] * mark * rate                           # Binance: position notional at the mark price x rate
    tot_engine += pay_engine; tot_mark += pay_mark
    rows.append([ts(t), f"{rate:.8f}", f"{mark:.6f}", f"{bar['c']:.4f}", f"{leg['qty']:.6f}", f"{leg['qty'] * mark:.4f}",
                 f"{pay_mark:+.6f}", f"{pay_engine:+.6f}", leg["id"]])
gaps = sorted({round((y[0] - x[0]) / 3_600_000, 3) for x, y in zip(fund, fund[1:])})   # ms jitter: round, never floor
with open(out_dir + "/zec_perp_funding_ledger.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["settlement_utc", "rate", "mark_price", "engine_price_4h_close", "qty", "notional_at_mark",
                                   "payment_at_mark_usdt", "payment_engine_usdt", "trade_id"]); w.writerows(rows)
neg = sum(1 for r_ in rows if float(r_[1]) < 0)
print(f"\nfunding ledger A: {len(rows)} settlements while long ({neg} negative = paid to longs, {len(rows) - neg} positive/zero = charged), "
      f"engine total {tot_engine:+.2f} USDT, mark-price total {tot_mark:+.2f} USDT; settlement intervals seen (h): {gaps}")
