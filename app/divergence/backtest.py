"""Event replay of one independent symbol / rule sleeve; never a portfolio.

Signal calculation is vector-like, execution is deliberately chronological.
Funding is booked only when the position existed before a settlement event.
Gross removes fees and execution slippage but retains funding separately.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
import math
import statistics

from .model import BASE, RULES, RULE_VERSION, clean_bars


def _funding_rows(funding):
    out = {}
    for row in funding:
        try:
            if isinstance(row, dict):
                ts = int(row.get("ts", row.get("fundingTime", row.get("t"))))
                rate = float(row.get("rate", row.get("fundingRate")))
                mark = float(row.get("mark", row.get("markPrice", 0)) or 0)
            else:
                ts, rate, mark = int(row[0]), float(row[1]), float(row[2] or 0)
            if math.isfinite(rate) and math.isfinite(mark) and mark >= 0:
                out[ts] = (ts, rate, mark)
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    return sorted(out.values())


def backtest(bars, signals, funding, start_equity=1000.0, start_ms=None, end_ms=None,
             fee_per_side=0.0004, slippage_per_side=0.0002):
    """One position at a time, 0.5% risk and 1x notional ceiling.

    ``start_ms`` is inclusive and ``end_ms`` exclusive. Signals may use pre-start
    indicator warmup, but must become confirmed inside the evaluation interval.
    The first entry is the 15m OPEN whose timestamp equals trigger CLOSE time.
    A missing entry bar cancels the signal, never silently fills it later.
    Target is recalculated at actual slipped entry to preserve 2R price risk.
    End-of-period positions liquidate at the final close with exit costs, so no
    position or equity carries from training into the held-out interval.
    """
    if start_equity <= 0 or fee_per_side < 0 or slippage_per_side < 0:
        raise ValueError("Positive starting equity and nonnegative costs required")
    all_bars = clean_bars(bars)
    selected = [b for b in all_bars if (start_ms is None or b["t"] >= start_ms)
                and (end_ms is None or b["t"] + BASE <= end_ms)]
    if not selected:
        return dict(version=RULE_VERSION, metrics={}, trades=[], equity=[], monthly=[], skipped=[],
                    book="Independent symbol/rule sleeve", funding_events=0)
    begin = selected[0]["t"] if start_ms is None else start_ms
    finish = selected[-1]["t"] + BASE if end_ms is None else end_ms
    entries = {}
    for signal in signals:
        ts = int(signal["confirmed_at"])
        if begin <= ts < finish:
            entries.setdefault(ts, []).append(signal)
    funding_rows = _funding_rows(funding)
    funding_times = [r[0] for r in funding_rows]
    cash = float(start_equity)
    equity = [dict(t=begin, equity=cash, gross=cash, fees=0.0, slippage=0.0, funding=0.0)]
    trades, skipped = [], []
    pos = None
    fees = slip_cost = funding_pnl = 0.0
    funding_events = estimated_marks = withheld_credits = ambiguous_funding = 0

    def settle(through, ambiguous=False):
        nonlocal cash, funding_pnl, funding_events, estimated_marks, withheld_credits, ambiguous_funding
        if not pos:return
        left=bisect_right(funding_times,pos['funding_through'])
        right=bisect_right(funding_times,through)
        for ts,rate,mark in funding_rows[left:right]:
            if ts<=pos['t']:continue
            if mark<=0:
                estimated_marks+=1
                continue
            amount=-pos['direction']*pos['quantity']*mark*rate
            if ambiguous:
                ambiguous_funding+=1
                if amount>0:
                    withheld_credits+=1
                    continue
            cash+=amount;funding_pnl+=amount;pos['funding']+=amount;funding_events+=1
        pos['funding_through']=through

    def close_position(raw_exit, ts, reason):
        nonlocal cash, pos, fees, slip_cost
        direction = pos["direction"]
        fill = raw_exit * (1 - direction * slippage_per_side)
        exit_fee = pos["quantity"] * fill * fee_per_side
        exit_slip = pos["quantity"] * abs(fill - raw_exit)
        price_pnl = direction * pos["quantity"] * (fill - pos["entry"])
        cash += price_pnl - exit_fee
        fees += exit_fee; slip_cost += exit_slip
        gross = direction * pos["quantity"] * (raw_exit - pos["raw_entry"])
        trades.append(dict(id=pos["signal"]["id"], symbol=pos["signal"].get("symbol"),
                           divergence_tf=pos["signal"].get("divergence_tf"), side=pos["signal"]["side"],
                           entry_t=pos["t"], exit_t=ts, entry=pos["entry"], exit=fill,
                           raw_entry=pos["raw_entry"], raw_exit=raw_exit, stop=pos["stop"], target=pos["target"],
                           quantity=pos["quantity"], gross=gross, fees=pos["entry_fee"] + exit_fee,
                           slippage=pos["entry_slip"] + exit_slip, funding=pos["funding"],
                           net=price_pnl - pos["entry_fee"] - exit_fee + pos["funding"],
                           reason=reason, holding_hours=(ts - pos["t"]) / 3_600_000))
        pos = None

    prior_t = None
    for bar in selected:
        t = bar["t"]
        # No intragap path is invented. Liquidate at the first observed open,
        # including adverse gaps and mark the data-quality failure explicitly.
        if pos:settle(t)
        if pos and prior_t is not None and t != prior_t + BASE:
            close_position(bar["o"], t, "data_gap")
        for signal in sorted(entries.get(t, []), key=lambda x: x["id"]):
            if pos:
                skipped.append(dict(id=signal["id"], reason="Independent book already holds a position"))
                continue
            direction = 1 if signal["side"] == "long" else -1
            stop = float(signal["stop"])
            raw_entry = bar["o"]
            entry = raw_entry * (1 + direction * slippage_per_side)
            distance = direction * (entry - stop)
            if direction * (raw_entry - stop) <= 0 or distance <= 0 or cash <= 0:
                skipped.append(dict(id=signal["id"], reason="Entry open beyond stop or insufficient equity"))
                continue
            reference=abs(signal.get('entry_reference',raw_entry)-stop)
            if reference<=0 or abs(entry-signal.get('entry_reference',raw_entry))>.25*reference:
                skipped.append(dict(id=signal['id'],reason='Price moved beyond entry tolerance'))
                continue
            stop_fill=stop*(1-direction*slippage_per_side)
            loss_per_unit=abs(entry-stop_fill)+fee_per_side*(entry+stop_fill)
            quantity = min(cash * RULES["risk_fraction"] / loss_per_unit,
                           cash * RULES["max_notional_equity"] / (entry * (1 + fee_per_side)))
            entry_fee = quantity * entry * fee_per_side
            entry_slip = quantity * abs(entry - raw_entry)
            cash -= entry_fee; fees += entry_fee; slip_cost += entry_slip
            pos = dict(signal=signal, t=t, direction=direction, entry=entry, raw_entry=raw_entry,
                       stop=stop, target=entry + direction * 2 * distance, quantity=quantity,
                       entry_fee=entry_fee, entry_slip=entry_slip, funding=0.0, funding_through=t)
        if pos:
            long = pos["direction"] == 1
            stop_hit = bar["l"] <= pos["stop"] if long else bar["h"] >= pos["stop"]
            target_hit = bar["h"] >= pos["target"] if long else bar["l"] <= pos["target"]
            # Some exchange settlement timestamps are boundary+1ms. Do not
            # drop those charges just because a stop/target exits in this bar.
            # Intrabar exit ordering is unknowable: charge debits, withhold credits.
            settle(t+BASE-1,ambiguous=stop_hit or target_hit)
            if stop_hit:
                raw_exit = min(bar["o"], pos["stop"]) if long else max(bar["o"], pos["stop"])
                close_position(raw_exit, t + BASE, "stop_and_target_same_bar" if target_hit else "stop")
            elif target_hit:
                close_position(pos["target"], t + BASE, "target")
        if pos and bar is selected[-1]:
            close_position(bar["c"], t + BASE, "period_end")
        marked = cash + (pos["direction"] * pos["quantity"] * (bar["c"] - pos["entry"]) if pos else 0)
        equity.append(dict(t=t + BASE, equity=marked, gross=marked + fees + slip_cost - funding_pnl,
                           fees=fees, slippage=slip_cost, funding=funding_pnl))
        prior_t = t
    existing = {b["t"] for b in selected}
    for t, queued in entries.items():
        if t not in existing:
            skipped.extend(dict(id=s["id"], reason="Missing contiguous next-open entry bar") for s in queued)
    result = _metrics(equity, trades, start_equity)
    result.update(fees=fees, slippage=slip_cost, funding=funding_pnl,
                  gross_pnl=equity[-1]["gross"] - start_equity,
                  net_pnl=equity[-1]["equity"] - start_equity,
                  net_without_funding=equity[-1]["equity"] - start_equity - funding_pnl,
                  funding_drag_pct=-funding_pnl / start_equity * 100,
                  funding_events=funding_events, estimated_funding_marks=estimated_marks,
                  funding_verified=bool(funding_rows) and estimated_marks==0,
                  withheld_ambiguous_funding_credits=withheld_credits,
                  ambiguous_funding_events=ambiguous_funding,
                  funding_ordering='Boundary settlements before exit; intrabar exit ambiguity charges debits and withholds credits',
                  funding_data_available=bool(funding_rows),
                  fee_per_side=fee_per_side, slippage_per_side=slippage_per_side)
    return dict(version=RULE_VERSION, metrics=result, trades=trades, equity=equity,
                monthly=result.pop("monthly"), skipped=skipped, funding_events=funding_events,
                book="Independent symbol/rule sleeve; not an aggregate portfolio",
                interval=dict(start=begin, end=finish))


def _metrics(equity, trades, start):
    daily = {}
    month_end = {}
    peak = start; max_dd = 0.0
    for i, point in enumerate(equity):
        # Close stamped 00:00 belongs to the session/month that just finished.
        date = datetime.fromtimestamp((point["t"] - 1) / 1000, timezone.utc)
        daily[date.strftime("%Y-%m-%d")] = point["equity"]
        if i:
            month_end[date.strftime("%Y-%m")] = point["equity"]
        peak = max(peak, point["equity"])
        max_dd = max(max_dd, 1 - point["equity"] / peak)
    vals = list(daily.values())
    returns = [vals[i] / vals[i-1] - 1 for i in range(1, len(vals)) if vals[i-1] > 0]
    mean = statistics.mean(returns) if returns else 0
    std = statistics.stdev(returns) if len(returns) > 1 else 0
    downside = math.sqrt(sum(min(x, 0) ** 2 for x in returns) / len(returns)) if returns else 0
    monthly = []; prev = start
    for month, value in month_end.items():
        monthly.append(dict(month=month, net_return_pct=(value / prev - 1) * 100 if prev > 0 else 0, end_equity=value))
        prev = value
    days = (equity[-1]["t"] - equity[0]["t"]) / 86_400_000
    final = equity[-1]["equity"]
    annual = ((final / start) ** (365.25 / days) - 1) if days >= 1 and final > 0 else None
    profits = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] < 0)
    monthly_values = [m["net_return_pct"] for m in monthly]
    return dict(start_equity=start, end_equity=final, days=days, trades=len(trades),
                net_return_pct=(final / start - 1) * 100, annualized_return_pct=annual * 100 if annual is not None else None,
                sharpe=mean / std * math.sqrt(365.25) if std else None,
                sortino=mean / downside * math.sqrt(365.25) if downside else None,
                max_drawdown_pct=max_dd * 100, calmar=annual / max_dd if max_dd and annual is not None else None,
                monthly_avg_net_pct=statistics.mean(monthly_values) if monthly_values else 0,
                worst_month_pct=min(monthly_values, default=0), best_month_pct=max(monthly_values, default=0),
                positive_months_pct=sum(v > 0 for v in monthly_values) / len(monthly_values) * 100 if monthly_values else 0,
                win_rate_pct=sum(t["net"] > 0 for t in trades) / len(trades) * 100 if trades else 0,
                profit_factor=profits / losses if losses else None,
                avg_holding_hours=statistics.mean(t["holding_hours"] for t in trades) if trades else 0,
                monthly=monthly, annualization_warning="Short samples are not reliable annual return forecasts")


def split_backtest(bars, signals, funding, start_equity=1000.0):
    """Frozen chronological 70/30 split with a one-15m-bar execution embargo."""
    rows = clean_bars(bars)
    if len(rows) < 4:
        raise ValueError("At least four completed bars required")
    boundary = rows[int(len(rows) * 0.7)]["t"]
    return dict(split_at=boundary, embargo_ms=BASE,
                in_sample=backtest(rows, signals, funding, start_equity, end_ms=boundary),
                out_of_sample=backtest(rows, signals, funding, start_equity, start_ms=boundary + BASE))
