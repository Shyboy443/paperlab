"""MACD 12/26/9 on Binance USD-M ZECUSDT 4h, long/cash 1x, one account from 2026-01-01 00:00 UTC (flat start).
Fill: next 4h open. Costs: commission f and adverse slippage s per side. Funding: every settlement while long, on
units x Binance's recorded settlement mark price; a position filled at T pays the settlement stamped at/after T and an
exit at T does not (settlements are stamped T + a few ms). Equity marked at hourly closes: units x close + funding."""
import csv, json, sys
from datetime import datetime, timezone
sys.path.insert(0, "D:/Projects/BinanceBOt/paperlab")
from app.macd.model import channels
src, out_dir = sys.argv[1], sys.argv[2]
F, S, START_CASH = 0.0005, 0.0002, 10.0
ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
d = json.load(open(src))
b4 = [dict(t=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in d["k4h"]]
h1 = [dict(t=r[0], c=float(r[4])) for r in d["k1h"]]
fund = [(int(t), float(rate), float(mk)) for t, rate, mk in d["funding"]]
assert all(b[1]["t"] - b[0]["t"] == 14_400_000 for b in zip(b4, b4[1:])), "gap in 4h"
assert all(b[1]["t"] - b[0]["t"] == 3_600_000 for b in zip(h1, h1[1:])), "gap in 1h"
ch = channels(b4)
T0 = ms("2026-01-01T00:00")

def run(end_ms, sell_at_end):
    """Fills from 4h signals at closes >= T0 (signal candle opens >= T0 - 4h is excluded: start flat at T0)."""
    fills = []
    held = False
    for i in range(1, len(b4) - 1):
        if b4[i]["t"] < T0 or b4[i + 1]["t"] >= end_ms: continue
        h, hp = ch[i]["hist"], ch[i - 1]["hist"]
        if h is None or hp is None: continue
        if not held and hp <= 0 < h: fills.append(("buy", b4[i + 1]["t"], b4[i + 1]["o"])); held = True
        elif held and hp >= 0 > h: fills.append(("sell", b4[i + 1]["t"], b4[i + 1]["o"])); held = False
    cash, units, fund_acc, trades, ledger, k, fi = START_CASH, 0.0, 0.0, [], [], 0, 0
    entry = None
    curve = []                                         # (time of hourly close, equity)
    hours = [x for x in h1 if T0 <= x["t"] and x["t"] + 3_600_000 <= end_ms]
    for hb in hours:
        t0, t1 = hb["t"], hb["t"] + 3_600_000
        while k < len(fills) and fills[k][1] <= t0:    # fills happen at this hour's open
            kind, ft, px = fills[k]; k += 1
            if kind == "buy":
                bp = px * (1 + S); units = cash / (bp * (1 + F)); entry = dict(t=ft, px=bp, cash=cash, funding=0.0); cash = 0.0
            else:
                sp = px * (1 - S); cash = units * sp * (1 - F) + fund_acc
                trades.append(dict(entry_t=iso(entry["t"]), entry=entry["px"], exit_t=iso(ft), exit=sp, units=units,
                                   cash_in=entry["cash"], funding=fund_acc, cash_out=cash, pnl=cash - entry["cash"], forced=False))
                units, fund_acc, entry = 0.0, 0.0, None
        while fi < len(fund) and fund[fi][0] < t1:      # settlements inside this hour
            ft, rate, mk = fund[fi]; fi += 1
            if units > 0 and ft >= t0 or (units > 0 and entry and ft >= entry["t"]):
                pay = -units * mk * rate; fund_acc += pay
                ledger.append(dict(settlement_utc=iso(ft), rate=rate, mark=mk, units=units, notional=units * mk, payment=pay))
        curve.append((t1, units * hb["c"] + fund_acc if units > 0 else cash))
    if units > 0 and sell_at_end:
        last = hours[-1]["c"]; sp = last * (1 - S); cash = units * sp * (1 - F) + fund_acc
        trades.append(dict(entry_t=iso(entry["t"]), entry=entry["px"], exit_t=iso(hours[-1]["t"] + 3_600_000), exit=sp, units=units,
                           cash_in=entry["cash"], funding=fund_acc, cash_out=cash, pnl=cash - entry["cash"], forced=True))
        units = 0.0; curve[-1] = (curve[-1][0], cash)
    return curve, trades, ledger, units

def monthly(curve):
    rows, months = [], sorted({iso(t - 1)[:7] for t, _ in curve})
    prev_end, peak_all, dd_all = START_CASH, START_CASH, 0.0
    for m in months:
        pts = [v for t, v in curve if iso(t - 1)[:7] == m]
        peak, dd = prev_end, 0.0
        for v in pts:
            peak = max(peak, v); dd = max(dd, 1 - v / peak)
            peak_all = max(peak_all, v); dd_all = max(dd_all, 1 - v / peak_all)
        rows.append((m, 100 * (pts[-1] / prev_end - 1), 100 * dd, pts[-1])); prev_end = pts[-1]
    return rows, 100 * dd_all

theirs = {"2026-01": (-5.39, 13.31, 9.46), "2026-02": (-19.72, 29.04, 7.59), "2026-03": (30.62, 13.47, 9.92), "2026-04": (46.99, 12.35, 14.58),
          "2026-05": (84.26, 16.19, 26.87), "2026-06": (13.37, 18.09, 30.46), "2026-07": (5.34, 11.93, 32.09), "2026-08": (54.14, 10.61, 49.47),
          "2026-09": (22.57, 20.90, 60.63)}
curve, trades, ledger, _ = run(ms("2026-10-01T00:00"), sell_at_end=True)
rows, dd_all = monthly(curve)
print(f"{'month':8s} {'ours ret':>9s} {'theirs':>8s} | {'ours DD':>7s} {'theirs':>6s} | {'ours bal':>8s} {'theirs':>7s}")
for m, r, dd, bal in rows:
    t = theirs.get(m, (None,) * 3)
    print(f"{m:8s} {r:+8.2f}% {t[0]:+7.2f}% | {dd:6.2f}% {t[1]:5.2f}% | ${bal:7.2f} ${t[2]:6.2f}")
closed = [x for x in trades if not x["forced"]]
print(f"Jan-Sep: {100 * (curve[-1][1] / START_CASH - 1):+.2f}% (theirs +506.31%), trades {len(trades)} incl. final sale ({len(closed)} closed by signal; theirs 54), "
      f"max DD {dd_all:.2f}% (theirs 36.00%), funding net {sum(x['payment'] for x in ledger):+.4f} USD on $10")
with open(f"{out_dir}/trades_jan_sep.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(trades[0])); w.writeheader(); w.writerows(trades)
# October month-to-date: the account continues (no sale on Sep 30), marked at the last completed hour
end = h1[-1]["t"] + 3_600_000
curve2, trades2, ledger2, open_units = run(end, sell_at_end=False)
rows2, dd2 = monthly(curve2)
oct_ = [r for r in rows2 if r[0] == "2026-10"]
sep_end = [r for r in rows2 if r[0] == "2026-09"][0][3]
if oct_:
    m, r, dd, bal = oct_[0]
    print(f"October to {iso(end)} UTC (account continues, open position {'yes' if open_units else 'no'}): {r:+.2f}%, monthly DD {dd:.2f}%, "
          f"balance ${bal:.2f} (Sep-end mark ${sep_end:.2f}); Jan-Oct MTD {100 * (bal / START_CASH - 1):+.2f}%, max DD {dd2:.2f}%")
with open(f"{out_dir}/funding_ledger_2026.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(ledger2[0])); w.writeheader(); w.writerows(ledger2)
