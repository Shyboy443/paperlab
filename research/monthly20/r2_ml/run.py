"""r2_ml: robustness checks (ROBUSTNESS.md) for agent 10's ML walk-forward long/short, rebuilt WITH delisted coins.

    python run.py build    # archive 1h -> 4h bars per symbol, point-in-time universes (cache in data/monthly20_r2_ml)
    python run.py wf       # all walk-forward predictions (base + neighbours), cached
    python run.py checks   # the 8 checks, leverage table, results.json

Model, features, target, costs and trading rules are copied unchanged from 10_ml_walkforward/run.py.
Changes: the universe (archive incl. delisted coins), exit at the last traded price when a coin's data ends,
per-position P&L tracking, and a seed / random-pick option. Research only: no orders, no keys.
"""
from __future__ import annotations
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa: F401,F403
from common import CACHE, PROJECT, HERE as M20

OUT = M20 / "r2_ml"
DD = PROJECT / "data" / "monthly20_r2_ml"
D04 = PROJECT / "data" / "monthly20_04_crash_reversal"
D05 = PROJECT / "data" / "monthly20_05_new_listings"
D07 = PROJECT / "data" / "monthly20_07_pump_scanner"
DD.mkdir(parents=True, exist_ok=True)
BAR = 4 * HOUR
N_UNI = 40
EMBARGO = DAY
UNI_FROM = utc(2020, 2, 1)
TRAIN_STRIDE = 3
FULL_START, FULL_END = DEV_START, TEST_END
TIMES = np.arange(DATA_FROM, TEST_END, BAR, dtype=np.int64)
T0_07, T0_04 = utc(2020, 12, 1), utc(2020, 9, 1)


def month_list(a, b):
    out, d = [], datetime.fromtimestamp(a / 1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(d.timestamp() * 1000) < b:
        out.append(int(d.timestamp() * 1000))
        d = d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)
    return out


