"""Per-timestamp / per-trade comparison of the operator's package with PaperLab's independent data and the MACD-2 desk code."""
import csv, gzip, json, sys
from datetime import datetime, timezone
import pandas as pd
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.macd import model as m
P, MINE = sys.argv[1], sys.argv[2]
iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")

# 1) hourly candles: package CSV vs our own Binance spot API download
pk = pd.read_csv(f"{P}/Binance-ZECUSDT-1h.csv.gz", index_col=0, float_precision="round_trip"); pk.index = pd.to_datetime(pk.index, utc=True)
mine = {int(r["open_time"]): r for r in csv.DictReader(gzip.open(MINE, "rt"))}
print("package columns:", list(pk.columns), "rows", len(pk), pk.index[0], "->", pk.index[-1])
diffs, missing = [], 0
for ts, row in pk.iterrows():
    t = int(ts.value // 10**6); r = mine.get(t)
    if r is None: missing += 1; continue
    for k in ("open", "high", "low", "close", "volume"):
        if abs(float(row[k]) - float(r[k])) > 1e-9 * max(1, abs(float(r[k]))): diffs.append((iso(t), k, row[k], r[k]))
print(f"[1] hourly candles: {len(pk)} in package, {len(mine)} in ours, missing in ours {missing}, extra in ours {len(set(mine) - {int(x.value // 10**6) for x in pk.index})}, "
      f"field mismatches {len(diffs)}" + (f", first {diffs[0]}" if diffs else ""))

# 4h aggregation as the desk does it, from the PACKAGE data
hours = [dict(t=int(ts.value // 10**6), o=float(r.open), h=float(r.high), l=float(r.low), c=float(r.close), v=float(r.volume)) for ts, r in pk.iterrows()]
bars = m.aggregate(hours)
print(f"    4h candles from the package: {len(bars)} (first {iso(bars[0]['t'])}, last {iso(bars[-1]['t'])})")

# 2) native PineTS signals vs the desk's crossovers
nat = json.load(open(f"{P}/macd-native-signals.json"))
print("    native file keys:", [k for k in nat if not k.endswith("indices")], "bars", nat.get("bars"))
ch = m.channels(bars)
ours_buy = {i for i in range(1, len(bars)) if ch[i]["hist"] is not None and ch[i - 1]["hist"] is not None and ch[i - 1]["hist"] <= 0 < ch[i]["hist"]}
ours_sell = {i for i in range(1, len(bars)) if ch[i]["hist"] is not None and ch[i - 1]["hist"] is not None and ch[i - 1]["hist"] >= 0 > ch[i]["hist"]}
win = {i for i, b in enumerate(bars) if m.BACKTEST_START <= b["t"] < m.BACKTEST_END}
nb, ns = set(nat["buy_indices"]), set(nat["sell_indices"])
for name, ours, theirs in (("buy", ours_buy, nb), ("sell", ours_sell, ns)):
    w_o, w_t = ours & win, theirs & win
    print(f"[2] {name}: window {len(w_t)} native vs {len(w_o)} ours, identical={w_o == w_t}; "
          f"warm-up native {sorted(iso(bars[i]['t']) for i in theirs - win)} vs ours {sorted(iso(bars[i]['t']) for i in ours - win)}")

# 3) every reference trade vs the desk's MACD-2 code on the package data
res = m.simulate(bars, (), "ZECUSDT", start_ms=m.BACKTEST_START, end_ms=m.BACKTEST_END, close_at_end=True)
ref = pd.read_csv(f"{P}/reference-trades.csv")
ts_ = lambda s: int(pd.Timestamp(s).value // 10**6)
worst = {k: 0.0 for k in ("entry_price", "exit_price", "units", "entry_cash", "exit_cash")}
first_bad = None
for k, (r, t) in enumerate(zip(ref.itertuples(), res["trades"])):
    times_ok = ts_(r.entry_time) == t["entry_t"] and ts_(r.exit_time) == t["exit_t"] and ts_(r.entry_signal_time) == t["signal_t"]
    vals = dict(entry_price=t["entry"], exit_price=t["exit"], units=t["qty"], entry_cash=t["notional"], exit_cash=t["notional"] + t["pnl"])
    for f, v in vals.items():
        worst[f] = max(worst[f], abs(getattr(r, f) - v) / max(1, abs(v)))
    if not times_ok and first_bad is None: first_bad = (k + 1, r.entry_time, iso(t["entry_t"]))
print(f"[3] trades: reference {len(ref)} vs desk {len(res['trades'])}; timestamp mismatches: {first_bad or 'none'}; "
      f"max relative differences: " + ", ".join(f"{k} {v:.1e}" for k, v in worst.items()))
s = res["summary"]; print(f"    desk summary: cash {s['end_equity']:.6f}, return {s['return_pct']:+.6f}%, DD {s['max_drawdown_pct']:.6f}%")

# 4) equity per 4h candle vs the package's reproduced equity
eq = pd.read_csv(f"{P}/reproduced-equity-cost-1.csv", index_col=0); eq.index = pd.to_datetime(eq.index, utc=True)
ours_eq = {c["time"] - m.TF_MS: c["value"] for c in res["equity_curve"]}       # our points are stamped at the candle close
bad = [(ts, row.equity, ours_eq.get(int(ts.value // 10**6))) for ts, row in eq.iterrows()
       if ours_eq.get(int(ts.value // 10**6)) is None or abs(row.equity - ours_eq[int(ts.value // 10**6)]) > 1e-6 * max(1, row.equity)]
print(f"[4] equity: {len(eq)} package candles vs {len(ours_eq)} desk points; mismatches beyond 1e-6 relative: {len(bad)}" + (f", first {bad[0]}" if bad else ""))
