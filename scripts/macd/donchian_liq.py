"""Donchian (DTR-1, perp, 1x) liquidation-distance check on 4h trade-price candles (intrabar high/low)."""
import sys
from datetime import datetime, timezone
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.divergence.store import Store
from app.divergence.market import UNIVERSE
from app.donchian import model
ts = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
MMR = 0.025                                                   # conservative; Binance tier-1 MMR on these perps is lower
st = Store(sys.argv[1])
worst, n_long, n_short = [], 0, 0
for sym in UNIVERSE:
    bars = st.bars(sym, 0); fund = st.funding(sym, 0)
    end = bars[-1]["t"] + model.TF_MS
    r = model.simulate(bars, fund, sym, start_ms=end - 730 * 86_400_000)
    for t in r["trades"] + ([r["open_trade"]] if r["open_trade"] else []):
        held = [b for b in bars if t["entry_t"] <= b["t"] < (t["exit_t"] or end)]
        if t["side"] == "short":
            n_short += 1
            liq = 2 * t["entry"] / (1 + MMR)
            hi = max(b["h"] for b in held)
            worst.append(((hi - t["entry"]) / (liq - t["entry"]), sym, "short", t["entry_t"], t["entry"], hi, liq))
        else:
            n_long += 1
            lo = min(b["l"] for b in held)
            worst.append((0.0, sym, "long", t["entry_t"], t["entry"], lo, 0.0))
shorts = sorted([w for w in worst if w[2] == "short"], reverse=True)
print(f"trades checked: {n_long} longs (1x long liquidation price = 0) and {n_short} shorts")
print("closest shorts to liquidation (share of the entry -> liquidation distance reached intrabar):")
for w in shorts[:5]:
    print(f"  {w[1]:9s} short {ts(w[3])} entry {w[4]:.6g}  worst high {w[5]:.6g} ({100 * (w[5] / w[4] - 1):+.1f}%)  liq {w[6]:.6g}  -> {100 * w[0]:.1f}% of the way")
longs = sorted([w for w in worst if w[2] == "long"], key=lambda w: w[5] / w[4])
w = longs[0]; print(f"worst long drawdown intrabar: {w[1]} {ts(w[3])} entry {w[4]:.6g} low {w[5]:.6g} ({100 * (w[5] / w[4] - 1):+.1f}%)")
