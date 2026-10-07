"""V11 reversal event study: after a coin makes an EXTREME move of its own (not the market's), how much of it comes
back, and is that bigger than the costs? (docs/V11_PROTOCOL.md)

    python scripts/v11_reversal_study.py

Why this test: the feature study (docs/V11_FEATURE_STUDY.json) found one robust effect in the 30-coin universe -- a
short-term cross-sectional REVERSAL (last 1-4 h return vs the next hour: rank IC about -0.04, |t| 5-6 in BOTH halves)
-- but a plain long-short book on it earns ~3 bp per position, a quarter of the ~13 bp taker round trip. Reversals
are usually concentrated in the largest dislocations, so this conditions on extreme IDIOSYNCRATIC moves.

Every 15 minutes, for every coin: its return over the last L (1 h or 4 h) minus beta x BTC's (beta: 7 days of 15m
returns), divided by the coin's own residual volatility over those 7 days, = z. An EVENT is |z| >= k; the trade is
AGAINST the move (long after a dump, short after a pump), entered at the open of the minute starting T + 60 s, and
measured to T + H (15 / 30 / 60 / 120 / 240 min). One event per coin per hour at most. Gross = the raw move in the
trade's favour; net = gross - 2 x (taker 0.055% + the coin's half spread).

DECISION RULE (fixed before running): pick (L, k, H) on DISCOVERY only -- the best net bps with >= 150 events; it is
CONFIRMED if the same (L, k, H) nets > 0 bps per trade with t >= 2 in CONFIRMATION.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from v11_scan_study import MIN, TAKER, WARM_DAYS, bars, load  # noqa: E402

LOOKBACKS = {"1h": 4, "4h": 16}          # in 15m bars
KS = (2.0, 2.5, 3.0, 4.0)
HORIZONS = (15, 30, 60, 120, 240)       # minutes
WIN = 7 * 96                            # 7 days of 15m bars


def main() -> None:
    t0 = time.time()
    d = load()
    b = bars(d, 15)
    C = b["C"]
    ns, n = C.shape
    bi = d["syms"].index("BTCUSDT")
    lr = np.log(C / np.concatenate([C[:, :1], C[:, :-1]], axis=1))
    lrb = pd.Series(lr[bi])
    var_b = lrb.rolling(WIN).var().shift(1)
    beta = np.stack([(pd.Series(lr[k]).rolling(WIN).cov(lrb).shift(1) / var_b).to_numpy() for k in range(ns)])
    resid15 = lr - beta * lr[bi][None, :]
    sd15 = pd.DataFrame(resid15.T).rolling(WIN).std().shift(1).to_numpy().T
    warm = WARM_DAYS * 96
    mid_min = (warm * 15 + d["C"].shape[1]) // 2
    costs = 2 * (TAKER + d["hs"])
    rows = []
    for lname, L in LOOKBACKS.items():
        rL = pd.DataFrame(resid15.T).rolling(L).sum().to_numpy().T       # residual log return over the last L
        z = rL / (sd15 * np.sqrt(L))
        for k in KS:
            ev = np.abs(z) >= k
            ev[:, :max(warm, WIN + L)] = False
            ev[bi] = False
            sy, bj = np.nonzero(ev)
            order = np.lexsort((bj, sy))
            sy, bj = sy[order], bj[order]
            keep = np.ones(len(sy), dtype=bool)
            last: dict[int, int] = {}
            for i, (s, j) in enumerate(zip(sy, bj)):
                if j - last.get(s, -10 ** 9) < 4:                # one event per coin per hour
                    keep[i] = False
                else:
                    last[s] = j
            sy, bj = sy[keep], bj[keep]
            side = -np.sign(z[sy, bj])                          # against the move
            e = (bj + 1) * 15 + 1
            for H in HORIZONS:
                x = (bj + 1) * 15 + H - 1
                ok = x < d["C"].shape[1]
                g = side[ok] * (d["C"][sy[ok], x[ok]] / d["O"][sy[ok], e[ok]] - 1)
                net = g - costs[sy[ok]]
                half = e[ok] >= mid_min
                for name, m in (("discovery", ~half), ("confirmation", half)):
                    v = net[m]
                    rows.append({"L": lname, "k": k, "H": H, "half": name, "events": int(m.sum()),
                                 "per_day": round(m.sum() / ((d["C"].shape[1] - warm * 15) / 1440 / 2), 2),
                                 "gross_bps": round(float(g[m].mean() * 1e4), 1) if m.any() else None,
                                 "net_bps": round(float(v.mean() * 1e4), 1) if m.any() else None,
                                 "t": round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2) if len(v) > 5 else None,
                                 "win": round(float((v > 0).mean()), 3) if m.any() else None})
    df = pd.DataFrame(rows)
    disc = df[(df.half == "discovery") & (df.events >= 150)].sort_values("net_bps", ascending=False)
    pick = disc.iloc[0].to_dict() if len(disc) else None
    conf = None
    if pick:
        c = df[(df.half == "confirmation") & (df.L == pick["L"]) & (df.k == pick["k"]) & (df.H == pick["H"])]
        conf = c.iloc[0].to_dict() if len(c) else None
    confirmed = bool(conf and conf["net_bps"] is not None and conf["net_bps"] > 0 and (conf["t"] or 0) >= 2)
    wide = df.pivot_table(index=["L", "k", "H"], columns="half", values=["events", "gross_bps", "net_bps", "t"])
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(wide.to_string())
    print("pick (discovery):", pick)
    print("confirmation:", conf, "CONFIRMED" if confirmed else "NOT CONFIRMED", f"({time.time() - t0:.0f} s)")
    (PROJECT / "docs" / "V11_REVERSAL_STUDY.json").write_text(json.dumps(
        {"rows": rows, "pick": pick, "confirmation": conf, "confirmed": confirmed}, indent=1, default=float))


if __name__ == "__main__":
    main()
