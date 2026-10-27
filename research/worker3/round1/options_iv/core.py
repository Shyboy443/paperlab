"""Options-IV timing of BTC/ETH USDT perps. Daily decision from Deribit DVOL (close of UTC day d, i.e. 00:00 of d+1)
and Binance perp closes; executed at the 01:00 UTC open of day d+1 (one hour after the DVOL close, no look-ahead);
held until the 01:00 open of the next day. Costs fee+slip per side on traded notional (incl. drift), funding paid/received.
"""
import json, sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, DEV_START, DEV_END, TEST_START, TEST_END, DAY, HOUR, PROJECT, summary, monthly_returns

V07 = PROJECT / "data" / "monthly20_07_pump_scanner"
DV = PROJECT / "data" / "w3r1_options_iv"
D0 = 1606780800000 + 31 * DAY  # 2021-01-01
COINS = ("BTC", "ETH")


def load():
    t = np.arange(DEV_START, TEST_END, DAY, dtype=np.int64)  # day d starts at t[d]
    T = len(t)
    out = dict(t=t)
    for c in COINS:
        h = np.array(json.load(open(V07 / "1h" / f"{c}USDT.json")), float)
        idx = {int(r[0]): r for r in h}
        close = np.full(T, np.nan); o1 = np.full(T, np.nan)
        for d in range(T):
            r = idx.get(int(t[d] + 23 * HOUR));
            if r is not None: close[d] = r[4]            # close of day d (bar 23:00-24:00)
            r = idx.get(int(t[d] + HOUR))
            if r is not None: o1[d] = r[1]               # open at 01:00 of day d
        f = np.array(json.load(open(V07 / "funding" / f"{c}USDT.json")), float)
        # funding paid in holding window [01:00 d, 01:00 d+1)
        fund = np.zeros(T)
        di = ((f[:, 0] - (t[0] + HOUR)) // DAY).astype(int)
        ok = (di >= 0) & (di < T); np.add.at(fund, di[ok], f[ok, 1])
        lastf = int((f[-1, 0] - (t[0] + HOUR)) // DAY)
        fund[lastf + 1:] = 0.0003  # after the cached funding ends: +0.01%/8h, longs pay
        dv = json.load(open(DV / f"dvol_{c}_1d.json"))
        dvc = np.full(T, np.nan)
        for r in dv:
            d = int((r[0] - t[0]) // DAY)
            if 0 <= d < T: dvc[d] = r[4]                 # DVOL close of day d (known at 00:00 d+1)
        out[c] = dict(close=close, o1=o1, fund=fund, dvol=dvc)
    return out


def ffill(x):
    x = x.copy()
    for i in range(1, len(x)):
        if np.isnan(x[i]): x[i] = x[i - 1]
    return x


def features(D, c, rv_n=30):
    cl = ffill(D[c]["close"]); dv = ffill(D[c]["dvol"])
    lr = np.r_[np.nan, np.diff(np.log(cl))]
    rv = np.full(len(cl), np.nan)
    for i in range(rv_n, len(cl)):
        rv[i] = np.std(lr[i - rv_n + 1:i + 1], ddof=1) * np.sqrt(365) * 100
    return cl, dv, rv


def zscore(x, n):
    z = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        w = x[i - n + 1:i + 1]; w = w[~np.isnan(w)]
        if len(w) > n * 0.8 and w.std() > 0: z[i] = (x[i] - w.mean()) / w.std()
    return z


def signal(D, c, p):
    """Target position in {-1,0,+1} decided at end of day d (uses data up to day d)."""
    cl, dv, rv = features(D, c, p.get("rv_n", 30))
    fam = p["fam"]
    s = np.zeros(len(cl))
    if fam == "vrp":        # implied minus realized vol, z-scored: high VRP (fear premium priced) -> long
        z = zscore(dv - rv, p["zn"])
        s[z > p["th"]] = 1; s[z < -p["th"]] = -1 if p.get("short", True) else 0
    elif fam == "ivchg":    # IV k-day change: IV falling -> long, IV spiking -> short (or flat)
        k = p["k"]; ch = np.full(len(dv), np.nan); ch[k:] = dv[k:] / dv[:-k] - 1
        s[ch < -p["th"]] = 1; s[ch > p["th"]] = -1 if p.get("short", True) else 0
        if p.get("contra"): s = -s
    elif fam == "ivlvl":    # IV level vs its own z: low IV regime -> long (calm uptrend), high -> short/flat
        z = zscore(dv, p["zn"])
        s[z < -p["th"]] = 1; s[z > p["th"]] = -1 if p.get("short", True) else 0
        if p.get("contra"): s = -s
    elif fam == "trend_iv": # price trend (close vs SMA n) gated by IV: trade trend only when IV z below gate
        n = p["n"]; sma = np.full(len(cl), np.nan)
        cs = np.cumsum(np.r_[0, cl]); sma[n - 1:] = (cs[n:] - cs[:-n]) / n
        tr = np.sign(cl - sma)
        z = zscore(dv - rv, p["zn"]) if p.get("gate_on", "vrp") == "vrp" else zscore(dv, p["zn"])
        ok = z > p["g"] if p.get("gate_on", "vrp") == "vrp" else z < p["g"]
        s = np.where(ok, tr, 0.0)
        if not p.get("short", True): s = np.maximum(s, 0)
    s[np.isnan(s)] = 0
    hold = p.get("hold", 1)
    if hold > 1:  # only update decision every `hold` days
        s2 = s.copy()
        for i in range(len(s)):
            s2[i] = s[i - i % hold]
        s = s2
    return s


def simulate(D, p, start, end, cost_mult=1.0, coins=COINS, sig=None, record=False):
    t = D["t"]; i0 = int(np.searchsorted(t, start)); i1 = int(np.searchsorted(t, end))
    cost = (FEE + SLIP) * cost_mult
    sigs = {c: (sig[c] if sig is not None else signal(D, c, p)) for c in coins}
    wt = 1.0 / len(coins)
    eq = 1.0; w = {c: 0.0 for c in coins}
    curve = [(int(t[i0] + HOUR), eq)]; rets = []; pos = []; ntr = 0
    cur = {}
    for d in range(i0, i1 - 1):
        # decision from end of day d-1 executes at 01:00 of day d, held to 01:00 of day d+1
        r_day = 0.0; tc = 0.0
        for c in coins:
            tgt = sigs[c][d - 1] * wt
            if abs(tgt - w[c]) > 1e-12:
                tc += abs(tgt - w[c]) * cost
                if np.sign(tgt) != np.sign(w[c]): ntr += (tgt != 0)
            w[c] = tgt
            o, o2 = D[c]["o1"][d], D[c]["o1"][d + 1]
            r = (o2 / o - 1) if (o > 0 and o2 > 0) else 0.0
            pr = w[c] * r - w[c] * D[c]["fund"][d]
            r_day += pr
            if record:
                key = c
                if w[c] != 0 and (key not in cur or cur[key][1] != np.sign(w[c])):
                    if key in cur and cur[key][1] != 0: pos.append(cur.pop(key))
                    cur[key] = [c, np.sign(w[c]), d, 0.0]
                if w[c] == 0 and key in cur: pos.append(cur.pop(key))
                if key in cur: cur[key][3] += pr * eq
            # drift: weight after move
            w[c] = w[c] * (1 + r) / (1 + 0) if True else w[c]
        eq_new = eq * (1 + r_day - tc)
        if record:
            for k in cur: pass
        # renormalise drifted weights to new equity
        for c in coins:
            w[c] = w[c] * eq / eq_new if eq_new > 0 else 0
        rets.append(r_day - tc); eq = eq_new
        curve.append((int(t[d + 1] + HOUR), eq))
    pos += list(cur.values())
    return dict(curve=curve, rets=np.array(rets), trades=ntr, pos=pos, i0=i0)


def brief(r):
    s = summary(r["curve"])
    m = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(m.mean() / (m.std(ddof=1) / np.sqrt(len(m)))), 2) if len(m) > 2 else None
    s["trades_per_month"] = round(r["trades"] / max(1, len(m)), 1)
    return s
