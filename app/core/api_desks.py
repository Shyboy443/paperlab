"""Trading Desk ranking: every desk's paper results side by side (public, GET only, read-only).

Ranking rule (operator, 2026-10-12): highest NET RETURN among the books whose MAX DRAWDOWN is within the viewer's limit;
drawdown is shown next to the return, win rate is shown but never ranked. The limit is applied in the page.

Three tables, because they answer different questions and are not interchangeable:
- live: each desk's forward paper book(s) since it started.
- common: every rule desk replayed on ONE window (the No-Wick backtest window, the shortest history any desk keeps) with
  1,000 USDT per coin. Same window and capital per coin; the coin sets, markets and sizing rules still differ and are
  listed on every row.
- full: each desk's own full backtest window (different lengths); shown for reference, not like-for-like.
Every row carries its exact window (UTC ms), coins, capital, sizing and market. Drawdown is measured on the combined
equity of the desk's books: candle closes for Donchian / MACD, closed trades for the R-based desks (Divergence, No-Wick),
whose intra-trade marks are not stored. Nothing here trades or changes a desk; it only reads stored state.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/public/desks")
_CACHE: dict = {"live": (0.0, None), "bt": (0.0, None)}
RISK_R = 0.005                                                         # Divergence / No-Wick risk per trade
DAY = 86_400_000
NOWICK_WARMUP = 6 * 3_600_000                                          # 15m ATR14 = 3.5 h, 1m EMA50 = 50 min


def _row(desk, tab, pnl, capital, trades, wins, max_dd, *, book="", window=None, coins=None, market="", sizing="",
         dd_basis="", since=None, open_=None, note=None, dd_stress=None, provisional=False):
    return {"desk": desk, "tab": tab, "book": book, "pnl": pnl, "capital": capital,
            "roi_pct": 100 * pnl / capital if capital else None, "max_dd_pct": max_dd, "trades": trades, "wins": wins,
            "win_rate": wins / trades if trades else None, "window": window, "coins": coins, "market": market,
            "sizing": sizing, "dd_basis": dd_basis, "since": since, "open": open_, "note": note,
            "dd_stress_pct": dd_stress, "dd_provisional": provisional}


def _dd(values, start):
    peak, dd = start, 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0)
    return 100 * dd


def _curve(points):
    """Equity points from any desk's curve format, as (time, value) sorted by time."""
    out = []
    for p in points or []:
        t = p.get("time", p.get("ts", p.get("t")))
        v = p.get("value", p.get("equity"))
        if t is not None and v is not None:
            out.append((int(t), float(v)))
    return sorted(out)


def _portfolio(curves, start_each):
    """Sum per-book equity curves (each starting at start_each) on the union of their timestamps; returns max DD %."""
    if not curves:
        return None
    times = sorted({t for c in curves for t, _ in c})
    idx, last, total = [0] * len(curves), [start_each] * len(curves), []
    for t in times:
        for k, c in enumerate(curves):
            while idx[k] < len(c) and c[idx[k]][0] <= t:
                last[k] = c[idx[k]][1]
                idx[k] += 1
        total.append(sum(last))
    return _dd(total, start_each * len(curves))


