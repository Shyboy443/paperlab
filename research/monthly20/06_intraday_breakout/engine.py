"""Agent 06 engine: intraday volatility-expansion breakouts on 15m Binance USDT perps.

Signals use closed bars only and fill at the next bar's open (taker). Exits are resting stop orders
(trailing ATR chandelier, filled at the stop or at the open if the bar gaps through it) or a time exit
at a bar close. Costs: (FEE+SLIP) per side on notional, funding from the real funding stamps,
isolated-margin liquidation at a loss of 1/L - 0.5%, size capped at 1% of the signal bar's quote volume.
"""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import klines, funding, FEE, SLIP, MAINT_MARGIN, DAY, HOUR, MIN, DEV_START, DEV_END, TEST_START, TEST_END, summary  # noqa

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "AVAXUSDT"]  # 15m limited to 6 coins (shared IP)
BAR = 15 * MIN
_DATA = {}
_SIG = {}


def load(sym):
    if sym in _DATA:
        return _DATA[sym]
    k = np.array(klines(sym, "15m"), dtype=float)
    d = {"t": k[:, 0].astype(np.int64), "o": k[:, 1], "h": k[:, 2], "l": k[:, 3], "c": k[:, 4], "qv": k[:, 6]}
    f = funding(sym)
    ft = np.array([x[0] for x in f], dtype=np.int64)
    fr = np.array([x[1] for x in f], dtype=float)
    d["ft"], d["fcum"] = ft, np.concatenate([[0.0], np.cumsum(fr)])
    c, h, l = d["c"], d["h"], d["l"]
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    d["tr"] = tr
    d["atr"] = ema(tr, 2 / (14 + 1))
    d["tod"] = ((d["t"] % DAY) // BAR).astype(np.int64)  # bar slot in the UTC day, 0..95
    d["day"] = (d["t"] // DAY).astype(np.int64)
    # trend reference: EMA of closes over ~10 days (960 bars)
    d["ema_long"] = ema(c, 2 / (960 + 1))
    _DATA[sym] = d
    return d


def ema(x, a):
    out = np.empty_like(x)
    acc = x[0]
    for i in range(len(x)):  # small numba-free loop; fine for ~235k bars
        acc = acc + a * (x[i] - acc)
        out[i] = acc
    return out


def rolling(x, n, fn):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    out[n - 1:] = fn(sliding_window_view(x, n), axis=1)
    return out


# ---------------------------------------------------------------- signals
# Each returns sig (+1/-1/0 at closed bar i) and deadline (bar index of the time exit).

def sig_orb(d, p):
    """Session opening range: range = first R bars from session slot; break by a close within W bars after."""
    n = len(d["c"])
    sig = np.zeros(n, np.int8)
    dl = np.zeros(n, np.int64)
    starts = np.flatnonzero(d["tod"] == p["slot"])
    h, l, c, t = d["h"], d["l"], d["c"], d["t"]
    R, W, H = p["R"], p["W"], p["H"]
    for s in starts:
        e = s + R
        if e + W >= n or t[e] - t[s] != R * BAR:
            continue
        hi, lo = h[s:e].max(), l[s:e].min()
        if p.get("min_range_atr"):
            if hi - lo < p["min_range_atr"] * d["atr"][s - 1] * np.sqrt(R):
                continue
        for i in range(e, e + W):
            if c[i] > hi * (1 + p.get("buf", 0)):
                sig[i], dl[i] = 1, s + H
                break
            if c[i] < lo * (1 - p.get("buf", 0)):
                sig[i], dl[i] = -1, s + H
                break
    return sig, dl


def sig_squeeze(d, p):
    """Bollinger inside Keltner for >= m bars, then a close outside the band."""
    c = d["c"]
    n_, m = p["n"], p["m"]
    ma = rolling(c, n_, np.mean)
    sd = rolling(c, n_, np.std)
    atr_n = rolling(d["tr"], n_, np.mean)
    up, dn = ma + 2 * sd, ma - 2 * sd
    sq = (2 * sd) < (p.get("kc", 1.5) * atr_n)
    sq_run = rolling(sq.astype(float), m, np.min)  # 1 if squeezed for all of the last m bars
    prev = np.concatenate([[0], sq_run[:-1]])
    sig = np.zeros(len(c), np.int8)
    sig[(prev == 1) & (c > up)] = 1
    sig[(prev == 1) & (c < dn)] = -1
    dl = np.arange(len(c)) + p["H"]
    return sig, dl


def sig_pdhl(d, p):
    """First close of the UTC day above the prior day's high (long) or below its low (short)."""
    day, h, l, c = d["day"], d["h"], d["l"], d["c"]
    n = len(c)
    sig = np.zeros(n, np.int8)
    dl = np.zeros(n, np.int64)
    bounds = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [n]])
    for k in range(1, len(starts)):
        ps, pe, s, e = starts[k - 1], ends[k - 1], starts[k], ends[k]
        if pe - ps < 90:
            continue
        hi, lo = h[ps:pe].max(), l[ps:pe].min()
        last = min(e, s + p.get("W", 96))
        for i in range(s, last):
            if c[i] > hi:
                sig[i], dl[i] = 1, i + p["H"]
                break
            if c[i] < lo:
                sig[i], dl[i] = -1, i + p["H"]
                break
    return sig, dl


SIGNALS = {"orb": sig_orb, "squeeze": sig_squeeze, "pdhl": sig_pdhl}


