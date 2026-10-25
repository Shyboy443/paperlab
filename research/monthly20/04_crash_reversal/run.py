"""Agent 04 - liquidation cascades and capitulation reversals.

    python run.py fetch   # public data.binance.vision archive -> data/monthly20_04_crash_reversal (incl. delisted coins)
    python run.py dev     # DEV grid (2021-01..2024-10) -> dev_grid.json; nothing here looks at TEST
    python run.py test    # the PRE-REGISTERED attempts (PREREG.md) on TEST + doubled costs -> results.json
"""
import json, sys, time, itertools
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import *  # noqa
from common import summary  # noqa

OUT = HERE


def signal(P, F, fam, prm):
    inu = P.inu
    if fam == "flush_long":     # single-coin capitulation: big negative z move on a volume spike, sellers dominant
        f = F[prm["k"]]
        s = inu & (f["z"] <= -prm["z"]) & (f["vspike"] >= prm["v"])
        if prm.get("tbs") is not None:
            s &= f["tbs"] <= prm["tbs"]
        return s, +1, f["z"]
    if fam == "squeeze_short":  # single-coin squeeze: big positive z move on a volume spike, buyers dominant
        f = F[prm["k"]]
        s = inu & (f["z"] >= prm["z"]) & (f["vspike"] >= prm["v"])
        if prm.get("tbs") is not None:
            s &= f["tbs"] >= prm["tbs"]
        return s, -1, -f["z"]
    if fam == "breadth_crash":  # market-wide cascade: a large share of the universe down hard -> buy the worst hit
        f = F[prm["k"]]
        with np.errstate(all="ignore"):
            down = np.where(inu, f["rk"] <= np.log(1 - prm["drop"]), False).sum(1)
            share = down / np.maximum(inu.sum(1), 1)
        crash = share >= prm["share"]
        # only the first bar of a crash episode (no new episode within 24 h)
        first = crash & ~np.concatenate([[False] * 24, [crash[i - 24:i].any() for i in range(24, len(crash))]])
        s = inu & first[:, None] & np.isfinite(f["rk"])
        return s, +1, f["rk"]
    raise ValueError(fam)


def variants():
    out = []
    for k, z, v, tbs, hold, ent in itertools.product((4, 12, 24), (3.0, 4.0), (3.0,), (None, 0.45), (12, 24, 72),
                                                      ("market", "limit")):
        out.append(("flush_long", dict(k=k, z=z, v=v, tbs=tbs), dict(hold=hold, entry=ent)))
    for k, z, tbs, hold in itertools.product((4, 24), (3.0, 4.0), (None, 0.55), (12, 24, 72)):
        out.append(("squeeze_short", dict(k=k, z=z, v=3.0, tbs=tbs), dict(hold=hold, entry="market")))
    for k, drop, share, hold, top in itertools.product((4, 24), (0.05, 0.10), (0.3, 0.5), (24, 72), (5, 10)):
        out.append(("breadth_crash", dict(k=k, drop=drop, share=share), dict(hold=hold, entry="market", top=top)))
        out.append(("breadth_crash", dict(k=k, drop=drop, share=share),
                    dict(hold=hold, entry="limit", lim_off=0.02, lim_wait=6, top=top)))
    return out


def name_of(fam, prm, ex):
    return fam + "|" + ",".join(f"{a}={b}" for a, b in {**prm, **ex}.items())


def make_trades(P, F, fam, prm, ex):
    sig, side, rank = signal(P, F, fam, prm)
    kw = dict(hold=ex["hold"], entry=ex["entry"], lim_off=ex.get("lim_off", 0.01), lim_wait=ex.get("lim_wait", 3),
              tp=ex.get("tp"), sl=ex.get("sl"), cool=24)
    if fam == "breadth_crash":
        kw.update(max_per_bar=ex["top"], rank=rank, cool=0)
    return gen_trades(P, sig, side, **kw)


