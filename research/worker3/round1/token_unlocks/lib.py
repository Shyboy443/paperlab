"""Daily engine for the unlock-short family. Survivorship-free daily panel (r2_rotation/data.py, incl. delisted perps).

Trade of event e: short sym at the OPEN of day (U - X) and cover at the OPEN of day (U + Y), U = unlock day (UTC).
Optional hedge: an equal notional long BTC over the same days. Book: each day the active events share the equity,
weight per event = min(CAP, 1/n_active) (gross <= 1x, rest in cash). Costs FEE+SLIP per side on every change of weight
(entries, exits and re-sizing), funding from the daily funding sums (a short receives positive funding)."""
import json, sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, DAY, DATA_FROM, DEV_START, DEV_END, TEST_START, TEST_END

z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_rotation/panel_1d.npz")
T = z["t"]; SYMS = [str(s) for s in z["syms"]]; J = {s: i for i, s in enumerate(SYMS)}
O, C, QV, FUND = z["o"], z["c"], z["qv"], z["fund"]
FIRST = z["first"]
ND = len(T)
BTC = J["BTCUSDT"]
D0 = DATA_FROM // DAY  # day index offset: panel day d <-> epoch day D0 + d
EVENTS = json.loads(open(r"D:/Projects/BinanceBOt/paperlab/data/w3r1_token_unlocks/events.json").read())
# daily open-to-open return of day d (open d -> open d+1)
R = np.full_like(O, np.nan); R[:-1] = O[1:] / O[:-1] - 1
QV30 = np.full_like(QV, np.nan)
_c = np.vstack([np.zeros((1, QV.shape[1])), np.nancumsum(np.nan_to_num(QV), 0)])
QV30[30:] = (_c[30:-1] - _c[:-31]) / 30  # mean daily quote volume of days d-30..d-1 (known at open of d)
MONTHS = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in T]


def candidates(min_size, max_size=9e9, X=7, Y=3, min_qv=2e6, min_age=30, cats=None):
    """events tradable at entry: price at entry and exit opens, listed >= min_age days before entry, 30d avg QV >= min_qv.
    Only information known at entry is used (schedule size from the published vesting schedule, trailing volume)."""
    out = []
    for e in EVENTS:
        if e["size"] < min_size or e["size"] >= max_size:
            continue
        if cats and not set(e["cats"]) & set(cats):
            continue
        j = J.get(e["sym"]);
        u = e["day"] - D0
        a, b = u - X, u + Y
        if j is None or a < min_age + 1 or b >= ND:
            continue
        if not (np.isfinite(O[a, j]) and np.isfinite(O[b, j])):
            continue
        if not np.isfinite(O[a - min_age, j]) or not (QV30[a, j] >= min_qv):
            continue
        out.append((a, b, j, e))
    # one trade per coin at a time: drop events whose window overlaps an earlier accepted window of the same coin
    out.sort(key=lambda r: r[0]); last = {}; keep = []
    for a, b, j, e in out:
        if last.get(j, -1) > a:
            continue
        keep.append((a, b, j, e)); last[j] = b
    return keep


def book(trades, cap=0.25, hedge=False, cost_mult=1.0, skip_coins=(), scale=1.0, swap=None):
    """daily equity curve. Returns (daily_ret array over all days, per-trade contribution list)."""
    W = np.zeros((ND, len(SYMS)))
    act = [[] for _ in range(ND)]
    for k, (a, b, j, e) in enumerate(trades):
        if SYMS[j] in skip_coins:
            continue
        for d in range(a, b):
            act[d].append(k)
    for d in range(ND):
        n = len(act[d])
        if n:
            w = min(cap, 1.0 / n) * scale
            for k in act[d]:
                W[d, (swap[k] if swap is not None else trades[k][2])] -= w
    gross = np.where(np.isnan(R), 0.0, R)
    ret = (W * gross).sum(1) - (W * FUND).sum(1)  # short receives funding: -W*fund with W<0 gives +
    turn = np.abs(np.diff(np.vstack([np.zeros((1, W.shape[1])), W]), axis=0)).sum(1)
    cost = turn * (FEE + SLIP) * cost_mult
    if hedge:
        hb = -W.sum(1)  # long BTC = gross short
        ret = ret + hb * gross[:, BTC] - hb * FUND[:, BTC]
        cost = cost + np.abs(np.diff(np.r_[0.0, hb])) * (FEE + SLIP) * cost_mult
    ret = ret - cost
    # per trade contribution (approx: weight x return, using weights on its days)
    contrib = []
    for k, (a, b, j, e) in enumerate(trades):
        if SYMS[j] in skip_coins or swap is not None:
            continue
        cc = 0.0
        for d in range(a, b):
            w = -W[d, j]
            cc += w * (-(gross[d, j]) + FUND[d, j]) - (2 * (FEE + SLIP) * cost_mult * w / max(b - a, 1))
            if hedge:
                cc += w * (gross[d, BTC] - FUND[d, BTC])
        contrib.append((SYMS[j], a, cc))
    return ret, contrib


def monthly(ret, start, end):
    """{month: %} compounding daily returns, days with start <= T < end."""
    out = {}
    for d in range(ND):
        if start <= T[d] < end:
            m = MONTHS[d]; out[m] = out.get(m, 1.0) * (1 + ret[d])
    return {m: (v - 1) * 100 for m, v in out.items()}


def stats(mo):
    v = np.array(list(mo.values()))
    if not len(v):
        return {}
    eq = np.cumprod(1 + v / 100); dd = 1 - eq / np.maximum.accumulate(np.r_[1, eq])[1:]
    geo = (eq[-1] ** (1 / len(v)) - 1) * 100
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 and v.std() > 0 else 0
    return dict(n=len(v), median=round(float(np.median(v)), 3), geo=round(float(geo), 3), mean=round(float(v.mean()), 3),
                up=int((v > 0).sum()), total=round(float((eq[-1] - 1) * 100), 2), maxdd=round(float(dd.max() * 100), 2),
                t=round(float(t), 2), worst=round(float(v.min()), 2))


def daily_dd(ret, start, end):
    m = (T >= start) & (T < end); eq = np.cumprod(1 + ret[m])
    return float((1 - eq / np.maximum.accumulate(eq)).max() * 100)
