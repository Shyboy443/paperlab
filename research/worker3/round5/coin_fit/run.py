"""coin_fit main run.  python run.py dev   -> DEV-only work (selection, DEV split-half preview, DEV mix choice)
                       python run.py oos   -> the ONE TEST + HOLDOUT run of everything pre-registered in PREREG.md
Writes dev.json / results.json."""
import json, sys, time, math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from lib import DEV, TEST, HOLD, RULES, utc

MODE = sys.argv[1]
HERE = Path(__file__).resolve().parent
RES = Path(r"D:/Projects/BinanceBOt/paperlab/research")
t0 = time.time()
NR = 2000
D = lib.load(20); t = D["t"]
R, A, P, pairs = lib.pair_returns(D)
R2, _, _, _ = lib.pair_returns(D, cost_mult=2)
NP = len(pairs); coins = D["syms"]; NC = len(coins); NRu = len(RULES)
cidx = np.array([coins.index(p[0]) for p in pairs]); ridx = np.array([RULES.index(p[1]) for p in pairs])
SEL_THR = 0.5
W, choice = lib.select_static(R, A, t, pairs, DEV, thr=SEL_THR)
K = int(W.sum())
WINS = {"DEV": DEV} if MODE == "dev" else {"DEV": DEV, "TEST": TEST, "HOLDOUT": HOLD}


def daily(Wv, cm=1):
    return lib.book(R if cm == 1 else R2, A, Wv, P, cost_mult=cm)


def wstats(Wv, win, cm=1):
    rb = daily(Wv, cm); tt, x = lib.window(t, rb, win)
    mo = lib.monthly(tt, x)
    return lib.mstats(mo, x, lib.count_trades(P, Wv, t, win) if Wv.ndim == 1 else None), mo


def onehot(pairsel):
    w = np.zeros(NP); w[pairsel] = 1; return w


def pair_index(ci, ri):
    return ci * NRu + ri


# ---- comparison books ----
m_dev = (t >= DEV[0]) & (t < DEV[1])
rule_all_sh = {r: lib.sharpe(lib.window(t, daily((ridx == k).astype(float)), DEV)[1]) for k, r in enumerate(RULES)}
best_rule = max(rule_all_sh, key=rule_all_sh.get)
chosen_coins = sorted(set(cidx[W > 0]))
BOOKS = {
    "CHOSEN": W,
    "ALL_PAIRS": np.ones(NP),
    f"BEST_RULE_{best_rule}_ALL_COINS": (ridx == RULES.index(best_rule)).astype(float),
    f"BEST_RULE_{best_rule}_CHOSEN_COINS": ((ridx == RULES.index(best_rule)) & np.isin(cidx, chosen_coins)).astype(float),
}
for k, r in enumerate(RULES):
    BOOKS[f"RULE_{r}_ALL_COINS"] = (ridx == k).astype(float)


def rand_draws(win, n=NR, seed=11, mode="random"):
    """random K-pair books: 'random' = K random coins, a random rule each; 'perm' = chosen coins keep, chosen rules shuffled."""
    g = np.random.default_rng(seed)
    tt = (t >= win[0]) & (t < win[1])
    Rw, Aw, Pw = R[tt], A[tt], P[tt]
    sel_c = cidx[W > 0]; sel_r = ridx[W > 0]
    Ws = np.zeros((n, NP))
    for d in range(n):
        if mode == "random":
            cc = g.choice(NC, K, replace=False); rr = g.integers(0, NRu, K)
        else:
            cc = sel_c; rr = g.permutation(sel_r)
        Ws[d, pair_index(cc, rr)] = 1
    out = []
    for d in range(n):
        rb = lib.book(Rw, Aw, Ws[d], Pw)
        e = np.cumprod(1 + rb)
        out.append(((e[-1] - 1) * 100, lib.sharpe(rb)))
    return np.array(out)


def pctl(arr, v):
    return round(float((arr < v).mean() * 100), 1)


