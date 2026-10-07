"""Server-rendered public inspection report.

`/public/competition` is a JavaScript application. Plenty of automated readers do not run
JavaScript, so to them that page is the word "loading". This renders the same data as ordinary
HTML, complete before it leaves the server, with real anchors between pages.

It deliberately imports its field allow-lists and payload builders from `app.core.api_public`
rather than re-deriving them. Two independent definitions of "what is safe to publish" would drift,
and the one that drifts is the one that leaks.

Rendering rules, because the point is machine inspection:

* Values live in the HTML text, never in CSS `content` or a canvas.
* A missing number renders as a word -- NOT RUN, NOT ENTERED, UNAVAILABLE -- never as an empty
  cell, "null", "NaN" or "Infinity".
* Anything genuinely broken (a NaN metric, a null where a number belongs) is COUNTED and shown in
  the data-quality section instead of being quietly formatted away.
"""
from __future__ import annotations

import html
import json
import math
import time
from typing import Any, Iterable

from app.core.api_public import (METRIC_FIELDS, _diagnostics, _is_simulated, _pick, _public_fill)
from app.core.arena_view import arena_bot_payload, arena_payload
from app.core.jev_view import jev_pair_payload, jev_payload
from app.core.report_v3 import v3_data, v3_html, v3_text
from app.core.report_v31 import v31_data, v31_html, v31_text
from app.core.report_v4 import v4_data, v4_html, v4_text
from app.core.report_v5 import v5_data, v5_html, v5_text
from app.core.report_v6 import v6_data, v6_html, v6_text

DASH = "—"
TOP_N = 30
RECENT_FILLS = 25


# ---- value formatting ---------------------------------------------------------------------------

def _bad(v: Any) -> bool:
    """A number that must never reach a reader as-is."""
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def fmt_num(v: Any, digits: int = 2, suffix: str = "", missing: str = DASH) -> str:
    if v is None:
        return missing
    if _bad(v):
        return "INVALID"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}{suffix}"
    return html.escape(str(v))


def fmt_money(v: Any, signed: bool = False, missing: str = DASH) -> str:
    if v is None:
        return missing
    if _bad(v):
        return "INVALID"
    return f"{'+' if signed and v > 0 else ''}{v:,.2f}"


def fmt_pct(v: Any, digits: int = 1, missing: str = DASH) -> str:
    if v is None:
        return missing
    if _bad(v):
        return "INVALID"
    return f"{v * 100:.{digits}f}%"


def fmt_pf(v: Any) -> str:
    if v is None:
        return DASH
    if _bad(v):
        return "INF" if isinstance(v, float) and math.isinf(v) else "INVALID"
    return f"{v:.2f}"


def fmt_ts(ms: Any) -> str:
    if not ms:
        return DASH
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ms) / 1000))
    except (TypeError, ValueError, OSError):
        return DASH


def _sanitize(obj: Any) -> Any:
    """JSON-safe: NaN/Infinity become null so the embedded blob stays parseable."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if _bad(obj):
        return None
    return obj


# ---- data gathering ------------------------------------------------------------------------------

def collect(svc: Any, jev: Any = None, shadow: Any = None, v6: Any = None) -> dict[str, Any]:
    """Everything the report shows, using the public allow-lists only."""
    storage = svc.storage
    season_runs = storage.competition_runs(limit=1)
    season_id = season_runs[0]["run_id"] if season_runs else None
    season = storage.competition_run(season_id) if season_id else None
    season_rows: list[dict[str, Any]] = []
    if season_id:
        for c in storage.competition_competitors(season_id):
            m = c.get("metrics")
            q = c.get("qualification") or {}
            season_rows.append({
                "key": c.get("key") or c.get("strategy_id"), "strategy_id": c.get("strategy_id"),
                "name": c.get("name"), "state": c.get("state"), "score": c.get("score"),
                "leverage": c.get("leverage"), "skipped": c.get("skipped") or "",
                "ran": bool(m), "reasons": q.get("reasons") or [],
                "metrics": _pick(m, METRIC_FIELDS) if m else None,
            })
    season_rows.sort(key=lambda r: (r["score"] is not None, r["score"] if r["score"] is not None else 0),
                     reverse=True)
    for i, r in enumerate(season_rows, 1):
        r["rank"] = i

    val_runs = storage.validation_runs(limit=1)
    validation = val_runs[0] if val_runs else None
    val_rows: list[dict[str, Any]] = []
    if validation:
        for c in storage.validation_competitors(validation["run_id"]):
            full = storage.validation_competitor(validation["run_id"], c["key"]) or {}
            m = full.get("oos_metrics")
            w = full.get("walk_forward") or {}
            mcr = full.get("monte_carlo") or {}
            sr = full.get("stress") or {}
            q = full.get("qualification") or {}
            val_rows.append({
                "key": c["key"], "strategy_id": c["strategy_id"], "name": c.get("name"),
                "leverage": c["leverage"], "state": c["state"], "score": c.get("score"),
                "ran": bool(m), "skipped": full.get("skipped") or "",
                "metrics": _pick(m, METRIC_FIELDS) if m else None,
                "profitable_windows": w.get("profitable_windows"),
                "active_windows": w.get("active_windows"), "total_windows": w.get("total_windows"),
                "mc_ran": bool(mcr.get("ran")), "mc_ruin": mcr.get("ruin_probability"),
                "stress_ran": bool(sr.get("ran")), "stress_survives": sr.get("survives"),
                "reasons": q.get("reasons") or [],
            })
        val_rows.sort(key=lambda r: (r["score"] is not None, r["score"] if r["score"] is not None else 0),
                      reverse=True)
        for i, r in enumerate(val_rows, 1):
            r["rank"] = i

    arena = arena_payload(storage)
    jev_data = jev_payload(storage, health=jev.health() if jev is not None else None)
    from app.core.shadow_view import shadow_payload
    shadow_data = shadow_payload(storage, shadow)
    arena_runs = []
    for r in storage.arena_runs(limit=20):
        full = storage.arena_run(r["run_id"], heavy=True) or {}
        cfg, sm = full.get("config") or {}, full.get("summary") or {}
        arena_runs.append({"run_id": r["run_id"], "label": r.get("label"), "status": r.get("status"),
                           "first_month": r.get("first_month"), "last_month": r.get("last_month"),
                           "dataset_role": cfg.get("dataset_role") or "", "params_version": cfg.get("params_version") or "v1",
                           "active_bots": sm.get("active_bots"), "advanced": sm.get("advanced"),
                           "advanced_keys": sm.get("advanced_keys") or [],
                           "profitable": sm.get("profitable_after_costs"), "trades": sm.get("total_trades"),
                           "gross": sm.get("total_gross_pnl"), "fees": sm.get("total_fees"),
                           "slippage": sm.get("total_slippage"), "net": sm.get("total_net_pnl")})
    data = {
        "diagnostics": _diagnostics(svc, {
            "arena_run_id": ((arena or {}).get("run") or {}).get("run_id"),
            "arena_config_fingerprint": ((arena or {}).get("run") or {}).get("config_fingerprint"),
            "competition_run_id": season_id,
            "season_fingerprint": (season or {}).get("fingerprint"),
            "validation_run_id": (validation or {}).get("run_id"),
            "dataset_fingerprint": (validation or {}).get("dataset_fingerprint"),
            "config_fingerprint": (validation or {}).get("config_fingerprint"),
        }),
        "season": season, "season_rows": season_rows,
        "validation": validation, "validation_rows": val_rows,
        "arena": arena,
        "jev": jev_data,
        "shadow": shadow_data,
        "arena_runs": arena_runs,
        "candidates": _candidates_data(storage, shadow_data),
        "v6": v6_data(storage, v6),
        "v5": v5_data(storage),
        "v4": v4_data(storage),
        "v31": v31_data(storage),
        "v3": v3_data(storage),
    }
    data["quality"] = _quality(data)
    data["qualification"] = _qualification(val_rows or season_rows)
    return data


def _quality(data: dict[str, Any]) -> dict[str, Any]:
    """Count what is missing or malformed instead of formatting it away."""
    rows = list(data["season_rows"]) + list(data["validation_rows"])
    null_metrics = sum(1 for r in rows if r["metrics"] is None)
    nan = inf = nulls_inside = 0
    for r in rows:
        for v in (r["metrics"] or {}).values():
            if isinstance(v, float):
                if math.isnan(v):
                    nan += 1
                elif math.isinf(v):
                    inf += 1
            elif v is None:
                nulls_inside += 1
    val = data["validation"] or {}
    total = val.get("total_competitors") or 0
    done = len(data["validation_rows"])
    warnings: list[str] = []
    not_entered = [r for r in rows if not r["ran"]]
    if not_entered:
        warnings.append(f"{len(not_entered)} competitors NOT ENTERED (missing data feeds)")
    if total and done < total:
        warnings.append(f"{total - done} validation competitors pending ({done}/{total} complete)")
    if not any(r.get("mc_ran") for r in data["validation_rows"]):
        warnings.append("Monte Carlo not yet run for any competitor")
    if not any(r.get("stress_ran") for r in data["validation_rows"]):
        warnings.append("Stress testing not yet run for any competitor")
    if nan or inf:
        warnings.append(f"{nan} NaN and {inf} infinite metric values detected")
    arena = data.get("arena") or {}
    if arena:
        s = arena.get("summary") or {}
        active, need = s.get("active_bots") or 0, s.get("min_active_bots") or 10
        if active < need:
            warnings.append(f"arena has {active} active bots, below the minimum of {need}: "
                            "INSUFFICIENT_COMPETITORS, no winner declared")
        silent = [b["key"] for b in arena.get("bots") or []
                  if (b.get("metrics") or {}).get("trades") == 0]
        if silent:
            warnings.append(f"{len(silent)} active arena bots made no trades: {', '.join(silent)}")
    else:
        warnings.append("no specialist arena run has been published yet")
    return {"competitors_with_null_metrics": null_metrics, "nan_metrics": nan,
            "infinite_metrics": inf, "null_metric_fields": nulls_inside,
            "not_entered": len(not_entered), "validation_pending": max(0, total - done),
            "warnings": warnings}


FAILURE_LABELS = {
    "min_net_profit": "negative net PnL after costs",
    "min_expectancy_r": "negative expectancy",
    "min_profit_factor": "profit factor below threshold",
    "max_drawdown_pct": "excessive drawdown",
    "min_profitable_oos_ratio": "too few profitable OOS windows",
    "min_active_oos_ratio": "too few active OOS windows",
    "max_symbol_concentration": "symbol concentration",
    "max_profit_concentration": "single-trade concentration",
    "max_ruin_probability": "Monte Carlo ruin",
    "stress_survives": "stress testing",
}


def _qualification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualified = [r["key"] for r in rows
                 if r["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")]
    counts: dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for r in rows:
        if r["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE") or not r["ran"]:
            continue
        if r["state"] == "INSUFFICIENT_SAMPLE":
            bump("insufficient trades")
            continue
        if r["state"] == "DISQUALIFIED":
            bump("liquidation")
            continue
        for reason in r.get("reasons") or []:
            bump(FAILURE_LABELS.get(reason.split(" ")[0], reason.split(" ")[0]))
    return {"qualified": qualified, "failures": counts}


# ---- HTML --------------------------------------------------------------------------------------

_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#2a3140;--text:#e6edf3;--muted:#8b949e;
--up:#3fb950;--down:#f85149;--warn:#d29922;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:16px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;
color:var(--muted);margin:24px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
a{color:var(--accent)}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:12px}
th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
border-bottom:1px solid var(--line);padding:5px 7px;white-space:nowrap}
td{padding:5px 7px;border-bottom:1px solid rgba(42,49,64,.6);white-space:nowrap}
td.num,th.num{text-align:right}
.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.wrap{overflow-x:auto}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;margin:6px 0}
dt{color:var(--muted)}dd{margin:0}
.warnbox{border:1px solid var(--warn);background:rgba(210,153,34,.1);padding:8px 10px;margin:8px 0}
.okbox{border:1px solid var(--line);padding:8px 10px;margin:8px 0}
footer{margin-top:28px;color:var(--muted);font-size:11px;border-top:1px solid var(--line);padding-top:8px}
"""


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _state_class(state: str | None) -> str:
    if state in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE"):
        return "up"
    if state in ("FAILED", "DISQUALIFIED"):
        return "down"
    if state in ("WATCHLIST", "INSUFFICIENT_SAMPLE"):
        return "warn"
    return "muted"


def _dl(pairs: Iterable[tuple[str, Any]]) -> str:
    items = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in pairs)
    return f"<dl>{items}</dl>"


