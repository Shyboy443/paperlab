"""V4 INTRADAY SPECIALISTS sections of /public/competition/report (HTML) and report.txt.

Server-rendered from the same allow-listed payload as the API (app/core/system_view.v4_payload): the answers and
the ADVANCED SET, the raw edge before any gate, the gross -> fees -> slippage -> net decomposition, the exit
analyzer, Jev V4 against its baselines (selection alpha, AUC, ATTACK) and the account-size diagnostic -- for the
DEVELOPMENT run and the pre-registered holdout TEST.
"""
from __future__ import annotations

from typing import Any

from app.core.report_v3 import _e, _money, _num, _pct

TFS = ("3m", "5m", "15m", "30m")


def v4_data(storage: Any) -> dict[str, Any]:
    try:
        from app.core.system_view import v4_payload
        return v4_payload(storage)
    except Exception as exc:                  # the report must render even without V4 tables
        return {"dev": None, "test": None, "error": type(exc).__name__}


def _table(head: list[str], rows: list[list[str]]) -> str:
    return ("<table><thead><tr>" + "".join(f"<th>{_e(x)}</th>" for x in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")


def _r(v: Any) -> str:
    return (("+" if v > 0 else "") + f"{v:.3f}R") if isinstance(v, (int, float)) else "—"


def _block_html(title: str, b: dict[str, Any]) -> str:
    run = b.get("run") or {}
    if not b.get("counts"):
        return f"<h3>{_e(title)}</h3><p class=muted>run {_e(run.get('run_id'))} {_e(run.get('status'))} · stage {_e(run.get('stage'))}</p>"
    a, w = b.get("answers") or {}, b.get("window") or {}
    adv = b.get("advanced_set")
    out = [f"<h3>{_e(title)} · {_e(w.get('from'))} → {_e(w.get('to'))} · run {_e(run.get('run_id'))}</h3>",
           f"<p><b>ADVANCED SET: {_e(', '.join(adv) if isinstance(adv, list) else adv)}</b> · edge: {_e(a.get('intraday_edge'))}<br>"
           f"Jev: {_e(a.get('jev_selection'))}</p>",
           "<p class=muted>universe (tradeability rule, never PnL): " + _e("; ".join(f"{tf} {', '.join(c)}" for tf, c in
                                                                              (b.get("universe") or {}).items())) + "</p>"]
    raw = (b.get("raw_edge") or {})
    if raw.get("by_tf"):
        out.append("<h4>Raw edge before any gate (every legal setup at TAKE size, ungated)</h4>")
        out.append(_table(["group", "setups", "gross R", "t", "cost R", "net R", "diagnosis"],
                          [[_e(r["group"]), str(r["setups"]), _r(r["gross_r"]), _num(r.get("t"), 2), _num(r["cost_r"], 3),
                            _r(r["net_r"]), _e(r["diagnosis"])] for r in raw["by_tf"] + (raw.get("by_family") or [])]))
    eq = (b.get("edge_quality") or {}).get("by_tf") or []
    if eq:
        out.append("<h4>Executed trades: gross → fees → slippage → net (CONTROL bots, 20 USDT)</h4>")
        out.append(_table(["timeframe", "bots", "trades", "gross", "fees", "slippage", "net", "gross bps / turnover", "diagnosis"],
                          [[_e(r["group"]), str(r["bots"]), str(r["trades"]), _money(r["gross"]), _money(r["fees"]),
                            _money(r["slippage"]), _money(r["net"]), _num(r.get("gross_bps_of_turnover"), 1), _e(r["diagnosis"])]
                           for r in sorted(eq, key=lambda r: TFS.index(r["group"]) if r["group"] in TFS else 9)]))
    ex = (b.get("exits") or {}).get("by_tf") or {}
    if ex:
        out.append("<h4>Exit analyzer</h4>")
        out.append(_table(["timeframe", "trades", "MFE R", "MAE R", "stopped → +2R", "random entries", "4h after a win", "verdict"],
                          [[_e(tf), str(r.get("trades")), _num(r.get("mfe_r_mean"), 2), _num(r.get("mae_r_mean"), 2),
                            _pct(r.get("stopped_then_2r_share")), _pct(r.get("random_stopped_then_2r_share")),
                            _r(r.get("winners_post_exit_4h_r_mean")), _e(" · ".join(r.get("flags") or []))] for tf, r in ex.items()]))
    j = b.get("jev_pooled") or {}
    if j.get("decisions"):
        sa, att = b.get("selection_alpha") or {}, (b.get("attack") or {}).get("jev") or {}
        auc = j.get("auc_support") or {}
        out.append("<h4>Jev V4 (CONTRADICT / SUPPORT / STRONGLY SUPPORT → SKIP / TAKE / ATTACK)</h4>")
        out.append(f"<p>{j.get('decisions')} decisions · final {_e(j.get('final'))} · AUC {_num(auc.get('auc'), 3)} "
                   f"[{_num(auc.get('low'), 3)}, {_num(auc.get('high'), 3)}] · selection alpha vs matched random "
                   f"{_money(sa.get('total_usdt'))} USDT ({sa.get('positive_pairs')}/{sa.get('pairs')} pairs positive) · "
                   f"ATTACK {att.get('attack_trades')} trades, win rate {_pct(att.get('attack_win_rate'))}, added "
                   f"{_money(att.get('attack_added_net'))} USDT vs the same trades at normal size</p>")
    cap = b.get("capacity") or {}
    if cap.get("verdicts"):
        out.append(f"<p>Account size (diagnostic only, never qualifies a 20 USDT bot): {_e(cap['verdicts'])}</p>")
    fm = (b.get("failure_modes") or {}).get("controls") or {}
    out.append("<p class=muted>primary failure of each CONTROL: " + _e(" · ".join(f"{k} {v}" for k, v in fm.items())) + "</p>")
    return "".join(out)


def v4_html(d: dict[str, Any]) -> str:
    out = ["<h2 id=v4>V4 — INTRADAY SPECIALISTS</h2>",
           "<p>1h trend → 15m / 30m structure → 3m / 5m / 15m / 30m trigger; seven families with one market hypothesis "
           "each; one exit; a causal family × timeframe expected-edge gate; Jev V4; coins chosen by a tradeability score, "
           "never by PnL. Frozen after DEVELOPMENT (docs/V4_FREEZE.md), holdout 2025-04 → 2025-09 pre-registered before "
           "download (docs/V4_TEST_PREREGISTRATION.json). ADVANCED = every gate in both windows.</p>"]
    dev, test = d.get("dev"), d.get("test")
    if not dev and not test:
        out.append("<p class=muted>No V4 run yet.</p>")
        return "".join(out)
    if test:
        out.append(_block_html("TEST (pre-registered holdout)", test))
    if dev:
        out.append(_block_html("DEVELOPMENT", dev))
    return "".join(out)


def v4_text(d: dict[str, Any]) -> list[str]:
    L = ["", "V4 - INTRADAY SPECIALISTS", "-" * 72]
    for title, b in (("TEST", d.get("test")), ("DEVELOPMENT", d.get("dev"))):
        if not b:
            continue
        run = b.get("run") or {}
        if not b.get("counts"):
            L.append(f"  {title}: run {run.get('run_id')} {run.get('status')} stage {run.get('stage')}")
            continue
        a, adv = b.get("answers") or {}, b.get("advanced_set")
        L.append(f"  {title}: run {run.get('run_id')} | ADVANCED SET {', '.join(adv) if isinstance(adv, list) else adv}")
        L.append(f"    edge: {a.get('intraday_edge')}")
        L.append(f"    Jev: {a.get('jev_selection')}")
        for r in (b.get("raw_edge") or {}).get("by_tf") or []:
            L.append(f"    raw {r['group']}: {r['setups']} setups gross {_r(r['gross_r'])} cost {r['cost_r']:.3f}R net {_r(r['net_r'])} ({r['diagnosis']})")
        L.append("    failures: " + " · ".join(f"{k} {v}" for k, v in ((b.get("failure_modes") or {}).get("controls") or {}).items()))
    if len(L) == 3:
        L.append("  no V4 run yet")
    return L
