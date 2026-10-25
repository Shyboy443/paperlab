"""Panel, features, trade generation and a leveraged portfolio simulator for agent 04 (crash / squeeze reversal)."""
import sys, math
from pathlib import Path
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from common import (DEV_START, DEV_END, TEST_START, TEST_END, FEE, SLIP, MAINT_MARGIN, HOUR, DAY, utc)  # noqa
import data04

GRID0 = utc(2020, 9, 1)
STABLE = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT", "FDUSDUSDT", "DAIUSDT", "USDEUSDT", "EURUSDT", "GBPUSDT",
          "AEURUSDT", "XUSDUSDT", "USD1USDT", "BFUSDUSDT", "RLUSDUSDT", "PAXGUSDT", "XAUTUSDT"}


def month_starts():
    out, y, m = [], 2020, 10
    while utc(y, m, 1) < TEST_END:
        out.append(utc(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def universes(n=60):
    """Point-in-time universe: at each month start, the n perps with the most quote volume in the previous 30 days
    (>= 80% of hourly bars present). Uses every USDT perp that ever traded, delisted ones included."""
    cache = data04.ROOT / f"universe_{n}.npy"
    ms = month_starts()
    if cache.exists():
        return ms, np.load(cache, allow_pickle=True).tolist()
    vols = {m: [] for m in ms}
    for s in data04.all_usdt_perps():
        if s in STABLE:
            continue
        a = data04.load(s)
        if not len(a):
            continue
        for m in ms:
            sel = (a[:, 0] >= m - 30 * DAY) & (a[:, 0] < m)
            if sel.sum() >= 0.8 * 720:
                vols[m].append((a[sel, 5].sum(), s))
    out = [[s for _, s in sorted(vols[m], reverse=True)[:n]] for m in ms]
    np.save(cache, np.array(out, dtype=object), allow_pickle=True)
    return ms, out


class Panel:
    def __init__(self, n=60):
        self.ms, self.univ = universes(n)
        self.syms = sorted({s for u in self.univ for s in u})
        self.t = np.arange(GRID0, TEST_END, HOUR, dtype=np.int64)
        T, S = len(self.t), len(self.syms)
        self.O, self.H, self.L, self.C, self.QV, self.TB = (np.full((T, S), np.nan, np.float64) for _ in range(6))
        for j, s in enumerate(self.syms):
            a = data04.load(s)
            idx = ((a[:, 0].astype(np.int64) - GRID0) // HOUR)
            ok = (idx >= 0) & (idx < T)
            idx, a = idx[ok], a[ok]
            for arr, c in ((self.O, 1), (self.H, 2), (self.L, 3), (self.C, 4), (self.QV, 5), (self.TB, 6)):
                arr[idx, j] = a[:, c]
        self.inu = np.zeros((T, S), bool)
        col = {s: j for j, s in enumerate(self.syms)}
        bounds = list(self.ms) + [TEST_END]
        for k, m in enumerate(self.ms):
            r0, r1 = (m - GRID0) // HOUR, (bounds[k + 1] - GRID0) // HOUR
            for s in self.univ[k]:
                self.inu[r0:r1, col[s]] = True
        self.inu &= ~np.isnan(self.C)
        self.fund = {}

    def funding_cum(self, j):
        """Cumulative funding per grid row (row i includes stamps inside bar i). The archive ends 2026-09; the
        October 2026 tail uses the coin's trailing 30-day mean rate per hour."""
        if j in self.fund:
            return self.fund[j]
        s = self.syms[j]
        if not (data04.ROOT / "funding" / f"{s}.npz").exists():
            data04._fetch_symbol_funding(s)
        a = data04.load(s, "funding")
        per_bar = np.zeros(len(self.t))
        if len(a):
            idx = ((a[:, 0].astype(np.int64) - GRID0) // HOUR)
            ok = (idx >= 0) & (idx < len(self.t))
            np.add.at(per_bar, idx[ok], a[ok, 1])
            last = int(a[-1, 0])
            if last < TEST_END - 2 * DAY:
                recent = a[a[:, 0] > last - 30 * DAY]
                r0 = (last - GRID0) // HOUR + 1
                per_bar[r0:] += recent[:, 1].sum() / (30 * 24)
        self.fund[j] = np.cumsum(per_bar)
        return self.fund[j]


def _roll(x, w, fn):
    import pandas as pd
    r = pd.DataFrame(x).rolling(w, min_periods=w)
    return {np.std: r.std, np.median: r.median, np.sum: r.sum}[fn]().to_numpy()


def _shift(x, k):
    return np.vstack([np.full((k, x.shape[1]), np.nan), x[:-k]])


def base_features(P, vol_win=14 * 24, base_win=7 * 24):
    C = P.C
    lr = np.vstack([np.full((1, C.shape[1]), np.nan), np.diff(np.log(C), axis=0)])
    with np.errstate(all="ignore"):
        sd = _roll(np.nan_to_num(lr), vol_win, np.std)
        base = _roll(np.nan_to_num(P.QV), base_win, np.median)
    return {"lr": lr, "sd": sd, "base": base}


def features(P, B, k):
    """Move over the last k closed bars, scaled by volatility and volume known BEFORE the move."""
    with np.errstate(all="ignore"):
        rk = np.full_like(P.C, np.nan)
        rk[k:] = np.log(P.C[k:] / P.C[:-k])
        z = rk / (_shift(B["sd"], k) * math.sqrt(k))
        qk = _roll(np.nan_to_num(P.QV), k, np.sum)
        vspike = qk / (_shift(B["base"], k) * k)
        tbs = _roll(np.nan_to_num(P.TB), k, np.sum) / qk
    return {"rk": rk, "z": z, "vspike": vspike, "tbs": tbs}


def gen_trades(P, sig, side, hold, entry="market", lim_off=0.0, lim_wait=3, tp=None, sl=None, cool=24,
               max_per_bar=None, rank=None):
    """sig: bool (T,S) signal on CLOSED bar i; side +1 long / -1 short.
    market entry: open of bar i+1. limit entry: limit = close_i x (1 - side x lim_off), live for bars i+1..i+lim_wait,
    filled only if the price trades THROUGH it (low < limit for a long), at the limit (or the open if it gaps past).
    Exits: stop `sl` (checked first in each bar, fills at the stop or the worse open), target `tp` as a maker limit
    (needs trade-through), else at the open `hold` bars after entry. Records the worst intrabar adverse excursion."""
    T, S = sig.shape
    trades, last = [], np.full(S, -10 ** 9)
    for i in np.where(sig.any(axis=1))[0]:
        js = np.where(sig[i])[0]
        if rank is not None and max_per_bar and len(js) > max_per_bar:
            js = js[np.argsort(rank[i, js])][:max_per_bar]
        for j in js:
            if i - last[j] < cool or i + 2 >= T:
                continue
            if entry == "market":
                e_i, px, maker_in = i + 1, P.O[i + 1, j], False
                if np.isnan(px):
                    continue
            else:
                lim, e_i = P.C[i, j] * (1 - side * lim_off), None
                for b in range(i + 1, min(i + 1 + lim_wait, T)):
                    if side > 0 and P.L[b, j] < lim:
                        e_i, px = b, min(lim, P.O[b, j]); break
                    if side < 0 and P.H[b, j] > lim:
                        e_i, px = b, max(lim, P.O[b, j]); break
                if e_i is None:
                    continue
                maker_in = px == lim
            last[j] = i
            end = min(e_i + hold, T - 1)
            worst, x_i, x_px, maker_out, path = 0.0, end, P.O[end, j], False, []
            for b in range(e_i, end):
                h, l, o, c = P.H[b, j], P.L[b, j], P.O[b, j], P.C[b, j]
                if np.isnan(c):
                    x_i, x_px = b, (path[-1][1] if path else px); break
                if sl is not None:
                    stop = px * (1 - side * sl)
                    if (side > 0 and l <= stop) or (side < 0 and h >= stop):
                        fill = stop if b == e_i else (min(stop, o) if side > 0 else max(stop, o))
                        worst = max(worst, abs(fill - px) / px)
                        x_i, x_px = b + 1, fill; break
                worst = max(worst, (px - l) / px if side > 0 else (h - px) / px)
                if tp is not None:
                    tgt = px * (1 + side * tp)
                    if (side > 0 and h > tgt) or (side < 0 and l < tgt):
                        x_i, x_px, maker_out = b + 1, tgt, True; break
                path.append((b, float(c)))
            if np.isnan(x_px):
                x_px = path[-1][1] if path else px
            trades.append({"j": int(j), "sig_i": int(i), "e_i": int(e_i), "x_i": int(x_i), "px": float(px),
                           "x_px": float(x_px), "side": side, "worst": float(worst), "maker_in": bool(maker_in),
                           "maker_out": bool(maker_out), "path": path,
                           "qv": float(np.nan_to_num(P.QV[e_i, j]))})
    return trades


def simulate(P, trades, start, end, lev=1.0, frac=0.1, max_pos=10, cost_mult=1.0, eq0=1000.0):
    """Hourly equity curve. Each trade gets margin = frac x equity (isolated), notional = margin x lev.
    Fee+slip per side on notional (maker legs: fee only), funding at the real stamps, liquidation (margin lost) when
    the intrabar adverse excursion reaches 1/lev - MAINT_MARGIN. Notional capped at 1% of the entry bar quote volume."""
    r0, r1 = (start - GRID0) // HOUR, (end - GRID0) // HOUR
    fee, slip = FEE * cost_mult, SLIP * cost_mult
    liq = 1.0 / lev - MAINT_MARGIN
    by_entry = {}
    for t in sorted(trades, key=lambda t: (t["e_i"], t["j"])):
        if r0 <= t["e_i"] < r1:
            by_entry.setdefault(t["e_i"], []).append(t)
    cash, openp, curve, pnls, nliq = eq0, [], [(start, eq0)], [], 0
    for i in range(r0, r1):
        still = []
        for p in openp:
            if p["t"]["x_i"] <= i:
                cash += p["margin"] + p["pnl"]; pnls.append(p["pnl"])
            else:
                still.append(p)
        openp = still
        for t in by_entry.get(i, []):
            if len(openp) >= max_pos:
                continue
            eq = cash + sum(p["margin"] + p["mtm"] for p in openp)
            margin = min(frac * eq, cash)
            if margin <= 0.01:
                continue
            notional = min(margin * lev, 0.01 * t["qv"])
            margin = notional / lev
            if margin <= 0:
                continue
            side = t["side"]
            c_in = notional * (fee + (0 if t["maker_in"] else slip))
            if t["worst"] >= liq:
                pnl = -margin; nliq += 1
            else:
                gross = notional * side * (t["x_px"] / t["px"] - 1)
                c_out = notional * (t["x_px"] / t["px"]) * (fee + (0 if t["maker_out"] else slip))
                fc = P.funding_cum(t["j"])
                f = notional * side * (fc[max(t["x_i"] - 1, t["e_i"])] - fc[t["e_i"] - 1])
                pnl = max(gross - c_in - c_out - f, -margin)
            cash -= margin
            openp.append({"t": t, "margin": margin, "notional": notional, "pnl": pnl, "mtm": -c_in,
                          "pd": dict(t["path"]), "side": side})
        for p in openp:
            c = p["pd"].get(i)
            if c is not None:
                p["mtm"] = max(p["notional"] * p["side"] * (c / p["t"]["px"] - 1), -p["margin"])
        curve.append((int(P.t[i]) + HOUR, cash + sum(p["margin"] + p["mtm"] for p in openp)))
    for p in openp:
        cash += p["margin"] + p["pnl"]; pnls.append(p["pnl"])
    curve[-1] = (curve[-1][0], cash)
    return curve, pnls, nliq
