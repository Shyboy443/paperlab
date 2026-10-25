"""Agent 05 - what Binance USDT perps (and spot pairs) do after they are newly listed.

Data: fetch.py pulls the first ~4 months of 1h bars + funding for EVERY USDT-M perp / USDT spot pair in the public
archive data.binance.vision, INCLUDING DELISTED ONES (survivorship-free). Listings after the last archived month are
topped up from common.klines/common.funding (only coins still trading; there are a handful).

    python fetch.py            # once, downloads the archive slices
    python run.py dev          # descriptive study + DEV grid (design only)
    python run.py test         # the single pre-registered TEST run (after PREREG.md) + diagnostics
    python run.py spot         # descriptive spot-listing study
    python run.py final        # assembles results.json
"""
from __future__ import annotations
import json, math, statistics, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import (DEV_START, DEV_END, TEST_START, TEST_END, FEE, SLIP, MAINT_MARGIN, HOUR, DAY,
                    perp_symbols, klines, funding, summary, utc)

HERE = Path(__file__).resolve().parent
DATA = Path(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_05_new_listings")
HORIZ = [1, 3, 7, 14, 30, 60, 90]


def d(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- data
def load(venue="perp"):
    live = {s["symbol"]: s["onboard"] for s in perp_symbols()} if venue == "perp" else None
    out = []
    for p in sorted((DATA / venue).glob("*.json")):
        x = json.loads(p.read_text())
        bars = x["bars"]
        if not bars or bars[0][0] < DEV_START:
            continue
        x["listed"] = bars[0][0]
        x["alive"] = (x["symbol"] in live) if live is not None else None
        out.append(x)
    if venue == "perp":  # top up listings newer than the archive (still trading by construction)
        have = {x["symbol"]: x for x in out}
        for sym, onboard in live.items():
            if onboard < utc(2026, 7, 1) or onboard >= TEST_END:
                continue
            x = have.get(sym)
            if x and x["bars"][-1][0] - x["bars"][0][0] > 95 * DAY:
                continue
            kb = klines(sym, "1h")
            if not kb:
                continue
            fr = funding(sym)
            y = {"symbol": sym, "bars": [r[:7] for r in kb[:24 * 125]], "funding": [list(f) for f in fr],
                 "listed": kb[0][0], "alive": True, "topped_up": True}
            if x:
                out[out.index(x)] = y
            else:
                out.append(y)
    # classify with one full exchangeInfo snapshot (fetched 2026-10-23, includes SETTLING = delisted and TradFi)
    xi = {r["symbol"]: r for r in json.loads((DATA / "exchangeinfo_all.json").read_text())} if venue == "perp" else {}
    for x in out:
        r = xi.get(x["symbol"])
        if venue != "perp":
            x["kind"] = "spot"
        elif r and r["contractType"] == "TRADIFI_PERPETUAL":
            x["kind"] = "tradfi"
        elif x["alive"]:
            x["kind"] = "crypto_live"
        else:
            x["kind"] = "crypto_delisted"
    # one listing per base: drop a symbol whose bars start within 3 days of the end of its archive (empty shells)
    return [x for x in out if len(x["bars"]) >= 24]


# ---------------------------------------------------------------- descriptive study
def describe(rows, start, end, delay=1):
    res = {}
    sel = [x for x in rows if start <= x["listed"] < end]
    for grp, g in [("all", sel)] + [(k, [x for x in sel if x.get("kind") == k])
                                    for k in ("crypto_live", "crypto_delisted", "tradfi")]:
        if not g:
            continue
        r = {"n": len(g)}
        for h in HORIZ:
            vals, mfe = [], []
            for x in g:
                b = x["bars"]
                if len(b) <= delay + h * 24:
                    continue
                p0 = b[delay][1]
                p1 = b[delay + h * 24][1]
                vals.append((p1 / p0 - 1) * 100)
                mfe.append((max(z[2] for z in b[delay:delay + h * 24]) / p0 - 1) * 100)
            if vals:
                r[f"{h}d"] = {"n": len(vals), "mean_ret": round(statistics.mean(vals), 2),
                              "median_ret": round(statistics.median(vals), 2),
                              "share_down": round(sum(v < 0 for v in vals) / len(vals) * 100, 1),
                              "median_max_rise": round(statistics.median(mfe), 1),
                              "share_rise_over_50pct": round(sum(m > 50 for m in mfe) / len(mfe) * 100, 1),
                              "share_rise_over_100pct": round(sum(m > 100 for m in mfe) / len(mfe) * 100, 1)}
        fr = [f[1] for x in g for f in x.get("funding", []) if x["listed"] <= f[0] < x["listed"] + 30 * DAY]
        if fr:
            r["funding_first30d_mean_per_stamp_pct"] = round(statistics.mean(fr) * 100, 4)
        res[grp] = r
    return res


# ---------------------------------------------------------------- portfolio simulation
def simulate(rows, p, start, end, cost_mult=1.0):
    """side=-1 short / +1 long. Each listing in [start,end) is a candidate; entry at the open of bar `delay`
    (hours after the first bar). Optional condition (bounce longs): only enter if price at entry is <= `cond_dd`
    below the listing price. Hold `hold_d` days, exit at that bar's open. Isolated margin = equity/slots,
    notional = margin*lev, capped at 1% of the entry bar quote volume. Liquidation at loss = 1/lev - 0.5%.
    Optional stop at `stop` (fractional adverse move), filled at the worse of the stop and the bar open."""
    side, delay, hold, lev, slots = p["side"], p["delay_h"], p["hold_d"] * 24, p["lev"], p["slots"]
    stop, cond = p.get("stop"), p.get("cond_dd")
    cost = (FEE + SLIP) * cost_mult
    events = {}  # hour -> list of entries
    for x in rows:
        b = x["bars"]
        if not (start <= x["listed"] < end) or len(b) <= delay + 1:
            continue
        if cond is not None and not (b[delay][1] <= b[0][1] * (1 - cond)):
            continue
        events.setdefault(b[delay][0], []).append(x)
    cash, open_, curve, pnls = 1.0, [], [], []
    liq = stops = capped = 0
    t = start
    index = {}

    def close(pos, price, why):
        nonlocal cash
        gross = side * pos["qty"] * (price - pos["entry"])
        fee = pos["qty"] * price * cost
        pl = max(gross - fee, -pos["margin"]) if why != "liq" else -pos["margin"]
        cash += pl + pos["fund"]
        pnls.append(pl + pos["fund"] - pos["fee_in"])

    while t < end:
        still = []
        for pos in open_:
            bar = pos["bars"].get(t)
            if bar is None:
                if t >= pos["last_t"]:  # delisted / data ends: settle at the last close
                    close(pos, pos["last_close"], "end")
                    continue
                still.append(pos)
                continue
            if t >= pos["exit_t"]:
                close(pos, bar[1], "exit")
                continue
            o, h, l, c = bar[1], bar[2], bar[3], bar[4]
            adverse = h if side < 0 else l
            liq_px = pos["entry"] * (1 - side * (1 / lev - MAINT_MARGIN))
            stop_px = pos["entry"] * (1 - side * stop) if stop else None
            if stop_px is not None and (adverse - stop_px) * -side >= 0 and (stop_px - liq_px) * side > 0:
                close(pos, max(stop_px, o) if side < 0 else min(stop_px, o), "stop")
                stops += 1
                continue
            if (adverse - liq_px) * -side >= 0:
                close(pos, liq_px, "liq")
                liq += 1
                continue
            for ft, rate in pos["funding"].get(t, []):
                pos["fund"] += -side * rate * pos["qty"] * o  # long pays positive funding, short receives
            pos["last_px"] = c
            still.append(pos)
        open_ = still
        for x in events.get(t, []):
            if len(open_) >= slots:
                continue
            eq = cash + sum(side * q["qty"] * (q["last_px"] - q["entry"]) + q["fund"] for q in open_)
            if eq <= 0:
                break
            b = x["bars"]
            entry = b[delay][1] * (1 - side * 0)  # slippage is part of `cost`
            margin = eq / slots
            notional = margin * lev
            capv = 0.01 * b[delay][6]
            if notional > capv:
                notional, capped = capv, capped + 1
                margin = notional / lev
            qty = notional / entry
            fee_in = notional * cost
            cash -= fee_in
            bars = {r[0]: r for r in b[delay:]}
            fund = {}
            for ft, rate in x.get("funding", []):
                fund.setdefault(ft // HOUR * HOUR, []).append((ft, rate))
            exit_idx = delay + hold
            pos = {"sym": x["symbol"], "entry": entry, "qty": qty, "margin": margin, "fee_in": fee_in, "fund": 0.0,
                   "bars": bars, "funding": fund, "last_px": entry,
                   "exit_t": b[exit_idx][0] if exit_idx < len(b) else b[-1][0] + HOUR,
                   "last_t": b[-1][0] + HOUR, "last_close": b[-1][4]}
            # the entry bar itself: check intrabar risk
            o, h, l, c = b[delay][1:5]
            liq_px = entry * (1 - side * (1 / lev - MAINT_MARGIN))
            if (h if side < 0 else l) * -side >= liq_px * -side:
                close(pos, liq_px, "liq")
                liq += 1
                continue
            pos["last_px"] = c
            open_.append(pos)
        eq = cash + sum(side * q["qty"] * (q["last_px"] - q["entry"]) + q["fund"] for q in open_)
        curve.append((t, max(eq, 1e-9)))
        if eq <= 0:
            curve.append((end - HOUR, 1e-9))
            break
        t += HOUR
    for pos in open_:  # window end: mark out at the last price
        close(pos, pos["last_px"], "end")
    s = summary(curve, pnls)
    s.update({"liquidations": liq, "stops": stops, "volume_capped": capped,
              "max_single_trade_share_of_profit": round(max(pnls) / sum(pnls) * 100, 1) if pnls and sum(pnls) > 0 else None})
    return s


def grid():
    out = []
    for delay in (1, 24, 72):
        for hold in (7, 14, 30, 60):
            for stop in (None, 0.5):
                for lev in (1, 2, 3):
                    for slots in (5, 20):
                        out.append({"name": f"SHORT_d{delay}h_h{hold}d_s{stop}_L{lev}_k{slots}", "side": -1,
                                    "delay_h": delay, "hold_d": hold, "stop": stop, "lev": lev, "slots": slots})
    for hold in (3, 7):  # second DEV pass: tighter stops with more leverage / concentration
        for stop in (0.15, 0.25, 0.35):
            for lev in (2, 3, 5):
                for slots in (3, 5, 10):
                    out.append({"name": f"SHORT2_d1h_h{hold}d_s{stop}_L{lev}_k{slots}", "side": -1, "delay_h": 1,
                                "hold_d": hold, "stop": stop, "lev": lev, "slots": slots})
    for delay_d in (14, 30):
        for cond in (0.3, 0.5):
            for hold in (14, 30):
                out.append({"name": f"BOUNCE_d{delay_d}d_dd{cond}_h{hold}d_L1", "side": 1, "delay_h": delay_d * 24,
                            "hold_d": hold, "cond_dd": cond, "lev": 1, "slots": 20})
    for hold in (1, 7, 30):
        out.append({"name": f"LONG_d1h_h{hold}d_L1", "side": 1, "delay_h": 1, "hold_d": hold, "lev": 1, "slots": 20})
    return out


def brief(s):
    return {k: s.get(k) for k in ("mean_month", "median_month", "worst_month", "max_dd_pct", "net_pct", "trades",
                                  "liquidations", "top3_share_of_profit")}


def main(mode):
    perp = load("perp")
    if mode == "spot":
        spot = load("spot")
        out = {"n_spot_listings": len(spot), "describe_spot_dev": describe(spot, DEV_START, DEV_END),
               "describe_spot_test": describe(spot, TEST_START, TEST_END)}
        (HERE / "results_spot.json").write_text(json.dumps(out, indent=1))
        print(json.dumps(out, indent=1))
        return
    res_path = HERE / "results_dev.json"
    if mode == "dev":
        out = {"n_perp_listings_dev": sum(DEV_START <= x["listed"] < DEV_END for x in perp),
               "n_perp_listings_test": sum(TEST_START <= x["listed"] < TEST_END for x in perp),
               "describe_perp_dev": describe(perp, DEV_START, DEV_END),
               "grid": {}}
        only = sys.argv[2] if len(sys.argv) > 2 else ""
        if only and res_path.exists():
            out = json.loads(res_path.read_text())
        for p in grid():
            if only and not p["name"].startswith(only):
                continue
            s = simulate(perp, p, DEV_START, DEV_END)
            out["grid"][p["name"]] = brief(s)
            print(p["name"], brief(s), flush=True)
        # survivorship on DEV: the same rule on survivors only
        res_path.write_text(json.dumps(out, indent=1))
        return
    if mode == "test":
        pre = json.loads((HERE / "prereg.json").read_text())
        attempts = []
        alive = [x for x in perp if x["alive"]]
        for p in pre["attempts"]:
            a = {"name": p["name"], "prereg": True, "params": p,
                 "dev": simulate(perp, p, DEV_START, DEV_END),
                 "test": simulate(perp, p, TEST_START, TEST_END),
                 "test_double_cost": simulate(perp, p, TEST_START, TEST_END, 2.0),
                 "test_survivors_only": brief(simulate(alive, p, TEST_START, TEST_END)),
                 "dev_survivors_only": brief(simulate(alive, p, DEV_START, DEV_END))}
            attempts.append(a)
            print(p["name"], "TEST", brief(a["test"]), flush=True)
        # diagnostics (NOT pre-registered): leverage sweep of the primary rule on TEST
        prim = pre["attempts"][0]
        sweep = {}
        for lev in (1, 2, 3, 5, 10):
            q = dict(prim, lev=lev, name=f"{prim['name']}_lev{lev}")
            sweep[lev] = {"dev": brief(simulate(perp, q, DEV_START, DEV_END)),
                          "test": brief(simulate(perp, q, TEST_START, TEST_END))}
            print("sweep", lev, sweep[lev], flush=True)
        desc = {"perp_test": describe(perp, TEST_START, TEST_END)}
        # POST-TEST attempts (NOT pre-registered): the archive turned out to contain TradFi perps (stocks, ETFs,
        # metals, pre-IPO) that the prereg universe rule did not exclude. Same rules, crypto listings only.
        crypto = [x for x in perp if x["kind"] != "tradfi"]
        for p in pre["attempts"]:
            q = dict(p, name=p["name"] + "_CRYPTO_ONLY_postTEST")
            a = {"name": q["name"], "prereg": False, "params": q,
                 "dev": simulate(crypto, q, DEV_START, DEV_END),
                 "test": simulate(crypto, q, TEST_START, TEST_END),
                 "test_double_cost": simulate(crypto, q, TEST_START, TEST_END, 2.0),
                 "test_survivors_only": brief(simulate([x for x in crypto if x["alive"]], q, TEST_START, TEST_END))}
            attempts.append(a)
            print(q["name"], "TEST", brief(a["test"]), flush=True)
        desc["perp_dev"] = describe(perp, DEV_START, DEV_END)
        (HERE / "results_test_raw.json").write_text(json.dumps({"attempts": attempts, "lev_sweep": sweep,
                                                                "describe": desc}, indent=1))


def final():
    raw = json.loads((HERE / "results_test_raw.json").read_text())
    dev = json.loads((HERE / "results_dev.json").read_text())
    spot = json.loads((HERE / "results_spot.json").read_text())
    out = {"agent": "05_new_listings",
           "idea": "Short newly listed Binance USDT-M perps 1 h after listing and hold 7 days (survivorship-free "
                   "universe from data.binance.vision incl. delisted symbols); also tested bounce longs, plain longs.",
           "attempts": [{k: a[k] for k in ("name", "prereg", "params", "dev", "test", "test_double_cost",
                                           "test_survivors_only")} for a in raw["attempts"]],
           "best_honest": "A_SHORT_7d_L1_k5_stop50",
           "verdict": "FAIL",
           "leverage_needed_for_20pct": None,
           "leverage_note": "No leverage reaches a GEOMETRIC +20%/month on DEV or TEST. The arithmetic mean month "
                            "crosses 20% at ~2x (B) but compounding is then ~0%/month with 90% drawdown; at 3x+ the "
                            "book is effectively wiped (TEST 3x -82%, 5x -99.9%, 10x -100%).",
           "lev_sweep_A_diagnostic": raw["lev_sweep"],
           "describe": raw["describe"], "describe_spot": spot,
           "dev_grid_count": len(dev["grid"]), "dev_grid": dev["grid"]}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "final":
        final()
        sys.exit()
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
