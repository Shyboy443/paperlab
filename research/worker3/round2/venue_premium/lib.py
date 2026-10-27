"""Cross-venue premium signals aligned to the survivorship-free daily panel (research/monthly20/r2_rotation).

Day d of the panel = 00:00..24:00 UTC. Every signal stored at row d uses data of day d only (closes at 24:00 UTC of day d);
the rotation engine reads score[i] at the open of day i, so signals are SHIFTED by one row (score[i] = sig[i-1]).

Premiums (all daily, from data that is complete at 24:00 UTC of day d):
  CBP[d]   Coinbase premium BTC: mean over the 24 hours of day d of (Coinbase BTC-USD close / Binance BTCUSDT close - 1).
  CBPE[d]  same for ETH.
  KP[d]    Kimchi premium BTC: Upbit KRW-BTC daily VWAP / (USDKRW * Binance BTCUSDT daily VWAP) - 1, USDKRW = Yahoo KRW=X
           close of the most recent FX date STRICTLY BEFORE day d (an extra day of lag; Yahoo FX dates are London days).
  EP[d,j]  coin excess (kimchi) premium, no FX needed: log(Upbit VWAP_j / Binance VWAP_j) - log(same for BTC).
VWAPs: Upbit candle_acc_trade_price / candle_acc_trade_volume; Binance sum(quote vol)/sum(base vol) of the day's 1h bars.
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END, DATA_FROM, DAY, HOUR, PROJECT  # noqa

DD = PROJECT / "data" / "w3r2_venue_premium"
V07 = PROJECT / "data" / "monthly20_07_pump_scanner" / "1h"
D = build()
P = E.Panel(D)
T, N = P.c.shape
J = {s: i for i, s in enumerate(P.syms)}
BTC, ETH = J["BTCUSDT"], J["ETHUSDT"]
CACHE = DD / "signals.npz"


def _day(datestr):
    return int((int(datetime.fromisoformat(datestr).replace(tzinfo=timezone.utc).timestamp() * 1000) - DATA_FROM) // DAY)


def _binance_vwap_and_hourly(sym):
    rows = json.loads((V07 / f"{sym}.json").read_text())
    a = np.array([r[:7] for r in rows], float)
    d = ((a[:, 0].astype(np.int64) - DATA_FROM) // DAY).astype(int)
    ok = (d >= 0) & (d < T)
    qv = np.bincount(d[ok], a[ok, 6], minlength=T)[:T]; bv = np.bincount(d[ok], a[ok, 5], minlength=T)[:T]
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(bv > 0, qv / bv, np.nan)
    return vw, a


def _mult(sym):
    b = sym[:-4]
    for pre, m in (("1000000", 1e6), ("10000", 1e4), ("1000", 1e3), ("1M", 1e6)):
        if b.startswith(pre) and len(b) > len(pre) + 1:
            return b[len(pre):], m
    return b, 1.0


def build_signals(force=False):
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=False)
        return {k: z[k] for k in z.files}
    mk = set(json.loads((DD / "upbit_markets.json").read_text()))
    # USD/KRW, lagged: value for day d = last Yahoo close with date < d
    fxr = json.loads((DD / "fx_yahoo_krw.json").read_text())
    fx = np.full(T, np.nan)
    fd = [(int((t * 1000 - DATA_FROM) // DAY), c) for t, c in fxr]
    last = np.nan; k = 0
    fd.sort()
    for d in range(T):
        while k < len(fd) and fd[k][0] < d:
            last = fd[k][1]; k += 1
        fx[d] = last
    upv = {}
    EP_raw = np.full((T, N), np.nan)
    LR = np.full((T, N), np.nan)  # log(upbit vwap / binance vwap), KRW per USDT units
    for j, s in enumerate(P.syms):
        base, m = _mult(s)
        if f"KRW-{base}" not in mk or not (DD / "upbit" / f"KRW-{base}.json").exists():
            continue
        up = json.loads((DD / "upbit" / f"KRW-{base}.json").read_text())
        u = np.full(T, np.nan)
        for r in up:
            d = _day(r[0])
            if 0 <= d < T and r[6] > 0 and r[5] > 0:
                u[d] = r[5] / r[6]
        if not np.isfinite(u).any():
            continue
        vw, _ = _binance_vwap_and_hourly(s)
        with np.errstate(invalid="ignore", divide="ignore"):
            LR[:, j] = np.log(u * m / vw)
        upv[s] = int(np.isfinite(LR[:, j]).sum())
    KP = np.exp(LR[:, BTC] - np.log(fx)) - 1
    EP_raw = LR - LR[:, [BTC]]
    # sanity: drop days where the coin's ratio is > 40% away from BTC's (token swaps / redenominations / wrong mapping)
    EP = np.where(np.abs(EP_raw) < np.log(1.4), EP_raw, np.nan)
    # Coinbase hourly premiums
    def cbp(prod, sym):
        cb = json.loads((DD / "coinbase" / f"{prod}_1h.json").read_text())
        cbc = {int(x[0]) * 1000: x[4] for x in cb}
        _, a = _binance_vwap_and_hourly(sym)
        # Binance 1h row open time t has close at t+1h; Coinbase candle time = open time too
        prem = np.array([cbc.get(int(t), np.nan) / c - 1 for t, c in zip(a[:, 0], a[:, 4])])
        d = ((a[:, 0].astype(np.int64) - DATA_FROM) // DAY).astype(int)
        ok = (d >= 0) & (d < T) & np.isfinite(prem)
        sm = np.bincount(d[ok], prem[ok], minlength=T)[:T]; n = np.bincount(d[ok], minlength=T)[:T]
        return np.where(n >= 20, sm / np.maximum(n, 1), np.nan)
    out = dict(KP=KP, EP=EP, EP_raw=EP_raw, LR=LR, fx=fx, CBP=cbp("BTC-USD", "BTCUSDT"), CBPE=cbp("ETH-USD", "ETHUSDT"),
               has_up=np.array([s in upv for s in P.syms]))
    np.savez_compressed(CACHE, **out)
    return out


def shift(x):
    """score[i] = x[i-1]: day i-1's signal is known at the open of day i."""
    y = np.full_like(x, np.nan)
    y[1:] = x[:-1]
    return y


