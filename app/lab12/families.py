"""Renata V12 research: the strategy families (docs/V12_RESEARCH.md). Each config turns a Window into Events using
only bars that closed before the decision. Decisions are at hour (or 15-minute) closes; event.t is the 5-minute bar
that STARTS at the decision instant, so the entry fills one minute later (engine.trade).

Every config is a plain dict: {"family", "name", ...parameters}; `generate(w, panel, cfg)` dispatches on "family".
The stop of a time-based family is a wide guard sized to the holding period: 1.5 x ATR(1h) x sqrt(hold hours),
clamped 1-25% -- it also defines the R unit, so results are comparable across holds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.lab12.engine import Events, HOUR, Window, atr, btc_regime_hourly, resample, rolling_mean


@dataclass
class Panel:
    syms: list[str]
    h: np.ndarray            # (coins, hours, 5) hourly bars
    atr_h: np.ndarray        # (coins, hours) ATR14 of hourly bars, as a fraction of the close
    vol_rel: np.ndarray      # (coins, hours) quote volume / its trailing 7-day hourly mean
    regime: np.ndarray       # (hours,) BTC regime known at the START of hour i
    first: int               # first decision hour index (its close is >= window start)
    last: int                # last decision hour index


def panel(w: Window) -> Panel:
    syms = sorted(w.coins)
    hrs = [resample(w.coins[s].bars, 12) for s in syms]
    nh = min(len(x) for x in hrs)
    h = np.stack([x[:nh] for x in hrs])
    a = np.stack([atr(x[:nh]) / x[:nh, 3] for x in hrs])
    vr = np.stack([x[:nh, 4] / rolling_mean(x[:nh, 4], 168) for x in hrs])
    first = max(200, int(math.ceil((w.start - w.a) / HOUR)) - 1)
    last = min(nh - 30, int((w.end - w.a) // HOUR) - 1)
    return Panel(syms, h, a, vr, btc_regime_hourly(w)[:nh], first, last)


def guard_stop(atr_pct: np.ndarray, hold_h: float) -> np.ndarray:
    return np.clip(1.5 * atr_pct * math.sqrt(max(hold_h, 0.25)), 0.01, 0.25)


def _ev(rows: list[tuple]) -> Events:
    if not rows:
        return Events.empty()
    t, c, s, st, ho, sc = zip(*rows)
    return Events(np.array(t), np.array(c), np.array(s, float), np.array(st, float), np.array(ho), np.array(sc, float))


def gate(p: Panel, i: int, side: int, cfg: dict) -> bool:
    """Experiment C overlay: with regime=True, longs only while BTC is up, shorts only while BTC is down."""
    if not cfg.get("regime"):
        return True
    return int(p.regime[i + 1]) == side if i + 1 < len(p.regime) else False


# -- A: momentum ----------------------------------------------------------------------------------------------------------
def xs_momentum(w: Window, p: Panel, cfg: dict) -> Events:
    L, H, k, mode = cfg["lookback_h"], cfg["hold_h"], cfg.get("k", 3), cfg["mode"]
    rows = []
    close = p.h[:, :, 3]
    for i in range(p.first, p.last):
        if (i + 1) % H:
            continue
        r = close[:, i] / close[:, i - L] - 1
        if cfg.get("accel"):
            r = r - (close[:, i] / close[:, i - 3 * L] - 1) / 3
        ok = ~np.isnan(r) & ~np.isnan(p.atr_h[:, i])
        if cfg.get("vol_min"):
            vr = np.nanmean(p.vol_rel[:, i - 23: i + 1], axis=1)
            ok &= vr >= cfg["vol_min"]
        idx = np.where(ok)[0]
        if len(idx) < 8:
            continue
        order = idx[np.argsort(r[idx])]
        t = 12 * (i + 1)
        picks = [(c, 1) for c in order[-k:]] if mode != "ew_long" else [(c, 1) for c in idx]
        if mode == "long_short":
            picks += [(c, -1) for c in order[:k]]
        for c, sd in picks:
            if gate(p, i, sd, cfg):
                rows.append((t, int(c), sd, float(guard_stop(p.atr_h[c, i], H)), H * 12, abs(float(r[c]))))
    return _ev(rows)


# -- B: breakouts ---------------------------------------------------------------------------------------------------------
def _prior_extremes(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn (nanmax / nanmin) of the n bars BEFORE each bar, per coin; NaN where fewer than n bars exist."""
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(x.shape, np.nan)
    if x.shape[1] <= n:
        return out
    win = sliding_window_view(x, n, axis=1)[:, :-1, :]          # windows ending at bar i-1, for bars n..end
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out[:, n:] = fn(win, axis=2)
    return out