def run_one(P, trades, start, end, lev=1.0, frac=0.1, max_pos=10, cost=1.0):
    curve, pnls, nliq = simulate(P, trades, start, end, lev=lev, frac=frac, max_pos=max_pos, cost_mult=cost)
    s = summary(curve, pnls)
    s["liquidations"] = nliq
    if pnls and sum(pnls) > 0:
        s["max_trade_share_of_profit"] = round(max(pnls) / sum(pnls) * 100, 1)
    return s


def load_all():
    t0 = time.time()
    P = Panel(60)
    data04.fetch_all("funding", P.syms, workers=48)
    B = base_features(P)
    F = {k: features(P, B, k) for k in (4, 12, 24)}
    print(f"panel {P.C.shape} in {time.time() - t0:.0f}s", flush=True)
    return P, F


def dev():
    P, F = load_all()
    rows = []
    for fam, prm, ex in variants():
        tr = make_trades(P, F, fam, prm, ex)
        s = run_one(P, tr, DEV_START, DEV_END)
        trd = [t for t in tr if DEV_START <= P.t[t["e_i"]] < DEV_END]
        avg = np.mean([t["side"] * (t["x_px"] / t["px"] - 1) for t in trd]) * 100 if trd else 0
        row = {"name": name_of(fam, prm, ex), "fam": fam, "prm": prm, "ex": ex, "avg_trade_gross_pct": round(avg, 3),
               **{k: s.get(k) for k in ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct",
                                         "net_pct", "trades", "win_rate", "months_up")}}
        rows.append(row)
        print(row["name"], row["trades"], row["avg_trade_gross_pct"], row["mean_month"], row["geo_month"],
              row["max_dd_pct"], flush=True)
    (OUT / "dev_grid.json").write_text(json.dumps(rows, indent=1, default=lambda o: o.item()))


CAND = {
    "BREADTH_LIMIT": ("breadth_crash", dict(k=4, drop=0.05, share=0.3),
                      dict(hold=24, entry="limit", lim_off=0.02, lim_wait=6, top=10)),
    "FLUSH_LIMIT": ("flush_long", dict(k=4, z=3.0, v=3.0, tbs=0.45), dict(hold=24, entry="limit")),
}


def dev2():
    """DEV-only robustness: per-year, doubled costs, leverage sweep and liquidation counts for the DEV leaders."""
    P, F = load_all()
    out = {}
    years = [(utc(y, 1, 1), utc(y + 1, 1, 1) if y < 2024 else DEV_END) for y in range(2021, 2025)]
    for nm, v in CAND.items():
        tr = make_trades(P, F, *v)
        r = {"base": run_one(P, tr, DEV_START, DEV_END), "double_cost": run_one(P, tr, DEV_START, DEV_END, cost=2)}
        r["years"] = {str(2021 + i): {k: run_one(P, tr, a, b)[k] for k in ("geo_month", "mean_month", "max_dd_pct")}
                      for i, (a, b) in enumerate(years)}
        r["lev"] = {}
        for lev in (1, 1.5, 2, 2.5, 3, 4, 5, 7, 10):
            s = run_one(P, tr, DEV_START, DEV_END, lev=lev)
            r["lev"][str(lev)] = {k: s.get(k) for k in ("mean_month", "median_month", "geo_month", "worst_month",
                                                        "max_dd_pct", "net_pct", "liquidations")}
            print(nm, lev, r["lev"][str(lev)], flush=True)
        r["base"].pop("months"); r["double_cost"].pop("months")
        print(nm, json.dumps({k: r[k] for k in ("base", "double_cost", "years")}, default=lambda o: o.item()), flush=True)
        out[nm] = r
    (OUT / "dev_robust.json").write_text(json.dumps(out, indent=1, default=lambda o: o.item()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "fetch":
        data04.fetch_all("1h", workers=48)
    elif cmd == "dev":
        dev()
    elif cmd == "dev2":
        dev2()
    elif cmd == "test":
        import run_test
        run_test.main()