# ---------------------------------------------------------------- per-coin trades

def coin_trades(sym, p, start, end):
    """Unlevered trades: dict per trade with returns as a fraction of notional (before costs)."""
    d = load(sym)
    key = (sym, tuple(sorted((a, b) for a, b in p.items() if a not in ("k", "trend", "side"))))
    if _SIG.get(sym, (None,))[0] != key:  # keep one signal set per coin (grid loops k/trend innermost)
        _SIG[sym] = (key, SIGNALS[p["kind"]](d, p))
    sig, dl = _SIG[sym][1]
    if p.get("trend"):
        tr = np.sign(d["c"] - d["ema_long"])
        sig = np.where(sig == tr, sig, 0).astype(np.int8)
    if p.get("side") == "long":
        sig = np.where(sig > 0, sig, 0).astype(np.int8)
    t, o, h, l, c, atr, qv = d["t"], d["o"], d["h"], d["l"], d["c"], d["atr"], d["qv"]
    n = len(c)
    k = p["k"]
    idx = np.flatnonzero((sig != 0) & (t >= start) & (t < end))
    trades, busy_until = [], -1
    for i in idx:
        if i <= busy_until or i + 1 >= n:
            continue
        side = int(sig[i])
        j = i + 1
        ep = o[j]
        stop = ep - side * k * atr[i]
        ext = ep
        mae = 0.0
        deadline = min(max(dl[i], j), n - 1)
        exit_p = None
        while True:
            if side > 0:
                if o[j] <= stop:
                    exit_p = o[j]
                elif l[j] <= stop:
                    exit_p = stop
                adverse = (ep - (l[j] if exit_p is None else exit_p)) / ep
            else:
                if o[j] >= stop:
                    exit_p = o[j]
                elif h[j] >= stop:
                    exit_p = stop
                adverse = ((h[j] if exit_p is None else exit_p) - ep) / ep
            mae = max(mae, adverse)
            if exit_p is not None:
                break
            if j >= deadline or t[j] + BAR >= end:
                exit_p = c[j]
                break
            ext = max(ext, h[j]) if side > 0 else min(ext, l[j])
            ns = ext - side * k * atr[j]
            stop = max(stop, ns) if side > 0 else min(stop, ns)
            j += 1
        et, xt = int(t[i + 1]), int(t[j] + BAR)
        a, b = np.searchsorted(d["ft"], et, "left"), np.searchsorted(d["ft"], xt, "left")
        fund = d["fcum"][b] - d["fcum"][a]
        trades.append({"sym": sym, "et": et, "xt": xt, "side": side,
                       "gross": side * (exit_p / ep - 1), "fund": -side * fund, "mae": mae,
                       "cap": 0.01 * qv[i]})
        busy_until = j
    return trades


def all_trades(p, start, end, coins=COINS):
    out = []
    for s in coins:
        out += coin_trades(s, p, start, end)
    return sorted(out, key=lambda x: x["et"])


# ---------------------------------------------------------------- portfolio

def portfolio(trades, L, start, end, cost_mult=1.0, n_slots=len(COINS), eq0=10_000.0):
    """Isolated margin of equity/n_slots per trade, notional = L x margin (capped by 1% bar volume).

    Equity is realized cash; open trades are booked at exit (trades last hours, a day at most)."""
    side_cost = (FEE + SLIP) * cost_mult
    liq = 1 / L - MAINT_MARGIN
    events = []  # (time, order, kind, trade_id)
    for k, tr in enumerate(trades):
        events.append((tr["et"], 1, k))
        events.append((tr["xt"], 0, k))
    events.sort()
    eq = eq0
    curve = [(start, eq)]
    open_ = {}
    pnls, liqs = [], 0
    for tm, kind, k in events:
        tr = trades[k]
        if kind == 1:
            margin = eq / n_slots
            notional = min(margin * L, tr["cap"])
            if eq <= 1 or notional <= 0:
                continue
            margin = notional / L
            open_[k] = (margin, notional)
        else:
            if k not in open_:
                continue
            margin, notional = open_.pop(k)
            if tr["mae"] >= liq:
                pnl = -margin - notional * side_cost
                liqs += 1
            else:
                pnl = notional * (tr["gross"] + tr["fund"] - 2 * side_cost)
                pnl = max(pnl, -margin - notional * side_cost)
            eq += pnl
            pnls.append(pnl)
            curve.append((tm, max(eq, 1e-6)))
    # month-end anchors so empty months show 0
    from common import monthly_returns  # noqa
    curve.append((end - 1, max(eq, 1e-6)))
    s = summary(curve, pnls)
    s["liquidations"] = liqs
    tot = sum(pnls)
    s["top1_share_of_profit"] = round(max(pnls) / tot * 100, 1) if pnls and tot > 0 else None
    return s, curve, pnls


def unlev_stats(trades, cost_mult=1.0):
    side_cost = (FEE + SLIP) * cost_mult
    r = np.array([t["gross"] + t["fund"] - 2 * side_cost for t in trades]) if trades else np.zeros(0)
    g = np.array([t["gross"] for t in trades]) if trades else np.zeros(0)
    return {"n": len(r), "mean_net_bp": round(float(r.mean() * 1e4), 2) if len(r) else None,
            "mean_gross_bp": round(float(g.mean() * 1e4), 2) if len(g) else None,
            "win": round(float((r > 0).mean() * 100), 1) if len(r) else None}
