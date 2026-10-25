"""Daily panel for the round-2 rotation check, built ONLY from local data.binance.vision caches (no API, no downloads).

Source: agent 07's 1h kline + funding cache (data/monthly20_07_pump_scanner/{1h,funding}/SYM.json), which covers every
USDT perp that ever had a monthly archive file, INCLUDING delisted coins (LUNA, SRM, FTT, BTCST ...), 2020-12..2026-10-21.
Agent 04's npz cache (same archive, 895 symbols, subset of 07's 901) is used as a cross-check only.
Non-crypto contracts are dropped using agent 05's exchangeInfo snapshot: TradFi perps (stocks, gold, FX ...) and
index contracts (DEFI, BTCDOM, ALL, FOOTBALL, BLUEBIRD). The original agent 02 universe (today's PERPETUAL list) also
excluded TradFi perps, so this keeps the same kind of universe and only adds the delisted coins.

Daily bar d = 00:00..24:00 UTC aggregated from 1h bars: open = first hour's open, high/low = max/min, close = last close,
quote volume = sum. "Listing time" for the >=30-day rule = first archived bar (exchangeInfo onboard is missing for delisted coins).
Output cache: data/monthly20_r2_rotation/panel_1d.npz
"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DATA_FROM, TEST_END, DAY, HOUR, PROJECT

V07 = PROJECT / "data" / "monthly20_07_pump_scanner"
EI = PROJECT / "data" / "monthly20_05_new_listings" / "exchangeinfo_all.json"
OUT = PROJECT / "data" / "monthly20_r2_rotation"
INDEXES = {"DEFIUSDT", "BTCDOMUSDT", "ALLUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT"}


def eligible_symbols():
    ei = {x["symbol"]: x for x in json.loads(EI.read_text(encoding="utf-8"))}
    syms, dropped = [], []
    for p in sorted((V07 / "1h").glob("*.json")):
        s = p.stem
        if s in INDEXES or (s in ei and ei[s].get("underlyingType") != "COIN"):
            dropped.append(s); continue
        syms.append(s)
    return syms, dropped, ei


def build(force=False):
    path = OUT / "panel_1d.npz"
    if path.exists() and not force:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    syms, dropped, ei = eligible_symbols()
    t = np.arange(DATA_FROM, TEST_END, DAY, dtype=np.int64)
    T, N = len(t), len(syms)
    o, h, l, c, qv, nh = (np.full((T, N), np.nan) for _ in range(6))
    fund = np.zeros((T, N)); has_f = np.zeros(N, bool); first = np.zeros(N, np.int64); last = np.zeros(N, np.int64)
    for j, s in enumerate(syms):
        rows = json.loads((V07 / "1h" / f"{s}.json").read_text())
        if not rows:
            continue
        a = np.array([r[:7] for r in rows], dtype=float)
        a = a[(a[:, 0] >= DATA_FROM) & (a[:, 0] < TEST_END)]
        if not len(a):
            continue
        first[j], last[j] = int(a[0, 0]), int(a[-1, 0])
        d = ((a[:, 0].astype(np.int64) - DATA_FROM) // DAY).astype(int)
        # rows are time-sorted; first/last per day via unique
        u, i_first = np.unique(d, return_index=True)
        i_last = np.r_[i_first[1:] - 1, len(d) - 1]
        o[u, j] = a[i_first, 1]; c[u, j] = a[i_last, 4]
        h[u, j] = np.maximum.reduceat(a[:, 2], i_first); l[u, j] = np.minimum.reduceat(a[:, 3], i_first)
        qv[u, j] = np.add.reduceat(a[:, 6], i_first); nh[u, j] = np.diff(np.r_[i_first, len(d)])
        fp = V07 / "funding" / f"{s}.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            if f:
                fa = np.array(f, dtype=float)
                fi = ((fa[:, 0].astype(np.int64) - DATA_FROM) // DAY).astype(int)
                ok = (fi >= 0) & (fi < T)
                np.add.at(fund[:, j], fi[ok], fa[ok, 1]); has_f[j] = True
        if j % 100 == 0:
            print("panel", j, "/", N, s.encode("ascii", "replace").decode(), flush=True)
    fund[:, ~has_f] = 0.0001 * 3  # coins without funding history: +0.01%/8h, longs pay (agent 02's rule)
    today = {x["symbol"] for x in json.loads(EI.read_text(encoding="utf-8"))
             if x.get("status") == "TRADING" and x.get("contractType") == "PERPETUAL"}
    out = dict(t=t, syms=np.array(syms), o=o, h=h, l=l, c=c, qv=qv, nh=nh, fund=fund, has_f=has_f, first=first, last=last,
               trading_today=np.array([s in today for s in syms]), dropped=np.array(dropped))
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return out


if __name__ == "__main__":
    P = build(force="force" in sys.argv)
    print(len(P["syms"]), "coins;", int(P["trading_today"].sum()), "trading today;", len(P["dropped"]), "non-crypto dropped")
