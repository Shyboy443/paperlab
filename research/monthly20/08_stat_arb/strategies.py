"""Agent 08 - the three stat-arb families. Every function returns a target-weight DataFrame (gross ~1)
decided at the close of each bar using data up to that bar only."""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from engine import COINS, SECTORS, rolling_beta

FACT = ["BTCUSDT", "ETHUSDT"]


def returns(D):
    return np.log(D["c"]).diff()


def residuals(D, win=720, refit=24):
    r = returns(D)
    alts = [c for c in r.columns if c not in FACT]
    B = rolling_beta(r, alts, FACT, win=win, refit=refit)
    E = pd.DataFrame({a: r[a] - B[a]["BTCUSDT"] * r["BTCUSDT"] - B[a]["ETHUSDT"] * r["ETHUSDT"] for a in alts})
    return r, E, B


def _hedge_and_norm(wa, B, cols):
    """wa: alt weights DataFrame. Add BTC/ETH legs that cancel the alts' factor exposure; gross = 1."""
    w = wa.copy()
    w["BTCUSDT"] = -sum(wa[a].fillna(0) * B[a]["BTCUSDT"].fillna(0) for a in wa.columns)
    w["ETHUSDT"] = -sum(wa[a].fillna(0) * B[a]["ETHUSDT"].fillna(0) for a in wa.columns)
    g = w.abs().sum(1).replace(0, np.nan)
    return w.div(g, axis=0).fillna(0.0).reindex(columns=cols, fill_value=0.0)


def resid_reversal(D, r, E, B, look=24, z_entry=0.0, mode="cs"):
    """A. Residual mean reversion. Score = residual return summed over `look` hours.
    mode cs: cross-sectional z; ts: own z vs its 30-day history. Weight = -z where |z| > z_entry."""
    s = E.rolling(look, min_periods=look).sum()
    if mode == "cs":
        z = s.sub(s.mean(1), axis=0).div(s.std(1), axis=0)
    else:
        z = s / s.rolling(720, min_periods=500).std()
    wa = -z.where(z.abs() > z_entry, 0.0)
    if mode == "cs":
        wa = wa.sub(wa.mean(1), axis=0).where(s.notna(), 0.0) if z_entry == 0 else wa
    return _hedge_and_norm(wa.fillna(0.0), B, D["o"].columns)


def lead_lag(D, r, E, B, k=2.0, look=1, sign=+1):
    """C. Lead-lag. When BTC's last-`look`-hour move exceeds k sigma, bet the alts' residual follows it next
    (sign=+1: alts catch up -> long alts / short beta-hedge) or reverts (sign=-1)."""
    rb = r["BTCUSDT"].rolling(look).sum()
    sig = rb / rb.rolling(720, min_periods=500).std()
    trig = np.sign(sig).where(sig.abs() > k, 0.0) * sign
    wa = pd.DataFrame({a: trig * B[a]["BTCUSDT"].notna() for a in E.columns}).where(E.notna(), 0.0)
    return _hedge_and_norm(wa.fillna(0.0), B, D["o"].columns)


def sector_pairs(sectors=None):
    sectors = sectors or {k: v for k, v in SECTORS.items() if k != "MAJORS"}
    out = []
    for name, cs in sectors.items():
        out += [(name, a, b) for a, b in itertools.combinations(cs, 2)]
    return out


