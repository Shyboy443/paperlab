"""Read-only views behind the simplified UI: SYSTEM, the unified trader's trade list and the V4 arena.

Every payload is rebuilt from allow-listed fields. Nothing here reads a credential, an environment variable
that could hold one, the exchange client, order ids or a request header, and nothing can change anything.
"""
from __future__ import annotations

import math
import time
from typing import Any, Mapping

TRADE_FIELDS = ("entry_ts", "exit_ts", "side", "entry", "exit", "qty", "notional", "gross", "fees", "slippage",
                "funding", "net", "r", "exit_kind", "hold_s", "tier", "risk_pct", "jev_level", "jev_mult",
                "signal_ts", "maker_fees", "taker_fees", "funding_paid", "funding_received", "setup", "stop_pct",
                "expected_move_pct", "expected_funding_pct", "regime", "vol_band", "trend_c1", "trend_c2")
POSITIONING_FIELDS = ("funding_rate", "funding_pct_90d", "funding_chg_24h", "oi_chg_1h", "oi_chg_4h", "oi_chg_24h",
                      "basis", "long_ratio")
PREFIX = {"v3": ("v3", None), "v31d": ("v31", "DEVELOPMENT"), "v31t": ("v31", "TEST"),
          "v4d": ("v4", "DEVELOPMENT"), "v4t": ("v4", "TEST"), "v5d": ("v5", "DEVELOPMENT"), "v5t": ("v5", "TEST")}


def _finite(x: Any) -> Any:
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


def _runs(storage: Any, table: str) -> list[dict[str, Any]]:
    return {"v3": lambda: storage.v3_runs(10), "v31": lambda: storage.v31_runs(20), "v4": lambda: storage.v4_runs(20),
            "v5": lambda: storage.v5_runs(20)}[table]()


def _bots(storage: Any, table: str, run_id: str, **k: Any) -> list[dict[str, Any]]:
    return {"v3": storage.v3_bots, "v31": storage.v31_bots, "v4": storage.v4_bots, "v5": storage.v5_bots}[table](run_id, **k)


def _run_for(storage: Any, table: str, role: str | None) -> dict[str, Any] | None:
    runs = _runs(storage, table)
    for r in runs:
        if r.get("status") == "complete" and (role is None or str(r.get("dataset_role") or "").upper() == role):
            return r
    return None


def trader_trades(storage: Any, bot_id: str, limit: int = 300) -> dict[str, Any]:
    """The newest `limit` closed trades of one research bot (V3 / V3.1 / V4 / V5 store every trade). A V5 trade also
    carries its entry thesis, stop, expected funding, the positioning it was entered on and funding paid / received."""
    prefix, _, key = bot_id.partition(":")
    if prefix not in PREFIX or not key:
        return {"ok": True, "bot_id": bot_id, "trades": [], "stored": False,
                "note": "trade lists are stored for V3, V3.1, V4 and V5 research bots; other programs keep metrics only"}
    table, role = PREFIX[prefix]
    run = _run_for(storage, table, role)
    if run is None:
        return {"ok": False, "error": "no completed run for this bot"}
    rows = _bots(storage, table, run["run_id"], heavy=True, keys=[key])
    if not rows:
        return {"ok": False, "error": f"no bot {key} in {run['run_id']}"}
    trades = sorted(rows[0].get("trades") or [], key=lambda t: t.get("exit_ts") or 0)
    out = []
    for t in trades[-int(limit):]:
        row = {k: t.get(k) for k in TRADE_FIELDS}
        if isinstance(t.get("positioning"), dict):
            row["positioning"] = {k: t["positioning"].get(k) for k in POSITIONING_FIELDS}
        out.append(row)
    return _finite({"ok": True, "bot_id": bot_id, "run_id": run["run_id"], "stored": True, "total": len(trades),
                    "shown": len(out), "trades": out[::-1]})