def _coin_paths(trades, bars, step_ms):
    """One coin's 1,000 USDT book from R-based trades (entry_t, exit_t, r, side, entry, stop), which may overlap.
    Each trade risks RISK_R of the REALIZED equity at its entry; P&L is realized at exit as risk x r. Returns the
    end equity, the closed-trade curve, the mark-to-market curve (every candle close while a trade is open:
    risk x side x (close - entry) / |entry - stop| per open trade; exit costs only in the realized r) and the
    stop-loss stress curve (each trade also dips to -1R, or its realized R if worse, just before it closes)."""
    import bisect
    times = [b["t"] for b in bars]
    marks = {}
    for entry_t, exit_t, *_ in trades:
        k = bisect.bisect_left(times, entry_t)
        while k < len(bars) and bars[k]["t"] + step_ms <= exit_t:
            marks[bars[k]["t"] + step_ms] = bars[k]["c"]
            k += 1
    entries, exits = {}, {}
    for i, tr in enumerate(trades):
        entries.setdefault(tr[0], []).append(i)
        exits.setdefault(tr[1], []).append(i)
    realized, amt, open_, last_px = 1000.0, {}, set(), None
    closed, mtm, stress = [], [], []

    def unreal(px):
        if px is None:
            return 0.0
        return sum(amt[i] * trades[i][3] * (px - trades[i][4]) / (abs(trades[i][4] - trades[i][5]) or float("inf"))
                   for i in open_)

    for t in sorted(set(entries) | set(exits) | set(marks)):
        if t in marks:
            last_px = marks[t]
        for i in exits.get(t, []):
            if i in open_:
                stress.append((t - 1, realized + amt[i] * min(trades[i][2], -1.0)))
                realized += amt[i] * trades[i][2]
                open_.discard(i)
                closed.append((t, realized))
        if not open_ and t not in marks:
            last_px = None                                            # never carry a stale price into the next trade
        for i in entries.get(t, []):
            amt[i] = RISK_R * realized
            open_.add(i)
            if t not in marks:
                last_px = trades[i][4]                                # the fill is the coin's price at this instant
        mtm.append((t, realized + unreal(last_px)))
        stress.append((t, realized))
    return realized, closed, mtm, stress


def _r_books(trades_by_coin, bars_by_coin=None, step_ms=60_000, start_ms=None, end_ms=None):
    """R-based desks -> 1,000 USDT per coin. Trades count only when entered AND exited inside [start_ms, end_ms).
    Drawdowns: dd_mtm = the actual equity curve including open positions at candle closes (None without bars);
    dd_closed = closed trades only; dd_stress = stop-loss stress scenario (NOT an upper bound: unrealized gains can
    lift the peak first, and stops can fill worse than the trigger)."""
    curves = {"closed": [], "mtm": [], "stress": []}
    pnl, n, wins = 0.0, 0, 0
    for sym, trades in trades_by_coin.items():
        kept = [x for x in trades if not ((start_ms and x[0] < start_ms) or (end_ms and x[1] >= end_ms))]
        end_eq, closed, mtm, stress = _coin_paths(kept, (bars_by_coin or {}).get(sym) or [], step_ms)
        curves["closed"].append(closed)
        curves["mtm"].append(mtm)
        curves["stress"].append(stress)
        pnl += end_eq - 1000.0
        n += len(kept)
        wins += sum(1 for x in kept if x[2] > 0)
    return dict(pnl=pnl, n=n, wins=wins, dd_mtm=_portfolio(curves["mtm"], 1000.0) if bars_by_coin else None,
                dd_closed=_portfolio(curves["closed"], 1000.0), dd_stress=_portfolio(curves["stress"], 1000.0))


def _safe(fn, out):
    try:
        out.extend(fn())
    except Exception as exc:                                          # one desk failing must not hide the others
        out.append({"error": f"{fn.__name__}: {type(exc).__name__}"})


