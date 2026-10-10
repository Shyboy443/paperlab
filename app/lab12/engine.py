"""Renata V12 research engine (docs/V12_RESEARCH.md): point-in-time features, realistic trade simulation and robust
statistics over the 1-minute archives. Research only -- nothing here runs a bot or touches an exchange.

Data: an archive directory in the scripts/backtest_2y.py layout (Bybit, or Binance's public archive for the final
untouched period): 1m klines per coin and month, funding settlements per coin and month.

Execution model (conservative):
- a decision is taken at a bar close from COMPLETE bars only (a bar with a missing minute is not a bar);
- the entry fills at the OPEN of the first 1-minute bar after the decision (1 minute of latency), as a taker:
  fee 5.5 bp + slippage 2 bp (BTC ETH SOL XRP BNB DOGE) or 4 bp (every other coin), per side;
- the stop and the path are checked on 5-minute bars from the bar that contains the entry; if a bar reaches the stop
  it fills at the stop, or at the bar's open when the bar opens through it; time exits fill at the last bar's close
  as a taker; there are no take-profits (no resting-limit optimism);
- funding: every settlement between entry and exit is charged (a long pays a positive rate).
Portfolio: at most `max_pos` open positions, one per coin; a signal arriving when the book is full is skipped; each
position is equity / max_pos of notional (no leverage), so portfolio returns are additive fractions of equity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
BAR = 5 * MIN                                            # path resolution
TAKER = 5.5e-4
MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"}


def slip(sym: str) -> float:
    return 2e-4 if sym in MAJORS else 4e-4


# -- data ---------------------------------------------------------------------------------------------------------------
@dataclass
class Coin:
    sym: str
    open1m: np.ndarray                                   # (n_min,) 1m opens, NaN when missing
    bars: np.ndarray                                     # (n_bars, 5) 5m open/high/low/close/quote volume, NaN = incomplete
    funding: np.ndarray                                  # (k, 2) settlement ms, rate


@dataclass
class Window:
    name: str
    start: int                                           # first decision ms (after warm-up)
    a: int                                               # data origin ms (day aligned)
    end: int
    coins: dict[str, Coin] = field(default_factory=dict)

    @property
    def n_bars(self) -> int:
        return min(len(c.bars) for c in self.coins.values()) if self.coins else 0


def load_window(name: str, root: str | Path, start: int, end: int, syms: Sequence[str], warm_days: int = 35) -> Window:
    import scripts.backtest_2y as bt
    root = Path(root)
    a = (start - warm_days * DAY) // DAY * DAY
    b = (end + 2 * DAY) // DAY * DAY
    n = (b - a) // MIN
    w = Window(name, start, a, end)
    for sym in syms:
        o1 = np.full(n, np.nan, np.float32)
        m = np.full((n, 5), np.nan, np.float32)
        seen = False
        for month in bt.months(a, b):
            d = bt.load_month(root, sym, month)
            if d is None:
                continue
            ts = np.asarray(d["ts"], dtype=np.int64)
            idx = (ts - a) // MIN
            ok = (idx >= 0) & (idx < n)
            if not ok.any():
                continue
            seen = True
            o, h, l, c, v, q = (np.asarray(col, dtype=np.float64)[ok] for col in d["cols"])
            k = idx[ok]
            o1[k] = o
            m[k, 0], m[k, 1], m[k, 2], m[k, 3] = o, h, l, c
            m[k, 4] = q if q.any() else v * c
        if not seen:
            continue
        nb = n // 5
        x = m[: nb * 5].reshape(nb, 5, 5)
        full = ~np.isnan(x[:, :, 3]).any(axis=1)
        bars = np.full((nb, 5), np.nan, np.float32)
        bars[full, 0] = x[full, 0, 0]
        bars[full, 1] = x[full, :, 1].max(axis=1)
        bars[full, 2] = x[full, :, 2].min(axis=1)
        bars[full, 3] = x[full, -1, 3]
        bars[full, 4] = x[full, :, 4].sum(axis=1)
        fr = np.asarray(bt.funding_rows(root, sym, a, b) or [], dtype=np.float64).reshape(-1, 2)
        w.coins[sym] = Coin(sym, o1, bars, fr)
    return w


def resample(bars: np.ndarray, k: int) -> np.ndarray:
    """k consecutive 5m bars -> one bar (open, high, low, close, quote volume); NaN when any part is missing."""
    nb = len(bars) // k
    x = bars[: nb * k].reshape(nb, k, 5)
    full = ~np.isnan(x[:, :, 3]).any(axis=1)
    out = np.full((nb, 5), np.nan, np.float32)
    out[full, 0] = x[full, 0, 0]
    out[full, 1] = x[full, :, 1].max(axis=1)
    out[full, 2] = x[full, :, 2].min(axis=1)
    out[full, 3] = x[full, -1, 3]
    out[full, 4] = x[full, :, 4].sum(axis=1)
    return out


def atr(b: np.ndarray, n: int = 14) -> np.ndarray:
    """Mean true range of the last n complete bars (NaN until n complete bars in a row exist)."""
    prev = np.concatenate([[np.nan], b[:-1, 3]])
    tr = np.nanmax(np.stack([b[:, 1] - b[:, 2], np.abs(b[:, 1] - prev), np.abs(b[:, 2] - prev)]), axis=0)
    tr[np.isnan(b[:, 3]) | np.isnan(prev)] = np.nan
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(tr))])
    bad = np.concatenate([[0], np.cumsum(np.isnan(tr))])
    out = np.full(len(tr), np.nan)
    i = np.arange(n - 1, len(tr))
    ok = bad[i + 1] - bad[i + 1 - n] == 0
    out[i[ok]] = (c[i[ok] + 1] - c[i[ok] + 1 - n]) / n
    return out


def rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    """Mean of the previous n values (excluding the current one); NaN-tolerant (needs >= n/2 values)."""
    v = np.nan_to_num(x.astype(np.float64))
    cnt = (~np.isnan(x)).astype(np.float64)
    cs, cc = np.concatenate([[0], np.cumsum(v)]), np.concatenate([[0], np.cumsum(cnt)])
    out = np.full(len(x), np.nan)
    i = np.arange(n, len(x))
    s, k = cs[i] - cs[i - n], cc[i] - cc[i - n]
    ok = k >= n / 2
    out[i[ok]] = s[ok] / k[ok]
    return out


# -- events and simulation ----------------------------------------------------------------------------------------------
@dataclass
class Events:
    """Signals: decision bar index (5m units, the decision is at the START of this bar), coin, side (+1 / -1), stop as
    a fraction of price, hold in 5m bars, and a score (higher first when the book is crowded)."""
    t: np.ndarray
    coin: np.ndarray
    side: np.ndarray
    stop: np.ndarray
    hold: np.ndarray
    score: np.ndarray

    @staticmethod
    def empty() -> "Events":
        z = np.zeros(0)
        return Events(z.astype(int), z.astype(int), z, z, z.astype(int), z)

    @staticmethod
    def concat(parts: list["Events"]) -> "Events":
        parts = [p for p in parts if len(p.t)]
        if not parts:
            return Events.empty()
        return Events(*(np.concatenate([getattr(p, f) for p in parts]) for f in ("t", "coin", "side", "stop", "hold", "score")))


def simulate(w: Window, ev: Events, syms: list[str], max_pos: int = 6, cost_mult: float = 1.0) -> dict[str, np.ndarray]:
    """Run the events through the portfolio in time order. Returns per-trade arrays (only trades taken)."""
    order = np.lexsort((-ev.score, ev.t))
    busy_until = np.zeros(len(syms), dtype=np.int64)
    open_ends: list[int] = []
    out = {k: [] for k in ("t", "coin", "side", "ret", "r", "cost", "funding", "exit_t", "stopped")}
    for i in order:
        t, ci, sd = int(ev.t[i]), int(ev.coin[i]), float(ev.side[i])
        if busy_until[ci] > t:
            continue
        open_ends = [e for e in open_ends if e > t]
        if len(open_ends) >= max_pos:
            continue
        res = trade(w, syms[ci], t, sd, float(ev.stop[i]), int(ev.hold[i]), cost_mult)
        if res is None:
            continue
        ret, cost, fund, exit_t, stopped = res
        busy_until[ci] = exit_t
        open_ends.append(exit_t)
        for k, v in (("t", t), ("coin", ci), ("side", sd), ("ret", ret), ("r", ret / ev.stop[i]), ("cost", cost),
                     ("funding", fund), ("exit_t", exit_t), ("stopped", stopped)):
            out[k].append(v)
    return {k: np.asarray(v) for k, v in out.items()}


def trade(w: Window, sym: str, t: int, side: float, stop: float, hold: int, cost_mult: float = 1.0):
    """One trade: (net return on notional, costs, funding, exit bar, stopped?) or None when data is missing."""
    c = w.coins[sym]
    m0 = t * 5 + 1                                       # the minute after the decision
    if m0 >= len(c.open1m) or t + hold >= len(c.bars):
        return None
    entry = float(c.open1m[m0])
    if not entry or math.isnan(entry):
        return None
    seg = c.bars[t: t + hold]
    if np.isnan(seg[:, 3]).any():
        return None
    level = entry * (1 - side * stop)
    hit = (seg[:, 2] <= level) if side > 0 else (seg[:, 1] >= level)
    stopped = bool(hit.any())
    if stopped:
        j = int(hit.argmax())
        o = float(seg[j, 0])
        px = min(level, o) if side > 0 else max(level, o)
        if j == 0:
            px = level                                   # the entry bar opened before we were filled
        exit_t = t + j + 1
    else:
        px = float(seg[-1, 3])
        exit_t = t + hold
    gross = side * (px / entry - 1.0)
    cost = 2 * (TAKER + slip(sym)) * cost_mult
    t_ms, e_ms = w.a + t * BAR + MIN, w.a + exit_t * BAR
    f = c.funding
    fund = 0.0
    if len(f):
        sel = (f[:, 0] > t_ms) & (f[:, 0] <= e_ms)
        fund = -side * float(f[sel, 1].sum())
    return gross - cost + fund, cost, fund, exit_t, stopped


# -- statistics ---------------------------------------------------------------------------------------------------------
def metrics(w: Window, tr: dict[str, np.ndarray], syms: list[str], max_pos: int = 6, regime: np.ndarray | None = None,
            seed: int = 7) -> dict[str, Any]:
    n = len(tr["r"])
    if n == 0:
        return {"trades": 0}
    r, ret = tr["r"], tr["ret"]
    day = ((w.a + tr["t"] * BAR) // DAY).astype(np.int64)
    days = np.unique(day)
    daily_r = np.array([r[day == d].sum() for d in days])
    span = max(1.0, (w.end - w.start) / DAY)
    # every calendar day counts (a day without trades is a 0 day), so t and the CI are not flattered by idle periods
    full = np.zeros(int(math.ceil(span)))
    di = ((days * DAY - w.start) // DAY).astype(int)
    keep = (di >= 0) & (di < len(full))
    full[di[keep]] = daily_r[keep]
    t_stat = float(full.mean() / (full.std(ddof=1) / math.sqrt(len(full)))) if full.std() > 0 else 0.0
    rng = np.random.default_rng(seed)
    boots = rng.choice(full, size=(1000, len(full)), replace=True).sum(axis=1) / max(1, n)
    port = np.cumsum(ret / max_pos)
    dd_port = float(np.max(np.maximum.accumulate(np.concatenate([[0], port]))[1:] - port))
    eq_r = np.cumsum(r)
    dd_r = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq_r]))[1:] - eq_r))
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    years = {}
    yr = np.array([int(np.datetime64(int(w.a + x * BAR), "ms").astype("datetime64[Y]").astype(int) + 1970) for x in tr["t"]])
    for y in np.unique(yr):
        s = yr == y
        years[int(y)] = {"trades": int(s.sum()), "expectancy_r": round(float(r[s].mean()), 4),
                         "portfolio_pct": round(float(100 * ret[s].sum() / max_pos), 2)}
    coins = {}
    for ci in np.unique(tr["coin"]):
        s = tr["coin"] == ci
        coins[syms[int(ci)]] = {"trades": int(s.sum()), "expectancy_r": round(float(r[s].mean()), 4)}
    out = {"trades": int(n), "per_day": round(n / span, 2), "win_rate": round(float((r > 0).mean()), 3),
           "pf": round(float(wins / losses), 3) if losses else None, "expectancy_r": round(float(r.mean()), 4),
           "ci95_r": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)],
           "t_daily": round(t_stat, 2), "net_r": round(float(r.sum()), 1), "max_dd_r": round(dd_r, 1),
           "portfolio_pct": round(float(100 * port[-1]), 2), "portfolio_max_dd_pct": round(100 * dd_port, 2),
           "avg_cost_bp": round(float(1e4 * tr["cost"].mean()), 1), "funding_pct": round(float(100 * tr["funding"].sum() / max_pos), 3),
           "stopped_share": round(float(tr["stopped"].mean()), 3), "years": years, "coins": coins}
    if regime is not None:
        reg = regime[np.clip(tr["t"] // 12, 0, len(regime) - 1)]
        out["regimes"] = {name: {"trades": int((reg == k).sum()), "expectancy_r": round(float(r[reg == k].mean()), 4)}
                          for k, name in ((1, "BTC up"), (-1, "BTC down"), (0, "BTC range")) if (reg == k).sum()}
    return out


def btc_regime_hourly(w: Window) -> np.ndarray:
    """Per hour: +1 BTC above a rising 7-day EMA, -1 below a falling one, 0 otherwise (from closed hourly bars)."""
    if "BTCUSDT" not in w.coins:
        return np.zeros(w.n_bars // 12, dtype=np.int8)
    h = resample(w.coins["BTCUSDT"].bars, 12)[:, 3].astype(np.float64)
    ema = np.full(len(h), np.nan)
    k, e = 2 / (168 + 1), np.nan
    for i, x in enumerate(h):
        if np.isnan(x):
            ema[i] = e
            continue
        e = x if np.isnan(e) else e + k * (x - e)
        ema[i] = e
    slope = ema - np.concatenate([np.full(24, np.nan), ema[:-24]])
    reg = np.zeros(len(h), dtype=np.int8)
    reg[(h > ema) & (slope > 0)] = 1
    reg[(h < ema) & (slope < 0)] = -1
    # the regime used at hour i must come from bars closed BEFORE i: shift by one hour
    return np.concatenate([[0], reg[:-1]]).astype(np.int8)
