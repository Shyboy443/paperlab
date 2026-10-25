"""Copy of r4_03 engine.sim with two additions (r4_03 files are not modified):
- trim_band: at a daily open, a live position whose |value| > trim_band x |target weight| x equity is cut back to target
  (reduce-only, costs charged, same position for the accounting);
- record_orders: every order (day, coin, signed notional change, reduce_only) for the size check.
Everything else is line-for-line the r4_03 logic, so with trim_band=None it reproduces r4_03 exactly."""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, MAINT_MARGIN, summary  # noqa

STEP = 86_400_000


def sim(P, W, start, end, cost_mult=1.0, lev=1.0, rebal=None, record=False, equity0=10_000.0, exclude=None,
        trim_band=None, record_orders=False):
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(P.t, start)); i1 = int(np.searchsorted(P.t, end))
    N = P.c.shape[1]
    if exclude is not None and len(exclude):
        W = W.copy(); W[:, list(exclude)] = 0.0
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N); last_px = np.full(N, np.nan); wcur = np.zeros(N)
    pos_pnl = np.zeros(N); pos_open = np.zeros(N, int); pos_side = np.zeros(N)
    positions, trade_pnl, orders = [], [], []
    curve = [(int(P.t[i0]), eq)]
    n_liq, wiped, n_trim = 0, False, 0

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]
        live = units != 0
        if live.any():
            px = np.where(np.isnan(o), last_px, o)
            g = np.where(live, units * (px - last_px), 0.0)
            eq += g.sum(); pos_pnl += g
            last_px = np.where(live, px, last_px)
        if not wiped:
            w = np.nan_to_num(W[i]) * lev
            gone = live & np.isnan(o)
            w = np.where(np.isnan(o), 0.0, w)
            chg = (w != wcur) | gone
            if rebal is not None and rebal[i]:
                chg |= (w != 0)
            px = np.where(np.isnan(o), last_px, o)
            cur = units * np.nan_to_num(px)
            trim = np.zeros(N, bool)
            if trim_band is not None:
                trim = live & ~chg & (w == wcur) & (w != 0) & (np.abs(cur) > trim_band * np.abs(w) * eq)
                chg = chg | trim
                n_trim += int(trim.sum())
            if chg.any():
                cap = 0.01 * np.nan_to_num(P.qv[i - 1])
                tgt = np.clip(w * eq, -cap, cap)
                newn = np.where(chg, tgt, cur)
                traded = np.abs(newn - cur) * chg
                eq -= traded.sum() * cost; pos_pnl -= traded * cost
                if record_orders:
                    for j in np.where(chg & (traded > 0))[0]:
                        red = bool(cur[j] != 0 and np.sign(newn[j]) == np.sign(cur[j]) and abs(newn[j]) < abs(cur[j])) \
                            or newn[j] == 0
                        opening = bool(newn[j] != 0 and (cur[j] == 0 or np.sign(newn[j]) != np.sign(cur[j])))
                        orders.append((i, int(j), float(newn[j] - cur[j]), red, opening, float(w[j]), float(eq)))
                for j in np.where(chg & live)[0]:
                    if newn[j] == 0 or np.sign(newn[j]) != np.sign(units[j]):
                        close(j, i)
                opened = chg & (newn != 0) & ((units == 0) | (np.sign(newn) != np.sign(units)))
                pos_open[opened] = i; pos_side[opened] = np.sign(newn[opened])
                safe_px = np.where(np.isnan(px) | (px <= 0), 1.0, px)
                units = np.where(chg, np.where(newn != 0, newn / safe_px, 0.0), units)
                entry = np.where(chg & (newn != 0) & ~trim, px, entry)
                last_px = np.where(chg & (newn != 0), px, last_px)
                wcur = np.where(chg, w, wcur)
                live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(P.t[i] + STEP - 1), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * np.nan_to_num(P.fund[i])), 0.0)
        eq += f.sum(); pos_pnl += f
        liq = np.zeros(N, bool)
        if lev > 1.0 + 1e-9:
            thr = 1.0 / lev - MAINT_MARGIN
            hh = np.where(np.isnan(h), last_px, h); ll = np.where(np.isnan(l), last_px, l)
            ent = np.where(entry > 0, entry, 1.0)
            adv = np.where(units > 0, (entry - ll) / ent, (hh - entry) / ent)
            liq = live & (adv >= thr)
        cc = np.where(np.isnan(c), last_px, c)
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        eq += g.sum(); pos_pnl += g
        for j in np.where(liq)[0]:
            margin = abs(units[j] * entry[j]) / lev
            loss = -margin - units[j] * (last_px[j] - entry[j])
            eq += loss; pos_pnl[j] += loss
            close(j, i + 1); units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        if eq <= 0:
            eq = 1e-9; wiped = True; units[:] = 0
        curve.append((int(P.t[i] + STEP - 1), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["wiped"] = wiped; out["trims"] = n_trim
    out["_curve"] = curve
    if record:
        out["_positions"] = positions
    if record_orders:
        out["_orders"] = orders
    return out
