"""Hourly panel for the lead-lag study, built only from local survivorship-free caches (no downloads).

Coins: every USDT perp that was ever in the top-50 by trailing 30-day quote volume at a month start 2021-01..2026-10,
using the r2_rotation daily panel (agent 07's data.binance.vision 1h cache, delisted coins included, TradFi/index
perps dropped). Membership in this candidate list is only a storage shortcut: the strategy picks its universe
point-in-time every hour from trailing volume (see univ_rank), so a coin is only tradable when it was liquid then.

Output data/monthly20_r4_leadlag/panel_1h.npz:
  t (hours), syms, o h l c (float32, NaN when no bar), fund (float32 funding rate stamped at that hour, 0 otherwise),
  rank (int16: point-in-time volume rank by the 30 completed days before the bar's day; 999 = not eligible)
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
from common import DATA_FROM, TEST_END, DAY, HOUR, PROJECT
from data import build as build_daily

V07 = PROJECT / "data" / "monthly20_07_pump_scanner"
OUT = PROJECT / "data" / "monthly20_r4_leadlag"
T0 = 1606780800000  # 2020-12-01, first archived month


def build(force=False):
    path = OUT / "panel_1h.npz"
    if path.exists() and not force:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    D = build_daily()
    td, qv, dsyms, first = D["t"], np.nan_to_num(D["qv"]), D["syms"], D["first"]
    ms = [i for i in range(len(td)) if datetime.fromtimestamp(td[i] / 1000, timezone.utc).day == 1 and td[i] >= 1609459200000]
    cand = set()
    for i in ms:
        cand |= set(np.argsort(-qv[i - 30:i].sum(0))[:50])
    cand = sorted(cand)
    syms = dsyms[cand]
    # point-in-time daily rank: by the 30 completed days before day d, coin must have >= 30 days of history
    Td = len(td)
    roll = np.full((Td, len(dsyms)), 0.0)
    cs = np.cumsum(np.vstack([np.zeros(len(dsyms)), qv]), 0)
    for d in range(30, Td):
        roll[d] = cs[d] - cs[d - 30]
    age_ok = (first[None, :] > 0) & (td[:, None] - first[None, :] >= 30 * DAY)
    roll = np.where(age_ok, roll, -1)
    order = np.argsort(-roll, 1)
    rk = np.empty_like(order)
    rows = np.arange(Td)[:, None]
    rk[rows, order] = np.arange(len(dsyms))[None, :]
    rk = np.where(roll > 0, rk, 999)[:, cand]
    t = np.arange(T0, TEST_END, HOUR, dtype=np.int64)
    T, N = len(t), len(syms)
    o, h, l, c = (np.full((T, N), np.nan, np.float32) for _ in range(4))
    fund = np.zeros((T, N), np.float32)
    for j, s in enumerate(syms):
        a = np.array([r[:5] for r in json.loads((V07 / "1h" / f"{s}.json").read_text())], dtype=float)
        a = a[(a[:, 0] >= T0) & (a[:, 0] < TEST_END)]
        ix = ((a[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
        o[ix, j], h[ix, j], l[ix, j], c[ix, j] = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        fp = V07 / "funding" / f"{s}.json"
        f = json.loads(fp.read_text()) if fp.exists() else []
        if f:
            fa = np.array(f, dtype=float)
            fi = ((fa[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
            ok = (fi >= 0) & (fi < T)
            np.add.at(fund[:, j], fi[ok], fa[ok, 1].astype(np.float32))
        else:  # no funding history: longs pay 0.01% per 8h (agent 02's rule)
            fund[(np.arange(T) % 8) == 0, j] = 0.0001
        if j % 50 == 0:
            print("panel", j, N, flush=True)
    dayidx = ((t - DATA_FROM) // DAY).astype(int)
    rank = rk[dayidx].astype(np.int16)
    out = dict(t=t, syms=syms, o=o, h=h, l=l, c=c, fund=fund, rank=rank)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(path, **out)
    return out


if __name__ == "__main__":
    P = build(force="force" in sys.argv)
    print(P["c"].shape, (P["rank"] < 50).sum(1)[-1])
