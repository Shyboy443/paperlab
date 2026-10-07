"""V3.1 AGGRESSIVE EDGE sections of /public/competition/report (HTML) and report.txt.

Server-rendered from the same allow-listed payload as the API (app/core/v31_view.py): the answers and the
ADVANCED SET, small-timeframe viability, the leaderboard, CONTROL vs +JEV3 against every baseline, Jev V3
selection alpha and ATTACK effectiveness, the expected-net-edge calibration, participation funnels, the
holding-period and exit diagnostics, maker vs taker and the 20 / 50 / 100 USDT capacity diagnostic --
for the DEVELOPMENT run and, once it exists, the pre-registered TEST run.
"""
from __future__ import annotations

from typing import Any

from app.core.report_v3 import _dl, _e, _money, _num, _pct, _pf

TFS = ("3m", "5m", "15m", "30m")


def v31_data(storage: Any) -> dict[str, Any]:
    try:
        from app.core.v31_view import v31_payload
        return v31_payload(storage)
    except Exception as exc:                  # the report must render even without V3.1 tables
        return {"dev": None, "test": None, "error": type(exc).__name__}


def _key(k: Any) -> str:
    return str(k or "").replace("@20x", "")


def _table(head: list[str], rows: list[str]) -> str:
    return ("<table><thead><tr>" + "".join(f"<th>{_e(x)}</th>" for x in head) + "</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _mix(m: dict[str, Any] | None) -> str:
    if not m:
        return "—"
    return " ".join(f"{k[0] if k != 'STRONG_ATTACK' else 'S'}{_pct(v, 0)}" for k, v in m.items())


def _viability(v: dict[str, Any], test: bool = False) -> str:
    rows = []
    for tf in TFS:
        c = v.get(tf)
        if not c:
            continue
        raw = c.get("raw") or {}
        rawcells = ("<td class=num colspan=5>n/a on TEST (RAW is never observed: no TEST outcome becomes evidence)</td>" if test else
                    f"<td class=num>{raw.get('candidates')}</td><td class=num>{raw.get('sequenced')}</td>"
                    f"<td class=num>{_num(raw.get('gross_r'), 3)}</td><td class=num>{_num(raw.get('cost_r'), 3)}</td>"
                    f"<td class=num>{_num(raw.get('net_r'), 3)}</td>")
        rows.append(f"<tr><td>{tf}{' (bench)' if tf == '30m' else ''}</td>{rawcells}"
                    f"<td class=num>{c.get('signals')}</td><td class=num>{c.get('edge_passed')}</td><td class=num>{c.get('trades')}</td>"
                    f"<td class=num>{_num(c.get('trades_per_bot_day'))}</td><td class=num>{_num(c.get('gross_expectancy_r'), 3)}</td>"
                    f"<td class=num>{_num(c.get('cost_r'), 3)} ({_num(c.get('cost_bps'), 1)} bps)</td>"
                    f"<td class=num>{_num(c.get('net_expectancy_r'), 3)}</td><td class=num>{_pf(c.get('pf'))}</td>"
                    f"<td class=num>{_pct(c.get('max_dd_mean'))}</td><td class=num>{c.get('profitable_bots')}/{c.get('bots')} "
                    f"({_pct(c.get('profitable_pct'), 0)})</td></tr>")
    return _table(["tf", "raw candidates", "sequenced", "raw gross R", "raw cost R", "raw net R", "gated signals",
                   "edge passed", "trades", "trades/bot-day", "gross ExpR", "cost", "net ExpR", "PF", "mean DD",
                   "profitable bots"], rows)


def _leaderboard(rows: list[dict[str, Any]], labels: dict[str, str], limit: int = 200) -> str:
    out = []
    for r in rows[:limit]:
        out.append(f"<tr><td class=num>{r.get('rank')}</td><td class=mono>{_e(_key(r.get('key')))}</td><td>{_e(r.get('coin'))}</td>"
                   f"<td>{_e(r.get('tf'))}</td><td>{_e(r.get('mode'))}</td><td class=num>{_money(r.get('equity'), False)}</td>"
                   f"<td class=num>{_pct(r.get('net_return_pct'), 1, True)}</td><td class=num>{_num(r.get('trades_per_day'))}</td>"
                   f"<td class=num>{_num(r.get('exp_r'), 3)}</td><td class=num>{_pf(r.get('pf'))}</td><td class=num>{_pct(r.get('max_dd'))}</td>"
                   f"<td>{_e(_mix(r.get('jev_actions')))}</td><td class=num>{_money(r.get('selection_alpha'))}</td>"
                   f"<td>{_e(r.get('state'))} · {_e(labels.get(r.get('failure_mode'), r.get('failure_mode')))}</td></tr>")
    return _table(["rank", "bot", "coin", "tf", "mode", "equity", "return", "trades/day", "ExpR", "PF", "DD",
                   "Jev action", "selection alpha", "status"], out)


def _pairs(pairs: list[dict[str, Any]]) -> str:
    rows = []
    for p in pairs:
        r = p.get("random") or {}
        aj = p.get("attack_jev") or {}
        auc = p.get("jev_auc") or {}
        rows.append(f"<tr><td class=mono>{_e(p.get('strategy_id'))}-{_e(p.get('coin'))}-{_e(p.get('tf'))}</td>"
                    f"<td class=num>{_money((p.get('control') or {}).get('net'))}</td><td class=num>{_money((p.get('jev') or {}).get('net'))}</td>"
                    f"<td class=num>{_money((p.get('always_take') or {}).get('net'))}</td><td class=num>0.00</td>"
                    f"<td class=num>{_money(r.get('random_median'))} / {_money(r.get('random_p90'))}</td>"
                    f"<td class=num>{_money(r.get('alpha_usdt'))}</td><td class=num>{_num(r.get('p_value'))}</td>"
                    f"<td>{_e(_mix({k: v / max(1, sum((p.get('jev_actions') or {}).values())) for k, v in (p.get('jev_actions') or {}).items()}))}</td>"
                    f"<td class=num>{aj.get('attack_trades', 0)} · {_money(aj.get('attack_added_net'))}</td>"
                    f"<td class=num>{_num(auc.get('auc'), 3)} [{_num(auc.get('low'))}, {_num(auc.get('high'))}]</td>"
                    f"<td>{_e(p.get('state'))}</td></tr>")
    return _table(["pair", "CONTROL", "+JEV3", "ALWAYS TAKE", "ALWAYS SKIP", "random p50 / p90", "selection alpha",
                   "rand p", "Jev actions", "ATTACK n · added", "AUC P(support)", "state"], rows)


def _calibration(c: dict[str, Any] | None) -> str:
    c = c or {}
    rows = [f"<tr><td>{_e(b.get('bucket'))}</td><td class=num>{b.get('n')}</td><td class=num>{_num(b.get('predicted'), 3)}</td>"
            f"<td class=num>{_num(b.get('realized'), 3)}</td><td class=num>{_pct(b.get('win_rate'), 0)}</td></tr>"
            for b in c.get("buckets") or []]
    return (_dl([("candidates with an outcome", c.get("n")), ("slope realized-on-predicted", _num(c.get("slope"), 3)),
                 ("Pearson / Spearman", f"{_num(c.get('pearson'), 3)} / {_num(c.get('spearman'), 3)}"),
                 ("passed gate: realized mean R (n)", f"{_num(c.get('passed_realized'), 3)} ({c.get('passed_n')})"),
                 ("refused: realized mean R (n)", f"{_num(c.get('refused_realized'), 3)} ({c.get('refused_n')})")])
            + _table(["predicted net R", "n", "mean predicted", "mean realized", "win rate"], rows))


def _maker(m: dict[str, Any] | None) -> str:
    rows = []
    for g, block in (m or {}).items():
        for w, per in (block or {}).items():
            t, c, o = per.get("taker") or {}, per.get("conservative") or {}, per.get("optimistic") or {}
            rows.append(f"<tr><td>{_e(g)}</td><td>{_e(w)}</td><td class=num>{_money(t.get('net'))} ({t.get('trades')})</td>"
                        f"<td class=num>{_money(c.get('net'))} ({c.get('trades')}; missed {c.get('missed')}, "
                        f"{c.get('missed_winners')} winners)</td><td class=num>{_money(o.get('net'))} ({o.get('trades')}; missed {o.get('missed')})</td>"
                        f"<td>{_e(per.get('verdict'))}</td></tr>")
    return _table(["group", "wait", "taker only", "conservative maker", "optimistic maker", "verdict"], rows)


def _block_html(title: str, b: dict[str, Any]) -> str:
    run, cfg, s = b.get("run") or {}, b.get("config") or {}, b.get("summary") or {}
    out = [f"<h3>{_e(title)}</h3>", _dl([
        ("run", f"{run.get('run_id')} · {run.get('status')} · stage {run.get('stage')} · config {run.get('config_fingerprint')}"),
        ("window", f"{cfg.get('trade_from')} → {cfg.get('trade_to')} ({cfg.get('dataset_role')})"),
        ("edge-model evidence", f"{(b.get('evidence') or {}).get('observations')} sequenced RAW observations · fingerprint "
                                f"{(b.get('evidence') or {}).get('fingerprint')}"),
        ("fingerprints", " · ".join(f"{k} {v}" for k, v in (cfg.get("strategy_fingerprints") or {}).items())
         + " · " + " · ".join(f"{k} {v}" for k, v in (cfg.get("jev_fingerprints") or {}).items())),
        ("dataset", cfg.get("dataset_fingerprint"))])]
    if not s:
        out.append(f"<p class=muted>In progress: {(b.get('progress') or {}).get('bots_done', 0)} bots finished.</p>")
        return "".join(out)
    a = s.get("answers") or {}
    labels = (s.get("failure_modes") or {}).get("labels") or {}
    out.append(_dl([("aggressive edge?", a.get("aggressive_edge")), ("Jev selection", a.get("jev_selection")),
                    ("small timeframes", a.get("small_timeframes")), ("ADVANCED SET", a.get("advanced_set"))]))
    out.append("<h4>Small-timeframe viability</h4>" + _viability(s.get("viability") or {}, cfg.get("dataset_role") == "TEST"))
    out.append("<h4>Leaderboard — Jev field, matched controls, always-take</h4>" + _leaderboard(s.get("leaderboard_field") or [], labels))
    b2 = s.get("baselines") or {}
    out.append("<h4>CONTROL vs +JEV3 against every baseline</h4>" + _pairs(s.get("pairs") or []) + _dl([
        ("totals (Jev / control / always-take / always-skip / random median)",
         f"{_money(b2.get('jev_total'))} / {_money(b2.get('control_total'))} / {_money(b2.get('always_take_total'))} / 0.00 / "
         f"{_money(b2.get('random_median_total'))}"),
        ("Jev beats control (meaningfully) / always-skip / always-take / random p90",
         f"{b2.get('jev_beats_control')} ({b2.get('jev_beats_control_meaningfully')}) / {b2.get('jev_beats_skip')} / "
         f"{b2.get('jev_beats_take')} / {b2.get('jev_beats_random_p90')} of {b2.get('pairs')}")]))
    sa = s.get("selection_alpha") or {}
    j = s.get("jev_pooled") or {}
    if j.get("decisions"):
        fin, auc, auc2, lat = j.get("final") or {}, j.get("auc_support") or {}, j.get("auc_not_skip") or {}, j.get("latency_ms") or {}
        by = j.get("by_action") or {}
        out.append("<h4>Jev V3 — decisions, selection alpha, confidence</h4>" + _dl([
            ("decisions", j.get("decisions")),
            ("SKIP / TAKE / ATTACK / STRONG_ATTACK", " / ".join(str(fin.get(k, 0)) for k in ("SKIP", "TAKE", "ATTACK", "STRONG_ATTACK"))),
            ("JEV SELECTION ALPHA (Jev − matched random action)", f"total {_money(sa.get('total_usdt'))} USDT · mean "
                                                                  f"{_money(sa.get('mean_usdt'))} · positive in {sa.get('positive_pairs')} of {sa.get('pairs')} pairs"),
            ("AUC P(support) → win", f"{_num(auc.get('auc'), 3)} [{_num(auc.get('low'), 3)}, {_num(auc.get('high'), 3)}]"),
            ("AUC 1 − P(SKIP) → win", f"{_num(auc2.get('auc'), 3)} [{_num(auc2.get('low'), 3)}, {_num(auc2.get('high'), 3)}]"),
            ("realized mean R by action (SKIP = shadow)", " · ".join(f"{k} {_num((by.get(k) or {}).get('mean_r'), 3)} (n {(by.get(k) or {}).get('n', 0)})"
                                                                     for k in ("SKIP", "TAKE", "ATTACK", "STRONG_ATTACK"))),
            ("ATTACK downgrades", ", ".join(f"{k} {v}" for k, v in (j.get("downgrades") or {}).items()) or "none"),
            ("MIN_NOTIONAL_AFTER_JEV", j.get("min_notional_after_jev")),
            ("latency p50 / p95 / p99", f"{lat.get('p50')} / {lat.get('p95')} / {lat.get('p99')} ms"),
            ("API errors · cost", f"{j.get('errors', 0)} ({_pct(j.get('error_rate'), 2)}) · ${(j.get('cost_usd') or 0):.3f}")]))
        sb = "".join(f"<tr><td>{_e(r.get('bucket'))}</td><td class=num>{r.get('n')}</td><td class=num>{_num(r.get('mean_r'), 3)}</td>"
                     f"<td class=num>{_pct(r.get('win_rate'), 0)}</td></tr>" for r in j.get("support_buckets") or [])
        out.append(_table(["P(support) band", "candidates", "realized mean R", "win rate"], [sb]))
    att = s.get("attack") or {}
    rows = []
    for name, x in (("+JEV3 ATTACK", att.get("jev")), ("CONTROL deterministic ATTACK (field)", att.get("controls_field")),
                    ("CONTROL deterministic ATTACK (all)", att.get("controls_all"))):
        x = x or {}
        rows.append(f"<tr><td>{_e(name)}</td><td class=num>{x.get('attack_trades', 0)} ({x.get('strong_attack_trades', 0)} strong)</td>"
                    f"<td class=num>{_num(x.get('attack_expectancy_r'), 3)} vs TAKE {_num(x.get('take_expectancy_r'), 3)}</td>"
                    f"<td class=num>{_money(x.get('attack_net'))}</td><td class=num>{_money(x.get('counterfactual_normal_net'))}</td>"
                    f"<td class=num>{_money(x.get('attack_added_net'))}</td></tr>")
    out.append("<h4>ATTACK effectiveness</h4>" + _table(["", "ATTACK trades", "ExpR (vs TAKE trades)", "net", "at TAKE size", "added by ATTACK"], rows))
    out.append("<h4>Expected-net-edge calibration (CONTROL candidates: traded, or refused and shadowed)</h4>" + _calibration(s.get("calibration")))
    cb = s.get("calibration_by_tf") or {}
    out.append(_dl([(tf, f"n {(cb.get(tf) or {}).get('n')} · slope {_num((cb.get(tf) or {}).get('slope'), 3)} · Spearman "
                         f"{_num((cb.get(tf) or {}).get('spearman'), 3)} · passed {_num((cb.get(tf) or {}).get('passed_realized'), 3)}R vs refused "
                         f"{_num((cb.get(tf) or {}).get('refused_realized'), 3)}R") for tf in TFS if cb.get(tf)]))
    fu = s.get("funnels") or {}
    rows = []
    for who in ("controls", "jev"):
        for tf in TFS:
            f = (fu.get(who) or {}).get(tf) or {}
            if not f.get("raw_setups"):
                continue
            rows.append(f"<tr><td>{'CONTROL' if who == 'controls' else '+JEV3'}</td><td>{tf}</td><td class=num>{f.get('raw_setups')}</td>"
                        f"<td class=num>{f.get('legal')}</td><td class=num>{f.get('positive_edge')}</td>"
                        f"<td class=num>{f.get('jev_accepted') if who == 'jev' else '—'}</td><td class=num>{f.get('executed')}</td></tr>")
    out.append("<h4>Participation funnel</h4>" + _table(["bots", "tf", "raw setups", "legal", "positive edge", "Jev TAKE/ATTACK", "executed"], rows))
    ho = s.get("holding") or {}
    rows = []
    for tf in TFS:
        x = ho.get(tf)
        if not x:
            continue
        cells = "".join(f"<td class=num>{(x['buckets'].get(bk) or {}).get('n', 0)} · {_money((x['buckets'].get(bk) or {}).get('gross'))} · "
                        f"{_num((x['buckets'].get(bk) or {}).get('mean_r'), 2)}R</td>" for bk in ("<5m", "5-15m", "15-60m", "1-4h", ">4h"))
        rows.append(f"<tr><td>{tf}</td><td class=num>{x.get('trades')}</td><td class=num>{_num(x.get('median_hold_min'), 0)}</td>{cells}</tr>")
    out.append("<h4>Holding period (CONTROL trades: n · gross · mean R)</h4>" + _table(
        ["tf", "trades", "median hold (min)", "<5m", "5-15m", "15-60m", "1-4h", ">4h"], rows))
    ed = s.get("exit_diagnostics") or {}
    rows = []
    for sid, tfs in ed.items():
        for tf in TFS:
            x = (tfs or {}).get(tf)
            if not x:
                continue
            d = x.get("drift_bps") or {}
            rows.append(f"<tr><td>{_e(sid)}</td><td>{tf}</td><td class=num>{x.get('n')}</td><td>{_e(x.get('verdict'))}</td>"
                        f"<td class=num>{_num(x.get('median_hold_min_median'), 0)}</td><td class=num>{_num(x.get('gross_bps'), 1)} / {_num(x.get('cost_bps'), 1)}</td>"
                        f"<td class=num>{' / '.join(_num(d.get(k), 1) for k in ('15m', '60m', '240m', '720m'))}</td>"
                        f"<td class=num>{_pct(x.get('gave_back_share'), 0)}</td></tr>")
    out.append("<h4>Entry / exit diagnostics (family × timeframe)</h4>" + _table(
        ["family", "tf", "trades", "verdict", "median hold", "gross / cost bps", "drift after entry 15m/1h/4h/12h bps", "gave back +1R"], rows))
    out.append("<h4>Maker-first vs taker (post hoc on the 1m tape; diagnostic only)</h4>" + _maker(s.get("maker")))
    cap = s.get("capacity") or {}
    tot = cap.get("totals") or {}
    out.append("<h4>Capacity diagnostic (never qualifies the 20 USDT competition)</h4>" + _table(
        ["book", "net", "return", "trades", "below exchange minimum"],
        [f"<tr><td>{_e(k)} USDT</td><td class=num>{_money(v.get('net'))}</td><td class=num>{_pct(v.get('return_pct'), 1, True)}</td>"
         f"<td class=num>{v.get('trades')}</td><td class=num>{v.get('below_min')}</td></tr>" for k, v in tot.items()]))
    fm = s.get("failure_modes") or {}
    out.append("<h4>Diagnoses (root cause first)</h4>" + _dl([
        ("+JEV3 bots", ", ".join(f"{labels.get(k, k)} {n}" for k, n in (fm.get("jev_bots") or {}).items()) or "—"),
        ("all CONTROL bots", ", ".join(f"{labels.get(k, k)} {n}" for k, n in (fm.get("controls") or {}).items()) or "—"),
        ("LOW_ACTIVITY_EDGE", ", ".join(_key(k) for k in s.get("low_activity_edge") or []) or "none"),
        ("passed every gate here", ", ".join(_key(k) for k in s.get("passed_all") or []) or "none")]))
    f = b.get("field") or {}
    out.append(f"<p class=muted>Field: {f.get('n', len(f.get('pairs') or []))} pairs · {_e(f.get('rule'))}</p>")
    if f.get("amendment"):
        am, pre = f.get("amendment") or {}, f.get("preregistered") or {}
        out.append(f"<p><b>FIELD AMENDMENT {am.get('id')}:</b> the pre-registered rule qualified {pre.get('pairs')} pairs "
                   f"({_e(pre.get('status'))}); {_e(am.get('rule'))} → {am.get('added')} EXTENDED pairs "
                   f"({_e(am.get('basis'))}). Every gate still applies to them.</p>")
    out.append("<details><summary>All CONTROL bots</summary>" + _leaderboard(s.get("leaderboard_controls") or [], labels, 400) + "</details>")
    return "".join(out)


def v31_html(d: dict[str, Any]) -> str:
    out = ["<h2 id=v31>V3.1 — AGGRESSIVE EDGE</h2>",
           "<p>V3 (run v3-e39e94890b) is frozen with ADVANCED SET = NONE (docs/V3_RESULTS_FREEZE.md). V3.1 is a new program: "
           "continuation families S33.1 / S35.1 / S37 with long-hold exits, an expected-net-edge gate fitted on DEVELOPMENT "
           "candidates only, Jev V3 (SKIP / TAKE / ATTACK, no DEFENSIVE), AGGRESSIVE_V31 sizing, and a pre-registered TEST "
           "(2026-05 → 2026-08) judged on data the design never saw.</p>"]
    dev, test = d.get("dev"), d.get("test")
    if not dev and not test:
        out.append("<p class=muted>No V3.1 run yet.</p>")
        return "".join(out)
    if test:
        out.append(_block_html("TEST (pre-registered holdout)", test))
    if dev:
        out.append(_block_html("DEVELOPMENT", dev))
    return "".join(out)


def _block_text(title: str, b: dict[str, Any]) -> list[str]:
    run, cfg, s = b.get("run") or {}, b.get("config") or {}, b.get("summary") or {}
    L = [f"  {title}: run {run.get('run_id')} {run.get('status')} stage {run.get('stage')} | {cfg.get('trade_from')} -> "
         f"{cfg.get('trade_to')} | config {run.get('config_fingerprint')} | evidence {(b.get('evidence') or {}).get('fingerprint')}"]
    if not s:
        L.append(f"    in progress: {(b.get('progress') or {}).get('bots_done', 0)} bots finished")
        return L
    a = s.get("answers") or {}
    L.append(f"    AGGRESSIVE EDGE: {a.get('aggressive_edge')}")
    L.append(f"    JEV SELECTION: {a.get('jev_selection')}")
    L.append(f"    ADVANCED SET: {a.get('advanced_set')}")
    am = (b.get("field") or {}).get("amendment")
    if am:
        L.append(f"    FIELD: pre-registered rule -> {((b.get('field') or {}).get('preregistered') or {}).get('pairs')} pairs; "
                 f"amendment {am.get('id')} (activity only, floor {am.get('floor')}) added {am.get('added')} EXTENDED pairs")
    L.append("    SMALL-TIMEFRAME VIABILITY (raw net R | gated trades, gross/cost/net ExpR, PF, profitable bots)")
    for tf, c in (s.get("viability") or {}).items():
        rawtxt = "n/a" if cfg.get("dataset_role") == "TEST" else _num((c.get("raw") or {}).get("net_r"), 3) + "R"
        L.append(f"      {tf:<4} raw {rawtxt:>8} | trades {c.get('trades'):>5} "
                 f"gross {_num(c.get('gross_expectancy_r'), 3):>7} cost {_num(c.get('cost_r'), 3):>6} net {_num(c.get('net_expectancy_r'), 3):>7} "
                 f"PF {_pf(c.get('pf')):>5} profitable {c.get('profitable_bots')}/{c.get('bots')}")
    L.append("    CONTROL vs +JEV3: control / jev / take / skip / random median / alpha / state")
    for p in s.get("pairs") or []:
        r = p.get("random") or {}
        L.append(f"      {p.get('strategy_id')}-{p.get('coin')}-{str(p.get('tf')):<4} {_money((p.get('control') or {}).get('net')):>7} "
                 f"{_money((p.get('jev') or {}).get('net')):>7} {_money((p.get('always_take') or {}).get('net')):>7} 0.00 "
                 f"{_money(r.get('random_median')):>7} {_money(r.get('alpha_usdt')):>7} {p.get('state')}")
    sa = s.get("selection_alpha") or {}
    att = (s.get("attack") or {}).get("jev") or {}
    L.append(f"    SELECTION ALPHA total {_money(sa.get('total_usdt'))} ({sa.get('positive_pairs')}/{sa.get('pairs')} pairs positive) | "
             f"ATTACK {att.get('attack_trades', 0)} trades, ExpR {_num(att.get('attack_expectancy_r'), 3)}, added {_money(att.get('attack_added_net'))}")
    c = s.get("calibration") or {}
    L.append(f"    EDGE CALIBRATION n {c.get('n')} slope {_num(c.get('slope'), 3)} Spearman {_num(c.get('spearman'), 3)} | passed "
             f"{_num(c.get('passed_realized'), 3)}R vs refused {_num(c.get('refused_realized'), 3)}R")
    mk = ((s.get("maker") or {}).get("ALL") or {})
    for w, per in mk.items():
        L.append(f"    MAKER {w}: taker {_money((per.get('taker') or {}).get('net'))} | conservative {_money((per.get('conservative') or {}).get('net'))} | "
                 f"optimistic {_money((per.get('optimistic') or {}).get('net'))} -> {per.get('verdict')}")
    tot = (s.get("capacity") or {}).get("totals") or {}
    L.append("    CAPACITY " + " | ".join(f"{k} USDT {_money(v.get('net'))} ({_pct(v.get('return_pct'), 1, True)})" for k, v in tot.items()))
    return L


def v31_text(d: dict[str, Any]) -> list[str]:
    L = ["", "V3.1 - AGGRESSIVE EDGE", "-" * 72, "  V3 run v3-e39e94890b frozen, ADVANCED SET = NONE (docs/V3_RESULTS_FREEZE.md)"]
    dev, test = d.get("dev"), d.get("test")
    if not dev and not test:
        L.append("  no V3.1 run yet")
        return L
    if test:
        L.extend(_block_text("TEST", test))
    if dev:
        L.extend(_block_text("DEVELOPMENT", dev))
    return L
