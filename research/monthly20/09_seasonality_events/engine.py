"""Hourly portfolio backtester for calendar strategies.

W: DataFrame (hourly UTC index x symbols) of target weights in [-1, 1]; gross sum per row <= 1.
The weight for bar t is known before bar t opens (calendars are scheduled), the trade fills at bar t's open.
Leverage L multiplies the notional: notional_i = w_i * equity * L, isolated margin |w_i| * equity per position.
Costs: (FEE+SLIP)*cost_mult per side on traded notional. Funding charged on positions held through 00/08/16 UTC.
Liquidation: a position is wiped (loses its margin) when the adverse move from entry reaches 1/L - MAINT_MARGIN,
checked on bar highs/lows. Entry notional capped at 1% of the entry bar's quote volume."""
from base import *


def backtest(W, frames, L=1.0, cost_mult=1.0, start=None, end=None):
    syms = list(W.columns)
    idx = W.index
    if start is not None:
        idx = idx[(idx >= pd.Timestamp(start, unit="ms", tz="UTC")) & (idx < pd.Timestamp(end, unit="ms", tz="UTC"))]
    W = W.loc[idx].fillna(0.0)
    cost = (FEE + SLIP) * cost_mult
    liq = 1.0 / L - MAINT_MARGIN if L > 1 else 10.0
    data = {}
    for s in syms:
        f = frames[s].reindex(idx)
        fr = dict(funding(s))
        fund = np.array([fr.get(int(t.value // 10**6) + HOUR, np.nan) for t in idx])  # stamp at the end of bar t
        data[s] = (f.o.values, f.h.values, f.l.values, f.c.values, f.qv.values, fund)
    eq = 1.0
    qty = {s: 0.0 for s in syms}; entry = {s: 0.0 for s in syms}; margin = {s: 0.0 for s in syms}
    wcur = {s: 0.0 for s in syms}
    curve, trades, open_pnl = [], [], {s: 0.0 for s in syms}
    Wv = W.values
    n_liq = 0
    for i, t in enumerate(idx):
        for j, s in enumerate(syms):
            o, h, l, c, qv, fund = data[s]
            if np.isnan(o[i]):
                continue
            w = Wv[i, j]
            if w != wcur[s]:
                # close the old position completely, open the new one (schedule strategies flip whole blocks)
                if qty[s] != 0:
                    pnl = qty[s] * (o[i] - entry[s]) - abs(qty[s]) * o[i] * cost
                    eq += pnl; trades.append(open_pnl[s] + pnl); open_pnl[s] = 0.0
                    qty[s] = 0.0
                if w != 0 and eq > 0:
                    notional = min(abs(w) * eq * L, 0.01 * qv[i]) if qv[i] > 0 else 0.0
                    if notional > 0:
                        qty[s] = np.sign(w) * notional / o[i]; entry[s] = o[i]; margin[s] = notional / L
                        eq -= notional * cost; open_pnl[s] = -notional * cost
                wcur[s] = w
            if qty[s] != 0:
                adverse = (entry[s] - l[i]) / entry[s] if qty[s] > 0 else (h[i] - entry[s]) / entry[s]
                if adverse >= liq:
                    eq -= margin[s]; trades.append(open_pnl[s] - margin[s]); open_pnl[s] = 0.0
                    qty[s] = 0.0; n_liq += 1
                elif not np.isnan(fund[i]):
                    fpay = qty[s] * c[i] * fund[i]
                    eq -= fpay; open_pnl[s] -= fpay
        mtm = eq + sum(qty[s] * (data[s][3][i] - entry[s]) for s in syms if qty[s] != 0 and not np.isnan(data[s][3][i]))
        curve.append((int(t.value // 10**6), max(mtm, 0.0)))
        if mtm <= 0:
            break
    for s in syms:   # close at the end
        if qty[s] != 0:
            c = data[s][3][~np.isnan(data[s][3])][-1]
            pnl = qty[s] * (c - entry[s]) - abs(qty[s]) * c * cost
            eq += pnl; trades.append(open_pnl[s] + pnl)
    out = summary(curve, trades)
    out["liquidations"] = n_liq
    out["leverage"] = L
    return out
