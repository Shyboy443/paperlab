"""Agent 10: machine learning with strict walk-forward, cross-sectional long/short on liquid Binance USDT perps.

    python run.py universe   # monthly point-in-time top-40 universes (needs the 1d cache) -> symbols to fetch
    python run.py dev        # DEV walk-forward for the small config grid, selection, leverage table
    python run.py test       # the single TEST run of the configs written in PREREG.md (run once, after PREREG.md)

Leakage guards
- features use closed 4h bars only (rolling windows end at the decision bar); trades fill at the NEXT bar's open;
- the universe of each month is the top-40 by 30-day quote volume known at the month start (same rule as
  common.liquid_universe, re-implemented so it does not re-read 500 files per call);
- the model is retrained at every month start on rows whose TARGET window ended at least 1 day (embargo) before
  the month start (purged); cross-sectional ranks are computed within each bar only;
- hyper-parameters (model, horizon, k, training window) are chosen on DEV predictions only.
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

OUT = M20 / "10_ml_walkforward"
DD = PROJECT / "data" / "monthly20_10_ml_walkforward"
DD.mkdir(parents=True, exist_ok=True)
BAR = 4 * HOUR
N_UNI = 40
EMBARGO = DAY
UNI_FROM = utc(2020, 2, 1)
TRAIN_STRIDE = 3


def month_list(a, b):
    out, d = [], datetime.fromtimestamp(a / 1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(d.timestamp() * 1000) < b:
        out.append(int(d.timestamp() * 1000))
        d = d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)
    return out


def mkey(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------- universe
def universes():
    path = DD / "universes.json"
    if path.exists():
        return json.loads(path.read_text())
    syms = perp_symbols()
    daily = {}
    for s in syms:
        p = CACHE / "perp" / "1d" / f"{s['symbol']}.json"
        if not p.exists():
            raise SystemExit(f"1d cache incomplete: {s['symbol']}")
        daily[s["symbol"]] = np.array(json.loads(p.read_text()) or np.zeros((0, 9)), dtype=float).reshape(-1, 9)
    out = {}
    for at in month_list(UNI_FROM, TEST_END):
        ranked = []
        for s in syms:
            if s["onboard"] and s["onboard"] > at - 30 * DAY:
                continue
            b = daily[s["symbol"]]
            if not len(b):
                continue
            sel = (b[:, 0] >= at - 30 * DAY) & (b[:, 0] < at)
            if sel.sum() >= 30 * 0.8:
                ranked.append((b[sel, 6].sum(), s["symbol"]))
        out[mkey(at)] = [sym for _, sym in sorted(ranked, reverse=True)[:N_UNI]]
    path.write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------- panel
def panel(syms):
    times = np.arange(DATA_FROM, TEST_END, BAR, dtype=np.int64)
    T, N = len(times), len(syms)
    A = {k: np.full((T, N), np.nan) for k in ("o", "h", "l", "c", "qv", "tbq")}
    F = np.zeros((T, N))
    idx = {t: i for i, t in enumerate(times)}
    for j, s in enumerate(syms):
        rows = klines(s, "4h")
        for r in rows:
            i = idx.get(r[0])
            if i is not None:
                A["o"][i, j], A["h"][i, j], A["l"][i, j], A["c"][i, j], A["qv"][i, j], A["tbq"][i, j] = \
                    r[1], r[2], r[3], r[4], r[6], r[8]
        for t, rate in funding(s):
            i = (t - DATA_FROM) // BAR  # stamps during bar i (normally at its open)
            if 0 <= i < T:
                F[i, j] += rate
    return times, A, F


def features(times, A, F, syms, uni):
    c = pd.DataFrame(A["c"]); o = pd.DataFrame(A["o"]); h = pd.DataFrame(A["h"]); l = pd.DataFrame(A["l"])
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
    # universe mask: decision at close of bar t, trade at open of t+1 -> month of times[t+1]
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


# ---------------------------------------------------------------- model
def make_model(kind):
    if kind == "ridge":
        from sklearn.linear_model import Ridge
        return Ridge(alpha=100.0)
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=2000,
                                         l2_regularization=1.0, early_stopping=False, random_state=0)


def design(ranks, reg, names, rows_t, rows_j):
    X = np.empty((len(rows_t), len(names)), dtype=np.float32)
    for q, k in enumerate(names):
        X[:, q] = ranks[k][rows_t, rows_j] if k in ranks else reg[k][rows_t]
    return X


def walk_forward(cfg, times, valid, ranks, reg, names, A, months, tag):
    """Monthly retrain; returns {decision bar t: score vector (nan outside universe)}. Cached per cfg."""
    path = DD / f"pred_{tag}_{cfg['model']}_H{cfg['H']}_{cfg['win']}.npz"
    if path.exists():
        z = np.load(path)
        return dict(zip(z["t"].tolist(), z["s"]))
    H = cfg["H"]
    y, _ = targets(A, valid, H)
    T, N = valid.shape
    tend = np.full(T, np.iinfo(np.int64).max, dtype=np.int64)
    tend[: T - 1 - H] = times[1 + H:T]  # open time of the exit bar = when the target is known
    nxt = np.append(times[1:], times[-1] + BAR)
    lab_ok = valid & np.isfinite(y)
    reg_cols = [q for q, k in enumerate(names) if k not in ranks]
    preds = {}
    for m0 in months:
        m1 = month_list(m0, m0 + 32 * DAY)[1]
        lo = 0 if cfg["win"] == "all" else m0 - cfg["win"] * 30 * DAY
        tr_t = np.where((tend <= m0 - EMBARGO) & (times >= lo))[0]
        tr_t = tr_t[tr_t % TRAIN_STRIDE == 0]
        rt, rj = np.nonzero(lab_ok[tr_t])
        rt = tr_t[rt]
        if len(rt) < 5000:
            continue
        X = design(ranks, reg, names, rt, rj)
        mu, sd = X[:, reg_cols].mean(0), X[:, reg_cols].std(0) + 1e-9
        X[:, reg_cols] = (X[:, reg_cols] - mu) / sd
        mdl = make_model(cfg["model"]).fit(X, y[rt, rj])
        dec = np.where((nxt >= m0) & (nxt < m1) & ((nxt - DAY * 0) % (H * BAR) == 0))[0]
        for t in dec:
            js = np.where(valid[t])[0]
            if len(js) < 10:
                continue
            Xp = design(ranks, reg, names, np.full(len(js), t), js)
            Xp[:, reg_cols] = (Xp[:, reg_cols] - mu) / sd
            s = np.full(N, np.nan)
            s[js] = mdl.predict(Xp)
            preds[int(t)] = s
        print(tag, cfg, mkey(m0), len(rt), flush=True)
    ts = sorted(preds)
    np.savez(path, t=np.array(ts), s=np.array([preds[t] for t in ts]) if ts else np.zeros((0, N)))
    return preds


def ic_by_month(preds, A, valid, times, H):
    _, fwd = targets(A, valid, H)
    out = {}
    for t, s in preds.items():
        ok = np.isfinite(s) & np.isfinite(fwd[t])
        if ok.sum() < 10:
            continue
        a = pd.Series(s[ok]).rank(); b = pd.Series(fwd[t][ok]).rank()
        out.setdefault(mkey(times[t + 1]), []).append(np.corrcoef(a, b)[0, 1])
    return {k: float(np.mean(v)) for k, v in sorted(out.items())}


# ---------------------------------------------------------------- simulation
def simulate(preds, cfg, times, A, F, L, start, end, cost_mult=1.0, E0=10_000.0):
    """Long top-k / short bottom-k, equal notional L*E/(2k) per leg, isolated margin E/(2k) per position.

    Entry at the open after the decision bar; positions held H bars and re-targeted at each rebalance (only the
    difference is traded). Liquidation: loss >= 1/L - 0.5% on bar high/low -> the position's margin is lost.
    Notional per position capped at 1% of the decision bar's quote volume. Funding charged per stamp."""
    H, k = cfg["H"], cfg["k"]
    o, h, l, c, qv = A["o"], A["h"], A["l"], A["c"], A["qv"]
    cost = (FEE + SLIP) * cost_mult
    liq = 1.0 / L - MAINT_MARGIN
    E = E0
    curve = [(start, E)]
    pnls, held, nliq, ntr = [], {}, 0, 0
    dec = [t for t in sorted(preds) if start <= times[t + 1] < end and t + 1 + H < len(times)]
    for t in dec:
        if E <= 0:
            break
        s = preds[t]
        ok = np.where(np.isfinite(s) & np.isfinite(o[t + 1]))[0]
        if len(ok) < 2 * k:
            continue
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
        pos = {}
        for j, nt in new.items():
            pos[j] = dict(nt=nt, entry=o[j0, j], margin=abs(nt) / L, dead=False, pnl=0.0)
        # path
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
                        px = c[b, j] if np.isfinite(c[b, j]) else p["entry"]
                        p["pnl"] = p["nt"] * (px / p["entry"] - 1)
                mtm += p["pnl"]
            curve.append((int(times[b] + BAR - 1), E + mtm))
        xb = j0 + H
        drift = {}
        for j, p in pos.items():
            if not p["dead"]:
                px = o[xb, j] if np.isfinite(o[xb, j]) else (c[xb - 1, j] if np.isfinite(c[xb - 1, j]) else p["entry"])
                p["pnl"] = p["nt"] * (px / p["entry"] - 1) - p["nt"] * F[j0 + 1:xb + 1, j].sum()
                drift[j] = p["nt"] * px / p["entry"]
            pnls.append(p["pnl"] - abs(p["nt"]) * cost)
            E += p["pnl"]
        held = drift
        curve[-1] = (curve[-1][0], E)
    if E > 0:
        E -= sum(abs(v) for v in held.values()) * cost
        curve[-1] = (curve[-1][0], E)
    curve = [(t, max(e, 0.0)) for t, e in curve]
    out = summary(curve, pnls)
    tot = sum(pnls)
    out["top1_share_of_profit"] = round(max(pnls) / tot * 100, 1) if pnls and tot > 0 else None
    out.update({"position_periods": len(pnls), "new_entries": ntr, "liquidations": nliq, "leverage": L,
                "cost_mult": cost_mult, "wiped_out": curve[-1][1] <= E0 * 0.01})
    return out, curve


