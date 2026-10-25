"""Forced-flow (liquidation cascade / OI build) event engine on 5m bars with 5m open interest.

Signal at the close of 5m bar k, over a window of W bars:
  z   = log(c[k]/c[k-W]) / (sigma_5m * sqrt(W))        sigma_5m = std of 5m log returns over the prior 3 days
  vs  = quote volume of the W bars / (W * mean 5m quote volume over the prior 3 days)
  doi = OI[close k] / OI[close k-W] - 1                 OI = archive `metrics` sum_open_interest (5m stamps)
FLUSH  (mode "revert"): |z| >= Z, vs >= V, doi <= -D   -> trade AGAINST the move (forced flow exhausted)
BUILD  (mode "cont")  : |z| >= Z, vs >= V, doi >= +D   -> trade WITH the move (new positions pushing)
confirm=1 (revert only): wait for the flush to end = first bar j in k..k+6 whose OI stops falling (OI[j] >= OI[j-1]).
Entry at the OPEN of bar j+2 (one extra 5m bar of delay, so the OI stamp is surely published). Stop = S x ATR1h
(mean 12-bar high-low range over the prior day), optional target T x ATR1h, time stop H bars. Stop is checked
before target inside a bar. Costs FEE+SLIP per side on notional, funding paid on every 8h stamp held.
Portfolio: K slots, notional per trade = lev x equity / K at entry, capped at 1% of the entry bar's quote volume.
Liquidation at lev L: adverse move >= 1/L - 0.5% from entry (on bar extremes) wipes the slot margin.
"""
import math, sys
from datetime import datetime, timezone
import numpy as np

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")
from common import FEE, SLIP, MAINT_MARGIN, DEV_START, DEV_END, TEST_START, TEST_END  # noqa
import data_cascade as DC

B5 = 300_000
DAY = 86_400_000
LOOK = 864  # 3 days of 5m bars


def _qms(q):
    return int(datetime.fromisoformat(q).replace(tzinfo=timezone.utc).timestamp() * 1000)


class Coin:
    pass