# ---- walk-forward: each quarter, per coin choose rule from trailing 365 d Sharpe (> thr), equal weight ----
def wf_weights(thr=SEL_THR, random_seed=None):
    q_starts = [utc(y, mm, 1) for y in range(2018, 2027) for mm in (1, 4, 7, 10)]
    Wt = np.zeros((len(t), NP)); g = np.random.default_rng(random_seed) if random_seed is not None else None
    log = {}
    for qs in q_starts:
        qe = utc(*(lambda d: (d.year + (d.month == 10), (d.month + 3 - 1) % 12 + 1, 1))(pd.Timestamp(qs, unit="ms")))
        rows = (t >= qs) & (t < qe)
        if not rows.any():
            continue
        tr = (t >= qs - 365 * lib.DAY) & (t < qs)
        Wq = np.zeros(NP)
        for ci in range(NC):
            idx = np.where(cidx == ci)[0]
            sc = np.array([lib.sharpe(R[tr, i][A[tr, i]]) if A[tr, i].mean() >= 0.5 else np.nan for i in idx])
            if np.all(np.isnan(sc)):
                continue
            b = int(np.nanargmax(sc))
            if sc[b] > thr:
                Wq[idx[b]] = 1
        if g is not None:   # same count, random coins and rules among coins with data
            k = int(Wq.sum()); Wq = np.zeros(NP)
            okc = [ci for ci in range(NC) if A[rows][:, cidx == ci].any()]
            if k and okc:
                cc = g.choice(okc, min(k, len(okc)), replace=False); rr = g.integers(0, NRu, len(cc))
                Wq[pair_index(cc, rr)] = 1
        Wt[rows] = Wq
        log[pd.Timestamp(qs, unit="ms").strftime("%Y-Q%q") if False else pd.Timestamp(qs, unit="ms").strftime("%Y-%m")] = \
            [f"{pairs[i][0][:-4]}:{pairs[i][1]}" for i in np.where(Wq > 0)[0]]
    return Wt, log


# ---- personality ----
def personality(win, min_days=300):
    tt = (t >= win[0]) & (t < win[1]); out = {}
    for ci, coin in enumerate(coins):
        c = D["c"][tt, ci]; r = np.diff(np.log(c)); r = r[~np.isnan(r)]
        if len(r) < min_days:
            continue
        ac1 = float(np.corrcoef(r[:-1], r[1:])[0, 1])
        lr = np.log(c[~np.isnan(c)])
        vr10 = float(np.var(lr[10:] - lr[:-10]) / (10 * np.var(np.diff(lr))))     # Lo & MacKinlay (1988)
        ks = [1, 2, 4, 8, 16, 32]
        sd = [np.std(lr[k:] - lr[:-k]) for k in ks]
        hurst = float(np.polyfit(np.log(ks), np.log(sd), 1)[0])                     # aggregated-variance Hurst
        sh = [lib.sharpe(R[tt, pair_index(ci, k)][A[tt, pair_index(ci, k)]]) for k in range(NRu)]
        tm = float(np.nanmean(sh[:4]) - np.nanmean(sh[4:]))
        out[coin] = dict(ac1=round(ac1, 4), vr10=round(vr10, 3), hurst=round(hurst, 3), trend_minus_mr=round(tm, 3),
                         sharpes=[round(x, 2) for x in sh])
    return out


def persist(pa, pb):
    cs = [c for c in pa if c in pb]; res = {"n_coins": len(cs)}
    for k in ("ac1", "vr10", "hurst", "trend_minus_mr"):
        rho, p = spearmanr([pa[c][k] for c in cs], [pb[c][k] for c in cs])
        res[k] = dict(rho=round(float(rho), 3), p=round(float(p), 3))
    # pair-level: raw Sharpe and the coin x rule interaction residual
    a = np.array([pa[c]["sharpes"] for c in cs]); b = np.array([pb[c]["sharpes"] for c in cs])
    res["pair_sharpe_rho"] = round(float(spearmanr(a.ravel(), b.ravel(), nan_policy="omit")[0]), 3)
    inter = lambda x: x - np.nanmean(x, 1, keepdims=True) - np.nanmean(x, 0, keepdims=True) + np.nanmean(x)
    rho, p = spearmanr(inter(a).ravel(), inter(b).ravel(), nan_policy="omit")
    res["interaction_rho"] = dict(rho=round(float(rho), 3), p=round(float(p), 3))
    # rank (1 = best of 6) of the earlier-best rule in the later window
    rk = [int((b[i] > b[i][np.nanargmax(a[i])]).sum()) + 1 for i in range(len(cs)) if not np.all(np.isnan(a[i]))]
    res["later_rank_of_earlier_best_rule"] = dict(mean=round(float(np.mean(rk)), 2), chance=3.5, ranks=rk)
    return res


out = dict(universe=coins, rules=RULES, selection=choice, K=K, best_rule_dev=best_rule,
           rule_all_coin_dev_sharpe={k: round(v, 3) for k, v in rule_all_sh.items()})

