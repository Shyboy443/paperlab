"""V3 AGGRESSIVE INTRADAY JEV ARENA sections of /public/competition/report (HTML) and report.txt.

Server-rendered from the same allow-listed payload as the API (app/core/v3_view.py): leaderboards,
failure reasons, CONTROL vs +JEV2 against every baseline, Jev V2 decisions and calibration, the
strategy x timeframe and coin x timeframe matrices, bot diagnostics and the advanced set.
"""
from __future__ import annotations

import html
from typing import Any


def _e(v: Any) -> str:
    return html.escape("—" if v is None else str(v))


def _num(v: Any, d: int = 2) -> str:
    return f"{v:.{d}f}" if isinstance(v, (int, float)) else "—"


def _money(v: Any, signed: bool = True) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _pct(v: Any, d: int = 1, signed: bool = False) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"


def _pf(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return "∞" if v >= 999 else f"{v:.2f}"


def _dl(pairs: list[tuple[str, Any]]) -> str:
    return "<dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in pairs) + "</dl>"


def v3_data(storage: Any) -> dict[str, Any]:
    try:
        from app.core.v3_view import v3_payload
        return v3_payload(storage)
    except Exception as exc:                  # the report must render even without V3 tables
        return {"run": None, "error": type(exc).__name__}


def _key(r: dict[str, Any]) -> str:
    return str(r.get("key", "")).replace("@20x", "")


def _state(r: dict[str, Any], labels: dict[str, str]) -> str:
    return f"{r.get('state')} · {labels.get(r.get('failure_mode'), r.get('failure_mode'))}"


LB_HEAD = ("<table><thead><tr><th>bot</th><th>coin</th><th>tf</th><th>mode</th><th>equity</th><th>return</th>"
           "<th>trades/day</th><th>ExpR</th><th>PF</th><th>DD</th><th>cost bps</th><th>Jev take</th>"
           "<th>status · root cause</th></tr></thead><tbody>")


def _lb_row(r: dict[str, Any], labels: dict[str, str]) -> str:
    jev = r.get("role") == "JEV"
    return (f"<tr><td class=mono>{_e(_key(r))}</td><td>{_e(r.get('coin'))}</td><td>{_e(r.get('tf'))}</td>"
            f"<td>{'JEV2' if jev else _e(r.get('role'))}</td><td class=num>{_money(20 + (r.get('net_pnl') or 0), False)}</td>"
            f"<td class=num>{_pct(r.get('net_return_pct'), 1, True)}</td><td class=num>{_num(r.get('trades_per_day'))}</td>"
            f"<td class=num>{_num(r.get('exp_r'), 3)}</td><td class=num>{_pf(r.get('pf'))}</td>"
            f"<td class=num>{_pct(r.get('max_dd'))}</td><td class=num>{_num(r.get('rt_cost_bps'), 1)}</td>"
            f"<td class=num>{_pct(r.get('jev_take'), 0) if jev else '—'}</td><td>{_e(_state(r, labels))}</td></tr>")


def _matrix(title: str, m: dict[str, Any] | None) -> str:
    tfs = ["1m", "3m", "5m", "15m", "30m"]
    head = "".join(f"<th>{tf}{' (exp.)' if tf == '1m' else ' (bench)' if tf == '30m' else ''}</th>" for tf in tfs)
    rows = []
    for rk, cells in (m or {}).items():
        tds = []
        for tf in tfs:
            c = (cells or {}).get(tf)
            tds.append("<td class=num>—</td>" if not c else
                       f"<td class=num>{_pct(c.get('net_return_mean'), 1, True)}<br><small>{_num(c.get('expectancy_r'))}R · "
                       f"PF {_pf(c.get('pf'))} · {c.get('trades')} tr · DD {_pct(c.get('max_dd_mean'), 0)} · "
                       f"{c.get('profitable')}/{c.get('bots')} profitable</small></td>")
        rows.append(f"<tr><td>{_e(rk)}</td>{''.join(tds)}</tr>")
    return f"<h3>{_e(title)}</h3><table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def v3_html(d: dict[str, Any]) -> str:
    out = ["<h2 id=v3>V3 — AGGRESSIVE INTRADAY JEV ARENA (DEVELOPMENT discovery)</h2>"]
    v2 = d.get("v2") or {}
    if isinstance(v2.get("passed"), int):
        out.append(f"<p><b>V2 MULTI-YEAR {v2['passed']} / {v2.get('candidates')} PASSED</b> (run {_e(v2.get('run_id'))}). "
                   "V3 is a new research program (new strategies, Jev policy V2, AGGRESSIVE_V3); V1 and V2 are unchanged.</p>")
    run = d.get("run")
    if not run:
        out.append("<p class=muted>No V3 discovery run yet.</p>")
        return "".join(out)
    s, cfg = d.get("summary") or {}, d.get("config") or {}
    c = s.get("counts") or {}
    out.append(_dl([
        ("run", f"{run.get('run_id')} · {run.get('status')} · stage {run.get('stage')}"),
        ("venue", d.get("venue_label")),
        ("window", f"{cfg.get('trade_from')} → {cfg.get('trade_to')} · DEVELOPMENT data only; TEST 2026-05 → 2026-08 untouched"),
        ("protocol", f"{cfg.get('protocol')} (docs/V3_PROTOCOL.md) · config {run.get('config_fingerprint')}"),
        ("dataset", cfg.get("dataset_fingerprint")), ("coins", " ".join(cfg.get("coins") or [])),
        ("field", f"{c.get('active_jev_bots', 0)} active Jev bots · {c.get('matched_controls', 0)} matched controls · "
                  + " · ".join(f"{k} {v}" for k, v in (c.get("field_by_tf") or {}).items())
                  + f" · {c.get('controls_scanned', 0)} scanned controls (+{c.get('experimental_1m', 0)} experimental 1m)"),
        ("fingerprints", " · ".join(f"{k} {v}" for k, v in (cfg.get("strategy_fingerprints") or {}).items())
         + " · " + " · ".join(f"{k} {v}" for k, v in (cfg.get("jev_fingerprints") or {}).items()))]))
    if not s:
        out.append(f"<p class=muted>In progress: {(d.get('progress') or {}).get('bots_done', 0)} bots finished.</p>")
        return "".join(out)
    a = s.get("answers") or {}
    adv = s.get("advanced_set")
    f = d.get("field") or {}
    if f.get("preregistered"):
        pre, am = f.get("preregistered") or {}, f.get("amendment") or {}
        out.append(f"<p><b>FIELD:</b> the pre-registered activity rule qualified {pre.get('pairs')} Jev pairs → "
                   f"{_e(pre.get('status'))}. Protocol amendment {am.get('id')} (activity counts only, before any Jev answer) "
                   f"added {am.get('added')} EXTENDED pairs; every gate, including participation, still applies to them.</p>")
    out.append("<h3>Answers</h3>" + _dl([
        ("an aggressive small-timeframe bot with a positive after-cost edge?", a.get("aggressive_edge")),
        ("does Jev make it better?", a.get("jev_makes_it_better")),
        ("ADVANCED SET (to the holdout test)", ", ".join(adv) if isinstance(adv, list) and adv else "NONE")]))
    labels = (s.get("failure_modes") or {}).get("labels") or {}
    out.append("<h3>Leaderboard — Jev field and matched controls</h3>" + LB_HEAD
               + "".join(_lb_row(r, labels) for r in s.get("leaderboard_field") or []) + "</tbody></table>")
    fm = s.get("failure_modes") or {}

    def counts(x: dict[str, int] | None) -> str:
        return ", ".join(f"{labels.get(k, k)} {n}" for k, n in (x or {}).items()) or "—"
    out.append("<h3>Failure reasons (root cause)</h3>" + _dl([
        ("+JEV2 bots", counts(fm.get("jev_bots"))), ("matched controls", counts(fm.get("field_controls"))),
        ("all scanned controls", counts(fm.get("all_controls")))]))
    rows = []
    for p in s.get("pairs") or []:
        r, acc, sk, auc = p.get("random") or {}, p.get("jev_accepted") or {}, p.get("jev_skipped") or {}, p.get("jev_auc") or {}
        rows.append(f"<tr><td class=mono>{_e(p.get('strategy_id'))}-{_e(p.get('coin'))}-{_e(p.get('tf'))}</td>"
                    f"<td class=num>{_money((p.get('control') or {}).get('net'))}</td><td class=num>{_money((p.get('jev') or {}).get('net'))}</td>"
                    f"<td class=num>{_money((p.get('always_take') or {}).get('net'))}</td><td class=num>0.00</td>"
                    f"<td class=num>{_money(r.get('p50'))} / {_money(r.get('p90'))}</td><td class=num>{_num(r.get('p_value'))}</td>"
                    f"<td class=num>{acc.get('winners', 0)}/{acc.get('losers', 0)}</td><td class=num>{sk.get('winners', 0)}/{sk.get('losers', 0)}</td>"
                    f"<td class=num>{(p.get('jev_refused') or {}).get('n', 0)}</td>"
                    f"<td class=num>{_num(auc.get('auc'), 3)} [{_num(auc.get('low'))}, {_num(auc.get('high'))}]</td><td>{_e(p.get('state'))}</td></tr>")
    out.append("<h3>CONTROL vs +JEV2 against ALWAYS-TAKE / ALWAYS-SKIP / RANDOM-FILTER</h3><table><thead><tr><th>pair</th>"
               "<th>control</th><th>+JEV2</th><th>always-take</th><th>always-skip</th><th>random p50 / p90</th><th>rand p</th>"
               "<th>accepted W/L</th><th>Jev skipped W/L</th><th>refused after resize</th><th>AUC [95%]</th><th>state</th></tr></thead><tbody>"
               + "".join(rows) + "</tbody></table>")
    b = s.get("baselines") or {}
    out.append(_dl([("totals (Jev / control / always-take / always-skip / random median)",
                     f"{_money(b.get('jev_total'))} / {_money(b.get('control_total'))} / {_money(b.get('always_take_total'))} / 0.00 / "
                     f"{_money(b.get('random_median_total'))}"),
                    ("Jev beats control / always-skip / always-take / random p90",
                     f"{b.get('jev_beats_control')} / {b.get('jev_beats_skip')} / {b.get('jev_beats_take')} / "
                     f"{b.get('jev_beats_random_p90')} of {b.get('pairs')}")]))
    j = s.get("jev_pooled") or {}
    if j.get("decisions"):
        fin, auc, lat = j.get("final") or {}, j.get("auc") or {}, j.get("latency_ms") or {}
        acc, sk = j.get("accepted") or {}, j.get("skipped") or {}
        out.append("<h3>Jev V2 decisions (all pairs)</h3>" + _dl([
            ("decisions", j.get("decisions")),
            ("SKIP / DEFENSIVE / NORMAL / ATTACK", " / ".join(str(fin.get(k, 0)) for k in ("SKIP", "DEFENSIVE", "NORMAL", "ATTACK"))),
            ("skip rate · ATTACK rate", f"{_pct(j.get('skip_rate'))} · {_pct(j.get('attack_rate'))}"),
            ("AUC of P(win) (95% interval, n)", f"{_num(auc.get('auc'), 3)} [{_num(auc.get('low'), 3)}, {_num(auc.get('high'), 3)}], "
                                                f"n {(auc.get('wins') or 0) + (auc.get('losses') or 0)}"),
            ("accepted", f"{acc.get('winners', 0)} winners / {acc.get('losers', 0)} losers, mean {_num(acc.get('mean_r'), 3)}R"),
            ("Jev SKIP (counterfactual)", f"{sk.get('winners', 0)} winners / {sk.get('losers', 0)} losers, mean {_num(sk.get('mean_r'), 3)}R"),
            ("refused after Jev's resize (half size below the exchange minimum)",
             f"{(j.get('refused_after_resize') or {}).get('n', 0)} candidates, mean {_num((j.get('refused_after_resize') or {}).get('mean_r'), 3)}R; "
             f"not traded for any reason {_pct(j.get('not_traded_rate'))}"),
            ("request latency p50 / p95 / p99", f"{lat.get('p50')} / {lat.get('p95')} / {lat.get('p99')} ms"),
            ("API errors", f"{j.get('errors', 0)} ({_pct(j.get('error_rate'), 2)})"),
            ("API cost", f"${(j.get('cost_usd') or 0):.3f}"),
            ("MODELLED move during Jev latency p50 / p95 / p99",
             f"{_num((j.get('latency_model') or {}).get('move_bps_p50'))} / {_num((j.get('latency_model') or {}).get('move_bps_p95'))} / "
             f"{_num((j.get('latency_model') or {}).get('move_bps_p99'))} bps (1m realized volatility x sqrt(latency/60s); live slippage is "
             f"measured on the forward shadow)")]))
        cal = "".join(f"<tr><td>{_e(r.get('bucket'))}</td><td class=num>{r.get('n')}</td><td class=num>{_pct(r.get('predicted'), 0)}</td>"
                      f"<td class=num>{_pct(r.get('realized_win_rate'), 0)}</td><td class=num>{_num(r.get('mean_r'))}</td></tr>"
                      for r in j.get("calibration") or [])
        out.append("<table><thead><tr><th>P(win) band</th><th>candidates</th><th>predicted</th><th>realized win rate</th>"
                   f"<th>mean R</th></tr></thead><tbody>{cal}</tbody></table>")
    out.append(_matrix("Strategy × timeframe (every scanned control)", s.get("matrix_strategy_tf")))
    out.append(_matrix("Coin × timeframe (every scanned control)", s.get("matrix_coin_tf")))
    tv = s.get("timeframe_verdict") or {}
    out.append("<h3>Which timeframe works?</h3>" + _dl([
        (sid, f"best {v.get('best_tf')} · " + " · ".join(f"{tf}: {_pct(x.get('mean_net_return'), 1, True)} ({x.get('profitable_active')} ok)"
                                                          for tf, x in (v.get("by_tf") or {}).items())) for sid, v in tv.items()]))
    diag = []
    for r in s.get("leaderboard_field") or []:
        jev = r.get("role") == "JEV"
        diag.append(f"<tr><td class=mono>{_e(_key(r))}</td><td>{_e(r.get('state'))}</td>"
                    f"<td>{_e(labels.get(r.get('failure_mode'), r.get('failure_mode')))}</td>"
                    f"<td class=num>{_num(r.get('gross_exp_r'), 3)}R / {_num(r.get('exp_r'), 3)}R</td>"
                    f"<td class=num>{_num(r.get('rt_cost_bps'), 1)}</td><td class=num>{_num(r.get('edge_to_cost'))}</td>"
                    f"<td class=num>{_pct(r.get('jev_take'), 0) if jev else '—'}</td><td class=num>{_num(r.get('jev_auc')) if jev else '—'}</td></tr>")
    out.append("<h3>Bot diagnostics (Bot Analyzer)</h3><table><thead><tr><th>bot</th><th>result</th><th>primary issue</th>"
               "<th>gross / net expectancy</th><th>round trip bps</th><th>edge/cost</th><th>Jev take</th><th>Jev AUC</th>"
               f"</tr></thead><tbody>{''.join(diag)}</tbody></table>")
    out.append("<details><summary>All scanned controls</summary>" + LB_HEAD
               + "".join(_lb_row(r, labels) for r in s.get("leaderboard_scan") or []) + "</tbody></table></details>")
    return "".join(out)


def v3_text(d: dict[str, Any]) -> list[str]:
    L = ["", "V3 - AGGRESSIVE INTRADAY JEV ARENA (DEVELOPMENT discovery)", "-" * 72]
    v2 = d.get("v2") or {}
    if isinstance(v2.get("passed"), int):
        L.append(f"  V2 MULTI-YEAR {v2['passed']} / {v2.get('candidates')} PASSED (run {v2.get('run_id')})")
    run = d.get("run")
    if not run:
        L.append("  no V3 discovery run yet")
        return L
    s, cfg = d.get("summary") or {}, d.get("config") or {}
    L.append(f"  run {run.get('run_id')} {run.get('status')} stage {run.get('stage')} | {d.get('venue_label')}")
    L.append(f"  window {cfg.get('trade_from')} -> {cfg.get('trade_to')} (DEVELOPMENT) | dataset {cfg.get('dataset_fingerprint')} | "
             f"config {run.get('config_fingerprint')}")
    if not s:
        L.append(f"  in progress: {(d.get('progress') or {}).get('bots_done', 0)} bots finished")
        return L
    a, c = s.get("answers") or {}, s.get("counts") or {}
    labels = (s.get("failure_modes") or {}).get("labels") or {}
    L.append(f"  field: {c.get('active_jev_bots')} Jev bots, {c.get('matched_controls')} controls, by tf {c.get('field_by_tf')}; "
             f"{c.get('controls_scanned')} scanned controls (+{c.get('experimental_1m')} experimental 1m)")
    f = d.get("field") or {}
    if f.get("preregistered"):
        L.append(f"  FIELD: pre-registered rule -> {(f.get('preregistered') or {}).get('pairs')} pairs = "
                 f"{(f.get('preregistered') or {}).get('status')}; amendment 1 added {(f.get('amendment') or {}).get('added')} EXTENDED pairs "
                 f"(Jev evidence only, gates unchanged)")
    L.append(f"  AGGRESSIVE EDGE: {a.get('aggressive_edge')}")
    L.append(f"  JEV MAKES IT BETTER: {a.get('jev_makes_it_better')}")
    L.append(f"  ADVANCED SET: {a.get('advanced_set')}")
    fm = s.get("failure_modes") or {}
    L.append("  failure reasons, Jev bots: " + ", ".join(f"{labels.get(k, k)} {n}" for k, n in (fm.get("jev_bots") or {}).items()))
    L.append("  failure reasons, all controls: " + ", ".join(f"{labels.get(k, k)} {n}" for k, n in (fm.get("all_controls") or {}).items()))
    L.append("  LEADERBOARD (field)")
    for r in s.get("leaderboard_field") or []:
        jev = r.get("role") == "JEV"
        L.append(f"    {_key(r):<24} net {_money(r.get('net_pnl')):>7} tr/day {_num(r.get('trades_per_day')):>5} "
                 f"expR {_num(r.get('exp_r'), 3):>7} PF {_pf(r.get('pf')):>5} DD {_pct(r.get('max_dd')):>6} "
                 f"cost {_num(r.get('rt_cost_bps'), 1):>5}bps {('take ' + _pct(r.get('jev_take'), 0)) if jev else '':<9} {_state(r, labels)}")
    L.append("  CONTROL vs +JEV2: control / jev / always-take / always-skip / random p50,p90 / p / AUC")
    for p in s.get("pairs") or []:
        r = p.get("random") or {}
        L.append(f"    {p.get('strategy_id')}-{p.get('coin')}-{str(p.get('tf')):<4} {_money((p.get('control') or {}).get('net')):>7} "
                 f"{_money((p.get('jev') or {}).get('net')):>7} {_money((p.get('always_take') or {}).get('net')):>7} 0.00 "
                 f"{_money(r.get('p50'))},{_money(r.get('p90'))} p={_num(r.get('p_value'))} AUC {_num((p.get('jev_auc') or {}).get('auc'), 3)}")
    j = s.get("jev_pooled") or {}
    if j.get("decisions"):
        fin, auc, lat = j.get("final") or {}, j.get("auc") or {}, j.get("latency_ms") or {}
        L.append(f"  JEV V2: {j.get('decisions')} decisions | SKIP {fin.get('SKIP', 0)} DEFENSIVE {fin.get('DEFENSIVE', 0)} "
                 f"NORMAL {fin.get('NORMAL', 0)} ATTACK {fin.get('ATTACK', 0)} | skip {_pct(j.get('skip_rate'))} | AUC "
                 f"{_num(auc.get('auc'), 3)} [{_num(auc.get('low'), 3)}, {_num(auc.get('high'), 3)}] | latency p50/p95/p99 "
                 f"{lat.get('p50')}/{lat.get('p95')}/{lat.get('p99')} ms | errors {j.get('errors', 0)}")
    for title, m in (("STRATEGY x TIMEFRAME", s.get("matrix_strategy_tf")), ("COIN x TIMEFRAME", s.get("matrix_coin_tf"))):
        L.append(f"  {title} (mean net return per timeframe)")
        for rk, cells in (m or {}).items():
            L.append(f"    {rk:<30} " + "  ".join(
                f"{tf}:{_pct(((cells or {}).get(tf) or {}).get('net_return_mean'), 1, True) if (cells or {}).get(tf) else '—'}"
                for tf in ("1m", "3m", "5m", "15m", "30m")))
    return L