def render_html(data: dict[str, Any], base: str = "/public/competition/report") -> str:
    d = data["diagnostics"]
    season = data["season"] or {}
    val = data["validation"] or {}
    q = data["quality"]
    qual = data["qualification"]
    srows = data["season_rows"]
    vrows = data["validation_rows"]
    active = [r for r in (vrows or srows) if r["ran"]]
    not_entered = [r for r in (vrows or srows) if not r["ran"]]

    parts: list[str] = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<meta name=robots content=noindex>",
        "<title>PaperLab Bot Arena — public inspection report</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>PAPERLAB BOT ARENA — PUBLIC INSPECTION REPORT</h1>",
        "<p class=muted>Server-rendered, read-only. Every value below is in the HTML; "
        "no JavaScript is required. "
        f"<a href='/public/competition'>Interactive dashboard</a> · "
        f"<a href='{base}.txt'>Plain text</a></p>",

        v6_html(data.get("v6") or {}),

        "<h2>Deployment</h2>",
        _dl([
            ("API version", d.get("api_version")),
            ("asset version", d.get("asset_version")),
            ("schema version", d.get("schema_version")),
            ("read only", d.get("read_only")),
            ("generated at", d.get("generated_at")),
            ("competition run id", d.get("competition_run_id") or "NONE"),
            ("season fingerprint", d.get("season_fingerprint") or "NONE"),
            ("validation run id", d.get("validation_run_id") or "NONE"),
            ("dataset fingerprint", d.get("dataset_fingerprint") or "NONE"),
            ("config fingerprint", d.get("config_fingerprint") or "NONE"),
            ("arena run id", d.get("arena_run_id") or "NONE"),
            ("arena config fingerprint", d.get("arena_config_fingerprint") or "NONE"),
        ]),
    ]
    parts.append(v5_html(data.get("v5") or {}))
    parts.append(v4_html(data.get("v4") or {}))
    parts.append(v31_html(data.get("v31") or {}))
    parts.append(v3_html(data.get("v3") or {}))
    parts.append(_candidates_html(data.get("candidates") or {}))
    parts.append(_shadow_html(data.get("shadow") or {}))
    parts.append(_runs_html(data.get("arena_runs") or []))
    parts.append(_fee_model_html(data))
    if data.get("jev"):
        parts.append(_jev_html(data["jev"], base))
    if data.get("arena"):
        parts.append(_arena_html(data["arena"], base))

    # ---- warnings
    parts.append("<h2>Data quality warnings</h2>")
    if q["warnings"]:
        parts.append("<div class=warnbox><ul>"
                     + "".join(f"<li>{_e(w)}</li>" for w in q["warnings"]) + "</ul></div>")
    else:
        parts.append("<div class=okbox>No warnings.</div>")

    # ---- active competition
    parts.append("<h2>Active competition</h2>")
    parts.append(_dl([
        ("status", season.get("status") or "NONE"),
        ("season", season.get("label") or season.get("season_id") or "NONE"),
        ("active bots", len(active)),
        ("configured bots", len(vrows or srows)),
        ("not entered", len(not_entered)),
        ("starting balance", f"{season.get('starting_balance') or 20.0:.2f} USDT per bot"),
        ("venue", "BINANCE_USDM (simulated — no live orders)"),
        ("signal timeframe", season.get("timeframe") or "1m"),
        ("symbols", ", ".join(season.get("symbols") or []) or "NONE"),
        ("max leverage", f"{season.get('max_leverage') or DASH}x"),
        ("execution profile", season.get("execution_profile") or "NONE"),
        ("window", f"{fmt_ts(season.get('start_ms'))} → {fmt_ts(season.get('end_ms'))}"),
    ]))

    # ---- qualification
    parts.append("<h2>Qualification</h2>")
    parts.append(f"<p><b>QUALIFIED SET: "
                 f"{_e(', '.join(qual['qualified'])) if qual['qualified'] else 'NONE'}</b></p>")
    if qual["failures"]:
        parts.append("<table><thead><tr><th>failure reason</th><th class=num>competitors</th>"
                     "</tr></thead><tbody>")
        for k, v in sorted(qual["failures"].items(), key=lambda kv: -kv[1]):
            parts.append(f"<tr><td>{_e(k)}</td><td class=num>{v}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p class=muted>No judged failures recorded.</p>")

    # ---- leaderboards
    if vrows:
        parts.append(f"<h2>Out-of-sample leaderboard (top {min(TOP_N, len(vrows))} of {len(vrows)})</h2>")
        parts.append(_validation_table(vrows[:TOP_N], base))
    if srows:
        parts.append(f"<h2>Season leaderboard (top {min(TOP_N, len(srows))} of {len(srows)})</h2>")
        parts.append(_season_table(srows[:TOP_N], base))

    # ---- validation state
    parts.append("<h2>Validation (walk-forward)</h2>")
    total = val.get("total_competitors") or 0
    done = len(vrows)
    parts.append(_dl([
        ("status", val.get("status") or "NOT RUN"),
        ("stage", val.get("stage") or "NOT RUN"),
        ("total competitors", total or "NOT RUN"),
        ("completed competitors", done),
        ("pending competitors", max(0, total - done)),
        ("walk-forward windows", val.get("windows") or "NOT RUN"),
        ("historical range", f"{val.get('first_month') or '?'} → {val.get('last_month') or '?'}"),
        ("leverage identities", ", ".join(f"{x}x" for x in (val.get("leverages") or [])) or "NONE"),
        ("starting balance", f"{val.get('starting_balance') or 20.0:.2f} USDT per window"),
        ("Monte Carlo", "RAN" if any(r.get("mc_ran") for r in vrows) else "NOT RUN"),
        ("stress testing", "RAN" if any(r.get("stress_ran") for r in vrows) else "NOT RUN"),
    ]))

    # ---- not entered
    parts.append("<h2>Not entered</h2>")
    if not_entered:
        parts.append("<table><thead><tr><th>bot</th><th>strategy</th><th>reason</th></tr></thead><tbody>")
        for r in not_entered:
            parts.append(f"<tr><td>{_e(r['key'])}</td><td>{_e(r.get('name') or DASH)}</td>"
                         f"<td>{_e(r.get('skipped') or 'UNAVAILABLE')}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p class=muted>Every configured competitor was able to run.</p>")

    # ---- frontend health
    parts.append("<h2>Frontend health</h2>")
    parts.append(_dl([
        ("last public API success", d.get("generated_at")),
        ("last competition update", fmt_ts(season.get("finished_ts") or season.get("created_ts"))),
        ("latest validation update", fmt_ts(val.get("finished_ts") or val.get("created_ts"))),
        ("expected asset version", d.get("asset_version")),
        ("competitors with null metrics", q["competitors_with_null_metrics"]),
        ("NaN metric values", q["nan_metrics"]),
        ("infinite metric values", q["infinite_metrics"]),
        ("null metric fields", q["null_metric_fields"]),
    ]))

    blob = json.dumps(_sanitize({
        "diagnostics": d, "quality": q, "qualification": qual,
        "season": {k: season.get(k) for k in
                   ("run_id", "season_id", "label", "status", "symbols", "starting_balance",
                    "max_leverage", "execution_profile", "start_ms", "end_ms", "bars")},
        "validation": {k: val.get(k) for k in
                       ("run_id", "status", "stage", "first_month", "last_month", "windows",
                        "total_competitors", "leverages", "dataset_fingerprint",
                        "config_fingerprint")},
        "season_rows": srows, "validation_rows": vrows,
        "arena": _arena_blob(data.get("arena")),
        "jev": {k: (data.get("jev") or {}).get(k) for k in ("ran", "run", "summary", "pairs", "health")},
    }), separators=(",", ":"), allow_nan=False)
    parts.append('<h2>Machine-readable summary</h2>')
    parts.append('<p class=muted>Same fields as the visible tables, for automated parsing.</p>')
    parts.append(f'<script type="application/json" id="inspection-data">{blob}</script>')
    parts.append(f"<footer>PaperLab public inspection report · generated {_e(d.get('generated_at'))}"
                 f" · read-only · no credentials required</footer></body></html>")
    return "".join(parts)


def _validation_table(rows: list[dict[str, Any]], base: str) -> str:
    head = ("<div class=wrap><table><thead><tr>"
            "<th class=num>rank</th><th>bot id</th><th>strategy</th><th>coin</th>"
            "<th>timeframe</th><th class=num>max lev</th><th class=num>avg eff lev</th>"
            "<th class=num>start</th><th class=num>equity</th><th class=num>net pnl</th>"
            "<th class=num>return</th><th class=num>trades</th><th class=num>expectancy R</th>"
            "<th class=num>PF</th><th class=num>max DD</th><th class=num>liq</th>"
            "<th class=num>windows +</th><th>MC ruin</th><th>stress</th><th>status</th>"
            "</tr></thead><tbody>")
    body = []
    for r in rows:
        m = r["metrics"] or {}
        syms = list((m.get("by_symbol") or {}).keys())
        coin = syms[0] if len(syms) == 1 else (", ".join(syms) if syms else DASH)
        body.append(
            f"<tr><td class=num>{r['rank']}</td>"
            f"<td><a href='{base}/bot/{html.escape(str(r['key']))}'>{_e(r['key'])}</a></td>"
            f"<td>{_e(r.get('name') or DASH)}</td><td>{_e(coin)}</td>"
            f"<td>{_e(m.get('timeframe') or 'multi')}</td>"
            f"<td class=num>{fmt_num(r.get('leverage'), 0, 'x')}</td>"
            f"<td class=num>{fmt_num(m.get('avg_effective_leverage'), 2, 'x')}</td>"
            f"<td class=num>{fmt_money(m.get('starting_equity'))}</td>"
            f"<td class=num>{fmt_money(m.get('ending_equity'))}</td>"
            f"<td class='num {_pn(m.get('net_profit'))}'>{fmt_money(m.get('net_profit'), True)}</td>"
            f"<td class='num {_pn(m.get('net_return_pct'))}'>{fmt_pct(m.get('net_return_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('trades'), 0)}</td>"
            f"<td class='num {_pn(m.get('expectancy_r'))}'>{fmt_num(m.get('expectancy_r'), 3)}</td>"
            f"<td class=num>{fmt_pf(m.get('profit_factor'))}</td>"
            f"<td class='num down'>{fmt_pct(m.get('max_drawdown_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('liquidation_count'), 0)}</td>"
            f"<td class=num>{fmt_num(r.get('profitable_windows'), 0)}/"
            f"{fmt_num(r.get('active_windows'), 0)}</td>"
            f"<td>{fmt_pct(r.get('mc_ruin'), 2) if r.get('mc_ran') else 'NOT RUN'}</td>"
            f"<td>{('PASS' if r.get('stress_survives') else 'FAIL') if r.get('stress_ran') else 'NOT RUN'}</td>"
            f"<td class={_state_class(r['state'])}>{_e(r['state'])}</td></tr>")
    return head + "".join(body) + "</tbody></table></div>"


def _season_table(rows: list[dict[str, Any]], base: str) -> str:
    head = ("<div class=wrap><table><thead><tr>"
            "<th class=num>rank</th><th>bot id</th><th>strategy</th><th>coin</th>"
            "<th class=num>max lev</th><th class=num>avg eff lev</th><th class=num>start</th>"
            "<th class=num>equity</th><th class=num>net pnl</th><th class=num>return</th>"
            "<th class=num>trades</th><th class=num>expectancy R</th><th class=num>PF</th>"
            "<th class=num>max DD</th><th class=num>liq</th><th>status</th>"
            "</tr></thead><tbody>")
    body = []
    for r in rows:
        m = r["metrics"] or {}
        syms = list((m.get("by_symbol") or {}).keys())
        coin = syms[0] if len(syms) == 1 else (", ".join(syms) if syms else DASH)
        body.append(
            f"<tr><td class=num>{r['rank']}</td>"
            f"<td><a href='{base}/bot/{html.escape(str(r['key']))}'>{_e(r['key'])}</a></td>"
            f"<td>{_e(r.get('name') or DASH)}</td><td>{_e(coin)}</td>"
            f"<td class=num>{fmt_num(r.get('leverage') or m.get('configured_max_leverage'), 0, 'x')}</td>"
            f"<td class=num>{fmt_num(m.get('avg_effective_leverage'), 2, 'x')}</td>"
            f"<td class=num>{fmt_money(m.get('starting_equity'))}</td>"
            f"<td class=num>{fmt_money(m.get('ending_equity'))}</td>"
            f"<td class='num {_pn(m.get('net_profit'))}'>{fmt_money(m.get('net_profit'), True)}</td>"
            f"<td class='num {_pn(m.get('net_return_pct'))}'>{fmt_pct(m.get('net_return_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('trades'), 0)}</td>"
            f"<td class='num {_pn(m.get('expectancy_r'))}'>{fmt_num(m.get('expectancy_r'), 3)}</td>"
            f"<td class=num>{fmt_pf(m.get('profit_factor'))}</td>"
            f"<td class='num down'>{fmt_pct(m.get('max_drawdown_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('liquidation_count'), 0)}</td>"
            f"<td class={_state_class(r['state'])}>{_e(r['state'])}</td></tr>")
    return head + "".join(body) + "</tbody></table></div>"


def _pn(v: Any) -> str:
    if v is None or _bad(v):
        return "muted"
    return "up" if v > 0 else ("down" if v < 0 else "muted")


# ---- bot detail -----------------------------------------------------------------------------------

def collect_bot(svc: Any, key: str) -> dict[str, Any] | None:
    """One competitor, from whichever run knows it. Validation first: it has the OOS evidence."""
    storage = svc.storage
    val_runs = storage.validation_runs(limit=1)
    if val_runs:
        full = storage.validation_competitor(val_runs[0]["run_id"], key)
        if full:
            return {"source": "validation", "run_id": val_runs[0]["run_id"], "record": full,
                    "fills": []}
    season_runs = storage.competition_runs(limit=1)
    if season_runs:
        rid = season_runs[0]["run_id"]
        c = storage.competition_competitor(rid, key)
        if c:
            fills = [_public_fill(f) for f in (c.get("fills") or []) if _is_simulated(f)]
            return {"source": "season", "run_id": rid,
                    "record": {"key": c.get("key") or c.get("strategy_id"),
                               "strategy_id": c.get("strategy_id"), "name": c.get("name"),
                               "state": c.get("state"), "leverage": c.get("leverage"),
                               "oos_metrics": _pick(c.get("metrics"), METRIC_FIELDS),
                               "qualification": c.get("qualification"),
                               "walk_forward": None, "monte_carlo": None, "stress": None},
                    "fills": fills}
    return None


