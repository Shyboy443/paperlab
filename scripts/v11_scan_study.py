"""V11 SCAN study: do bots that scan ALL the liquid crypto coins find trades with an edge? (docs/V11_PROTOCOL.md)

    python scripts/v11_scan_data.py        # universe + 95 days of 1m bars (cached)
    python scripts/v11_scan_study.py

Universe: the top 30 crypto perps on Bybit by 30-day median turnover (scripts/v11_scan_data.py). The first 5 days warm
the indicators; the remaining ~90 days are split into DISCOVERY (first half) and CONFIRMATION (second half).

Families (all fixed before the run; each scans every coin at every decision bar and ranks what it finds):

  V8P  V8.1's pullback rule on every coin          5m, stop 0.45-1.2%, 1.5R, 45 min      (V8 rules, scanned)
  V8B  V8.2's breakout rule on every coin          5m, stop 0.45-1.2%, 1.5R, 45 min
  V8V  V8.3's VWAP snap rule on every coin         5m, stop 0.45-1.2%, 1.5R, 45 min
  RSB  relative-strength breakout: a top-20% coin (4h return vs the universe) breaks its 4h high on 1.5x volume
       with the 1h trend (mirror for the weakest coins)                    15m, stop 0.8-3%, 2R, 4 h
  RSP  relative-strength pullback: a top-20% coin in a 15m + 1h uptrend dips to its 15m EMA20 and resumes
       (mirror for the weakest)                                             15m, stop 0.8-3%, 2R, 4 h
  VSH  volume shock: a 5m bar on >= 5x its day's median volume that moves >= 1.2 ATR and closes near its extreme;
       continuation                                                         5m, stop 0.6-3%, 2R, 2 h
  CAP  capitulation snap: a 1h fall of >= 4 ATR(5m) on climax volume, then a reversal bar (mirror: blow-off top)
                                                                            5m, stop 0.6-3%, 2R, 2 h
  BTL  BTC lead: BTC's 5m move >= 2.5 sigma, a coin that lagged its beta by >= 1.5 sigma follows
                                                                            5m, stop 1.5 ATR (0.6-2%), 1.5R, 30 min

Execution (the V8 rule, conservative): market entry at the OPEN of the 1m bar starting 60 s after the decision; stops
and targets are the signal's levels; a bar that touches both is a STOP; a bar that opens beyond a level fills at the
open; the time stop exits at the close of the last minute. Costs: Bybit taker 0.055% per side plus each coin's half
spread (max(1 bp, 1.5 x the half spread observed on its book at study time)) per side. R = the planned risk
|signal close - stop|.

Scanner book: at most 3 positions at once, one per coin, a one-bar cooldown per coin after an exit; at each decision
bar the candidates are taken best score first. Two versions per family: ALL (any candidate) and TOP (only scores at or
above the family's DISCOVERY-half 67th percentile; the cut is then applied unchanged to CONFIRMATION).

DECISION RULE (fixed before running): a variant PASSES if its net R per trade is > 0 and its profit factor >= 1.1 in
BOTH halves, with >= 2 trades a day. Passing variants become the V11 forward scanner bots. If none passes, the forward
field is built from the best DISCOVERY variants, labelled as unproven, and nothing is claimed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data" / "v11_scan"
DAY = 86_400_000
MIN = 60_000
TAKER = 0.00055
WARM_DAYS = 5
BOOK_SLOTS = 3


# -- data ------------------------------------------------------------------------------------------------------
def load() -> dict:
    uni = json.loads((DATA / "universe.json").read_text())
    start, end = int(uni["start_ms"]), int(uni["end_ms"])
    syms = [u["symbol"] for u in uni["coins"]]
    if "BTCUSDT" not in syms:
        syms.append("BTCUSDT")
    n = (end - start) // MIN
    O, H, L, C, V = (np.full((len(syms), n), np.nan) for _ in range(5))
    for k, s in enumerate(syms):
        a = np.load(DATA / f"{s}.npy")
        idx = ((a[:, 0] - start) // MIN).astype(int)
        ok = (idx >= 0) & (idx < n)
        idx, a = idx[ok], a[ok]
        O[k, idx], H[k, idx], L[k, idx], C[k, idx], V[k, idx] = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    for k in range(len(syms)):                     # a silent minute: flat at the last close, no volume
        c = pd.Series(C[k]).ffill().bfill().to_numpy()
        miss = np.isnan(C[k])
        O[k, miss] = H[k, miss] = L[k, miss] = C[k, miss] = c[miss]
        V[k, miss] = 0.0
    return {"syms": syms, "start": start, "end": end, "O": O, "H": H, "L": L, "C": C, "V": V,
            "hs": half_spreads(syms)}


def half_spreads(syms: list[str]) -> np.ndarray:
    f = DATA / "spreads.json"
    if f.exists():
        got = json.loads(f.read_text())
    else:
        req = urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear",
                                     headers={"User-Agent": "paperlab-research"})
        rows = json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]["list"]
        got = {}
        for r in rows:
            b, a = float(r.get("bid1Price") or 0), float(r.get("ask1Price") or 0)
            if b > 0 and a > b:
                got[r["symbol"]] = (a - b) / (a + b)
        f.write_text(json.dumps({s: got.get(s) for s in syms}))
    return np.array([max(0.0001, 1.5 * float(got.get(s) or 0.0001)) for s in syms])


def bars(d: dict, k: int) -> dict:
    """k-minute bars from the 1m grid (the grid starts on a UTC day, so every window is aligned)."""
    n = d["C"].shape[1] // k
    sl = slice(0, n * k)
    r = lambda a: a[:, sl].reshape(a.shape[0], n, k)
    return {"k": k, "O": r(d["O"])[:, :, 0], "H": r(d["H"]).max(2), "L": r(d["L"]).min(2), "C": r(d["C"])[:, :, -1],
            "V": r(d["V"]).sum(2)}


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.DataFrame(x.T).ewm(span=n, adjust=False).mean().to_numpy().T


def atr(b: dict, n: int = 14) -> np.ndarray:
    pc = np.concatenate([b["C"][:, :1], b["C"][:, :-1]], axis=1)
    tr = np.maximum(b["H"] - b["L"], np.maximum(abs(b["H"] - pc), abs(b["L"] - pc)))
    return pd.DataFrame(tr.T).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy().T


def roll(x: np.ndarray, n: int, how: str, shift: int = 1) -> np.ndarray:
    """Rolling statistic over the n bars BEFORE each bar (shift=1) -- never including the bar itself."""
    df = pd.DataFrame(x.T).shift(shift).rolling(n, min_periods=n)
    return getattr(df, how)().to_numpy().T


def trend(b: dict) -> np.ndarray:
    """ctx_trend: +1 up / -1 down / 0 flat -- EMA20 vs EMA50, the EMA50's 5-bar slope, close vs EMA50."""
    f, s = ema(b["C"], 20), ema(b["C"], 50)
    s5 = np.concatenate([np.full((s.shape[0], 5), np.nan), s[:, :-5]], axis=1)
    slope = (s - s5) / s5
    up = (f > s) & (slope > 0) & (b["C"] > s)
    dn = (f < s) & (slope < 0) & (b["C"] < s)
    out = np.where(up, 1, np.where(dn, -1, 0))
    out[:, :55] = 0
    return out


