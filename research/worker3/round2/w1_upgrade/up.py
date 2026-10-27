"""w1_upgrade: pre-registered upgrades of Worker 1 (r4_07 ensemble). See PREREG.md.

    python up.py repro   # baseline == Worker 1 (TEST median 3.84 / geo 5.33)
    python up.py dev     # the 4 changes + their neighbours on DEV only -> dev.json (selection rule in PREREG.md)
    python up.py test    # the ONE TEST run of the pre-registered PRIMARY (+SECONDARY), the 8 checks -> results.json
Research only: no orders, no keys.
"""
import json, math, sys, time
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ens import *  # noqa  (copy of r4_07 ens.py + pos_out)
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
import core as W2  # noqa  Worker 2 shared code (read-only import)

t0 = time.time()
D = Data()
FULL_START = DEV_START


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


LEGP = dict(R=dict(lb=14, k=10, univ=40), M=dict(k=3, seed=0), T=dict(look=60, univ=50))
I_START = D.idx(FULL_START)
W1CFG = dict(scheme="IV", wlook=60, wevery=7)


def make_leg(kind, drop=(), rng=None):
    p = LEGP[kind]
    if kind == "R":
        F = leg_rotation(D, p["lb"], p["k"], p["univ"], drop=drop, rng=rng)
    elif kind == "M":
        F = leg_ml(D, p["k"], p["seed"], drop=drop, rng=rng)
    else:
        F = leg_mom(D, p["look"], p["univ"], drop=drop, rng=rng)
    return (kind, F, trade_mask(D, F, kind))


def standalone(leg):
    r = simulate(D, [leg], FULL_START, TEST_END)
    out = np.full(D.T, np.nan)
    out[r["i0"]:r["i0"] + len(r["ret"])] = r["ret"]
    return out


# ------------------------------------------------------------------ weight modifications (all causal: rows < d)
def ivar_path(R, lookback=60, every=7, min_obs=20):
    T, n = R.shape
    W = np.full((T, n), 1 / n); cur = np.full(n, 1 / n)
    for d in range(T):
        if (d - I_START) % every == 0:
            X = R[max(0, d - lookback):d]; X = X[~np.isnan(X).any(1)]
            if len(X) >= min_obs:
                iv = 1 / (X.var(0, ddof=1) + 1e-12); cur = iv / iv.sum()
            else:
                cur = np.full(n, 1 / n)
        W[d] = cur
    return W


def v1_scale(R, win=126, every=7, ref="median", min_obs=60, min_hist=8):
    """Barroso & Santa-Clara: s_i = min(1, ref_i / sigma_i), sigma over `win` days, ref = expanding stat of past sigmas."""
    T, n = R.shape
    S = np.ones((T, n)); cur = np.ones(n); hist = [[] for _ in range(n)]
    for d in range(T):
        if (d - I_START) % every == 0:
            for i in range(n):
                x = R[max(0, d - win):d, i]; x = x[~np.isnan(x)]
                if len(x) < min_obs:
                    cur[i] = 1.0; continue
                sg = x.std(ddof=1)
                h = hist[i]
                if len(h) >= min_hist:
                    r = {"median": np.median(h), "mean": np.mean(h), "p25": np.percentile(h, 25),
                         "p75": np.percentile(h, 75)}[ref]
                    cur[i] = min(1.0, r / max(sg, 1e-12))
                else:
                    cur[i] = 1.0
                h.append(sg)
        S[d] = cur
    return S


def v2_scale(legkinds, bear=365, reb=30, cut=0.5, every=7, use_reb=True):
    """Daniel & Moskowitz: in a BTC bear state that is rebounding, cut the momentum legs (R, T)."""
    j = D.col["BTCUSDT"]; C = D.Cf[:, j]
    n = len(legkinds)
    S = np.ones((D.T, n)); cur = np.ones(n)
    mom = np.array([k in ("R", "T") for k in legkinds])
    for d in range(D.T):
        if (d - I_START) % every == 0:
            cur = np.ones(n)
            if d - 1 - max(bear, reb) >= 0 and np.isfinite(C[d - 1 - bear]):
                is_bear = C[d - 1] / C[d - 1 - bear] < 1
                is_reb = C[d - 1] / C[d - 1 - reb] > 1
                if is_bear and (is_reb or not use_reb):
                    cur[mom] = cut
        S[d] = cur
    return S


def weights(v, SRm, kinds):
    if v.get("scheme", "IV") == "IVAR":
        W = ivar_path(SRm, v.get("wlook", 60), v.get("wevery", 7))
    else:
        W = weight_path(D, SRm, "IV", v.get("wlook", 60), v.get("wevery", 7), start_i=I_START)
    if "v1" in v:
        p = v["v1"]
        W = W * v1_scale(SRm, p.get("win", 126), p.get("every", 7), p.get("ref", "median"))
    if "v2" in v:
        p = v["v2"]
        W = W * v2_scale(kinds, p.get("bear", 365), p.get("reb", 30), p.get("cut", 0.5), p.get("every", 7), p.get("use_reb", True))
    if v.get("w2", {}).get("fixed"):
        W = W * 0.75
    return W


