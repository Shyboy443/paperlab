"""Volatility compression -> expansion engine on 4h bars, portfolio across a point-in-time liquid universe.

Signal at the close of 4h bar i (all inputs known at that close), fill at bar i+1 open (taker).
  squeeze  : volatility measure (BB width / ATR% / 24h range) in the bottom q of its own trailing W-bar history,
             at some bar within the last A bars (the compression precondition)
  trigger  : close breaks the prior D-bar high (long) / low (short)
  direction: "htf"  -> long only if close > EMA(E) and EMA(E) rising over 6 bars (short mirrored)
             "bar"  -> direction of the breakout bar, no slower filter
  exits    : initial stop k x ATR14 from entry; chandelier trail = extreme since entry - k x ATR14 (ratchets only);
             time exit at the close after H bars. Stops fill at the stop or the gapped open.
  sizing   : equal risk: notional = rp x L x equity / stop distance, capped at pc x L x equity per position,
             total gross <= L x equity, <= 1% of the signal bar quote volume, max M positions.
  costs    : (FEE+SLIP) x cost_mult per side on notional; funding every 4h bar (long pays rate x notional).
  liquidation (L>1): position loses its margin (notional/L) when the adverse move from entry reaches 1/L - 0.5%.
Delisted coins: when a coin's data ends the position is closed at its last close.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, HOUR, summary  # noqa

STEP = 4 * HOUR


class Panel:
    def __init__(self, D, cols=None):
        cols = np.arange(len(D["syms"])) if cols is None else np.asarray(cols)
        self.t = D["t"]
        self.syms = [str(s) for s in D["syms"][cols]]
        for k in ("o", "h", "l", "c", "qv", "fund"):
            setattr(self, k, D[k][:, cols])
        self.first = D["first"][cols]
        T, N = self.c.shape
        valid = ~np.isnan(self.c)
        self.lastvalid = np.where(valid.any(0), T - 1 - np.argmax(valid[::-1], 0), -1)
        self._cache = {}

    def df(self, k):
        key = ("df", k)
        if key not in self._cache:
            self._cache[key] = pd.DataFrame(getattr(self, k))
        return self._cache[key]

    def atr(self, n=14):
        key = ("atr", n)
        if key not in self._cache:
            c, h, l = self.df("c"), self.df("h"), self.df("l")
            pc = c.shift(1)
            tr = np.fmax(h - l, np.fmax((h - pc).abs(), (l - pc).abs()))
            self._cache[key] = tr.ewm(alpha=1 / n, min_periods=n, adjust=False, ignore_na=True).mean().values
        return self._cache[key]

    def ema(self, n):
        key = ("ema", n)
        if key not in self._cache:
            self._cache[key] = self.df("c").ewm(span=n, min_periods=n, adjust=False, ignore_na=True).mean().values
        return self._cache[key]

    def volmeasure(self, kind):
        key = ("vm", kind)
        if key not in self._cache:
            c = self.df("c")
            if kind == "bbw":
                v = 4 * c.rolling(20, min_periods=20).std() / c.rolling(20, min_periods=20).mean()
            elif kind == "atrp":
                v = pd.DataFrame(self.atr(14)) / c
            elif kind == "range6":  # 24h high-low range relative to close (daily NR-type compression)
                v = (self.df("h").rolling(6, min_periods=6).max() - self.df("l").rolling(6, min_periods=6).min()) / c
            else:
                raise ValueError(kind)
            self._cache[key] = v
        return self._cache[key]

    def squeeze(self, kind, W, q, A):
        key = ("sq", kind, W, q, A)
        if key not in self._cache:
            v = self.volmeasure(kind)
            rkey = ("rank", kind, W)
            if rkey not in self._cache:
                self._cache[rkey] = v.rolling(W, min_periods=int(W * 0.8)).rank(pct=True)
            rk = self._cache[rkey]
            sq = (rk <= q).astype(float)
            armed = sq.rolling(A, min_periods=1).max().values > 0
            self._cache[key] = armed
        return self._cache[key]

    def donchian(self, D):
        key = ("don", D)
        if key not in self._cache:
            hi = self.df("h").rolling(D, min_periods=D).max().shift(1).values
            lo = self.df("l").rolling(D, min_periods=D).min().shift(1).values
            self._cache[key] = (hi, lo)
        return self._cache[key]

    def universe(self, n, lookback=180):
        """Top-n by trailing 30-day (180 x 4h) quote volume known at the bar open; >= 80% bars present and listed
        >= 30 days. Recomputed once a day (every 6 bars) and held. Row i = universe usable for a signal at close i."""
        key = ("univ", n)
        if key not in self._cache:
            T, N = self.c.shape
            qv = np.nan_to_num(self.qv)
            cs = np.vstack([np.zeros((1, N)), np.cumsum(qv, 0)])
            cnt = np.vstack([np.zeros((1, N)), np.cumsum(~np.isnan(self.c), 0)])
            mask = np.zeros((T, N), bool)
            rank = np.full((T, N), 1e9)
            cur, currank = np.zeros(N, bool), np.full(N, 1e9)
            for i in range(T):
                if i >= lookback and i % 6 == 0:
                    vol = cs[i] - cs[i - lookback]
                    ok = (cnt[i] - cnt[i - lookback]) >= lookback * 0.8
                    ok &= ~((self.first > 0) & (self.first > self.t[i] - lookback * STEP))
                    v = np.where(ok, vol, -1)
                    top = np.argsort(-v)[:n]
                    top = top[v[top] > 0]
                    cur = np.zeros(N, bool); cur[top] = True
                    currank = np.full(N, 1e9); currank[top] = np.arange(len(top))
                mask[i] = cur; rank[i] = currank
            self._cache[key] = (mask, rank)
        return self._cache[key]

    def signals(self, p):
        """(long_sig, short_sig) boolean T x N, evaluated at bar close."""
        key = ("sig", p["sq"], p["W"], p["q"], p["A"], p["D"], p["dir"], p["E"], p["univ"], p["side"])
        if key not in self._cache:
            armed = self.squeeze(p["sq"], p["W"], p["q"], p["A"])
            hi, lo = self.donchian(p["D"])
            c = self.c
            um, _ = self.universe(p["univ"])
            with np.errstate(invalid="ignore"):
                up = (c > hi) & armed & um
                dn = (c < lo) & armed & um
                if p["dir"] == "htf":
                    e = self.ema(p["E"])
                    e_prev = np.vstack([np.full((6, e.shape[1]), np.nan), e[:-6]])
                    up &= (c > e) & (e > e_prev)
                    dn &= (c < e) & (e < e_prev)
            if p["side"] == "long":
                dn = np.zeros_like(dn)
            self._cache[key] = (up, dn)
        return self._cache[key]


def run(P, p, start, end, cost_mult=1.0, lev=1.0, equity0=10_000.0, record=False, rand_seed=None):
    """p: dict(sq, W, q, A, D, dir, E, k, H, univ, side, rp, pc, M).
    rand_seed -> random-coin null: same signal bar times, same number of long/short candidates, same sides, same
    sizing and exits, but the coins are drawn at random from that bar's eligible universe."""
    cost = (FEE + SLIP) * cost_mult
    up, dn = P.signals(p)
    um, urank = P.universe(p["univ"])
    atr = P.atr(14)
    o, h, l, c, qv, fund = P.o, P.h, P.l, P.c, P.qv, P.fund
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    k, H, rp, pc, M = p["k"], p["H"], p["rp"], p["pc"], p["M"]
    rng = np.random.default_rng(rand_seed) if rand_seed is not None else None
    thr = (1.0 / lev - MAINT_MARGIN) if lev > 1.0 / (1 + MAINT_MARGIN) + 1e-9 else None
    eq = equity0
    pos = {}      # j -> [side, units, entry, stop, extreme, open_i, eq0, pnl, last_px, margin]
    pending = []
    curve = [(int(P.t[i0]), eq)]
    trades = []   # (j, side, open_i, close_i, pnl, eq0)
    n_liq = 0; wiped = False

    def close(j, i, px):
        nonlocal eq
        s = pos.pop(j)
        g = s[1] * (px - s[8]) - abs(s[1]) * px * cost
        eq += g; s[7] += g
        trades.append((j, s[0], s[5], i, s[7], s[6]))

    i = i0
    for i in range(i0, i1):
        # 1) fills at this bar's open
        if pending and not wiped:
            gross = sum(abs(s[1]) * s[8] for s in pos.values())
            for j, side in pending:
                if len(pos) >= M or j in pos:
                    continue
                px = o[i, j]; a = atr[i - 1, j]
                if not (px > 0) or not (a > 0):
                    continue
                dist = k * a / px
                notional = min(rp * lev * eq / dist, pc * lev * eq, 0.01 * np.nan_to_num(qv[i - 1, j]),
                               lev * eq - gross)
                if notional <= 0.001 * eq:
                    continue
                units = side * notional / px
                eq -= notional * cost
                stop = px - side * k * a
                pos[j] = [side, units, px, stop, px, i, eq, -notional * cost, px, notional / lev]
                gross += notional
        pending = []
        # 2) manage open positions over bar i
        for j in list(pos.keys()):
            s = pos[j]
            side, units, entry = s[0], s[1], s[2]
            if np.isnan(c[i, j]):
                if i > P.lastvalid[j]:
                    close(j, i, s[8])  # delisted: closed at last close
                continue
            f = -units * s[8] * fund[i, j]
            eq += f; s[7] += f
            oo, hh, ll, cc = o[i, j], h[i, j], l[i, j], c[i, j]
            if np.isnan(oo):
                oo = s[8]
            liqp = entry * (1 - side * thr) if thr is not None else None
            done = False
            if side > 0:
                if liqp is not None and oo <= liqp:
                    done = "liq"
                elif ll <= s[3] and (liqp is None or s[3] > liqp):
                    close(j, i, min(oo, s[3])); done = True
                elif liqp is not None and ll <= liqp:
                    done = "liq"
            else:
                if liqp is not None and oo >= liqp:
                    done = "liq"
                elif hh >= s[3] and (liqp is None or s[3] < liqp):
                    close(j, i, max(oo, s[3])); done = True
                elif liqp is not None and hh >= liqp:
                    done = "liq"
            if done == "liq":
                loss = -s[9] - units * (s[8] - entry)  # total loss of the position = its margin
                eq += loss; s[7] += loss
                pos.pop(j); trades.append((j, side, s[5], i, s[7], s[6])); n_liq += 1
                continue
            if done:
                continue
            g = units * (cc - s[8]); eq += g; s[7] += g; s[8] = cc
            a = atr[i, j]
            if side > 0:
                s[4] = max(s[4], hh)
                if a > 0:
                    s[3] = max(s[3], s[4] - k * a)
            else:
                s[4] = min(s[4], ll)
                if a > 0:
                    s[3] = min(s[3], s[4] + k * a)
            if i - s[5] + 1 >= H:
                close(j, i, cc)
        if eq <= 0:
            eq = 1e-9; wiped = True
            pos.clear()
        curve.append((int(P.t[i]), eq))
        # 3) signals at this bar's close
        if wiped or i + 1 >= i1:
            continue
        free = M - len(pos)
        if free <= 0:
            continue
        L_ = [j for j in np.nonzero(up[i])[0] if j not in pos]
        S_ = [j for j in np.nonzero(dn[i])[0] if j not in pos]
        if not L_ and not S_:
            continue
        if rng is not None:
            pool = [j for j in np.nonzero(um[i] & ~np.isnan(c[i]))[0] if j not in pos]
            pool = [pool[x] for x in rng.permutation(len(pool))]
            nl, ns = len(L_), len(S_)
            cand = [(j, 1) for j in pool[:nl]] + [(j, -1) for j in pool[nl:nl + ns]]
            cand = [cand[x] for x in rng.permutation(len(cand))]
        else:
            cand = sorted([(j, 1) for j in L_] + [(j, -1) for j in S_], key=lambda x: urank[i, x[0]])
        pending = cand[:free]
    for j in list(pos.keys()):
        close(j, i1, pos[j][8])
    out = summary(curve, [x[4] for x in trades])
    out["liquidations"] = n_liq; out["wiped"] = wiped
    out["n_long"] = sum(1 for x in trades if x[1] > 0); out["n_short"] = len(trades) - out["n_long"]
    if record:
        out["_curve"] = curve; out["_trades"] = trades
    return out
