"""Funding-clock family: shared engine. Data = r4_09_session hourly panel (survivorship-free top-40, funding per hour bar).

Funding stamps (00/08/16 UTC, plus 04/12/20 for 4h coins) are attributed to the hour bar that contains them (hpanel).
Signal f = sum of stamps in the 8 bars before the decision bar (= last 8h-equivalent rate, known at decision time).
Legs per stamp s (bar index of the stamp hour):
  NEG_PRE : f <= -thr_neg -> long  from open s-h1 to open s   (closed just before the stamp)
  NEG_POST: f <= -thr_neg -> short from open s    to open s+h2 (opened just after the stamp)
  POS_PRE : f >= +thr_pos -> short from open s-h1 to open s
  POS_POST: f >= +thr_pos -> long  from open s    to open s+h2
Pre and post legs both use f known at s-h1 (no use of the stamp at s). h1+h2 <= 8 so legs never overlap other stamps' legs.
Exact stamp rule (baseline): a stamp is paid/received only if strictly inside (entry, exit): bars ie+1..ix-1.
Conservative rule (conv='worst'): stamps at the entry bar and exit bar are also charged when they cost money (never credited).
Sizing: each leg group (one stamp, pre or post) gives each position equity/max(n,K), 1x; notional <= 1% of entry-bar quote volume."""
import sys
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import *  # noqa
from common import summary

d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
STAMP_HOURS = (0, 8, 16)
ELIG = [np.where(U40[d])[0] for d in range(len(td))]
NB = len(th)


def legs(p, d_from, d_to, exclude=()):
    """rows: (group_key, ie, ix, j, side). group_key = (s, 0 pre / 1 post)."""
    h1, h2 = p["h1"], p["h2"]
    out = []
    for d in range(d_from, d_to):
        js = ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        for hh in STAMP_HOURS:
            s = d * 24 + hh
            dec = s - h1 if p["pre"] else s
            if s + h2 >= NB or dec < 9:
                continue
            f = FCUM[dec, js] - FCUM[dec - 8, js]
            okp = np.isfinite(O[dec, js]) & np.isfinite(O[s, js])
            for lab, sel, side_pre in (("NEG", f <= -p["thr_neg"], +1), ("POS", f >= p["thr_pos"], -1)):
                if not p.get(lab):
                    continue
                sel = sel & okp
                for j in js[sel]:
                    if p["pre"] and p[lab] in ("pre", "both"):
                        out.append(((s, 0), s - h1, s, int(j), side_pre))
                    if p[lab] in ("post", "both") and np.isfinite(O[min(s + h2, NB - 1), j]):
                        out.append(((s, 1), s, s + h2, int(j), -side_pre))
    return out


def fund_paid(ie, ix, j, s, conv):
    """funding fraction paid by the position (positive = cost)."""
    base = s * (FCUM[ix, j] - FCUM[ie + 1, j]) if ix > ie + 1 else 0.0
    if conv == "worst":
        for b in (ie, ix):
            if b < NB:
                c = s * FUND[b, j]
                if c > 0:
                    base += c
    return base


def run(rows, K, cost_mult=1.0, conv="exact", eq0=10_000.0, liq_cap=True, t_from=None):
    groups = {}
    for r in rows:
        groups.setdefault(r[0], []).append(r)
    eq = eq0
    curve = [(int(t_from) if t_from is not None else int(th[min(min(groups)[0], NB - 1)]) - 1, eq)] if groups else [(int(t_from or 0), eq)]
    trades = []
    cost = (FEE + SLIP) * cost_mult
    for key in sorted(groups):
        rs = groups[key]
        w = 1.0 / max(len(rs), K)
        pnl = 0.0
        for (_, ie, ix, j, s) in rs:
            notional = eq * w
            if liq_cap and np.isfinite(QV[ie, j]):
                notional = min(notional, 0.01 * QV[ie, j])
            px = O[ix, j]
            if not np.isfinite(px):
                seg = C[ie:ix, j]; fin = seg[np.isfinite(seg)]
                px = fin[-1] if len(fin) else O[ie, j]
            ret = s * (px / O[ie, j] - 1) - fund_paid(ie, ix, j, s, conv) - 2 * cost
            p_ = notional * ret
            pnl += p_
            trades.append((key[0] // 24, j, s, p_, p_ / eq, notional / eq, ret, key[1]))
        eq = max(eq + pnl, 0.0)
        curve.append((int(th[rs[0][2]]), eq))
        if eq <= 0:
            break
    return curve, trades


def sim(p, a, b, cost_mult=1.0, conv="exact", exclude=(), liq_cap=True):
    rows = legs(p, a, b, exclude)
    c, tr = run(rows, p["K"], cost_mult, conv, liq_cap=liq_cap, t_from=td[a])
    c.append((int(td[b - 1] + DAY - 1), c[-1][1]))
    return c, tr, rows


def stats(c, tr=None):
    s = summary(c, [t[3] for t in tr] if tr is not None else None)
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))), 2) if len(v) > 2 and v.std() > 0 else None
    s["trades_per_month"] = round(len(tr) / len(v), 1) if tr is not None and len(v) else None
    return s


def yearly(months):
    out = {}
    for k_, v in months.items():
        out.setdefault(k_[:4], 1.0)
        out[k_[:4]] *= 1 + v / 100
    return {y: round((g - 1) * 100, 2) for y, g in out.items()}