def mkey(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------- data (archive, incl. delisted)
def eligible_symbols():
    info = {x["symbol"]: x for x in json.loads((D05 / "exchangeinfo_all.json").read_text(encoding="utf-8"))}
    out = []
    for f in sorted((D07 / "1h").glob("*.json")):
        s = f.stem
        x = info.get(s)
        if x is not None and (x["contractType"] != "PERPETUAL" or x["underlyingType"] != "COIN"):
            continue
        if s in ("BLUEBIRDUSDT", "FOOTBALLUSDT", "DEFIUSDT", "BTCDOMUSDT"):
            continue
        out.append(s)
    return out


def load_1h(sym):
    parts = []
    p = DD / "early1h" / f"{sym}.json"
    if p.exists():
        a = np.array(json.loads(p.read_text()) or np.zeros((0, 9)), dtype=float).reshape(-1, 9)
        parts.append(a[a[:, 0] < T0_04][:, [0, 1, 2, 3, 4, 6, 8]])
    p = D04 / "1h" / f"{sym}.npz"
    if p.exists():
        a = np.load(p)["a"]
        if len(a):
            a = a[(a[:, 0] >= T0_04) & (a[:, 0] < T0_07)]
            parts.append(a[:, [0, 1, 2, 3, 4, 5, 6]])
    a = np.array(json.loads((D07 / "1h" / f"{sym}.json").read_text()) or np.zeros((0, 9)), dtype=float).reshape(-1, 9)
    parts.append(a[a[:, 0] >= T0_07][:, [0, 1, 2, 3, 4, 6, 8]])
    a = np.concatenate([x for x in parts if len(x)]) if any(len(x) for x in parts) else np.zeros((0, 7))
    a = a[(a[:, 5] > 0) & (a[:, 0] < TEST_END)]  # cols t o h l c qv tbq; zero-volume hours = missing
    return a


def to_4h(a):
    T = len(TIMES)
    out = np.full((6, T), np.nan)
    if not len(a):
        return out
    b = ((a[:, 0] - DATA_FROM) // BAR).astype(np.int64)
    ok = (b >= 0) & (b < T)
    a, b = a[ok], b[ok]
    df = pd.DataFrame({"b": b, "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "qv": a[:, 5], "tb": a[:, 6]})
    g = df.groupby("b").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), qv=("qv", "sum"),
                            tb=("tb", "sum"))
    for q, k in enumerate(("o", "h", "l", "c", "qv", "tb")):
        out[q, g.index.values] = g[k].values
    return out


def load_funding(sym):
    rows = []
    p = D04 / "funding" / f"{sym}.npz"
    if p.exists():
        a = np.load(p)["a"]
        rows += [(int(t), float(r)) for t, r in a if t < T0_07]
    p = D07 / "funding" / f"{sym}.json"
    if p.exists():
        rows += [(int(t), float(r)) for t, r in json.loads(p.read_text()) if t >= T0_07]
    last = max((t for t, _ in rows), default=0)
    p = CACHE / "funding" / f"{sym}.json"
    if p.exists():
        rows += [(int(t), float(r)) for t, r in json.loads(p.read_text()) if t > last + HOUR and t < TEST_END]
    F = np.zeros(len(TIMES))
    for t, r in rows:
        i = (t - DATA_FROM) // BAR
        if 0 <= i < len(F):
            F[i] += r
    return F


def stage_build():
    syms = eligible_symbols()
    (DD / "bars4h").mkdir(exist_ok=True)
    qv = {}
    for n, s in enumerate(syms):
        p = DD / "bars4h" / f"{s}.npz"
        if not p.exists():
            np.savez_compressed(p, b=to_4h(load_1h(s)), f=load_funding(s))
        qv[s] = np.load(p)["b"][4]
        if n % 100 == 0:
            print("build", n, len(syms), flush=True)
    uni = {}
    for at in month_list(UNI_FROM, TEST_END):
        i1 = (at - DATA_FROM) // BAR
        i0 = i1 - 180
        ranked = []
        for s, v in qv.items():
            w = v[max(i0, 0):i1]
            if np.isfinite(w).sum() >= 0.8 * 180:
                ranked.append((np.nansum(w), s))
        uni[mkey(at)] = [s for _, s in sorted(ranked, reverse=True)[:N_UNI]]
    (DD / "universes.json").write_text(json.dumps(uni))
    orig = json.loads((PROJECT / "data" / "monthly20_10_ml_walkforward" / "union.json").read_text())
    alive = {s["symbol"] for s in perp_symbols()}
    union = sorted(set(s for v in uni.values() for s in v))
    dead = [s for s in union if s not in alive]
    print("union", len(union), "delisted in union", len(dead), dead)
    months_with_dead = {k: [s for s in v if s not in alive] for k, v in uni.items()}
    (DD / "universe_delisted.json").write_text(json.dumps(months_with_dead))
    print("orig union", len(orig))


# ---------------------------------------------------------------- panel / features (copied from agent 10)
def panel(syms):
    T, N = len(TIMES), len(syms)
    A = {k: np.full((T, N), np.nan) for k in ("o", "h", "l", "c", "qv", "tbq")}
    F = np.zeros((T, N))
    for j, s in enumerate(syms):
        z = np.load(DD / "bars4h" / f"{s}.npz")
        for q, k in enumerate(("o", "h", "l", "c", "qv", "tbq")):
            A[k][:, j] = z["b"][q]
        F[:, j] = z["f"]
    A["cff"] = pd.DataFrame(A["c"]).ffill().values  # last traded close (exit when a coin's data ends)
    return TIMES, A, F


def features(times, A, F, syms, uni):
    c = pd.DataFrame(A["c"]); h = pd.DataFrame(A["h"]); l = pd.DataFrame(A["l"])
    qv = pd.DataFrame(A["qv"]); tbq = pd.DataFrame(A["tbq"]); f = pd.DataFrame(F)
    lc = np.log(c); r1 = lc.diff()
    per = {}
    for n in (1, 3, 6, 18, 42, 180):
        per[f"r{n}"] = lc - lc.shift(n)
    for n in (6, 42, 180):
        per[f"vol{n}"] = r1.rolling(n).std()
    per["vr"] = per["vol6"] / per["vol42"]
    per["qvz"] = np.log(qv.rolling(6).mean() / qv.rolling(180).mean())
    per["size"] = np.log(qv.rolling(180).mean())
    per["tb6"] = tbq.rolling(6).sum() / qv.rolling(6).sum() - 0.5
    per["tb42"] = tbq.rolling(42).sum() / qv.rolling(42).sum() - 0.5
    rng = h.rolling(42).max() - l.rolling(42).min()
    per["rpos"] = (c - l.rolling(42).min()) / rng
    per["hl6"] = np.log(h / l).rolling(6).mean()
    per["fund6"] = f.rolling(6).sum()
    per["fund18"] = f.rolling(18).sum()
    bj = syms.index("BTCUSDT")
    per["res6"] = per["r6"].sub(per["r6"][bj], axis=0)
    per["res42"] = per["r42"].sub(per["r42"][bj], axis=0)
    T, N = c.shape
    U = np.zeros((T, N), bool)
    col = {s: j for j, s in enumerate(syms)}
    nxt = np.append(times[1:], times[-1] + BAR)
    keys = np.array([mkey(t) for t in nxt])
    for k in np.unique(keys):
        rows = np.where(keys == k)[0]
        for s in uni.get(k, []):
            if s in col:
                U[rows, col[s]] = True
    valid = U & np.isfinite(A["c"]) & np.isfinite(np.vstack([A["o"][1:], np.full((1, N), np.nan)]))
    for v in per.values():
        valid &= np.isfinite(v.values)
    ranks = {}
    for k, v in per.items():
        m = v.where(valid)
        ranks[k] = (m.rank(axis=1, pct=True) - 0.5).values
    reg = {"btc_r6": per["r6"][bj].values, "btc_r42": per["r42"][bj].values, "btc_r180": per["r180"][bj].values,
           "btc_vol42": per["vol42"][bj].values,
           "breadth42": per["r42"].where(valid).mean(axis=1).values,
           "disp6": per["r6"].where(valid).std(axis=1).values}
    names = list(ranks) + list(reg)
    return valid, ranks, reg, names


def targets(A, valid, H):
    o = A["o"]; T, N = o.shape
    fwd = np.full((T, N), np.nan)
    fwd[: T - 1 - H] = np.log(o[1 + H:T] / o[1:T - H])
    m = pd.DataFrame(np.where(valid, fwd, np.nan))
    return (m.rank(axis=1, pct=True) - 0.5).values, fwd


def make_model(seed=0):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=2000,
                                         l2_regularization=1.0, early_stopping=False, random_state=seed)


def design(ranks, reg, names, rows_t, rows_j):
    X = np.empty((len(rows_t), len(names)), dtype=np.float32)
    for q, k in enumerate(names):
        X[:, q] = ranks[k][rows_t, rows_j] if k in ranks else reg[k][rows_t]
    return X


def walk_forward(cfg, times, valid, ranks, reg, names, A, months, tag):
    path = DD / f"pred_{tag}_H{cfg['H']}_{cfg['win']}_s{cfg.get('seed', 0)}.npz"
    if path.exists():
        z = np.load(path)
        return dict(zip(z["t"].tolist(), z["s"]))
    H = cfg["H"]
    y, _ = targets(A, valid, H)
    T, N = valid.shape
    tend = np.full(T, np.iinfo(np.int64).max, dtype=np.int64)
    tend[: T - 1 - H] = times[1 + H:T]
    nxt = np.append(times[1:], times[-1] + BAR)
    lab_ok = valid & np.isfinite(y)
    reg_cols = [q for q, k in enumerate(names) if k not in ranks]
    preds = {}
    t0 = time.time()
    for m0 in months:
        m1 = month_list(m0, m0 + 32 * DAY)[1]
        lo = 0 if cfg["win"] == "all" else m0 - cfg["win"] * 30 * DAY
        tr_t = np.where((tend <= m0 - EMBARGO) & (times >= lo))[0]
        tr_t = tr_t[tr_t % TRAIN_STRIDE == 0]
        rt, rj = np.nonzero(lab_ok[tr_t])
        rt = tr_t[rt]
        if len(rt) < 5000:
            print(tag, cfg, mkey(m0), "too few rows", len(rt), flush=True)
            continue
        X = design(ranks, reg, names, rt, rj)
        mu, sd = X[:, reg_cols].mean(0), X[:, reg_cols].std(0) + 1e-9
        X[:, reg_cols] = (X[:, reg_cols] - mu) / sd
        mdl = make_model(cfg.get("seed", 0)).fit(X, y[rt, rj])
        dec = np.where((nxt >= m0) & (nxt < m1) & (nxt % (H * BAR) == 0))[0]
        for t in dec:
            js = np.where(valid[t])[0]
            if len(js) < 10:
                continue
            Xp = design(ranks, reg, names, np.full(len(js), t), js)
            Xp[:, reg_cols] = (Xp[:, reg_cols] - mu) / sd
            s = np.full(N, np.nan)
            s[js] = mdl.predict(Xp)
            preds[int(t)] = s
    print(tag, cfg, "done", f"{time.time()-t0:.0f}s", len(preds), "decisions", flush=True)
    ts = sorted(preds)
    np.savez(path, t=np.array(ts), s=np.array([preds[t] for t in ts]) if ts else np.zeros((0, N)))
    return preds


# ---------------------------------------------------------------- simulation (agent 10's, plus position records)
def simulate(preds, cfg, times, A, F, L, start, end, cost_mult=1.0, E0=10_000.0, rng=None):
    """rng given -> random picks: at each decision, k random longs and k random shorts among coins with a score."""
    H, k = cfg["H"], cfg["k"]
    o, h, l, c, qv, cff = A["o"], A["h"], A["l"], A["c"], A["qv"], A["cff"]
    cost = (FEE + SLIP) * cost_mult
    liq = 1.0 / L - MAINT_MARGIN
    E = E0
    curve = [(start, E)]
    pos_rec, held, nliq, ntr = [], {}, 0, 0
    T = len(times)
    dec = [t for t in sorted(preds) if start <= times[t + 1] < end and t + 1 + H < T]
    for t in dec:
        if E <= 0:
            break
        s = preds[t]
        ok = np.where(np.isfinite(s) & np.isfinite(o[t + 1]))[0]
        if len(ok) < 2 * k:
            continue
        if rng is not None:
            order = rng.permutation(ok)
        else:
            order = ok[np.argsort(s[ok])]
        legs = [(j, -1) for j in order[:k]] + [(j, 1) for j in order[::-1][:k]]
        j0 = t + 1
        new = {}
        for j, sg in legs:
            notional = min(L * E / (2 * k), 0.01 * qv[t, j]) if np.isfinite(qv[t, j]) else 0.0
            new[j] = sg * notional
        trade = sum(abs(new.get(j, 0) - held.get(j, 0)) for j in set(new) | set(held))
        E -= trade * cost
        ntr += sum(1 for j in new if np.sign(new[j]) != np.sign(held.get(j, 0)))
        pos = {j: dict(nt=nt, entry=o[j0, j], margin=abs(nt) / L, dead=False, pnl=0.0) for j, nt in new.items()}
        for b in range(j0, j0 + H):
            mtm = 0.0
            for j, p in pos.items():
                if not p["dead"]:
                    sg = np.sign(p["nt"])
                    worst = (l[b, j] if sg > 0 else h[b, j])
                    if np.isfinite(worst) and sg * (worst / p["entry"] - 1) <= -liq:
                        p["dead"], p["pnl"] = True, -p["margin"]
                        nliq += 1
                    else:
                        px = cff[b, j] if np.isfinite(cff[b, j]) else p["entry"]
                        p["pnl"] = p["nt"] * (px / p["entry"] - 1)
                mtm += p["pnl"]
            curve.append((int(times[b] + BAR - 1), E + mtm))
        xb = j0 + H
        drift = {}
        for j, p in pos.items():
            if not p["dead"]:
                if np.isfinite(o[xb, j]):
                    px = o[xb, j]
                    drift[j] = p["nt"] * px / p["entry"]
                else:  # data ended (delisted / halted): closed at the last traded price, not carried
                    px = cff[xb - 1, j] if np.isfinite(cff[xb - 1, j]) else p["entry"]
                    E -= abs(p["nt"]) * px / p["entry"] * cost
                p["pnl"] = p["nt"] * (px / p["entry"] - 1) - p["nt"] * F[j0 + 1:xb + 1, j].sum()
            pos_rec.append((int(t), int(j), p["pnl"] - abs(p["nt"]) * cost, int(np.sign(p["nt"]))))
            E += p["pnl"]
        held = drift
        curve[-1] = (curve[-1][0], E)
    if E > 0:
        E -= sum(abs(v) for v in held.values()) * cost
        curve[-1] = (curve[-1][0], E)
    curve = [(t, max(e, 0.0)) for t, e in curve]
    pnls = [r[2] for r in pos_rec]
    out = summary(curve, pnls)
    tot = sum(pnls)
    out["top1_share_of_profit"] = round(max(pnls) / tot * 100, 1) if pnls and tot > 0 else None
    out.update({"position_periods": len(pnls), "new_entries": ntr, "liquidations": nliq, "leverage": L,
                "cost_mult": cost_mult, "wiped_out": curve[-1][1] <= E0 * 0.01,
                "long_pnl": round(sum(r[2] for r in pos_rec if r[3] > 0), 2),
                "short_pnl": round(sum(r[2] for r in pos_rec if r[3] < 0), 2)})
    return out, curve, pos_rec


# ---------------------------------------------------------------- stages
BASE = dict(H=6, win="all", k=3, seed=0)
NEIGH = [dict(H=H, win=w, k=k, seed=0) for H in (3, 6, 12) for w in ("all", 12) for k in (2, 3, 5, 8)
         if not (H == 6 and w == "all" and k == 3)] + [dict(BASE, seed=1)]


def load(uni):
    syms = sorted(set(s for v in uni.values() for s in v) | {"BTCUSDT"})
    times, A, F = panel(syms)
    valid, ranks, reg, names = features(times, A, F, syms, uni)
    print("panel", len(syms), "symbols", A["o"].shape, flush=True)
    return syms, times, A, F, valid, ranks, reg, names


def wf_cfgs():
    seen, out = set(), []
    for c in [BASE] + NEIGH:
        key = (c["H"], c["win"], c["seed"])
        if key not in seen:
            seen.add(key); out.append({"H": c["H"], "win": c["win"], "seed": c["seed"]})
    return out


def stage_wf():
    uni = json.loads((DD / "universes.json").read_text())
    syms, times, A, F, valid, ranks, reg, names = load(uni)
    months = month_list(FULL_START, FULL_END)
    for g in wf_cfgs():
        walk_forward(g, times, valid, ranks, reg, names, A, months, "full")


def J(x, **kw):
    return json.dumps(x, default=lambda v: v.item() if hasattr(v, "item") else str(v), **kw)


def boot_p5(vals, n=5000, block=3, seed=0):
    rng = np.random.default_rng(seed)
    v = np.asarray(vals); m = len(v)
    nb = math.ceil(m / block)
    means = np.empty(n)
    for i in range(n):
        st = rng.integers(0, m - block + 1, nb)
        means[i] = np.concatenate([v[s:s + block] for s in st])[:m].mean()
    return float(np.percentile(means, 5))


def compound(vals):
    return (np.prod([1 + x / 100 for x in vals]) - 1) * 100


def stage_checks():
    uni = json.loads((DD / "universes.json").read_text())
    syms, times, A, F, valid, ranks, reg, names = load(uni)
    months = month_list(FULL_START, FULL_END)
    wfk = lambda c: {"H": c["H"], "win": c["win"], "seed": c["seed"]}
    P = walk_forward(wfk(BASE), times, valid, ranks, reg, names, A, months, "full")
    res = {"agent": "r2_ml", "strategy": "10_ml_walkforward A_hgb_H6_k3 at 1x, universe incl. delisted coins",
           "checks": {}}
    full, curve, rec = simulate(P, BASE, times, A, F, 1.0, FULL_START, FULL_END)
    dev, _, _ = simulate(P, BASE, times, A, F, 1.0, DEV_START, DEV_END)
    tst, _, _ = simulate(P, BASE, times, A, F, 1.0, TEST_START, TEST_END)
    dev2, _, _ = simulate(P, BASE, times, A, F, 1.0, DEV_START, DEV_END, cost_mult=2.0)
    tst2, _, _ = simulate(P, BASE, times, A, F, 1.0, TEST_START, TEST_END, cost_mult=2.0)
    full2, _, _ = simulate(P, BASE, times, A, F, 1.0, FULL_START, FULL_END, cost_mult=2.0)
    strip = lambda s: {k: v for k, v in s.items() if k != "months"}
    res["full_1x"], res["dev_1x"], res["test_1x"] = strip(full), strip(dev), tst
    res["full_1x_2xcost"], res["dev_1x_2xcost"], res["test_1x_2xcost"] = strip(full2), strip(dev2), strip(tst2)
    mon = full["months"]
    vals = list(mon.values())
    print("FULL", J(strip(full)), "\nDEV", J(strip(dev)), "\nTEST", J(strip(tst)), flush=True)

    # 1 both halves
    c1 = dict(dev_net=dev["net_pct"], test_net=tst["net_pct"], dev_net_2xcost=dev2["net_pct"],
              test_net_2xcost=tst2["net_pct"])
    res["checks"]["1"] = {"pass": all(v > 0 for v in c1.values()), "numbers": c1}
    # 2 most years
    yrs = {y: round(compound([v for k, v in mon.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
    ex21 = round(compound([v for k, v in mon.items() if k >= "2022-01"]), 2)
    c2 = dict(years=yrs, years_positive=sum(v > 0 for v in yrs.values()), net_2022_2026=ex21)
    res["checks"]["2"] = {"pass": c2["years_positive"] >= 4 and ex21 > 0, "numbers": c2}
    # 3 statistics
    n = len(vals); mu = float(np.mean(vals)); sd = float(np.std(vals, ddof=1))
    tstat = mu / (sd / math.sqrt(n)); p5 = boot_p5(vals)
    c3 = dict(n_months=n, mean_month=round(mu, 3), t_stat=round(tstat, 2), boot_p5_mean_month=round(p5, 3))
    res["checks"]["3"] = {"pass": tstat >= 2.0 and p5 > 0, "numbers": c3}
    # 4 drop 3 best months
    best3 = sorted(mon, key=mon.get)[-3:]
    rest = [v for k, v in mon.items() if k not in best3]
    c4 = dict(best3={k: round(mon[k], 2) for k in best3}, net_without_best3=round(compound(rest), 2),
              net_full=full["net_pct"])
    res["checks"]["4"] = {"pass": c4["net_without_best3"] > 0, "numbers": c4}
    # 5 neighbours
    rows = []
    for c in NEIGH:
        Pn = walk_forward(wfk(c), times, valid, ranks, reg, names, A, months, "full")
        sd_, _, _ = simulate(Pn, c, times, A, F, 1.0, DEV_START, DEV_END)
        st_, _, _ = simulate(Pn, c, times, A, F, 1.0, TEST_START, TEST_END)
        rows.append(dict(cfg=c, dev_net=sd_["net_pct"], test_net=st_["net_pct"], dev_mean=sd_["mean_month"],
                         test_mean=st_["mean_month"], both_pos=sd_["net_pct"] > 0 and st_["net_pct"] > 0))
        print("neigh", rows[-1], flush=True)
    frac = sum(r["both_pos"] for r in rows) / len(rows)
    c5 = dict(n=len(rows), share_both_positive=round(frac * 100, 1), grid=rows)
    res["checks"]["5"] = {"pass": frac >= 0.70, "numbers": c5}
    # 6 random picks
    rng = np.random.default_rng(12345)
    rnd = []
    for i in range(300):
        s_, _, _ = simulate(P, BASE, times, A, F, 1.0, FULL_START, FULL_END, rng=rng)
        rnd.append(s_["net_pct"])
        if i % 50 == 0:
            print("random", i, s_["net_pct"], flush=True)
    rnd = np.array(rnd)
    beat = float((full["net_pct"] > rnd).mean())
    c6 = dict(n_random=len(rnd), strategy_net=full["net_pct"], random_median_net=round(float(np.median(rnd)), 2),
              random_p95_net=round(float(np.percentile(rnd, 95)), 2), random_share_positive=round(float((rnd > 0).mean()) * 100, 1),
              share_beaten=round(beat * 100, 1))
    res["checks"]["6"] = {"pass": beat >= 0.95, "numbers": c6}
    # 7 top coins / single position
    by = {}
    for t, j, pnl, sg in rec:
        by[syms[j]] = by.get(syms[j], 0.0) + pnl
    top5 = sorted(by, key=by.get, reverse=True)[:5]
    tot = sum(r[2] for r in rec)
    top_pos = max(rec, key=lambda r: r[2])
    uni_x = {k: [s for s in v if s not in top5] for k, v in json.loads((DD / "universes.json").read_text()).items()}
    # refill to 40 with the next coins by volume: rebuild from the stored volume ranking
    uni_x = rebuild_universe(exclude=set(top5))
    syms_x, times_x, A_x, F_x, valid_x, ranks_x, reg_x, names_x = load(uni_x)
    Px = walk_forward(wfk(BASE), times_x, valid_x, ranks_x, reg_x, names_x, A_x, months, "no_top5")
    sx, _, _ = simulate(Px, BASE, times_x, A_x, F_x, 1.0, FULL_START, FULL_END)
    share = top_pos[2] / tot * 100 if tot > 0 else None
    c7 = dict(top5_coins={s: round(by[s], 2) for s in top5}, total_pnl=round(tot, 2),
              net_without_top5=sx["net_pct"], mean_month_without_top5=sx["mean_month"],
              largest_position=dict(coin=syms[top_pos[1]], decided=mkey(times[top_pos[0]]), pnl=round(top_pos[2], 2),
                                    share_of_total_pct=round(share, 2) if share is not None else None))
    res["checks"]["7"] = {"pass": sx["net_pct"] > 0 and share is not None and share <= 10, "numbers": c7}
    # 8 compounding
    c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"])
    res["checks"]["8"] = {"pass": full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"], "numbers": c8}

    res["robust"] = all(v["pass"] for v in res["checks"].values())
    res["monthly_1x"] = {k: round(v, 2) for k, v in mon.items()}
    lev = []
    for L in (1, 1.5, 2):
        sf, _, _ = simulate(P, BASE, times, A, F, L, FULL_START, FULL_END)
        st, _, _ = simulate(P, BASE, times, A, F, L, TEST_START, TEST_END)
        lev.append(dict(L=L, full_mean=sf["mean_month"], full_median=sf["median_month"], full_geo=sf["geo_month"],
                        full_worst=sf["worst_month"], full_max_dd=sf["max_dd_pct"], full_liq=sf["liquidations"],
                        full_wiped=sf["wiped_out"], test_mean=st["mean_month"], test_median=st["median_month"],
                        test_geo=st["geo_month"], test_max_dd=st["max_dd_pct"]))
        print("lev", lev[-1], flush=True)
    res["leverage_table"] = lev
    alive = {s["symbol"] for s in perp_symbols()}
    dead_tr = [r for r in rec if syms[r[1]] not in alive]
    res["delisted_exposure"] = dict(positions=len(dead_tr), of_total=len(rec),
                                    pnl=round(sum(r[2] for r in dead_tr), 2),
                                    short_pnl=round(sum(r[2] for r in dead_tr if r[3] < 0), 2),
                                    long_pnl=round(sum(r[2] for r in dead_tr if r[3] > 0), 2),
                                    coins=sorted({syms[r[1]] for r in dead_tr}))
    (OUT / "results.json").write_text(J(res, indent=1))
    print(J({k: (v["pass"], v["numbers"] if k != "5" else v["numbers"]["share_both_positive"])
                      for k, v in res["checks"].items()}))
    print("ROBUST", res["robust"])


def rebuild_universe(exclude):
    qv = {}
    for s in eligible_symbols():
        if s in exclude:
            continue
        qv[s] = np.load(DD / "bars4h" / f"{s}.npz")["b"][4]
    uni = {}
    for at in month_list(UNI_FROM, TEST_END):
        i1 = (at - DATA_FROM) // BAR
        ranked = []
        for s, v in qv.items():
            w = v[max(i1 - 180, 0):i1]
            if np.isfinite(w).sum() >= 0.8 * 180:
                ranked.append((np.nansum(w), s))
        uni[mkey(at)] = [s for _, s in sorted(ranked, reverse=True)[:N_UNI]]
    return uni


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "checks"
    {"build": stage_build, "wf": stage_wf, "checks": stage_checks}[stage]()
