"""Re-check of the V11 multi-coin studies for the end-of-replay pricing bug (found 2026-10-03, see app/live/v13_engine.py).

The shared ReplayEngine closes whatever is still open at the end of a replay (`_flatten`, exit_kind "time", reason "end
of replay") and at each book reset (`_reset_book`, exit_kind "reset") against `self._bar`, the LAST bar it processed. On
the V11 tape (every coin each minute, BTCUSDT the anchor LAST) the end-of-replay bar is BTC's, so the execution model
prices an alt's exit with BTC's tick and BTC's ATR (half spread + 0.02 x ATR, in the alt's price units): BTC-sized dollar
slippage on a coin worth cents. A reset fires on the first bar of the new day, the alphabetically first coin
(1000PEPEUSDT), so reset exits get PEPE's (tiny) slippage instead of their own. Live trading never takes either path.

The studies drop "reset" exits from their statistics but KEEP the end-of-replay ones (they are labelled "time"; the hold
study's `!= "end_of_run"` filter matches nothing, so it also keeps the resets -- deliberately, they are where a long
hold ends).

This script replays the study variants whose PASS / adopt rule uses the SECOND half (where the last day's trades fall):
v11_zap_study (main + --wider), v11_scanner_cost_study, v11_hold_study. It calls each study's own `run()` so the
statistics are computed by the study's own code, with in-memory instrumentation only (no module is edited):

    orig       the replay exactly as the study ran it (must reproduce the JSON numbers -- checked)
    fixed      the end-of-replay trades removed (V13's rule: they are labelled end_of_run and left out)
    repriced   the end-of-replay trades kept but priced on their OWN coin's last bar (V13's _own_bar)
    hold only: fixed + every reset exit priced on its own coin's bar (the hold study counts resets)

The scanner parameters are put back to their values at the time each study ran (the adopted min stops came later):
V11.3 min stop 0.6%, V11.1 / V11.2 0.8%.

    V11_STUDY_WORKERS=5 python scripts/v11_end_of_run_recheck.py      -> docs/V11_END_OF_RUN_RECHECK.json (~45 min)
    python scripts/v11_end_of_run_recheck.py --only V11.4/BASE         (one job, a quick check of the harness)
    python scripts/v11_end_of_run_recheck.py --summarize               (re-apply the decision rules to the saved runs)

Memory: the per-bar equity curve is dropped in the workers (no statistic here reads it), ~0.65 GB a worker.
"""
from __future__ import annotations

import copy
import dataclasses
import importlib
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
WORKERS = int(os.environ.get("V11_STUDY_WORKERS", "7"))
OUT = PROJECT / "docs" / "V11_END_OF_RUN_RECHECK.json"
AT_STUDY_MIN_STOP = {"V11.1": 0.008, "V11.2": 0.008, "V11.3": 0.006}     # before the 2026-10-02 adoptions

ZAP, COST, HOLD = "v11_zap_study", "v11_scanner_cost_study", "v11_hold_study"
# one replay per job; every (study, args) in a job is the SAME configuration, evaluated by that study's own statistics
JOBS: list[tuple[str, list[tuple[str, tuple]]]] = []
for _v in ("BASE", "MAKER_TP", "MINSTOP10", "STRICT5", "TOP2", "MAKER_TP+MINSTOP10", "MAKER_TP+MINSTOP15",
           "MAKER_TP+MINSTOP20"):
    JOBS.append((f"V11.3/{_v}", [(ZAP, ("V11.3", _v))] + ([(HOLD, ("V11.3", "X1"))] if _v == "BASE" else [])))
JOBS += [(f"V11.3/{_h}", [(HOLD, ("V11.3", _h))]) for _h in ("X2", "X4", "NONE")]
for _f in ("V11.2", "V11.1"):
    JOBS.append((f"{_f}/BASE", [(ZAP, (_f, "BASE")), (HOLD, (_f, "X1"))]))
    JOBS.append((f"{_f}/MAKER_TP", [(ZAP, (_f, "MAKER_TP")), (COST, (_f, "MAKER_TP"))]))
    JOBS += [(f"{_f}/{_v}", [(COST, (_f, _v))]) for _v in ("MAKER_TP+MINSTOP12", "MAKER_TP+MINSTOP15", "MAKER_TP+TOP2")]
    JOBS += [(f"{_f}/{_h}", [(HOLD, (_f, _h))]) for _h in ("X2", "X4", "NONE")]