def pairs(D, beta_win=720, z_win=168, entry=2.0, exit_=0.5, stop=4.0, max_hold=240, refit=24, pairs_list=None, norm="all"):
    """B. Sector pairs. Spread = log A - beta log B with beta from rolling OLS on past log prices (refit daily);
    z of the spread vs its own past z_win hours. Enter |z|>entry against the spread, exit |z|<exit_,
    stop |z|>stop or after max_hold hours. Capital split equally across all pairs that are tradeable that bar."""
    lp = np.log(D["c"])
    cols = list(D["o"].columns)
    ci = {c: i for i, c in enumerate(cols)}
    T = len(lp)
    W = np.zeros((T, len(cols)))
    active_count = np.zeros(T)
    pl = pairs_list or sector_pairs()
    contrib = []
    for _, a, b in pl:
        if a not in ci or b not in ci:
            continue
        ya, yb = lp[a].values, lp[b].values
        beta = np.full(T, np.nan)
        cur = np.nan
        for t in range(beta_win, T):
            if t % refit == 0:
                x, y = yb[t - beta_win + 1:t + 1], ya[t - beta_win + 1:t + 1]
                m = np.isfinite(x) & np.isfinite(y)
                if m.sum() > beta_win * 0.9:
                    X = np.column_stack([np.ones(m.sum()), x[m]])
                    cur = np.linalg.lstsq(X, y[m], rcond=None)[0][1]
                else:
                    cur = np.nan
            beta[t] = cur
        spread = pd.Series(ya - beta * yb)
        # z uses a spread built with today's beta over the past window (consistent, causal)
        mu = spread.rolling(z_win, min_periods=z_win).mean().values
        sd = spread.rolling(z_win, min_periods=z_win).std().values
        z = (spread.values - mu) / sd
        pos, held, bfix, blocked = 0, 0, np.nan, False
        wa = np.zeros(T)
        wb = np.zeros(T)
        live = np.isfinite(z) & (beta > 0)
        for t in range(T):
            if not live[t]:
                pos, blocked = 0, False
                continue
            active_count[t] += 1
            if blocked and abs(z[t]) < entry:
                blocked = False
            if pos != 0:
                held += 1
                if abs(z[t]) < exit_ or np.sign(z[t]) == pos or abs(z[t]) > stop or held >= max_hold:
                    blocked = abs(z[t]) >= entry
                    pos = 0
                    continue
            elif not blocked:
                if z[t] > entry:
                    pos, held, bfix = -1, 0, beta[t]
                elif z[t] < -entry:
                    pos, held, bfix = 1, 0, beta[t]
            if pos != 0:
                g = 1 + abs(bfix)
                wa[t] = pos / g
                wb[t] = -pos * bfix / g
        W[:, ci[a]] += wa
        W[:, ci[b]] += wb
        contrib.append((a, b))
    if norm == "open":
        g = np.abs(W).sum(1)
        W = W / np.maximum(g, 1.0)[:, None]
    else:
        W = W / np.maximum(active_count, 1)[:, None]
    return pd.DataFrame(W, index=D["o"].index, columns=cols)


def shock(D, r, E, B, up_look=4, up_z=4.0, dn_look=1, dn_z=-4.0, hold=24, slots=10, use_up=True, use_dn=True,
          control=False):
    """D. Residual-shock events (found on DEV): after a big DOWN residual shock the alt bounces; after a big UP
    residual shock over 4h it keeps outperforming. Both are LONG alt / SHORT beta-weighted BTC+ETH, held `hold` hours.
    Each event takes one slot of 1/slots of equity (alt notional); new events for an alt already held restart the clock.
    control=True: hold every alt long-hedged at all times (measures the residual drift / survivorship baseline)."""
    sd = E.rolling(720, min_periods=500).std()
    zu = E.rolling(up_look).sum() / sd / np.sqrt(up_look)
    zd = E.rolling(dn_look).sum() / sd / np.sqrt(dn_look)
    ev = pd.DataFrame(False, index=E.index, columns=E.columns)
    if use_up:
        ev |= zu > up_z
    if use_dn:
        ev |= zd < dn_z
    if control:
        wa = E.notna().astype(float) / slots
    else:
        evv = ev.values
        T, N = evv.shape
        last = np.full(N, -10**9)
        act = np.zeros((T, N))
        for t in range(T):
            last = np.where(evv[t], t, last)
            act[t] = (t - last) < hold
        wa = pd.DataFrame(act / slots, index=E.index, columns=E.columns)
        g = wa.sum(1)
        wa = wa.div(np.maximum(g, 1.0), axis=0)  # never more than 1x alt notional
    w = wa.copy()
    w["BTCUSDT"] = -sum(wa[a] * B[a]["BTCUSDT"].fillna(0) for a in wa.columns)
    w["ETHUSDT"] = -sum(wa[a] * B[a]["ETHUSDT"].fillna(0) for a in wa.columns)
    return w.fillna(0.0).reindex(columns=D["o"].columns, fill_value=0.0)


def shock_vs_basket(D, r, E, B, hold=24, slots=5, up_z=4.0, dn_z=-4.0, use_up=True, use_dn=True):
    """D2. Same events as `shock`, but each long alt is hedged with an equal-weight SHORT basket of all the other
    alts available that bar (removes the alt-vs-BTC drift that the BTC/ETH hedge leaves in)."""
    w0 = shock(D, r, E, B, hold=hold, slots=slots, up_z=up_z, dn_z=dn_z, use_up=use_up, use_dn=use_dn)
    alts = list(E.columns)
    wa = w0[alts]
    avail = E.notna() & D["o"][alts].notna()
    n_av = avail.sum(1).clip(lower=2)
    tot = wa.sum(1)
    short_each = avail.astype(float).mul(tot / n_av, axis=0)
    w = wa - short_each
    return w.reindex(columns=D["o"].columns, fill_value=0.0).fillna(0.0)
