"""Worker 2 rule P on Binance SPOT hourly data (2017-08..2021-06). Logic copied line-for-line from
worker2/session/core.py (signals, run), r4_09_session/base.py (hvol) and r2_rotation/engine_r2.py (universe_mask).
Only the data source differs: spot klines from data.binance.vision instead of USDT perps; funding = 0 (no perp)."""
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

CACHE = Path(r"D:/Projects/BinanceBOt/paperlab/data/w2_backfill")
HOUR, DAY = 3_600_000, 86_400_000
FEE, SLIP = 0.0005, 0.0002


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


DATA_FROM, DATA_END = utc(2017, 8, 1), utc(2021, 7, 1)
P = dict(H=16, k=2.0, X=26, K=5, W=480, uni="ALT", mode="mom", sig_ref=0.01154)   # frozen rule P
STABLE_FIAT = {"USDCUSDT", "TUSDUSDT", "PAXUSDT", "USDPUSDT", "BUSDUSDT", "USDSUSDT", "USDSBUSDT", "DAIUSDT",
               "FDUSDUSDT", "USTUSDT", "SUSDUSDT", "EURUSDT", "GBPUSDT", "AUDUSDT", "AEURUSDT", "EURIUSDT", "USD1USDT",
               "USDEUSDT", "XUSDUSDT", "BFUSDUSDT", "RLUSDUSDT", "TRYUSDT", "BRLUSDT", "RUBUSDT", "UAHUSDT", "NGNUSDT",
               "ZARUSDT", "IDRTUSDT", "BIDRUSDT", "BKRWUSDT", "VAIUSDT", "USDTUSDT"}


def excluded(s):
    b = s[:-4]
    if s in STABLE_FIAT:
        return True
    if b in ("BULL", "BEAR") or (len(b) > 2 and any(b.endswith(k) for k in ("UP", "DOWN", "BULL", "BEAR"))
                                 and s not in ("JUPUSDT", "SYRUPUSDT")):
        return True
    return False


def load(iv, s):
    p = CACHE / iv / f"{s}.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    if not r:
        return None
    a = np.array(r, dtype=float)
    return a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < DATA_END)]


