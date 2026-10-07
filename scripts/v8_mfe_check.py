"""How far into profit did V8's closed trades go before they closed? (max favourable excursion, in R)

    python scripts/v8_mfe_check.py [experiment_id]        (on the server: reads v8-forward.db)

For every closed paper trade: R = |entry - initial stop| (from the recorded open event), and the best price reached
between entry and exit from the stored 1m bars. Reports, for LOSERS, how many had been at +0.5R / +0.75R / +1R / +1.5R
first -- the "it was in profit, then reversed into the stop" pattern -- and the same for winners and time exits.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys


def main() -> None:
    db = sqlite3.connect(os.path.join(os.environ.get("DATA_DIR") or "data", "v8-forward.db"))
    db.row_factory = sqlite3.Row
    eid = sys.argv[1] if len(sys.argv) > 1 else db.execute(
        "SELECT experiment_id FROM fwd6_experiments ORDER BY created_ts DESC LIMIT 1").fetchone()[0]
    stops = {}
    for (raw,) in db.execute("SELECT data_json FROM fwd6_events WHERE experiment_id=? AND kind='open'", (eid,)):
        d = json.loads(raw)
        stops[(d.get("bot_key"), int(d.get("ts") or 0))] = d.get("stop")
    trades = [dict(r) for r in db.execute("SELECT * FROM fwd6_trades WHERE experiment_id=? AND counterfactual=0", (eid,))]
    out = {"experiment": eid, "trades": 0, "groups": {}}
    rows = []
    for t in trades:
        stop = stops.get((t["bot_key"], int(t["entry_ts"])))
        entry = float(t["entry_price"])
        if not stop or entry <= 0:
            continue
        risk = abs(entry - float(stop))
        if risk <= 0:
            continue
        bars = db.execute("SELECT high, low FROM fwd6_bars WHERE symbol=? AND open_time>=? AND open_time<?",
                          (t["symbol"], int(t["entry_ts"]) // 60_000 * 60_000, int(t["exit_ts"]))).fetchall()
        if not bars:
            continue
        long = t["side"] == "long"
        best = max(b[0] for b in bars) if long else min(b[1] for b in bars)
        mfe = (best - entry) / risk if long else (entry - best) / risk
        kind = "loser" if float(t["net"]) < 0 else "winner"
        rows.append({"kind": kind, "exit": t["exit_kind"], "mfe_r": mfe, "net": float(t["net"]), "role": "JEV" if t["bot_key"].endswith("+JEV") else "CONTROL"})
    out["trades"] = len(rows)
    for kind in ("loser", "winner"):
        g = [r for r in rows if r["kind"] == kind]
        if not g:
            continue
        out["groups"][kind] = {"n": len(g), "median_mfe_r": round(statistics.median(r["mfe_r"] for r in g), 2),
                               **{f"reached_{x}R": round(sum(1 for r in g if r["mfe_r"] >= x) / len(g), 3) for x in (0.5, 0.75, 1.0, 1.5)},
                               "exits": {k: sum(1 for r in g if r["exit"] == k) for k in sorted({r["exit"] for r in g})}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