JOBS.append(("V11.4/BASE", [(ZAP, ("V11.4", "BASE")), (HOLD, ("V11.4", "X1"))]))
JOBS.append(("V11.4/MAKER_TP", [(ZAP, ("V11.4", "MAKER_TP"))]))
JOBS += [(f"V11.4/{_h}", [(HOLD, ("V11.4", _h))]) for _h in ("X2", "X4", "NONE")]

_S: dict = {}          # per-worker state: the cached replay, the instrumented closes, the serving mode
_ORIG_PARAMS: dict = {}


def _at_study_params() -> None:
    """Give V11.1-V11.3 their minimum stops as of the studies (a dataclass subclass; the module is not touched)."""
    from app.strategies.v11 import scan
    for cls in scan.load_v11_scanners().values():
        base = _ORIG_PARAMS.setdefault(cls.id, cls.Params)
        if cls.id in AT_STUDY_MIN_STOP:
            cls.Params = dataclasses.make_dataclass(
                base.__name__, [("min_stop_pct", float, dataclasses.field(default=AT_STUDY_MIN_STOP[cls.id]))],
                bases=(base,))


def _install() -> None:
    """Wrap ReplayEngine.run / _flatten / _reset_book in memory: record what the forced closes did, and what the same
    close would have cost on the position's own coin; serve the cached replay to a study's second statistics pass."""
    from app.backtest.replay import ReplayEngine
    from app.execution.orders import Order
    if getattr(ReplayEngine, "_eor_recheck", False):
        return
    run0, flatten0, reset0 = ReplayEngine.run, ReplayEngine._flatten, ReplayEngine._reset_book

    def own_bar(self, symbol, bar):
        prev = getattr(self, "_prev_bar", {}) or {}
        return bar if bar.symbol == symbol else prev.get(symbol, bar)

    def probe(self, pos, bar, ts):
        price = self.ctx.prices.get(pos.symbol)
        if not price or bar is None:
            return None
        side = "SELL" if pos.side == "long" else "BUY"

        def px(b):
            o = Order(symbol=pos.symbol, side=side, qty=pos.qty, order_type="MARKET", decision_price=price,
                      signal_ts=ts, execute_at=ts)
            o.submit(ts)
            r = self.execution.execute(o, self._market_state(b, price, ts, pos.tf or "1m", complete=True))
            return r.avg_price if r.fills else None
        ob = own_bar(self, pos.symbol, bar)
        return {"position_id": pos.id, "symbol": pos.symbol, "side": pos.side, "tf": pos.tf, "ref": price,
                "qty_left": pos.qty, "risk_usd": pos.initial_risk_usd, "priced_on": bar.symbol, "own_bar": ob.symbol,
                "px_engine": px(bar), "px_own": px(ob)}

    def forced(self, sid, kind, ts, call):
        bar = getattr(self, "_bar", None)
        probes = {p.id: probe(self, p, bar, ts) for p in list(self.portfolio.positions_of(sid))}
        n_t, n_f = len(self.portfolio.closed_trades), len(self._fills)
        call()
        fills = {f.position_id: f for f in self._fills[n_f:]}
        for t in itertools.islice(self.portfolio.closed_trades, n_t, None):
            p, f = probes.get(t.position_id), fills.get(t.position_id)
            if p is None or f is None or p["px_own"] is None:
                continue
            sign = 1 if t.side == "long" else -1
            d_pnl = (p["px_own"] - f.price) * f.qty * sign
            d_fee = self.portfolio.fee_for(f.qty * p["px_own"]) - f.fee
            _S["forced"][id(t)] = {**p, "kind": kind, "exit_ts": t.exit_ts, "entry_ts": t.entry_ts,
                                   "fill_px": f.price, "fill_matches_probe": abs(f.price - p["px_engine"]) < 1e-12,
                                   "slip_bps_engine": round(abs(f.price / p["ref"] - 1) * 1e4, 2),
                                   "slip_bps_own": round(abs(p["px_own"] / p["ref"] - 1) * 1e4, 2),
                                   "r": t.r_multiple, "net": t.net,
                                   "r_own": (t.net + d_pnl - d_fee) / p["risk_usd"] if p["risk_usd"] else t.r_multiple,
                                   "net_own": t.net + d_pnl - d_fee}

    def _flatten(self, sid):
        forced(self, sid, "end_of_replay", self._now, lambda: flatten0(self, sid))

    def _reset_book(self, sid, balance, res, ts):
        forced(self, sid, "reset", ts, lambda: reset0(self, sid, balance, res, ts))

    def run(self, *a, **kw):
        if _S.get("cache") is None:
            _S["cache"] = run0(self, *a, **kw)
        return _serve(_S["cache"], _S["mode"])

    ReplayEngine.run, ReplayEngine._flatten, ReplayEngine._reset_book = run, _flatten, _reset_book
    ReplayEngine._eor_recheck = True

    # The per-bar equity curve (~4 M points: 95 days x 30 coins x 1440 minutes) is most of a replay's memory, and no
    # statistic of these studies reads it (they work from the closed trades and fills). Seven workers next to other
    # work ran the machine down to 1 GB free with it.
    from app.backtest.replay import ReplayResult
    init0 = ReplayResult.__init__

    def init(self, *a, **kw):
        init0(self, *a, **kw)
        self.equity = _NoEquity()
    ReplayResult.__init__ = init