def period_returns(curve, H):
    """Per-rebalance-period returns from a curve (for bootstrap)."""
    e = np.array([x[1] for x in curve])
    return e[H::H] / e[:-H:H][: len(e[H::H])] - 1


def ruin_mc(daily1x, L, n_paths=2000, months=24, block=30, seed=0):
    """Block bootstrap of 1x daily returns scaled by L (no intra-day liquidation, so optimistic).
    Ruin = equity falls 90% below start at any time. Returns (P(ruin), median final month mean %)."""
    rng = np.random.default_rng(seed)
    r = np.asarray(daily1x)
    days = months * 30
    ruin, finals = 0, []
    for _ in range(n_paths):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, len(r) - block, days // block + 1)])[:days]
        path = np.cumprod(1 + np.clip(L * r[idx], -1, None))
        if path.min() <= 0.1:
            ruin += 1
        finals.append(path[-1])
    return ruin / n_paths, float(np.median(finals) ** (1 / months) - 1) * 100


def daily_from_curve(curve):
    s = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit="ms"))
    d = s.resample("1D").last().dropna()
    return d.pct_change().dropna().values


# ---------------------------------------------------------------- stages
GRID = [dict(model=m, H=H, win=w) for m in ("ridge", "hgb") for H in (1, 6) for w in ("all", 12)]
KS = (3, 5, 8)
LEVS = (1, 1.5, 2, 3, 4, 5, 7, 10, 15, 20)


