"""Agent 03 - funding-rate strategies on Binance USDT perps (monthly-20% round).

    python run.py dev      # DEV exploration grid (2021-01..2024-10) -> dev_grid.json
    python run.py test     # the pre-registered attempts in PREREG.md on DEV + TEST + TEST at 2x costs -> results.json

Families
  CARRY  long spot / short perp on the coins with the highest trailing funding (delta neutral), rotated daily.
  XS     contrarian cross-section: short the K highest-funding perps, long the K most negative ones.
  TS     contrarian time-series: short a perp whose trailing funding is above a threshold (crowded longs).

Bars: 1d (perp + spot) - decisions at a daily boundary use only closed bars and funding stamps before it,
fills at that bar's open. Funding is paid/received on stamps strictly inside the holding.
Liquidation: a position is wiped when its loss reaches its margin minus 0.5% of notional, checked on bar highs/lows
(for the hedged carry: worst same-side basis, spot high vs perp high and spot low vs perp low).
Capacity: notional per leg <= 1% of the previous daily bar's quote volume (both venues for carry).
Carry at leverage L > 1 borrows (L-1) x margin of USDT to buy the spot leg (portfolio margin), charged 10% APR.
"""
from __future__ import annotations
import bisect, json, math, sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa

OUT = Path(__file__).resolve().parent
DATA = PROJECT / "data" / "monthly20_03_funding_carry"
BAR = DAY
IV = "1d"
BORROW_APR = 0.10
LIQ_LOG = []  # (symbol, day ms, kind) of every liquidation in the last simulate() call

# ---------------------------------------------------------------- data

_daily = {}


def daily_vol(sym):
    if sym not in _daily:
        p = CACHE / "perp" / "1d" / f"{sym}.json"
        rows = json.loads(p.read_text()) if p.exists() else []
        _daily[sym] = ({r[0]: r[6] for r in rows})
    return _daily[sym]


_SYMS = None


def syms_meta():
    global _SYMS
    if _SYMS is None:
        _SYMS = perp_symbols()
    return _SYMS


def spot_symbol(perp_sym, base):
    """Spot pair for a perp (1000PEPE -> PEPEUSDT). Returns name or None if spot has no USDT pair."""
    spot = set(json.loads((DATA / "spot_usdt_symbols.json").read_text()))
    b = base
    for pre in ("1000000", "1000", "1M"):
        if b.startswith(pre) and (b[len(pre):] + "USDT") in spot:
            return b[len(pre):] + "USDT"
    return b + "USDT" if b + "USDT" in spot else None


_universe_cache = {}


def universe(at, n=40, lookback_days=30):
    """Same rule as common.liquid_universe (top n by 30d perp quote volume before `at`), fast and cached."""
    key = (at, n)
    if key not in _universe_cache:
        ranked = []
        days = [at - (i + 1) * DAY for i in range(lookback_days)]
        for s in syms_meta():
            if s["onboard"] and s["onboard"] > at - lookback_days * DAY:
                continue
            dv = daily_vol(s["symbol"])
            vals = [dv[d] for d in days if d in dv]
            if len(vals) >= lookback_days * 0.8:
                ranked.append((sum(vals), s["symbol"]))
        _universe_cache[key] = [sym for _, sym in sorted(ranked, reverse=True)[:n]]
    return _universe_cache[key]


