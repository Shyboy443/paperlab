"""V6 FORWARD LIVE: the top section of /public/competition/report (HTML) and report.txt.

Server-rendered from the same allow-listed payload as the API (app/core/v6_view.v6_payload): status, forward age and
evidence maturity, the virtual equity and net, trades and candidates in the last 24 h, the gross -> fees -> slippage ->
funding -> net decomposition, the leaderboard, open positions, CONTROL vs +JEV pairs and the Jev live analytics.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.report_v3 import _e, _money, _num, _pct
from app.core.report_v4 import _table


def v6_data(storage: Any, svc: Any = None) -> dict[str, Any]:
    try:
        from app.core.v6_view import v6_payload
        return v6_payload(storage, svc, activity=15)
    except Exception as exc:                      # the report must render even without V6 tables
        return {"experiment": None, "error": type(exc).__name__}


def _ts(ms: Any) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ms) / 1000)) if ms else "—"


def v6_html(d: dict[str, Any]) -> str:
    out = ["<h2 id=v6>V6 FORWARD LIVE</h2>",
           "<p>Frozen V6 bots on LIVE Bybit market data (prices, observed spreads, funding, open interest, basis, "
           "long/short positioning) with simulated fills and real trading costs. DRY_RUN=true: no real orders, no real "
           "money. Only decisions after the forward start count; the books survive every redeploy.</p>"]
    if not d.get("experiment"):
        out.append(f"<p class=muted>{_e(d.get('note') or 'no V6 forward experiment yet')}</p>")
        return "".join(out)
    h, x = d["hero"], d["experiment"]
    tot = d.get("totals") or {}
    c, j = tot.get("controls") or {}, tot.get("jev") or {}
    out.append(f"<p><b>{_e(h['status'])}</b> · experiment {_e(x['experiment_id'])} · forward start {_e(_ts(h['forward_start_ms']))} · "
               f"<b>forward age {_e(h['forward_age'])}</b> · evidence: <b>{_e(h['maturity'])}</b> · "
               f"{h['active_bots']}/{h['bots']} bots live ({h['controls']} controls, {h['jev_bots']} +JEV) · "
               f"{h['positions_open']} positions open · {h['trades_24h']} trades / 24h · {h['candidates_24h']} candidates / 24h</p>")
    out.append(f"<p>total virtual equity <b>{_money(h['total_virtual_equity'])}</b> USDT of {_money(h['start_equity_total'])} · "
               f"net <b>{_money(h['net_pnl'])}</b> · Jev edge {_money(h.get('jev_edge'))} USDT</p>")
    out.append(_table(["closed trades", "trades", "gross", "fees", "slippage", "funding", "net", "exp R", "PF"],
                      [[label, str(s.get("trades")), _money(s.get("gross")), _money(-(s.get("fees") or 0)),
                        _money(-(s.get("slippage") or 0)), _money(s.get("funding")), _money(s.get("net")),
                        _num(s.get("expectancy_r"), 3), _num(s.get("profit_factor"), 2)]
                       for label, s in (("CONTROL", c), ("+JEV", j))]))
    lb = d.get("leaderboard") or []
    if lb:
        out.append("<h4>Leaderboard (paper; ranking is not qualification)</h4>")
        out.append(_table(["#", "bot", "equity", "net", "trades", "24h", "exp R", "PF", "DD", "risk", "evidence"],
                          [[str(r.get("rank")), _e(r.get("key")), _money(r.get("equity_now")), _money(r.get("net_now")),
                            str(r.get("trades")), str(r.get("trades_24h")), _num(r.get("expectancy_r"), 3),
                            _num(r.get("profit_factor"), 2), _pct(r.get("max_dd")), _e(r.get("risk_state")),
                            _e(r.get("maturity"))] for r in lb[:30]]))
    pos = d.get("positions") or []
    if pos:
        out.append("<h4>Open positions</h4>")
        out.append(_table(["bot", "side", "entry", "mark", "unrealized", "stop", "target", "risk %", "hold"],
                          [[_e(p.get("bot_key")), _e(p.get("side")), _num(p.get("entry"), 6), _num(p.get("mark"), 6),
                            _money(p.get("upnl")), _num(p.get("stop"), 6), _num(p.get("target"), 6),
                            _pct(p.get("risk_pct")), f"{int((p.get('hold_s') or 0) // 3600)}h"] for p in pos]))
    jv = d.get("jev") or {}
    lat = jv.get("latency_ms") or {}
    out.append(f"<h4>Jev V6 live</h4><p>{jv.get('decisions', 0)} decisions {_e(jv.get('actions'))} · accepted "
               f"{jv.get('accepted', 0)} · latency p50/p95/p99 {lat.get('p50')}/{lat.get('p95')}/{lat.get('p99')} ms · "
               f"selection alpha {_num(jv.get('selection_alpha_r'), 3)} R · ATTACK increment "
               f"{_money(jv.get('attack_increment_usdt'))} USDT · errors {_e(jv.get('error_codes'))}</p>")
    return "".join(out)


def v6_text(d: dict[str, Any]) -> list[str]:
    L = ["", "V6 FORWARD LIVE (live Bybit data, paper fills, DRY_RUN)", "-" * 72]
    if not d.get("experiment"):
        L.append("  " + str(d.get("note") or "no V6 forward experiment yet"))
        return L
    h, x = d["hero"], d["experiment"]
    tot = d.get("totals") or {}
    L.append(f"  {h['status']} | experiment {x['experiment_id']} | forward start {_ts(h['forward_start_ms'])} | "
             f"forward age {h['forward_age']} | evidence {h['maturity']}")
    L.append(f"  {h['active_bots']}/{h['bots']} bots live | {h['positions_open']} positions open | {h['trades_24h']} trades/24h | "
             f"equity {h['total_virtual_equity']} of {h['start_equity_total']} | net {h['net_pnl']} | Jev edge {h.get('jev_edge')}")
    for label, s in (("CONTROL", tot.get("controls") or {}), ("+JEV", tot.get("jev") or {})):
        L.append(f"  {label}: {s.get('trades')} closed trades, gross {s.get('gross')} fees {s.get('fees')} slippage "
                 f"{s.get('slippage')} funding {s.get('funding')} net {s.get('net')}")
    for r in (d.get("leaderboard") or [])[:10]:
        L.append(f"    #{r.get('rank')} {r.get('key')}: net {r.get('net_now')} over {r.get('trades')} trades ({r.get('maturity')})")
    return L
