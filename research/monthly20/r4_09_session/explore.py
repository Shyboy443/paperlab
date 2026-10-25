"""DEV-only exploration: conditional intraday session/clock events. Prints per-event net stats (bp) by |z| bucket.
z = signal return / (hourly vol * sqrt(signal hours)). 'mom' trades in the signal's direction, 'rev' against it."""
import numpy as np
from base import *

d0, d1 = dayidx(DEV_START), dayidx(DEV_END)
dmid = dayidx(1672531200000)  # 2023-01-01
OUT = []


def events(sig_a, sig_b, ent, ext, days, uni):
    """signal = open(bar sig_b)/open(bar sig_a)-1 (bar offsets from day start), trade ent->ext (offsets, may be >24)."""
    D, Jx = [], []
    for d in days:
        if uni == "BE":
            js = [BTC, ETH]
        else:
            js = [j for j in np.where(U40[d])[0] if j not in (BTC, ETH)]
        D += [d] * len(js); Jx += list(js)
    D, Jx = np.array(D), np.array(Jx)
    b = D * 24
    sa, sb, ie, ix = b + sig_a, b + sig_b, b + ent, b + ext
    ok = (ix < len(th))
    D, Jx, sa, sb, ie, ix = D[ok], Jx[ok], sa[ok], sb[ok], ie[ok], ix[ok]
    sig = np.log(O[sb, Jx] / O[sa, Jx])
    z = sig / (hvol(sa)[np.arange(len(sa)), Jx] if False else hvol_at(sa, Jx)) / np.sqrt(sig_b - sig_a)
    g = O[ix, Jx] / O[ie, Jx] - 1
    f = FCUM[ix, Jx] - FCUM[ie, Jx]
    ok = np.isfinite(z) & np.isfinite(g)
    return D[ok], Jx[ok], z[ok], g[ok], f[ok]


def hvol_at(t, j):
    v = hvol(np.unique(t))
    idx = np.searchsorted(np.unique(t), t)
    return v[idx, j]


def report(name, D, z, g, f, ks=(0, 1, 1.5, 2, 3)):
    cost = 2 * (FEE + SLIP)
    for k in ks:
        m = np.abs(z) >= k
        if m.sum() < 30:
            continue
        s = np.sign(z[m])
        for mode, sg in (("mom", s), ("rev", -s)):
            r = sg * g[m] - sg * f[m] - cost
            a = D[m] < dmid
            ud, inv = np.unique(D[m], return_inverse=True)
            dm = np.bincount(inv, r) / np.bincount(inv)
            t = dm.mean() / dm.std() * np.sqrt(len(dm))  # clustered by day (one portfolio return per day)
            line = f"{name:34s} k>={k:<4} {mode} n={m.sum():6d} days={len(ud):5d} net={r.mean()*1e4:7.1f}bp t={t:5.2f} 21-22={r[a].mean()*1e4:7.1f} 23-24={r[~a].mean()*1e4:7.1f}"
            OUT.append((t, line))
            print(line, flush=True)


days = np.arange(d0, d1)
specs = []
# 1) intraday momentum: first H hours -> rest of the day / last hours
for H_ in (1, 2, 4, 8, 12):
    specs.append((f"IM 0-{H_} -> {H_}-24", 0, H_, H_, 24))
for H_ in (12, 18, 22):
    specs.append((f"IM 0-{H_} -> {H_}-24", 0, H_, H_, 24)) if H_ != 12 else None
specs.append(("IM 0-1 -> 23-24", 0, 1, 23, 24))
specs.append(("IM 0-23 -> 23-24", 0, 23, 23, 24))
specs.append(("IM 0-20 -> 20-24", 0, 20, 20, 24))
# 2) US session
specs += [("US pre 0-13 -> 13-21", 0, 13, 13, 21), ("US pre 0-13 -> 14-21", 0, 13, 14, 21),
          ("US open 13-15 -> 15-21", 13, 15, 15, 21), ("US open 13-15 -> 15-24", 13, 15, 15, 24),
          ("US open 14-15 -> 15-20", 14, 15, 15, 20), ("US 13-21 -> 21-24", 13, 21, 21, 24),
          ("US 13-21 -> next Asia 24-32", 13, 21, 24, 32), ("Asia 0-8 -> Europe 8-13", 0, 8, 8, 13),
          ("Europe 7-9 -> 9-13", 7, 9, 9, 13), ("Asia 0-2 -> 2-8", 0, 2, 2, 8),
          ("day 0-24 -> next 24-48", 0, 24, 24, 48), ("late 21-24 -> next 24-32", 21, 24, 24, 32)]
# 3) funding stamps: move into the stamp predicts move after
for s in (8, 16, 24):
    specs += [(f"FUND pre {s-4}-{s} -> post {s}-{s+2}", s - 4, s, s, s + 2),
              (f"FUND pre {s-4}-{s} -> post {s}-{s+4}", s - 4, s, s, s + 4)]
for uni in ("BE", "ALT"):
    print("=====", uni)
    for name, a, b, e, x in specs:
        D, Jx, z, g, f = events(a, b, e, x, days, uni)
        report(f"{uni} {name}", D, z, g, f)

# 4) weekend: Fri 0-24 signal -> weekend Sat0-Mon0 ; weekend Sat0-Mon0 -> Monday 0-24
for uni in ("BE", "ALT"):
    fri = days[DOW[days] == 4]; sat = days[DOW[days] == 5]
    D, Jx, z, g, f = events(0, 24, 24, 72, fri, uni); report(f"{uni} WKND Fri -> Sat+Sun", D, z, g, f)
    D, Jx, z, g, f = events(0, 48, 48, 72, sat, uni); report(f"{uni} WKND Sat+Sun -> Mon", D, z, g, f)
    D, Jx, z, g, f = events(0, 48, 48, 56, sat, uni); report(f"{uni} WKND Sat+Sun -> Mon 0-8", D, z, g, f)
    D, Jx, z, g, f = events(0, 48, 61, 69, sat, uni); report(f"{uni} WKND Sat+Sun -> Mon US 13-21", D, z, g, f)

print("\n===== TOP by t (DEV) =====")
for t, line in sorted(OUT, reverse=True)[:40]:
    print(line)