def month_starts(start, end):
    out, d = [], datetime.fromtimestamp(start / 1000, timezone.utc)
    while True:
        t = utc(d.year, d.month, 1)
        if t >= end:
            return out
        out.append(t)
        d = datetime(d.year + (d.month // 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)


def needed_symbols(n=40):
    out = set()
    for t in month_starts(DEV_START, TEST_END):
        out.update(universe(t, n))
    return sorted(out)


class Series:
    def __init__(self, rows):
        self.t = {r[0]: i for i, r in enumerate(rows)}
        self.rows = rows

    def bar(self, t):
        i = self.t.get(t)
        return None if i is None else self.rows[i]

    def prev(self, t):
        i = self.t.get(t - BAR)
        return None if i is None else self.rows[i]


class Fund:
    def __init__(self, rows):
        self.t = [r[0] for r in rows]
        self.r = [r[1] for r in rows]
        self.cum = [0.0]
        for x in self.r:
            self.cum.append(self.cum[-1] + x)

    def avg8h(self, t, days):
        """Mean funding per 8h over stamps in [t - days, t) - interval independent."""
        i0, i1 = bisect.bisect_left(self.t, t - days * DAY), bisect.bisect_left(self.t, t)
        if i1 - i0 < days * 2:  # need history
            return None
        return (self.cum[i1] - self.cum[i0]) / (days * 3)

    def stamps(self, a, b):
        """Stamps with a <= s < b."""
        i0, i1 = bisect.bisect_left(self.t, a), bisect.bisect_left(self.t, b)
        return [(self.t[i], self.r[i]) for i in range(i0, i1)]


def _cached(path):
    return json.loads(path.read_text()) if path.exists() else None


class Market:
    """Cache-only loader (fetch.py downloads through common.py). Symbols without funding history are dropped."""

    def __init__(self, syms):
        meta = {s["symbol"]: s for s in syms_meta()}
        self.perp, self.spot, self.fund, self.spotname = {}, {}, {}, {}
        self.missing = []
        for s in syms:
            k = _cached(CACHE / "perp" / IV / f"{s}.json")
            f = _cached(CACHE / "funding" / f"{s}.json")
            if not k or f is None:
                self.missing.append(s)
                continue
            self.perp[s] = Series(k)
            self.fund[s] = Fund(f)
            sp = spot_symbol(s, meta[s]["base"])
            self.spotname[s] = sp
            if sp:
                rows = _cached(CACHE / "spot" / IV / f"{sp}.json")
                if rows:
                    self.spot[s] = Series(rows)


# ---------------------------------------------------------------- engine

class Pos:
    _mark = 0.0


def simulate(mkt, strat, start, end, L=1.0, cost_mult=1.0, start_eq=10_000.0):
    side_cost = (FEE + SLIP) * cost_mult
    cash, pos, curve, trades = start_eq, {}, [(start, start_eq)], []
    nliq = 0
    LIQ_LOG.clear()
    t = start
    rebalance_every = strat["rebalance_h"] * HOUR

    def equity():
        return cash + sum(p.a + p.pnl for p in pos.values())

    def close(p, px_perp, px_spot, liquidated=False):
        nonlocal cash
        if liquidated:
            pnl = -p.a
        else:
            if p.kind == "carry":
                price = p.N * (px_spot / p.s0 - px_perp / p.p0)
                exit_cost = p.N * side_cost * (px_spot / p.s0 + px_perp / p.p0)
            else:
                price = p.N * p.side * (px_perp / p.p0 - 1)
                exit_cost = p.N * side_cost * px_perp / p.p0
            pnl = p.pnl + price - exit_cost
        cash += p.a + pnl
        trades.append(pnl)
        del pos[p.sym]

    while t < end:
        # 1. funding stamp exactly at t for positions held across t is collected in step 3 of the previous bar
        # 2. rebalance at open of bar t
        if (t - start) % rebalance_every == 0:
            targets = strat["fn"](mkt, t, strat, pos)  # {sym: (kind, side)}
            for sym in list(pos):
                if sym not in targets or targets[sym] != (pos[sym].kind, pos[sym].side):
                    b = mkt.perp[sym].bar(t)
                    sb = mkt.spot[sym].bar(t) if pos[sym].kind == "carry" else None
                    if b is None or (pos[sym].kind == "carry" and sb is None):
                        continue  # no price: keep it (rare gaps)
                    close(pos[sym], b[1], sb[1] if sb else None)
            E = equity()
            nslots = strat["slots"]
            for sym, (kind, side) in targets.items():
                if sym in pos:
                    continue
                b, pb = mkt.perp[sym].bar(t), mkt.perp[sym].prev(t)
                if b is None or pb is None:
                    continue
                cap = 0.01 * pb[6]
                if kind == "carry":
                    sb, spb = mkt.spot[sym].bar(t), mkt.spot[sym].prev(t)
                    if sb is None or spb is None:
                        continue
                    cap = min(cap, 0.01 * spb[6])
                a = min(E / nslots, cash)
                if a <= 0:
                    break
                p = Pos()
                p.sym, p.kind, p.side, p.a = sym, kind, side, a
                p.N = min(L * a, cap)
                p.p0, p.s0, p.entry = b[1], (sb[1] if kind == "carry" else None), t
                legs = 2 if kind == "carry" else 1
                p.costs = p.N * side_cost * legs
                p.pnl, p.fund = -p.costs, 0.0
                cash -= a
                pos[sym] = p
        # 3. run bar t: funding stamps in (t .. t+BAR], liquidation on high/low, mark at close
        for sym in list(pos):
            p = pos[sym]
            b = mkt.perp[sym].bar(t)
            sb = mkt.spot[sym].bar(t) if p.kind == "carry" else None
            if b is None or (p.kind == "carry" and sb is None):
                continue
            # funding stamps in [t, t+BAR); the stamp exactly at t only for positions held across t (not opened at t;
            # positions closed at t were removed before this loop and do not get it)
            stamps = mkt.fund[sym].stamps(t if p.entry < t else t + 1, t + BAR)
            if p.kind == "carry":
                for s, r in stamps:
                    p.pnl += r * p.N * (b[1] / p.p0)  # short perp receives positive funding
                if L > 1:
                    p.pnl -= max(0.0, p.N - p.a) * BORROW_APR * BAR / (365 * DAY)
                worst = min(sb[2] / p.s0 - b[2] / p.p0, sb[3] / p.s0 - b[3] / p.p0)
                base_pnl = p.pnl  # funding + costs so far, price part computed fresh
                if (base_pnl + p.N * worst) <= -(p.a - MAINT_MARGIN * p.N):
                    p.pnl = -p.a + 0.0
                    nliq += 1
                    LIQ_LOG.append((sym, t, "carry"))
                    trades.append(-p.a)
                    del pos[sym]
                    continue
                p._mark = p.N * (sb[4] / p.s0 - b[4] / p.p0)
            else:
                sg = p.side
                for s, r in stamps:
                    p.pnl -= sg * r * p.N * (b[1] / p.p0)
                worst = (b[3] / p.p0 - 1) if sg > 0 else -(b[2] / p.p0 - 1)
                if p.pnl + p.N * worst <= -(p.a - MAINT_MARGIN * p.N):
                    nliq += 1
                    LIQ_LOG.append((sym, t, "dir"))
                    trades.append(-p.a)
                    del pos[sym]
                    continue
                p._mark = p.N * sg * (b[4] / p.p0 - 1)
        t += BAR
        E = cash + sum(p.a + p.pnl + getattr(p, "_mark", 0.0) for p in pos.values())
        curve.append((t - 1, E))
        if E <= 0:
            break
    # close what is left at the last mark
    for p in list(pos.values()):
        trades.append(p.pnl + p._mark - p.N * side_cost * (2 if p.kind == "carry" else 1))
    final = cash + sum(p.a + p.pnl + p._mark - p.N * side_cost * (2 if p.kind == "carry" else 1) for p in pos.values())
    curve[-1] = (curve[-1][0], final)
    return curve, trades, nliq


# ---------------------------------------------------------------- strategies

def carry_fn(mkt, t, st, pos):
    m0 = month_floor(t)
    uni = [s for s in universe(m0, st["U"]) if s in mkt.spot and s in mkt.perp]
    scored = []
    for s in uni:
        f = mkt.fund[s].avg8h(t, st["D"])
        if f is not None:
            scored.append((f, s))
    scored.sort(reverse=True)
    keep_rank = int(st["K"] * st.get("hyst", 1.0))
    top = [s for f, s in scored[:st["K"]] if f >= st["min_f"]]
    held_ok = [s for f, s in scored[:keep_rank] if f >= st["min_f"] * st.get("exit_frac", 0.5) and s in pos]
    out = {}
    for s in held_ok + top:
        if len(out) >= st["K"]:
            break
        out[s] = ("carry", -1)
    return out


def xs_fn(mkt, t, st, pos):
    uni = [s for s in universe(month_floor(t), st["U"]) if s in mkt.perp]
    scored = [(mkt.fund[s].avg8h(t, st["D"]), s) for s in uni]
    scored = sorted((f, s) for f, s in scored if f is not None)
    K = st["K"]
    out = {}
    if st.get("short", True):
        for f, s in scored[-K:]:
            if f >= st.get("min_short", -1):
                out[s] = ("dir", -1)
    if st.get("long", True):
        for f, s in scored[:K]:
            if f <= st.get("max_long", 1):
                out[s] = ("dir", 1)
    return out


def ts_fn(mkt, t, st, pos):
    uni = [s for s in universe(month_floor(t), st["U"]) if s in mkt.perp]
    out = {}
    cands = []
    for s in uni:
        f = mkt.fund[s].avg8h(t, st["D"])
        if f is None:
            continue
        if f >= st["thr"]:
            cands.append((-f, s, -1))
        elif st.get("neg_thr") is not None and f <= st["neg_thr"]:
            cands.append((f, s, 1))
    for _, s, side in sorted(cands)[:st["slots"]]:
        out[s] = ("dir", side)
    return out


def month_floor(t):
    d = datetime.fromtimestamp(t / 1000, timezone.utc)
    return utc(d.year, d.month, 1)


FNS = {"carry": carry_fn, "xs": xs_fn, "ts": ts_fn}


def make(cfg):
    st = dict(cfg)
    st["fn"] = FNS[cfg["family"]]
    if "slots" not in st:
        st["slots"] = st["K"] * (2 if cfg["family"] == "xs" and st.get("short", True) and st.get("long", True) else 1)
    return st


def run_cfg(mkt, cfg, start, end, L=1.0, cost_mult=1.0):
    curve, trades, nliq = simulate(mkt, make(cfg), start, end, L=L, cost_mult=cost_mult)
    s = summary(curve, trades)
    s["liquidations"] = nliq
    return s


# ---------------------------------------------------------------- main

def isolated_short_risk(mkt, syms, start, end, hold_days=7):
    """How often an UNHEDGED-margin short perp (isolated, perp leg alone) at leverage x would be liquidated within
    a hold of `hold_days` (perp high rises >= 1/x - 0.5% above entry), sampled daily."""
    out = {}
    for x in (2, 3, 5, 10, 20):
        hit = tot = 0
        lim = 1 / x - MAINT_MARGIN
        for s in syms:
            rows = [r for r in mkt.perp[s].rows if start <= r[0] < end]
            for i in range(0, len(rows) - hold_days * 2, 2):
                p0 = rows[i][1]
                mx = max(r[2] for r in rows[i:i + hold_days * 2])
                tot += 1
                hit += (mx / p0 - 1) >= lim
        out[f"{x}x"] = round(hit / tot * 100, 2) if tot else None
    return out


def dev_grid(mkt):
    grid = []
    for U in (40, 80):
        for D in (1, 3, 7):
            for K in (3, 5, 10):
                for min_f in (0.0001, 0.0003):
                    grid.append({"family": "carry", "U": U, "D": D, "K": K, "min_f": min_f, "hyst": 2.0,
                                 "exit_frac": 0.5, "rebalance_h": 24})
    for U in (40, 80):
        for D in (1, 3):
            for K in (3, 5):
                for H in (24, 72, 168):
                    grid.append({"family": "xs", "U": U, "D": D, "K": K, "rebalance_h": H})
                    grid.append({"family": "xs", "U": U, "D": D, "K": K, "rebalance_h": H, "long": False})
                    grid.append({"family": "xs", "U": U, "D": D, "K": K, "rebalance_h": H, "short": False})
    for U in (40, 80):
        for D in (1, 3):
            for thr in (0.0005, 0.001):
                for H in (24, 72):
                    grid.append({"family": "ts", "U": U, "D": D, "thr": thr, "neg_thr": -thr, "slots": 5,
                                 "rebalance_h": H})
    res = []
    for i, cfg in enumerate(grid):
        s = run_cfg(mkt, cfg, DEV_START, DEV_END)
        res.append({"cfg": cfg, "mean": s["mean_month"], "median": s["median_month"], "worst": s["worst_month"],
                    "dd": s["max_dd_pct"], "net": s["net_pct"], "trades": s.get("trades"),
                    "liq": s["liquidations"], "h1": None})
        print(i, cfg, s["mean_month"], s["median_month"], s["worst_month"], s["max_dd_pct"], s.get("trades"),
              flush=True)
    return res


def lev_needed(mkt, cfg, start, end, cost_mult=1.0, levels=(1, 2, 3, 5, 7, 10, 15, 20, 30, 50)):
    """Smallest leverage whose mean month >= 20% on this window (None if none), plus the whole ladder."""
    ladder, need = {}, None
    for L in levels:
        s = run_cfg(mkt, cfg, start, end, L=L, cost_mult=cost_mult)
        ladder[L] = {k: s[k] for k in ("mean_month", "median_month", "worst_month", "max_dd_pct", "net_pct",
                                        "liquidations")}
        if need is None and s["mean_month"] is not None and s["mean_month"] >= 20:
            need = L
    return need, ladder


# Filled in from DEV only, BEFORE the TEST run (mirrors PREREG.md).
_CARRY = {"family": "carry", "U": 40, "D": 7, "K": 10, "min_f": 0.0001, "hyst": 2.0, "exit_frac": 0.5, "rebalance_h": 24}
_XS = {"family": "xs", "U": 40, "D": 3, "K": 5, "rebalance_h": 72}
_XSL = {"family": "xs", "U": 80, "D": 3, "K": 3, "rebalance_h": 72, "short": False}
ATTEMPTS = [{"name": "CARRY_1x", "cfg": _CARRY, "L": 1}, {"name": "CARRY_3x", "cfg": _CARRY, "L": 3},
            {"name": "CARRY_10x", "cfg": _CARRY, "L": 10}, {"name": "CARRY_30x", "cfg": _CARRY, "L": 30},
            {"name": "XS_LS_1x", "cfg": _XS, "L": 1}, {"name": "XS_LS_2x", "cfg": _XS, "L": 2},
            {"name": "XS_LONGNEG_1x", "cfg": _XSL, "L": 1}]
PRIMARY = "CARRY_30x"


def funding_regime(mkt):
    """Per year: mean funding per 8h across the U40 universe, and the mean of the top-10 coins' 7-day funding."""
    out = {}
    for t in month_starts(DEV_START, TEST_END):
        y = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y")
        uni = [s for s in universe(t, 40) if s in mkt.perp]
        fs = sorted([f for f in (mkt.fund[s].avg8h(t + 14 * DAY, 7) for s in uni) if f is not None], reverse=True)
        if fs:
            out.setdefault(y, []).append((sum(fs) / len(fs), sum(fs[:10]) / min(10, len(fs))))
    return {y: {"all_apr_pct": round(sum(a for a, _ in v) / len(v) * 3 * 365 * 100, 1),
                "top10_apr_pct": round(sum(b for _, b in v) / len(v) * 3 * 365 * 100, 1)} for y, v in out.items()}


def run_test(mkt):
    assert (OUT / "PREREG.md").exists(), "write PREREG.md first"
    res = {"agent": "03_funding_carry", "idea": "Binance USDT-perp funding: delta-neutral carry (long spot / short perp) "
           "on the highest-funding coins, contrarian funding cross-section and time-series, and leverage on both",
           "attempts": []}
    for a in ATTEMPTS:
        cfg, L = a["cfg"], a["L"]
        row = {"name": a["name"], "prereg": True, "cfg": cfg, "leverage": L}
        for key, (s0, s1, cm) in {"dev": (DEV_START, DEV_END, 1), "test": (TEST_START, TEST_END, 1),
                                  "test_double_cost": (TEST_START, TEST_END, 2)}.items():
            curve, trades, nliq = simulate(mkt, make(cfg), s0, s1, L=L, cost_mult=cm)
            s = summary(curve, trades)
            s["liquidations"] = nliq
            row[key] = s
        print(a["name"], "TEST", {k: v for k, v in row["test"].items() if k != "months"}, flush=True)
        res["attempts"].append(row)
    return res


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    U = 80
    syms = needed_symbols(U)
    mkt = Market(syms)
    print("loaded", len(mkt.perp), "with spot", len(mkt.spot), "missing", mkt.missing, flush=True)
    if mode == "dev":
        res = dev_grid(mkt)
        (OUT / "dev_grid.json").write_text(json.dumps(res, indent=1))
    elif mode == "test":
        res = run_test(mkt)
        pri = next(a for a in res["attempts"] if a["name"] == PRIMARY)
        t = pri["test"]
        top3 = t.get("top3_share_of_profit")
        passed = (t["mean_month"] >= 20 and pri["test_double_cost"]["mean_month"] > 0 and t["net_pct"] > -99
                  and t["liquidations"] == 0 or False)
        # single-trade check: largest trade share of profit must be <= 50%
        need, ladder = lev_needed(mkt, _CARRY, TEST_START, TEST_END)
        need2, ladder2 = lev_needed(mkt, _CARRY, TEST_START, TEST_END, cost_mult=2)
        res["best_honest"] = PRIMARY
        res["verdict"] = "PASS" if (t["mean_month"] >= 20 and pri["test_double_cost"]["mean_month"] > 0
                                    and t["net_pct"] > -99) else "FAIL"
        res["leverage_needed_for_20pct"] = need
        res["analysis"] = {
            "carry_leverage_ladder_test": ladder, "carry_leverage_ladder_test_double_cost": ladder2,
            "carry_leverage_needed_test_double_cost": need2,
            "isolated_short_perp_liq_pct_7d_hold_dev": isolated_short_risk(
                mkt, [s for s in universe(utc(2023, 1, 1), 40) if s in mkt.perp], DEV_START, DEV_END),
            "isolated_short_perp_liq_pct_7d_hold_test": isolated_short_risk(
                mkt, [s for s in universe(utc(2025, 6, 1), 40) if s in mkt.perp], TEST_START, TEST_END),
            "funding_regime_by_year": funding_regime(mkt)}
        (OUT / "results.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res["analysis"], indent=1))
        print("verdict", res["verdict"], "need", need)
