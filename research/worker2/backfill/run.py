"""Worker 2 rule P out-of-sample backfill 2018-01..2020-12 on Binance SPOT (perp proxy, no funding).
Usage: python -I run.py need1h   -> writes data/w2_backfill/need_1h.json (symbols ever in the spot top-40)
       python -I run.py          -> full run, writes results.json next to this file"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/backfill")
import w2bf as W

HERE = Path(r"D:/Projects/BinanceBOt/paperlab/research/worker2/backfill")
PERP_RES = Path(r"D:/Projects/BinanceBOt/paperlab/research/worker2/session/results.json")

if len(sys.argv) > 1 and sys.argv[1] == "need1h":
    Pn = W.Panel(need_1h=False)
    (W.CACHE / "need_1h.json").write_text(json.dumps(Pn.syms))
    print(len(Pn.syms), "symbols ever in top-40")
    sys.exit()

PN = W.Panel()
print("missing 1h:", PN.missing_1h, flush=True)
d18, d21, d21b = PN.dayidx(W.utc(2018, 1, 1)), PN.dayidx(W.utc(2021, 1, 1)), PN.dayidx(W.utc(2021, 7, 1))
P = W.P
R = {"rule": P, "data": "Binance SPOT 1h klines, data.binance.vision monthly archive (incl. delisted pairs)",
     "funding": "ignored (0) - spot has no funding; shorts are synthetic perp-proxy shorts",
     "symbols_listed_usdt": len(json.loads((W.CACHE / "list.json").read_text())),
     "symbols_ever_top40": len(PN.syms), "missing_1h": PN.missing_1h}


def window(a, b, cost_mult=1.0, side=None):
    rows = PN.signals(P, a, b)
    if side is not None:
        rows = [r for r in rows if r[4] == side]
    c, tr = PN.run(rows, a, b, P["K"], cost_mult=cost_mult)
    m = {k: round(v, 2) for k, v in W.monthly_returns(c).items()}
    return c, tr, m


def tpm(tr, months):
    cnt = {}
    for t in tr:
        k = W.datetime.fromtimestamp(PN.td[t[0]] / 1000, W.timezone.utc).strftime("%Y-%m")
        cnt[k] = cnt.get(k, 0) + 1
    return {k: cnt.get(k, 0) for k in months}


# --- sanity: spot pipeline on 2021-01..2021-06 vs the perp monthly numbers
perp_m = json.loads(PERP_RES.read_text())["primary"]["monthly_1x"]
c, tr, m = window(d21, d21b)
R["sanity_2021H1"] = {"spot": m, "perp": {k: perp_m[k] for k in m},
                      "diff_pp": {k: round(m[k] - perp_m[k], 2) for k in m},
                      "spot_compounded": round((np.prod([1 + v / 100 for v in m.values()]) - 1) * 100, 2),
                      "perp_compounded": round((np.prod([1 + perp_m[k] / 100 for k in m]) - 1) * 100, 2),
                      "spot_trades": len(tr)}
print("sanity", R["sanity_2021H1"], flush=True)

# --- main backfill 2018-01..2020-12
out = {}
for tag, cm, side in (("1x", 1, None), ("2x_cost", 2, None), ("long_only", 1, 1.0), ("short_only", 1, -1.0)):
    c, tr, m = window(d18, d21, cm, side)
    out[tag] = {"stats": W.mstats(m, W.max_dd(c), len(tr)), "years": W.yearly(m), "monthly": m,
                "trades_per_month": tpm(tr, m),
                "win_rate": round(float(np.mean([t[3] > 0 for t in tr]) * 100), 1) if tr else None,
                "longs": int(sum(t[2] > 0 for t in tr)), "shorts": int(sum(t[2] < 0 for t in tr))}
    if tag == "1x":
        curve18 = c
        pc = {}
        for t in tr:
            pc[PN.syms[t[1]]] = pc.get(PN.syms[t[1]], 0) + t[3]
        out[tag]["top_coins_pnl_usd"] = {k: round(v, 0) for k, v in sorted(pc.items(), key=lambda x: -x[1])[:8]}
        out[tag]["bottom_coins_pnl_usd"] = {k: round(v, 0) for k, v in sorted(pc.items(), key=lambda x: x[1])[:5]}
    print(tag, out[tag]["stats"], out[tag]["years"], flush=True)
R["backfill_2018_2020"] = out

# universe size over time (alts eligible = top-40 minus BTC/ETH), monthly average
us = {}
for d in range(d18, d21):
    k = W.datetime.fromtimestamp(PN.td[d] / 1000, W.timezone.utc).strftime("%Y-%m")
    us.setdefault(k, []).append(len(PN.ALT_ELIG[d]))
R["universe_size_monthly_avg"] = {k: round(float(np.mean(v)), 1) for k, v in us.items()}

# --- join with the perp series 2021-01..2026-10
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
try:
    import core as PC
    pp = dict(PC.SEL, sig_ref=0.01154)
    pcurve, ptr, _, _ = PC.sim(pp, PC.d0, PC.d1)
    pm = {k: round(v, 2) for k, v in W.monthly_returns(pcurve).items()}
    R["perp_recompute_matches_results_json"] = bool(all(abs(pm[k] - perp_m[k]) < 0.011 for k in perp_m))
    joined_curve = list(curve18) + [(t, e * curve18[-1][1] / pcurve[0][1]) for t, e in pcurve[1:]]
    dd_join = W.max_dd(joined_curve)
    R["perp_2021_2026_max_dd_pct"] = W.max_dd(pcurve)
except Exception as e:  # fall back to month-end equity drawdown
    print("perp recompute failed:", e)
    dd_join = None
mj = dict(out["1x"]["monthly"]); mj.update(perp_m)
keys = sorted(mj)
if dd_join is None:
    eq = np.cumprod([1 + mj[k] / 100 for k in keys]); dd_join = round(float((1 - eq / np.maximum.accumulate(eq)).max() * 100), 2)
J = {"stats": W.mstats(mj, dd_join), "years": W.yearly(mj)}
v = np.array([mj[k] for k in keys])
roll = {keys[i] + ".." + keys[i + 23]: int((v[i:i + 24] > 0).sum()) for i in range(len(v) - 23)}
J["rolling24_wins"] = roll
J["rolling24_summary"] = {"windows": len(roll), "min": min(roll.values()), "max": max(roll.values()),
                          "median": float(np.median(list(roll.values()))),
                          "windows_with_>=16_wins": sum(x >= 16 for x in roll.values()),
                          "windows_with_<12_wins": sum(x < 12 for x in roll.values())}
J["note"] = "2018-01..2020-12 spot backfill chained with perp 2021-01..2026-10 (results.json primary.monthly_1x; 2026-10 partial to 10-21)"
R["joined_2018_2026"] = J
print("joined", J["stats"], J["rolling24_summary"], flush=True)
(HERE / "results.json").write_text(json.dumps(R, indent=1))