def system_payload(state: Any) -> dict[str, Any]:
    """SYSTEM: execution and safety, stream and API health, research jobs, database, environment,
    and the developer details (fingerprints, run ids, schema / API version, data hashes)."""
    from app.core import inspection
    from app.core.storage import SCHEMA_VERSION
    from app.main import asset_version
    comp = getattr(state, "competition", None)
    storage = getattr(comp, "storage", None)
    rt = getattr(state, "realtime", None)
    health = rt.health_payload(private=False) if rt is not None else {}
    settings = getattr(state, "settings", None)
    safety = inspection._paper_status(state, storage)
    e = getattr(state, "engine", None)
    feed = None
    if e is not None and getattr(e, "feed", None) is not None:
        try:
            m = (e.feed.status() or {}).get("market") or {}
            feed = {"connected": bool(m.get("connected")), "age_s": m.get("last_msg_age_s")}
        except Exception:
            feed = {"connected": None}
    db: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    runs: list[dict[str, Any]] = []
    if storage is not None:
        try:
            db["journal_mode"] = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            pages = storage.conn.execute("PRAGMA page_count").fetchone()[0]
            size = storage.conn.execute("PRAGMA page_size").fetchone()[0]
            db["size_mb"] = round(pages * size / 1e6, 1)
        except Exception:
            pass
        try:
            for table, rows in (("V5", storage.v5_runs(10)), ("V4", storage.v4_runs(10)), ("V3.1", storage.v31_runs(10)),
                                ("V3", storage.v3_runs(5))):
                for r in rows:
                    cfg = r.get("config") or {}
                    runs.append({"program": table, "run_id": r["run_id"], "role": r.get("dataset_role"),
                                 "status": r.get("status"), "stage": r.get("stage"),
                                 "config_fingerprint": r.get("config_fingerprint"),
                                 "dataset_fingerprint": cfg.get("dataset_fingerprint"),
                                 "strategy_fingerprints": cfg.get("strategy_fingerprints"),
                                 "jev_fingerprints": cfg.get("jev_fingerprints"),
                                 "universe_fingerprint": cfg.get("universe_fingerprint"),
                                 "window": f"{cfg.get('trade_from')}..{cfg.get('trade_to')}"})
        except Exception:
            pass
    env = {}
    if settings is not None:
        env = {"venue": getattr(getattr(settings, "venue", None), "label", None), "dry_run": getattr(settings, "dry_run", None),
               "symbols": list(getattr(settings, "symbols", []) or []), "data_dir_on_volume": str(getattr(settings, "data_dir", "")) == "/data"}
    jev = health.get("jev") or {}
    shadow = health.get("shadow") or {}
    sfeed = shadow.get("feed") or {}
    return _finite({
        "ok": True, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "read_only": True,
        "safety": {**safety, "engine_market_feed": feed,
                   "risk_halt": safety.get("paused_reason"),
                   "liquidations_live": "none (paper books; per-bot liquidations are in each bot's metrics)"},
        "stream": health.get("stream"),
        "jev": {k: jev.get(k) for k in ("status", "configured", "enabled", "model_requested", "calls_since_boot",
                                        "errors_since_boot", "latency_p50_ms", "latency_p95_ms")},
        "forward_shadow": {k: shadow.get(k) for k in ("status", "enabled", "bots", "controls", "active", "warming",
                                                      "positions_open", "restarts", "error")},
        "data": {"forward_klines_connected": sfeed.get("klines"), "forward_klines_age_s": sfeed.get("klines_age_s"),
                 "forward_book_connected": sfeed.get("book"), "gaps_repaired": sfeed.get("gaps_repaired"),
                 "bars_live": sfeed.get("bars_live")},
        "database": db, "environment": env,
        "research": {"running": [r for r in runs if r["status"] == "running"], "runs": runs},
        "developer": {"api_version": "public-1", "asset_version": asset_version(), "schema_version": SCHEMA_VERSION,
                      "event_bus_published": (health.get("stream") or {}).get("published"),
                      "uptime_s": (health.get("stream") or {}).get("uptime_s")},
    })


# ---- V4 arena -----------------------------------------------------------------------------------------------

V4_BLOCKS = ("run_id", "protocol", "dataset_role", "window", "venue", "price_tape", "universe", "counts", "answers",
             "advanced_set", "passed", "states", "failure_modes", "edge_quality", "exits", "calibration", "funnels",
             "baselines", "selection_alpha", "jev_pooled", "attack", "capacity", "viability", "field", "raw_edge")
LB_FIELDS = ("rank", "key", "mode", "strategy_id", "family", "coin", "tf", "equity", "net_pnl", "net_return_pct", "trades",
             "trades_per_day", "exp_r", "gross_exp_r", "pf", "max_dd", "p_mean_le_0", "jev_actions", "selection_alpha",
             "state", "failure_mode", "exit_flags", "score")


