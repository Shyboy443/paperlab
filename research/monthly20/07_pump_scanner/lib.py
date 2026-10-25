"""Top-gainer scanner research: data loading, event study and a portfolio simulator on 1h bars.

Signals use closed 1h bars only; entries fill at the next bar's open. Stops are checked on bar highs/lows with the
worst-case ordering (the adverse extreme is checked before the bar's favourable extreme updates the trailing stop,
and before any take-profit). Take-profits need the price to trade through the level.
"""
import sys, json, math, bisect
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa
import numpy as np
import pandas as pd

STEP = HOUR
BASE = 720          # 30 days of 1h bars for the volume baseline / prior high
VDIR = PROJECT / "data" / "monthly20_07_pump_scanner"


def load_all(syms=None, delisted=True):
    """All perps trading today plus (delisted=True) every delisted USDT perp the public data portal still holds."""
    data = {}
    names = [s["symbol"] for s in perp_symbols()]
    if delisted and (VDIR / "delisted.json").exists():
        names += json.loads((VDIR / "delisted.json").read_text())
    for sym in names:
        if syms and sym not in syms:
            continue
        p = VDIR / "1h" / f"{sym}.json"
        if not p.exists():
            continue
        rows = json.loads(p.read_text())
        if len(rows) < BASE + 48:
            continue
        a = np.array(rows, dtype=float)
        t = a[:, 0].astype(np.int64)
        o, h, l, c, qv = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 6]
        cs = pd.Series(c)
        r4 = (cs / cs.shift(4) - 1).values
        r24 = (cs / cs.shift(24) - 1).values
        r1 = c / o - 1
        qvs = pd.Series(qv)
        basev = qvs.shift(1).rolling(BASE, min_periods=BASE).mean().values   # mean 1h quote vol, prior 30d
        v24 = qvs.rolling(24).sum().values                                     # last 24h quote vol
        vratio24 = v24 / (basev * 24)                                          # 24h volume vs normal
        prevhi = pd.Series(h).shift(1).rolling(BASE, min_periods=BASE).max().values
        hi24 = pd.Series(h).rolling(24).max().values
        age = np.arange(len(t))  # bars since first bar
        f = json.loads((VDIR / "funding" / f"{sym}.json").read_text()) if (VDIR / "funding" / f"{sym}.json").exists() else []
        data[sym] = dict(t=t, o=o, h=h, l=l, c=c, qv=qv, r1=r1, r4=r4, r24=r24, basev=basev, vratio24=vratio24,
                         prevhi=prevhi, hi24=hi24, age=age, ft=[x[0] for x in f], fr=[x[1] for x in f],
                         idx={int(x): i for i, x in enumerate(t)})
    return data


def window_idx(d, start, end):
    """bar indices i whose CLOSE (t+STEP) is in [start, end) and where i+1 exists (entry bar)."""
    i0 = int(np.searchsorted(d["t"], start - STEP)); i1 = int(np.searchsorted(d["t"], end - STEP))
    return max(i0, BASE), min(i1, len(d["t"]) - 1)


def events(data, sig, start, end, cooldown=24):
    """(sym, i) where sig(d) is true at the close of bar i; one event per coin per `cooldown` bars."""
    out = []
    for sym, d in data.items():
        m = np.nan_to_num(sig(d)).astype(bool)
        a, b = window_idx(d, start, end)
        last = -10**9
        for i in np.nonzero(m[a:b])[0] + a:
            if i - last >= cooldown:
                out.append((sym, int(i))); last = i
    return out


def fwd_stats(data, evs, horizons=(1, 4, 12, 24, 48, 72, 168)):
    """Gross forward return from the next bar's open to the close `h` bars later (long sense), plus MAE/MFE in 24h."""
    res = {}
    for hz in horizons:
        r = np.array([data[s]["c"][i + hz] / data[s]["o"][i + 1] - 1 for s, i in evs if i + hz < len(data[s]["c"])])
        if len(r):
            res[f"{hz}h"] = dict(n=len(r), mean=round(r.mean() * 100, 2), median=round(float(np.median(r)) * 100, 2),
                                 up=round(float((r > 0).mean()) * 100, 1))
    mfe = [data[s]["h"][i + 1:i + 25].max() / data[s]["o"][i + 1] - 1 for s, i in evs if i + 25 < len(data[s]["c"])]
    mae = [data[s]["l"][i + 1:i + 25].min() / data[s]["o"][i + 1] - 1 for s, i in evs if i + 25 < len(data[s]["c"])]
    if mfe:
        res["24h_MFE_median"] = round(float(np.median(mfe)) * 100, 2)
        res["24h_MAE_median"] = round(float(np.median(mae)) * 100, 2)
    return res