# ------------------------------------------------------------------ Worker 2 leg
W2P = dict(W2.SEL, sig_ref=0.01154)  # Worker 2 primary rule P
W2_D0, W2_D1 = W2.dayidx(DEV_START), W2.dayidx(TEST_END)
_w2cache = {}


def w2_trades(p, cost_mult=1.0, exclude=(), rng=None):
    """Worker 2 trades over the full period: list of (D-day index, D-col, side, x = notional/eq, ret)."""
    key = (json.dumps(p, sort_keys=True), cost_mult, tuple(exclude))
    if rng is None and key in _w2cache:
        return _w2cache[key]
    ex = [W2.syms.index(s) for s in exclude if s in W2.syms]
    rows = W2.signals(p, W2_D0, W2_D1, ex)
    if rng is not None:  # same days / times / sides / size multipliers, coins drawn from that day's eligible alts
        rows = [(r[0], r[1], r[2], int(rng.choice(W2.ALT_ELIG[r[0]])), r[4], r[5]) for r in rows]
        rows = [r for r in rows if np.isfinite(W2.O[r[1], r[3]])]
    _, tr, _ = W2.run(rows, W2_D0, W2_D1, p["K"], cost_mult=cost_mult)
    out = []
    for d, j, s, pd_, peq, x, ret in tr:
        di = D.idx(int(W2.td[d]))
        sym = W2.syms[j]
        if di < D.T and D.day[di] == int(W2.td[d]) and sym in D.col:
            out.append((di, D.col[sym], s, x, ret))
    if rng is None:
        _w2cache[key] = out
    return out


# ------------------------------------------------------------------ one variant run
_sr = {}


def run_variant(v, start, end, cost_mult=1.0, drop=(), rng=None, record=False, wpath=None, kinds="RMT"):
    legs = [make_leg(k, drop=drop, rng=rng) for k in kinds]
    if wpath is None:
        key = (kinds, tuple(drop))
        if rng is None and key in _sr:
            SRm = _sr[key]
        else:
            SRm = np.column_stack([standalone(l) for l in legs])
            if rng is None:
                _sr[key] = SRm
        wpath = weights(v, SRm, kinds)
    has_w2 = "w2" in v
    pos = np.zeros((D.T, D.N)) if has_w2 else None
    r = simulate(D, legs, start, end, wpath=wpath, cost_mult=cost_mult, record=record, pos_out=pos)
    r["wpath"] = wpath
    if has_w2:
        p = dict(W2P, **v["w2"].get("p", {}))
        tr = w2_trades(p, cost_mult, exclude=drop, rng=rng)
        i0 = r["i0"]; n = len(r["ret"])
        r2 = np.zeros(n); by_day = {}
        for t in tr:
            if i0 <= t[0] < i0 + n:
                by_day.setdefault(t[0], []).append(t)
        eqs = np.array([e for _, e in r["curve"]])
        w2pos = []
        ntr = 0
        for di, ts in by_day.items():
            nv = pos[di]
            G1 = np.abs(nv).sum()
            dG = sum(abs(nv[j] + s * x) - abs(nv[j]) for _, j, s, x, _ in ts)
            room = max(0.0, 1 - G1)
            f = 1.0 if dG <= room else room / dG
            k = di - i0
            for _, j, s, x, ret in ts:
                r2[k] += f * x * ret
                if f > 0:
                    ntr += 1
                    w2pos.append((j, di, f * x * ret))
        rb = r["ret"] + r2
        new = eqs[0] * np.cumprod(1 + rb)
        r["w1_curve"] = r["curve"]
        r["curve"] = [r["curve"][0]] + [(ts, float(e)) for (ts, _), e in zip(r["curve"][1:], new)]
        r["ret"] = rb
        r["w2_trades_used"] = ntr
        r["w2_ret"] = r2
        if record:  # W2 trades as positions, $ on the combined equity of the day before
            for j, di, rr in w2pos:
                r["episodes"].append((j, di, di + 1, float(rr * eqs[0] * np.prod(1 + rb[:di - i0]))))
    r["stats"] = stats_of(r["curve"])
    return r


def brief(r):
    s = r["stats"]
    out = {k: s[k] for k in ("median_month", "geo_month", "mean_month", "worst_month", "months_up", "n_months",
                             "max_dd_pct", "net_pct", "t_stat")}
    out["liq"] = r["liquidations"]
    if "w2_trades_used" in r:
        out["w2_trades_used"] = r["w2_trades_used"]
    return out


# ------------------------------------------------------------------ variants + neighbours (PREREG.md)
V = {
    "W1": {},
    "V1": {"v1": {}},
    "V2": {"v2": {}},
    "V3": {"scheme": "IVAR"},
    "V4": {"w2": {}},
}