def render_bot_html(svc: Any, payload: dict[str, Any],
                    base: str = "/public/competition/report") -> str:
    rec = payload["record"]
    m = rec.get("oos_metrics") or {}
    w = rec.get("walk_forward") or {}
    mc = rec.get("monte_carlo") or {}
    st = rec.get("stress") or {}
    q = rec.get("qualification") or {}
    d = _diagnostics(svc, {"run_id": payload["run_id"], "source": payload["source"]})
    syms = list((m.get("by_symbol") or {}).keys())
    coin = syms[0] if len(syms) == 1 else (", ".join(syms) if syms else DASH)

    p: list[str] = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<meta name=robots content=noindex>",
        f"<title>{_e(rec.get('key'))} — PaperLab inspection</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{_e(rec.get('key'))} — {_e(rec.get('name') or '')}</h1>",
        f"<p class=muted><a href='{base}'>&larr; back to report</a> · source: "
        f"{_e(payload['source'])} run {_e(payload['run_id'])} · read-only</p>",
        "<h2>Identity</h2>",
        _dl([("bot id", rec.get("key")), ("strategy", rec.get("strategy_id")),
             ("name", rec.get("name") or DASH), ("coin", coin),
             ("timeframe", m.get("timeframe") or "multi"),
             ("max leverage", fmt_num(rec.get("leverage"), 0, "x")),
             ("average effective leverage", fmt_num(m.get("avg_effective_leverage"), 2, "x")),
             ("maximum effective leverage", fmt_num(m.get("max_effective_leverage"), 2, "x")),
             ("status", rec.get("state") or "UNAVAILABLE")]),
        "<h2>Gross to net</h2>",
        "<table><tbody>"
        f"<tr><td>gross PnL (at decision prices)</td><td class='num {_pn(m.get('gross_pnl'))}'>"
        f"{fmt_money(m.get('gross_pnl'), True)}</td></tr>"
        f"<tr><td>commission</td><td class='num down'>"
        f"{fmt_money(-abs(m.get('fees_paid') or 0))}</td></tr>"
        f"<tr><td>spread crossed</td><td class='num down'>"
        f"{fmt_money(-abs(m.get('spread_cost') or 0))}</td></tr>"
        f"<tr><td>latency in flight</td><td class='num down'>"
        f"{fmt_money(-abs(m.get('latency_cost') or 0))}</td></tr>"
        f"<tr><td>market impact</td><td class='num down'>"
        f"{fmt_money(-abs(m.get('impact_cost') or 0))}</td></tr>"
        f"<tr><td>funding</td><td class='num {_pn(m.get('funding_paid'))}'>"
        f"{fmt_money(m.get('funding_paid'), True)}</td></tr>"
        f"<tr><th>net PnL</th><th class='num {_pn(m.get('net_profit'))}'>"
        f"{fmt_money(m.get('net_profit'), True)}</th></tr>"
        "</tbody></table>",
        "<h2>Performance</h2>",
        _dl([("trades", fmt_num(m.get("trades"), 0)), ("wins", fmt_num(m.get("wins"), 0)),
             ("losses", fmt_num(m.get("losses"), 0)),
             ("win rate", fmt_pct(m.get("win_rate"), 0)),
             ("expectancy R", fmt_num(m.get("expectancy_r"), 3)),
             ("expectancy USDT", fmt_money(m.get("expectancy_usdt"), True)),
             ("profit factor", fmt_pf(m.get("profit_factor"))),
             ("median R", fmt_num(m.get("median_r"), 3)),
             ("best trade", fmt_money(m.get("best_trade"), True)),
             ("worst trade", fmt_money(m.get("worst_trade"), True)),
             ("max drawdown", fmt_pct(m.get("max_drawdown_pct"))),
             ("liquidations", fmt_num(m.get("liquidation_count"), 0))]),
    ]

    p.append("<h2>Qualification gates</h2>")
    gates = q.get("gates") or []
    if gates:
        p.append("<table><thead><tr><th>result</th><th>gate</th><th class=num>actual</th>"
                 "<th class=num>threshold</th></tr></thead><tbody>")
        for g in gates:
            verdict = "PASS" if g.get("ok") else ("SOFT" if g.get("soft") else "FAIL")
            cls = "up" if g.get("ok") else ("warn" if g.get("soft") else "down")
            p.append(f"<tr><td class={cls}>{verdict}</td><td>{_e(g.get('name'))}</td>"
                     f"<td class=num>{fmt_num(g.get('actual'), 4)}</td>"
                     f"<td class=num>{_e(g.get('comparison'))} {fmt_num(g.get('threshold'), 4)}</td></tr>")
        p.append("</tbody></table>")
    else:
        p.append("<p class=muted>No gate record available.</p>")
    if q.get("reasons"):
        p.append("<p class=warn>not qualified: " + _e("; ".join(q["reasons"])) + "</p>")

    p.append("<h2>Validation stages</h2>")
    evaluated = set(q.get("evaluated_stages") or [])
    p.append("<table><thead><tr><th>stage</th><th>state</th></tr></thead><tbody>")
    for name in ("season", "walk_forward", "monte_carlo", "stress", "shadow_live"):
        ran = name in evaluated
        p.append(f"<tr><td>{name.replace('_', ' ')}</td>"
                 f"<td class={'up' if ran else 'muted'}>{'RAN' if ran else 'NOT RUN'}</td></tr>")
    p.append("</tbody></table>")

    if w:
        p.append("<h2>Walk-forward</h2>")
        p.append(_dl([("total windows", fmt_num(w.get("total_windows"), 0)),
                      ("active windows", fmt_num(w.get("active_windows"), 0)),
                      ("profitable windows", fmt_num(w.get("profitable_windows"), 0)),
                      ("profitable ratio", fmt_pct(w.get("profitable_ratio"))),
                      ("active ratio", fmt_pct(w.get("active_ratio"))),
                      ("symbol concentration", fmt_pct(w.get("symbol_concentration"))),
                      ("median window return", fmt_pct(w.get("median_window_return"))),
                      ("worst window return", fmt_pct(w.get("worst_window_return"))),
                      ("best window return", fmt_pct(w.get("best_window_return")))]))

    p.append("<h2>Monte Carlo</h2>")
    if mc.get("ran"):
        p.append(_dl([("simulations", fmt_num(mc.get("simulations"), 0)),
                      ("mode", mc.get("mode")),
                      ("median ending equity", fmt_money(mc.get("median_ending_equity"))),
                      ("median max drawdown", fmt_pct(mc.get("median_max_drawdown"))),
                      ("95th pct max drawdown", fmt_pct(mc.get("p95_max_drawdown"))),
                      ("99th pct max drawdown", fmt_pct(mc.get("p99_max_drawdown"))),
                      ("probability of ruin", fmt_pct(mc.get("ruin_probability"), 2))]))
    else:
        p.append(f"<p>NOT RUN — {_e(mc.get('reason') or 'skipped: cannot qualify regardless')}</p>")

    p.append("<h2>Stress scenarios</h2>")
    if st.get("ran"):
        p.append("<table><thead><tr><th>scenario</th><th class=num>net</th><th class=num>return</th>"
                 "<th class=num>max DD</th></tr></thead><tbody>")
        for s in st.get("scenarios") or []:
            p.append(f"<tr><td>{_e(s.get('name'))}</td>"
                     f"<td class='num {_pn(s.get('net_profit'))}'>{fmt_money(s.get('net_profit'), True)}</td>"
                     f"<td class=num>{fmt_pct(s.get('net_return_pct'))}</td>"
                     f"<td class=num>{fmt_pct(s.get('max_drawdown_pct'))}</td></tr>")
        p.append("</tbody></table>")
    else:
        p.append("<p>NOT RUN</p>")

    p.append("<h2>Performance by symbol</h2>")
    by = m.get("by_symbol") or {}
    if by:
        p.append("<table><thead><tr><th>symbol</th><th class=num>trades</th><th class=num>net</th>"
                 "<th class=num>total R</th></tr></thead><tbody>")
        for sym, v in by.items():
            p.append(f"<tr><td>{_e(sym)}</td><td class=num>{fmt_num(v.get('trades'), 0)}</td>"
                     f"<td class='num {_pn(v.get('net'))}'>{fmt_money(v.get('net'), True)}</td>"
                     f"<td class=num>{fmt_num(v.get('r'), 2)}</td></tr>")
        p.append("</tbody></table>")
    else:
        p.append("<p class=muted>UNAVAILABLE</p>")

    fills = payload.get("fills") or []
    p.append(f"<h2>Recent simulated trades ({min(RECENT_FILLS, len(fills))} of {len(fills)})</h2>")
    if fills:
        p.append("<div class=wrap><table><thead><tr><th>time</th><th>symbol</th><th>kind</th>"
                 "<th>side</th><th class=num>decision</th><th class=num>fill</th>"
                 "<th class=num>qty</th><th>role</th><th class=num>exec</th>"
                 "<th class=num>fee</th><th class=num>realised</th></tr></thead><tbody>")
        for f in fills[-RECENT_FILLS:][::-1]:
            p.append(f"<tr><td>{fmt_ts(f.get('ts'))}</td><td>{_e(f.get('symbol'))}</td>"
                     f"<td>{_e(f.get('kind'))}</td><td>{_e(f.get('side'))}</td>"
                     f"<td class=num>{fmt_num(f.get('decision_price'), 4)}</td>"
                     f"<td class=num>{fmt_num(f.get('price'), 4)}</td>"
                     f"<td class=num>{fmt_num(f.get('qty'), 6)}</td>"
                     f"<td>{_e(f.get('liquidity_role') or DASH)}</td>"
                     f"<td class=num>L{_e(f.get('execution_level') or '?')}</td>"
                     f"<td class='num down'>{fmt_money(-abs(f.get('fee') or 0))}</td>"
                     f"<td class='num {_pn(f.get('realized_pnl'))}'>"
                     f"{fmt_money(f.get('realized_pnl'), True)}</td></tr>")
        p.append("</tbody></table></div>")
    else:
        p.append("<p class=muted>No simulated fills stored for this competitor "
                 "(validation records keep aggregate metrics, not the per-fill ledger).</p>")

    blob = json.dumps(_sanitize({"diagnostics": d, "record": rec,
                                 "recent_fills": fills[-RECENT_FILLS:]}),
                      separators=(",", ":"), allow_nan=False)
    p.append(f'<script type="application/json" id="inspection-data">{blob}</script>')
    p.append(f"<footer>PaperLab public inspection · {_e(d.get('generated_at'))} · read-only</footer>"
             "</body></html>")
    return "".join(p)


# ---- plain text -------------------------------------------------------------------------------------

def render_text(data: dict[str, Any]) -> str:
    d = data["diagnostics"]
    season = data["season"] or {}
    val = data["validation"] or {}
    q = data["quality"]
    qual = data["qualification"]
    rows = data["validation_rows"] or data["season_rows"]
    active = [r for r in rows if r["ran"]]
    not_entered = [r for r in rows if not r["ran"]]

    L: list[str] = []
    L.append("PAPERLAB BOT ARENA - PUBLIC INSPECTION REPORT")
    L.append("=" * 72)
    L.extend(v6_text(data.get("v6") or {}))
    L.extend(v5_text(data.get("v5") or {}))
    L.extend(v4_text(data.get("v4") or {}))
    L.extend(v31_text(data.get("v31") or {}))
    L.extend(v3_text(data.get("v3") or {}))
    L.extend(_candidates_text(data.get("candidates") or {}))
    L.extend(_shadow_text(data.get("shadow") or {}))
    L.append("")
    L.append("DISCOVERY RUNS")
    for r in data.get("arena_runs") or []:
        L.append(f"  {r['run_id']}  {(r.get('dataset_role') or 'v1'):<11} {r.get('first_month')}..{r.get('last_month')}  "
                 f"bots {r.get('active_bots') or 0:>3}  advanced {r.get('advanced') or 0:>2}  net {fmt_money(r.get('net'), signed=True)}")
    L.append("")
    L.append(f"api version        {d.get('api_version')}")
    L.append(f"asset version      {d.get('asset_version')}")
    L.append(f"schema version     {d.get('schema_version')}")
    L.append(f"generated at       {d.get('generated_at')}")
    L.append(f"read only          {d.get('read_only')}")
    L.append(f"competition run    {d.get('competition_run_id') or 'NONE'}")
    L.append(f"season fingerprint {d.get('season_fingerprint') or 'NONE'}")
    L.append(f"validation run     {d.get('validation_run_id') or 'NONE'}")
    L.append(f"dataset fingerprint{' ' + (d.get('dataset_fingerprint') or 'NONE')}")
    L.append(f"config fingerprint {d.get('config_fingerprint') or 'NONE'}")
    L.append(f"arena run          {d.get('arena_run_id') or 'NONE'}")
    L.append("")
    L.extend(_fee_model_text(data))
    L.append("")
    if data.get("jev"):
        L.extend(_jev_text(data["jev"]))
        L.append("")
    if data.get("arena"):
        L.extend(_arena_text(data["arena"]))
        L.append("")
    L.append("ACTIVE COMPETITION")
    L.append(f"  status           {season.get('status') or 'NONE'}")
    L.append(f"  active bots      {len(active)}")
    L.append(f"  configured bots  {len(rows)}")
    L.append(f"  not entered      {len(not_entered)}")
    L.append(f"  starting balance {season.get('starting_balance') or 20.0:.2f} USDT per bot")
    L.append(f"  venue            BINANCE_USDM (simulated)")
    L.append(f"  symbols          {', '.join(season.get('symbols') or []) or 'NONE'}")
    L.append("")
    L.append("WARNINGS")
    if q["warnings"]:
        for w in q["warnings"]:
            L.append(f"  ! {w}")
    else:
        L.append("  none")
    L.append("")
    L.append(f"QUALIFIED SET: {', '.join(qual['qualified']) if qual['qualified'] else 'NONE'}")
    if qual["failures"]:
        L.append("FAILURE REASONS")
        for k, v in sorted(qual["failures"].items(), key=lambda kv: -kv[1]):
            L.append(f"  {v:>4}  {k}")
    L.append("")
    L.append(f"LEADERBOARD (top {min(TOP_N, len(rows))} of {len(rows)})")
    L.append(f"  {'#':>3} {'bot':<14}{'trades':>8}{'net':>10}{'return':>9}{'expR':>8}"
             f"{'PF':>7}{'maxDD':>8}{'lev':>7}  status")
    for r in rows[:TOP_N]:
        m = r["metrics"] or {}
        L.append(f"  {r['rank']:>3} {str(r['key'])[:13]:<14}"
                 f"{fmt_num(m.get('trades'), 0):>8}{fmt_money(m.get('net_profit'), True):>10}"
                 f"{fmt_pct(m.get('net_return_pct')):>9}{fmt_num(m.get('expectancy_r'), 3):>8}"
                 f"{fmt_pf(m.get('profit_factor')):>7}{fmt_pct(m.get('max_drawdown_pct')):>8}"
                 f"{fmt_num(m.get('avg_effective_leverage'), 2, 'x'):>7}  {r['state']}")
    L.append("")
    L.append("VALIDATION")
    total = val.get("total_competitors") or 0
    L.append(f"  status           {val.get('status') or 'NOT RUN'}")
    L.append(f"  stage            {val.get('stage') or 'NOT RUN'}")
    L.append(f"  completed        {len(data['validation_rows'])} / {total or 'NOT RUN'}")
    L.append(f"  pending          {max(0, total - len(data['validation_rows']))}")
    L.append(f"  windows          {val.get('windows') or 'NOT RUN'}")
    L.append(f"  range            {val.get('first_month') or '?'} -> {val.get('last_month') or '?'}")
    L.append(f"  monte carlo      {'RAN' if any(r.get('mc_ran') for r in data['validation_rows']) else 'NOT RUN'}")
    L.append(f"  stress           {'RAN' if any(r.get('stress_ran') for r in data['validation_rows']) else 'NOT RUN'}")
    L.append("")
    L.append("NOT ENTERED")
    if not_entered:
        for r in not_entered:
            L.append(f"  {str(r['key']):<14} {r.get('skipped') or 'UNAVAILABLE'}")
    else:
        L.append("  none")
    L.append("")
    L.append("FRONTEND HEALTH")
    L.append(f"  competitors with null metrics  {q['competitors_with_null_metrics']}")
    L.append(f"  NaN metric values              {q['nan_metrics']}")
    L.append(f"  infinite metric values         {q['infinite_metrics']}")
    L.append(f"  null metric fields             {q['null_metric_fields']}")
    return "\n".join(L) + "\n"


# ---- specialist arena ------------------------------------------------------------------------------

ARENA_STATE_CLASS = {"ADVANCE": "up", "FAILED": "down", "DISQUALIFIED": "down",
                     "INSUFFICIENT_SAMPLE": "warn", "ERROR": "down"}


