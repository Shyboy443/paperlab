"""Vectorised daily portfolio simulator for the round-2 trend checks (T1 EMA+ADX, T2 momentum).

Rules (fixed before any check was run):
- Daily UTC bars built from 1h archive bars; a signal comes from the closed day d-1 and fills at day d's open.
- Universe: at each month start, the top-N USDT perps (delisted ones included, non-crypto excluded) by the
  trailing 30-day quote volume, at least 24 of those 30 days with a bar. Known before the month starts.
- Sizing ("equal risk", 1x gross): weight_i = (1/vol_i) / sum over the month's universe of (1/vol_j), vol = 30-day
  annualised realised vol of daily log returns at the previous close; a single weight is capped at 2.5/N.
  With every universe coin in a position, gross exposure = 1.0 x equity (x L for the leverage table).
- A position opens / closes / flips when its side changes; on rebalance days (daily / weekly Monday /
  monthly 1st) open positions are resized to target. Size capped at 1% of the previous day's quote volume.
- Costs: (FEE + SLIP) x cost_mult per side on traded notional; funding: units x close x sum of that day's rates.
- Liquidation (isolated margin per position, leverage L): when the day's low/high crosses
  entry x (1 -/+ (1/L - 0.5%)), the whole margin (notional/L at entry) is lost.
- A delisted coin (no later bars) is closed at its last close, with costs.
"""
from __future__ import annotations
import math, sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_trend")
from common import FEE, SLIP, MAINT_MARGIN, DEV_START, DEV_END, TEST_START, TEST_END, utc  # noqa
import data_r2

DAY = 86_400_000