def neighbours(name):
    out = []
    if name == "V1":
        out += [(f"win={w}", {"v1": {"win": w}}) for w in (63, 94, 158, 189)]
        out += [(f"every={e}", {"v1": {"every": e}, "wevery": 7}) for e in (3, 14)]
        out += [(f"ref={r}", {"v1": {"ref": r}}) for r in ("mean", "p25", "p75")]
        out += [(f"wlook={l}", {"v1": {}, "wlook": l}) for l in (30, 90)]
    elif name == "V2":
        out += [(f"bear={b}", {"v2": {"bear": b}}) for b in (180, 270, 540, 730)]
        out += [(f"reb={r}", {"v2": {"reb": r}}) for r in (14, 21, 45, 60)]
        out += [(f"cut={c}", {"v2": {"cut": c}}) for c in (0.0, 0.25, 0.75)]
        out += [("bear_only", {"v2": {"use_reb": False}})]
    elif name == "V3":
        out += [(f"wlook={l}", {"scheme": "IVAR", "wlook": l}) for l in (30, 45, 75, 90)]
        out += [(f"wevery={e}", {"scheme": "IVAR", "wevery": e}) for e in (1, 3, 14, 30)]
    elif name == "V4":
        out += [(f"K={k}", {"w2": {"p": {"K": k}}}) for k in (3, 4, 6, 8)]
        out += [(f"k={k}", {"w2": {"p": {"k": k}}}) for k in (1.5, 2.5)]
        out += [(f"H={h}", {"w2": {"p": {"H": h, "X": h + 10}}}) for h in (12, 20)]
        out += [(f"hold={h}", {"w2": {"p": {"X": 16 + h}}}) for h in (8, 12)]
        out += [("fixed_0.75_W1", {"w2": {"fixed": True}})]
        out += [("no_volscale", {"w2": {"p": {"sig_ref": None}}})]
    return out


def combine(a, b):
    """Merge two variant dicts (V1/V2 multiply, V3 replaces the base, V4 adds on top)."""
    out = json.loads(json.dumps(V[a]))
    for k, v in V[b].items():
        out[k] = v
    return out


def stage_repro():
    w1t = run_variant(V["W1"], TEST_START, TEST_END)
    w1d = run_variant(V["W1"], DEV_START, DEV_END)
    log("W1 test", brief(w1t)); log("W1 dev", brief(w1d))
    ref = json.loads((PROJECT / "research/monthly20/r4_07_ensemble/results.json").read_text())
    ok = all(abs(brief(w1t)[k] - ref["test_1x"][k]) < 0.011 for k in ("median_month", "geo_month", "mean_month", "max_dd_pct"))
    log("REPRODUCES W1:", ok)
    return ok


def stage_dev():
    assert stage_repro()
    res = {"W1_dev": brief(run_variant(V["W1"], DEV_START, DEV_END)), "variants": {}}
    base = res["W1_dev"]
    for name in ("V1", "V2", "V3", "V4"):
        r = run_variant(V[name], DEV_START, DEV_END)
        r2 = run_variant(V[name], DEV_START, DEV_END, cost_mult=2)
        b = brief(r)
        nb = []
        for nm, nv in neighbours(name):
            rr = run_variant(nv, DEV_START, DEV_END)
            nb.append(dict(name=nm, **brief(rr)))
        frac = sum(x["net_pct"] > 0 for x in nb) / len(nb)
        mean_w = np.round(r["wpath"][D.idx(DEV_START):D.idx(DEV_END)].sum(1).mean(), 3)
        q = dict(geo_up=b["geo_month"] > base["geo_month"], median_ok=b["median_month"] >= base["median_month"],
                 dd_ok=b["max_dd_pct"] <= base["max_dd_pct"] + 2, nb_ok=frac >= 0.7,
                 compounds=b["geo_month"] >= 0.5 * b["mean_month"])
        res["variants"][name] = dict(dev=b, dev_2xcost=brief(r2), mean_gross_weight_dev=float(mean_w),
                                     neighbours_dev=nb, nb_frac_positive=round(frac, 3), qual=q,
                                     qualifies=all(q.values()), geo_uplift=round(b["geo_month"] - base["geo_month"], 2))
        log(name, b, "nb+", round(frac, 2), q)
    qual = sorted([n for n, x in res["variants"].items() if x["qualifies"]], key=lambda n: -res["variants"][n]["geo_uplift"])
    res["qualifiers_ranked"] = qual
    res["primary"] = qual[0] if qual else None
    res["secondary"] = qual[:2] if len(qual) >= 2 else None
    log("QUALIFIERS", qual, "PRIMARY", res["primary"], "SECONDARY", res["secondary"])
    (HERE / "dev.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    st = sys.argv[1]
    if st == "repro":
        stage_repro()
    elif st == "dev":
        stage_dev()
    else:
        __import__("up_test").main()
