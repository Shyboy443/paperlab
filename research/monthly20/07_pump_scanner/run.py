"""Agent 07 - top-gainer / top-loser scanner across all Binance USDT perps (1h bars).

    python run.py dev     # event study + DEV grid + leverage sweep  -> dev_results.json
    python run.py test    # the pre-registered attempts on TEST (+ doubled costs) -> results.json

Data: 1h klines + funding for every perp trading today, from data.binance.vision (fetch_vision.py).
"""
import sys, json
from lib import *

LIQ = 200_000  # mean 1h quote volume over the prior 30 days (~$5M/day) - keeps out the dust
liq = lambda d: np.nan_to_num(d["basev"]) >= LIQ


def fam_long_mom(r4, trail, hold):
    return dict(name=f"LONG_MOM r4>={r4:.0%} newhigh30d trail{trail:.0%} hold{hold}h", side="long",
                sig=lambda d: (d["r4"] >= r4) & (d["c"] > d["prevhi"]) & liq(d),
                rank=lambda d: d["r4"], stop=trail, trail=trail, tp=None, hold=hold, k=5, lev=1)


def fam_short_exh(stop, tp, hold):
    return dict(name=f"SHORT_EXH r24>=50% c<hi24*0.9 stop{stop:.0%} tp{tp:.0%} hold{hold}h", side="short",
                sig=lambda d: (d["r24"] >= 0.5) & (d["c"] < d["hi24"] * 0.9) & liq(d),
                rank=lambda d: d["r24"], stop=stop, trail=None, tp=tp, hold=hold, k=5, lev=1)


def fam_short_fade(stop, hold):
    return dict(name=f"SHORT_FADE r24>=30% stop{stop:.0%} hold{hold}h", side="short",
                sig=lambda d: (d["r24"] >= 0.3) & liq(d), rank=lambda d: d["r24"],
                stop=stop, trail=None, tp=None, hold=hold, k=5, lev=1)


def fam_dump_bounce(stop, hold):
    return dict(name=f"LONG_BOUNCE r24<=-20% stop{stop:.0%} hold{hold}h", side="long",
                sig=lambda d: (d["r24"] <= -0.2) & liq(d), rank=lambda d: -d["r24"],
                stop=stop, trail=None, tp=None, hold=hold, k=5, lev=1)


def grid():
    g = []
    for r4 in (0.10, 0.15):
        for trail in (0.05, 0.10, 0.15):
            for hold in (72, 168):
                g.append(fam_long_mom(r4, trail, hold))
    for stop in (0.10, 0.20):
        for tp in (0.10, 0.20):
            g.append(fam_short_exh(stop, tp, 72))
    for stop in (0.15, 0.30):
        for hold in (24, 72):
            g.append(fam_short_fade(stop, hold))
    for stop in (0.10, 0.20):
        for hold in (24, 48):
            g.append(fam_dump_bounce(stop, hold))
    return g


def brief(s):
    return {k: s.get(k) for k in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "net_pct",
                                  "trades", "win_rate", "biggest_trade_share", "exits", "capacity_capped_entries")}


H1 = utc(2023, 1, 1)


def dev(delisted=True):
    """DEV grid. delisted=False reproduces the first pass on today's symbol list (dev_results_today_list.json)."""
    data = load_all(delisted=delisted)
    print("symbols", len(data), flush=True)
    out = {"symbols": len(data), "delisted_included": delisted, "grid": []}
    for cfg in grid():
        s, _ = simulate(data, cfg, DEV_START, DEV_END)
        a, _ = simulate(data, cfg, DEV_START, H1)
        b, _ = simulate(data, cfg, H1, DEV_END)
        row = dict(name=cfg["name"], dev=brief(s), dev_2021_22_geo=a["geo_month"], dev_2023_24_geo=b["geo_month"])
        out["grid"].append(row)
        print(json.dumps(row), flush=True)
    json.dump(out, open("dev_results.json" if delisted else "dev_results_today_list.json", "w"), indent=1)