class Panel:
    def __init__(self):
        P = data_r2.load()
        self.syms_all = [str(s) for s in P["syms"]]
        self.day = P["day"].astype(np.int64)
        O, H, L, C, Q, F = P["O"], P["H"], P["L"], P["C"], P["QV"], P["FUND"]
        nd = len(self.day)
        self.i0 = int(np.searchsorted(self.day, DEV_START))
        self.iend = int(np.searchsorted(self.day, TEST_END))
        # month starts in the simulation window
        dts = [datetime.fromtimestamp(t / 1000, timezone.utc) for t in self.day]
        self.mkey = np.array([d.strftime("%Y-%m") for d in dts])
        self.weekday = np.array([d.weekday() for d in dts])
        self.dom = np.array([d.day for d in dts])
        # 30-day trailing quote volume score at every month start (all coins)
        self.month_starts = [i for i in range(self.i0, self.iend) if self.dom[i] == 1]
        self.score = {}
        for i in self.month_starts:
            q = Q[i - 30:i]
            s = q.sum(0)
            s[(q > 0).sum(0) < 24] = 0
            self.score[i] = s
        # pool = coins ever in the top 80: keeps arrays small; every universe used here is inside it
        pool = set()
        for i, s in self.score.items():
            pool |= set(np.argsort(-s)[:80][s[np.argsort(-s)[:80]] > 0].tolist())
        self.cols = np.array(sorted(pool))
        c = self.cols
        self.syms = [self.syms_all[j] for j in c]
        self.O, self.H, self.L, self.C, self.Q, self.F = O[:, c], H[:, c], L[:, c], C[:, c], Q[:, c], F[:, c]
        self.score = {i: s[c] for i, s in self.score.items()}
        self.N = len(c)
        self.has = ~np.isnan(self.O)
        last = np.full(self.N, -1)
        for j in range(self.N):
            idx = np.where(self.has[:, j])[0]
            last[j] = idx[-1] if len(idx) else -1
        self.last = last
        Cf = pd.DataFrame(self.C).ffill()
        self.Cf = Cf.values
        self.Hf = pd.DataFrame(self.H).ffill().values
        self.Lf = pd.DataFrame(self.L).ffill().values
        self.nbars = np.cumsum(self.has, axis=0)  # bars seen so far
        lr = np.log(Cf).diff()
        lr[~pd.DataFrame(self.has)] = np.nan
        self.vol = (lr.rolling(30, min_periods=20).std() * math.sqrt(365)).values
        self._cache = {}

    # ------------------------------------------------------------ universe
    def universe(self, n, drop=()):
        key = ("uni", n, tuple(sorted(drop)))
        if key in self._cache:
            return self._cache[key]
        U = np.zeros((len(self.day), self.N), bool)
        dropj = [self.syms.index(s) for s in drop]
        ms = self.month_starts + [self.iend]
        for a, b in zip(ms[:-1], ms[1:]):
            s = self.score[a].copy()
            s[dropj] = 0
            top = np.argsort(-s)[:n]
            top = top[s[top] > 0]
            U[a:b, top] = True
        self._cache[key] = U
        return U

    # ------------------------------------------------------------ indicators (per coin, own history)
    def ema_sig(self, f, s):
        key = ("ema", f, s)
        if key not in self._cache:
            C = pd.DataFrame(self.Cf)
            ef = C.ewm(span=f, adjust=False).mean().values
            es = C.ewm(span=s, adjust=False).mean().values
            sig = np.where(ef > es, 1, -1)
            sig[self.nbars < s] = 0
            sig[np.isnan(self.Cf)] = 0
            self._cache[key] = sig.astype(np.int8)
        return self._cache[key]

    def mom_sig(self, n):
        key = ("mom", n)
        if key not in self._cache:
            C = self.Cf
            prev = np.vstack([np.full((n, self.N), np.nan), C[:-n]])
            sig = np.where(C > prev, 1, np.where(C < prev, -1, 0))
            sig[(self.nbars <= n) | np.isnan(prev) | np.isnan(C)] = 0
            self._cache[key] = sig.astype(np.int8)
        return self._cache[key]

    def adx(self, n=14):
        key = ("adx", n)
        if key in self._cache:
            return self._cache[key]
        out = np.zeros_like(self.Cf)
        for j in range(self.N):
            idx = np.where(~np.isnan(self.Cf[:, j]))[0]
            if len(idx) < 2 * n + 2:
                continue
            a0 = idx[0]
            h, l, c = self.Hf[a0:, j], self.Lf[a0:, j], self.Cf[a0:, j]
            M = len(c)
            tr = np.zeros(M); pdm = np.zeros(M); mdm = np.zeros(M)
            up = h[1:] - h[:-1]; dn = l[:-1] - l[1:]
            pdm[1:] = np.where((up > dn) & (up > 0), up, 0)
            mdm[1:] = np.where((dn > up) & (dn > 0), dn, 0)
            tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
            atr, ap, am = tr[1:n + 1].sum(), pdm[1:n + 1].sum(), mdm[1:n + 1].sum()
            dxs, a = [], None
            res = np.zeros(M)
            for i in range(n + 1, M):
                atr += tr[i] - atr / n; ap += pdm[i] - ap / n; am += mdm[i] - am / n
                pdi = 100 * ap / atr if atr else 0.0
                mdi = 100 * am / atr if atr else 0.0
                dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
                if a is None:
                    dxs.append(dx)
                    if len(dxs) == n:
                        a = sum(dxs) / n
                else:
                    a = (a * (n - 1) + dx) / n
                res[i] = a if a is not None else 0.0
            out[a0:, j] = res
        self._cache[key] = out
        return out

    # ------------------------------------------------------------ strategy -> side & weight matrices
    def build(self, cfg, drop=()):
        """Returns S (desired side at day d's open, from close d-1) and W (weights), both day x coin."""
        n = cfg["uni"]
        U = self.universe(n, drop)
        if cfg["kind"] == "ema":
            raw = self.ema_sig(cfg["fast"], cfg["slow"]).copy()
            if cfg.get("adx"):
                raw[self.adx() < cfg["adx"]] = 0
        else:
            raw = self.mom_sig(cfg["look"])
        S = np.zeros_like(raw)
        S[1:] = raw[:-1]
        S = S * U * self.has
        iv = np.zeros_like(self.vol)
        v = np.vstack([np.full((1, self.N), np.nan), self.vol[:-1]])
        ok = U & ~np.isnan(v) & (v > 0)
        iv[ok] = 1 / v[ok]
        W = iv / np.maximum(iv.sum(1, keepdims=True), 1e-12)
        W = np.minimum(W, 2.5 / n)
        S[~ok] = 0
        return S.astype(np.int8), W

    def rebal_mask(self, freq):
        if freq == "daily":
            return np.ones(len(self.day), bool)
        if freq == "weekly":
            return self.weekday == 0
        return self.dom == 1

    # ------------------------------------------------------------ simulation
    def sim(self, S, W, lev=1.0, cost_mult=1.0, rebal="monthly", trades=False, eq0=10_000.0):
        c_side = (FEE + SLIP) * cost_mult
        R = self.rebal_mask(rebal)
        O, H, L, C, Q, F = self.O, self.H, self.L, self.C, self.Q, self.F
        N = self.N
        u = np.zeros(N); m = np.zeros(N); entry = np.zeros(N); side = np.zeros(N, np.int8)
        tp = np.zeros(N); tstart = np.zeros(N, int)
        E = eq0
        curve = [(DEV_START, eq0)]
        book = []
        nliq = 0
        mm = 1 / lev - MAINT_MARGIN

        def close(js, px, d):
            nonlocal E
            if not len(js):
                return
            pnl = u[js] * (px - m[js]) - np.abs(u[js]) * px * c_side
            E += pnl.sum()
            if trades:
                for k, j in enumerate(js):
                    book.append((self.syms[j], int(tstart[j]), d, tp[j] + pnl[k]))
            u[js] = 0; side[js] = 0; tp[js] = 0

        for d in range(self.i0, self.iend):
            has = self.has[d]
            # delisted coins still held: close at the last close
            gone = np.where((side != 0) & ~has & (self.last < d))[0]
            close(gone, m[gone], d)
            o = O[d]
            hj = np.where(has & (side != 0))[0]
            mv = u[hj] * (o[hj] - m[hj]); E += mv.sum(); tp[hj] += mv; m[hj] = o[hj]
            if E <= 0:
                E = 0.0
                curve.append((int(self.day[d]) + DAY - 1, 1e-9))
                break
            s = S[d]
            act = has
            want = np.where(act, s, side)
            chg = np.where(act & (want != side))[0]
            # close changed positions
            cj = chg[side[chg] != 0]
            close(cj, o[cj], d)
            # target notionals
            tgt = lev * E * W[d] * want
            cap = 0.01 * Q[d - 1]
            tgt = np.sign(tgt) * np.minimum(np.abs(tgt), cap)
            oj = chg[want[chg] != 0]
            oj = oj[np.abs(tgt[oj]) >= 10]
            if len(oj):
                newu = tgt[oj] / o[oj]
                cost = np.abs(tgt[oj]) * c_side
                E -= cost.sum()
                u[oj] = newu; m[oj] = o[oj]; entry[oj] = o[oj]; side[oj] = want[oj]
                tp[oj] = -cost; tstart[oj] = d
            if R[d]:
                rj = np.where(act & (side != 0) & (want == side))[0]
                rj = np.setdiff1d(rj, oj)
                if len(rj):
                    newu = tgt[rj] / o[rj]
                    du = newu - u[rj]
                    cost = np.abs(du) * o[rj] * c_side
                    E -= cost.sum(); tp[rj] -= cost
                    grow = np.abs(newu) > np.abs(u[rj])
                    # average entry for liquidation when the position grows
                    entry[rj] = np.where(grow, (u[rj] * entry[rj] + du * o[rj]) / np.where(newu == 0, 1, newu),
                                         entry[rj])
                    u[rj] = newu
            # liquidation on the day's extreme
            hj = np.where(has & (side != 0))[0]
            if len(hj):
                liq_px = entry[hj] * (1 - side[hj] * mm)
                hit = np.where(side[hj] == 1, L[d, hj] <= liq_px, H[d, hj] >= liq_px)
                if hit.any():
                    lj = hj[hit]
                    px = entry[lj] * (1 - side[lj] / lev)  # whole margin gone
                    pnl = u[lj] * (px - m[lj])
                    E += pnl.sum(); nliq += len(lj)
                    if trades:
                        for k, j in enumerate(lj):
                            book.append((self.syms[j], int(tstart[j]), d, tp[j] + pnl[k]))
                    u[lj] = 0; side[lj] = 0; tp[lj] = 0
            # mark to close + funding
            hj = np.where(has & (side != 0))[0]
            c = C[d]
            mv = u[hj] * (c[hj] - m[hj]) - u[hj] * c[hj] * F[d, hj]
            E += mv.sum(); tp[hj] += mv; m[hj] = c[hj]
            curve.append((int(self.day[d]) + DAY - 1, max(E, 1e-9)))
        # close the rest at the end
        rest = np.where(side != 0)[0]
        close(rest, m[rest], self.iend)
        curve[-1] = (curve[-1][0], max(E, 1e-9))
        return curve, book, nliq
