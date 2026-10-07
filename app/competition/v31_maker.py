"""MAKER-FIRST execution experiment (docs/V31_PROTOCOL.md). A diagnostic: it never changes a bot's result.

Every bot in the arena is replayed TAKER-only (entry at the next 1m open after latency, plus the modelled
half-spread). This experiment re-prices each executed entry as if it had been posted as a limit first:

    post a limit at t0 (the moment the taker order filled) -> wait W minutes (5 or 15) ->
    filled if the 1m tape realistically reached it -> otherwise cancel, or convert to a taker order at
    t0 + W only if price is still within 0.25 x the stop distance of the limit (the edge remains);
    if it ran away the trade is MISSED (and a runaway is usually a winner: adverse selection).

    OPTIMISTIC MAKER    limit AT the decision price, filled on a touch (low <= limit)
    CONSERVATIVE MAKER  limit one half-spread better (resting at the bid/ask), filled only if the tape
                        trades THROUGH it by max(1 tick, 1 bp); a touch is not a fill
    TAKER ONLY          the replay itself

A filled maker entry keeps the real trade's exit path (same stop and target prices, same exit fills and
fees), pays the maker fee on entry and gains the price difference. A limit that would only fill after
the real trade already exited is counted as MISSED (its outcome is unknowable without re-simulation).
Verdict: if the edge is positive only with the OPTIMISTIC maker, maker-first FAILS.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.competition.v31_config import MakerConfig
from app.competition.v31_diagnostics import Tape

VARIANTS = ("taker", "conservative", "optimistic")


def _r(x: Any, nd: int = 4) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


def simulate(t: Mapping[str, Any], tape: Tape, *, variant: str, wait_min: int, cfg: MakerConfig,
             taker_fee: float, maker_fee: float, tick: float = 0.0) -> dict[str, Any]:
    """One trade under one execution assumption: {status FILLED/CONVERTED/MISSED/TAKER, net, r}."""
    net0, risk = float(t["net"]), float(t.get("risk_usd") or 0.0)
    if variant == "taker":
        return {"status": "TAKER", "net": net0, "r": net0 / risk if risk else None}
    sgn = 1.0 if t["side"] == "long" else -1.0
    qty, fill0 = float(t["qty"]), float(t["entry"])
    decision = float(t.get("decision_price") or fill0)
    stop_dist = risk / qty if qty else 0.0
    half_bps = float(t.get("half_spread_bps") or max(0.0, sgn * (fill0 / decision - 1.0) * 1e4))
    exit_avg = float(t["exit"])
    exit_fee = max(0.0, float(t["fees"]) - taker_fee * qty * fill0)
    funding = float(t.get("funding") or 0.0)
    t0, exit_ts = int(t["entry_ts"]), int(t["exit_ts"])
    if variant == "optimistic":
        limit, through = decision, 0.0
    else:
        limit = decision * (1.0 - sgn * half_bps / 1e4)
        through = max(tick * cfg.conservative_through_ticks, decision * cfg.conservative_through_bps / 1e4)
    i0 = tape.index(t0 - t0 % 60_000)
    i1 = tape.index(t0 + wait_min * 60_000)
    for i in range(i0, min(i1, len(tape.t))):
        hit = (tape.lo[i] <= limit - through) if sgn > 0 else (tape.h[i] >= limit + through)
        if hit:
            if tape.t[i] > exit_ts:
                return {"status": "MISSED", "net": 0.0, "r": 0.0, "why": "fills after the real exit"}
            gross = (exit_avg - limit) * qty * sgn
            net = gross - maker_fee * qty * limit - exit_fee + funding
            return {"status": "FILLED", "net": net, "r": net / risk if risk else None, "fill_ts": tape.t[i]}
    last = min(i1, len(tape.t)) - 1
    if last < i0:
        return {"status": "MISSED", "net": 0.0, "r": 0.0, "why": "no tape"}
    conv_ts = t0 + wait_min * 60_000
    px = tape.c[last]
    if conv_ts >= exit_ts or sgn * (px - limit) > cfg.convert_within_stop_frac * stop_dist:
        return {"status": "MISSED", "net": 0.0, "r": 0.0,
                "why": "trade already over" if conv_ts >= exit_ts else "price ran away"}
    entry = px * (1.0 + sgn * half_bps / 1e4)
    gross = (exit_avg - entry) * qty * sgn
    net = gross - taker_fee * qty * entry - exit_fee + funding
    return {"status": "CONVERTED", "net": net, "r": net / risk if risk else None}


def _summ(rows: Sequence[dict[str, Any]], taker: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    nets = [r["net"] for r in rows if r["status"] != "MISSED"]
    rs = [r["r"] for r in rows if r["status"] != "MISSED" and r.get("r") is not None]
    missed = [(r, t) for r, t in zip(rows, taker) if r["status"] == "MISSED"]
    wins = sum(x for x in nets if x > 0)
    loss = -sum(x for x in nets if x < 0)
    by = defaultdict(int)
    for r in rows:
        by[r["status"]] += 1
    return {"trades": len(nets), "net": _r(sum(nets)), "expectancy_r": _r(sum(rs) / len(rs)) if rs else None,
            "pf": _r(wins / loss, 3) if loss > 0 else None,
            "win_rate": _r(sum(1 for x in nets if x > 0) / len(nets), 3) if nets else None,
            "status": dict(by), "missed": len(missed),
            "missed_taker_net": _r(sum(float(t["net"]) for _, t in missed)),
            "missed_winners": sum(1 for _, t in missed if float(t["net"]) > 0)}


def simulate_trades(trades: Sequence[Mapping[str, Any]], tape: Tape, cfg: MakerConfig, *, taker_fee: float,
                    maker_fee: float, tick: float = 0.0, group_of=None) -> list[dict[str, Any]]:
    """Every trade of ONE symbol under every variant x wait (tapes are loaded one coin at a time)."""
    out = []
    for t in trades:
        g = str(group_of(t)) if group_of is not None else "ALL"
        for w in cfg.wait_minutes:
            for v in VARIANTS:
                r = simulate(t, tape, variant=v, wait_min=w, cfg=cfg, taker_fee=taker_fee, maker_fee=maker_fee, tick=tick)
                out.append({"group": g, "wait": w, "variant": v, "status": r["status"], "net": r["net"], "r": r.get("r"),
                            "taker_net": float(t["net"])})
    return out


def summarize(rows: Sequence[Mapping[str, Any]], cfg: MakerConfig) -> dict[str, Any]:
    """{group (and ALL): {"5m": {taker, conservative, optimistic, verdict}, "15m": {...}}}"""
    cells: dict[tuple[str, int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        for g in {"ALL", r["group"]}:
            cells[(g, int(r["wait"]), r["variant"])].append(r)
    groups = sorted({g for g, _, _ in cells}, key=lambda g: (g != "ALL", g))
    out: dict[str, Any] = {}
    for g in groups:
        block: dict[str, Any] = {}
        for w in cfg.wait_minutes:
            per: dict[str, Any] = {}
            for v in VARIANTS:
                rs = cells.get((g, w, v), [])
                per[v] = _summ([{"status": x["status"], "net": x["net"], "r": x["r"]} for x in rs],
                               [{"net": x["taker_net"]} for x in rs])
            per["verdict"] = verdict(per["taker"]["net"], per["conservative"]["net"], per["optimistic"]["net"])
            block[f"{w}m"] = per
        out[g] = block
    return out


def experiment(trades: Sequence[Mapping[str, Any]], tapes: Mapping[str, Tape], cfg: MakerConfig, *,
               taker_fee: float, maker_fee: float, ticks: Mapping[str, float] | None = None,
               group_of=None) -> dict[str, Any]:
    """All three variants x every wait, overall and per group (e.g. timeframe)."""
    ticks = ticks or {}
    rows: list[dict[str, Any]] = []
    for sym, tape in tapes.items():
        mine = [t for t in trades if t.get("symbol") == sym]
        rows += simulate_trades(mine, tape, cfg, taker_fee=taker_fee, maker_fee=maker_fee,
                                tick=float(ticks.get(sym) or 0.0), group_of=group_of)
    return summarize(rows, cfg)


def verdict(taker: float | None, conservative: float | None, optimistic: float | None) -> str:
    t, c, o = (taker or 0.0), (conservative or 0.0), (optimistic or 0.0)
    if t > 0:
        return "TAKER PROFITABLE" + (" (maker-first adds to it)" if c > t else "")
    if c > 0:
        return "PROFITABLE ONLY WITH CONSERVATIVE MAKER ENTRIES (candidate, needs live fill evidence)"
    if o > 0:
        return "FAIL: profitable only under the optimistic (unrealistic) maker assumption"
    return "FAIL: unprofitable under every execution assumption"