# -- live paper books ----------------------------------------------------------------------------------------------------
def build_live(state) -> list:
    live = []

    def ai_live():
        svc = getattr(state, "desk", None)
        if svc is None:
            return []
        rep = svc.report()
        closed = rep.get("closed") or []
        start = float(rep.get("start_balance") or 1000)
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        curve = [v for _, v in _curve(rep.get("equity_curve"))] + [float(rep["equity"])]
        first = min((t.get("created_ts") or 0 for t in closed + list(rep.get("open") or [])), default=0) or None
        return [_row("AI agents", "desk", float(rep["equity"]) - start, start, len(closed), wins, _dd(curve, start),
                     book="group-chat book", coins="agents pick", market="Binance perps (paper)",
                     sizing=f"{(rep.get('rules') or {}).get('risk_pct', 2)}% risk per trade", dd_basis="equity checks",
                     since=first, open_=len(rep.get("open") or []))]

    def divergence_live():
        svc = getattr(state, "divergence", None)
        if svc is None:
            return []
        s = svc.summary()
        out = []
        for name, b in (s.get("books") or {}).items():
            closed = b.get("trades") or []
            start = float(b.get("start_equity") or 1000)
            curve = [v for _, v in _curve(b.get("equity_curve"))] + [float(b["equity"])]
            out.append(_row("Divergence", "divergence", float(b["equity"]) - start, start, int(b.get("closed_trades") or 0),
                            sum(1 for t in closed if (t.get("net") or 0) > 0), _dd(curve, start), book=name,
                            coins=len(s.get("universe") or []), market="Binance perps", sizing="0.5% risk per trade",
                            dd_basis="equity marks", open_=len(b.get("open") or [])))
        return out

    def nowick_live():
        svc = getattr(state, "nowick", None)
        if svc is None or not getattr(svc, "enabled", True):         # switched off by the operator on 2026-10-18
            return []
        s = svc.summary()
        out = []
        for tf, b in (s.get("books") or {}).items():
            start = float(b.get("start_equity") or 1000)
            curve = [v for _, v in _curve(b.get("equity_curve"))] + [float(b.get("equity") or start)]
            out.append(_row("No-Wick", "nowick", float(b.get("equity") or start) - start, start, int(b.get("closed") or 0),
                            int(b.get("wins") or 0), _dd(curve, start), book=tf, coins=len(s.get("universe") or []),
                            market="Binance perps", sizing="0.5% risk per trade",
                            dd_basis="closed trades only (open trades not marked): eligibility provisional",
                            since=s.get("started_ms"), open_=int(b.get("open") or 0), provisional=True))
        return out

    def indicator_live(attr, desk, tab):
        def run():
            svc = getattr(state, attr, None)
            if svc is None:
                return []
            s = svc.summary()
            full = svc.store.get("books", {}) or {}
            books = {k: v for k, v in full.items() if "summary" in v}
            if not books:
                return []
            start_each = float(next(iter(books.values()))["summary"]["start_equity"])
            pnl = sum(float(b["summary"]["end_equity"]) - float(b["summary"]["start_equity"]) for b in books.values())
            cap = sum(float(b["summary"]["start_equity"]) for b in books.values())
            dd = _portfolio([_curve(b.get("equity_curve")) for b in books.values()], start_each)
            market = svc.MODEL.RULES.get("market") or "Binance perps"
            return [_row(desk, tab, pnl, cap, sum(b["summary"]["trades"] for b in books.values()),
                         sum(b["summary"]["wins"] for b in books.values()), dd,
                         book=f"{len(books)} coins" if len(books) > 1 else next(iter(books)).replace("USDT", "") + " book",
                         coins=len(books), market=market, sizing="whole book, 1x", dd_basis="4h closes",
                         since=s.get("started_ms"), open_=sum(1 for b in books.values() if b.get("open_trade")))]
        run.__name__ = attr + "_live"
        return run

    for fn in (ai_live, divergence_live, nowick_live, indicator_live("donchian", "Donchian", "donchian"),
               indicator_live("macd", "MACD", "macd")):
        _safe(fn, live)
    return live


