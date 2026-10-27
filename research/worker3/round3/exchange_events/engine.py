"""Event-slot engine. Each event = one 1x perp position of min(1/S, free/k) of equity (gross never above 1x),
entered at the open of the first full hour after the announcement (+delay h), exited at the open h hours later
(or the last close if the perp stops trading). Costs 0.05% fee + 0.02% slippage per side (x cost_mult), funding
charged/received per stamp. Hourly mark-to-market equity curve."""
import sys
from datetime import datetime, timezone
import numpy as np
from evlib import load_events, FEE, SLIP, HOUR, DAY, DEV_START, TEST_END
from panel import make, T0
P = make()
TH, SYMS, O, C, QV, F = P["th"], [str(s) for s in P["syms"]], P["O"], P["C"], P["QV"], P["F"]
J = {s: i for i, s in enumerate(SYMS)}
NT = len(TH)
ALIAS = {"RNDR": "RENDER", "MATIC": "POL", "LUNA": "LUNA2"}
_cq = np.vstack([np.zeros((1, QV.shape[1]), np.float64), np.cumsum(np.nan_to_num(QV, nan=0.0).astype(np.float64), 0)])
_cn = np.vstack([np.zeros((1, O.shape[1]), np.int32), np.cumsum(~np.isnan(O), 0, dtype=np.int32)])
_cf = np.vstack([np.zeros((1, F.shape[1]), np.float64), np.cumsum(F.astype(np.float64), 0)])
MIN_QV, MIN_AGE = 1e6, 24


def eligible(e, j):
    if e < 24 or e >= NT or np.isnan(O[e, j]):
        return False
    return (_cn[e, j] - _cn[e - 24, j]) >= 20 and (_cn[e, j] >= MIN_AGE) and (_cq[e, j] - _cq[e - 24, j]) >= MIN_QV


def col_for(tk, e):
    for s in (f"{tk}USDT", f"1000{tk}USDT", f"1000000{tk}USDT", f"{ALIAS.get(tk, tk)}USDT"):
        j = J.get(s)
        if j is not None and eligible(e, j):
            return j
    return None


def event_list(types, delay=0, t0=DEV_START, t1=TEST_END, exclude=()):
    """[(entry_idx, col, ticker, type)] deduped by (day, ticker); only events with an eligible perp."""
    out, seen = [], set()
    for typ, ts, tk, _ in load_events():
        if typ not in types or not (t0 <= ts < t1) or tk in exclude:
            continue
        if (ts // DAY, tk) in seen:
            continue
        e = int(((ts // HOUR + 1) * HOUR - T0) // HOUR) + delay
        j = col_for(tk, e)
        if j is None:
            continue
        seen.add((ts // DAY, tk)); out.append((e, j, tk, typ))
    out.sort()
    return out


def run(evs, H=48, S=4, side=-1, cm=1.0, t0=DEV_START, t1=TEST_END):
    """evs = [(e, j, tk, typ)]. Returns daily curve [(ms, eq)], trades [dict]."""
    cost = (FEE + SLIP) * cm
    i0 = int((t0 - T0) // HOUR); i1 = min(NT, int((t1 - T0) // HOUR))
    by_e = {}
    for ev in evs:
        if i0 <= ev[0] < i1:
            by_e.setdefault(ev[0], []).append(ev)
    cash, open_, trades, curve = 1.0, [], [], []
    for t in range(i0, i1):
        # exits at open of t
        keep = []
        for p in open_:
            if t >= p["x"]:
                v = p["val"]
                cash += p["cap"] * v - p["cap"] * p["nr"] * cost  # exit cost on the current notional
                p["pnl"] = p["cap"] * v - p["cap"] * p["nr"] * cost - p["cap"] * (1 + cost)
                p["ret"] = p["pnl"] / p["cap"]; trades.append(p)
            else:
                keep.append(p)
        open_ = keep
        # mark open positions at open of t
        for p in open_:
            px = O[t, p["j"]]
            if np.isnan(px):
                px = p["last"]
            else:
                p["last"] = px
            fsum = _cf[t, p["j"]] - _cf[p["e"], p["j"]]
            p["val"] = 1 + side * (px / p["p0"] - 1) - side * fsum; p["nr"] = px / p["p0"]
        eq = cash + sum(p["cap"] * p["val"] for p in open_)
        # entries
        new = by_e.get(t, [])
        if new:
            gross = sum(p["cap"] * abs(p["val"]) for p in open_) / eq if eq > 0 else 1
            free = max(0.0, 1 - gross)
            w = min(1.0 / S, free / len(new))
            if w > 1e-6:
                for (e, j, tk, typ) in new:
                    cap = w * eq
                    cash -= cap * (1 + cost)
                    # exit index: e+H, or earlier if the perp stops trading (last close)
                    x = e + H
                    seg = O[e:min(x, NT), j]
                    nn = np.where(~np.isnan(seg))[0]
                    lastbar = e + nn[-1] if len(nn) else e
                    if lastbar < x - 1 and (x >= NT or np.isnan(O[min(x, NT - 1), j])):
                        x = lastbar + 1  # exit at the last bar's close (≈ open of the next hour)
                    open_.append(dict(e=e, x=min(x, i1), j=j, tk=tk, typ=typ, cap=cap, p0=O[e, j], last=O[e, j], val=1.0, nr=1.0,
                                      t=int(TH[e])))
                eq = cash + sum(p["cap"] * p["val"] for p in open_)
        # last close of a stopped perp: use C at x-1 when O[x] missing
        for p in open_:
            if t + 1 >= p["x"]:
                px = O[t + 1, p["j"]] if t + 1 < NT else np.nan
                if np.isnan(px):
                    px = C[t, p["j"]] if not np.isnan(C[t, p["j"]]) else p["last"]
                fsum = _cf[t + 1, p["j"]] - _cf[p["e"], p["j"]]
                p["val"] = 1 + side * (px / p["p0"] - 1) - side * fsum; p["nr"] = px / p["p0"]
        if (TH[t] % DAY) == 0 or t == i1 - 1:
            curve.append((int(TH[t]), eq))
    eq = cash + sum(p["cap"] * p["val"] for p in open_)
    curve.append((int(TH[i1 - 1]) + HOUR, eq))
    return curve, trades


def month_key(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")
