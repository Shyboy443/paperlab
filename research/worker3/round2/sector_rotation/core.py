"""Sector (narrative) momentum on the survivorship-free daily panel (research/monthly20/r2_rotation/data.py).

Sector map: data/w3r2_sector_rotation/sector_map.json (built by sector_map.py from CoinGecko public categories, one coarse
label per coin, frozen before any backtest). Coins without a label are never traded.

Signal at rebalance bar i (trades at the OPEN of bar i, every score uses closes up to c[i-1] only):
  members(s, i) = coins of sector s inside the point-in-time top-`univ` (30d quote volume) universe with K full days of data
  R_s = equal-weight mean of members' K-day return c[i-1]/c[i-1-K]-1 ; sectors with < MIN_M members are skipped
  rank sectors by R_s.  LS: long all members of the top S sectors, short all members of the bottom S sectors (each side
  0.5 of equity, sectors equal inside a side, coins equal inside a sector).  LO: long top S sectors with 1.0 of equity.
  LOG: LO but flat when BTC c[i-1] < its 50-day SMA (BTC gate).  1x gross always.
Engine = agent-02 / engine_r2.run logic (next-open fills, fee+slip per side on traded notional, funding, 1% bar-volume cap,
liquidation check) with arbitrary target weights.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import FEE, SLIP, MAINT_MARGIN, DAY, summary, DEV_START, DEV_END, TEST_START, TEST_END  # noqa

MAP = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_sector_rotation/sector_map.json")
MIN_M = 3
D = build()
P = E.Panel(D)
_UM = {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


def sectors_for(Pn):
    m = json.loads(MAP.read_text())["map"]
    labs = sorted(set(m.values()))
    sid = np.array([labs.index(m[s]) if s in m else -1 for s in Pn.syms])
    return sid, labs


def ret_k(Pn, K):
    c = Pn.c
    out = np.full_like(c, np.nan)
    out[1 + K:] = c[K:-1] / c[:-1 - K] - 1
    return out


def btc_gate(Pn, n=50):
    b = Pn.syms.index("BTCUSDT")
    c = Pn.c[:, b]
    sma = np.full_like(c, np.nan)
    ok = ~np.isnan(c)
    cs = np.r_[0, np.cumsum(np.where(ok, c, 0.0))]; cn = np.r_[0, np.cumsum(ok)]
    with np.errstate(invalid="ignore", divide="ignore"):
        sma[n:] = (cs[n:-1] - cs[:-1 - n]) / (cn[n:-1] - cn[:-1 - n])   # mean of c[i-n..i-1] at row i
    sma[n:][(cn[n:-1] - cn[:-1 - n]) < n] = np.nan
    prev = np.r_[np.nan, c[:-1]]
    return prev > sma                                  # known at the open of i (NaN -> False = flat)


def make_weights(Pn, K, S, mode, univ, sid=None, rng=None, gate_n=50):
    """Returns function i -> weight vector (fraction of equity).  rng/struct: random-picks version for check 6."""
    if sid is None:
        sid, _ = sectors_for(Pn)
    U = um(univ, Pn)
    R = ret_k(Pn, K)
    gate = btc_gate(Pn, gate_n) if mode == "LOG" else None
    nsec = sid.max() + 1
    log = {}

    def w_at(i):
        N = Pn.c.shape[1]
        w = np.zeros(N)
        if gate is not None and not gate[i]:
            log[i] = (0, 0, [], [])
            return w
        elig = U[i] & (sid >= 0) & ~np.isnan(R[i]) & ~np.isnan(Pn.o[i])
        secs = []
        for s in range(nsec):
            mem = np.where(elig & (sid == s))[0]
            if len(mem) >= MIN_M:
                secs.append((float(R[i, mem].mean()), s, mem))
        need = 2 * S if mode == "LS" else S
        if len(secs) < need + (1 if mode == "LS" else 0):
            log[i] = (0, 0, [], [])
            return w
        secs.sort(key=lambda x: -x[0])
        tops = secs[:S]
        bots = secs[::-1][:S] if mode == "LS" else []
        side_l = 0.5 if mode == "LS" else 1.0
        for _, s, mem in tops:
            w[mem] += side_l / S / len(mem)
        for _, s, mem in bots:
            w[mem] -= 0.5 / S / len(mem)
        nl, ns = int((w > 0).sum()), int((w < 0).sum())
        if rng is not None:   # same counts, random coins from the same eligible pool (labelled, in universe)
            pool = np.where(elig)[0]
            pick = rng.permutation(pool)[:nl + ns]
            w = np.zeros(N)
            w[pick[:nl]] = side_l / max(nl, 1)
            if ns:
                w[pick[nl:]] = -0.5 / ns
        log[i] = (nl, ns, [t[1] for t in tops], [t[1] for t in bots])
        return w
    w_at.log = log
    return w_at


def run_w(Pn, w_at, rb, start, end, cost_mult=1.0, equity0=10_000.0, record=False):
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(Pn.t, start)); i1 = int(np.searchsorted(Pn.t, end))
    N = Pn.c.shape[1]
    eq = equity0
    units = np.zeros(N); entry = np.zeros(N)
    curve = [(int(Pn.t[i0]), eq)]
    trade_pnl = []; pos_pnl = np.zeros(N); pos_open = np.zeros(N, int); pos_side = np.zeros(N)
    positions = []; n_liq = 0; n_orders = 0
    last_px = np.full(N, np.nan)

    def close(j, i):
        trade_pnl.append(pos_pnl[j])
        if record:
            positions.append((j, pos_side[j], pos_open[j], i, pos_pnl[j]))
        pos_pnl[j] = 0.0

    for i in range(i0, i1):
        o, h, l, c = Pn.o[i], Pn.h[i], Pn.l[i], Pn.c[i]
        live = units != 0
        if (i - i0) % rb == 0:
            px = np.where(np.isnan(o), last_px, o)
            if live.any():
                g = units[live] * (px[live] - last_px[live])
                eq += g.sum(); pos_pnl[live] += g
            w = w_at(i)
            w = np.where(np.isnan(o), 0.0, w)
            tgt = w * eq
            cap = 0.01 * np.nan_to_num(Pn.qv[i - 1])
            tgt = np.clip(tgt, -cap, cap)
            cur = units * np.nan_to_num(px)
            traded = np.abs(tgt - cur)
            n_orders += int((traded > 1e-9 * eq).sum())
            eq -= traded.sum() * cost
            for j in np.where(live)[0]:
                if tgt[j] == 0 or np.sign(tgt[j]) != np.sign(units[j]):
                    close(j, i)
            pos_pnl -= traded * cost
            new = (tgt != 0) & ((units == 0) | (np.sign(tgt) != np.sign(units)))
            pos_open[new] = i; pos_side[new] = np.sign(tgt[new])
            units = np.where(tgt != 0, tgt / np.where(np.isnan(o), 1, o), 0.0)
            entry = np.where(tgt != 0, o, entry)
            last_px = np.where(tgt != 0, o, last_px)
            live = units != 0
        if not live.any():
            last_px = np.where(np.isnan(c), last_px, c)
            curve.append((int(Pn.t[i] + Pn.step), eq)); continue
        notional = units * np.nan_to_num(last_px)
        f = np.where(live, -(notional * Pn.fund[i]), 0.0)
        eq += f.sum(); pos_pnl += f
        hh = np.where(np.isnan(h), last_px, h)
        thr = 1.0 - MAINT_MARGIN
        adv_short = (hh - entry) / np.where(entry > 0, entry, 1)
        liq = live & (units < 0) & (adv_short >= thr)
        cc = np.where(np.isnan(c), last_px, c)
        g = np.where(live & ~liq, units * (cc - last_px), 0.0)
        eq += g.sum(); pos_pnl += g
        for j in np.where(liq)[0]:
            loss = units[j] * (entry[j] * (1 + thr) - last_px[j])
            eq += loss; pos_pnl[j] += loss
            close(j, i + 1); units[j] = 0.0; n_liq += 1
        last_px = np.where(np.isnan(c), last_px, c)
        curve.append((int(Pn.t[i] + Pn.step), eq))
    for j in np.where(units != 0)[0]:
        close(j, i1)
    out = summary(curve, trade_pnl)
    out["liquidations"] = n_liq; out["orders"] = n_orders
    if record:
        out["_curve"] = curve; out["_positions"] = positions
    return out


def run_cfg(K, S, mode, univ, rb=7, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None, rng=None, gate_n=50):
    Pn = Pn or P
    w = make_weights(Pn, K, S, mode, univ, rng=rng, gate_n=gate_n)
    r = run_w(Pn, w, rb, start, end, cost_mult=cost_mult, record=record)
    r["_log"] = w.log
    return r


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")
