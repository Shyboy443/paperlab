"""Agent 01 - leveraged, volatility-targeted trend following.

    python run.py dev     # DEV sweep (2021-01..2024-10) -> dev_sweep.json (+ Monte Carlo of the chosen configs)
    python run.py test    # the pre-registered configs on TEST, baseline + doubled costs -> results.json

Engine and cost model: engine.py. All windows/costs/summary from ../common.py.
"""
from __future__ import annotations
import json, random, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import *  # noqa
ME = Path(__file__).resolve().parent

TV_GRID = [0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8]
KINDS = ["ema5_100", "ema20_100", "mom60", "mom20", "don20", "don55"]
FILTS = ["none", "adx25"]
_LOADED = {}


def load(symbols, interval):
    out = {}
    for s in symbols:
        k = (s, interval)
        if k not in _LOADED:
            try:
                _LOADED[k] = Sym(s, interval)
            except Exception as e:  # noqa
                print("skip", s, e, flush=True)
                _LOADED[k] = None
        if _LOADED[k] is not None and len(_LOADED[k].t) > 120:
            out[s] = _LOADED[k]
    return out


def pool_for(n, end=DEV_END):
    """Every symbol that is in the point-in-time monthly top-n at some month start in [2021-01, end)."""
    pool, t = set(), DEV_START
    while t < end:
        pool |= set(universe_at(n, t))
        d = datetime.fromtimestamp(t / 1000, timezone.utc)
        t = utc(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return sorted(pool)


def run_cfg(syms, cfg, start, end, cost_mult=1.0, keep_months=False):
    curve, trades, nliq = simulate(syms, cfg, start, end, cost_mult)
    s = summary(curve, trades)
    s.update(liquidations=nliq, wiped=curve[-1][1] < 1.0, avg_gross_lev=round(simulate.avg_gross, 2),
             max_gross_lev=round(simulate.max_gross, 2))
    tot = sum(trades)
    s["top1_share_of_profit"] = round(max(trades) / tot * 100, 1) if trades and tot > 0 else None
    if not keep_months:
        s = {k: v for k, v in s.items() if k != "months"}
    return s, curve


def sweep(syms, fam, rows, t0):
    for tv in TV_GRID:
        cfg = dict(fam, tv=tv)
        s, _ = run_cfg(syms, cfg, DEV_START, DEV_END)
        rows.append({"cfg": cfg, "dev": s})
        print(fam["label"], fam["kind"], fam["filt"], fam.get("pyr", 0), tv, "| mean", s["mean_month"], "geo",
              s["geo_month"], "dd", s["max_dd_pct"], "liq", s["liquidations"], "gross", s["avg_gross_lev"],
              f"{time.time() - t0:.0f}s", flush=True)
        if s["wiped"]:
            break


def dev_sweep(stage):
    out_path = ME / f"dev_sweep_{stage}.json"
    rows, t0 = [], time.time()
    if stage == "big10_1d":
        syms = load(BIG10, "1d")
        for kind in KINDS:
            for filt in FILTS:
                sweep(syms, dict(kind=kind, filt=filt, interval="1d", uni=None, slots=10, max_lev=50,
                                 label=stage), rows, t0)
    elif stage.startswith("top"):  # top30_1d / top50_1d: monthly point-in-time universe
        n = int(stage[3:5])
        pool = pool_for(n)
        syms = load(pool, "1d")
        print("pool", len(pool), "loaded", len(syms), flush=True)
        for kind in KINDS:
            for filt in FILTS:
                sweep(syms, dict(kind=kind, filt=filt, interval="1d", uni=n, slots=n, max_lev=50, label=stage,
                                 pool=None), rows, t0)
    elif stage == "big10_4h":
        syms = load(BIG10, "4h")
        for kind in ["ema30_600", "ema120_600", "mom360", "mom120", "don120", "don330"]:  # = the daily lengths x6
            for filt in FILTS:
                sweep(syms, dict(kind=kind, filt=filt, interval="4h", uni=None, slots=10, max_lev=50,
                                 label=stage), rows, t0)
    elif stage in ("big3_1h", "big3_4h"):
        iv = stage[-2:]
        syms = load(["BTCUSDT", "ETHUSDT", "SOLUSDT"], iv)
        kinds = (["ema120_2400", "mom1440", "don480", "ema20_100", "don48", "mom24"] if iv == "1h"
                 else ["ema30_600", "mom360", "don120", "ema5_25", "don12", "mom6"])
        for kind in kinds:
            for filt in FILTS:
                sweep(syms, dict(kind=kind, filt=filt, interval=iv, uni=None, slots=3, max_lev=50, label=stage,
                                 vol_n=720 if iv == "1h" else 180), rows, t0)
    elif stage == "pyramid":
        best = json.loads((ME / "dev_best_families.json").read_text())
        for fam in best:
            syms = load(pool_for(fam["uni"]) if isinstance(fam["uni"], int) else BIG10, fam["interval"])
            for pyr in (2, 4):
                f = dict(fam, pyr=pyr, pyr_step=1.0, label=fam["label"] + f"_pyr{pyr}")
                sweep(syms, f, rows, t0)
    out_path.write_text(json.dumps(rows, indent=1))


# ---------------------------------------------------------------- risk of ruin

def mc_monthly(months, n_months=24, n=20000, seed=1):
    """Bootstrap whole DEV months (with replacement) into 24-month paths."""
    rng = random.Random(seed)
    m = [x / 100 for x in months]
    wipe = dd50 = dd80 = lose = hit = 0
    finals = []
    for _ in range(n):
        eq, peak, mdd, path = 1.0, 1.0, 0.0, []
        for _ in range(n_months):
            r = rng.choice(m); path.append(r)
            eq *= 1 + r
            peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
            if eq <= 0.01:
                break
        wipe += eq <= 0.01; dd50 += mdd >= 0.5; dd80 += mdd >= 0.8; lose += eq < 1
        hit += statistics.mean(path) >= 0.20 and eq > 0.01
        finals.append(eq)
    finals.sort()
    return {"p_wipe_99pct": round(wipe / n, 3), "p_dd_50": round(dd50 / n, 3), "p_dd_80": round(dd80 / n, 3),
            "p_end_below_start": round(lose / n, 3), "p_mean_month_ge_20": round(hit / n, 3),
            "median_final_multiple": round(finals[n // 2], 3), "p10_final": round(finals[n // 10], 3),
            "p90_final": round(finals[9 * n // 10], 3)}


def mc_scaled(curve, L, days=730, block=30, n=5000, seed=2):
    """Block-bootstrap the DEV daily returns of the base config, scale them by L (daily re-levered),
    wipe if one day loses >= 100%. Answers: what does L times the base strategy do over 2 years?"""
    rng = random.Random(seed)
    daily, last_day, last_eq = [], None, None
    for t, e in curve:
        d = t // DAY
        if d != last_day:
            if last_eq is not None:
                daily.append(e / last_eq - 1)
            last_day, last_eq = d, e
    wipe = 0; finals = []; means = []
    for _ in range(n):
        eq, peak, monthly, meq = 1.0, 1.0, [], 1.0
        k = 0
        while k < days:
            i = rng.randrange(0, len(daily) - block)
            for r in daily[i:i + block]:
                eq *= max(0.0, 1 + L * r); k += 1
                if k % 30 == 0:
                    monthly.append(eq / meq - 1 if meq > 0 else -1); meq = eq
                if eq <= 0 or k >= days:
                    break
            if eq <= 0:
                break
        wipe += eq <= 0.01; finals.append(eq)
        means.append(statistics.mean(monthly) if monthly and eq > 0.01 else -1)
    finals.sort()
    return {"L": L, "p_wipe": round(wipe / n, 3), "median_final": round(finals[n // 2], 3),
            "p_mean_month_ge_20_and_alive": round(sum(m >= 0.2 for m in means) / n, 3),
            "p_end_below_start": round(sum(f < 1 for f in finals) / n, 3)}


PREREG = [
    ("A_target20", dict(kind="don120", filt="adx25", interval="4h", uni=None, slots=10, max_lev=50, tv=4.0)),
    ("B_growth_optimal", dict(kind="don120", filt="adx25", interval="4h", uni=None, slots=10, max_lev=50, tv=3.0)),
    ("C_moderate", dict(kind="don120", filt="adx25", interval="4h", uni=None, slots=10, max_lev=50, tv=1.0)),
    ("D_ema_reference", dict(kind="ema5_100", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=3.0)),
    ("E_broad_top50", dict(kind="mom60", filt="none", interval="1d", uni=50, slots=50, max_lev=50, tv=2.0)),
]


def test_run():
    out = {"agent": "01_leverage_trend", "idea": "leveraged, volatility-targeted trend following on Binance perps "
           "(Donchian/EMA/momentum, ADX filter, per-lot isolated margin with liquidation, optional broad universe)",
           "attempts": [], "monte_carlo": {}}
    for name, cfg in PREREG:
        syms = load(pool_for(cfg["uni"], TEST_END) if cfg["uni"] else BIG10, cfg["interval"])
        dev, dcurve = run_cfg(syms, cfg, DEV_START, DEV_END, keep_months=True)
        test, _ = run_cfg(syms, cfg, TEST_START, TEST_END, keep_months=True)
        test2, _ = run_cfg(syms, cfg, TEST_START, TEST_END, cost_mult=2.0, keep_months=True)
        out["attempts"].append({"name": name, "prereg": True, "cfg": cfg, "dev": dev, "test": test,
                                "test_double_cost": test2})
        out["monte_carlo"][name] = {"dev_monthly_bootstrap_24m": mc_monthly(list(dev["months"].values()))}
        if name == "C_moderate":  # scale the tv=1 DEV daily path by L to map leverage -> ruin
            out["monte_carlo"]["scaled_from_C"] = [mc_scaled(dcurve, L) for L in (1, 2, 3, 4, 5, 6)]
        print(name, "TEST mean", test["mean_month"], "median", test["median_month"], "worst", test["worst_month"],
              "dd", test["max_dd_pct"], "x2 mean", test2["mean_month"], flush=True)
    a = out["attempts"][0]["test"]; a2 = out["attempts"][0]["test_double_cost"]
    def ok(t, t2):
        return (t["mean_month"] >= 20 and t2["mean_month"] > 0 and not t["wiped"]
                and (t["top1_share_of_profit"] or 999) <= 50)
    passes = [x["name"] for x in out["attempts"] if ok(x["test"], x["test_double_cost"])]
    out["verdict"] = "PASS" if passes else "FAIL"  # overridden with a note by sensitivity(), see REPORT.md
    out["passing_attempts"] = passes
    out["best_honest"] = max(out["attempts"], key=lambda x: x["test"]["geo_month"])["name"]
    out["leverage_needed_for_20pct"] = 4.0
    out["leverage_note"] = ("tv=4.0 annual vol target per position on A = about 5-6x per open position, "
                            "average gross 1.9x, peaks ~10x; it reaches a 20% DEV MEAN month only through a few "
                            "huge months (DEV median month -3.6%, max DD 97%)")
    (ME / "results.json").write_text(json.dumps(out, indent=1))


SENS = [  # run AFTER seeing TEST: NOT pre-registered, robustness of D only
    ("S_D_tv1", dict(kind="ema5_100", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=1.0)),
    ("S_D_tv2", dict(kind="ema5_100", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=2.0)),
    ("S_D_tv4", dict(kind="ema5_100", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=4.0)),
    ("S_D_nofilter_tv2", dict(kind="ema5_100", filt="none", interval="1d", uni=None, slots=10, max_lev=50, tv=2.0)),
    ("S_ema20_100_adx_tv3", dict(kind="ema20_100", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=3.0)),
    ("S_don55_adx_tv3", dict(kind="don55", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=3.0)),
    ("S_mom60_adx_tv3", dict(kind="mom60", filt="adx25", interval="1d", uni=None, slots=10, max_lev=50, tv=3.0)),
    ("S_D_on_top50_tv3", dict(kind="ema5_100", filt="adx25", interval="1d", uni=50, slots=50, max_lev=50, tv=3.0)),
    ("S_D_pit_top10_tv3", dict(kind="ema5_100", filt="adx25", interval="1d", uni=10, slots=10, max_lev=50, tv=3.0)),
    ("S_D_pit_top15_tv3", dict(kind="ema5_100", filt="adx25", interval="1d", uni=15, slots=15, max_lev=50, tv=3.0)),
    ("S_D_pit_top20_tv3", dict(kind="ema5_100", filt="adx25", interval="1d", uni=20, slots=20, max_lev=50, tv=3.0)),
    ("S_D_4h_ema30_600_tv3", dict(kind="ema30_600", filt="adx25", interval="4h", uni=None, slots=10, max_lev=50, tv=3.0)),
]


def sensitivity():
    out = json.loads((ME / "results.json").read_text())
    out["attempts"] = [a for a in out["attempts"] if a["prereg"]]
    for name, cfg in SENS:
        syms = load(pool_for(cfg["uni"], TEST_END) if cfg["uni"] else BIG10, cfg["interval"])
        dev, _ = run_cfg(syms, cfg, DEV_START, DEV_END, keep_months=True)
        test, _ = run_cfg(syms, cfg, TEST_START, TEST_END, keep_months=True)
        test2, _ = run_cfg(syms, cfg, TEST_START, TEST_END, cost_mult=2.0, keep_months=True)
        m = test["months"]
        ex = [v for k, v in m.items() if k != "2024-11"]
        test["mean_month_ex_2024_11"] = round(sum(ex) / len(ex), 2)
        out["attempts"].append({"name": name, "prereg": False, "cfg": cfg, "dev": dev, "test": test,
                                "test_double_cost": test2})
        print(name, "DEV mean", dev["mean_month"], "geo", dev["geo_month"], "| TEST mean", test["mean_month"],
              "ex-Nov24", test["mean_month_ex_2024_11"], "median", test["median_month"], "geo", test["geo_month"],
              "dd", test["max_dd_pct"], "x2", test2["mean_month"], "top1", test["top1_share_of_profit"], flush=True)
    out["monte_carlo"]["S_D_tv4"] = {"dev_monthly_bootstrap_24m": mc_monthly(list(
        next(a for a in out["attempts"] if a["name"] == "S_D_tv4")["dev"]["months"].values()))}
    out["verdict"] = "FAIL"
    out["verdict_note"] = ("Primary A (the DEV 20%/month leverage) made +2.6% mean, -18.6% median month, -85% net, 97% DD on "
                           "TEST. D (pre-registered, EMA5/100+ADX on the fixed BIG10 list, tv 3) met the numeric bar "
                           "(+23.4% mean) but the fixed BIG10 list is today's survivors (only 4 of 10 were point-in-time "
                           "top-10 by volume on 2024-11-01), breaking PROTOCOL's no-look-ahead universe rule; the same "
                           "rule on a point-in-time top-10/15/20 universe made +8.2/+10.0/+8.5% mean on TEST with a "
                           "negative DEV geometric month. Half of D's mean is one month (Nov 2024, +277%).")
    d = next(a for a in out["attempts"] if a["name"] == "D_ema_reference")
    m = d["test"]["months"]; ex = [v for k, v in m.items() if k != "2024-11"]
    d["test"]["mean_month_ex_2024_11"] = round(sum(ex) / len(ex), 2)
    (ME / "results.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "sens":
        sensitivity()
    if sys.argv[1] == "test":
        test_run()
    if sys.argv[1] == "dev":
        dev_sweep(sys.argv[2])