# -- backtests: common window and each desk's full window ------------------------------------------------------------------
def build_backtests(state) -> dict:
    common, full = [], []
    nw = getattr(state, "nowick", None)
    if nw is not None and not getattr(nw, "enabled", True):          # switched off: not ranked, no window anchor
        nw = None
    nw_rep = (nw.store.get("backtest:5m") or {}) if nw is not None else {}
    W = (nw_rep.get("start"), nw_rep.get("end")) if nw_rep.get("status") == "READY" else None
    if W is None:                                                    # fallback: the last 90 days
        now = int(time.time() * 1000)
        W = (now - 90 * DAY, now)
    # Common window on COMPLETED 4h candles. End: snapped down (No-Wick ends at its last closed minute). Start: the first
    # 4h boundary at least NOWICK_WARMUP after No-Wick's first stored minute (its slowest input, the 15m ATR14, needs
    # 3.5 h) at which no No-Wick book holds a trade -- No-Wick's replay carries state across start_ms, so a book is only
    # flat at a boundary nothing spans. The 4h desks re-simulate from a flat start; Divergence signals are independent.
    H4 = 4 * 3_600_000
    data_start = W[0]
    spans = []
    if nw is not None:
        for tf in ("5m", "15m"):
            for sym in ((nw.store.get("backtest:" + tf) or {}).get("per_symbol") or {}):
                spans += [(t["entry_t"], t["exit_t"]) for t in (nw.store.get(f"backtest:{tf}:{sym}") or []) if t.get("exit_t")]
    start = -(-(data_start + NOWICK_WARMUP) // H4) * H4
    while any(lo < start < hi for lo, hi in spans):
        start += H4
    W = (start, W[1] // H4 * H4)
    window_rule = {"data_start": data_start, "warmup_ms": NOWICK_WARMUP, "start": W[0], "end": W[1],
                   "start_rule": "first 4h boundary >= No-Wick data start + 6 h with no No-Wick trade open"}

    def nowick_rows():
        if nw is None:
            return [], []
        c, f = [], []
        for tf in ("5m", "15m"):
            rep = nw.store.get("backtest:" + tf) or {}
            if rep.get("status") != "READY":
                continue
            syms = list(rep.get("per_symbol") or {})
            by = {s_: [(t["entry_t"], t["exit_t"], float(t["r"]), 1 if t["side"] == "long" else -1, float(t["entry"]),
                        float(t["stop"])) for t in (nw.store.get(f"backtest:{tf}:{s_}") or [])
                       if t.get("r") is not None and t.get("exit_t")] for s_ in syms}
            bars = {s_: nw.store.bars(s_, rep.get("start") or 0) for s_ in syms}
            kw = dict(book=tf, coins=len(syms), market="Binance perps, 1m-timed", sizing="0.5% risk per trade, compounded",
                      dd_basis="1m closes incl. open trades")
            for target, win, lo, hi in ((c, list(W), W[0], W[1]), (f, [rep.get("start"), rep.get("end")], None, None)):
                x = _r_books(by, bars, 60_000, lo, hi)
                target.append(_row("No-Wick", "nowick", x["pnl"], 1000.0 * len(syms), x["n"], x["wins"], x["dd_mtm"],
                                   window=win, dd_stress=x["dd_stress"], **kw))
        return c, f

    def divergence_rows():
        svc = getattr(state, "divergence", None)
        if svc is None:
            return [], []
        by, bars, win = {}, {}, [None, None]
        for sym in getattr(svc, "BACKTEST_SYMBOLS", ("BTCUSDT", "ETHUSDT")):
            rep = svc.backtest(sym)
            if rep.get("status") != "READY":
                continue
            by[sym] = [(o["entry_t"], o["exit_t"], float(o["r"]), 1 if t["side"] == "long" else -1, float(o["entry"]),
                        float(o["stop"])) for t in rep.get("trades") or [] for o in [t.get("outcome") or {}]
                       if o.get("exit_t") and o.get("r") is not None and o.get("entry") is not None and o.get("stop") is not None]
            bars[sym] = svc.store.bars(sym, rep.get("start") or 0)
            win = [rep.get("start"), rep.get("end")]
        if not by:
            return [], []
        kw = dict(book="rule backtest", coins=len(by), market="Binance perps",
                  sizing="0.5% risk per trade, 2R target, compounded", dd_basis="15m closes incl. open trades")
        c, f = [], []
        for target, w_, lo, hi in ((c, list(W), W[0], W[1]), (f, win, None, None)):
            x = _r_books(by, bars, 900_000, lo, hi)
            target.append(_row("Divergence", "divergence", x["pnl"], 1000.0 * len(by), x["n"], x["wins"], x["dd_mtm"],
                               window=w_, dd_stress=x["dd_stress"], **kw))
        return c, f

    def donchian_rows():
        svc = getattr(state, "donchian", None)
        if svc is None:
            return [], []
        M = svc.MODEL
        c_curves, f_curves = [], []
        cp, fp = dict(pnl=0.0, n=0, w=0), dict(pnl=0.0, n=0, w=0)
        win = None
        for sym in (svc.store.get("backtest_index", {}) or {}):
            rep = svc.store.get("backtest:" + sym) or {}
            if rep.get("equity_curve"):
                scale = 1000.0 / float(rep["summary"]["start_equity"])
                f_curves.append([(t, v * scale) for t, v in _curve(rep["equity_curve"])])
                fp["pnl"] += (float(rep["summary"]["end_equity"]) - float(rep["summary"]["start_equity"])) * scale
                fp["n"] += rep["summary"]["trades"]
                fp["w"] += rep["summary"]["wins"]
                win = [rep.get("start"), rep.get("end")]
            bars = [b for b in svc.store.bars(sym, W[0] - 60 * DAY) if b["t"] < W[1]]
            if not bars:
                continue
            r = M.simulate(bars, svc.store.funding(sym, bars[0]["t"]), sym, start_ms=W[0], start_equity=1000.0)
            c_curves.append(_curve(r["equity_curve"]))
            cp["pnl"] += r["summary"]["end_equity"] - 1000.0
            cp["n"] += r["summary"]["trades"]
            cp["w"] += r["summary"]["wins"]
        kw = dict(book=M.RULE_VERSION, market="Binance perps", sizing="whole book 1x, long & short", dd_basis="4h closes")
        c = [_row("Donchian", "donchian", cp["pnl"], 1000.0 * len(c_curves), cp["n"], cp["w"], _portfolio(c_curves, 1000.0),
                  window=list(W), coins=len(c_curves), **kw)] if c_curves else []
        f = [_row("Donchian", "donchian", fp["pnl"], 1000.0 * len(f_curves), fp["n"], fp["w"], _portfolio(f_curves, 1000.0),
                  window=win, coins=len(f_curves), **kw)] if f_curves else []
        return c, f

    def macd_rows():
        svc = getattr(state, "macd", None)
        if svc is None:
            return [], []
        M = svc.MODEL
        c, f = [], []
        for sym in svc.universe:
            rep = svc.store.get("backtest:" + sym) or {}
            kw = dict(book=M.RULE_VERSION, coins=1, market=M.RULES.get("market", ""), sizing="all cash, long or cash",
                      dd_basis="4h closes")
            if rep.get("equity_curve"):
                s = rep["summary"]
                scale = 1000.0 / float(s["start_equity"])
                f.append(_row("MACD", "macd", (float(s["end_equity"]) - float(s["start_equity"])) * scale, 1000.0, s["trades"],
                              s["wins"], float(s["max_drawdown_pct"]), window=[rep.get("start"), rep.get("end")], **kw))
            bars = [b for b in svc.store.bars(sym, W[0] - 60 * DAY) if b["t"] < W[1]]
            if bars:
                r = M.simulate(bars, (), sym, start_ms=W[0], start_equity=1000.0)
                s = r["summary"]
                c.append(_row("MACD", "macd", s["end_equity"] - 1000.0, 1000.0, s["trades"], s["wins"],
                              float(s["max_drawdown_pct"]), window=list(W), **kw))
        return c, f

    for fn in (divergence_rows, nowick_rows, donchian_rows, macd_rows):
        try:
            c, f = fn()
            common.extend(c)
            full.extend(f)
        except Exception as exc:
            common.append({"error": f"{fn.__name__}: {type(exc).__name__}"})
    return {"common": common, "full": full, "common_window": list(W), "common_window_rule": window_rule}


@router.get("/ranking")
async def ranking(request: Request):
    now = time.time()
    t, live = _CACHE["live"]
    if live is None or now - t > 60:
        live = await asyncio.to_thread(build_live, request.app.state)
        _CACHE["live"] = (now, live)
    t, bt = _CACHE["bt"]
    if bt is None or now - t > 900:                                  # re-simulating the common window is heavier
        bt = await asyncio.to_thread(build_backtests, request.app.state)
        _CACHE["bt"] = (now, bt)
    return {"live": live, **bt, "rule": "net return among books with max drawdown within the limit; win rate not ranked",
            "generated_ms": int(now * 1000), "paper_only": True, "read_only": True}