def ema(x, span):
    a = 2 / (span + 1)
    out = np.full_like(x, np.nan); prev = None
    for i in range(len(x)):
        v = x[i]
        if prev is None or not np.all(np.isfinite(prev)):
            prev = np.where(np.isfinite(v), v, np.nan) if np.ndim(v) else (v if np.isfinite(v) else None)
        else:
            prev = np.where(np.isfinite(v), a * v + (1 - a) * prev, prev)
        out[i] = prev if prev is not None else np.nan
    return out


def rmean(x, L):
    """trailing mean over rows i-L+1..i ignoring NaN; needs >= 0.7 L values."""
    x2 = np.atleast_2d(x.T).T if x.ndim == 1 else x
    ok = np.isfinite(x2).astype(float); z = np.where(ok > 0, x2, 0.0)
    cs = np.vstack([np.zeros((1, x2.shape[1])), np.cumsum(z, 0)]); cn = np.vstack([np.zeros((1, x2.shape[1])), np.cumsum(ok, 0)])
    out = np.full(x2.shape, np.nan)
    s = cs[L:] - cs[:-L]; n = cn[L:] - cn[:-L]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[L - 1:] = np.where(n >= 0.7 * L, s / n, np.nan)
    return out[:, 0] if x.ndim == 1 else out


def rstd(x, L):
    m = rmean(x, L); m2 = rmean(x * x, L)
    return np.sqrt(np.maximum(m2 - m * m, 1e-12))


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


# ---------- timing engine for BTC / ETH (1x, long/flat or long/short), daily decisions at 00:00 UTC ----------
def run_timing(pos, assets, start, end, cost_mult=1.0, record=False):
    """pos[i] in [-1, 1] = target exposure decided from data up to the close of day i-1 (already shifted);
    equal split over `assets`; fill at open of day i; funding charged; fee+slip on traded notional."""
    from common import FEE, SLIP, summary
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    eq = 10_000.0; curve = [(int(P.t[i0]), eq)]; w = np.zeros(len(assets)); trades = []; ntr = 0
    for i in range(i0, i1):
        tgt = np.nan_to_num(pos[i]) * np.ones(len(assets)) / len(assets)
        dw = np.abs(tgt - w).sum()
        if dw > 1e-9:
            ntr += 1
        eq -= eq * dw * cost; w = tgt
        r = 0.0
        for a, j in enumerate(assets):
            r += w[a] * (P.c[i, j] / P.o[i, j] - 1) - w[a] * P.fund[i, j]
        eq *= 1 + r
        # weights drift ignored within a day (rebalanced daily to target) -- small
        curve.append((int(P.t[i] + DAY), eq))
    out = summary(curve)
    out["trades"] = ntr
    if record:
        out["_curve"] = curve
    return out