def at_close(ctx: np.ndarray, k_ctx: int, k_sig: int, n_sig: int) -> np.ndarray:
    """The latest CLOSED context bar at each signal bar's close: signal bar b closes at minute (b+1)k_sig."""
    j = ((np.arange(n_sig) + 1) * k_sig) // k_ctx - 1
    out = np.zeros((ctx.shape[0], n_sig), dtype=ctx.dtype)
    ok = j >= 0
    out[:, ok] = ctx[:, j[ok]]
    return out


def cloc(b: dict) -> np.ndarray:
    rng = b["H"] - b["L"]
    return np.where(rng > 0, (b["C"] - b["L"]) / np.where(rng > 0, rng, 1), 0.5)


def scale(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


# -- families: each returns candidate arrays (bar, sym, side, stop, score) on its own timeframe ------------------
def stop_from(close, extreme, buf, side, lo, hi):
    dist = np.where(side > 0, close - extreme, extreme - close)
    raw = (dist + buf) / close
    ok = (dist > 0) & (raw <= hi)
    pct = np.maximum(raw, lo)
    stop = np.where(side > 0, close * (1 - pct), close * (1 + pct))
    return ok, stop


def _pack(mask_l, mask_s, b, ext_l, ext_s, buf, lo, hi, score):
    out = []
    for side, mask, ext in ((1, mask_l, ext_l), (-1, mask_s, ext_s)):
        sy, bi = np.nonzero(mask)
        if not len(sy):
            continue
        close = b["C"][sy, bi]
        ok, stop = stop_from(close, ext[sy, bi], buf[sy, bi], np.full(len(sy), side), lo, hi)
        sc = score[sy, bi] if np.ndim(score) else np.full(len(sy), float(score))
        out.append(np.column_stack([bi[ok], sy[ok], np.full(ok.sum(), side), stop[ok], sc[ok] if np.ndim(sc) else sc]))
    return np.concatenate(out) if out else np.zeros((0, 5))


def fam_v8p(d, b5, b15, b60):
    n = b5["C"].shape[1]
    a = atr(b5)
    e = ema(b5["C"], 20)
    t15 = at_close(trend(b15), 15, 5, n)
    t60 = at_close(trend(b60), 60, 5, n)
    loc = cloc(b5)
    lo3 = roll(b5["L"], 3, "min")
    hi3 = roll(b5["H"], 3, "max")
    prevH = np.roll(b5["H"], 1, 1)
    prevL = np.roll(b5["L"], 1, 1)
    touch_l = lo3 <= e + 0.35 * a
    touch_s = hi3 >= e - 0.35 * a
    near = abs(b5["C"] - e) <= 1.5 * a
    L = (t15 == 1) & (t60 != -1) & touch_l & (b5["C"] > prevH) & (b5["C"] > e) & (loc >= 0.5) & near
    S = (t15 == -1) & (t60 != 1) & touch_s & (b5["C"] < prevL) & (b5["C"] < e) & (1 - loc >= 0.5) & near
    ext_l = np.minimum(lo3, b5["L"])
    ext_s = np.maximum(hi3, b5["H"])
    sc = (scale(np.where(L, loc, 1 - loc), 0.5, 1.0) + np.where(t15 == t60, 1.0, 0.5)) / 2
    return _pack(L, S, b5, ext_l, ext_s, 0.2 * a, 0.0045, 0.012, sc)


def fam_v8b(d, b5, b15, b60):
    n = b5["C"].shape[1]
    a = atr(b5)
    t60 = at_close(trend(b60), 60, 5, n)
    med = roll(b5["V"], 6, "median")
    hi6, lo6 = roll(b5["H"], 6, "max"), roll(b5["L"], 6, "min")
    loc = cloc(b5)
    base = (med > 0) & (b5["V"] >= 1.1 * med) & (b5["H"] - b5["L"] <= 2.5 * a)
    L = base & (b5["C"] > hi6) & (t60 != -1) & (loc >= 0.55)
    S = base & (b5["C"] < lo6) & (t60 != 1) & (1 - loc >= 0.55)
    ext_l = np.minimum(roll(b5["L"], 2, "min"), b5["L"])
    ext_s = np.maximum(roll(b5["H"], 2, "max"), b5["H"])
    sc = (scale(np.where(L, loc, 1 - loc), 0.5, 1.0) + scale(b5["V"] / np.where(med > 0, med, 1), 1.0, 2.5)) / 2
    return _pack(L, S, b5, ext_l, ext_s, 0.2 * a, 0.0045, 0.012, sc)


def fam_v8v(d, b5, b15, b60):
    a = atr(b5)
    tp = (b5["H"] + b5["L"] + b5["C"]) / 3
    pv = pd.DataFrame((tp * b5["V"]).T).rolling(48).sum().to_numpy().T
    vv = pd.DataFrame(b5["V"].T).rolling(48).sum().to_numpy().T
    vwap = np.where(vv > 0, pv / np.where(vv > 0, vv, 1), np.nan)
    pc = np.roll(b5["C"], 1, 1)
    stretch = (pc - vwap) / a                   # V8.3: the previous close vs the 4h VWAP and ATR as of this bar
    loc = cloc(b5)
    up = (b5["C"] > b5["O"]) & (b5["C"] > pc)
    dn = (b5["C"] < b5["O"]) & (b5["C"] < pc)
    L = (stretch >= -4) & (stretch <= -1.8) & up & (loc >= 0.6)
    S = (stretch >= 1.8) & (stretch <= 4) & dn & (loc <= 0.4)
    ext_l = np.minimum(roll(b5["L"], 2, "min"), b5["L"])
    ext_s = np.maximum(roll(b5["H"], 2, "max"), b5["H"])
    return _pack(L, S, b5, ext_l, ext_s, 0.2 * a, 0.0045, 0.012, scale(abs(stretch), 1.8, 3.5))


def rs_rank(b15: dict, syms: list[str]) -> np.ndarray:
    """Each coin's 4h return relative to the universe, as a cross-sectional percentile at every 15m close."""
    c = b15["C"]
    r4 = c / np.concatenate([np.full((c.shape[0], 16), np.nan), c[:, :-16]], axis=1) - 1
    return pd.DataFrame(r4).rank(axis=0, pct=True).to_numpy()


def fam_rsb(d, b5, b15, b60):
    n = b15["C"].shape[1]
    a = atr(b15)
    rk = rs_rank(b15, d["syms"])
    t60 = at_close(trend(b60), 60, 15, n)
    med = roll(b15["V"], 20, "median")
    hi16, lo16 = roll(b15["H"], 16, "max"), roll(b15["L"], 16, "min")
    loc = cloc(b15)
    vol = (med > 0) & (b15["V"] >= 1.5 * med)
    L = vol & (rk >= 0.8) & (b15["C"] > hi16) & (t60 == 1) & (loc >= 0.6)
    S = vol & (rk <= 0.2) & (b15["C"] < lo16) & (t60 == -1) & (loc <= 0.4)
    ext_l = np.minimum(roll(b15["L"], 2, "min"), b15["L"])
    ext_s = np.maximum(roll(b15["H"], 2, "max"), b15["H"])
    sc = (scale(abs(rk - 0.5), 0.3, 0.5) + scale(b15["V"] / np.where(med > 0, med, 1), 1.5, 4.0)) / 2
    return _pack(L, S, b15, ext_l, ext_s, 0.25 * a, 0.008, 0.03, sc)


def fam_rsp(d, b5, b15, b60):
    n = b15["C"].shape[1]
    a = atr(b15)
    e = ema(b15["C"], 20)
    rk = rs_rank(b15, d["syms"])
    t15 = trend(b15)
    t60 = at_close(trend(b60), 60, 15, n)
    loc = cloc(b15)
    lo3, hi3 = roll(b15["L"], 3, "min"), roll(b15["H"], 3, "max")
    prevH, prevL = np.roll(b15["H"], 1, 1), np.roll(b15["L"], 1, 1)
    near = abs(b15["C"] - e) <= 1.5 * a
    L = (rk >= 0.8) & (t15 == 1) & (t60 == 1) & (lo3 <= e + 0.3 * a) & (b15["C"] > prevH) & (b15["C"] > e) & (loc >= 0.55) & near
    S = (rk <= 0.2) & (t15 == -1) & (t60 == -1) & (hi3 >= e - 0.3 * a) & (b15["C"] < prevL) & (b15["C"] < e) & (loc <= 0.45) & near
    ext_l = np.minimum(lo3, b15["L"])
    ext_s = np.maximum(hi3, b15["H"])
    sc = (scale(abs(rk - 0.5), 0.3, 0.5) + scale(np.where(L, loc, 1 - loc), 0.55, 1.0)) / 2
    return _pack(L, S, b15, ext_l, ext_s, 0.25 * a, 0.008, 0.03, sc)


def fam_vsh(d, b5, b15, b60):
    a = np.roll(atr(b5), 1, 1)
    med = roll(b5["V"], 288, "median")
    loc = cloc(b5)
    ratio = b5["V"] / np.where(med > 0, med, np.inf)
    body = b5["C"] - b5["O"]
    shock = (med > 0) & (ratio >= 5)
    L = shock & (body >= 1.2 * a) & (loc >= 0.7)
    S = shock & (-body >= 1.2 * a) & (loc <= 0.3)
    return _pack(L, S, b5, b5["L"], b5["H"], 0.2 * a, 0.006, 0.03, scale(ratio, 5, 15))


def fam_cap(d, b5, b15, b60):
    a = atr(b5)
    med = roll(b5["V"], 288, "median")
    hi12, lo12 = roll(b5["H"], 12, "max"), roll(b5["L"], 12, "min")
    pc = np.roll(b5["C"], 1, 1)
    fall = (pc - hi12) / a
    rise = (pc - lo12) / a
    climax = (pd.DataFrame(((b5["V"] >= 2.5 * med) & (med > 0)).T.astype(float)).shift(1).rolling(4).sum().to_numpy().T >= 2)
    loc = cloc(b5)
    L = (fall <= -4) & climax & (b5["C"] > b5["O"]) & (b5["C"] > pc) & (loc >= 0.6)
    S = (rise >= 4) & climax & (b5["C"] < b5["O"]) & (b5["C"] < pc) & (loc <= 0.4)
    ext_l = np.minimum(roll(b5["L"], 3, "min"), b5["L"])
    ext_s = np.maximum(roll(b5["H"], 3, "max"), b5["H"])
    return _pack(L, S, b5, ext_l, ext_s, 0.2 * a, 0.006, 0.03, scale(np.maximum(-fall, rise), 4, 8))


def fam_btl(d, b5, b15, b60):
    syms = d["syms"]
    bi = syms.index("BTCUSDT")
    c = b5["C"]
    r = c / np.roll(c, 1, 1) - 1
    r[:, 0] = 0
    sd = roll(r, 288, "std")
    rb = r[bi]
    varb = pd.Series(rb).shift(1).rolling(288).var().to_numpy()
    cov = np.stack([pd.Series(r[k]).shift(1).rolling(288).cov(pd.Series(rb).shift(1)).to_numpy() for k in range(len(syms))])
    beta = cov / np.where(varb > 0, varb, np.nan)
    zb = rb / sd[bi]
    lag = (beta * rb - r) / sd                 # how far the coin is behind what BTC's move implies, in its own sigmas
    a = atr(b5)
    big_up, big_dn = (zb >= 2.5)[None, :], (zb <= -2.5)[None, :]
    notbtc = (np.arange(len(syms)) != bi)[:, None]
    L = notbtc & big_up & (lag >= 1.5)
    S = notbtc & big_dn & (lag <= -1.5)
    return _pack(L, S, b5, c - 1.3 * a, c + 1.3 * a, 0.2 * a, 0.006, 0.02, scale(abs(lag), 1.5, 4))


FAMILIES = {  # id: (fn, timeframe minutes, target R, hold minutes)
    "V8P": (fam_v8p, 5, 1.5, 45), "V8B": (fam_v8b, 5, 1.5, 45), "V8V": (fam_v8v, 5, 1.5, 45),
    "RSB": (fam_rsb, 15, 2.0, 240), "RSP": (fam_rsp, 15, 2.0, 240),
    "VSH": (fam_vsh, 5, 2.0, 120), "CAP": (fam_cap, 5, 2.0, 120), "BTL": (fam_btl, 5, 1.5, 30),
}


# -- one trade ---------------------------------------------------------------------------------------------------
def simulate(d: dict, cands: np.ndarray, k: int, target_r: float, hold: int, sig_close: np.ndarray) -> np.ndarray:
    """Per candidate: [entry minute, exit minute, gross R, net R]; NaN when it cannot be simulated."""
    O, H, L, C, hs = d["O"], d["H"], d["L"], d["C"], d["hs"]
    n = C.shape[1]
    out = np.full((len(cands), 4), np.nan)
    for i, (b, s, side, stop, _) in enumerate(cands):
        b, s = int(b), int(s)
        e = (b + 1) * k + 1
        if e + hold >= n:
            continue
        ref = sig_close[i]
        risk = abs(ref - stop)
        if risk <= 0:
            continue
        tgt = ref + side * target_r * risk
        fill = O[s, e]
        h, l, o = H[s, e:e + hold], L[s, e:e + hold], O[s, e:e + hold]
        if side > 0:
            hit_s = np.nonzero(l <= stop)[0]
            hit_t = np.nonzero(h >= tgt)[0]
        else:
            hit_s = np.nonzero(h >= stop)[0]
            hit_t = np.nonzero(l <= tgt)[0]
        js = hit_s[0] if len(hit_s) else hold
        jt = hit_t[0] if len(hit_t) else hold
        if js <= jt and js < hold:                     # a bar touching both is a stop
            j = js
            px = min(o[j], stop) if side > 0 else max(o[j], stop)
        elif jt < hold:
            j = jt
            px = max(o[j], tgt) if side > 0 else min(o[j], tgt)
        else:
            j = hold - 1
            px = C[s, e + j]
        gross = side * (px - fill) / risk
        cost = (TAKER + hs[s]) * (fill + px) / risk
        out[i] = (e, e + j, gross, gross - cost)
    return out


# -- the scanner book ------------------------------------------------------------------------------------------------
def book(cands: np.ndarray, res: np.ndarray, k: int, slots: int = BOOK_SLOTS) -> np.ndarray:
    """Which candidates a 3-slot scanner book actually takes: best score first at each bar, one position per coin, a
    one-bar cooldown per coin after its exit."""
    order = np.lexsort((-cands[:, 4], cands[:, 0]))
    taken = np.zeros(len(cands), dtype=bool)
    open_until: dict[int, float] = {}                  # coin -> exit minute
    free_at: dict[int, float] = {}                     # coin -> minute it may trade again
    for i in order:
        if np.isnan(res[i, 0]):
            continue
        e, s = res[i, 0], int(cands[i, 1])
        open_until = {c: x for c, x in open_until.items() if x >= e}
        if len(open_until) >= slots or s in open_until or free_at.get(s, -1) > e:
            continue
        taken[i] = True
        open_until[s] = res[i, 1]
        free_at[s] = res[i, 1] + k
    return taken


def stats(r: np.ndarray, days: float) -> dict:
    net, gross = r[:, 3], r[:, 2]
    if not len(net):
        return {"trades": 0}
    pos, neg = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    return {"trades": int(len(net)), "per_day": round(len(net) / days, 2), "win": round(float((net > 0).mean()), 3),
            "net_r": round(float(net.mean()), 4), "gross_r": round(float(gross.mean()), 4),
            "pf": round(float(pos / neg), 3) if neg > 0 else None, "sum_r": round(float(net.sum()), 1),
            "max_dd_r": round(dd, 1)}


def main() -> None:
    t0 = time.time()
    d = load()
    b5, b15, b60 = bars(d, 5), bars(d, 15), bars(d, 60)
    n1 = d["C"].shape[1]
    warm = WARM_DAYS * 1440
    mid = warm + (n1 - warm) // 2
    days = (n1 - warm) / 1440 / 2
    print(f"{len(d['syms'])} symbols, {n1 / 1440:.0f} days; discovery {days:.0f} d + confirmation {days:.0f} d; "
          f"half spreads {np.round(d['hs'] * 1e4, 1).tolist()}", flush=True)
    report: dict = {"universe": d["syms"], "start_ms": d["start"], "end_ms": d["end"], "warm_days": WARM_DAYS,
                    "split_ms": d["start"] + mid * MIN, "slots": BOOK_SLOTS, "families": {}}
    tf_bars = {5: b5, 15: b15}
    for fid, (fn, k, tr, hold) in FAMILIES.items():
        cands = fn(d, b5, b15, b60)
        cands = cands[((cands[:, 0] + 1) * k) >= warm] if len(cands) else cands
        b = tf_bars[k]
        sig_close = b["C"][cands[:, 1].astype(int), cands[:, 0].astype(int)] if len(cands) else np.zeros(0)
        res = simulate(d, cands, k, tr, hold, sig_close)
        ok = ~np.isnan(res[:, 0])
        cands, res = cands[ok], res[ok]
        first = res[:, 0] < mid
        cut = float(np.quantile(cands[first, 4], 2 / 3)) if first.any() else 1.0
        fam = {"timeframe_min": k, "target_r": tr, "hold_min": hold, "candidates": int(len(cands)),
               "top_cut": round(cut, 4), "per_trade_all_candidates": {
                   "discovery": stats(res[first], days), "confirmation": stats(res[~first], days)}}
        for name, mask in (("ALL", np.ones(len(cands), dtype=bool)), ("TOP", cands[:, 4] >= cut)):
            c2, r2 = cands[mask], res[mask]
            took = book(c2, r2, k)
            r3, f3 = r2[took], r2[took, 0] < mid
            disc, conf = stats(r3[f3], days), stats(r3[~f3], days)
            passed = all(x.get("trades", 0) and x["net_r"] > 0 and (x["pf"] or 0) >= 1.1 for x in (disc, conf)) and \
                (disc["trades"] + conf["trades"]) / (2 * days) >= 2
            fam[name] = {"discovery": disc, "confirmation": conf, "pass": bool(passed),
                         "by_coin": {d["syms"][int(s)]: round(float(r3[cands[mask][took][:, 1] == s, 3].sum()), 1)
                                     for s in np.unique(c2[took][:, 1])}}
            print(f"{fid} {name:3s} disc {disc.get('trades', 0):5d} tr {disc.get('per_day', 0):5.1f}/d "
                  f"net {disc.get('net_r', 0):+.3f}R gross {disc.get('gross_r', 0):+.3f}R pf {disc.get('pf')} | "
                  f"conf {conf.get('trades', 0):5d} tr net {conf.get('net_r', 0):+.3f}R gross {conf.get('gross_r', 0):+.3f}R "
                  f"pf {conf.get('pf')} {'PASS' if passed else ''}", flush=True)
        report["families"][fid] = fam
    report["passing"] = [f"{f}-{v}" for f, x in report["families"].items() for v in ("ALL", "TOP") if x[v]["pass"]]
    report["elapsed_s"] = round(time.time() - t0, 1)
    (PROJECT / "docs" / "V11_SCAN_STUDY.json").write_text(json.dumps(report, indent=1))
    print("passing:", report["passing"] or "NONE", f"({report['elapsed_s']} s)")


if __name__ == "__main__":
    main()