def simulate(data, cfg, start, end, cost_mult=1.0, funding_on=True, start_eq=1000.0):
    """Portfolio simulation of a scanner.

    cfg keys: side 'long'|'short'; sig(d)->bool array (signal at close of bar i); rank(d)->array (higher first);
    k max concurrent positions (each gets equity/k margin); lev leverage; stop initial stop fraction;
    trail trailing fraction or None; tp take-profit fraction or None; hold max bars; min_basev minimum mean 1h quote
    volume over the prior 30 days; cap fraction of a bar's quote volume a fill may take (0.01).
    """
    side = 1 if cfg["side"] == "long" else -1
    fee = (FEE + SLIP) * cost_mult
    k, lev, cap = cfg["k"], cfg["lev"], cfg.get("cap", 0.01)
    liq = 1 / lev - MAINT_MARGIN
    # signal book: close time -> [(rank, sym, i)]
    book = {}
    for sym, d in data.items():
        m = np.nan_to_num(cfg["sig"](d)).astype(bool) & (np.nan_to_num(d["basev"]) >= cfg.get("min_basev", 0))
        rk = cfg["rank"](d) if cfg.get("rank") else d["r24"]
        a, b = window_idx(d, start, end)
        for i in np.nonzero(m[a:b])[0] + a:
            if d["t"][i + 1] == d["t"][i] + STEP:
                book.setdefault(int(d["t"][i]), []).append((float(rk[i]), sym, int(i)))
    times = range(int(start // STEP * STEP), int(end), STEP)
    eq = start_eq
    curve, pnls, trades = [], [], []
    pos = {}
    capped = 0
    for T in times:
        for sym in list(pos):
            d, p = data[sym], pos[sym]
            i = d["idx"].get(T)
            if i is None:
                continue
            o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
            exit_px, why = None, None
            adverse = l if side == 1 else h
            sp, lp = p["stop_px"], p["liq_px"]
            hit_stop = adverse <= sp if side == 1 else adverse >= sp
            hit_liq = adverse <= lp if side == 1 else adverse >= lp
            liq_first = (lp >= sp) if side == 1 else (lp <= sp)   # liquidation level reached before the stop
            if hit_liq and (liq_first or ((o <= lp) if side == 1 else (o >= lp))):
                exit_px, why = lp, "liq"
            elif hit_stop:
                gap = o <= sp if side == 1 else o >= sp
                exit_px, why = (o if gap else sp), "stop"
            elif cfg.get("tp"):
                tpx = p["entry"] * (1 + side * cfg["tp"])
                if (h > tpx) if side == 1 else (l < tpx):
                    exit_px, why = ((max(o, tpx)) if side == 1 else min(o, tpx)), "tp"
            if exit_px is None and i - p["entry_i"] + 1 >= cfg["hold"]:
                exit_px, why = c, "time"
            if funding_on:
                a = bisect.bisect_left(d["ft"], T); b = bisect.bisect_left(d["ft"], T + STEP)
                for j in range(a, b):
                    p["fund"] -= side * d["fr"][j] * p["notional"] * (o / p["entry"])
            if exit_px is not None:
                if why == "liq":
                    pnl = -p["margin"]
                else:
                    gross = side * (exit_px / p["entry"] - 1) * p["notional"]
                    pnl = max(gross - fee * p["notional"] * exit_px / p["entry"] + p["fund"], -p["margin"])
                pnl -= p["fee_in"]
                eq += pnl + p["margin"]
                pnls.append(pnl)
                trades.append([sym, p["entry_t"], T + STEP, why, round(pnl, 4), round(p["notional"], 2)])
                del pos[sym]
                continue
            if cfg.get("trail"):
                if side == 1:
                    p["best"] = max(p["best"], h); p["stop_px"] = max(sp, p["best"] * (1 - cfg["trail"]))
                else:
                    p["best"] = min(p["best"], l); p["stop_px"] = min(sp, p["best"] * (1 + cfg["trail"]))
            p["last"] = c
        mtm = eq + sum(p["margin"] + max(-p["margin"], side * (p["last"] / p["entry"] - 1) * p["notional"] + p["fund"])
                       for p in pos.values())
        curve.append((T + STEP, mtm))
        if mtm <= start_eq * 1e-4:
            break
        if len(pos) >= k or T not in book:
            continue
        for _, sym, i in sorted(book[T], reverse=True):
            if len(pos) >= k:
                break
            if sym in pos:
                continue
            d = data[sym]
            margin = min(mtm / k, eq)
            notional = margin * lev
            capn = cap * min(d["qv"][i], d["qv"][i + 1])     # <=1% of the signal bar and of the fill bar
            if notional > capn:
                notional, margin, capped = capn, capn / lev, capped + 1
            if margin <= 0:
                continue
            entry = d["o"][i + 1]
            eq -= margin
            pos[sym] = dict(entry=entry, entry_i=i + 1, entry_t=T + STEP, margin=margin, notional=notional,
                            stop_px=entry * (1 - side * cfg["stop"]), liq_px=entry * (1 - side * liq),
                            best=entry, fund=0.0, fee_in=fee * notional, last=entry)
    s = summary(curve, pnls)
    why = {}
    for tr in trades:
        why[tr[3]] = why.get(tr[3], 0) + 1
    s["exits"] = why
    s["capacity_capped_entries"] = capped
    s["biggest_trade_share"] = round(max(pnls) / sum(pnls) * 100, 1) if pnls and sum(pnls) > 0 else None
    return s, trades