def _v4_block(storage: Any, run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    s = run.get("summary") or {}
    out: dict[str, Any] = {"run": {"run_id": run["run_id"], "status": run.get("status"), "stage": run.get("stage"),
                                   "label": run.get("label"), "role": run.get("dataset_role"),
                                   "progress": run.get("progress")}}
    if not s:
        out["bots_done"] = len(storage.v4_bot_keys(run["run_id"]))
        return out
    out.update({k: s.get(k) for k in V4_BLOCKS})
    out["leaderboard_controls"] = [{k: r.get(k) for k in LB_FIELDS} for r in (s.get("leaderboard_controls") or [])[:40]]
    out["leaderboard_field"] = [{k: r.get(k) for k in LB_FIELDS} for r in (s.get("leaderboard_field") or [])[:60]]
    out["pairs"] = [{k: p.get(k) for k in ("pair_id", "strategy_id", "coin", "tf", "jev", "control", "always_take",
                                            "random", "delta_vs_control", "jev_actions", "attack_jev", "state",
                                            "failure_mode")} for p in (s.get("pairs") or [])]
    return out


def v4_payload(storage: Any) -> dict[str, Any]:
    runs = storage.v4_runs(20)
    dev = next((r for r in runs if r.get("dataset_role") == "DEVELOPMENT"), None)
    test = next((r for r in runs if r.get("dataset_role") == "TEST"), None)
    return _finite({"ok": True, "dev": _v4_block(storage, dev), "test": _v4_block(storage, test),
                    "note": None if (dev or test) else "no V4 run yet",
                    "venue_label": "Bybit linear costs & filters · Binance USD-M 1m tape"})


# ---- V5 arena (the CURRENT arena) -------------------------------------------------------------------------

V5_BLOCKS = ("run_id", "protocol", "dataset_role", "window", "venue", "price_tape", "data", "coins", "counts", "answers",
             "advanced_set", "passed", "states", "failure_modes", "labels", "viability", "costs", "selection_alpha",
             "jev_pooled", "attack", "baselines", "profitable_controls", "survivors", "economic")
V5_LB = ("rank", "key", "role", "strategy_id", "family", "coin", "horizon", "exit", "equity", "trades", "trades_per_day",
         "avg_hold_h", "gross", "fees", "slippage", "funding", "net", "net_per_trade", "net_per_day", "exp_r", "gross_exp_r",
         "pf", "max_dd", "p_mean_le_0", "jev", "selection_alpha", "label", "flags", "state", "failure_mode")
V5_FAMILY = ("strategy_id", "family", "horizon", "verdict", "verdict_baseline", "raw_edge", "economic_20_baseline",
             "economic_20", "economic_100_baseline", "costs_20", "costs_100", "horizons", "exit", "exit_grid", "by_regime",
             "by_vol_band", "by_side", "bots_20", "bots_100")


def _edge_brief(e: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not e:
        return None
    return {k: e.get(k) for k in ("measure", "books", "trades", "needed", "mean_r", "p_mean_le_0", "ci90",
                                  "subperiod_mean_r", "subperiod_trades", "coins", "mean_r_without_top_5pct", "checks",
                                  "passed")}


def _v5_block(storage: Any, run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    s = run.get("summary") or {}
    out: dict[str, Any] = {"run": {"run_id": run["run_id"], "status": run.get("status"), "stage": run.get("stage"),
                                   "label": run.get("label"), "role": run.get("dataset_role"),
                                   "progress": run.get("progress")}}
    field = run.get("field") or {}
    if not s:
        out["bots_done"] = len(storage.v5_bot_keys(run["run_id"]))
        if field.get("families"):                      # ANALYZE 1 is done: the raw-edge verdicts are already final
            out["families"] = [{k: f.get(k) for k in V5_FAMILY} for f in field["families"]]
        return out
    out.update({k: s.get(k) for k in V5_BLOCKS})
    fams = []
    for f in s.get("families") or []:
        row = {k: f.get(k) for k in V5_FAMILY}
        for k in ("raw_edge", "economic_20_baseline", "economic_20", "economic_100_baseline"):
            row[k] = _edge_brief(f.get(k))
        fams.append(row)
    out["families"] = fams
    out["leaderboard_controls"] = [{k: r.get(k) for k in V5_LB} for r in s.get("leaderboard_controls") or []]
    out["leaderboard_field"] = [{k: r.get(k) for k in V5_LB} for r in (s.get("leaderboard_field") or [])[:80]]
    out["pairs"] = [{k: p.get(k) for k in ("pair_id", "strategy_id", "coin", "horizon", "jev", "control", "always_take",
                                            "random", "delta_vs_control", "jev_actions", "jev_auc", "attack", "state",
                                            "failure_mode")} for p in s.get("pairs") or []]
    cap = s.get("capacity") or {}
    out["capacity"] = {"verdicts": cap.get("verdicts"), "note": cap.get("note"),
                       "rows": [{"pair_id": r.get("pair_id"), "verdict": r.get("verdict"),
                                 **{b: {k: (r.get(b) or {}).get(k) for k in ("trades", "net", "exp_r", "gross_exp_r", "label")}
                                    for b in ("20", "50", "100") if r.get(b)}} for r in cap.get("rows") or []]}
    return out


def v5_payload(storage: Any) -> dict[str, Any]:
    runs = storage.v5_runs(20)
    dev = next((r for r in runs if r.get("dataset_role") == "DEVELOPMENT"), None)
    test = next((r for r in runs if r.get("dataset_role") == "TEST"), None)
    return _finite({"ok": True, "dev": _v5_block(storage, dev), "test": _v5_block(storage, test),
                    "note": None if (dev or test) else "no V5 run yet",
                    "venue_label": "Bybit linear: native 1m tape, funding, open interest, basis; Bybit fees & filters"})
