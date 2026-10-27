"""Noise band (Zarattini/Aziz/Barbon 2024) on 6 Binance perps, 15m bars. Rule and judgement in PREREG.md."""
import sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa: E402,F403
import numpy as np  # noqa: E402

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "AVAXUSDT"]
Q = 15 * MIN


def sleeve(sym, start_h=13.5, end_h=20.0, cost=FEE + SLIP):
    """Daily P&L (fraction of the sleeve) per session day: {day_ms: ret}."""
    b = np.array(klines(sym, "15m"), dtype=float)
    t = b[:, 0].astype(np.int64)
    idx = {int(x): i for i, x in enumerate(t)}
    s0, s1 = int(start_h * HOUR), int(end_h * HOUR)
    marks = list(range(s0 + 30 * MIN, s1 + 1, 30 * MIN))      # decision times (bar closes)
    days = sorted({int(x) // DAY * DAY for x in t})
    moves = {}                                                  # day -> [|move| at each mark]
    out, daily_close = {}, {}
    for d in days:
        i0 = idx.get(d + s0)
        if i0 is None:
            continue
        o = b[i0, 1]
        ok, mv, cl = True, [], []
        for m in marks:
            j = idx.get(d + m - Q)
            if j is None:
                ok = False
                break
            mv.append(abs(b[j, 4] / o - 1))
            cl.append(j)
        jc = idx.get(d + DAY - Q)
        if jc is not None:
            daily_close[d] = b[jc, 4]
        if not ok:
            continue
        prev = [moves[x] for x in sorted(moves)[-14:] if x < d]
        moves[d] = mv
        if len(prev) < 14 or (d - DAY) not in daily_close:
            continue
        sig = np.mean(prev, 0)
        P = b[idx[d + s0] - 1, 4] if idx.get(d + s0) else o      # last price before the session (= ref close)
        rets = [daily_close[x] / daily_close[x - DAY] - 1 for x in range(d - 14 * DAY, d, DAY)
                if x in daily_close and x - DAY in daily_close]
        vol = np.std(rets) if len(rets) >= 10 else 0.04
        lev = min(1.0, 0.02 / max(vol, 1e-6))
        ref_hi, ref_lo = max(o, P), min(o, P)
        pos, pnl, last = 0, 0.0, o
        pv = vv = 0.0
        jprev = i0
        for k, j in enumerate(cl):
            seg = b[jprev:j + 1]
            tp = (seg[:, 2] + seg[:, 3] + seg[:, 4]) / 3
            pv += (tp * seg[:, 5]).sum()
            vv += seg[:, 5].sum()
            jprev = j + 1
            c = b[j, 4]
            pnl += pos * lev * (c / last - 1)
            last = c
            vwap = pv / vv if vv else c
            ub, lb = ref_hi * (1 + sig[k]), ref_lo * (1 - sig[k])
            if k == len(cl) - 1:
                new = 0
            elif pos == 1 and c < max(ub, vwap):
                new = 0
            elif pos == -1 and c > min(lb, vwap):
                new = 0
            else:
                new = pos
            if new == 0 and k < len(cl) - 1:
                new = 1 if c > ub else -1 if c < lb else 0
            pnl -= abs(new - pos) * lev * cost
            pos = new
        out[d] = pnl
    return out


def book(**kw):
    sl = [sleeve(s, **kw) for s in COINS]
    days = sorted(set().union(*sl))
    eq, curve = 1.0, []
    for d in days:
        r = np.mean([x.get(d, 0.0) for x in sl])
        eq *= 1 + r
        curve.append((d, eq))
    return curve


def stats(curve, a, z):
    c = [(t, e) for t, e in curve if a <= t < z]
    m = list(monthly_returns(c).values())
    eq = np.array([e for _, e in c])
    dd = 1 - (eq / np.maximum.accumulate(eq)).min()
    geo = (eq[-1] / eq[0]) ** (1 / len(m)) - 1
    return dict(months=len(m), mean=np.mean(m), median=np.median(m), up=sum(x > 0 for x in m),
                geo=100 * geo, dd=100 * dd, worst=min(m)), monthly_returns(c)


if __name__ == "__main__":
    mid = utc(2023, 1, 1)
    for name, kw in [("primary 13:30", {}), ("2x costs", {"cost": 2 * (FEE + SLIP)}), ("start 14:30", {"start_h": 14.5}),
                     ("no costs", {"cost": 0.0})]:
        cv = book(**kw)
        print(f"== {name}")
        for lbl, a, z in [("DEV1", DEV_START, mid), ("DEV2", mid, DEV_END), ("TEST", TEST_START, TEST_END)]:
            s, _ = stats(cv, a, z)
            print(f"  {lbl}: " + "  ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in s.items()))
        if name == "primary 13:30":
            _, mt = stats(cv, TEST_START, TEST_END)
            print("  TEST months:", " ".join(f"{k[2:]}:{v:+.1f}" for k, v in mt.items()))