class _NoEquity(list):
    def append(self, _item) -> None:
        pass


def _serve(res, mode: str):
    """The cached replay as one statistics pass should see it."""
    out = copy.copy(res)
    forced = _S["forced"]
    trades = []
    for t in res.trades:
        info = forced.get(id(t))
        eor = info is not None and info["kind"] == "end_of_replay"
        if eor and mode in ("fixed", "fixed_own_resets"):
            continue
        if info is not None and ((eor and mode == "repriced") or (info["kind"] == "reset" and mode == "fixed_own_resets")):
            t = dataclasses.replace(t, net=info["net_own"], r_multiple=info["r_own"])
        trades.append(t)
    out.trades = trades
    return out


def work(job: tuple[str, list[tuple[str, tuple]]]) -> dict:
    key, calls = job
    _at_study_params()
    _install()
    _S.clear()
    _S.update(cache=None, forced={}, mode="orig")
    t0 = time.time()
    stats = []
    for mod, args in calls:
        m = importlib.import_module(mod)
        row = {"study": mod, "args": list(args)}
        modes = ("orig", "fixed", "repriced") + (("fixed_own_resets",) if mod == HOLD else ())
        for mode in modes:
            _S["mode"] = mode
            row[mode] = m.run(tuple(args))
        stats.append(row)
    res = _S["cache"]
    eor = [dict(v) for v in _S["forced"].values() if v["kind"] == "end_of_replay"]
    resets = [v for v in _S["forced"].values() if v["kind"] == "reset"]
    return {"job": key, "elapsed_s": round(time.time() - t0, 1), "end_of_replay_trades": eor,
            "resets": {"n": len(resets), "priced_on": sorted({v["priced_on"] for v in resets}),
                       "mean_r": _mean([v["r"] for v in resets]), "mean_r_own": _mean([v["r_own"] for v in resets]),
                       "mean_slip_bps_engine": _mean([v["slip_bps_engine"] for v in resets]),
                       "mean_slip_bps_own": _mean([v["slip_bps_own"] for v in resets]),
                       "fills_match_probe": all(v["fill_matches_probe"] for v in resets)},
            "trades_total": len(res.trades), "stats": stats}


def _mean(xs: list) -> float | None:
    return round(sum(xs) / len(xs), 5) if xs else None


