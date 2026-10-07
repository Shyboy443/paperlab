"""ReplayEngine: run a strategy over historical bars using the live trading components.

Everything that decides money is imported, never reimplemented:

    sizing / gates      app.core.risk.RiskManager.approve
    fills / fees        app.core.portfolio.Portfolio.open_position / close_position, charged at the
                        FeeSchedule rates (see `fee_source`)
    exits               app.core.positions.ExitEngine.on_price / apply_update
    slippage            Portfolio.slippage_bps() -> settings.slippage_bps_{min,max}

The one thing history cannot supply is a price path inside a bar. Live, ExitEngine sees every trade
tick; here it sees four prices per bar:

    PATH = open -> adverse extreme -> favourable extreme -> close

"adverse" is resolved per position, so a long is walked open->low->high->close and a short
open->high->low->close. That is the pessimistic assumption: when a bar's range spans both a stop and
a take-profit, the stop is reached first. It is also why a stop no longer scores exactly -1R here --
the exit fills at the path price, not at the stop level, which is what the live ExitEngine does.

Not modelled (klines carry none of it): order book, trade tape, funding payments, mark-vs-last
divergence, exchange rejects, latency. Strategies that require those feeds are refused by
`unsupported()` rather than run on empty data.

A book that hits its strategy halt floor stays halted for the rest of the replay; the live engine
respawns it on a new life. Respawn is deliberately omitted so a blown book reads as a blown book in
the results instead of being quietly rolled forward.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from app.backtest.brackets import BracketTable
from app.backtest.context import ReplayContext, default_rules
from app.backtest.funding import FundingSchedule
from app.execution.config import LEGACY_PRE_2026_09_23, ExecutionConfig, FeeSchedule, fee_schedule_for
from app.execution.model import ExecutionModel, MarketState
from app.execution.orders import Order
from app.core.portfolio import ClosedTrade, Portfolio
from app.core.positions import ExitEngine
from app.core.risk import RiskManager, StrategyMeta
from app.core.types import Candle, Fill, MarketRules, Signal, tf_ms

# Feeds a historical kline replay cannot provide, and the strategies that need them.
NEEDS_LIVE_FEED = {"S16": "funding", "S17": "order book", "S18": "cross-symbol ranking",
                   "S20": "votes from other strategies"}


def unsupported(strategy_id: str, cls: Any = None) -> str:
    """Why this strategy cannot be replayed on klines, or '' when it can."""
    if strategy_id in NEEDS_LIVE_FEED:
        return NEEDS_LIVE_FEED[strategy_id]
    if cls is not None:
        if getattr(cls, "needs_book", False):
            return "order book"
        if getattr(cls, "needs_funding", False):
            return "funding"
        if getattr(cls, "needs_ticks", False):
            return "trade tape"
    return ""


@dataclass
class ReplayResult:
    strategy_id: str
    trades: list[ClosedTrade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)                 # the authoritative ledger
    equity: list[tuple[int, float]] = field(default_factory=list)   # (ts, wallet equity) per bar
    rejects: dict[str, int] = field(default_factory=dict)           # risk reject reason -> count
    starting_equity: float = 0.0
    halted: bool = False
    halt_ts: int | None = None
    # Leverage actually USED, sampled once per bar. `max_leverage` is only a ceiling: the
    # RiskManager sizes from risk-per-trade and stop distance, so a 20x book routinely runs at 2x.
    bars_seen: int = 0
    bars_in_market: int = 0
    lev_sum: float = 0.0            # summed over bars holding a position
    lev_sum_all: float = 0.0        # summed over every bar (flat counts as 0)
    lev_max: float = 0.0
    margin_sum: float = 0.0
    margin_max: float = 0.0
    signals: int = 0                # entry signals the strategy emitted on its own market
    partial_fills: int = 0          # entries/exits whose order only partly filled
    fee_source: str = "schedule"
    # Gate (Jev) bookkeeping. `gate_events` records what happened to every gated candidate; the
    # shadow ledger holds what a REFUSED candidate would have done at the control's size.
    gate_events: list[dict[str, Any]] = field(default_factory=list)
    shadow_trades: list[ClosedTrade] = field(default_factory=list)
    shadow_links: dict[str, str] = field(default_factory=dict)       # shadow position id -> decision id
    cost_gate_events: list[dict[str, Any]] = field(default_factory=list)
    entry_links: dict[str, str] = field(default_factory=dict)        # real position id -> decision id

    @property
    def avg_effective_leverage(self) -> float:
        return self.lev_sum / self.bars_in_market if self.bars_in_market else 0.0

    @property
    def time_weighted_leverage(self) -> float:
        """Averaged over ALL bars, so time spent flat counts as zero exposure."""
        return self.lev_sum_all / self.bars_seen if self.bars_seen else 0.0

    @property
    def margin_utilization_avg(self) -> float:
        return self.margin_sum / self.bars_seen if self.bars_seen else 0.0

    @property
    def r_series(self) -> list[float]:
        return [t.r_multiple for t in self.trades]

    @property
    def final_equity(self) -> float:
        return self.equity[-1][1] if self.equity else self.starting_equity

    def max_drawdown(self) -> float:
        """Peak-to-trough on the equity curve, as a negative fraction."""
        peak, worst = self.starting_equity, 0.0
        for _, eq in self.equity:
            peak = max(peak, eq)
            if peak > 0:
                worst = min(worst, eq / peak - 1.0)
        return worst


class ReplayEngine:
    PATH = ("open", "adverse", "favourable", "close")

    def __init__(self, settings: Any, symbols: Sequence[str],
                 rules: dict[str, MarketRules] | None = None, venue: str = "futures",
                 seed: int | None = None, clock_start: int = 0,
                 funding: FundingSchedule | None = None,
                 execution: ExecutionConfig | None = None, fees: FeeSchedule | None = None,
                 brackets: BracketTable | None = None, fee_source: str = "schedule",
                 sizing: Callable[[Signal, dict[str, Any]], tuple[float, dict[str, Any]]] | None = None,
                 leverage_policy: str = "ceiling", max_risk_pct: float | None = None,
                 gate: Callable[[Signal, dict[str, Any]], Any] | None = None,
                 cost_gate: Callable[[Signal, dict[str, Any]], tuple[bool, dict[str, Any]]] | None = None,
                 shadow_cost_rejects: bool = False,
                 quotes: Callable[[str, int], float | None] | None = None):
        """`fee_source` decides which rates the wallet is actually CHARGED:

        * "schedule" (default) -- `fees` if given, else the configured venue's own schedule
          (app.execution.config.fee_schedule_for), which is exactly what the live paper engine
          charges for the same venue. The RiskManager's fee gate reads the same rates.
        * "legacy" -- the frozen pre-2026-09-23 rates (0.04% taker / 0.02% maker). Every run
          before that date was charged this way while displaying 0.05%; it exists ONLY so those
          runs stay reproducible, e.g. validation bdddc67f6315.
        * "settings" -- whatever `settings.taker_fee` / `maker_fee` say.

        `sizing(signal, health) -> (risk multiplier, info)` lets a caller scale risk per signal from
        the bot's own state (the arena's ATTACK/NORMAL/DEFENSIVE/HALTED machine). A multiplier of 0
        refuses the entry. The RiskManager still owns every gate and the final size.

        `leverage_policy` "needed" gives each position the smallest leverage its margin requires,
        capped by the bot's ceiling, instead of the ceiling itself: a 20x ceiling is available when
        sizing needs it rather than applied to every trade.

        `max_risk_pct` is a hard ceiling on the risk of any single entry, as a fraction of the
        bot's equity. It can only shrink or refuse an order. It matters for sizing paths the risk
        multiplier does not reach -- grid legs sized from a margin fraction, for instance.

        `gate(signal, context) -> verdict` is a decision layer between the strategy and the
        RiskManager (Jev). It is consulted only for GENUINE candidates -- signals the RiskManager
        has already approved at the control's size -- and may only scale that size (0 = skip).
        A scaled order goes back through the RiskManager, which stays the final authority. Every
        refused candidate is re-created in a separate shadow book at the control's size, so the
        cost of each skip can be measured without touching the gated bot's wallet.

        `quotes(symbol, ts) -> half spread in bps | None` (live forward only, default off): the half-spread
        OBSERVED on the exchange at or before `ts`. When it is known, every fill crosses a quote that wide
        around its reference price (execution level 2) instead of the modelled OHLCV spread.
        """
        venue_cfg = getattr(settings, "venue", None)          # NOT `venue`: that is the kind string
        default = (fee_schedule_for(venue_cfg.exchange, venue_cfg.caps.kind) if venue_cfg is not None
                   else FeeSchedule())
        if fee_source == "legacy":
            fees = LEGACY_PRE_2026_09_23
        fees = fees or default
        if fee_source in ("schedule", "legacy"):
            settings = dataclasses.replace(settings, taker_fee=fees.taker_rate,
                                           maker_fee=fees.maker_rate, fee_source=fees.source)
        elif fee_source != "settings":
            raise ValueError(f"fee_source must be 'schedule', 'legacy' or 'settings', not {fee_source!r}")
        if leverage_policy not in ("ceiling", "needed"):
            raise ValueError(f"leverage_policy must be 'ceiling' or 'needed', not {leverage_policy!r}")
        self.fee_source = fee_source
        self.quotes = quotes
        self.sizing = sizing
        self.leverage_policy = leverage_policy
        self.max_risk_pct = max_risk_pct
        self.gate = gate
        self.cost_gate = cost_gate
        self.settings = settings
        self.symbols = list(symbols)
        self.rules = dict(rules) if rules else default_rules(symbols)
        self.venue = venue
        self._now = clock_start
        rng = random.Random(seed) if seed is not None else None
        self.portfolio = Portfolio(settings, self.rules, self._clock, rng=rng)
        self.risk = RiskManager(settings, self.portfolio, self.rules, self._clock)
        self.brackets = brackets or BracketTable.fallback()
        # Maintenance margin comes from the notional-tiered bracket table, never from
        # exchangeInfo.maintMarginPercent (which is the generic 20x-tier default for every symbol).
        self.exits = ExitEngine(self.rules, mmr_by_symbol=self.brackets.first_bracket_mmr(),
                                atr_provider=self._atr, line_provider=self._line)
        self.execution = ExecutionModel(execution or ExecutionConfig(), fees)
        # Shadow book: same rules, fees, execution and exit logic, its own wallet. It never
        # influences the gated bot; it only answers "what would the refused candidate have done".
        shadowing = gate is not None or (cost_gate is not None and shadow_cost_rejects)
        self.shadow = Portfolio(settings, self.rules, self._clock) if shadowing else None
        self.shadow_exits = (ExitEngine(self.rules, mmr_by_symbol=self.brackets.first_bracket_mmr(),
                                        atr_provider=self._atr, line_provider=self._line)
                             if shadowing else None)
        self._shadow_pending: list[tuple[Order, Any, Any, str]] = []
        self.latency_ms = self.execution.config.total_latency_ms
        self.ctx = ReplayContext(self.portfolio, self.rules, symbols, venue=venue)
        self.risk.state.engine_running = True
        self.funding = funding
        self._day = ""
        self._funded: dict[str, int] = {}
        self._pending: list[tuple[Order, Any, Any]] = []
        self._prev_bar: dict[str, Candle] = {}
        # Execution stress only (never a strategy input): "adverse" charges every funding
        # settlement against the open position at the historical |rate|. Default "normal".
        self.funding_mode = "normal"

    def _clock(self) -> int:
        return self._now

    # -- providers the live ExitEngine expects ---------------------------------------------
    def _atr(self, symbol: str, tf: str, period: int) -> float | None:
        return self.ctx.ind_last(symbol, tf or "1m", "atr", n=int(period or 14))

    def _line(self, symbol: str, tf: str) -> float | None:
        return self.ctx.ind_last(symbol, tf or "1m", "supertrend")

    # -- intrabar path ----------------------------------------------------------------------
    @staticmethod
    def _path(bar: Candle, side: str) -> list[tuple[float, int]]:
        """Four (price, ts) steps across the bar, adverse extreme before the favourable one."""
        span = max(1, bar.close_time - bar.open_time)
        adverse, favourable = (bar.low, bar.high) if side == "long" else (bar.high, bar.low)
        return [(bar.open, bar.open_time), (adverse, bar.open_time + span // 3),
                (favourable, bar.open_time + 2 * span // 3), (bar.close, bar.close_time)]

    # -- the run ------------------------------------------------------------------------------
    def run(self, cls: Any, bars: Iterable[Candle], *, since_ms: int = 0,
            size_mult: float = 1.0, leverage: int | None = None,
            reset_at: Sequence[int] | None = None,
            signal_tf: str | None = None, only_symbol: str | None = None) -> ReplayResult:
        """Replay one strategy over a 1m tape sorted by (close_time, symbol).

        `signal_tf` makes this a single-timeframe specialist: `on_candle` fires only on bars of
        that timeframe, while the strategy's own declared timeframes are still aggregated and
        pushed into the context so multi-timeframe indicator logic keeps working. Without that a
        dual-timeframe strategy forced onto one signal timeframe would read an empty higher series
        and silently stop trading.

        `only_symbol` confines the bot to one market: signals for anything else are refused and
        counted. The symbol is part of a specialist bot's identity, so it may not drift to another
        coin because its own is going against it.

        `reset_at` rebases the book to its starting balance at each listed timestamp, flattening
        whatever is open first. Walk-forward needs this: a 20 USDT book that blows its halt floor
        in week one stays halted for the rest of the tape, so a single continuous multi-year replay
        yields no out-of-sample evidence at all after the first failure. Resetting at each
        out-of-sample window boundary makes every window an independent 20 USDT competition, which
        is what the master OOS ledger is meant to concatenate.
        """
        strat = cls()
        sid = cls.id
        balance = float(self.settings.strategy_starting_balance)
        self.portfolio.set_allocation(sid, balance)
        self.risk.set_halt_floor(sid, balance)
        self.ctx.board.set_enabled(sid, True)
        meta = StrategyMeta(sid, True, size_mult, int(leverage or cls.default_leverage),
                            int(cls.max_positions), float(cls.min_rr), strat.supports(self.venue))
        # Subscribe to everything the strategy needs AND the signal timeframe; trigger on the
        # signal timeframe alone when one is given.
        self._signal_tf = signal_tf
        self._only_symbol = only_symbol
        res = ReplayResult(sid, starting_equity=balance, fee_source=self.fee_source)
        self._meta = meta        # _reset_book has to clear the per-strategy halt flag too
        self._peak = balance
        self._fills: list[Fill] = res.fills
        self._res = res
        self._pending = []
        self._shadow_pending = []
        self._funded = {}
        self._prev_bar = {}
        tfs = tuple(dict.fromkeys(tuple(cls.timeframes) + ((signal_tf,) if signal_tf else ())))
        agg: dict[tuple[str, str], list[Candle]] = {}
        cooldown_ms = strat.cooldown_ms() or 0

        resets = sorted(reset_at or ())
        next_reset = 0
        for bar in bars:
            if only_symbol and bar.symbol != only_symbol:
                continue
            self._now = bar.close_time
            self._bar = bar
            while next_reset < len(resets) and bar.close_time >= resets[next_reset]:
                self._reset_book(sid, balance, res, resets[next_reset])
                next_reset += 1
            self._roll_day(bar.close_time)
            self._push_bars(bar, tfs, agg)
            # 1. execute orders whose latency has elapsed, settle any funding instants this bar
            #    crossed, then walk the bar's price path against every open position
            self._execute_pending(bar, meta, res)
            self._shadow_execute(bar)
            self._settle_funding(bar)
            self._walk(bar)
            self._shadow_walk(bar)
            self.ctx.set_price(bar.symbol, bar.close, bar.close_time)
            # 2. let the strategy manage what it holds, then emit, on the bars it subscribes to
            for tf in tfs:
                closed = self.ctx.latest(bar.symbol, tf, bar.close_time)
                if closed is None:
                    continue
                self._manage(strat, closed, bar)
                if bar.close_time < since_ms:
                    continue
                # A specialist only decides on its own timeframe; the others are context.
                if signal_tf and tf != signal_tf:
                    continue
                self._signals(strat, closed, meta, res, cooldown_ms)
            # 3. mark the book, and sample how much leverage is actually in use
            eq = self.portfolio.wallet_equity(sid, self.ctx.prices)
            self._peak = max(self._peak, eq)
            res.equity.append((bar.close_time, eq))
            self._sample_leverage(res, sid, eq)
            self._prev_bar[bar.symbol] = bar
            if not res.halted and self.risk.check_strategy_halt(sid, eq):
                res.halted, res.halt_ts = True, bar.close_time
                meta.halted = True

        self._flatten(sid)
        self._shadow_flatten()
        res.trades = [t for t in self.portfolio.closed_trades if t.strategy_id == sid]
        if self.shadow is not None:
            res.shadow_trades = list(self.shadow.closed_trades)
        return res

    def _market_state(self, bar: Candle, price: float, ts: int, tf: str = "1m",
                      complete: bool = False) -> MarketState:
        """What the execution model may see, and nothing later.

        The range and volume proxies come from the PREVIOUS closed bar, not the bar being executed
        against: at the moment an order executes, the current bar's high, low and final volume have
        not happened yet. Using them would be look-ahead -- small, but exactly the kind that makes a
        backtest quietly optimistic. `complete=True` is for an exit priced at a path point the walk
        has already reached, where the bar's extremes are legitimately known.
        """
        prev = self._prev_bar.get(bar.symbol)
        ref = bar if complete else prev
        bid = ask = None
        if self.quotes is not None and price > 0:
            half = self.quotes(bar.symbol, int(ts))
            if half is not None and half >= 0:
                bid, ask = price * (1.0 - half / 1e4), price * (1.0 + half / 1e4)
        return MarketState(symbol=bar.symbol, ts=ts, last=price, bid=bid, ask=ask,
                           atr=self.ctx.ind_last(bar.symbol, tf, "atr", n=14),
                           bar_range=abs(ref.high - ref.low) if ref else None,
                           quote_volume=(ref.quote_volume or None) if ref else None,
                           tick=self.rules[bar.symbol].tick if bar.symbol in self.rules else None)

    def _execute_pending(self, bar: Candle, meta: StrategyMeta, res: ReplayResult) -> None:
        """Fill queued orders whose latency has elapsed.

        An order signalled on the close of bar N has `execute_at` a few hundred ms later, which is
        inside bar N+1. The earliest price attributable to that instant is bar N+1's OPEN, so that
        is the reference the execution model prices against. This is strictly worse than filling at
        the signal bar's close, which is the point: the old behaviour gave every strategy a free
        zero-latency fill at a price it could only know once the bar had already closed.
        """
        if not self._pending:
            return
        still: list[tuple[Order, Any, Any]] = []
        for order, sig, decision in self._pending:
            if order.symbol != bar.symbol or bar.close_time < order.execute_at:
                still.append((order, sig, decision))
                continue
            ts = max(bar.open_time, order.execute_at)
            state = self._market_state(bar, bar.open, ts, sig.tf or "1m")
            result = self.execution.execute(order, state)
            if not result.fills or result.rejected:
                res.rejects["execution_" + (result.rejected or "no_fill")] =                     res.rejects.get("execution_" + (result.rejected or "no_fill"), 0) + 1
                order.reject(result.rejected or "no_fill")
                continue
            for f in result.fills:
                order.add_fill(f)
            if result.remainder == "EXPIRED" and not order.is_terminal:
                order.expire()           # a MARKET remainder never rests on Binance
            if result.partial:
                res.partial_fills += 1
            self._now = ts
            self.ctx.set_price(bar.symbol, bar.open, ts)
            _pos, fill = self.portfolio.open_position(
                sig, decision, result.decision_price, True, ts=ts,
                fill_price=result.avg_price, maker=(result.role == "MAKER"),
                qty=result.filled_qty,
                fill_meta={**(order.meta.get("sizing") or {}),
                           "requested_qty": order.qty, "order_state": order.state,
                           "execution_level": result.execution_level_used,
                           "level_reason": result.level_reason,
                           "liquidity_role": result.role,
                           "latency_cost": result.latency_cost,
                           "spread_cost": result.spread_cost,
                           "impact_cost": result.impact_cost,
                           "book_depth_consumed": result.book_depth_consumed,
                           "latency_ms": ts - order.signal_ts})
            self._fills.append(fill)
            did = (order.meta.get("sizing") or {}).get("decision_id")
            if did:
                res.entry_links[_pos.id] = did
        self._pending = still

    def _reset_book(self, sid: str, balance: float, res: ReplayResult, ts: int) -> None:
        """Flatten and rebase to the starting balance, clearing any halt.

        Open positions are closed at the last price first, so the previous window's result is
        complete before the next one starts; nothing is carried across a boundary.
        """
        for pos in list(self.portfolio.positions_of(sid)):
            price = self.ctx.prices.get(pos.symbol)
            bar = getattr(self, "_bar", None)
            if price and bar is not None:
                self._fills.append(self._close(pos, pos.id, 1.0, price, "reset",
                                               "walk-forward window reset", bar, ts))
        wallet = self.portfolio.ensure_wallet(sid, balance)
        wallet.realized = 0.0
        wallet.fees = 0.0
        wallet.funding = 0.0
        wallet.allocation = balance
        self.risk.state.halted_strategies.discard(sid)
        # RiskManager.approve refuses on EITHER the shared halt set or the per-strategy flag, so
        # clearing only one of them leaves the book silently halted for the rest of the tape.
        meta = getattr(self, "_meta", None)
        if meta is not None:
            meta.halted = False
            meta.cooldown_until.clear()
        self.risk.state.daily_halted = False
        self.risk.set_halt_floor(sid, balance)
        self._pending = []
        self._peak = balance
        res.halted = False

    def _sample_leverage(self, res: ReplayResult, sid: str, equity: float) -> None:
        res.bars_seen += 1
        if equity <= 0:
            return
        notional = sum(p.notional(self.ctx.prices.get(p.symbol) or p.entry_price)
                       for p in self.portfolio.positions_of(sid))
        util = self.portfolio.margin_used(sid) / equity
        res.margin_sum += util
        res.margin_max = max(res.margin_max, util)
        if notional <= 0:
            return
        lev = notional / equity
        res.bars_in_market += 1
        res.lev_sum += lev
        res.lev_sum_all += lev
        res.lev_max = max(res.lev_max, lev)

    def _settle_funding(self, bar: Candle) -> None:
        """Pay/receive real historical funding on positions open at each settlement instant.

        Mirrors engine_dispatch.on_funding: the wallet is charged through Portfolio.apply_funding,
        so funding lands in `wallet.funding` and in the ledger as its own fill kind, never folded
        into trade PnL.
        """
        if not self.funding:
            return
        prev = self._funded.get(bar.symbol, bar.open_time - 1)
        events = self.funding.events(bar.symbol, prev, bar.close_time)
        if not events:
            return
        self._funded[bar.symbol] = events[-1][0]
        if self.shadow is not None and self.shadow.positions_on(bar.symbol):
            for ts, rate in events:
                self.shadow.apply_funding(bar.symbol, rate, bar.close, ts)
        if not self.portfolio.positions_on(bar.symbol):
            return
        for ts, rate in events:
            if self.funding_mode == "adverse":
                sides = {p.side for p in self.portfolio.positions_on(bar.symbol)}
                rate = abs(rate) if sides == {"long"} else -abs(rate) if sides == {"short"} else rate
            self._fills.extend(self.portfolio.apply_funding(bar.symbol, rate, bar.close, ts))

    # -- steps ----------------------------------------------------------------------------------
    def _roll_day(self, ts: int) -> None:
        day = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat()
        if day != self._day:
            self.risk.roll_day(self.portfolio.total_equity(self.ctx.prices), day)
            self._day = day

    def _push_bars(self, bar: Candle, tfs: Sequence[str], agg: dict) -> None:
        for tf in tfs:
            if tf == bar.tf:
                self.ctx.push(bar)
                continue
            rolled = self._aggregate(bar, tf, agg)
            if rolled is not None:
                self.ctx.push(rolled)

    @staticmethod
    def _aggregate(bar: Candle, tf: str, agg: dict) -> Candle | None:
        """Fold 1m bars into a closed `tf` bar, emitted only on the bar that completes the window."""
        step = tf_ms(tf) // 60_000
        if step <= 1:
            return None
        buf = agg.setdefault((bar.symbol, tf), [])
        buf.append(bar)
        if (bar.open_time // 60_000) % step != step - 1:
            return None
        if len(buf) != step or buf[0].open_time + (step - 1) * 60_000 != bar.open_time:
            buf.clear()          # a gap in the tape: drop the partial window rather than fake a bar
            return None
        out = Candle(bar.symbol, tf, buf[0].open_time, buf[0].open, max(b.high for b in buf),
                     min(b.low for b in buf), bar.close, sum(b.volume for b in buf), bar.close_time,
                     True, sum(b.quote_volume for b in buf), sum(b.trades for b in buf), "backfill",
                     sum(b.taker_buy_volume for b in buf))
        buf.clear()
        return out

    def _walk(self, bar: Candle) -> None:
        """Feed the intrabar path to the live ExitEngine, position by position."""
        for pos in self.portfolio.positions_on(bar.symbol):
            for price, ts in self._path(bar, pos.side):
                if pos.qty <= 0:
                    break
                self._now = ts
                self.ctx.set_price(bar.symbol, price, ts)
                for intent in self.exits.on_price(pos, price, None, ts):
                    if pos.qty <= 0:
                        break
                    self._fills.append(self._close(pos, intent.position_id, intent.fraction,
                                                   intent.ref_price, intent.kind, intent.reason,
                                                   bar, ts))
        self._now = bar.close_time

    def _manage(self, strat: Any, closed: Candle, bar: Candle) -> None:
        for pos in self.portfolio.positions_on(bar.symbol):
            if pos.tf != closed.tf:
                continue
            upd = strat.manage(pos, closed, self.ctx)
            if upd is None:
                continue
            for intent in self.exits.apply_update(pos, upd, bar.close):
                self._fills.append(self._close(pos, intent.position_id, intent.fraction,
                                               intent.ref_price, intent.kind, intent.reason,
                                               bar, bar.close_time))

    def _signals(self, strat: Any, closed: Candle, meta: StrategyMeta, res: ReplayResult,
                 cooldown_ms: int) -> None:
        for sig in strat.on_candle(closed, self.ctx):
            only = getattr(self, "_only_symbol", None)
            if only and sig.symbol != only:
                # A single-market bot must not trade another coin, even if it asks to.
                res.rejects["foreign_symbol"] = res.rejects.get("foreign_symbol", 0) + 1
                continue
            if sig.kind == "exit":
                self._close_matching(sig, closed.close_time)
                continue
            res.signals += 1
            if self.cost_gate is not None:
                ok, cinfo = self.cost_gate(sig, self._cost_context(sig, closed))
                sig.meta["edge_to_cost"] = cinfo.get("edge_to_cost")
                if not ok:
                    res.rejects["cost_gate"] = res.rejects.get("cost_gate", 0) + 1
                    self._shadow_cost_reject(sig, closed, meta, res, cinfo)
                    continue
            info: dict[str, Any] = {}
            saved_mult = meta.size_mult
            mult = 1.0
            if self.sizing is not None:
                mult, info = self.sizing(sig, self._health(meta.id))
                if mult <= 0:
                    why = str((info or {}).get("reject_reason") or "bot_halted")
                    res.rejects[why] = res.rejects.get(why, 0) + 1
                    continue
                meta.size_mult = saved_mult * mult
            try:
                decision = self.risk.approve(sig, meta, self.ctx.prices, venue_kind=self.venue,
                                             dry_run=True)
            finally:
                meta.size_mult = saved_mult
            if not decision.approved:
                key = decision.reason.split(":")[0]
                res.rejects[key] = res.rejects.get(key, 0) + 1
                continue
            if self.max_risk_pct is not None and not self._cap_risk(decision, sig, meta):
                res.rejects["risk_above_hard_max"] = res.rejects.get("risk_above_hard_max", 0) + 1
                continue
            if self.leverage_policy == "needed":
                self._fit_leverage(decision, meta)
            if self.gate is not None:
                gated = self._apply_gate(sig, closed, meta, decision, info, res,
                                         saved_mult * (mult if self.sizing is not None else 1.0))
                if gated is None:
                    continue
                decision, info = gated
            # Do NOT fill here. The order is queued and executes against the market state that
            # exists after signal + order latency has elapsed, which is the next bar.
            order = Order(symbol=sig.symbol, side="BUY" if sig.side == "long" else "SELL",
                          qty=decision.qty, order_type="MARKET",
                          decision_price=self.ctx.prices[sig.symbol], signal_ts=closed.close_time,
                          execute_at=closed.close_time + self.latency_ms)
            order.submit(closed.close_time)
            if info:
                eq = self.portfolio.wallet_equity(meta.id, self.ctx.prices)
                order.meta["sizing"] = {**info, "risk_usd": decision.risk_usd,
                                        "risk_pct": decision.risk_usd / eq if eq > 0 else None,
                                        "position_leverage": decision.leverage}
            self._pending.append((order, sig, decision))
            if cooldown_ms:
                meta.cooldown_until[sig.symbol] = closed.close_time + cooldown_ms

    def _close(self, pos, position_id: str, fraction: float, ref_price: float, kind: str,
               reason: str, bar: Candle, ts: int) -> Fill:
        """Route an exit through the execution model: exits pay spread and impact too."""
        side = "SELL" if pos.side == "long" else "BUY"
        qty = pos.qty * min(1.0, max(0.0, fraction))
        order = Order(symbol=pos.symbol, side=side, qty=qty, order_type="MARKET",
                      decision_price=ref_price, signal_ts=ts, execute_at=ts)
        order.submit(ts)
        result = self.execution.execute(
            order, self._market_state(bar, ref_price, ts, pos.tf or "1m", complete=True))
        price = result.avg_price if result.fills else None
        if result.partial and qty > 0:
            # Only what filled is closed; the MARKET remainder expired and the position stays
            # open for the exit engine to try again on the next price it sees.
            fraction = min(fraction, fraction * result.filled_qty / qty)
        return self.portfolio.close_position(
            position_id, fraction, ref_price, kind, True, reason, ts=ts, fill_price=price,
            maker=(result.role == "MAKER"),
            fill_meta={"execution_level": result.execution_level_used,
                       "liquidity_role": result.role or "TAKER",
                       "latency_cost": result.latency_cost, "spread_cost": result.spread_cost,
                       "impact_cost": result.impact_cost})

    # -- gate (Jev) and shadow book ------------------------------------------------------------------
    def _gate_context(self, sig: Signal, closed: Candle, meta: StrategyMeta, decision: Any,
                      info: dict[str, Any]) -> dict[str, Any]:
        """Everything a gate may know, all of it as of the signal bar's close."""
        ts = closed.close_time
        price = self.ctx.prices.get(sig.symbol) or sig.entry_price
        eq = self.portfolio.wallet_equity(meta.id, self.ctx.prices)
        tf = sig.tf or closed.tf
        candles = [c for c in self.ctx.candles(sig.symbol, tf) if c.close_time <= ts]
        funding = self.funding.events(sig.symbol, ts - 3 * 8 * 3600 * 1000 - 1, ts) if self.funding else []
        rules = self.rules.get(sig.symbol)
        state = MarketState(symbol=sig.symbol, ts=ts, last=price,
                            atr=self.ctx.ind_last(sig.symbol, tf, "atr", n=14),
                            tick=rules.tick if rules else None)
        half = self.execution._half_spread_bps(state)
        return {"ts": ts, "candles": candles, "funding": funding, "health": self._health(meta.id),
                "equity": eq, "start_equity": float(self.settings.strategy_starting_balance),
                "available": self.portfolio.available(meta.id, self.ctx.prices),
                "open_positions": len(self.portfolio.positions_of(meta.id)),
                "leverage_ceiling": int(meta.leverage), "decision": decision, "sizing": dict(info),
                "price": price, "half_spread_bps": half,
                "expected_slippage_bps": half * self.execution.config.slippage_mult,
                "taker_fee": float(self.settings.taker_fee), "maker_fee": float(self.settings.maker_fee),
                # read-only extras for gates that build a multi-timeframe state (Jev V2): the context
                # (a gate must itself keep to bars closed by `ts`), the bot's own closed trades, rules
                "ctx": self.ctx, "rules": rules,
                "trades": [t for t in self.portfolio.closed_trades if t.strategy_id == meta.id][-40:]}

    def _apply_gate(self, sig: Signal, closed: Candle, meta: StrategyMeta, decision: Any,
                    info: dict[str, Any], res: ReplayResult, base_mult: float):
        """Ask the gate, then re-size through the RiskManager. Returns (decision, info) to trade,
        or None when the candidate is not traded (and has been sent to the shadow book)."""
        verdict = self.gate(sig, self._gate_context(sig, closed, meta, decision, info))
        vinfo = dict(getattr(verdict, "info", {}) or {})
        did = vinfo.get("decision_id") or ""
        m = float(getattr(verdict, "multiplier", 0.0))
        if m <= 0:
            key = "jev_error" if getattr(verdict, "error", False) else "jev_skip"
            res.rejects[key] = res.rejects.get(key, 0) + 1
            res.gate_events.append({"decision_id": did, "result": "SKIPPED", "ts": closed.close_time})
            self._shadow_queue(sig, decision, closed, did)
            return None
        if abs(m - 1.0) > 1e-9:
            saved = meta.size_mult
            meta.size_mult = base_mult * m
            try:
                resized = self.risk.approve(sig, meta, self.ctx.prices, venue_kind=self.venue,
                                            dry_run=True)
            finally:
                meta.size_mult = saved
            ok = resized.approved and (self.max_risk_pct is None or self._cap_risk(resized, sig, meta))
            if not ok:
                why = resized.reason.split(":")[0] if not resized.approved else "risk_above_hard_max"
                res.rejects["jev_resize_" + why] = res.rejects.get("jev_resize_" + why, 0) + 1
                res.gate_events.append({"decision_id": did, "result": "RISK_REJECTED:" + why,
                                        "ts": closed.close_time})
                self._shadow_queue(sig, decision, closed, did)
                return None
            if self.leverage_policy == "needed":
                self._fit_leverage(resized, meta)
            decision = resized
        res.gate_events.append({"decision_id": did, "result": "TRADED", "ts": closed.close_time})
        return decision, {**info, **vinfo}

    def _shadow_queue(self, sig: Signal, decision: Any, closed: Candle, decision_id: str) -> None:
        if self.shadow is None:
            return
        order = Order(symbol=sig.symbol, side="BUY" if sig.side == "long" else "SELL",
                      qty=decision.qty, order_type="MARKET",
                      decision_price=self.ctx.prices[sig.symbol], signal_ts=closed.close_time,
                      execute_at=closed.close_time + self.latency_ms)
        order.submit(closed.close_time)
        self.shadow.ensure_wallet(sig.strategy_id, 1e12)
        self._shadow_pending.append((order, sig, decision, decision_id))

    def _shadow_execute(self, bar: Candle) -> None:
        if not self._shadow_pending:
            return
        still = []
        for order, sig, decision, did in self._shadow_pending:
            if order.symbol != bar.symbol or bar.close_time < order.execute_at:
                still.append((order, sig, decision, did))
                continue
            ts = max(bar.open_time, order.execute_at)
            result = self.execution.execute(order, self._market_state(bar, bar.open, ts, sig.tf or "1m"))
            if not result.fills or result.rejected:
                continue
            pos, _ = self.shadow.open_position(sig, decision, result.decision_price, True, ts=ts,
                                               fill_price=result.avg_price,
                                               maker=(result.role == "MAKER"),
                                               qty=result.filled_qty)
            self._res.shadow_links[pos.id] = did
        self._shadow_pending = still

    def _shadow_walk(self, bar: Candle) -> None:
        if self.shadow is None:
            return
        for pos in self.shadow.positions_on(bar.symbol):
            for price, ts in self._path(bar, pos.side):
                if pos.qty <= 0:
                    break
                for intent in self.shadow_exits.on_price(pos, price, None, ts):
                    if pos.qty <= 0:
                        break
                    self._shadow_close(pos, intent.position_id, intent.fraction, intent.ref_price,
                                       intent.kind, intent.reason, bar, ts)

    def _shadow_close(self, pos, position_id: str, fraction: float, ref_price: float, kind: str,
                      reason: str, bar: Candle, ts: int) -> None:
        side = "SELL" if pos.side == "long" else "BUY"
        qty = pos.qty * min(1.0, max(0.0, fraction))
        order = Order(symbol=pos.symbol, side=side, qty=qty, order_type="MARKET",
                      decision_price=ref_price, signal_ts=ts, execute_at=ts)
        order.submit(ts)
        result = self.execution.execute(
            order, self._market_state(bar, ref_price, ts, pos.tf or "1m", complete=True))
        price = result.avg_price if result.fills else None
        self.shadow.close_position(position_id, fraction, ref_price, kind, True, reason, ts=ts,
                                   fill_price=price, maker=(result.role == "MAKER"))

    def _shadow_flatten(self) -> None:
        if self.shadow is None:
            return
        bar = getattr(self, "_bar", None)
        for pos in list(self.shadow.positions.values()):
            price = self.ctx.prices.get(pos.symbol)
            if price and bar is not None:
                self._shadow_close(pos, pos.id, 1.0, price, "time", "end of replay", bar, self._now)
        self._shadow_pending = []

    def _cost_context(self, sig: Signal, closed: Candle) -> dict[str, Any]:
        price = self.ctx.prices.get(sig.symbol) or sig.entry_price
        tf = sig.tf or closed.tf
        rules = self.rules.get(sig.symbol)
        atr = self.ctx.ind_last(sig.symbol, tf, "atr", n=14)
        state = MarketState(symbol=sig.symbol, ts=closed.close_time, last=price, atr=atr,
                            tick=rules.tick if rules else None)
        return {"price": price, "atr": atr, "taker_fee": float(self.settings.taker_fee),
                "half_spread_bps": self.execution._half_spread_bps(state)}

    def _shadow_cost_reject(self, sig: Signal, closed: Candle, meta: StrategyMeta, res: ReplayResult,
                            cinfo: dict[str, Any]) -> None:
        """Record a cost-gate rejection and, when shadowing, what it would have done at normal size."""
        sid = f"cg-{closed.close_time}-{len(res.cost_gate_events)}"
        res.cost_gate_events.append({"id": sid, "ts": closed.close_time, "side": sig.side, **cinfo})
        if self.shadow is None:
            return
        d = self.risk.approve(sig, meta, self.ctx.prices, venue_kind=self.venue, dry_run=True)
        if d.approved:
            self._shadow_queue(sig, d, closed, sid)

    def _health(self, sid: str) -> dict[str, Any]:
        """What a sizing policy may know about the bot: its own closed trades and drawdown."""
        eq = self.portfolio.wallet_equity(sid, self.ctx.prices)
        peak = max(getattr(self, "_peak", eq), eq)
        rs = [t.r_multiple for t in self.portfolio.closed_trades if t.strategy_id == sid]
        return {"equity": eq, "peak": peak, "drawdown": (1.0 - eq / peak) if peak > 0 else 0.0,
                "r": rs, "trades": len(rs)}

    def _cap_risk(self, decision: Any, sig: Signal, meta: StrategyMeta) -> bool:
        """Enforce the hard per-trade risk ceiling. Shrinks the order to fit, or refuses it when
        the shrunk order would no longer be a legal exchange order. Never enlarges anything."""
        eq = self.portfolio.wallet_equity(meta.id, self.ctx.prices)
        cap = float(self.max_risk_pct) * eq
        if decision.risk_usd <= cap + 1e-9:
            return True
        dist = abs(sig.entry_price - sig.stop)
        rules = self.rules.get(sig.symbol)
        price = self.ctx.prices.get(sig.symbol) or sig.entry_price
        if dist <= 0 or rules is None or price <= 0:
            return False
        qty = rules.round_qty_down(cap / dist) if rules.step else cap / dist
        safety = max(1.0, float(getattr(self.settings, "min_notional_safety_multiplier", 1.0) or 1.0))
        if qty < rules.min_qty - 1e-12 or qty * price < rules.min_notional * safety - 1e-9:
            return False
        decision.qty = qty
        decision.notional = qty * price
        decision.margin = decision.notional / max(1, int(decision.leverage))
        decision.risk_usd = qty * dist
        return True

    def _fit_leverage(self, decision: Any, meta: StrategyMeta) -> None:
        """Smallest integer leverage whose margin fits this position's share of the free margin,
        capped by the bot's ceiling. The share is the free margin divided by the position slots the
        strategy still has, so a multi-leg strategy's first leg cannot take all of it. The
        RiskManager approved the size at the ceiling, so fitting can only LOWER the leverage."""
        available = self.portfolio.available(meta.id, self.ctx.prices)
        ceiling = max(1, int(meta.leverage))
        if available <= 0 or decision.notional <= 0:
            return
        slots = max(1, int(meta.max_positions) - len(self.portfolio.positions_of(meta.id)))
        budget = available / slots
        lev = min(ceiling, max(1, math.ceil(decision.notional / budget - 1e-9)))
        decision.leverage = lev
        decision.margin = decision.notional / lev

    def _close_matching(self, sig: Signal, ts: int) -> None:
        price = self.ctx.prices.get(sig.symbol)
        bar = getattr(self, "_bar", None)
        if price is None or bar is None:
            return
        for pos in self.portfolio.positions_of(sig.strategy_id):
            if pos.symbol == sig.symbol and (sig.leg is None or pos.leg == sig.leg):
                self._fills.append(self._close(pos, pos.id, 1.0, price, "manual",
                                               sig.reason or "strategy exit", bar, ts))

    def _flatten(self, sid: str) -> None:
        """Close anything still open at the end of the tape so it shows up in the results."""
        bar = getattr(self, "_bar", None)
        for pos in list(self.portfolio.positions_of(sid)):
            price = self.ctx.prices.get(pos.symbol)
            if price and bar is not None:
                self._fills.append(self._close(pos, pos.id, 1.0, price, "time", "end of replay",
                                               bar, self._now))
