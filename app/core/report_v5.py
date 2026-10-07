"""V5 HOURLY / DAILY FUTURES ARENA sections of /public/competition/report (HTML) and report.txt.

Server-rendered from the same allow-listed payload as the API (app/core/system_view.v5_payload): the answers and the
ADVANCED SET, the Stage 1 raw-edge verdict per family x class, HOURLY / DAILY viability, the gross -> maker fees ->
taker fees -> slippage -> funding paid / received -> net decomposition, the top controls after costs, Jev V5 against
its baselines and the account-size diagnostic -- for the DEVELOPMENT run and the pre-registered PSEUDO-HOLDOUT.
"""
from __future__ import annotations

from typing import Any

from app.core.report_v3 import _e, _money, _num, _pct
from app.core.report_v4 import _r, _table


def v5_data(storage: Any) -> dict[str, Any]:
    try:
        from app.core.system_view import v5_payload
        return v5_payload(storage)
    except Exception as exc:                  # the report must render even without V5 tables
        return {"dev": None, "test": None, "error": type(exc).__name__}


def _fam_rows(b: dict[str, Any]) -> list[list[str]]:
    out = []
    for f in b.get("families") or []:
        r = f.get("raw_edge") or {}
        e = f.get("economic_20") or f.get("economic_20_baseline") or {}
        c = f.get("costs_100") or {}
        out.append([_e(f"{f.get('strategy_id')} {f.get('family')}"), _e(f.get("horizon")), str(r.get("trades")),
                    _r(r.get("mean_r")), _num(r.get("p_mean_le_0"), 3),
                    _e(" / ".join(f"{x:+.2f}" if isinstance(x, (int, float)) else "–" for x in r.get("subperiod_mean_r") or [])),
                    _num(c.get("cost_r"), 3), _r(c.get("funding_r")), f"{_r(e.get('mean_r'))} ({e.get('trades')})",
                    _e(f.get("verdict") or ("PASSED" if r.get("passed") else "NO_RAW_EDGE"))])
    return out


def _block_html(title: str, b: dict[str, Any]) -> str:
    run = b.get("run") or {}
    if not b.get("counts"):
        return (f"<h3>{_e(title)}</h3><p class=muted>run {_e(run.get('run_id'))} {_e(run.get('status'))} · "
                f"stage {_e(run.get('stage'))}</p>")
    a, w = b.get("answers") or {}, b.get("window") or {}
    adv = b.get("advanced_set")
    c = (b.get("costs") or {}).get("controls_20") or {}
    out = [f"<h3>{_e(title)} · {_e(w.get('from'))} → {_e(w.get('to'))} · run {_e(run.get('run_id'))}</h3>",
           f"<p><b>ADVANCED SET: {_e(', '.join(adv) if isinstance(adv, list) else adv)}</b><br>raw edge: {_e(a.get('raw_edge'))}"
           f"<br>after costs: {_e(a.get('economics'))}<br>Jev: {_e(a.get('jev'))}</p>",
           f"<p class=muted>coins (objective universe rule, never PnL): {_e(', '.join(b.get('coins') or []))} · data: "
           f"{_e(b.get('data'))}</p>",
           "<h4>Stage 1 raw edge per family × class (gross R of the 100 USDT twins; economics on the 20 USDT books)</h4>",
           _table(["family", "class", "trades", "gross R", "P(≤0)", "sub-periods", "cost R", "funding R", "net R @20 (n)",
                   "verdict"], _fam_rows(b))]
    via = b.get("viability") or []
    if via:
        out.append("<h4>HOURLY / DAILY viability (official 20 USDT controls)</h4>")
        out.append(_table(["horizon", "bots", "trades", "trades/day/bot", "avg hold h", "gross exp", "fees", "funding",
                           "net exp", "PF", "profitable", "qualified"],
                          [[_e(r["horizon"]), str(r["bots"]), str(r["trades"]), _num(r.get("trades_per_day_per_bot"), 3),
                            _num(r.get("avg_hold_h"), 1), _r(r.get("gross_exp_r")), _money(r.get("fees")),
                            _money(r.get("funding")), _r(r.get("net_exp_r")), _num(r.get("pf"), 2),
                            _pct(r.get("profitable_pct")), str(r.get("qualified"))] for r in via]))
    out.append("<h4>Gross → net (official controls)</h4>")
    out.append(_table(["gross", "maker fees", "taker fees", "slippage", "funding paid", "funding received", "net",
                       "trades", "net / trade", "refused (min notional)"],
                      [[_money(c.get("gross")), _money(-(c.get("maker_fees") or 0)), _money(-(c.get("taker_fees") or 0)),
                        _money(-(c.get("slippage") or 0)), _money(-(c.get("funding_paid") or 0)),
                        _money(c.get("funding_received")), _money(c.get("net")), str(c.get("trades")),
                        _money(c.get("net_per_trade")), str(c.get("refused_min_notional"))]]))
    lb = (b.get("leaderboard_controls") or [])[:10]
    if lb:
        out.append("<h4>Top 10 controls after costs (ranking is not qualification)</h4>")
        out.append(_table(["#", "bot", "trades", "hold h", "gross", "fees", "funding", "net", "exp R", "PF", "status"],
                          [[str(r.get("rank")), _e(r.get("key")), str(r.get("trades")), _num(r.get("avg_hold_h"), 1),
                            _money(r.get("gross")), _money(-(r.get("fees") or 0)), _money(r.get("funding")),
                            _money(r.get("net")), _r(r.get("exp_r")), _num(r.get("pf"), 2),
                            _e(r.get("failure_mode") or r.get("state"))] for r in lb]))
    j = b.get("jev_pooled") or {}
    if j.get("decisions"):
        sa, att = b.get("selection_alpha") or {}, b.get("attack") or {}
        auc = j.get("auc_support") or {}
        out.append(f"<h4>Jev V5</h4><p>{j.get('decisions')} decisions · AUC {_num(auc.get('auc'), 3)} "
                   f"[{_num(auc.get('low'), 3)}, {_num(auc.get('high'), 3)}] · selection alpha {_money(sa.get('total_usdt'))} USDT "
                   f"· ATTACK added {_money(att.get('attack_added_net'))} USDT vs the same trades at normal size</p>")
    cap = b.get("capacity") or {}
    if cap.get("verdicts"):
        out.append(f"<p>Account size (diagnostic only, never qualifies a 20 USDT bot): {_e(cap['verdicts'])}</p>")
    fm = (b.get("failure_modes") or {}).get("controls") or {}
    out.append("<p class=muted>primary failure of each CONTROL: " + _e(" · ".join(f"{k} {v}" for k, v in fm.items())) + "</p>")
    return "".join(out)