# -- the decisions, re-applied --------------------------------------------------------------------------------------------
# Every other study that replays the multi-coin tape chose on the DISCOVERY half only (or decided nothing). An
# end-of-replay trade opens on the tape's last day (books reset every UTC midnight), 2026-09-28, long after the discovery
# half ends (2026-08-15), so those decisions cannot move; only their reported confirmation / whole-window numbers can.
SCREEN = {
    "scripts/v11_zap_study.py (+ --wider)": "rechecked: adopt rule uses BOTH halves",
    "scripts/v11_scanner_cost_study.py": "rechecked: adopt rule uses BOTH halves",
    "scripts/v11_hold_study.py": "rechecked: rule uses BOTH halves + PF (its outcome was not adopted: V11 kept unchanged); "
                                 "it also COUNTS reset exits, which are priced on 1000PEPEUSDT's bar (fixed_own_resets)",
    "scripts/v11_ladder_study.py (main, --equal)": "immune: picks rungs on the DISCOVERY half",
    "scripts/v11_ladder_study.py (--guard, --exits, --final)": "no pre-registered rule (informational); not rerun",
    "scripts/v11_tp1_study.py (main, --level, --spacing)": "immune: picks on the DISCOVERY half",
    "scripts/v11_breakout_stop_study.py": "immune: LEVEL vs LOW3 decided on the DISCOVERY half",
    "scripts/v11_activity_check.py": "informational (plain ReplayEngine, keeps end-of-replay exits); not rerun",
    "scripts/v11_scan_study.py, v11_feature_study.py, v11_reversal_study.py": "not affected: vectorised, no ReplayEngine",
    "scripts/v12_bizzy_study.py": "not affected: vectorised, no ReplayEngine",
    "scripts/v13_snapback_study.py": "not affected: LimitEntryEngineV13 prices on the coin's own bar and drops end_of_run",
    "scripts/v8_*.py": "not affected: one coin per replay (only_symbol), so the last bar is the coin's own",
}


def _decisions(st: dict, mode: str) -> dict:
    """The three studies' pre-registered rules, as their scripts apply them, on one statistics mode."""
    def g(study, f, v):
        d = st[(study, f, v)]
        return d.get(mode) or d["fixed"]

    def beats(r, b):
        return r["half1"]["net_r"] > b["half1"]["net_r"] and r["half2"]["net_r"] > b["half2"]["net_r"]
    zap = {}
    for f in ("V11.1", "V11.2", "V11.3", "V11.4"):
        vs = ("MAKER_TP", "MINSTOP10", "STRICT5", "TOP2") if f == "V11.3" else ("MAKER_TP",)
        zap[f] = {"passing": [v for v in vs if beats(g(ZAP, f, v), g(ZAP, f, "BASE"))]}
    ok = zap["V11.3"]["passing"]
    if len(ok) > 1:
        combo, best = "+".join(ok), max(ok, key=lambda v: g(ZAP, "V11.3", v)["all"]["net_r"])
        zap["V11.3"]["adopt"] = (combo if (ZAP, "V11.3", combo) in st and g(ZAP, "V11.3", combo)["all"]["net_r"] >
                                 g(ZAP, "V11.3", best)["all"]["net_r"] else best)
    else:
        zap["V11.3"]["adopt"] = ok[0] if ok else "BASE"
    for f in ("V11.1", "V11.2", "V11.4"):
        zap[f]["adopt"] = "MAKER_TP" if zap[f]["passing"] else "BASE"
    pick = g(ZAP, "V11.3", "MAKER_TP+MINSTOP10")
    wide = [v for v in ("MAKER_TP+MINSTOP15", "MAKER_TP+MINSTOP20") if beats(g(ZAP, "V11.3", v), pick)]
    zap["V11.3"]["wider_followup"] = {"passing": wide, "adopt": max(wide, key=lambda v: g(ZAP, "V11.3", v)["all"]["net_r"])
                                      if wide else "MAKER_TP+MINSTOP10"}
    cost = {}
    for f in ("V11.1", "V11.2"):
        base = g(COST, f, "MAKER_TP")
        ok = [v for v in ("MAKER_TP+MINSTOP12", "MAKER_TP+MINSTOP15", "MAKER_TP+TOP2") if beats(g(COST, f, v), base)]
        mins = [v for v in ok if "MINSTOP" in v]
        if len(mins) > 1:
            keep = max(mins, key=lambda v: g(COST, f, v)["all"]["net_r"])
            ok = [v for v in ok if "MINSTOP" not in v or v == keep]
        cost[f] = ok
    hold = {}
    for f in ("V11.1", "V11.2", "V11.3", "V11.4"):
        base = g(HOLD, f, "X1")
        ok = [h for h in ("X2", "X4", "NONE") if beats(g(HOLD, f, h), base)
              and (g(HOLD, f, h)["all"]["pf"] or 0) >= (base["all"]["pf"] or 0)]
        hold[f] = max(ok, key=lambda h: g(HOLD, f, h)["all"]["net_r"]) if ok else "X1"
    return {"zap": zap, "scanner_cost": cost, "hold": hold}