def load_coins(verbose=False):
    u = DC.universe()
    qs = sorted(u)
    qstart = [_qms(q) for q in qs] + [_qms("2027-01-01")]
    coins = {}
    for sym in sorted({s for v in u.values() for s in v}):
        a = DC.load(sym, "5m")
        m = DC.load(sym, "metrics")
        if a is None or len(a) < LOOK + 50:
            continue
        # regular 5m grid (fill gaps with flat bars, zero volume) so index arithmetic = time arithmetic
        t0, t1 = int(a[0, 0]), int(a[-1, 0])
        n = (t1 - t0) // B5 + 1
        idx = ((a[:, 0] - t0) // B5).astype(np.int64)
        c = Coin()
        c.sym, c.t0, c.n = sym, t0, n
        c.t = t0 + np.arange(n, dtype=np.int64) * B5
        cl = np.full(n, np.nan)
        cl[idx] = a[:, 4]
        have = ~np.isnan(cl)
        # forward fill closes
        last = np.maximum.accumulate(np.where(have, np.arange(n), 0))
        cl = cl[last]
        o, h, l = cl.copy(), cl.copy(), cl.copy()
        o[idx], h[idx], l[idx] = a[:, 1], a[:, 2], a[:, 3]
        qv = np.zeros(n); qv[idx] = a[:, 5]
        c.o, c.h, c.l, c.c, c.qv = o, h, l, cl, qv
        c.real = have
        # OI at the close of each bar = stamp t + 5m
        oi = np.full(n, np.nan)
        if m is not None and len(m):
            k = ((m[:, 0].astype(np.int64) - B5 - t0) // B5)
            ok = (k >= 0) & (k < n) & ((m[:, 0].astype(np.int64) - t0) % B5 == 0) & (m[:, 1] > 0)
            oi[k[ok]] = m[ok, 1]
        c.oi = oi
        # tradable mask: bar inside a quarter where the coin was picked
        tr = np.zeros(n, bool)
        for i, q in enumerate(qs):
            if sym in u[q]:
                tr[(c.t >= qstart[i]) & (c.t < qstart[i + 1])] = True
        c.tradable = tr
        # features
        lr = np.zeros(n); lr[1:] = np.diff(np.log(cl))
        cs = np.concatenate([[0], np.cumsum(lr)]); cs2 = np.concatenate([[0], np.cumsum(lr * lr)])
        sig = np.full(n, np.nan)
        j = np.arange(LOOK, n)
        mu = (cs[j] - cs[j - LOOK]) / LOOK  # prior LOOK bars ending at j-1 ... j-LOOK
        var = (cs2[j] - cs2[j - LOOK]) / LOOK - mu * mu
        sig[j] = np.sqrt(np.maximum(var, 1e-12))
        c.sig = sig  # sigma known at bar j from bars < j (shifted: excludes the event bars? uses up to j-1)
        cq = np.concatenate([[0], np.cumsum(qv)])
        mq = np.full(n, np.nan); mq[j] = (cq[j] - cq[j - LOOK]) / LOOK
        c.cq, c.mq = cq, mq
        # ATR1h proxy: mean over prior 288 bars of the 12-bar high-low range, sampled every bar
        from numpy.lib.stride_tricks import sliding_window_view as sw
        hh = np.full(n, np.nan); ll = np.full(n, np.nan)
        hh[11:] = sw(h, 12).max(1); ll[11:] = sw(l, 12).min(1)
        rng = (hh - ll) / cl
        rng = np.nan_to_num(rng, nan=0.0)
        cr = np.concatenate([[0], np.cumsum(rng)])
        atr = np.full(n, np.nan); jj = np.arange(300, n)
        atr[jj] = (cr[jj] - cr[jj - 288]) / 288
        c.atr = atr  # relative (fraction of price)
        f = DC.load_funding(sym)
        c.fund_t, c.fund_r = (f[:, 0].astype(np.int64), f[:, 1]) if len(f) else (np.zeros(0, np.int64), np.zeros(0))
        c.fund_cum = np.concatenate([[0], np.cumsum(c.fund_r)])
        coins[sym] = c
        if verbose:
            print(sym, n, int(np.isfinite(oi).sum()), flush=True)
    return coins


def features(c, W):
    """z, vs, doi arrays for window W (value at index k uses bars up to and including k)."""
    n = c.n
    np.seterr(divide="ignore", invalid="ignore")
    z = np.full(n, np.nan); vs = np.full(n, np.nan); doi = np.full(n, np.nan)
    k = np.arange(LOOK + W, n)
    r = np.log(c.c[k] / c.c[k - W])
    s = c.sig[k - W + 1]  # sigma from bars strictly before the window
    z[k] = r / (s * math.sqrt(W))
    vs[k] = (c.cq[k + 1] - c.cq[k - W + 1]) / (W * c.mq[k - W + 1])
    doi[k] = c.oi[k] / c.oi[k - W] - 1
    return z, vs, doi


def find_events(c, feats, p):
    """Candidate signal bars (index of the signal bar j, direction +1 long / -1 short)."""
    z, vs, doi = feats
    Z, V, D, mode = p["Z"], p["V"], p["D"], p["mode"]
    with np.errstate(invalid="ignore"):
        big = (np.abs(z) >= Z) & (vs >= V) & c.tradable
        if mode == "revert":
            ev = big & (doi <= -D)
            dirn = -np.sign(z)
        else:
            ev = big & (doi >= D)
            dirn = np.sign(z)
    ks = np.nonzero(ev)[0]
    out = []
    for k in ks:
        j = k
        if mode == "revert" and p.get("confirm", 0):
            j = None
            for jj in range(k, min(k + 7, c.n)):
                if np.isfinite(c.oi[jj]) and np.isfinite(c.oi[jj - 1]) and c.oi[jj] >= c.oi[jj - 1]:
                    j = jj
                    break
            if j is None:
                continue
        out.append((k, j, int(dirn[k])))
    return out


def sim_trade(c, e, d, p, lev, cost_mult=1.0):
    """Simulate one trade entering at the open of bar e, direction d. Returns dict or None."""
    H, S, T = p["H"], p["S"], p.get("T", 0)
    if e >= c.n or not np.isfinite(c.atr[e - 1]) or c.atr[e - 1] <= 0:
        return None
    px = c.o[e]
    atr = c.atr[e - 1]
    stop = px * (1 - d * S * atr)
    tgt = px * (1 + d * T * atr) if T else None
    liq_m = 1.0 / lev - MAINT_MARGIN
    end = min(e + H, c.n)  # exit at the open of bar `end` on time stop
    hi, lo, op = c.h[e:end], c.l[e:end], c.o[e:end]
    adv = (px - lo) / px if d > 0 else (hi - px) / px  # adverse excursion per bar
    fav_hit = (hi >= tgt) if (tgt is not None and d > 0) else ((lo <= tgt) if tgt is not None else np.zeros(len(hi), bool))
    stop_hit = (lo <= stop) if d > 0 else (hi >= stop)
    liq_hit = adv >= liq_m
    first = lambda m: int(np.argmax(m)) if m.any() else 10 ** 9
    i_s, i_t, i_l = first(stop_hit), first(fav_hit), first(liq_hit)
    i = min(i_s, i_t, i_l)
    liq = False
    if i == 10 ** 9:
        if end >= c.n:
            xi, xp = c.n - 1, c.c[c.n - 1]
            xt = c.t[xi] + B5
        else:
            xi, xp = end, c.o[end]
            xt = c.t[end]
        ret = d * (xp / px - 1)
    else:
        b = e + i
        xt = c.t[b] + B5  # leave at the end of that bar at the latest
        gap = d * (op[i] / px - 1)  # open of the exit bar vs entry
        if i_l <= i_s and i_l <= i_t and (gap <= -liq_m or i_l < i_s):
            liq = True
            ret = -1.0 / lev  # slot margin lost (as a fraction of notional)
        elif i_s <= i_t:
            fill = op[i] if (d > 0 and op[i] < stop) or (d < 0 and op[i] > stop) else stop
            ret = d * (fill / px - 1)
            if -ret >= liq_m:
                liq, ret = True, -1.0 / lev
        else:
            fill = op[i] if (d > 0 and op[i] > tgt) or (d < 0 and op[i] < tgt) else tgt
            ret = d * (fill / px - 1)
        xi = b
    cost = 2 * (FEE + SLIP) * cost_mult
    # funding stamps in (entry_t, exit_t]
    et = c.t[e]
    a0, a1 = np.searchsorted(c.fund_t, et, "right"), np.searchsorted(c.fund_t, xt, "right")
    fund = -d * (c.fund_cum[a1] - c.fund_cum[a0])
    net = ret - cost + (0 if liq else fund)
    return {"sym": c.sym, "e": e, "x": xi, "et": int(et), "xt": int(xt), "d": d, "ret": net, "liq": liq,
            "qv": float(c.qv[e])}


def gen_trades(coins, p, lev=1.0, cost_mult=1.0, start=DEV_START, end=TEST_END, feat_cache=None, exclude=()):
    trades = []
    for sym, c in coins.items():
        if sym in exclude:
            continue
        key = (sym, p["W"])
        if feat_cache is not None and key in feat_cache:
            f = feat_cache[key]
        else:
            f = features(c, p["W"])
            if feat_cache is not None:
                feat_cache[key] = f
        busy_until = -1
        for k, j, d in find_events(c, f, p):
            e = j + 2
            if e <= busy_until or e >= c.n or not c.real[e]:
                continue
            if not (start <= c.t[e] < end):
                continue
            tr = sim_trade(c, e, d, p, lev, cost_mult)
            if tr is None:
                continue
            tr["k"] = k
            trades.append(tr)
            busy_until = tr["x"] + p.get("cool", 0)
    trades.sort(key=lambda r: r["et"])
    return trades


def day_marks(coins, tr, days):
    """Unrealized return (fraction of notional) of an open trade at each day-end stamp in `days`."""
    c = coins[tr["sym"]]
    out = []
    for dt in days:
        b = (dt - c.t0) // B5 - 1  # last bar closed by dt
        px = c.o[tr["e"]]
        out.append(tr["d"] * (c.c[b] / px - 1))
    return out


def portfolio(coins, trades, K, lev=1.0, start=DEV_START, end=TEST_END, eq0=10_000.0, vol_cap=0.01):
    """Slot portfolio. Returns curve [(ms, equity)] at every UTC day end + start, accepted trades with $ pnl."""
    cash = eq0
    open_ = []  # (xt, trade, notional)
    acc = []
    day_ends = list(range(start + DAY, end + 1, DAY))
    if day_ends[-1] < end:
        day_ends.append(end)
    curve = [(start, eq0)]
    ti = 0
    nT = len(trades)
    for de in day_ends:
        # process entries and exits within (prev, de] in time order
        while True:
            nxt_entry = trades[ti]["et"] if ti < nT else 10 ** 18
            nxt_exit = min((o[0] for o in open_), default=10 ** 18)
            if min(nxt_entry, nxt_exit) >= de:
                break
            if nxt_exit <= nxt_entry:
                o = min(open_, key=lambda r: r[0])
                open_.remove(o)
                pnl = o[2] * o[1]["ret"]
                cash += pnl
                o[1]["pnl"] = pnl
                acc.append(o[1])
            else:
                t = trades[ti]; ti += 1
                if t["et"] < start or t["et"] >= end:
                    continue
                if len(open_) >= K or any(o[1]["sym"] == t["sym"] for o in open_):
                    continue
                eq_now = cash  # realised equity
                notional = min(lev * eq_now / K, vol_cap * t["qv"])
                if notional <= 0:
                    continue
                t = dict(t); t["notional"] = notional
                open_.append((t["xt"], t, notional))
        # mark to market at day end
        mtm = 0.0
        for xt, t, notional in open_:
            c = coins[t["sym"]]
            b = min((de - c.t0) // B5 - 1, c.n - 1)
            u = t["d"] * (c.c[b] / c.o[t["e"]] - 1)
            mtm += notional * max(u, -1.0 / lev)
        curve.append((de - 1, cash + mtm))
    for xt, t, notional in open_:  # close anything still open at its own exit
        pnl = notional * t["ret"]; cash += pnl; t["pnl"] = pnl; acc.append(t)
    curve[-1] = (curve[-1][0], cash)
    return curve, acc
