"""DEV-only exploration of time and event effects. Prints mean hourly/period returns in basis points with t-stats,
for DEV-A (2021-2022) and DEV-B (2023-01..2024-10) separately, so an effect has to show up in both halves."""
from base import *

frames = {s: frame(s) for s in SYMS}
R = pd.DataFrame({s: f.r for s, f in frames.items()})
R = R[(R.index >= pd.Timestamp(DEV_START, unit="ms", tz="UTC")) & (R.index < pd.Timestamp(DEV_END, unit="ms", tz="UTC"))]
C = calendar(R.index)
grp = {"BTC": R["BTCUSDT"], "ETH": R["ETHUSDT"], "ALTS": R[ALTS].mean(axis=1)}
half = np.where(R.index < pd.Timestamp("2023-01-01", tz="UTC"), "A", "B")


def tab(mask_or_key, label, horizon=1):
    out = []
    for g, s in grp.items():
        for h in "AB":
            x = s[(half == h)]
            m = mask_or_key[(half == h)] if not isinstance(mask_or_key, str) else None
            v = x[m].dropna()
            out.append(f"{g}{h} {v.mean()*1e4:6.1f}bp t{v.mean()/v.std()*np.sqrt(len(v)):5.1f} n{len(v)}")
    print(f"{label:28s}", " | ".join(out))


print("== hour of day (UTC), mean 1h return")
for hh in range(24):
    tab(C.hour == hh, f"hour {hh:02d}")
print("== day of week, mean hourly return x24")
for d in range(7):
    tab(C.dow == d, f"dow {d}")
tab(C.dow >= 5, "weekend")
tab((C.dom >= C.dim) | (C.dom <= 3), "turn of month (last+first3)")
print("== funding stamps")
for off in (-2, -1, 0, 1):
    tab(((C.hour - off) % 8 == 0), f"funding hour {off:+d}")
print("== US session")
tab(C.us_open_bar, "NY 09:00 bar (open)")
tab(C.ny_hour == 15, "NY 15:00 bar (close)")
tab(C.us_session, "US session 10-16")
tab(~C.us_session & (C.dow < 5), "weekday off-session")
print("== macro")
for name in ("cpi", "fomc"):
    for lo, hi in [(1, 24), (1, 4), (25, 72)]:
        tab((C[f"{name}_to"] >= lo) & (C[f"{name}_to"] <= hi), f"{name} pre {lo}-{hi}h")
    for lo, hi in [(0, 0), (0, 3), (1, 24), (25, 72)]:
        tab((C[f"{name}_since"] >= lo) & (C[f"{name}_since"] <= hi), f"{name} post {lo}-{hi}h")
print("== options expiry")
tab(C.friday & (C.hour < 8), "Fri 00-08 pre-expiry")
tab(C.friday & (C.hour >= 8), "Fri 08-24 post-expiry")
tab(C.last_friday & (C.hour < 8), "last Fri pre")

print("== CME weekend gap (BTC price Sun 18:00 NY open vs Fri 17:00 NY close) -> forward returns from Sun open")
o = {s: frames[s].o for s in SYMS}
ny = R.index.tz_convert(NY)
fri = R.index[(ny.dayofweek == 4) & (ny.hour == 17)]
rows = []
for t in fri:
    sun = t + pd.Timedelta(hours=49)
    if sun not in o["BTCUSDT"].index:
        continue
    g = o["BTCUSDT"][sun] / o["BTCUSDT"][t] - 1
    fw = {}
    for H in (6, 24, 72):
        e = sun + pd.Timedelta(hours=H)
        fw[H] = {k: (o[s].get(e, np.nan) / o[s][sun] - 1) for k, s in [("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")]}
        fw[H]["ALTS"] = np.nanmean([o[s].get(e, np.nan) / o[s].get(sun, np.nan) - 1 for s in ALTS])
    rows.append((t, g, fw))
for H in (6, 24, 72):
    for k in ("BTC", "ETH", "ALTS"):
        for h, lo, hi in [("A", "2021", "2023"), ("B", "2023", "2025")]:
            sel = [(g, fw[H][k]) for t, g, fw in rows if lo <= str(t.year) < hi and abs(g) > 0.01]
            if not sel:
                continue
            x = np.array([-np.sign(g) * f for g, f in sel])  # gap-fade return
            print(f"gap>1% fade H{H:2d} {k:4s}{h}: mean {x.mean()*1e4:6.1f}bp t{x.mean()/x.std()*np.sqrt(len(x)):5.1f} n{len(x)}")
