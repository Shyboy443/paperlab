"""V11.5 study: can Renata's 1-hour reversal ranker (V11.4-SCAN) be made profitable after costs?
(operator, 2026-10-10: "fix & upgrade Renata ... rigorously tested V11.5 conditional-reversal strategy".)

    python scripts/v115_reversal_study.py build --data-dir /data/bt2y --start 2024-10-01 --end 2026-10-01 --out /data/v115/main.npz
    python scripts/v115_reversal_study.py build --data-dir /data/bt_hold_crypto --start 2023-10-01 --end 2024-10-01 --out /data/v115/hold1.npz
    python scripts/v115_reversal_study.py build --data-dir /data/bt_hold2 --start 2022-01-01 --end 2023-07-01 --out /data/v115/hold2.npz
    python scripts/v115_reversal_study.py analyze --dir /data/v115 --out docs/V115_REVERSAL_STUDY.json

PRE-REGISTERED (2026-10-10, before any run; selection on DEV only, nothing re-tuned after TEST / holdouts)

Universe: the 30 V11 coins (docs/V11_FREEZE.json); an hour is scanned only when >= 10 coins have complete data.
Decision: at every UTC hour close, from closed 1h bars only (built from the 1m tape; an hour with a missing minute is
not a bar). Entry: the OPEN of the minute after the decision (1 minute of latency), as a taker.
Costs (Bybit linear): taker fee 5.5 bp per side + 2 bp spread/slippage per market fill; a take-profit is a resting
limit (maker 2 bp, no slippage); a stop fills at the stop, or at the minute's open when it gaps through. If one minute
reaches both the stop and a target, the STOP fills (conservative). Funding is ignored (holds <= 2 h).
Results are in R (1R = the planned stop distance), costs included; max drawdown is on the cumulative R curve.

Windows: DEV 2024-10-01..2025-10-01 (all selection), TEST 2025-10-01..2026-10-01, HOLD1 2023-10-01..2024-10-01,
HOLD2 2022-01-01..2023-07-01.

Baseline (corrected V11.4): every hour short the 2 highest and buy the 2 lowest 4h returns; stop 2 x ATR14(1h),
clamped to 1.5-8%; the operator's ladder 25/50/25% at 0.5 / 1 / 1.5R with the stop to entry + half of TP1 after TP1
and onto TP1 after TP2; time exit at 58 minutes.

Variants, each adding to the one before (the spec's V11.5 A-E):
  A  STRENGTH      only coins whose 4h move is >= z_min ATR14(1h) units (a statistical extreme), 0-2 per side
                   ("LEGAL_STRONG / LEGAL_CONVICTION" are position-SIZING tiers, not signal strength: not used)
  B  REGIME        + skip the hour when BTC's own 4h move is >= btc_max ATR units (a broad move, not a coin extreme),
                   and rank on the move RELATIVE to BTC
  C  CONFIRM       + the last 15m bar closes against the 4h move (a reversal has started)
  D  CONTROL       + skip a coin whose 24h move is in the same direction and >= persist_z ATR units (persistent trend),
                   optionally require climax volume (the hour's quote volume >= vol_min x its 7-day median), and a
                   6-hour cooldown on a coin after a stop (a cooled-down trade is dropped, not replaced)
  E  EXITS         + the exit chosen on DEV from: hold 15/30/45/58/90/120 min x stop 1/1.5/2/3 ATR x target
                   time-only / fixed 1R / fixed 2R / the ladder
z_min, btc_max and persist_z are chosen on DEV from small grids (z_min 1/1.5/2/2.5/3, btc_max 1.5/2.5/none,
persist_z 3/5/none), maximising DEV expectancy with >= 300 DEV trades.

Verdict: a variant PASSES only if its expectancy (R/trade after costs) is > 0 in TEST, HOLD1 and HOLD2, its profit
factor over the three is >= 1.10, it has >= 150 trades in each, and it is still > 0 with costs doubled. If nothing
passes, the report says so and Renata stays V11.4 (no V11.5 goes live).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
H = 120                                   # minutes of path kept after each entry
TAKER, SLIP, MAKER = 5.5e-4, 2.0e-4, 2.0e-4
COINS = tuple(sorted(json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))["rules"]))
EPS = 1e-7                                # float32 paths: a level touched exactly still counts
TOPK = 5                                  # candidates kept per side per hour (the scanner never looks past these)


def ms(d: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# -- build: candidates with features and their next-120-minute paths ------------------------------------------------------
def load_minutes(root: Path, sym: str, a: int, b: int) -> np.ndarray | None:
    """(n_minutes, 5) open/high/low/close/quote-volume from a to b, NaN where a minute is missing."""
    import scripts.backtest_2y as bt
    n = (b - a) // MIN
    out = np.full((n, 5), np.nan, dtype=np.float32)
    seen = False
    for month in bt.months(a, b):
        d = bt.load_month(root, sym, month)
        if d is None:
            continue
        ts = np.asarray(d["ts"], dtype=np.int64)
        idx = (ts - a) // MIN
        ok = (idx >= 0) & (idx < n)
        if not ok.any():
            continue
        seen = True
        o, h, l, c, v, q = (np.asarray(col, dtype=np.float64) for col in d["cols"])
        out[idx[ok], 0], out[idx[ok], 1], out[idx[ok], 2], out[idx[ok], 3] = o[ok], h[ok], l[ok], c[ok]
        out[idx[ok], 4] = q[ok] if q.any() else v[ok] * c[ok]
    return out if seen else None


def hourly(m: np.ndarray) -> np.ndarray:
    """Complete hourly bars (open, high, low, close, quote volume); NaN when any minute of the hour is missing."""
    nh = m.shape[0] // 60
    x = m[: nh * 60].reshape(nh, 60, 5)
    full = ~np.isnan(x[:, :, 3]).any(axis=1)
    out = np.full((nh, 5), np.nan)
    out[full, 0] = x[full, 0, 0]
    out[full, 1] = np.nanmax(x[full, :, 1], axis=1)
    out[full, 2] = np.nanmin(x[full, :, 2], axis=1)
    out[full, 3] = x[full, -1, 3]
    out[full, 4] = np.nansum(x[full, :, 4], axis=1)
    return out


def atr14(hb: np.ndarray) -> np.ndarray:
    prev = np.roll(hb[:, 3], 1)
    tr = np.nanmax(np.stack([hb[:, 1] - hb[:, 2], np.abs(hb[:, 1] - prev), np.abs(hb[:, 2] - prev)]), axis=0)
    tr[0] = np.nan
    out = np.full(len(tr), np.nan)
    for i in range(14, len(tr)):                  # simple mean of the last 14 complete true ranges
        w = tr[i - 13: i + 1]
        if not np.isnan(w).any():
            out[i] = w.mean()
    return out


def build(args: argparse.Namespace) -> int:
    t0 = time.time()
    root = Path(args.data_dir)
    a, b = ms(args.start) - 2 * DAY, ms(args.end) + 3 * HOUR          # warm-up for the 24h move, room for the path
    a -= a % HOUR
    mins, hrs, atrs = {}, {}, {}
    for sym in COINS:
        m = load_minutes(root, sym, a, b)
        if m is None:
            continue
        mins[sym] = m
        hrs[sym] = hourly(m)
        atrs[sym] = atr14(hrs[sym])
    syms = sorted(mins)
    nh = min(len(h) for h in hrs.values())
    print(f"{len(syms)} coins, {nh} hours, loaded in {time.time() - t0:.0f}s", flush=True)
    btc = "BTCUSDT" if "BTCUSDT" in syms else None
    rows, paths = [], []
    first_h = (ms(args.start) - a) // HOUR
    for hi in range(max(first_h, 30), nh - 3):    # decision at the CLOSE of hour hi (minute (hi + 1) * 60)
        feats = {}
        for s in syms:
            hb, at = hrs[s], atrs[s][hi]
            if hi < 24 or np.isnan(hb[hi - 24: hi + 1, 3]).any() or not at or np.isnan(at):
                continue
            c, c4, c24 = hb[hi, 3], hb[hi - 4, 3], hb[hi - 24, 3]
            m = mins[s]
            e_i = (hi + 1) * 60 + 1                # entry: the open of the minute after the decision
            p = m[e_i: e_i + H]
            if p.shape[0] < H or np.isnan(p[0, 0]):
                continue
            q15 = m[(hi + 1) * 60 - 15: (hi + 1) * 60]
            if np.isnan(q15[:, 3]).any():
                continue
            vol_rel = hb[hi, 4] / np.nanmedian(hrs[s][max(0, hi - 168): hi, 4]) if hi > 24 else np.nan
            feats[s] = {"r4": c / c4 - 1, "r24": c / c24 - 1, "atr_pct": at / c, "close": c, "entry": p[0, 0],
                        "q15_dir": np.sign(q15[-1, 3] - q15[0, 0]), "vol_rel": vol_rel, "qvol_h": hb[hi, 4], "path": p}
        if len(feats) < 10:
            continue
        b4 = feats[btc]["r4"] / (feats[btc]["atr_pct"] * 2) if btc in feats else 0.0      # BTC move in ATR units
        r4 = {s: f["r4"] for s, f in feats.items()}
        order = sorted(r4, key=lambda s: (r4[s], s))
        cands = [(s, "long", i) for i, s in enumerate(order[:TOPK])] + \
                [(s, "short", i) for i, s in enumerate(reversed(order[-TOPK:]))]
        for s, side, rank in cands:
            f = feats[s]
            atrp = f["atr_pct"]
            z = f["r4"] / (atrp * 2)                                   # 4h move in units of a 4h-scaled hourly ATR
            z_rel = (f["r4"] - (feats[btc]["r4"] if btc in feats else 0.0)) / (atrp * 2)
            z24 = f["r24"] / (atrp * 4.9)                              # 24h move in units of a 24h-scaled ATR
            p = f["path"]
            rows.append([hi, COINS.index(s), 1 if side == "long" else -1, rank, z, z_rel, z24, b4, f["q15_dir"],
                         f["vol_rel"], f["qvol_h"], atrp, f["entry"]])
            paths.append((p[:, :4] / f["entry"] - 1.0).astype(np.float32))      # o/h/l/c relative to the entry
    meta = np.asarray(rows, dtype=np.float64)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, meta=meta, paths=np.stack(paths) if paths else np.zeros((0, H, 4), np.float32),
                        start=ms(args.start), a=a, coins=np.array(COINS))
    print(f"{len(rows)} candidates in {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


# -- simulate exits on the stored paths ------------------------------------------------------------------------------------
COL = {k: i for i, k in enumerate(("hour", "coin", "side", "rank", "z", "z_rel", "z24", "btc_z", "q15", "vol_rel", "qvol",
                                    "atr_pct", "entry"))}


def simulate(paths: np.ndarray, side: np.ndarray, stop_frac: np.ndarray, hold: int, target: str,
             cost_mult: float = 1.0) -> np.ndarray:
    """R per trade after costs. paths: (n, H, 4) open/high/low/close relative to entry; side +1 long / -1 short."""
    n = len(side)
    if n == 0:
        return np.zeros(0)
    o, hi_, lo, cl = (paths[:, :hold, k] * side[:, None] for k in range(4))
    # in the trade's favour: for a short, the "high" of the favourable move is the low of price
    fav = np.where(side[:, None] > 0, hi_, -paths[:, :hold, 2])
    adv = np.where(side[:, None] > 0, lo, -paths[:, :hold, 1])
    sf = stop_frac[:, None]
    stop_hit = adv <= -sf
    entry_cost = (TAKER + SLIP) * cost_mult
    if target == "ladder":
        return _ladder(o, fav, adv, cl, sf[:, 0], hold, cost_mult) - entry_cost / stop_frac
    tgt = {"time": None, "1R": 1.0, "2R": 2.0}[target]
    first_stop = np.where(stop_hit.any(1), stop_hit.argmax(1), hold)
    if tgt is not None:
        t_hit = fav >= tgt * sf - EPS
        first_tgt = np.where(t_hit.any(1), t_hit.argmax(1), hold)
    else:
        first_tgt = np.full(n, hold)
    r = np.empty(n)
    stop_first = (first_stop <= first_tgt) & (first_stop < hold)
    tgt_first = (first_tgt < first_stop) & (first_tgt < hold)
    timed = ~(stop_first | tgt_first)
    rows = np.arange(n)
    # a stop fills at the stop, or at the minute's open when that minute opened through it
    so = o[rows, np.minimum(first_stop, hold - 1)]
    stop_px = np.minimum(-stop_frac, so)
    r[stop_first] = (stop_px[stop_first] - (TAKER + SLIP) * cost_mult) / stop_frac[stop_first]
    if tgt is not None:
        r[tgt_first] = (tgt * stop_frac[tgt_first] - MAKER * cost_mult) / stop_frac[tgt_first]
    last = cl[:, hold - 1]
    r[timed] = (last[timed] - (TAKER + SLIP) * cost_mult) / stop_frac[timed]
    return r - entry_cost / stop_frac


def _ladder(o, fav, adv, cl, sf, hold, cost_mult):
    """The operator's ladder: 25/50/25% at 0.5/1/1.5R; after TP1 the stop to entry + max(fees, half of TP1), after TP2
    onto TP1; time exit for the rest. Minute by minute, stop first when a minute reaches both."""
    n = len(sf)
    out = np.zeros(n)
    rungs, shares = (0.5, 1.0, 1.5), (0.25, 0.5, 0.25)
    for i in range(n):
        s, left, got, stop = sf[i], 1.0, 0.0, -sf[i]
        k = 0
        for t in range(hold):
            if adv[i, t] <= stop:                                       # stop (or the moved stop) first
                px = min(stop, o[i, t])                            # a gap through the stop fills at the open
                got += left * (px - (TAKER + SLIP) * cost_mult)
                left = 0.0
                break
            while k < 3 and fav[i, t] >= rungs[k] * s - EPS:
                got += shares[k] * (rungs[k] * s - MAKER * cost_mult)
                left -= shares[k]
                k += 1
                stop = max(0.0015, 0.5 * rungs[0] * s) if k == 1 else rungs[0] * s
            if left <= 1e-9:
                break
        if left > 1e-9:
            got += left * (cl[i, hold - 1] - (TAKER + SLIP) * cost_mult)
        out[i] = got / s
    return out


def stats(r: np.ndarray, days: float) -> dict[str, Any]:
    if len(r) == 0:
        return {"trades": 0}
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)) if len(eq) else 0.0
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": int(len(r)), "per_day": round(len(r) / days, 2), "win_rate": round(float((r > 0).mean()), 3),
            "pf": round(float(wins / losses), 3) if losses else None, "expectancy_r": round(float(r.mean()), 4),
            "net_r": round(float(r.sum()), 1), "max_dd_r": round(dd, 1)}


def select(meta: np.ndarray, v: dict[str, Any]) -> np.ndarray:
    """Indices of the candidate trades a variant takes (best per side first, per hour), with its filters."""
    c = COL
    z = meta[:, c["z_rel"]] if v.get("relative") else meta[:, c["z"]]
    side = meta[:, c["side"]]
    ok = np.ones(len(meta), bool)
    if v.get("z_min"):
        ok &= np.abs(z) >= v["z_min"]
        ok &= np.sign(z) == -side                                 # a short needs a rise, a long a fall
    if v.get("btc_max"):
        ok &= np.abs(meta[:, c["btc_z"]]) < v["btc_max"]
    if v.get("confirm"):
        ok &= meta[:, c["q15"]] == side                           # the last 15m bar already moves our way
    if v.get("persist_z"):
        z24 = meta[:, c["z24"]]
        ok &= ~((np.sign(z24) == -side) & (np.abs(z24) >= v["persist_z"]))
    if v.get("vol_min"):                                          # climax volume: the hour's quote volume vs its week
        ok &= np.nan_to_num(meta[:, c["vol_rel"]]) >= v["vol_min"]
    per_side = v.get("per_side", 2)
    idx = np.where(ok)[0]
    # strongest first within each hour and side: by |z| for the filtered variants, by rank for the baseline
    key = meta[idx, c["rank"]] if not v.get("z_min") else -np.abs(z[idx])
    order = np.lexsort((key, meta[idx, c["side"]], meta[idx, c["hour"]]))
    idx = idx[order]
    taken, count = [], {}
    for i in idx:
        hk = (int(meta[i, c["hour"]]), int(meta[i, c["side"]]))
        if count.get(hk, 0) >= per_side:
            continue
        count[hk] = count.get(hk, 0) + 1
        taken.append(i)
    return np.asarray(taken, dtype=int)


def evaluate(data: dict, v: dict[str, Any], cost_mult: float = 1.0) -> np.ndarray:
    meta, paths = data["meta"], data["paths"]
    idx = select(meta, v)
    if len(idx) == 0:
        return np.zeros(0)
    stop = np.clip(v.get("stop_atr", 2.0) * meta[idx, COL["atr_pct"]], 0.015 if v.get("clamp", True) else 0.002, 0.08)
    return simulate(paths[idx], meta[idx, COL["side"]], stop, v.get("hold", 58), v.get("target", "ladder"), cost_mult)


def evaluate_cd(data: dict, v: dict[str, Any], cost_mult: float = 1.0) -> np.ndarray:
    """Like evaluate, with the per-coin cooldown after a stop applied in time order."""
    if not v.get("cooldown_h"):
        return evaluate(data, v, cost_mult)
    meta, paths = data["meta"], data["paths"]
    idx = select(meta, {**v, "cooldown_h": None})
    stop = np.clip(v.get("stop_atr", 2.0) * meta[idx, COL["atr_pct"]], 0.015 if v.get("clamp", True) else 0.002, 0.08)
    r = simulate(paths[idx], meta[idx, COL["side"]], stop, v.get("hold", 58), v.get("target", "ladder"), cost_mult)
    keep, cool = np.ones(len(idx), bool), {}
    for j, i in enumerate(idx):
        coin, h = int(meta[i, COL["coin"]]), int(meta[i, COL["hour"]])
        if cool.get(coin, -1) > h:
            keep[j] = False
            continue
        if r[j] <= -0.9:                                     # stopped out (about -1R after costs)
            cool[coin] = h + int(v["cooldown_h"])
    return r[keep]


def load(path: Path) -> dict:
    z = np.load(path, allow_pickle=False)
    return {"meta": z["meta"], "paths": z["paths"], "days": (z["meta"][:, 0].max() - z["meta"][:, 0].min()) / 24 if len(z["meta"]) else 1}


def split_dev_test(main: dict) -> tuple[dict, dict]:
    hours = main["meta"][:, 0]
    cut = hours.min() + 365 * 24
    d, t = hours < cut, hours >= cut
    mk = lambda m: {"meta": main["meta"][m], "paths": main["paths"][m], "days": m.sum() and (hours[m].max() - hours[m].min()) / 24}  # noqa: E731
    return mk(d), mk(t)


def analyze(args: argparse.Namespace) -> int:
    t0 = time.time()
    root = Path(args.dir)
    main = load(root / "main.npz")
    dev, test = split_dev_test(main)
    hold1, hold2 = load(root / "hold1.npz"), load(root / "hold2.npz")
    wins = {"DEV": dev, "TEST": test, "HOLD1": hold1, "HOLD2": hold2}
    base = {"name": "V11.4 baseline (corrected)", "per_side": 2, "stop_atr": 2.0, "hold": 58, "target": "ladder"}

    def best_on_dev(stage: dict, grid: dict[str, list]) -> dict:
        best, best_e = None, -9.0
        for combo in itertools.product(*grid.values()):
            v = {**stage, **dict(zip(grid.keys(), combo))}
            r = evaluate_cd(dev, v)
            if len(r) >= 300 and r.mean() > best_e:
                best, best_e = v, r.mean()
        return best or {**stage, **{k: vals[0] for k, vals in grid.items()}}

    A = best_on_dev({**base, "name": "V11.5-A strength"}, {"z_min": [1.0, 1.5, 2.0, 2.5, 3.0]})
    B = best_on_dev({**A, "name": "V11.5-B + BTC regime, BTC-relative"}, {"btc_max": [1.5, 2.5, None], "relative": [True]})
    C = {**B, "name": "V11.5-C + 15m reversal confirmation", "confirm": True}
    D = best_on_dev({**C, "name": "V11.5-D + persistence, volume, cooldown"},
                    {"persist_z": [3.0, 5.0, None], "vol_min": [None, 1.5], "cooldown_h": [6]})
    E = best_on_dev({**D, "name": "V11.5-E + exits chosen on DEV"},
                    {"hold": [15, 30, 45, 58, 90, 120], "stop_atr": [1.0, 1.5, 2.0, 3.0], "target": ["time", "1R", "2R", "ladder"]})
    # each single modification on its own, on top of the corrected baseline (which ones carry value)
    singles = {
        "strength only": {**base, "z_min": A.get("z_min")},
        "BTC regime only": {**base, "btc_max": B.get("btc_max") or 2.5, "relative": True},
        "confirmation only": {**base, "confirm": True},
        "persistence + cooldown only": {**base, "persist_z": D.get("persist_z") or 5.0, "cooldown_h": 6},
        "exits only": {**base, **{k: E[k] for k in ("hold", "stop_atr", "target")}},
        "1 per side (less forced)": {**base, "per_side": 1},
    }
    report: dict[str, Any] = {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                              "candidates": {k: int(len(w["meta"])) for k, w in wins.items()}, "variants": {}, "singles": {}}
    for v in (base, A, B, C, D, E):
        rows = {w: stats(evaluate_cd(d, v), d["days"]) for w, d in wins.items()}
        oos = np.concatenate([evaluate_cd(wins[w], v) for w in ("TEST", "HOLD1", "HOLD2")])
        oos2 = np.concatenate([evaluate_cd(wins[w], v, 2.0) for w in ("TEST", "HOLD1", "HOLD2")])
        checks = {"test_pos": rows["TEST"].get("expectancy_r", -1) > 0, "hold1_pos": rows["HOLD1"].get("expectancy_r", -1) > 0,
                  "hold2_pos": rows["HOLD2"].get("expectancy_r", -1) > 0,
                  "pf_1_10": (stats(oos, 1).get("pf") or 0) >= 1.10,
                  "trades_150": all(rows[w].get("trades", 0) >= 150 for w in ("TEST", "HOLD1", "HOLD2")),
                  "double_cost_pos": len(oos2) > 0 and oos2.mean() > 0}
        report["variants"][v["name"]] = {"config": {k: v[k] for k in v if k != "name"}, "windows": rows,
                                         "out_of_sample": stats(oos, 1), "checks": checks, "pass": all(checks.values())}
        print(f"{v['name']:40s} " + "  ".join(f"{w} {rows[w].get('expectancy_r', 0):+.3f}R n={rows[w].get('trades', 0)}"
                                              for w in wins) + f"  PASS={all(checks.values())}", flush=True)
    for k, v in singles.items():
        report["singles"][k] = {w: stats(evaluate_cd(d, v), d["days"]) for w, d in wins.items()}
        print(f"  single: {k:30s} " + "  ".join(f"{w} {report['singles'][k][w].get('expectancy_r', 0):+.3f}R"
                                                 for w in wins), flush=True)
    # gross edge by strength bucket (does the reversal grow with the size of the move?)
    buckets = {}
    for w, d in wins.items():
        m = d["meta"]
        z = np.abs(m[:, COL["z"]])
        for lo_, hi_ in ((0, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 99)):
            sel = (z >= lo_) & (z < hi_) & (np.sign(m[:, COL["z"]]) == -m[:, COL["side"]])
            if sel.sum() < 30:
                continue
            p = d["paths"][sel]
            side = m[sel, COL["side"]]
            gross_bp = float(np.mean(p[:, 57, 3] * side) * 1e4)           # 58-minute close-to-close move our way
            buckets.setdefault(w, {})[f"{lo_}-{hi_}"] = {"n": int(sel.sum()), "gross_bp_58m": round(gross_bp, 2)}
    report["gross_by_strength"] = buckets
    report["cost_round_trip_bp"] = round((2 * (TAKER + SLIP)) * 1e4, 1)
    passing = [n for n, x in report["variants"].items() if x["pass"]]
    report["recommendation"] = passing[-1] if passing else "NONE PASSES: keep Renata on V11.4 (paper); no V11.5 goes live"
    report["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print("recommendation:", report["recommendation"], flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--data-dir", required=True)
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--out", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--dir", required=True)
    a.add_argument("--out", default=str(PROJECT / "docs" / "V115_REVERSAL_STUDY.json"))
    args = ap.parse_args()
    return {"build": build, "analyze": analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