def summarize(doc: dict) -> dict:
    docs = PROJECT / "docs"
    zap_j = json.loads((docs / "V11_ZAP_STUDY.json").read_text())
    cost_j = json.loads((docs / "V11_SCANNER_COST_STUDY.json").read_text())
    hold_j = json.loads((docs / "V11_HOLD_STUDY.json").read_text())
    published = {ZAP: {(r["family"], r["variant"]): r for r in zap_j["runs"]},
                 COST: {(r["family"], r["variant"]): r for r in cost_j["runs"]},
                 HOLD: {(r["family"], r["hold"]): r for r in hold_j["runs"]}}
    st, runs, same = {}, [], 0
    for j in doc["jobs"]:
        for s in j["stats"]:
            st[(s["study"], *s["args"])] = s
            same += s["orig"] == published[s["study"]][tuple(s["args"])]
            line = {"study": s["study"], "family": s["args"][0], "variant": s["args"][1],
                    "end_of_replay_trades": [{k: t[k] for k in ("symbol", "side", "tf", "ref", "fill_px", "px_own",
                                                                "slip_bps_engine", "slip_bps_own", "r", "r_own")}
                                             for t in j["end_of_replay_trades"]]}
            for mode in ("orig", "fixed", "repriced", "fixed_own_resets"):
                if mode in s:
                    line[mode] = {h: {k: s[mode][h].get(k) for k in ("trades", "net_r", "pf", "net_usdt")}
                                  for h in ("half1", "half2", "all")}
            runs.append(line)
    recorded = {"zap": zap_j["chosen"], "scanner_cost": cost_j["passing"], "hold": hold_j["chosen"]}
    dec = {m: _decisions(st, m) for m in ("orig", "fixed", "repriced", "fixed_own_resets")}
    return {"reproduced": f"{same} of {sum(len(j['stats']) for j in doc['jobs'])} study results identical to the "
                          "published JSON (orig mode)",
            "studies_screened": SCREEN, "decisions_recorded": recorded, "decisions": dec,
            "runs": runs}


def main() -> None:
    if "--summarize" in sys.argv:
        doc = json.loads(OUT.read_text())
        doc["summary"] = summarize(doc)
        OUT.write_text(json.dumps(doc, indent=1, default=str))
        print(json.dumps(doc["summary"]["decisions"], indent=1))
        return
    jobs = JOBS
    if "--only" in sys.argv:
        want = sys.argv[sys.argv.index("--only") + 1].split(",")
        jobs = [j for j in JOBS if j[0] in want]
    rows = []
    with ProcessPoolExecutor(min(WORKERS, len(jobs))) as ex:
        futs = {ex.submit(work, j): j[0] for j in jobs}
        for fut in as_completed(futs):
            r = fut.result()
            rows.append(r)
            eor = ", ".join(f"{t['symbol']} {t['side']} R {t['r']:.2f} (own bar {t['r_own']:.2f})"
                            for t in r["end_of_replay_trades"]) or "none"
            print(f"[{time.strftime('%H:%M:%S')}] {r['job']:24s} {r['elapsed_s']:6.0f} s  end-of-replay: {eor}", flush=True)
    rows.sort(key=lambda r: [j[0] for j in JOBS].index(r["job"]))
    doc = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "jobs": rows}
    if "--only" in sys.argv:
        path = OUT.with_name("V11_END_OF_RUN_RECHECK.partial.json")
    else:
        path, doc["summary"] = OUT, summarize(doc)
    path.write_text(json.dumps(doc, indent=1, default=str))
    print("wrote", path)


if __name__ == "__main__":
    main()
