"""Paper vs replay parity for the live V8.3 bots (read-only research).

Rebuilds each bot exactly as app/live/v8_worker.py does (LevelMakerEngineV8, settings_v6, SizingV6, EXECUTION_V8,
maker TPs, frozen rules, ladder_class for +LADDER) and replays the experiment's OWN recorded 1m bars (fwd6_bars),
live half-spreads and charged funding from T0. The live gate is not reproduced (offline there is no LATE / STALE /
downtime skip); live decisions are listed so each difference can be attributed.

    MSYS_NO_PATHCONV=1 railway ssh --service paperlab -- sh -c "cat > /tmp/d.sh && sh /tmp/d.sh" < scripts/v8_live_dump.sh > dump.b64
    (decode: base64 -> gzip -> v8_live.json), then: python scripts/v8_live_parity.py v8_live.json out.json [LADDER,CONTROL]
"""
import dataclasses
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.backtest.funding import FundingSchedule
from app.competition import v8_config as v8
from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
from app.core.types import Candle, MarketRules
from app.live.v8_engine import LevelMakerEngineV8
from app.strategies.v8.ladder import ladder_class

iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%m-%d %H:%M:%S") if t else "-"
src, out_path = sys.argv[1], sys.argv[2]
roles = sys.argv[3].split(",") if len(sys.argv) > 3 else ["LADDER", "CONTROL"]
D = json.load(open(src))
T0 = D["experiment"]["forward_start_ms"]
FREEZE = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "V8_FREEZE.json"))
RULES = {s: MarketRules(**r) for s, r in FREEZE["rules"].items()}


def quotes_for(sym):
    rows = [(r[0], r[9]) for r in D["bars"][sym] if r[9] is not None]
    ts = [r[0] for r in rows]

    def q(symbol, t):
        i = bisect_right(ts, int(t))
        if not i or int(t) - ts[i - 1] > 180_000:
            return None
        return rows[i - 1][1]
    return q


def candles(sym):
    for r in D["bars"][sym]:
        t, o, h, l, c, v, turnover = r[:7]
        yield Candle(sym, "1m", t, o, h, l, c, v, t + 59_999, True, turnover or 0.0, 0, "historical", 0.0)


def replay(bot):
    sym = bot["symbol"]
    base = v8.load_v8()[bot["strategy_id"]]
    cls = (ladder_class(base) if bot["role"] == "LADDER" else base).for_class(bot.get("horizon") or "SCALP")
    fund = FundingSchedule({sym: [(int(ts), float(rate)) for s, ts, rate in D["funding"] if s == sym]})
    eng = LevelMakerEngineV8(settings_v6(), [sym], rules={sym: RULES[sym]}, seed=7, funding=fund,
                             execution=v8.EXECUTION_V8, fees=FEES_V6, fee_source="schedule",
                             sizing=SizingV6(RULES, jev=False), leverage_policy="needed",
                             max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None, quotes=quotes_for(sym), maker_tp=True)
    res = eng.run(cls, candles(sym), since_ms=T0, leverage=20, signal_tf="5m", only_symbol=sym)
    fills = defaultdict(list)
    for f in res.fills:
        fills[f.position_id].append({"kind": f.kind, "ts": f.ts, "price": f.price, "qty": f.qty, "fee": f.fee,
                                     "pnl": f.realized_pnl, "meta": {k: v for k, v in (f.meta or {}).items() if k in ("fraction", "remaining", "tp_index", "stop", "sizing")}})
    trades = []
    for t in sorted(res.trades, key=lambda t: t.entry_ts):
        if t.entry_ts < T0:
            continue
        fl = fills.get(t.position_id, [])
        funding = sum(x["pnl"] for x in fl if x["kind"] == "funding")
        sizing = next((x["meta"].get("sizing") for x in fl if x["kind"] == "entry"), None) or {}
        trades.append({"entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "side": t.side, "qty": t.qty, "entry": t.entry_price,
                       "exit": t.exit_price, "kind": t.exit_kind, "fees": t.fees, "funding": funding, "net": t.net + funding,
                       "r": t.r_multiple, "risk_usd": getattr(t, "initial_risk_usd", None),
                       "tier": sizing.get("tier") if isinstance(sizing, dict) else None,
                       "fills": [x for x in fl if x["kind"] != "funding"]})
    return {"trades": trades, "rejects": dict(res.rejects), "final_equity": res.equity[-1][1] if res.equity else None,
            "halted": res.halted}


def live_rows(bot_key):
    rows = [t for t in D["trades"] if t["bot_key"] == bot_key]
    return sorted(rows, key=lambda t: t["entry_ts"] or 0)


out = {"experiment": D["experiment"], "bots": {}}
for bot in sorted(D["bots"], key=lambda b: (b["symbol"], b["role"])):
    if bot["role"] not in roles:
        continue
    rep = replay(bot)
    live = live_rows(bot["bot_key"])
    real = [t for t in live if not t["counterfactual"] and not t["rederived"]]
    by_entry = {(t["entry_ts"], t["side"]): t for t in real}
    rep_by = {(t["entry_ts"], t["side"]): t for t in rep["trades"]}
    matched = sorted(set(by_entry) & set(rep_by))
    only_live, only_rep = sorted(set(by_entry) - set(rep_by)), sorted(set(rep_by) - set(by_entry))
    diffs = []
    for k in matched:
        a, b = by_entry[k], rep_by[k]
        d = {f: (a[g], b[f]) for f, g in (("exit_ts", "exit_ts"), ("kind", "exit_kind"), ("exit", "exit_price"),
                                           ("qty", "qty"), ("entry", "entry_price"))
             if not (a[g] == b[f] or (isinstance(b[f], float) and a[g] is not None and abs(a[g] - b[f]) <= 1e-9 * max(1, abs(b[f]))))}
        dn = (a["net"] or 0) - b["net"]
        if d or abs(dn) > 1e-6:
            diffs.append({"entry": iso(k[0]), "side": k[1], "net_live": a["net"], "net_replay": b["net"], **{f: v for f, v in d.items()}})
    out["bots"][bot["bot_key"]] = {
        "symbol": bot["symbol"], "role": bot["role"], "live_rows": len(live),
        "live_real": len(real), "live_counterfactual": sum(1 for t in live if t["counterfactual"]),
        "live_rederived": sum(1 for t in live if t["rederived"]), "replay": len(rep["trades"]),
        "matched": len(matched), "matched_identical": len(matched) - len(diffs),
        "only_live": [(iso(k[0]), k[1]) for k in only_live], "only_replay": [(iso(k[0]), k[1]) for k in only_rep],
        "diffs": diffs[:40], "net_live": sum(t["net"] or 0 for t in real), "net_replay": sum(t["net"] for t in rep["trades"]),
        "replay_rejects": rep["rejects"], "replay_trades": rep["trades"]}
    b = out["bots"][bot["bot_key"]]
    print(f"{bot['symbol']:9s} {bot['role']:7s} live {b['live_real']:3d} (+{b['live_counterfactual']} cf, +{b['live_rederived']} rederived) "
          f"replay {b['replay']:3d} matched {b['matched']:3d} identical {b['matched_identical']:3d} only-live {len(only_live)} "
          f"only-replay {len(only_rep)} | net live {b['net_live']:+.3f} replay {b['net_replay']:+.3f}")
json.dump(out, open(out_path, "w"), default=str)
