"""MACD-1 (retired) ZEC perp: recomputed returns with mark-price funding, and an exact equity bridge between the
fixed-window figure and the rolling-window figure. Reads the local snapshot; paper research."""
import json, sys, urllib.request
from datetime import datetime, timezone
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.divergence.store import Store
from app.donchian import model as engine
from app.macd.model import channels
H4 = 14_400_000
ts = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
st = Store(sys.argv[1]); bars_all = st.bars("ZECUSDT", 0); fund = st.funding("ZECUSDT", 0)

def run(start, end, funding=True):
    bars = [b for b in bars_all if b["t"] < end]; ch = channels(bars)
    def decide(i, b, held):
        if i == 0 or ch[i]["hist"] is None or ch[i - 1]["hist"] is None: return None
        if held == 0 and ch[i - 1]["hist"] <= 0 < ch[i]["hist"]: return ("enter", 1)
        if held == 1 and ch[i - 1]["hist"] >= 0 > ch[i]["hist"]: return ("exit", 0)
    return engine.run(bars, fund if funding else [], "ZECUSDT", start, 1000.0, "MACD-1", decide, ch), bars

A = (ms("2024-10-11T00:00"), ms("2026-10-11T00:00"))
Bw = (bars_all[-1]["t"] + H4 - 730 * 86_400_000, bars_all[-1]["t"] + H4)
(ra, ba), (rb, bb) = run(*A), run(*Bw)
for name, r, w in (("A fixed", ra, A), ("B rolling", rb, Bw)):
    nf, _ = run(*w, funding=False)
    o = r["open_trade"]; s = r["summary"]
    print(f"{name}: {ts(w[0])} -> {ts(w[1])} | return {s['return_pct']:+.4f}% (end equity {s['end_equity']:.6f}) | "
          f"no funding {nf['summary']['return_pct']:+.4f}% | closed {s['trades']} | open qty {o['qty']:.9f} entry {o['entry']:.6f} "
          f"funding on closed {s['funding']:+.6f} on open {o['funding']:+.6f}")
# bridge: identical paths up to A's end, then the open position is held for 5 more 4h bars
oa, ob = ra["open_trade"], rb["open_trade"]
assert oa["id"] == ob["id"] and abs(oa["qty"] - ob["qty"]) < 1e-12 and [t["id"] for t in ra["trades"]] == [t["id"] for t in rb["trades"]]
ea, eb = ra["summary"]["end_equity"], rb["summary"]["end_equity"]
pa, pb = ba[-1]["c"], bb[-1]["c"]
price_pnl = oa["qty"] * (pb - pa)
extra = [(f[0], f[1], f[2]) for f in fund if A[1] <= f[0] < Bw[1] and f[0] >= oa["entry_t"]]
extra_funding = sum(-oa["qty"] * (m or 0) * r_ for _, r_, m in extra)
print(f"\nbridge: A equity {ea:.6f} (open ZEC marked at {pa}) + price P&L {oa['qty']:.9f} x ({pb} - {pa}) = {price_pnl:+.6f}"
      f" + extra funding {extra_funding:+.6f} ({len(extra)} settlements: " + ", ".join(f"{ts(t)} rate {r_:+.8f} mark {m}" for t, r_, m in extra) +
      f") - extra costs 0 (no fills) = {ea + price_pnl + extra_funding:.6f}; B equity {eb:.6f}; residual {eb - (ea + price_pnl + extra_funding):+.2e}")
# settlement intervals: Binance stamps some settlements a few ms late, so compare at 1-second tolerance
g = [b[0] - a[0] for a, b in zip(fund, fund[1:])]
print()
print(f"funding intervals: {len(g)}, min {min(g) / 3_600_000:.6f} h, max {max(g) / 3_600_000:.6f} h, "
      f"outside 8 h +/- 1 s: {sum(abs(x - 8 * 3_600_000) > 1000 for x in g)}")