def breakout(w: Window, p: Panel, cfg: dict) -> Events:
    N, H = cfg["n_h"], cfg["hold_h"]
    hi, lo, cl = p.h[:, :, 1], p.h[:, :, 2], p.h[:, :, 3]
    top, bot = _prior_extremes(hi, N, np.nanmax), _prior_extremes(lo, N, np.nanmin)
    with np.errstate(invalid="ignore"):
        if cfg.get("fade_failed"):
            prev_top = np.concatenate([np.full((len(p.syms), 1), np.nan), top[:, :-1]], axis=1)
            prev_bot = np.concatenate([np.full((len(p.syms), 1), np.nan), bot[:, :-1]], axis=1)
            prev_cl = np.concatenate([np.full((len(p.syms), 1), np.nan), cl[:, :-1]], axis=1)
            side = np.where((prev_cl > prev_top) & (cl < prev_top), -1, np.where((prev_cl < prev_bot) & (cl > prev_bot), 1, 0))
        else:
            side = np.where(cl > top, 1, np.where(cl < bot, -1, 0))
        ok = (side != 0) & ~np.isnan(p.atr_h)
        if cfg.get("vol_min"):
            ok &= p.vol_rel >= cfg["vol_min"]
        if cfg.get("compression"):
            ok &= p.atr_h < 0.75 * np.stack([rolling_mean(a, 720) for a in p.atr_h])
    ok[:, : p.first] = False
    ok[:, p.last:] = False
    rows = []
    for c, i in zip(*np.where(ok)):
        sd = int(side[c, i])
        if gate(p, int(i), sd, cfg):
            ref = top[c, i] if sd > 0 else bot[c, i]
            score = abs(cl[c, i] - ref) / (p.atr_h[c, i] * cl[c, i]) if not np.isnan(ref) else 0.0
            rows.append((12 * (int(i) + 1), int(c), sd, float(guard_stop(p.atr_h[c, i], H)), H * 12, float(score)))
    return _ev(rows)