def v5_html(d: dict[str, Any]) -> str:
    out = ["<h2 id=v5>V5 — HOURLY / DAILY FUTURES ARENA (frozen research history)</h2>",
           "<p>HOURLY (1h signal, 4h + 1D context, 4-24 h holds) and SWING (4h signal, 1D + 1W context, 12-72 h holds); "
           "eight positioning families on Bybit-native funding, open interest, basis and the account long/short ratio; "
           "Bybit fees, filters, spread, slippage and actual funding settlements; raw edge first (Stage 1 on 100 USDT "
           "twins), then after-cost economics, then Jev V5 only for survivors. Frozen after DEVELOPMENT "
           "(docs/V5_FREEZE.md); the pseudo-holdout was pre-registered before download (docs/V5_TEST_PREREGISTRATION.json)."
           "</p>"]
    dev, test = d.get("dev"), d.get("test")
    if not dev and not test:
        out.append("<p class=muted>No V5 run yet.</p>")
        return "".join(out)
    if test:
        out.append(_block_html("PSEUDO-HOLDOUT (pre-registered)", test))
    if dev:
        out.append(_block_html("DEVELOPMENT", dev))
    return "".join(out)


def v5_text(d: dict[str, Any]) -> list[str]:
    L = ["", "V5 - HOURLY / DAILY FUTURES ARENA (frozen research history)", "-" * 72]
    for title, b in (("PSEUDO-HOLDOUT", d.get("test")), ("DEVELOPMENT", d.get("dev"))):
        if not b:
            continue
        run = b.get("run") or {}
        if not b.get("counts"):
            L.append(f"  {title}: run {run.get('run_id')} {run.get('status')} stage {run.get('stage')}")
            continue
        a, adv = b.get("answers") or {}, b.get("advanced_set")
        c = (b.get("costs") or {}).get("controls_20") or {}
        L.append(f"  {title}: run {run.get('run_id')} | ADVANCED SET {', '.join(adv) if isinstance(adv, list) else adv}")
        L.append(f"    raw edge: {a.get('raw_edge')} | after costs: {a.get('economics')}")
        L.append(f"    controls: gross {c.get('gross')} taker fees {c.get('taker_fees')} maker fees {c.get('maker_fees')} "
                 f"slippage {c.get('slippage')} funding paid {c.get('funding_paid')} received {c.get('funding_received')} "
                 f"net {c.get('net')} USDT over {c.get('trades')} trades")
        for f in b.get("families") or []:
            r = f.get("raw_edge") or {}
            L.append(f"    {f.get('strategy_id')} {f.get('horizon'):<6} gross {_r(r.get('mean_r'))} p {r.get('p_mean_le_0')} "
                     f"n {r.get('trades')} -> {f.get('verdict')}")
    if len(L) == 3:
        L.append("  no V5 run yet")
    return L