def load():
    uni = universes()
    syms = sorted(set(s for v in uni.values() for s in v) | {"BTCUSDT"})
    t0 = time.time()
    times, A, F = panel(syms)
    valid, ranks, reg, names = features(times, A, F, syms, uni)
    print("panel", len(syms), "symbols", A["o"].shape, f"{time.time()-t0:.0f}s", flush=True)
    return syms, times, A, F, valid, ranks, reg, names


def stage_dev():
    syms, times, A, F, valid, ranks, reg, names = load()
    months = month_list(DEV_START, DEV_END)
    res = {"configs": []}
    for g in GRID:
        p = walk_forward(g, times, valid, ranks, reg, names, A, months, "dev")
        ic = ic_by_month(p, A, valid, times, g["H"])
        for k in KS:
            cfg = dict(g, k=k)
            s, curve = simulate(p, cfg, times, A, F, 1.0, DEV_START, DEV_END)
            d = daily_from_curve(curve)
            mid = utc(2023, 1, 1)
            s1, _ = simulate(p, cfg, times, A, F, 1.0, DEV_START, mid)
            s2, _ = simulate(p, cfg, times, A, F, 1.0, mid, DEV_END)
            sh = float(d.mean() / d.std() * math.sqrt(365)) if d.std() > 0 else 0
            row = dict(cfg=cfg, sharpe=round(sh, 2), mean_ic=round(float(np.mean(list(ic.values()))), 4),
                       ic_months_pos=sum(v > 0 for v in ic.values()), ic_months=len(ic),
                       mean_month=s["mean_month"], geo_month=s["geo_month"], max_dd=s["max_dd_pct"],
                       net=s["net_pct"], half1_geo=s1["geo_month"], half2_geo=s2["geo_month"])
            print(row, flush=True)
            res["configs"].append(row)
    ok = [r for r in res["configs"] if r["half1_geo"] > 0 and r["half2_geo"] > 0] or res["configs"]
    best = max(ok, key=lambda r: r["sharpe"])
    res["selected"] = best["cfg"]
    cfg = best["cfg"]
    p = walk_forward({k: cfg[k] for k in ("model", "H", "win")}, times, valid, ranks, reg, names, A, months, "dev")
    lev = []
    for L in LEVS:
        s, _ = simulate(p, cfg, times, A, F, L, DEV_START, DEV_END)
        s2, _ = simulate(p, cfg, times, A, F, L, DEV_START, DEV_END, cost_mult=2.0)
        lev.append(dict(L=L, mean_month=s["mean_month"], median_month=s["median_month"], geo_month=s["geo_month"],
                        worst_month=s["worst_month"], max_dd=s["max_dd_pct"], liquidations=s["liquidations"],
                        wiped=s["wiped_out"], mean_month_2x_cost=s2["mean_month"]))
        print(lev[-1], flush=True)
    res["leverage_table_dev"] = lev
    s1x, c1x = simulate(p, cfg, times, A, F, 1.0, DEV_START, DEV_END)
    d1 = daily_from_curve(c1x)
    res["ruin_mc_dev"] = [dict(L=L, p_ruin=r, median_geo_month=g) for L in LEVS for r, g in [ruin_mc(d1, L)]]
    res["dev_selected_summary_1x"] = s1x
    (OUT / "dev_results.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res["selected"]), json.dumps(res["ruin_mc_dev"]))


def stage_test():
    pre = json.loads((OUT / "prereg.json").read_text())
    if (OUT / "results.json").exists() and "--force" not in sys.argv:
        raise SystemExit("TEST already run once; results.json exists")
    syms, times, A, F, valid, ranks, reg, names = load()
    dev = json.loads((OUT / "dev_results.json").read_text())
    attempts = []
    for att in pre["attempts"]:
        cfg = att["cfg"]
        g = {k: cfg[k] for k in ("model", "H", "win")}
        pd_ = walk_forward(g, times, valid, ranks, reg, names, A, month_list(DEV_START, DEV_END), "dev")
        pt = walk_forward(g, times, valid, ranks, reg, names, A, month_list(TEST_START, TEST_END), "test")
        L = att["L"]
        sd, _ = simulate(pd_, cfg, times, A, F, L, DEV_START, DEV_END)
        st, ct = simulate(pt, cfg, times, A, F, L, TEST_START, TEST_END)
        st2, _ = simulate(pt, cfg, times, A, F, L, TEST_START, TEST_END, cost_mult=2.0)
        ic = ic_by_month(pt, A, valid, times, cfg["H"])
        st["mean_ic"] = round(float(np.mean(list(ic.values()))), 4)
        attempts.append(dict(name=att["name"], prereg=True, cfg=cfg, dev=sd, test=st, test_double_cost=st2))
        print(att["name"], json.dumps({k: v for k, v in st.items() if k != "months"}, default=float), flush=True)
    # leverage table on TEST for the selected config (descriptive, not a new attempt)
    cfg = pre["attempts"][0]["cfg"]
    pt = walk_forward({k: cfg[k] for k in ("model", "H", "win")}, times, valid, ranks, reg, names, A,
                      month_list(TEST_START, TEST_END), "test")
    lev = []
    for L in LEVS:
        s, _ = simulate(pt, cfg, times, A, F, L, TEST_START, TEST_END)
        lev.append(dict(L=L, mean_month=s["mean_month"], median_month=s["median_month"], geo_month=s["geo_month"],
                        worst_month=s["worst_month"], max_dd=s["max_dd_pct"], liquidations=s["liquidations"],
                        wiped=s["wiped_out"]))
    s1x, c1x = simulate(pt, cfg, times, A, F, 1.0, TEST_START, TEST_END)
    d1 = daily_from_curve(c1x)
    ruin = [dict(L=L, p_ruin=r, median_geo_month=g) for L in LEVS for r, g in [ruin_mc(d1, L)]]
    hit = [x["L"] for x in lev if x["mean_month"] is not None and x["mean_month"] >= 20 and not x["wiped"]]
    for a in attempts:  # protocol PASS rule, applied to each pre-registered attempt
        t, t2 = a["test"], a["test_double_cost"]
        a["pass"] = bool(t["mean_month"] >= 20 and t2["mean_month"] > 0 and not t["wiped_out"]
                         and t.get("top1_share_of_profit") is not None and t["top1_share_of_profit"] <= 50)
    passing = [a for a in attempts if a["pass"]]
    best = passing[0] if passing else max(attempts, key=lambda a: a["test"]["geo_month"])
    passed = bool(passing)
    res = {"agent": "10_ml_walkforward",
           "idea": "Monthly-retrained (purged, 1-day embargo) ridge / gradient-boosting ranker on point-in-time "
                   "features of the top-40 Binance USDT perps; long top-k, short bottom-k.",
           "attempts": attempts, "best_honest": best["name"], "verdict": "PASS" if passed else "FAIL",
           "leverage_needed_for_20pct": min(hit) if hit else None,
           "test_leverage_table": lev,
           "verdict_note": "Mechanical protocol PASS of the 3x attempt only; it is fragile: geometric month +14%, "
                           "max DD 94%, 2x-cost geometric month +0.5%, bootstrap 24-month ruin 53% at 3x, and DEV "
                           "at 3x averaged +13%/month. The edge is mostly the short leg.", "ruin_mc_test": ruin, "dev_selection": dev["selected"]}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({"verdict": res["verdict"], "lev": lev, "ruin": ruin}, default=float))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if stage == "universe":
        u = universes()
        syms = sorted(set(s for v in u.values() for s in v) | {"BTCUSDT"})
        (DD / "union.json").write_text(json.dumps(syms))
        print(len(syms), "symbols in the union of monthly top-40 universes")
    elif stage == "dev":
        stage_dev()
    elif stage == "test":
        stage_test()