def _arena_blob(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return None
    return {k: a.get(k) for k in ("run", "summary", "config", "symbols", "strategies",
                                  "not_entered", "bots", "pipeline")} | {
        "eligible_not_selected": len(a.get("eligible_not_selected") or [])}


def _arena_html(a: dict[str, Any], base: str) -> str:
    run, s, cfg = a.get("run") or {}, a.get("summary") or {}, a.get("config") or {}
    risk = cfg.get("risk_profile") or {}
    fees = cfg.get("fees") or {}
    bots = a.get("bots") or []
    adv = s.get("advanced_keys") or []
    active, need = s.get("active_bots") or 0, s.get("min_active_bots") or 10
    p: list[str] = ["<h2 id=arena>Specialist bot arena — discovery</h2>"]
    p.append(f"<p><b>ACTIVE BOTS: {active}</b> · MINIMUM REQUIRED: {need} · "
             f"{'field OK' if active >= need else 'INSUFFICIENT_COMPETITORS'}</p>")
    p.append(f"<p><b>ADVANCED TO MULTI-YEAR VALIDATION: {_e(', '.join(adv)) if adv else 'NONE'}</b>"
             " — advancement is not qualification; nothing here can trade live.</p>")
    p.append("<p class=muted>Pipeline: " + " → ".join(_e(x) for x in a.get("pipeline") or []) + "</p>")
    p.append(_dl([
        ("competition id", run.get("run_id")), ("label", run.get("label")),
        ("status", (run.get("status") or "").upper() or DASH),
        ("window", f"{run.get('first_month') or '?'} → {run.get('last_month') or '?'} (1m bars)"),
        ("started / finished", f"{fmt_ts(run.get('created_ts'))} / {fmt_ts(run.get('finished_ts'))}"),
        ("active bots", active), ("not entered (candidates)", s.get("not_entered")),
        ("eligible but not selected", s.get("eligible_not_selected")),
        ("coins represented", ", ".join(s.get("coins") or []) or "NONE"),
        ("timeframes represented", ", ".join(s.get("timeframes") or []) or "NONE"),
        ("strategies represented", ", ".join(s.get("strategies") or []) or "NONE"),
        ("bots that traded", s.get("bots_that_traded")),
        ("profitable after all costs", s.get("profitable_after_costs")),
        ("liquidated", s.get("liquidated")),
        ("total trades", s.get("total_trades")),
        ("gross PnL (decision prices)", fmt_money(s.get("total_gross_pnl"), True)),
        ("fees paid", fmt_money(-(s.get("total_fees") or 0.0), True)),
        ("slippage", fmt_money(-(s.get("total_slippage") or 0.0), True)),
        ("funding", fmt_money(s.get("total_funding"), True)),
        ("net PnL", fmt_money(s.get("total_net_pnl"), True)),
        ("best bot", s.get("best_bot") or DASH), ("worst bot", s.get("worst_bot") or DASH),
        ("config fingerprint", run.get("config_fingerprint")),
    ]))
    p.append("<h2>Arena configuration</h2>")
    p.append(_dl([
        ("starting balance", f"{fmt_num(cfg.get('starting_balance'))} USDT per bot, isolated"),
        ("profile", cfg.get("profile")),
        ("ordinary / strong / exceptional risk",
         f"{fmt_pct(risk.get('ordinary_risk_pct'), 2)} / {fmt_pct(risk.get('strong_risk_pct'), 2)}"
         f" / {fmt_pct(risk.get('exceptional_risk_pct'), 2)} (hard max {fmt_pct(risk.get('max_risk_pct'), 2)})"),
        ("state multipliers", f"ATTACK {fmt_num(risk.get('attack_multiplier'))}x · NORMAL 1.00x · "
                              f"DEFENSIVE {fmt_num(risk.get('defensive_multiplier'))}x · HALTED 0"),
        ("ATTACK requires", f">= {risk.get('attack_min_trades')} closed trades, recent expectancy >= "
                            f"{fmt_num(risk.get('attack_min_expectancy_r'))}R, drawdown <= "
                            f"{fmt_pct(risk.get('attack_max_drawdown'))}, and a STRONG/EXCEPTIONAL "
                            "signal grade (v1 strategies grade nothing, so ATTACK cannot occur)"),
        ("DEFENSIVE / HALTED", f"drawdown >= {fmt_pct(risk.get('defensive_drawdown'))} or recent "
                               f"expectancy <= {fmt_num(risk.get('defensive_expectancy_r'))}R / "
                               f"drawdown >= {fmt_pct(risk.get('halt_drawdown'))}"),
        ("leverage", f"ceiling {cfg.get('leverage_ceiling')}x; each position gets the smallest "
                     f"leverage its margin needs ({cfg.get('leverage_policy')})"),
        ("fees charged", f"maker {fmt_pct(fees.get('maker_rate'), 3)} / taker "
                         f"{fmt_pct(fees.get('taker_rate'), 3)} ({cfg.get('fee_source')})"),
        ("fee gate", f"round trip <= {fmt_pct(cfg.get('max_fee_share_of_r'), 0)} of the trade's risk"),
        ("min-notional safety multiplier", f"{fmt_num(cfg.get('min_notional_safety_multiplier'))}x "
                                           "(1.00 = Binance's rule; above is PaperLab caution)"),
        ("advancement gates", f"trades >= {cfg.get('min_trades')}; net PnL > "
                              f"{fmt_num(cfg.get('min_net_profit'))}; expectancy > "
                              f"{fmt_num(cfg.get('min_expectancy_r'))}R; PF >= "
                              f"{fmt_num(cfg.get('min_profit_factor'))}; max DD <= "
                              f"{fmt_pct(cfg.get('max_drawdown_pct'), 0)}; liquidations <= "
                              f"{cfg.get('max_liquidations')}"),
        ("execution", "latency then next-bar open; OHLCV (L3) half-spread from tick size; "
                      "adverse extreme walked before the favourable one"),
    ]))

    p.append("<h2>Coin feasibility (min-notional audit applied)</h2>")
    p.append("<p class=muted>Binance USD-M checks MIN_NOTIONAL on the submitted order (mark price for "
             "MARKET) and exempts reduce-only orders; fills and resting remainders have no minimum. "
             "The old 2 x minNotional rule was a PaperLab assumption and is gone.</p>")
    p.append("<div class=wrap><table><thead><tr><th>symbol</th><th class=num>Binance minNotional</th>"
             "<th class=num>minQty</th><th class=num>ref price</th><th class=num>exchange min order</th>"
             "<th class=num>PaperLab min</th><th class=num>fee-gate max</th><th>legal stop band</th>"
             "<th class=num>min risk needed</th><th>tradeable</th><th>exclusion reason</th>"
             "</tr></thead><tbody>")
    for r in a.get("symbols") or []:
        band = DASH
        if r.get("min_stop_pct") is not None and r.get("max_stop_pct") is not None:
            band = ("empty" if r["max_stop_pct"] < r["min_stop_pct"] else
                    f"{fmt_pct(r['min_stop_pct'], 2)} – {fmt_pct(r['max_stop_pct'], 2)}")
        ok = bool(r.get("tradeable"))
        p.append(f"<tr><td>{_e(r.get('symbol'))}</td><td class=num>{fmt_num(r.get('min_notional'), 0)}</td>"
                 f"<td class=num>{fmt_num(r.get('min_qty'), 3)}</td>"
                 f"<td class=num>{fmt_num(r.get('reference_price'), 4)}</td>"
                 f"<td class=num>{fmt_num(r.get('exchange_min'))}</td>"
                 f"<td class=num>{fmt_num(r.get('floor'))}</td><td class=num>{fmt_num(r.get('ceiling'))}</td>"
                 f"<td>{band}</td><td class=num>{fmt_pct(r.get('min_risk_pct_needed'), 2)}</td>"
                 f"<td class={'up' if ok else 'down'}>{'YES' if ok else 'NO'}</td>"
                 f"<td>{_e(r.get('reason') or DASH)}</td></tr>")
    p.append("</tbody></table></div>")

    cd = a.get("cost_diagnostics") or {}
    p.append("<h2>Cost diagnostics</h2>")
    p.append("<p class=muted>GROSS_NEGATIVE: the trading logic loses before any cost. FEE_DESTROYED: it "
             "makes money at decision prices and fees + slippage eat all of it. MARGINAL: net positive but "
             "costs take more than half the gross edge. HEALTHY: net positive with costs well inside the edge. "
             "Diagnostics only, not gates.</p>")
    p.append(_dl([("classes", ", ".join(f"{k} {v}" for k, v in sorted((cd.get("classes") or {}).items()))),
                  ("gross edge (all bots)", fmt_money(cd.get("total_gross"), True)),
                  ("costs (fees + slippage + funding paid)", fmt_money(-(cd.get("total_costs") or 0.0), True)),
                  ("JEV-ELIGIBLE CONTROLS", f"{len(cd.get('jev_eligible') or [])}: "
                                            f"{', '.join(cd.get('jev_eligible') or []) or 'NONE'}")]))
    if cd.get("fee_destroyed"):
        p.append("<table><thead><tr><th>FEE-DESTROYED bot</th><th class=num>trades</th>"
                 "<th class=num>gross edge</th><th class=num>costs</th><th class=num>net</th>"
                 "<th class=num>cost / edge</th></tr></thead><tbody>")
        for r in cd["fee_destroyed"]:
            p.append(f"<tr><td>{_e(r['key'])}</td><td class=num>{r['trades']}</td>"
                     f"<td class='num up'>{fmt_money(r['gross_edge'], True)}</td>"
                     f"<td class=num>{fmt_money(-r['total_costs'], True)}</td>"
                     f"<td class='num down'>{fmt_money(r['net'], True)}</td>"
                     f"<td class=num>{fmt_num(r.get('cost_to_edge'), 2, 'x')}</td></tr>")
        p.append("</tbody></table>")
    p.append(f"<h2>Arena leaderboard ({len(bots)} active bots)</h2>")
    p.append("<p class=muted>Order: ADVANCE first, then net PnL after fees, slippage and funding; "
             "bots that never traded last. Rank is not advancement.</p>")
    p.append("<div class=wrap><table><thead><tr><th class=num>rank</th><th>bot</th><th>strategy</th>"
             "<th>coin</th><th>tf</th><th class=num>max lev</th><th class=num>avg eff lev</th>"
             "<th class=num>avg pos lev</th><th class=num>signals</th><th class=num>trades</th>"
             "<th class=num>gross</th><th class=num>fees</th><th class=num>slippage</th>"
             "<th class=num>funding</th><th class=num>net</th><th class=num>return</th>"
             "<th class=num>exp R</th><th class=num>PF</th><th class=num>max DD</th>"
             "<th class=num>liq</th><th>entry states</th><th>cost class</th><th>state</th><th>why</th>"
             "</tr></thead><tbody>")
    for b in bots:
        m = b.get("metrics") or {}
        states = ", ".join(f"{k} {v}" for k, v in sorted((b.get("entry_states") or {}).items()))
        p.append(
            f"<tr><td class=num>{_e(b.get('rank') or DASH)}</td>"
            f"<td><a href='{base}/arena/bot/{_e(b.get('key'))}'>{_e(b.get('key'))}</a></td>"
            f"<td>{_e(b.get('strategy_id'))} {_e(b.get('name') or '')}</td><td>{_e(b.get('coin'))}</td>"
            f"<td>{_e(b.get('timeframe'))}</td><td class=num>{fmt_num(b.get('max_leverage'), 0, 'x')}</td>"
            f"<td class=num>{fmt_num(m.get('avg_effective_leverage'), 2, 'x')}</td>"
            f"<td class=num>{fmt_num(b.get('avg_position_leverage'), 1, 'x')}</td>"
            f"<td class=num>{fmt_num(b.get('signals'), 0)}</td><td class=num>{fmt_num(m.get('trades'), 0)}</td>"
            f"<td class='num {_pn(m.get('gross_pnl'))}'>{fmt_money(m.get('gross_pnl'), True)}</td>"
            f"<td class=num>{fmt_money(-(m.get('fees_paid') or 0.0), True)}</td>"
            f"<td class=num>{fmt_money(-(m.get('slippage_cost') or 0.0), True)}</td>"
            f"<td class=num>{fmt_money(m.get('funding_paid'), True)}</td>"
            f"<td class='num {_pn(m.get('net_profit'))}'>{fmt_money(m.get('net_profit'), True)}</td>"
            f"<td class='num {_pn(m.get('net_return_pct'))}'>{fmt_pct(m.get('net_return_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('expectancy_r'), 3)}</td><td class=num>{fmt_pf(m.get('profit_factor'))}</td>"
            f"<td class=num>{fmt_pct(m.get('max_drawdown_pct'))}</td>"
            f"<td class=num>{fmt_num(m.get('liquidation_count'), 0)}</td><td>{_e(states or DASH)}</td>"
            f"<td>{_e((b.get('cost_efficiency') or {}).get('class') or DASH)}</td>"
            f"<td class={ARENA_STATE_CLASS.get(b.get('state'), 'muted')}>{_e(b.get('state'))}</td>"
            f"<td>{_e('; '.join(b.get('reasons') or []) or DASH)}</td></tr>")
    p.append("</tbody></table></div>")

    mx = a.get("matrix") or {}
    syms = mx.get("symbols") or []
    p.append("<h2>Strategy × coin matrix</h2>")
    p.append("<p class=muted>Each cell: return · PF · expectancy R · max DD · trades · state. "
             "An empty cell was not selected for this arena — it says nothing about that pair.</p>")
    p.append("<div class=wrap><table><thead><tr><th>strategy</th>"
             + "".join(f"<th>{_e(x[:-4] if x.endswith('USDT') else x)}</th>" for x in syms)
             + "</tr></thead><tbody>")
    for sid in mx.get("strategies") or []:
        row = [f"<td>{_e(sid)} <span class=muted>{_e((mx.get('names') or {}).get(sid) or '')}</span></td>"]
        for sym in syms:
            cell = ((mx.get("cells") or {}).get(sid) or {}).get(sym) or []
            if not cell:
                row.append("<td class=muted>·</td>")
                continue
            bits = []
            for c in cell:
                bits.append(f"<span class={ARENA_STATE_CLASS.get(c.get('state'), 'muted')}>"
                            f"{fmt_pct(c.get('net_return_pct'))} · PF {fmt_pf(c.get('profit_factor'))} · "
                            f"{fmt_num(c.get('expectancy_r'), 2)}R · DD {fmt_pct(c.get('max_drawdown_pct'))} · "
                            f"n={fmt_num(c.get('trades'), 0)} · {_e(c.get('state'))} [{_e(c.get('timeframe'))}]</span>")
            row.append("<td>" + "<br>".join(bits) + "</td>")
        p.append("<tr>" + "".join(row) + "</tr>")
    p.append("</tbody></table></div>")

    p.append("<h2>Strategy timeframes</h2>")
    p.append("<p class=muted>native = the timeframe the strategy's own gate signals on; context = "
             "series it reads but never trades. v1 strategies are entered on their native timeframe "
             "only; a timeframe-configurable version would be a new strategy version (v2).</p>")
    p.append("<table><thead><tr><th>strategy</th><th>native</th><th>arena-supported</th>"
             "<th>context</th></tr></thead><tbody>")
    for r in a.get("strategies") or []:
        p.append(f"<tr><td>{_e(r.get('strategy_id'))} {_e(r.get('name') or '')}</td>"
                 f"<td>{_e(r.get('native_timeframe') or 'UNKNOWN')}</td>"
                 f"<td>{_e(', '.join(r.get('supported_timeframes') or []) or 'NONE')}</td>"
                 f"<td>{_e(', '.join(r.get('context_timeframes') or []) or DASH)}</td></tr>")
    p.append("</tbody></table>")

    p.append("<h2>Arena: not entered</h2>")
    ne = a.get("not_entered") or []
    if ne:
        p.append("<table><thead><tr><th class=num>candidates</th><th>reason</th><th>bots</th>"
                 "</tr></thead><tbody>")
        for g in ne:
            p.append(f"<tr><td class=num>{g.get('count')}</td><td>{_e(g.get('reason'))}</td>"
                     f"<td style='white-space:normal'>{_e(', '.join(g.get('keys') or []))}</td></tr>")
        p.append("</tbody></table>")
    else:
        p.append("<p class=muted>Every candidate passed preflight.</p>")
    rest = a.get("eligible_not_selected") or []
    p.append(f"<p class=muted>{len(rest)} candidates passed preflight but were not selected (field cap "
             f"{cfg.get('max_bots')}; strategy × coin rotation, results never consulted): "
             f"{_e(', '.join(rest)) or DASH}</p>")
    return "".join(p)


def _arena_text(a: dict[str, Any]) -> list[str]:
    run, s = a.get("run") or {}, a.get("summary") or {}
    L = ["SPECIALIST BOT ARENA - DISCOVERY",
         f"  competition id   {run.get('run_id')}",
         f"  status           {(run.get('status') or '').upper()}",
         f"  window           {run.get('first_month')} -> {run.get('last_month')}",
         f"  ACTIVE BOTS      {s.get('active_bots')}   (MINIMUM REQUIRED {s.get('min_active_bots')})",
         f"  not entered      {s.get('not_entered')} candidates",
         f"  coins            {', '.join(s.get('coins') or []) or 'NONE'}",
         f"  timeframes       {', '.join(s.get('timeframes') or []) or 'NONE'}",
         f"  traded           {s.get('bots_that_traded')}   profitable {s.get('profitable_after_costs')}"
         f"   liquidated {s.get('liquidated')}",
         f"  fees {fmt_money(-(s.get('total_fees') or 0.0), True)}   slippage "
         f"{fmt_money(-(s.get('total_slippage') or 0.0), True)}   funding "
         f"{fmt_money(s.get('total_funding'), True)}   net {fmt_money(s.get('total_net_pnl'), True)}",
         f"  ADVANCED         {', '.join(s.get('advanced_keys') or []) or 'NONE'}",
         "", "  COIN FEASIBILITY"]
    for r in a.get("symbols") or []:
        L.append(f"    {str(r.get('symbol')):<9} minNotional {fmt_num(r.get('min_notional'), 0):>3}  "
                 f"exchange min {fmt_num(r.get('exchange_min')):>6}  fee cap {fmt_num(r.get('ceiling')):>6}"
                 f"  needs {fmt_pct(r.get('min_risk_pct_needed'), 2):>6}  "
                 f"{'YES' if r.get('tradeable') else 'NO  ' + str(r.get('reason') or '')}")
    L.append("")
    L.append(f"  {'#':>3} {'bot':<27}{'tf':<4}{'trades':>7}{'gross':>8}{'fees':>7}{'slip':>7}{'fund':>7}"
             f"{'net':>8}{'return':>8}{'expR':>7}{'PF':>6}{'maxDD':>7}{'liq':>4}  state")
    for b in a.get("bots") or []:
        m = b.get("metrics") or {}
        L.append(f"  {str(b.get('rank') or '-'):>3} {str(b.get('key')):<27}{str(b.get('timeframe')):<4}"
                 f"{fmt_num(m.get('trades'), 0):>7}{fmt_money(m.get('gross_pnl'), True):>8}"
                 f"{fmt_money(-(m.get('fees_paid') or 0.0), True):>7}"
                 f"{fmt_money(-(m.get('slippage_cost') or 0.0), True):>7}"
                 f"{fmt_money(m.get('funding_paid'), True):>7}{fmt_money(m.get('net_profit'), True):>8}"
                 f"{fmt_pct(m.get('net_return_pct')):>8}{fmt_num(m.get('expectancy_r'), 2):>7}"
                 f"{fmt_pf(m.get('profit_factor')):>6}{fmt_pct(m.get('max_drawdown_pct')):>7}"
                 f"{fmt_num(m.get('liquidation_count'), 0):>4}  {b.get('state')}")
    L.append("")
    L.append("  NOT ENTERED")
    for g in a.get("not_entered") or []:
        L.append(f"    {g.get('count'):>3}  {g.get('reason')}")
    return L


def render_arena_bot_html(svc: Any, b: dict[str, Any], base: str = "/public/competition/report") -> str:
    m = b.get("metrics") or {}
    d = _diagnostics(svc, {"arena_run_id": b.get("run_id")})
    p: list[str] = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<meta name=robots content=noindex>",
        f"<title>{_e(b.get('key'))} — PaperLab arena bot</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{_e(b.get('key'))}</h1>",
        f"<p class=muted><a href='{base}#arena'>&larr; back to report</a> · arena run "
        f"{_e(b.get('run_id'))} · simulated · read-only · generated {_e(d.get('generated_at'))}</p>",
        "<h2>Identity</h2>",
        _dl([("bot", b.get("key")), ("strategy", f"{b.get('strategy_id')} {b.get('name') or ''}"),
             ("coin", b.get("symbol")), ("timeframe", b.get("timeframe")),
             ("leverage ceiling", fmt_num(b.get("max_leverage"), 0, "x")),
             ("profile", b.get("profile")), ("parameter version", b.get("params_version")),
             ("version fingerprint", b.get("version")), ("state", b.get("state")),
             ("rank", b.get("rank") or DASH)]),
        "<h2>Why this state</h2>",
        "<ul>" + ("".join(f"<li>{_e(r)}</li>" for r in b.get("reasons") or [])
                  or "<li>all advancement gates passed</li>") + "</ul>",
        "<table><thead><tr><th>gate</th><th class=num>actual</th><th>rule</th><th>result</th>"
        "</tr></thead><tbody>"
        + "".join(f"<tr><td>{_e(g.get('name'))}</td><td class=num>{fmt_num(g.get('actual'), 4)}</td>"
                  f"<td>{_e(g.get('comparison'))} {fmt_num(g.get('threshold'), 4)}</td>"
                  f"<td class={'up' if g.get('ok') else 'down'}>{'PASS' if g.get('ok') else 'FAIL'}</td></tr>"
                  for g in b.get("gates") or [])
        + "</tbody></table>",
        "<h2>Gross to net</h2>",
        _dl([("gross PnL (decision prices)", fmt_money(m.get("gross_pnl"), True)),
             ("fees", fmt_money(-(m.get("fees_paid") or 0.0), True)),
             ("slippage", fmt_money(-(m.get("slippage_cost") or 0.0), True)),
             ("funding", fmt_money(m.get("funding_paid"), True)),
             ("net PnL", fmt_money(m.get("net_profit"), True)),
             ("return", fmt_pct(m.get("net_return_pct"))),
             ("ending equity", fmt_money(m.get("ending_equity")))]),
        "<h2>Behaviour</h2>",
        _dl([("signals on its own market", fmt_num(b.get("signals"), 0)),
             ("trades", fmt_num(m.get("trades"), 0)), ("win rate", fmt_pct(m.get("win_rate"))),
             ("expectancy", f"{fmt_num(m.get('expectancy_r'), 3)} R"),
             ("profit factor", fmt_pf(m.get("profit_factor"))),
             ("max drawdown", fmt_pct(m.get("max_drawdown_pct"))),
             ("liquidations", fmt_num(m.get("liquidation_count"), 0)),
             ("entry states", ", ".join(f"{k} {v}" for k, v in sorted((b.get("entry_states") or {}).items())) or DASH),
             ("signal grades", ", ".join(f"{k} {v}" for k, v in sorted((b.get("entry_quality") or {}).items())) or DASH),
             ("average / max risk per trade", f"{fmt_pct(b.get('avg_risk_pct'), 2)} / {fmt_pct(b.get('max_risk_pct'), 2)}"),
             ("average effective leverage", fmt_num(m.get("avg_effective_leverage"), 2, "x")),
             ("average / max position leverage", f"{fmt_num(b.get('avg_position_leverage'), 1, 'x')} / "
                                                 f"{fmt_num(b.get('max_position_leverage'), 0, 'x')}"),
             ("partially filled orders", fmt_num(b.get("partial_fills"), 0)),
             ("halted by the 25% book floor", "yes" if b.get("halted") else "no"),
             ("fees charged from", b.get("fee_source") or DASH)]),
        "<h2>Cost efficiency</h2>",
        _cost_dl(b.get("cost_efficiency") or {}, b),
        "<h2>Refused signals</h2>",
        "<table><thead><tr><th>reason</th><th class=num>count</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{_e(k)}</td><td class=num>{v}</td></tr>"
                  for k, v in sorted((b.get("rejects") or {}).items(), key=lambda kv: -kv[1]))
        + "</tbody></table>",
    ]
    trades = b.get("trades") or []
    p.append(f"<h2>Trade ledger ({len(trades)} closed trades, simulated)</h2>")
    p.append("<div class=wrap><table><thead><tr><th>entry</th><th>exit</th><th>side</th>"
             "<th class=num>qty</th><th class=num>entry px</th><th class=num>exit px</th>"
             "<th class=num>pnl</th><th class=num>fees</th><th class=num>net</th><th class=num>R</th>"
             "<th>exit</th></tr></thead><tbody>")
    for t in trades:
        p.append(f"<tr><td>{fmt_ts(t.get('entry_ts'))}</td><td>{fmt_ts(t.get('exit_ts'))}</td>"
                 f"<td>{_e(t.get('side'))}</td><td class=num>{fmt_num(t.get('qty'), 4)}</td>"
                 f"<td class=num>{fmt_num(t.get('entry'), 4)}</td><td class=num>{fmt_num(t.get('exit'), 4)}</td>"
                 f"<td class='num {_pn(t.get('pnl'))}'>{fmt_money(t.get('pnl'), True)}</td>"
                 f"<td class=num>{fmt_money(t.get('fees'))}</td>"
                 f"<td class='num {_pn(t.get('net'))}'>{fmt_money(t.get('net'), True)}</td>"
                 f"<td class=num>{fmt_num(t.get('r'), 2)}</td><td>{_e(t.get('exit_kind'))}</td></tr>")
    p.append("</tbody></table></div>")
    eq = b.get("equity") or []
    if eq:
        p.append("<h2>Equity (downsampled)</h2><p class=muted>"
                 + " · ".join(f"{fmt_ts(t)} {fmt_money(v)}" for t, v in eq[:: max(1, len(eq) // 12)])
                 + "</p>")
    p.append("<footer>PaperLab arena bot page · read-only · simulated records only</footer></body></html>")
    return "".join(p)


def collect_arena_bot(svc: Any, key: str) -> dict[str, Any] | None:
    return arena_bot_payload(svc.storage, None, key)


# ---- CONTROL vs +JEV ----------------------------------------------------------------------------------

def _jev_answer(s: dict[str, Any]) -> str:
    """The experiment's question, answered from the numbers and nothing else."""
    if s.get("verdict"):
        return f"{s['verdict']}: {s.get('verdict_detail') or ''}"
    imp, wor, p = s.get("improved") or 0, s.get("worsened") or 0, s.get("sign_test_p")
    mean = s.get("mean_edge_delta_usdt")
    if not (imp + wor):
        return "NO ANSWER: no pair differed"
    if p is not None and p < 0.05 and mean is not None:
        return ("YES: Jev improved significantly more pairs than it worsened" if imp > wor and mean > 0
                else "NO: Jev made significantly more pairs worse" if wor > imp and mean < 0
                else "MIXED: the pair count and the average disagree")
    return f"NOT DEMONSTRATED: {imp} improved vs {wor} worsened is not significant (sign test p={p:.2f})"


def _jev_health_line(h: dict[str, Any] | None) -> str:
    if not h:
        return "NOT STARTED"
    c = h.get("last_check") or {}
    return (f"{h.get('status')} · model {h.get('model_requested')} · last check "
            f"{c.get('status') or 'never'} {fmt_num(c.get('latency_ms'), 0, ' ms') if c else ''} "
            f"{fmt_ts(c.get('checked_at')) if c else ''}")


def _jev_html(j: dict[str, Any], base: str) -> str:
    p: list[str] = ["<h2 id=jev>CONTROL vs +JEV (Jev decision layer)</h2>"]
    p.append("<p class=muted>Same strategy, coin, timeframe, 20 USDT wallet, market data, fees, "
             "slippage, funding and risk limits; the only difference is Jev deciding TAKE / REDUCE / "
             "SKIP on the strategy's own candidate signals. JEV EDGE DELTA = +JEV net result − matched "
             "CONTROL net result. Jev never sees the control, the leaderboard or any outcome.</p>")
    p.append("<p>JEV API HEALTH: " + _e(_jev_health_line(j.get("health"))) + "</p>")
    if not j.get("ran"):
        p.append("<div class=warnbox>No CONTROL vs +JEV experiment has completed yet.</div>")
        return "".join(p)
    run, s = j.get("run") or {}, j.get("summary") or {}
    p.append(f"<p><b>DOES JEV ADD A MEASURABLE AFTER-COST EDGE? {_e(_jev_answer(s))}</b></p>")
    p.append(f"<p><b>JEV EDGE DELTA: mean {fmt_money(s.get('mean_edge_delta_usdt'), True)} USDT "
             f"({fmt_num(s.get('mean_edge_delta_return_pp'), 2, ' pp', )}) · median "
             f"{fmt_money(s.get('median_edge_delta_usdt'), True)} USDT "
             f"({fmt_num(s.get('median_edge_delta_return_pp'), 2, ' pp')})</b> · improved "
             f"{s.get('improved')} · worsened {s.get('worsened')} · unchanged {s.get('unchanged')} · "
             f"sign test p {fmt_num(s.get('sign_test_p'), 3)}</p>")
    auc = s.get("auc") or {}
    ao, so = s.get("allowed_outcomes") or {}, s.get("skipped_outcomes") or {}
    p.append("<h2>Against never trading, and does the probability discriminate?</h2>")
    p.append(_dl([
        ("always-skip null: mean delta vs control", f"{fmt_money(s.get('null_mean_edge_delta_usdt'), True)} USDT per pair"),
        ("+JEV: mean delta vs control", f"{fmt_money(s.get('mean_edge_delta_usdt'), True)} USDT per pair"),
        ("+JEV bots net positive / negative", f"{s.get('pairs_jev_profitable')} / {s.get('pairs_jev_losing')}"),
        ("trades Jev allowed", f"{ao.get('n')} · net {fmt_money(ao.get('net'), True)} · win "
                               f"{fmt_pct(ao.get('win_rate'))} · mean {fmt_num(ao.get('mean_r'), 3)}R"),
        ("candidates Jev skipped (shadow)", f"{so.get('n')} · win {fmt_pct(so.get('win_rate'))} · mean "
                                            f"{fmt_num(so.get('mean_r'), 3)}R"),
        ("AUC, take probability → win", f"{fmt_num(auc.get('auc'), 3)} (95% CI {fmt_num(auc.get('low'), 3)}–"
                                        f"{fmt_num(auc.get('high'), 3)}; 0.500 = coin flip)"),
        ("Spearman, take probability vs R", fmt_num(s.get("spearman_take_p_vs_r"), 3)),
        ("take probability min / q1 / median / q3 / max",
         " / ".join(fmt_num(x, 2) for x in (s.get("take_probability_quartiles") or []))),
        ("risk_state answers", s.get("risk_state_counts")), ("regime answers", s.get("regime_counts")),
    ]))
    qs = s.get("quintiles") or []
    if qs:
        p.append("<table><thead><tr><th>probability band (equal counts)</th><th class=num>candidates</th>"
                 "<th class=num>win rate</th><th class=num>mean R</th></tr></thead><tbody>")
        for q in qs:
            p.append(f"<tr><td>{fmt_num(q.get('p_low'), 3)} – {fmt_num(q.get('p_high'), 3)}</td>"
                     f"<td class=num>{q.get('candidates')}</td><td class=num>{fmt_pct(q.get('win_rate'))}</td>"
                     f"<td class='num {_pn(q.get('mean_r'))}'>{fmt_num(q.get('mean_r'), 3)}</td></tr>")
        p.append("</tbody></table>")
    p.append(_dl([
        ("experiment id", run.get("run_id")), ("arena run", run.get("arena_run_id")),
        ("window", f"{run.get('first_month')} → {run.get('last_month')}"),
        ("model requested", run.get("model")),
        ("model resolved", ", ".join(s.get("models_resolved") or []) or DASH),
        ("prompt / policy", f"{run.get('prompt_version')} / {run.get('policy_version')}"),
        ("status", (run.get("status") or "").upper()),
        ("pairs", s.get("pairs")), ("Jev bots / control bots", f"{s.get('jev_bots')} / {s.get('control_bots')}"),
        ("candidate signals reviewed", s.get("candidate_signals")),
        ("Jev API calls / cache hits", f"{s.get('jev_calls')} / {s.get('cache_hits')}"),
        ("API errors", f"{s.get('api_errors')} ({fmt_pct(s.get('error_rate'), 2)}) {s.get('error_codes') or ''}"),
        ("accepted / reduced / skipped", f"{s.get('accepted')} / {s.get('reduced')} / {s.get('skipped')} "
                                          f"(ATTACK {s.get('attack')})"),
        ("acceptance rate", fmt_pct(s.get("acceptance_rate"))),
        ("skipped candidates that would have lost / won", f"{s.get('skipped_losing')} / {s.get('skipped_winning')}"),
        ("latency avg / p95", f"{fmt_num(s.get('latency_avg_ms'), 0)} ms / {fmt_num(s.get('latency_p95_ms'), 0)} ms"),
        ("input tokens", fmt_num(s.get("total_input_tokens"), 0)),
        ("AI cost", f"{fmt_num(s.get('total_cost_usd'), 6)} USD ({fmt_num(s.get('cost_per_decision_usd'), 7)} per call)"),
        ("AI cost / gross trading profit", fmt_pct(s.get("ai_cost_vs_gross_profit"), 3)),
        ("total CONTROL net / +JEV net", f"{fmt_money(s.get('total_control_net'), True)} / "
                                         f"{fmt_money(s.get('total_jev_net'), True)} USDT"),
        ("best improvement", f"{s.get('best_pair')} {fmt_money(s.get('best_edge_delta_usdt'), True)}"),
        ("worst degradation", f"{s.get('worst_pair')} {fmt_money(s.get('worst_edge_delta_usdt'), True)}"),
    ]))
    p.append("<div class=wrap><table><thead><tr><th>pair</th><th>coin</th><th>tf</th>"
             "<th class=num>CONTROL net</th><th class=num>+JEV net</th><th class=num>EDGE DELTA</th>"
             "<th class=num>Δ return</th><th class=num>trades C / J</th><th class=num>PF C / J</th>"
             "<th class=num>max DD C / J</th><th class=num>reviewed</th><th class=num>accepted</th>"
             "<th class=num>AI cost</th><th class=num>economic net</th><th>verdict</th></tr></thead><tbody>")
    for r in sorted(j.get("pairs") or [], key=lambda r: -(r.get("edge_delta_usdt") or 0)):
        c, x, st = r.get("control") or {}, r.get("jev") or {}, r.get("jev_stats") or {}
        cls = {"IMPROVED": "up", "WORSENED": "down"}.get(r.get("verdict"), "muted")
        p.append(
            f"<tr><td><a href='{base}/jev/pair/{_e(r.get('pair_id'))}'>{_e(r.get('control_key'))}</a></td>"
            f"<td>{_e(r.get('coin'))}</td><td>{_e(r.get('timeframe'))}</td>"
            f"<td class='num {_pn(c.get('net_profit'))}'>{fmt_money(c.get('net_profit'), True)}</td>"
            f"<td class='num {_pn(x.get('net_profit'))}'>{fmt_money(x.get('net_profit'), True)}</td>"
            f"<td class='num {cls}'>{fmt_money(r.get('edge_delta_usdt'), True)}</td>"
            f"<td class=num>{fmt_num(r.get('edge_delta_return_pp'), 2, ' pp')}</td>"
            f"<td class=num>{fmt_num(c.get('trades'), 0)} / {fmt_num(x.get('trades'), 0)}</td>"
            f"<td class=num>{fmt_pf(c.get('profit_factor'))} / {fmt_pf(x.get('profit_factor'))}</td>"
            f"<td class=num>{fmt_pct(c.get('max_drawdown_pct'))} / {fmt_pct(x.get('max_drawdown_pct'))}</td>"
            f"<td class=num>{fmt_num(st.get('reviewed'), 0)}</td><td class=num>{fmt_pct(st.get('acceptance_rate'))}</td>"
            f"<td class=num>{fmt_num(st.get('cost_usd'), 6)}</td>"
            f"<td class=num>{fmt_money(r.get('economic_net_after_ai'), True)}</td>"
            f"<td class={cls}>{_e(r.get('verdict'))}</td></tr>")
    p.append("</tbody></table></div>")
    p.append(f"<p>Improved: {_e(', '.join(s.get('improved_keys') or []) or 'NONE')}</p>")
    p.append(f"<p>Worsened: {_e(', '.join(s.get('worsened_keys') or []) or 'NONE')}</p>")
    p.append(_calibration_html(s.get("calibration") or []))
    return "".join(p)


def _calibration_html(rows: list[dict[str, Any]]) -> str:
    out = ["<h2>Jev calibration (take probability → realised candidate outcome)</h2>",
           "<p class=muted>Every reviewed candidate, taken (its real trade) or skipped (its shadow "
           "trade at the control's size). If higher confidence means better trades, mean R rises "
           "down the table.</p>",
           "<table><thead><tr><th>take probability</th><th class=num>candidates</th>"
           "<th class=num>taken</th><th class=num>shadow</th><th class=num>mean R</th>"
           "<th class=num>win rate</th><th class=num>sum R</th></tr></thead><tbody>"]
    for r in rows:
        out.append(f"<tr><td>{_e(r.get('bucket'))}</td><td class=num>{r.get('candidates')}</td>"
                   f"<td class=num>{r.get('taken')}</td><td class=num>{r.get('shadow')}</td>"
                   f"<td class='num {_pn(r.get('mean_r'))}'>{fmt_num(r.get('mean_r'), 3)}</td>"
                   f"<td class=num>{fmt_pct(r.get('win_rate'))}</td>"
                   f"<td class=num>{fmt_num(r.get('sum_r'), 2)}</td></tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _jev_text(j: dict[str, Any]) -> list[str]:
    L = ["CONTROL vs +JEV", f"  JEV API HEALTH   {_jev_health_line(j.get('health'))}"]
    if not j.get("ran"):
        L.append("  no completed experiment yet")
        return L
    run, s = j.get("run") or {}, j.get("summary") or {}
    L += [f"  experiment       {run.get('run_id')}  (arena {run.get('arena_run_id')})",
          f"  model            {run.get('model')} -> {', '.join(s.get('models_resolved') or []) or '-'}",
          f"  prompt / policy  {run.get('prompt_version')} / {run.get('policy_version')}",
          f"  window           {run.get('first_month')} -> {run.get('last_month')}",
          f"  pairs            {s.get('pairs')}  (Jev bots {s.get('jev_bots')}, control bots {s.get('control_bots')})",
          f"  ANSWER           {_jev_answer(s)}",
          f"  never-trade null mean delta {fmt_money(s.get('null_mean_edge_delta_usdt'), True)} vs +JEV "
          f"{fmt_money(s.get('mean_edge_delta_usdt'), True)} USDT per pair",
          f"  AUC take_p->win {fmt_num((s.get('auc') or {}).get('auc'), 3)} "
          f"[{fmt_num((s.get('auc') or {}).get('low'), 3)}, {fmt_num((s.get('auc') or {}).get('high'), 3)}]"
          f"   spearman take_p vs R {fmt_num(s.get('spearman_take_p_vs_r'), 3)}",
          f"  allowed trades   {(s.get('allowed_outcomes') or {}).get('n')} net "
          f"{fmt_money((s.get('allowed_outcomes') or {}).get('net'), True)} mean "
          f"{fmt_num((s.get('allowed_outcomes') or {}).get('mean_r'), 3)}R; skipped mean "
          f"{fmt_num((s.get('skipped_outcomes') or {}).get('mean_r'), 3)}R",
          f"  JEV EDGE DELTA   mean {fmt_money(s.get('mean_edge_delta_usdt'), True)} USDT, median "
          f"{fmt_money(s.get('median_edge_delta_usdt'), True)} USDT; improved {s.get('improved')}, "
          f"worsened {s.get('worsened')}, unchanged {s.get('unchanged')}; sign test p "
          f"{fmt_num(s.get('sign_test_p'), 3)}",
          f"  decisions        {s.get('candidate_signals')} reviewed, {s.get('jev_calls')} API calls, "
          f"{s.get('cache_hits')} cache hits, {s.get('api_errors')} errors ({fmt_pct(s.get('error_rate'), 2)})",
          f"  actions          accepted {s.get('accepted')}, reduced {s.get('reduced')}, skipped "
          f"{s.get('skipped')}; acceptance {fmt_pct(s.get('acceptance_rate'))}",
          f"  skipped would    lose {s.get('skipped_losing')} / win {s.get('skipped_winning')}",
          f"  cost             {fmt_num(s.get('total_cost_usd'), 6)} USD, {fmt_num(s.get('total_input_tokens'), 0)} "
          f"input tokens; latency avg {fmt_num(s.get('latency_avg_ms'), 0)} ms p95 "
          f"{fmt_num(s.get('latency_p95_ms'), 0)} ms", ""]
    L.append(f"  {'pair':<30}{'ctrl net':>9}{'jev net':>9}{'delta':>8}{'d.ret':>8}{'tr c/j':>9}"
             f"{'rev':>5}{'acc':>6}  verdict")
    for r in sorted(j.get("pairs") or [], key=lambda r: -(r.get("edge_delta_usdt") or 0)):
        c, x, st = r.get("control") or {}, r.get("jev") or {}, r.get("jev_stats") or {}
        L.append(f"  {str(r.get('control_key'))[:29]:<30}{fmt_money(c.get('net_profit'), True):>9}"
                 f"{fmt_money(x.get('net_profit'), True):>9}{fmt_money(r.get('edge_delta_usdt'), True):>8}"
                 f"{fmt_num(r.get('edge_delta_return_pp'), 1):>8}"
                 f"{fmt_num(c.get('trades'), 0):>5}/{fmt_num(x.get('trades'), 0):<3}"
                 f"{fmt_num(st.get('reviewed'), 0):>5}{fmt_pct(st.get('acceptance_rate'), 0):>6}  {r.get('verdict')}")
    L.append("")
    L.append("  CALIBRATION  bucket      n   mean R   win%")
    for r in s.get("calibration") or []:
        L.append(f"               {r.get('bucket'):<10}{r.get('candidates'):>5}{fmt_num(r.get('mean_r'), 3):>9}"
                 f"{fmt_pct(r.get('win_rate'), 0):>7}")
    return L


def collect_jev_pair(svc: Any, pair: str) -> dict[str, Any] | None:
    return jev_pair_payload(svc.storage, None, pair)


def render_jev_pair_html(svc: Any, d: dict[str, Any], base: str = "/public/competition/report") -> str:
    pr = d.get("pair") or {}
    c, x, st = pr.get("control") or {}, pr.get("jev") or {}, pr.get("jev_stats") or {}
    rows = [("Return", fmt_pct(c.get("net_return_pct")), fmt_pct(x.get("net_return_pct"))),
            ("Net PnL (USDT)", fmt_money(c.get("net_profit"), True), fmt_money(x.get("net_profit"), True)),
            ("Gross PnL", fmt_money(c.get("gross_pnl"), True), fmt_money(x.get("gross_pnl"), True)),
            ("Trades", fmt_num(c.get("trades"), 0), fmt_num(x.get("trades"), 0)),
            ("Expectancy", fmt_num(c.get("expectancy_r"), 3) + "R", fmt_num(x.get("expectancy_r"), 3) + "R"),
            ("Profit factor", fmt_pf(c.get("profit_factor")), fmt_pf(x.get("profit_factor"))),
            ("Max drawdown", fmt_pct(c.get("max_drawdown_pct")), fmt_pct(x.get("max_drawdown_pct"))),
            ("Fees", fmt_money(c.get("fees_paid")), fmt_money(x.get("fees_paid"))),
            ("Slippage", fmt_money(c.get("slippage_cost")), fmt_money(x.get("slippage_cost"))),
            ("Funding", fmt_money(c.get("funding_paid"), True), fmt_money(x.get("funding_paid"), True)),
            ("Liquidations", fmt_num(c.get("liquidation_count"), 0), fmt_num(x.get("liquidation_count"), 0)),
            ("State", c.get("state"), x.get("state"))]
    p: list[str] = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content=noindex>",
        f"<title>{_e(pr.get('pair_id'))} — CONTROL vs +JEV</title><style>{_CSS}</style></head><body>",
        f"<h1>{_e(pr.get('control_key'))} vs {_e(pr.get('jev_key'))}</h1>",
        f"<p class=muted><a href='{base}#jev'>&larr; back to report</a> · experiment {_e(d.get('run_id'))} "
        "· simulated · read-only</p>",
        "<table><thead><tr><th></th><th class=num>CONTROL</th><th class=num>+JEV</th></tr></thead><tbody>"]
    for label, a, b in rows:
        p.append(f"<tr><td>{_e(label)}</td><td class=num>{_e(a)}</td><td class=num>{_e(b)}</td></tr>")
    p.append("</tbody></table>")
    delta = pr.get("edge_delta_usdt")
    p.append(f"<p><b>JEV EDGE DELTA: {fmt_money(delta, True)} USDT "
             f"({fmt_num(pr.get('edge_delta_return_pp'), 2, ' percentage points')}) — {_e(pr.get('verdict'))}</b>"
             f" · economic net after AI cost {fmt_money(pr.get('economic_net_after_ai'), True)} USDT</p>")
    p.append(_dl([("decisions", st.get("reviewed")), ("accepted / reduced / skipped",
                  f"{st.get('accepted')} / {st.get('reduced')} / {st.get('skipped')}"),
                  ("acceptance rate", fmt_pct(st.get("acceptance_rate"))),
                  ("skipped that would have lost / won", f"{st.get('skipped_losing')} / {st.get('skipped_winning')}"),
                  ("shadow net of skipped candidates", fmt_money(st.get("skipped_shadow_net"), True)),
                  ("API errors", f"{st.get('errors')} {st.get('error_codes') or ''}"),
                  ("latency avg / p95", f"{fmt_num(st.get('latency_avg_ms'), 0)} / {fmt_num(st.get('latency_p95_ms'), 0)} ms"),
                  ("cache hit rate", fmt_pct(st.get("cache_hit_rate"))),
                  ("input tokens / AI cost", f"{fmt_num(st.get('input_tokens'), 0)} / {fmt_num(st.get('cost_usd'), 6)} USD"),
                  ("models resolved", ", ".join(st.get("models_resolved") or []) or DASH)]))
    p.append(_calibration_html(d.get("calibration") or []))
    decs = d.get("decisions") or []
    p.append(f"<h2>Decision inspector ({len(decs)} of {d.get('decisions_total')})</h2>")
    p.append("<div class=wrap><table><thead><tr><th>signal time (UTC)</th><th>side</th>"
             "<th class=num>take p</th><th class=num>quality</th><th>risk state</th><th>regime</th>"
             "<th>action</th><th class=num>x</th><th>result</th><th>outcome</th><th class=num>net</th>"
             "<th class=num>R</th><th>market (5-bar ret · ATR% · RSI · trend)</th><th>signal (stop · target · RR)</th>"
             "<th>error</th></tr></thead><tbody>")
    for r in decs:
        m = (r.get("state") or {}).get("market") or {}
        g = (r.get("state") or {}).get("signal") or {}
        p.append(f"<tr><td>{fmt_ts(r.get('signal_ts'))}</td><td>{_e(r.get('side'))}</td>"
                 f"<td class=num>{fmt_num(r.get('take_probability'), 3)}</td>"
                 f"<td class=num>{fmt_num(r.get('setup_quality'), 2)}</td><td>{_e(r.get('risk_state') or DASH)}</td>"
                 f"<td>{_e(r.get('regime') or DASH)}</td><td>{_e(r.get('final_action'))}</td>"
                 f"<td class=num>{fmt_num(r.get('risk_multiplier'), 2)}</td><td>{_e(r.get('result') or DASH)}</td>"
                 f"<td>{_e(r.get('outcome_kind') or DASH)} {_e(r.get('outcome_exit') or '')}</td>"
                 f"<td class='num {_pn(r.get('outcome_net'))}'>{fmt_money(r.get('outcome_net'), True)}</td>"
                 f"<td class=num>{fmt_num(r.get('outcome_r'), 2)}</td>"
                 f"<td>{fmt_pct(m.get('ret_5'), 2)} · {fmt_pct(m.get('atr_pct'), 2)} · {fmt_num(m.get('rsi_14'), 0)} · "
                 f"{fmt_pct(m.get('ema20_vs_ema50'), 2)}</td>"
                 f"<td>{fmt_pct(g.get('stop_distance_pct'), 2)} · {fmt_pct(g.get('target_distance_pct'), 2)} · "
                 f"{fmt_num(g.get('reward_risk'), 2)}</td><td>{_e(r.get('error_code') or '')}</td></tr>")
    p.append("</tbody></table></div><footer>PaperLab · CONTROL vs +JEV · read-only</footer></body></html>")
    return "".join(p)


# ---- fee model --------------------------------------------------------------------------------------

LEGACY_NOTE = "legacy: charged 0.040% taker / 0.020% maker (the pre-2026-09-23 default) while declaring 0.050%"


def fee_rows(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Which fee model produced each result on this page -- stated, not assumed."""
    rows: list[tuple[str, str]] = []
    fees = (data.get("diagnostics") or {}).get("paper_engine_fees") or {}
    if fees.get("taker") is not None:
        rows.append(("live paper engine (now)", f"{fees.get('source')}: maker {fees['maker']:.3%} / taker {fees['taker']:.3%}"))
    arena = (data.get("arena") or {}).get("config") or {}
    if arena:
        f = arena.get("fees") or {}
        rows.append((f"arena {((data.get('arena') or {}).get('run') or {}).get('run_id')}",
                     f"{arena.get('fee_source')}: maker {f.get('maker_rate', 0):.3%} / taker {f.get('taker_rate', 0):.3%}"))
    jev = (data.get("jev") or {}).get("run") or {}
    if jev.get("run_id"):
        rows.append((f"Jev experiment {jev['run_id']}", "inherits its arena's schedule: maker 0.020% / taker 0.050%"))
    val = data.get("validation") or {}
    if val.get("run_id"):
        rows.append((f"validation {val['run_id']}", LEGACY_NOTE))
    season = data.get("season") or {}
    if season.get("run_id"):
        rows.append((f"season {season['run_id']}", LEGACY_NOTE))
    return rows


def _fee_model_html(data: dict[str, Any]) -> str:
    return ("<h2>Fee model</h2><p class=muted>One schedule per venue, shared by the live paper engine, "
            "replays, competitions and Jev. Binance USD-M: maker 0.020% / taker 0.050%. Bybit linear: "
            "maker 0.020% / taker 0.055%. Stored runs keep the model they were produced with.</p>"
            + _dl(fee_rows(data)))


def _fee_model_text(data: dict[str, Any]) -> list[str]:
    return ["FEE MODEL"] + [f"  {k:<32} {v}" for k, v in fee_rows(data)]



def _cost_dl(ce: dict[str, Any], b: dict[str, Any]) -> str:
    return _dl([
        ("class", ce.get("class")),
        ("gross edge (decision prices)", fmt_money(ce.get("gross_edge"), True)),
        ("fees / slippage / funding", f"{fmt_money(-(ce.get('fees') or 0.0), True)} / "
                                      f"{fmt_money(-(ce.get('slippage') or 0.0), True)} / "
                                      f"{fmt_money(ce.get('funding'), True)}"),
        ("cost-to-edge", fmt_num(ce.get("cost_to_edge"), 2, "x") if ce.get("cost_to_edge") is not None
         else "costs exceed a non-positive edge"),
        ("average gross edge per trade", f"{fmt_money(ce.get('avg_gross_edge_per_trade'), True)} USDT "
                                         f"({fmt_num(ce.get('avg_gross_edge_bps'), 1, ' bps')})"),
        ("average round-trip cost", f"{fmt_money(ce.get('avg_round_trip_cost'))} USDT "
                                    f"({fmt_num(ce.get('avg_round_trip_cost_bps'), 1, ' bps')})"),
        ("fees / gross winning PnL", fmt_pct(ce.get("fees_to_gross_winning"))),
        ("break-even win rate (net payoffs) / actual", f"{fmt_pct(ce.get('break_even_win_rate'))} / "
                                                      f"{fmt_pct(ce.get('win_rate'))}"),
        ("break-even win rate before fees / actual", f"{fmt_pct(ce.get('gross_break_even_win_rate'))} / "
                                                     f"{fmt_pct(ce.get('gross_win_rate'))}"),
        ("trades per day", fmt_num(ce.get("trades_per_day"), 2)),
        ("average holding", fmt_num(ce.get("avg_holding_minutes"), 0, " min")),
        ("turnover (notional / start equity)", fmt_num(ce.get("turnover"), 1, "x")),
        ("JEV-ELIGIBLE CONTROL", "YES" if b.get("jev_eligible") else
         "NO: " + "; ".join(b.get("jev_eligibility_reasons") or [])),
    ])


# ---- live shadow + discovery runs (2026-09-23) ------------------------------------------------------

ROLE_NOTE = {"TEST": "the one evaluation of the frozen v2 strategies (data they were never tuned on)",
             "DEVELOPMENT": "design data for v2; chose the TEST field; NOT performance",
             "": "v1 specialists, July-August 2026 -- inspected repeatedly, treated as contaminated"}


def _shadow_html(sh: dict[str, Any]) -> str:
    st = sh.get("status") or {}
    cfg = sh.get("config") or {}
    jl = sh.get("jev_live") or {}
    v = jl.get("verdict") or {}
    hv = sh.get("jev_historical") or {}
    elig = sh.get("eligible_controls") or []
    out = ["<h2 id=live>Live shadow — forward test</h2>",
           "<p class=muted>Frozen v2 bots from the TEST arena trade simulated 20 USDT books on live Binance "
           "USD-M market data through the same engine, fees, execution model, cost gate and RiskManager as the "
           "replay. Nothing can place an order. FORWARD data: unseen by every strategy and by Jev.</p>"]
    if len(elig) < int(cfg.get("min_pairs") or 10):
        out.append(f"<p class=warn><b>INSUFFICIENT JEV-ELIGIBLE CONTROLS</b> — {len(elig)} of "
                   f"{len(cfg.get('controls') or [])} frozen controls pass JEV_ELIGIBLE_CONTROL on TEST data "
                   f"({_e(', '.join(k.split('@')[0] for k in elig) or 'none')}); {cfg.get('min_pairs') or 10} are "
                   f"required for a live Jev verdict. Pairs running: {st.get('pairs', 0)}.</p>")
    ft = sh.get("forward_total") or {}
    out.append(_dl([
        ("status", st.get("status")), ("session", (sh.get("session") or {}).get("session_id") or "none"),
        ("source run", f"{cfg.get('source_label') or '—'} ({cfg.get('source_run_id') or '—'})"),
        ("frozen fingerprints", " · ".join(f"{k} {v2}" for k, v2 in (cfg.get("strategy_fingerprints") or {}).items()) or "—"),
        ("active controls", f"{st.get('active', 0)} of {st.get('controls', 0)}"),
        ("CONTROL vs +JEV pairs", st.get("pairs", 0)),
        ("positions open", st.get("positions_open", 0)),
        ("forward trades (controls, all sessions)", ft.get("trades", 0)),
        ("forward net", fmt_money(ft.get("net"), signed=True)),
        ("Jev state here", st.get("jev_state") or "—"),
    ]))
    out.append("<h3>Jev V1 on live shadow</h3>")
    out.append(f"<p class={'down' if v.get('verdict') == 'NO EDGE' else 'warn'}><b>LIVE VERDICT: "
               f"{_e(v.get('verdict') or 'NO VERDICT')}</b> — {_e(v.get('status') or '')}: {_e(v.get('why') or '')}</p>")
    auc = v.get("auc") or {}
    out.append(_dl([
        ("decisions / resolved", f"{jl.get('decisions', 0)} / {jl.get('resolved', 0)} (verdict needs {v.get('min_resolved', 100)})"),
        ("skip rate", fmt_pct(jl.get("skip_rate"), 1)),
        ("failure rate / timeouts", f"{fmt_pct(jl.get('failure_rate'), 2)} / {fmt_pct(jl.get('timeout_rate'), 2)}"),
        ("latency p50 / p95 / p99", " / ".join(fmt_num(jl.get(k), 0) for k in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms")) + " ms"),
        ("AI latency slippage (mean / p95)", f"{fmt_num(jl.get('latency_slippage_bps_mean'), 2)} / {fmt_num(jl.get('latency_slippage_bps_p95'), 2)} bps"),
        ("AUC take probability -> win", f"{fmt_num(auc.get('auc'), 3)} [{fmt_num(auc.get('low'), 3)}, {fmt_num(auc.get('high'), 3)}]"),
        ("+JEV minus control / minus always-skip", f"{fmt_money(v.get('jev_minus_control'), signed=True)} / {fmt_money(v.get('jev_minus_always_skip'), signed=True)}"),
        ("trades Jev let through", v.get("trades_taken", 0)),
    ]))
    rows = "".join(f"<tr><td>{_e(r['bucket'])}</td><td class=num>{r['n']}</td><td class=num>{fmt_num(r.get('mean_take_probability'), 3)}</td>"
                   f"<td class=num>{fmt_pct(r.get('win_rate'), 1)}</td><td class=num>{fmt_num(r.get('mean_r'), 2)}</td></tr>"
                   for r in jl.get("calibration") or [])
    out.append("<table><thead><tr><th>take probability</th><th>n</th><th>mean p</th><th>win rate</th><th>mean R</th></tr></thead>"
               f"<tbody>{rows}</tbody></table>")
    out.append(f"<h3>JEV V1 HISTORICAL DISCOVERY VERDICT: {_e(hv.get('verdict') or 'NO EDGE')}</h3>"
               f"<p>Experiment {_e(hv.get('run_id'))}, {_e(hv.get('period'))}: skip rate {fmt_pct(hv.get('skip_rate'), 1)}, "
               f"AUC {fmt_num(hv.get('auc'), 3)} {_e(hv.get('auc_ci'))}, {_e(hv.get('null_note'))}. "
               "No prompt or threshold was changed; V1 is judged again only on unseen live data.</p>")
    return "".join(out)


def _runs_html(runs: list[dict[str, Any]]) -> str:
    rows = []
    for r in runs:
        role = r.get("dataset_role") or ""
        rows.append(f"<tr><td>{_e(r['run_id'])}</td><td>{_e(r.get('label'))}</td>"
                    f"<td>{_e(role or ('v1 discovery' if r.get('params_version') == 'v1' else '—'))}</td>"
                    f"<td>{_e(r.get('first_month'))} → {_e(r.get('last_month'))}</td>"
                    f"<td class=num>{r.get('active_bots') or 0}</td><td class=num>{r.get('advanced') or 0}</td>"
                    f"<td class=num>{r.get('profitable') or 0}</td><td class=num>{fmt_money(r.get('gross'), signed=True)}</td>"
                    f"<td class=num>{fmt_money(-(r.get('fees') or 0), signed=True)}</td>"
                    f"<td class=num>{fmt_money(r.get('net'), signed=True)}</td>"
                    f"<td>{_e(', '.join(k.split('@')[0] for k in r.get('advanced_keys') or []) or '—')}</td>"
                    f"<td class=muted>{_e(ROLE_NOTE.get(role, ''))}</td></tr>")
    return ("<h2 id=runs>Discovery runs and their data</h2>"
            "<p class=muted>Dataset split fixed before any v2 result (docs/DATASET_SPLIT_V2.md): DEVELOPMENT "
            "2025-11 → 2026-04, TEST 2026-05 → 2026-06, July-August 2026 contaminated, FORWARD = live shadow. "
            "v2 was frozen before TEST (docs/V2_FREEZE.md); the TEST field was pre-registered from DEVELOPMENT "
            "by one rule (positive gross edge). ADVANCE earns multi-year validation, never live trading.</p>"
            "<table><thead><tr><th>run</th><th>label</th><th>role</th><th>window</th><th>bots</th><th>advanced</th>"
            "<th>net +</th><th>gross</th><th>fees</th><th>net</th><th>advanced bots</th><th>meaning</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def _shadow_text(sh: dict[str, Any]) -> list[str]:
    st = sh.get("status") or {}
    jl = sh.get("jev_live") or {}
    v = jl.get("verdict") or {}
    elig = sh.get("eligible_controls") or []
    return ["", "LIVE SHADOW (FORWARD TEST)", "-" * 72,
            f"status             {st.get('status')}",
            f"active controls    {st.get('active', 0)} of {st.get('controls', 0)}",
            f"eligible controls  {len(elig)} (10 required for a live Jev verdict)"
            + ("  -> INSUFFICIENT JEV-ELIGIBLE CONTROLS" if len(elig) < 10 else ""),
            f"pairs running      {st.get('pairs', 0)}",
            f"live Jev verdict   {v.get('verdict') or 'NO VERDICT'} - {v.get('why') or ''}",
            f"decisions          {jl.get('decisions', 0)} (resolved {jl.get('resolved', 0)})",
            "JEV V1 HISTORICAL DISCOVERY VERDICT: NO EDGE (skip 99.7%, AUC 0.512, always-skip slightly better)"]


# ---- frozen v2 candidates: multi-year validation + forward shadow (2026-09-23) --------------------------

CONTAMINATION = [
    ("2021-01 → 2025-10", "XRP, BNB", "HISTORICAL HOLDOUT", "never loaded before 2026-09-23; v2 not designed on it",
     "YES — family-level limitation: v1 ancestors of S12/S26 were seen on BTC/ETH/SOL over this period (bdddc67f6315)"),
    ("2021-01 → 2026-08", "BTC, ETH, SOL", "v1 multi-year validation", "all 27 v1 strategies (bdddc67f6315)", "not used for these candidates"),
    ("2025-11 → 2026-04", "9 coins", "DEVELOPMENT", "designing v2, choosing the TEST field", "NO"),
    ("2026-05 → 2026-06", "9 coins", "TEST", "the one v2 evaluation", "NO — consumed"),
    ("2026-07 → 2026-08", "9 coins", "CONTAMINATED", "v1 arena, Jev V1 experiment, seasons", "NO"),
    ("2026-09-23 →", "9 coins", "FORWARD", "live shadow", "evaluation only"),
]


def _candidates_data(storage: Any, shadow: dict[str, Any]) -> dict[str, Any]:
    from app.core.candidates_view import candidates_payload
    try:
        return candidates_payload(storage, shadow)
    except Exception as exc:                           # the report must render even if this fails
        return {"error": f"{type(exc).__name__}: {exc}"[:200], "candidates": []}


def _pf(v: Any) -> str:
    return "∞" if isinstance(v, (int, float)) and v >= 999 else fmt_num(v, 2)


def _candidates_html(d: dict[str, Any]) -> str:
    run = d.get("run") or {}
    proto = d.get("protocol") or {}
    out = ["<h2 id=candidates>Frozen v2 candidates — multi-year validation</h2>",
           "<p class=muted>Three bots survived the v2 TEST run. They have earned multi-year validation only: not "
           "qualified, never live. <b>HISTORICAL VALIDATION and LIVE FORWARD SHADOW are separate evidence; their PnLs "
           "are never added.</b> Protocol: docs/V2_MULTIYEAR_PROTOCOL.md (gates fixed before any result).</p>",
           _dl([("run", f"{run.get('run_id') or 'NOT RUN'} ({run.get('status') or '—'})"),
                ("protocol", f"{proto.get('version') or '—'} · {run.get('protocol_fingerprint') or '—'}"),
                ("window", f"{run.get('first_month') or '—'} → {run.get('last_month') or '—'} (+ warmup 2020-12)"),
                ("historical venue", "BINANCE USD-M (data, fees, filters, brackets, funding)"),
                ("forward venue", "BINANCE USD-M live shadow"),
                ("target live venue", "BYBIT LINEAR — configured real-money venue; Bybit execution validation required "
                                      "before any promotion (docs/VENUE_AUDIT.md)"),
                ("dataset", (run.get("dataset") or {}).get("fingerprint") or "—")])]
    rows = "".join(f"<tr><td>{_e(a)}</td><td>{_e(b)}</td><td>{_e(c)}</td><td>{_e(x)}</td><td>{_e(y)}</td></tr>"
                   for a, b, c, x, y in CONTAMINATION)
    out.append("<h3>Data contamination map</h3><table><thead><tr><th>date range</th><th>coins</th><th>role</th>"
               f"<th>used for</th><th>safe for v2 evaluation?</th></tr></thead><tbody>{rows}</tbody></table>")
    for c in d.get("candidates") or []:
        out.append(_candidate_html(c))
    return "".join(out)


def _candidate_html(c: dict[str, Any]) -> str:
    h0 = c.get("historical") or {}
    man = c.get("manifest") or {}
    out = [f"<h3 id='cand-{_e(c['key'])}'>{_e(c['label'])} — {_e(c['key'])}</h3>"]
    out.append("<p>" + " → ".join(f"<b>{_e(p['stage'])}</b> {_e(p['status'])}" for p in c.get("pipeline") or []) + "</p>")
    out.append(_dl([("source fingerprint", " · ".join(f"{k} {v}" for k, v in (man.get("source_fingerprint") or {}).items())),
                    ("manifest fingerprint", man.get("manifest_fingerprint")),
                    ("symbol / signal timeframe / context", f"{man.get('symbol')} / {man.get('timeframe')} / {man.get('context_timeframe')}"),
                    ("parameters", ", ".join(f"{k}={v}" for k, v in (man.get("parameters") or {}).items())),
                    ("risk", f"{(man.get('risk_profile') or {}).get('name')} · ordinary {(man.get('risk_engine') or {}).get('risk_per_trade_pct')} · "
                             f"halt floor {(man.get('risk_engine') or {}).get('strategy_halt_pct')} below start · halt DD "
                             f"{(man.get('risk_profile') or {}).get('halt_drawdown')} · ceiling {man.get('leverage_ceiling')}x ({man.get('leverage_policy')})"),
                    ("cost gate", f"edge-to-cost >= {(man.get('cost_gate') or {}).get('min_edge_to_cost')}"),
                    ("fees / execution", f"{(man.get('fee_schedule') or {}).get('source')} maker {(man.get('fee_schedule') or {}).get('maker_rate')} "
                                         f"taker {(man.get('fee_schedule') or {}).get('taker_rate')} · latency "
                                         f"{(man.get('execution') or {}).get('signal_latency_ms')}+{(man.get('execution') or {}).get('order_latency_ms')} ms")]))
    if not h0:
        out.append("<p class=muted>Multi-year validation has not completed.</p>")
    else:
        m = h0.get("metrics") or {}
        w = h0.get("windows") or {}
        hd = h0.get("headroom") or {}
        mc = h0.get("monte_carlo") or {}
        rb = h0.get("robustness") or {}
        out.append(f"<p class={'up' if h0.get('verdict') == 'PASS' else 'down'}><b>MULTI-YEAR VERDICT: {_e(h0.get('verdict'))}</b> "
                   f"— {_e(', '.join(f'{k} {v}' for k, v in (h0.get('stages') or {}).items() if k != 'VERDICT'))}</p>")
        out.append("<h4>HISTORICAL VALIDATION — BINANCE USD-M, one continuous ledger from 20 USDT</h4>")
        halt, cap = h0.get("halt") or {}, h0.get("capacity") or {}
        out.append(_dl([("equity", f"{fmt_money(m.get('starting_equity'))} → {fmt_money(m.get('ending_equity'))} "
                                   f"({fmt_pct(m.get('net_return_pct'), 1)})"),
                        ("halt", f"HALTED ({halt.get('kind')}) {fmt_ts(halt.get('ts'))} — {halt.get('note')}; "
                                 f"{halt.get('refused_after')} later signals refused" if halt.get("kind") else "never halted"),
                        ("capacity at 20 USDT", f"{cap.get('trades')} trades from {cap.get('signals')} signals; "
                                                f"{fmt_pct(cap.get('below_exchange_minimum_share'), 0)} of signals below the exchange minimum"),
                        ("gross / fees / slippage / funding / net", f"{fmt_money(m.get('gross_pnl'), True)} / {fmt_money(-(m.get('fees_paid') or 0), True)} / "
                                                                    f"{fmt_money(-(m.get('slippage_cost') or 0), True)} / {fmt_money(m.get('funding_paid'), True)} / "
                                                                    f"<b>{fmt_money(m.get('net_profit'), True)}</b>"),
                        ("trades · per month", f"{m.get('trades')} · {fmt_num(h0.get('trades_per_month'), 1)}"),
                        ("win rate · expectancy · PF", f"{fmt_pct(m.get('win_rate'), 1)} · {fmt_num(m.get('expectancy_r'), 3)} R · {_pf(m.get('profit_factor'))}"),
                        ("average winner / loser", f"{fmt_money(m.get('average_win'))} / {fmt_money(m.get('average_loss'))}"),
                        ("max drawdown · duration · longest losing streak", f"{fmt_pct(m.get('max_drawdown_pct'), 1)} · "
                                                                            f"{fmt_num((m.get('drawdown_duration_ms') or 0) / 86_400_000, 0)} days · {m.get('longest_loss_streak')}"),
                        ("effective leverage avg / max · liquidations", f"{fmt_num(m.get('avg_effective_leverage'), 2)}x / {fmt_num(m.get('max_effective_leverage'), 2)}x · {m.get('liquidation_count')}"),
                        ("cost-to-edge", fmt_num((h0.get('cost_efficiency') or {}).get('cost_to_edge'), 2)),
                        ("quarters: active / profitable / losing", f"{w.get('active')} / {w.get('profitable')} / {w.get('losing')} of {w.get('total')} · "
                                                                   f"median {fmt_pct(w.get('median_return'), 1)} · worst {_e((w.get('worst') or {}).get('window'))} "
                                                                   f"{fmt_pct((w.get('worst') or {}).get('return'), 1)} · best {_e((w.get('best') or {}).get('window'))} "
                                                                   f"{fmt_pct((w.get('best') or {}).get('return'), 1)}")]))
        yrows = "".join(f"<tr><td>{_e(y['year'])}{' (HALTED all year)' if y.get('halted') else ' (halted)' if y.get('halted_during') else ''}</td>"
                        f"<td class=num>{y['trades']}</td><td class=num>{fmt_pct(y.get('return'), 1)}</td>"
                        f"<td class=num>{fmt_money(y.get('net'), True)}</td><td class=num>{fmt_num(y.get('expectancy_r'), 3)}</td>"
                        f"<td class=num>{_pf(y.get('profit_factor'))}</td><td class=num>{fmt_pct(y.get('max_dd'), 1)}</td></tr>"
                        for y in h0.get("years") or [])
        out.append("<table><thead><tr><th>year</th><th>trades</th><th>return</th><th>net</th><th>exp R</th><th>PF</th><th>max DD</th></tr></thead>"
                   f"<tbody>{yrows}</tbody></table>")
        for dim, title in (("trend", "trend regime"), ("vol", "volatility regime")):
            rg = (h0.get("regimes") or {}).get(dim) or {}
            rrows = "".join(f"<tr><td>{_e(r['regime'])}</td><td class=num>{fmt_pct(r.get('time_share'), 0)}</td><td class=num>{r['trades']}</td>"
                            f"<td class=num>{fmt_money(r.get('net'), True)}</td><td class=num>{fmt_num(r.get('expectancy_r'), 3)}</td>"
                            f"<td class=num>{_pf(r.get('profit_factor'))}</td></tr>" for r in rg.get("rows") or [])
            flag = f"<p class=down>FLAG: all net profit comes from {_e(rg.get('narrow_regime'))}</p>" if rg.get("narrow") else ""
            out.append(f"<table><thead><tr><th>{title}</th><th>time</th><th>trades</th><th>net</th><th>exp R</th><th>PF</th></tr></thead>"
                       f"<tbody>{rrows}</tbody></table>{flag}")
        out.append(_dl([("largest winner / top-3 share of net", f"{fmt_pct(rb.get('largest_winner_share_of_net'), 1)} / {fmt_pct(rb.get('top3_share_of_net'), 1)}"),
                        ("net without best trade / best 3 / best year", f"{fmt_money(rb.get('net_without_best'), True)} / {fmt_money(rb.get('net_without_best3'), True)} / "
                                                                        f"{fmt_money(rb.get('net_without_best_year'), True)} ({_e(rb.get('best_year'))})"),
                        ("break-even round-trip cost", f"{fmt_num(hd.get('breakeven_cost_bps'), 1)} bps"),
                        ("modelled cost: Binance / Bybit", f"{fmt_num(hd.get('binance_cost_bps'), 1)} / {fmt_num(hd.get('bybit_cost_bps'), 1)} bps "
                                                            f"(Bybit extra tick {fmt_num(hd.get('bybit_extra_tick_bps'), 2)} bps)"),
                        ("cost headroom: Binance / Bybit", f"{fmt_num(hd.get('binance_headroom_bps'), 1)} / {fmt_num(hd.get('bybit_headroom_bps'), 1)} bps"),
                        ("Monte Carlo (10,000 paths)", f"median end {fmt_money(mc.get('median_ending_equity'))} · p5 end {fmt_money(mc.get('p5_ending_equity'))} · "
                                                       f"DD median {fmt_pct(mc.get('median_max_dd'), 1)} p95 {fmt_pct(mc.get('p95_max_dd'), 1)} p99 {fmt_pct(mc.get('p99_max_dd'), 1)} · "
                                                       f"P(DD>20/30/50%) {fmt_pct(mc.get('p_dd_over_20'), 1)} / {fmt_pct(mc.get('p_dd_over_30'), 1)} / {fmt_pct(mc.get('p_dd_over_50'), 1)} · "
                                                       f"longest losing streak median {mc.get('median_longest_losing_streak')} p95 {mc.get('p95_longest_losing_streak')} · "
                                                       f"<b>P(ruin) {fmt_pct(mc.get('ruin_probability'), 1)}</b>"),
                        ("ruin definition", mc.get("ruin_definition"))]))
        srows = "".join(f"<tr><td>{_e(x['label'])}{'' if x.get('gated') else ' <span class=muted>(not gated)</span>'}</td>"
                        f"<td class=num>{x.get('trades')}</td><td class=num>{fmt_money(x.get('net'), True)}</td>"
                        f"<td class=num>{fmt_num(x.get('expectancy_r'), 3)}</td><td class=num>{_pf(x.get('profit_factor'))}</td>"
                        f"<td class=num>{fmt_pct(x.get('max_dd'), 1)}</td><td>{'survives' if x.get('survives') else '<b class=down>fails</b>'}</td></tr>"
                        for x in h0.get("stress") or [])
        out.append("<table><thead><tr><th>stress</th><th>trades</th><th>net</th><th>exp R</th><th>PF</th><th>max DD</th><th></th></tr></thead>"
                   f"<tbody>{srows}</tbody></table>")
        grows = "".join(f"<li class={'up' if g['ok'] else 'down'}><b>{'PASS' if g['ok'] else 'FAIL'}</b> {_e(g['name'])}: {_e(g['actual'])} ({_e(g['threshold'])})</li>"
                        for g in h0.get("gates") or [])
        out.append(f"<ul>{grows}</ul>")
    f = c.get("forward") or {}
    out.append("<h4>LIVE FORWARD SHADOW — BINANCE USD-M (never added to the historical ledger)</h4>")
    out.append(_dl([("forward experiment", f"{f.get('experiment_id') or '—'} · started {fmt_ts(f.get('started_ts'))} · "
                                           f"{fmt_num((f.get('elapsed_ms') or 0) / 3_600_000, 1)} h · {f.get('sessions') or 0} session(s)"),
                    ("status", f"{f.get('status')} · mode {f.get('mode') or '—'}"),
                    ("closed forward trades · net", f"{f.get('trades') or 0} · {fmt_money(f.get('net'), True)}"),
                    ("continuous book equity / net", f"{fmt_money(f.get('session_equity'))} / {fmt_money(f.get('session_net'), True)}")]))
    return "".join(out)


def _candidates_text(d: dict[str, Any]) -> list[str]:
    run = d.get("run") or {}
    L = ["", "FROZEN V2 CANDIDATES - MULTI-YEAR VALIDATION (historical and forward never summed)", "-" * 72,
         f"run {run.get('run_id') or 'NOT RUN'} ({run.get('status') or '-'})  window {run.get('first_month')}..{run.get('last_month')}",
         "venues: historical BINANCE USD-M | forward BINANCE USD-M live shadow | target live BYBIT LINEAR (execution validation required)"]
    for c in d.get("candidates") or []:
        h0 = c.get("historical") or {}
        m = h0.get("metrics") or {}
        L.append(f"  {c['key']:<26} MULTI-YEAR {h0.get('verdict') or 'PENDING':<5} "
                 f"equity {fmt_money(m.get('starting_equity'))} -> {fmt_money(m.get('ending_equity'))}  net {fmt_money(m.get('net_profit'), True)}  "
                 f"trades {m.get('trades') if m.get('trades') is not None else '-'}  expR {fmt_num(m.get('expectancy_r'), 3)}  PF {_pf(m.get('profit_factor'))}  "
                 f"maxDD {fmt_pct(m.get('max_drawdown_pct'), 1)}  P(ruin) {fmt_pct((h0.get('monte_carlo') or {}).get('ruin_probability'), 1)}")
        for y in h0.get("years") or []:
            L.append(f"      {y['year']}: trades {y['trades']:>4}  return {fmt_pct(y.get('return'), 1):>8}  expR {fmt_num(y.get('expectancy_r'), 3):>7}  "
                     f"PF {_pf(y.get('profit_factor')):>5}  DD {fmt_pct(y.get('max_dd'), 1)}"
                     f"{'  HALTED all year' if y.get('halted') else '  (halted this year)' if y.get('halted_during') else ''}")
        for g in h0.get("gates") or []:
            L.append(f"      [{'PASS' if g['ok'] else 'FAIL'}] {g['name']}: {g['actual']} ({g['threshold']})")
        f = c.get("forward") or {}
        L.append(f"      FORWARD (separate): experiment {f.get('experiment_id')}  sessions {f.get('sessions')}  "
                 f"book {f.get('book') or '-'}  closed trades {f.get('trades') or 0}  net {fmt_money(f.get('net'), True)}")
    return L