# -- D: BTC leads, alts lag -----------------------------------------------------------------------------------------------
def btc_lead(w: Window, p: Panel, cfg: dict) -> Events:
    if "BTCUSDT" not in p.syms:
        return Events.empty()
    q = [resample(w.coins[s].bars, 3) for s in p.syms]                     # 15-minute bars
    n = min(len(x) for x in q)
    cl = np.stack([x[:n, 3] for x in q])
    a15 = np.stack([atr(x[:n]) / x[:n, 3] for x in q])
    b = p.syms.index("BTCUSDT")
    r15 = cl[:, 1:] / cl[:, :-1] - 1
    hold = cfg["hold_15m"]
    rows = []
    for j in range(max(4 * p.first, 100), min(4 * p.last, n - 2)):
        rb = r15[b, j - 1]                                                   # BTC's move over bar j (closed)
        if np.isnan(rb) or np.isnan(a15[b, j]) or abs(rb) < cfg["k_atr"] * a15[b, j]:
            continue
        side = 1 if rb > 0 else -1
        if not gate(p, j // 4, side, cfg):
            continue
        alts = [(c, r15[c, j - 1]) for c in range(len(p.syms)) if c != b and not np.isnan(r15[c, j - 1])
                and not np.isnan(a15[c, j]) and r15[c, j - 1] * side < 0.3 * abs(rb)]
        alts.sort(key=lambda x: x[1] * side)                                # the most lagging first
        for c, rc in alts[:3]:
            rows.append((3 * (j + 1), c, side, float(np.clip(1.5 * a15[c, j] * math.sqrt(hold), 0.005, 0.1)), hold * 3,
                         abs(rb) - rc * side))
    return _ev(rows)


# -- E: volatility expansion ----------------------------------------------------------------------------------------------
def vol_expansion(w: Window, p: Panel, cfg: dict) -> Events:
    H = cfg["hold_h"]
    rows = []
    for c in range(len(p.syms)):
        b = p.h[c]
        for i in range(p.first, p.last):
            a_prev = p.atr_h[c, i - 1]
            if np.isnan(b[i, 3]) or np.isnan(a_prev):
                continue
            rng = b[i, 1] - b[i, 2]
            if rng < cfg["range_atr"] * a_prev * b[i, 3] or not (p.vol_rel[c, i] >= cfg["vol_min"]):
                continue
            loc = (b[i, 3] - b[i, 2]) / rng if rng > 0 else 0.5
            side = 1 if loc >= 0.75 else -1 if loc <= 0.25 else 0
            if not side:
                continue
            if cfg["mode"] == "fade":
                side = -side
            if gate(p, i, side, cfg):
                rows.append((12 * (i + 1), c, side, float(guard_stop(p.atr_h[c, i], H)), H * 12, rng / (a_prev * b[i, 3])))
    return _ev(rows)


# -- G: funding and open interest -----------------------------------------------------------------------------------------
def funding_extreme(w: Window, p: Panel, cfg: dict) -> Events:
    """One hour before each 00 / 08 / 16 UTC settlement: short the k highest last-settled rates, long the k lowest."""
    H, k = cfg["hold_h"], cfg.get("k", 3)
    rows = []
    for i in range(p.first, p.last):
        if (i + 2) % 8:                                   # the decision at the close of 23:00 / 07:00 / 15:00 hours
            continue
        t_ms = w.a + (i + 1) * HOUR
        rates = np.full(len(p.syms), np.nan)
        for c, s in enumerate(p.syms):
            f = w.coins[s].funding
            past = f[f[:, 0] <= t_ms] if len(f) else f
            if len(past) and not np.isnan(p.atr_h[c, i]):
                rates[c] = past[-1, 1]
        idx = np.where(~np.isnan(rates))[0]
        if len(idx) < 8:
            continue
        order = idx[np.argsort(rates[idx])]
        for c, sd in [(c, -1) for c in order[-k:]] + [(c, 1) for c in order[:k]]:
            if abs(rates[c]) < cfg.get("min_rate", 0.0):
                continue
            if gate(p, i, sd, cfg):
                rows.append((12 * (i + 1), int(c), sd, float(guard_stop(p.atr_h[c, i], H)), H * 12, abs(float(rates[c]))))
    return _ev(rows)


def oi_divergence(w: Window, p: Panel, cfg: dict, root: str | Path) -> Events:
    """24 h price move vs 24 h open-interest change (positioning archive; only windows that have it)."""
    import csv
    H = cfg["hold_h"]
    rows = []
    for c, s in enumerate(p.syms):
        f = Path(root) / "positioning" / s / "oi_1h.csv"
        if not f.exists():
            continue
        oi = np.full(p.h.shape[1], np.nan)
        for r in csv.reader(open(f)):
            if r and r[0].isdigit():
                i = (int(r[0]) - w.a) // HOUR
                if 0 <= i < len(oi):
                    oi[i] = float(r[1])
        for i in range(p.first, p.last):
            if np.isnan(oi[i]) or np.isnan(oi[i - 24]) or np.isnan(p.h[c, i, 3]) or np.isnan(p.h[c, i - 24, 3]):
                continue
            dp = p.h[c, i, 3] / p.h[c, i - 24, 3] - 1
            doi = oi[i] / oi[i - 24] - 1
            if abs(dp) < 0.03 or abs(doi) < 0.05:
                continue
            new_money = np.sign(doi) > 0
            side = int(np.sign(dp)) if (new_money == (cfg["mode"] == "follow_new_money")) else 0
            if cfg["mode"] == "fade_covering" and not new_money:
                side = -int(np.sign(dp))
            if side and gate(p, i, side, cfg):
                rows.append((12 * (i + 1), c, side, float(guard_stop(p.atr_h[c, i], H)), H * 12, abs(doi)))
    return _ev(rows)


def generate(w: Window, p: Panel, cfg: dict, root: str | Path | None = None) -> Events:
    fam = cfg["family"]
    if fam == "A":
        return xs_momentum(w, p, cfg)
    if fam == "B":
        return breakout(w, p, cfg)
    if fam == "D":
        return btc_lead(w, p, cfg)
    if fam == "E":
        return vol_expansion(w, p, cfg)
    if fam == "G1":
        return funding_extreme(w, p, cfg)
    if fam == "G2":
        return oi_divergence(w, p, cfg, root) if root else Events.empty()
    raise ValueError(fam)


def configs() -> list[dict[str, Any]]:
    """The pre-registered round-1 grid (docs/V12_RESEARCH.md); every config also runs with the C regime overlay."""
    base: list[dict[str, Any]] = []
    for L in (4, 24, 72, 168):
        for H in (4, 24):
            for mode in ("long_short", "long_only"):
                base.append({"family": "A", "name": f"A xs-mom L{L}h H{H}h {mode}", "lookback_h": L, "hold_h": H, "mode": mode})
    for H in (4, 24):
        base.append({"family": "A", "name": f"A benchmark: equal-weight long all H{H}h", "lookback_h": 24, "hold_h": H, "mode": "ew_long"})
    for L in (24, 72):
        base.append({"family": "A", "name": f"A xs-mom L{L}h H24h volume-confirmed", "lookback_h": L, "hold_h": 24,
                     "mode": "long_short", "vol_min": 1.3})
    base.append({"family": "A", "name": "A xs-mom acceleration L24h H24h", "lookback_h": 24, "hold_h": 24, "mode": "long_short", "accel": True})
    for N in (24, 72):
        for vm in (None, 1.5):
            for H in (4, 24):
                base.append({"family": "B", "name": f"B donchian N{N}h H{H}h vol{vm or '-'}", "n_h": N, "hold_h": H, "vol_min": vm})
    for H in (4, 24):
        base.append({"family": "B", "name": f"B compression breakout N24h H{H}h", "n_h": 24, "hold_h": H, "compression": True})
    base.append({"family": "B", "name": "B failed-breakout fade N24h H4h", "n_h": 24, "hold_h": 4, "fade_failed": True})
    for k in (1.5, 2.5):
        for hold in (1, 4):
            base.append({"family": "D", "name": f"D btc-lead k{k} hold{hold * 15}m", "k_atr": k, "hold_15m": hold})
    for mode in ("follow", "fade"):
        for H in (4, 24):
            base.append({"family": "E", "name": f"E vol-expansion {mode} H{H}h", "range_atr": 2.5, "vol_min": 3.0, "hold_h": H, "mode": mode})
    for H in (8, 24):
        base.append({"family": "G1", "name": f"G funding-extreme H{H}h", "hold_h": H})
    for mode in ("follow_new_money", "fade_covering"):
        base.append({"family": "G2", "name": f"G oi-divergence {mode} H24h", "hold_h": 24, "mode": mode})
    out = []
    for c in base:
        out.append(c)
        if "benchmark" not in c["name"]:
            out.append({**c, "name": c["name"] + " +C regime", "regime": True})
    return out