# ---------------------------------------------------------------- leverage sweep (DEV only) + pre-registered TEST
def with_lev(cfg, lev):
    c = dict(cfg); c["lev"] = lev; c["name"] = cfg["name"] + f" lev{lev}"
    return c


LEVS = (1, 2, 3, 5, 10)


def dev_lev(names):
    data = load_all()
    out = json.load(open("dev_results.json"))
    out["lev_sweep"] = {}
    for cfg in grid():
        if cfg["name"] not in names:
            continue
        rows = []
        for lev in LEVS:
            s, _ = simulate(data, with_lev(cfg, lev), DEV_START, DEV_END)
            rows.append(dict(lev=lev, **brief(s), wiped=bool(s["net_pct"] <= -99.0)))
            print(cfg["name"], lev, json.dumps(rows[-1]), flush=True)
        out["lev_sweep"][cfg["name"]] = rows
    json.dump(out, open("dev_results.json", "w"), indent=1)


# ---------------------------------------------------------------- the pre-registered TEST run (see PREREG.md)
def prereg():
    g = {c["name"]: c for c in grid()}
    mom = g["LONG_MOM r4>=15% newhigh30d trail15% hold72h"]
    return [("A1 LONG_MOM lev1", with_lev(mom, 1)), ("A2 LONG_MOM lev3", with_lev(mom, 3)),
            ("A3 LONG_BOUNCE lev1", with_lev(g["LONG_BOUNCE r24<=-20% stop20% hold24h"], 1)),
            ("A4 SHORT_EXH lev1", with_lev(g["SHORT_EXH r24>=50% c<hi24*0.9 stop20% tp20% hold72h"], 1))]


def test():
    data = load_all()
    attempts = []
    for name, cfg in prereg():
        row = {"name": name, "prereg": True, "rule": cfg["name"]}
        for key, (a, b, cm) in {"dev": (DEV_START, DEV_END, 1), "test": (TEST_START, TEST_END, 1),
                                "test_double_cost": (TEST_START, TEST_END, 2)}.items():
            s, tr = simulate(data, cfg, a, b, cost_mult=cm)
            s["wiped"] = bool(s["net_pct"] <= -99.0)
            if key == "test":
                tr.sort(key=lambda x: -x[4])
                s["top5_trades"] = tr[:5]
                s["worst5_trades"] = tr[-5:]
            row[key] = s
            print(name, key, json.dumps(brief(s)), flush=True)
        attempts.append(row)

    def passes(r):
        t, t2 = r["test"], r["test_double_cost"]
        return (t["mean_month"] >= 20 and t2["mean_month"] > 0 and not t["wiped"]
                and (t["biggest_trade_share"] is not None and t["biggest_trade_share"] <= 50))
    best = max(attempts[:1] + attempts[2:], key=lambda r: r["test"]["geo_month"])
    out = {"agent": "07_pump_scanner",
           "idea": "Top-gainer / top-loser scanner over all Binance USDT perps incl. delisted ones (1h): ride 4h pumps "
                   "to new 30-day highs with a 15% trailing stop, short blow-off exhaustion, or buy 24h dumps.",
           "attempts": attempts, "best_honest": best["name"],
           "verdict": "PASS" if any(passes(r) for r in attempts) else "FAIL",
           "leverage_needed_for_20pct": 3,
           "leverage_note": "On DEV, A1 reaches a +20% ARITHMETIC mean month at 3x, but its geometric month is -0.2% "
                            "and max DD 98.7%; 5x and 10x are wiped out on DEV. No leverage gives +20%/month compounded.",
           "dev_grid_file": "dev_results.json", "dev_grid_today_list_file": "dev_results_today_list.json"}
    json.dump(out, open("results.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    {"dev": dev, "dev_today": lambda: dev(False), "dev_lev": lambda: dev_lev(sys.argv[2:]),
     "test": lambda: test()}[sys.argv[1]]()
