cd /srv/paperlab 2>/dev/null || cd /app
python3 - <<'PY'
import json, sqlite3, gzip, base64, io
c = sqlite3.connect("file:/data/v8-forward.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
exps = [dict(r) for r in c.execute("SELECT experiment_id, created_ts, forward_start_ms, warmup_from_ms, status FROM fwd6_experiments ORDER BY created_ts")]
exp = exps[-1]
eid, t0, warm = exp["experiment_id"], exp["forward_start_ms"], exp["warmup_from_ms"]
out = {"experiments": exps, "experiment": exp}
out["bots"] = [dict(r) for r in c.execute("SELECT bot_key, role, pair_id, strategy_id, symbol, updated_ts, state_json FROM fwd6_bots WHERE experiment_id=?", (eid,))]
out["trades"] = [dict(r) for r in c.execute("SELECT * FROM fwd6_trades WHERE experiment_id=?", (eid,))]
out["events"] = [dict(r) for r in c.execute("SELECT id, session_id, ts, kind, bot_key, role, symbol, data_json FROM fwd6_events WHERE experiment_id=? AND kind IN ('open','tp','closed','eliminated','system','error')", (eid,))]
out["decisions"] = [dict(r) for r in c.execute("SELECT id, bot_key, role, symbol, side, signal_ts, decision_instant_ms, final_action, final_level, reason, error_code, tier, legal_min_risk_pct, quality, health, source, rederived, outcome_kind, outcome_net, outcome_r FROM fwd6_decisions WHERE experiment_id=?", (eid,))]
out["sessions"] = [dict(r) for r in c.execute("SELECT session_id, created_ts, live_ts, heartbeat_ts, ended_ts, status FROM fwd6_sessions WHERE experiment_id=?", (eid,))]
syms = sorted({b["symbol"] for b in out["bots"]})
lo = min(warm or t0, t0 - 6 * 86400000)
out["bars"] = {s: [list(r) for r in c.execute("SELECT open_time, open, high, low, close, volume, turnover, bid, ask, half_spread_bps, source FROM fwd6_bars WHERE symbol=? AND open_time>=? ORDER BY open_time", (s, lo))] for s in syms}
out["funding"] = [list(r) for r in c.execute("SELECT symbol, ts, rate FROM fwd6_funding_charged ORDER BY ts")]
buf = io.BytesIO()
with gzip.GzipFile(fileobj=buf, mode="wb") as g: g.write(json.dumps(out).encode())
print(base64.b64encode(buf.getvalue()).decode())
PY