if MODE == "dev":
    # DEV split-half preview: choose on 2021-01..2022-12, evaluate 2023-01..2024-10 (still all DEV)
    A1, A2 = (utc(2021, 1, 1), utc(2023, 1, 1)), (utc(2023, 1, 1), DEV[1])
    Wh, ch = lib.select_static(R, A, t, pairs, A1, thr=SEL_THR)
    kh = int(Wh.sum())
    s_sel = wstats(Wh, A2)[0]; s_all = wstats(np.ones(NP), A2)[0]
    g = np.random.default_rng(3); tt = (t >= A2[0]) & (t < A2[1]); rn = []
    for d in range(NR):
        w = np.zeros(NP); w[pair_index(g.choice(NC, kh, replace=False), g.integers(0, NRu, kh))] = 1
        rn.append(np.prod(1 + lib.book(R[tt], A[tt], w, P[tt])) * 100 - 100)
    rn = np.array(rn)
    sc, sr = cidx[Wh > 0], ridx[Wh > 0]; pm = []
    for d in range(NR):
        w = np.zeros(NP); w[pair_index(sc, g.permutation(sr))] = 1
        pm.append(np.prod(1 + lib.book(R[tt], A[tt], w, P[tt])) * 100 - 100)
    pm = np.array(pm)
    out["dev_split_preview"] = dict(choice_2021_22=ch, K=kh, eval_2023_24_selected=s_sel, eval_all_pairs=s_all,
                                    random_pctl=pctl(rn, s_sel["net"]), random_median=round(float(np.median(rn)), 2),
                                    perm_pctl=pctl(pm, s_sel["net"]), perm_median=round(float(np.median(pm)), 2),
                                    personality_persistence=persist(personality(A1), personality(A2)))
    print(json.dumps(out["dev_split_preview"], indent=1, default=str))
    out["dev_books"] = {k: wstats(v, DEV)[0] for k, v in BOOKS.items()}
    for k, v in out["dev_books"].items():
        print(k.ljust(40), v)
    Wt, log = wf_weights()
    out["dev_wf_2022_2024"] = wstats(Wt, (utc(2022, 1, 1), DEV[1]))[0]
    out["dev_wf_all_pairs_2022_2024"] = wstats(np.ones(NP), (utc(2022, 1, 1), DEV[1]))[0]
    print("WF DEV 2022-01..2024-10", out["dev_wf_2022_2024"], "\nALL", out["dev_wf_all_pairs_2022_2024"])
    # DEV portfolio mix choice
    W1 = json.loads((RES / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
    W2 = json.loads((RES / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
    X = wstats(W, DEV)[1]
    ks = [k for k in X if k in W1]
    mix = {}
    for wx in np.round(np.arange(0, 0.61, 0.1), 1):
        v = [(1 - wx) / 2 * (W1[k] + W2[k]) + wx * X[k] for k in ks]
        mix[str(wx)] = dict(sharpe_m=round(float(np.mean(v) / np.std(v, ddof=1)), 3), **lib.mstats(dict(zip(ks, v))))
    out["dev_mix"] = mix
    for k, v in mix.items():
        print("mix X=", k, v)
    out["dev_personality"] = personality(DEV)
    (HERE / "dev.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"done [{time.time()-t0:.0f}s]")
    sys.exit()

# ======================= OOS (run once, after PREREG.md) =======================
PRE = json.loads((HERE / "dev.json").read_text())
WX = float(PRE["prereg_mix_wx"])
res = dict(out, books={}, random={}, wf={}, checks={}, portfolio={})
CM = {}
for k, v in BOOKS.items():
    res["books"][k] = {}
    for wn, win in WINS.items():
        s1, mo = wstats(v, win); s2 = wstats(v, win, 2)[0]
        res["books"][k][wn] = dict(x1=s1, x2=s2)
        if k == "CHOSEN":
            CM[wn] = mo
    print(k.ljust(40), {wn: (res["books"][k][wn]["x1"]["geo"], res["books"][k][wn]["x1"]["net"]) for wn in WINS}, flush=True)
for wn, win in WINS.items():
    chosen = res["books"]["CHOSEN"][wn]["x1"]
    rn = rand_draws(win, seed=101); pm = rand_draws(win, seed=202, mode="perm")
    res["random"][wn] = dict(random_net_pctl=pctl(rn[:, 0], chosen["net"]), random_sharpe_pctl=pctl(rn[:, 1], chosen["sharpe_ann"]),
                             random_net_median=round(float(np.median(rn[:, 0])), 2), random_net_p95=round(float(np.percentile(rn[:, 0], 95)), 2),
                             perm_net_pctl=pctl(pm[:, 0], chosen["net"]), perm_sharpe_pctl=pctl(pm[:, 1], chosen["sharpe_ann"]),
                             perm_net_median=round(float(np.median(pm[:, 0])), 2), perm_net_p95=round(float(np.percentile(pm[:, 0], 95)), 2),
                             chosen_net=chosen["net"], n_draws=NR)
    print(wn, "random/perm", res["random"][wn], flush=True)

# walk-forward
Wt, log = wf_weights()
WFW = {"WF_2022_2024_DEV": (utc(2022, 1, 1), DEV[1]), "WF_TEST": TEST, "WF_HOLDOUT_2019_2020": (utc(2019, 1, 1), HOLD[1])}
rb_wf = lib.book(R, A, Wt, P)
for wn, win in WFW.items():
    tt, x = lib.window(t, rb_wf, win)
    s = lib.mstats(lib.monthly(tt, x), x)
    s_all = wstats(np.ones(NP), win)[0]
    rr = []
    for sd in range(200):
        Wr, _ = wf_weights(random_seed=sd)
        tt2, x2 = lib.window(t, lib.book(R, A, Wr, P), win)
        rr.append(np.prod(1 + x2) * 100 - 100)
    rr = np.array(rr)
    res["wf"][wn] = dict(wf=s, all_pairs=s_all, random_wf_pctl=pctl(rr, s["net"]), random_wf_median=round(float(np.median(rr)), 2),
                         random_wf_draws=200)
    print(wn, res["wf"][wn], flush=True)
res["wf_log"] = log

# personality persistence
pdv, pts, pho = personality(DEV), personality(TEST), personality(HOLD)
res["personality"] = dict(DEV=pdv, TEST=pts, HOLDOUT=pho, DEV_vs_TEST=persist(pdv, pts), DEV_vs_HOLDOUT=persist(pdv, pho))
print("persist DEV->TEST", res["personality"]["DEV_vs_TEST"]); print("persist DEV->HOLD", res["personality"]["DEV_vs_HOLDOUT"])


# ---- 8 checks per window for CHOSEN ----
def contrib(win):
    tt = (t >= win[0]) & (t < win[1]); Rw, Aw, Pw = R[tt], A[tt], P[tt]
    M = Aw & (W > 0); n = M.sum(1); w = np.where(M, 1 / np.maximum(n, 1)[:, None], 0)
    rb = lib.book(Rw, Aw, W, Pw); eq = np.r_[1, np.cumprod(1 + rb)][:-1]
    C = eq[:, None] * w * Rw
    coinp = np.zeros(NC); positions = []
    for i in np.where(W > 0)[0]:
        coinp[cidx[i]] += C[:, i].sum()
        seg = 0.0; prev = 0
        for d in range(len(rb)):
            p = Pw[d, i]
            if p != prev and prev != 0:
                positions.append((pairs[i][0], seg)); seg = 0.0
            seg += C[d, i]; prev = p
        if prev != 0:
            positions.append((pairs[i][0], seg))
    return coinp, positions, np.prod(1 + rb) - 1


def years_check(mo, wn):
    yrs = sorted(set(k[:4] for k in mo))
    yv = {y: round(lib.compound([v for k, v in mo.items() if k[:4] == y]), 2) for y in yrs}
    ex_first = round(lib.compound([v for k, v in mo.items() if k[:4] != yrs[0]]), 2)
    return dict(pass_=sum(v > 0 for v in yv.values()) >= math.ceil(len(yv) / 2 + 0.01) and ex_first > 0,
                numbers=dict(years=yv, net_ex_first_year=ex_first))


NEIGH = [("scale", s) for s in (0.5, 0.75, 1.25, 1.5)] + [("thr", x) for x in (0.0, 0.25, 0.75, 1.0)] + \
        [("metric", m) for m in ("net", "calmar")] + [("univ", n) for n in (10, 15, 25, 30)]


def neighbour(name, v):
    if name == "univ":
        Dn = lib.load(v); Rn, An, Pn, pn = lib.pair_returns(Dn); tn = Dn["t"]
    else:
        Dn, tn = D, t
        Rn, An, Pn, pn = (lib.pair_returns(D, scale=v) if name == "scale" else (R, A, P, pairs))
    kw = dict(thr=v) if name == "thr" else (dict(metric=v, thr=1.0 if v == "calmar" else 0.0) if name == "metric" else dict(thr=SEL_THR))
    Wn, _ = lib.select_static(Rn, An, tn, pn, DEV, **kw)
    rb = lib.book(Rn, An, Wn, Pn)
    o = {}
    for wn, win in WINS.items():
        tt, x = lib.window(tn, rb, win); o[wn] = round(float(np.prod(1 + x) * 100 - 100), 2)
    return dict(param=name, value=v, K=int(Wn.sum()), net=o)


nb = [neighbour(n, v) for n, v in NEIGH]
res["neighbours"] = nb
for x in nb:
    print("nb", x, flush=True)

for wn, win in (("TEST", TEST), ("HOLDOUT", HOLD)):
    b = res["books"]["CHOSEN"][wn]; mo = CM[wn]; vals = np.array(list(mo.values()))
    c = {}
    c["1"] = dict(pass_=b["x1"]["net"] > 0 and b["x2"]["net"] > 0, numbers=dict(net_1x=b["x1"]["net"], net_2x=b["x2"]["net"]))
    c["2"] = years_check(mo, wn)
    ts, p5 = lib.tstat(vals), lib.boot_p5(vals)
    c["3"] = dict(pass_=ts >= 2 and p5 > 0, numbers=dict(t=round(ts, 2), boot_p5=round(p5, 3)))
    best3 = sorted(mo, key=mo.get, reverse=True)[:3]
    rest = lib.compound([v for k, v in mo.items() if k not in best3])
    c["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: round(mo[k], 2) for k in best3}, net_without=round(rest, 2)))
    fr = sum(x["net"][wn] > 0 for x in nb) / len(nb)
    c["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac_positive=round(fr, 3), n=len(nb)))
    rd = res["random"][wn]
    c["6"] = dict(pass_=rd["random_net_pctl"] >= 95, numbers=dict(beats_random_pct=rd["random_net_pctl"],
                                                                  beats_perm_pct=rd["perm_net_pctl"]))
    coinp, pos, net = contrib(win)
    top5 = np.argsort(-coinp)[:5]
    Wx = W * ~np.isin(cidx, top5)
    r7 = wstats(Wx, win)[0]["net"]
    mx = max(p[1] for p in pos) / net * 100 if net > 0 else float("nan")
    c["7"] = dict(pass_=bool(r7 > 0 and net > 0 and mx <= 10), numbers=dict(
        top5_coins={coins[j]: round(float(coinp[j] / net * 100), 1) if net > 0 else round(float(coinp[j] * 100), 2) for j in top5},
        net_without_top5=r7, largest_position_share_pct=round(mx, 2) if net > 0 else None))
    c["8"] = dict(pass_=b["x1"]["geo"] > 0 and b["x1"]["geo"] >= 0.5 * b["x1"]["mean"], numbers=dict(geo=b["x1"]["geo"], mean=b["x1"]["mean"]))
    for x in c.values():
        x["pass"] = bool(x.pop("pass_"))
    res["checks"][wn] = dict(checks=c, n_pass=sum(x["pass"] for x in c.values()))
    print(wn, "checks", res["checks"][wn]["n_pass"], {k: x["pass"] for k, x in c.items()}, flush=True)

# ---- portfolio ----
W1p = json.loads((RES / "monthly20/r4_07_ensemble/results.json").read_text())["monthly_1x"]
W2p = json.loads((RES / "worker2/session/results.json").read_text())["primary"]["monthly_1x"]
W1h = json.loads((RES / "worker1_backfill/results.json").read_text())["backfill_1x"]["monthly"]
W2h = json.loads((RES / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]["1x"]["monthly"]
for wn, (A_, B_) in {"DEV": (W1p, W2p), "TEST": (W1p, W2p), "HOLDOUT": (W1h, W2h)}.items():
    X = CM[wn]; ks = [k for k in X if k in A_ and k in B_]
    d = dict(months=f"{ks[0]}..{ks[-1]}", W1=lib.mstats({k: A_[k] for k in ks}), W2=lib.mstats({k: B_[k] for k in ks}),
             X=lib.mstats({k: X[k] for k in ks}),
             W1W2_5050=lib.mstats({k: (A_[k] + B_[k]) / 2 for k in ks}),
             W1W2X_thirds=lib.mstats({k: (A_[k] + B_[k] + X[k]) / 3 for k in ks}),
             W1W2X_devmix=dict(wx=WX, **lib.mstats({k: (1 - WX) / 2 * (A_[k] + B_[k]) + WX * X[k] for k in ks})),
             corr=dict(W1=round(float(np.corrcoef([A_[k] for k in ks], [X[k] for k in ks])[0, 1]), 3),
                       W2=round(float(np.corrcoef([B_[k] for k in ks], [X[k] for k in ks])[0, 1]), 3)))
    res["portfolio"][wn] = d
    print(wn, json.dumps(d), flush=True)
res["monthly_chosen"] = CM
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
print(f"done [{time.time()-t0:.0f}s]")