def daily_panel():
    syms = [s for s in json.loads((CACHE / "list.json").read_text()) if not excluded(s)]
    t = np.arange(DATA_FROM, DATA_END, DAY, dtype=np.int64)
    keep, cols = [], []
    for s in syms:
        a = load("1d", s)
        if a is None or not len(a):
            continue
        keep.append(s); cols.append(a)
    T, N = len(t), len(keep)
    c, qv = np.full((T, N), np.nan), np.full((T, N), np.nan)
    first = np.zeros(N, np.int64)
    for j, a in enumerate(cols):
        i = ((a[:, 0].astype(np.int64) - DATA_FROM) // DAY).astype(int)
        c[i, j] = a[:, 4]; qv[i, j] = a[:, 6]; first[j] = int(a[0, 0])
    return t, keep, c, qv, first


def universe_mask(t, c, qv_, onboard, n=40, lookback_days=30):
    """copy of engine_r2.universe_mask"""
    T, N = c.shape
    qv = np.nan_to_num(qv_)
    cs = np.vstack([np.zeros((1, N)), np.cumsum(qv, 0)])
    cnt = np.vstack([np.zeros((1, N)), np.cumsum(~np.isnan(c), 0)])
    mask = np.zeros((T, N), bool)
    for d in range(lookback_days, T):
        vol = cs[d] - cs[d - lookback_days]
        ok = (cnt[d] - cnt[d - lookback_days] >= lookback_days * 0.8)
        ok &= ~((onboard > 0) & (onboard > t[d] - lookback_days * DAY))
        v = np.where(ok, vol, -1)
        top = np.argsort(-v)[:n]
        top = top[v[top] > 0]
        mask[d, top] = True
    return mask


class Panel:
    def __init__(self, need_1h=True):
        td, dsyms, dc, dqv, first = daily_panel()
        U40 = universe_mask(td, dc, dqv, first, 40)
        cols = np.where(U40.any(0))[0]
        self.syms = [dsyms[j] for j in cols]
        self.U40 = U40[:, cols]; self.td = td
        if not need_1h:
            return
        self.th = np.arange(DATA_FROM, DATA_END, HOUR, dtype=np.int64)
        T, N = len(self.th), len(cols)
        arr = {k: np.full((T, N), np.nan) for k in ("o", "h", "l", "c", "qv")}
        self.missing_1h = []
        for j, s in enumerate(self.syms):
            a = load("1h", s)
            if a is None:
                self.missing_1h.append(s); continue
            i = ((a[:, 0].astype(np.int64) - DATA_FROM) // HOUR).astype(int)
            for k, ci in (("o", 1), ("h", 2), ("l", 3), ("c", 4), ("qv", 6)):
                arr[k][i, j] = a[:, ci]
        self.O, self.Hh, self.L, self.C, self.QV = arr["o"], arr["h"], arr["l"], arr["c"], arr["qv"]
        J = {s: i for i, s in enumerate(self.syms)}
        self.BTC, self.ETH = J["BTCUSDT"], J["ETHUSDT"]
        self.ALT_ELIG = [np.array([j for j in np.where(self.U40[d])[0] if j not in (self.BTC, self.ETH)], dtype=int)
                         for d in range(len(td))]
        O = self.O
        LR = np.full_like(O, np.nan); LR[:-1] = np.log(O[1:] / O[:-1])
        _x = np.nan_to_num(LR); _n = (~np.isnan(LR)).astype(float)
        self._c1 = np.vstack([np.zeros((1, N)), np.cumsum(_x, 0)])
        self._c2 = np.vstack([np.zeros((1, N)), np.cumsum(_x * _x, 0)])
        self._cn = np.vstack([np.zeros((1, N)), np.cumsum(_n, 0)])

    def hvol(self, t, W=480):
        """copy of base.hvol: std of hourly log returns over bars t-W-1 .. t-2."""
        a, b = np.maximum(t - W - 1, 0), np.maximum(t - 1, 0)
        n = self._cn[b] - self._cn[a]; s1 = self._c1[b] - self._c1[a]; s2 = self._c2[b] - self._c2[a]
        with np.errstate(invalid="ignore", divide="ignore"):
            v = s2 / n - (s1 / n) ** 2
        return np.where(n > W * 0.8, np.sqrt(np.maximum(v, 0)), np.nan)

    def dayidx(self, ms):
        return int((ms - DATA_FROM) // DAY)

    def signals(self, p, d_from, d_to):
        """copy of core.signals (uni=ALT, mode=mom, size m = min(1, sig_ref/sigma))."""
        H_, k, X, W = p["H"], p["k"], p["X"], p["W"]
        V = self.hvol(np.arange(d_from * 24, min(d_to * 24, len(self.th)), 24), W)
        O, C = self.O, self.C
        rows = []
        for d in range(d_from, d_to):
            b = d * 24
            if b + X >= len(self.th):
                break
            js = self.ALT_ELIG[d]
            if len(js) == 0:
                continue
            with np.errstate(invalid="ignore", divide="ignore"):
                z = np.log(C[b + H_ - 1, js] / O[b, js]) / (V[d - d_from, js] * np.sqrt(H_))
            ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[b + H_, js])
            for j, zz in zip(js[ok], z[ok]):
                sig = V[d - d_from, j]
                m = float(min(1.0, p["sig_ref"] / sig)) if np.isfinite(sig) and sig > 0 else 1.0
                rows.append((d, b + H_, b + X, int(j), np.sign(zz) * (1 if p["mode"] == "mom" else -1), m))
        return rows

    def run(self, rows, d_from, d_to, K, cost_mult=1.0, eq0=10_000.0, liq_cap=True):
        """copy of core.run at lev=1 with funding = 0 (spot data, no funding history)."""
        O, C, QV, td = self.O, self.C, self.QV, self.td
        by_day = {}
        for r in rows:
            by_day.setdefault(r[0], []).append(r)
        eq = eq0
        curve = [(int(td[d_from]), eq)]
        trades = []
        cost = (FEE + SLIP) * cost_mult
        for d in range(d_from, d_to):
            rs = by_day.get(d, [])
            if rs:
                n = len(rs)
                w = 1.0 / max(n, K)
                pnl = 0.0
                for r in rs:
                    _, ie, ix, j, s = r[:5]
                    m = r[5]
                    notional = eq * w * m
                    if liq_cap:
                        notional = min(notional, 0.01 * QV[ie, j]) if np.isfinite(QV[ie, j]) else notional
                    px = O[ix, j]
                    if not np.isfinite(px):
                        seg = C[ie:ix, j]; fin = seg[np.isfinite(seg)]
                        px = fin[-1] if len(fin) else O[ie, j]
                    g = s * (px / O[ie, j] - 1)
                    ret = g - 2 * cost
                    p_ = notional * ret
                    pnl += p_
                    trades.append((d, j, s, p_, p_ / eq, notional / eq, ret))
                eq = max(eq + pnl, 0.0)
            curve.append((int(td[d] + DAY - 1), eq))
            if eq <= 0:
                break
        return curve, trades


def monthly_returns(curve):
    """copy of common.monthly_returns"""
    out, start_eq, cur_key, last = {}, None, None, None
    for t, e in curve:
        key = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m")
        if key != cur_key:
            if cur_key is not None:
                out[cur_key] = (last / start_eq - 1) * 100
            cur_key, start_eq = key, (last if last is not None else e)
        last = e
    if cur_key is not None:
        out[cur_key] = (last / start_eq - 1) * 100
    return out


def max_dd(curve):
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak if peak > 0 else 1)
    return round(dd * 100, 2)


def mstats(months, dd=None, trades=None):
    v = np.array(list(months.values()))
    n = len(v)
    g = np.prod(1 + v / 100)
    out = {"n_months": n, "months_up": int((v > 0).sum()), "mean_month": round(float(v.mean()), 2),
           "median_month": round(float(np.median(v)), 2), "geo_month": round(float((g ** (1 / n) - 1) * 100), 2),
           "worst_month": round(float(v.min()), 2), "best_month": round(float(v.max()), 2),
           "net_pct": round(float((g - 1) * 100), 2)}
    if dd is not None:
        out["max_dd_pct"] = dd
    if trades is not None:
        out["trades"] = trades; out["trades_per_month"] = round(trades / n, 1)
    return out


def yearly(months):
    out = {}
    for k_, v in months.items():
        out.setdefault(k_[:4], 1.0)
        out[k_[:4]] *= 1 + v / 100
    return {y: round((g - 1) * 100, 2) for y, g in out.items()}
