"""RiskManager: every entry passes approve(); halts, kill switch and caps live here.

Sizing (per approved entry):
    risk_usd = risk_per_trade_pct * wallet_equity * strategy.size_mult * signal.size_mult
    qty      = risk_usd / |entry - stop|             (grid legs: meta["margin_usd"] or meta["margin_pct"] x equity,
                                                     times lev / price)
    qty     <= wallet_equity * lev_virtual / price   (virtual margin cap), rounded down to the lot step
    qty      >= minQty; notional >= minNotional x min_notional_safety_multiplier
                                                    (Binance checks MIN_NOTIONAL on the submitted order and
                                                     exempts reduce-only exits; the multiplier defaults to 1.0
                                                     and anything above is a PaperLab preference)
    2 x taker_fee x notional <= max_fee_share_of_r x risk_usd   (the round trip must not eat the edge)
    margin   = notional / lev_virtual <= available
Caps: gross virtual notional <= 8 x paper equity; per-symbol NET notional <= 4 x paper equity;
      (non-DRY) extra exchange margin for the delta <= 80% of the exchange's available balance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.portfolio import Portfolio
from app.core.types import Check, ExchangePosition, MarketRules, RiskDecision, Signal


# short, stable codes for the GUI: the Strategies row shows WHY a signal was refused
REJECT_CODES: dict[str, str] = {
    "below_min_notional": "min_notional", "below_min_qty": "min_qty", "max_positions": "max_positions", "already_in_symbol": "max_positions",
    "gross_notional_cap": "cap_gross", "net_notional_cap": "cap_net", "exchange_margin_cap": "cap_margin",
    "insufficient_virtual_margin": "margin", "rr_below_min": "rr", "no_take_profit": "rr",
    "stop_too_tight": "stop_bps", "stop_wrong_side": "stop_bps", "cooldown": "cooldown",
    "symbol_cooldown": "cooldown", "venue": "venue", "venue_unsupported": "venue", "wallet_empty": "wallet",
    "no_wallet": "wallet", "strategy_disabled": "off", "strategy_halted": "halted", "killed": "killed",
    "daily_halt": "daily_halt", "margin_halted": "margin_halt", "engine_not_running": "paused",
    "no_price": "no_price", "not_an_entry": "exit", "fee_gt_r": "fee_gt_r",
}


def reject_code(reason: str) -> str:
    """Collapse a full rejection reason ('below_min_notional:78.00<100.00') to a short code."""
    head = (reason or "").split(":")[0].strip()
    return REJECT_CODES.get(head, head or "unknown")


@dataclass(slots=True)
class StrategyMeta:
    id: str
    enabled: bool
    size_mult: float
    leverage: int
    max_positions: int
    min_rr: float
    supported: bool
    halted: bool = False
    cooldown_until: dict[str, int] = field(default_factory=dict)


@dataclass
class RiskState:
    killed: bool = False
    daily_halted: bool = False
    engine_running: bool = False
    start_of_day_date: str = ""
    start_of_day_equity: float = 0.0
    halted_strategies: set[str] = field(default_factory=set)
    margin_halted: set[str] = field(default_factory=set)
    symbol_cooldown_until: dict[str, int] = field(default_factory=dict)
    halt_floors: dict[str, float] = field(default_factory=dict)
    last_daily_halt_ts: int = 0


class RiskManager:
    def __init__(self, settings: Any, portfolio: Portfolio, rules: dict[str, MarketRules], clock: Callable[[], int]):
        self.settings = settings
        self.portfolio = portfolio
        self.rules = rules
        self.clock = clock
        self.state = RiskState()

    # -- approval -------------------------------------------------------------------------
    def approve(self, sig: Signal, meta: StrategyMeta, prices: dict[str, float], venue_kind: str = "futures",
                exchange_available: float | None = None, exchange_leverage: int = 15,
                dry_run: bool = True) -> RiskDecision:
        checks: list[Check] = []
        s = self.settings
        st = self.state
        now = self.clock()

        def reject(reason: str) -> RiskDecision:
            checks.append(Check(reason, False))
            return RiskDecision(False, reason, checks=checks)

        if not st.engine_running:
            return reject("engine_not_running")
        if st.killed:
            return reject("killed")
        if st.daily_halted:
            return reject("daily_halt")
        if not meta.enabled:
            return reject("strategy_disabled")
        if meta.halted or meta.id in st.halted_strategies:
            return reject("strategy_halted")
        if not meta.supported:
            return reject("venue_unsupported")
        if sig.kind != "entry":
            return reject("not_an_entry")
        if sig.side == "short" and venue_kind == "spot":
            return reject("venue: no shorts")
        checks.append(Check("venue", True))
        lev = max(1, int(meta.leverage))
        if venue_kind == "spot" and lev != 1:
            checks.append(Check("leverage_clamped", True, f"spot venue: leverage {lev} -> 1"))
            lev = 1
        price = prices.get(sig.symbol) or sig.entry_price
        if price <= 0:
            return reject("no_price")
        if not sig.stop_on_correct_side():
            return reject("stop_wrong_side")
        stop_dist = abs(sig.entry_price - sig.stop)
        if stop_dist / sig.entry_price < s.min_stop_bps / 1e4:
            return reject(f"stop_too_tight:{stop_dist / sig.entry_price * 1e4:.1f}bps<{s.min_stop_bps:.0f}")
        checks.append(Check("stop_sane", True, f"{stop_dist / sig.entry_price * 1e4:.1f} bps"))
        rr = sig.rr()
        if meta.min_rr > 0:
            if rr is None:
                return reject("no_take_profit")
            if rr < meta.min_rr - 1e-9:
                return reject(f"rr_below_min:{rr:.2f}<{meta.min_rr}")
        checks.append(Check("rr", True, "" if rr is None else f"{rr:.2f}"))
        open_positions = self.portfolio.positions_of(meta.id)
        if len(open_positions) >= max(1, meta.max_positions):
            return reject("max_positions")
        if any(p.symbol == sig.symbol and p.leg == sig.leg for p in open_positions):
            return reject("already_in_symbol")
        cd = meta.cooldown_until.get(sig.symbol, 0)
        if cd and now < cd:
            return reject("cooldown")
        scd = st.symbol_cooldown_until.get(sig.symbol, 0)
        if scd and now < scd:
            return reject("symbol_cooldown")
        if sig.symbol in st.margin_halted:
            return reject("margin_halted")
        checks.append(Check("slots", True))

        wallet = self.portfolio.wallets.get(meta.id)
        if wallet is None:
            return reject("no_wallet")
        equity = self.portfolio.wallet_equity(meta.id, prices)
        if equity <= 0:
            return reject("wallet_empty")
        rules = self.rules.get(sig.symbol) or MarketRules(sig.symbol, 0, 0, 0, 0)
        margin_hint = sig.meta.get("margin_usd")
        if not margin_hint and sig.meta.get("margin_pct"):
            margin_hint = float(sig.meta["margin_pct"]) * equity  # grid legs: fraction of wallet equity as margin
        if margin_hint:
            qty = float(margin_hint) * lev / price
            risk_usd = qty * stop_dist
        else:
            risk_usd = s.risk_per_trade_pct * equity * meta.size_mult * sig.size_mult
            qty = risk_usd / stop_dist
        cap_qty = equity * lev / price
        if qty > cap_qty:
            checks.append(Check("qty_capped_by_leverage", True, f"{qty:.6g} -> {cap_qty:.6g}"))
            qty = cap_qty
        qty = rules.round_qty_down(qty) if rules.step else qty
        notional = qty * price
        # Binance's MIN_NOTIONAL is a check on the SUBMITTED order. It does not apply to individual
        # fills, and reduce-only exits are exempt, so a later 50% partial exit needs no headroom
        # here. It used to be `2 x minNotional`; that was PaperLab's own caution presented as an
        # exchange rule, and it is now the explicit, default-off `min_notional_safety_multiplier`.
        safety = max(1.0, float(getattr(s, "min_notional_safety_multiplier", 1.0) or 1.0))
        min_needed = rules.min_notional * safety
        if qty <= 0 or qty < rules.min_qty - 1e-12:
            return reject(f"below_min_qty:{qty:.8g}<{rules.min_qty:.8g}")
        if notional < min_needed - 1e-9:
            return reject(f"below_min_notional:{notional:.2f}<{min_needed:.2f}")
        # Fee pre-check: refuse a trade whose round trip eats more than `max_fee_share_of_r` of the risk.
        # This is NOT a size increase - it skips scratches that cannot pay their own costs. With the default
        # 0.04% taker and a 25% ceiling it refuses stops tighter than ~32 bps of price.
        round_trip_fee = 2.0 * s.taker_fee * notional
        fee_budget = s.max_fee_share_of_r * risk_usd
        if risk_usd > 0 and round_trip_fee > fee_budget:
            return reject(f"fee_gt_r:{round_trip_fee:.2f}>{fee_budget:.2f}")
        checks.append(Check("fee_vs_r", True,
                            f"{(round_trip_fee / risk_usd * 100) if risk_usd else 0:.0f}% of R"))
        margin = notional / lev
        available = self.portfolio.available(meta.id, prices)
        if margin > available + 1e-9:
            return reject(f"insufficient_virtual_margin:{margin:.2f}>{available:.2f}")
        checks.append(Check("sizing", True, f"qty={qty:.6g} notional={notional:.2f} margin={margin:.2f}"))

        total_equity = self.portfolio.total_equity(prices)
        gross = self.portfolio.gross_notional(prices)
        gross_cap = s.max_total_notional_mult * total_equity
        if gross + notional > gross_cap + 1e-9:
            return reject(f"gross_notional_cap:{gross + notional:.0f}>{gross_cap:.0f}")
        signed = qty if sig.side == "long" else -qty
        net_before = self.portfolio.net_qty(sig.symbol)
        net_after = net_before + signed
        net_cap = s.max_net_notional_mult * total_equity
        if abs(net_after) * price > net_cap + 1e-9:
            return reject(f"net_notional_cap:{abs(net_after) * price:.0f}>{net_cap:.0f}")
        checks.append(Check("caps", True, f"gross={gross + notional:.0f}/{gross_cap:.0f} net={abs(net_after) * price:.0f}/{net_cap:.0f}"))
        if not dry_run and exchange_available is not None and venue_kind == "futures":
            lev_ex = max(1, exchange_leverage)
            delta_margin = max(0.0, (abs(net_after) - abs(net_before)) * price / lev_ex)
            budget = s.exchange_margin_use_max * exchange_available
            if delta_margin > budget + 1e-9:
                return reject(f"exchange_margin_cap:{delta_margin:.2f}>{budget:.2f}")
            checks.append(Check("exchange_margin", True, f"{delta_margin:.2f}<={budget:.2f}"))
        return RiskDecision(True, "ok", qty=qty, notional=notional, margin=margin, leverage=lev,
                            risk_usd=qty * stop_dist, rr=rr, checks=checks)

    # -- halts ---------------------------------------------------------------------------
    def set_halt_floor(self, strategy_id: str, allocation_or_equity: float) -> float:
        floor = (1.0 - self.settings.strategy_halt_pct) * allocation_or_equity
        self.state.halt_floors[strategy_id] = floor
        return floor

    def check_daily_halt(self, total_equity: float) -> bool:
        st = self.state
        if st.daily_halted or st.start_of_day_equity <= 0:
            return False
        if total_equity <= (1.0 - self.settings.daily_halt_pct) * st.start_of_day_equity:
            st.daily_halted = True
            st.last_daily_halt_ts = self.clock()
            return True
        return False

    def check_strategy_halt(self, strategy_id: str, equity: float) -> bool:
        st = self.state
        if strategy_id in st.halted_strategies:
            return False
        floor = st.halt_floors.get(strategy_id)
        if floor is not None and floor > 0 and equity <= floor:
            st.halted_strategies.add(strategy_id)
            return True
        return False

    def check_exchange_margin(self, positions: dict[str, ExchangePosition], mmr_by_symbol: dict[str, float]) -> list[str]:
        newly: list[str] = []
        for sym, pos in positions.items():
            if pos.qty == 0 or sym in self.state.margin_halted:
                continue
            dist = pos.liq_distance_pct()
            if dist is None:
                continue
            threshold = max(2.0 * mmr_by_symbol.get(sym, 0.025), 0.005)
            if dist <= threshold:
                self.state.margin_halted.add(sym)
                newly.append(sym)
        return newly

    def roll_day(self, total_equity: float, today: str) -> bool:
        st = self.state
        if st.start_of_day_date == today:
            return False
        st.start_of_day_date = today
        st.start_of_day_equity = total_equity
        return True

    def clear_daily_halt(self, total_equity: float) -> None:
        self.state.daily_halted = False
        self.state.start_of_day_equity = total_equity

    def clear_strategy_halt(self, strategy_id: str, equity: float) -> float:
        self.state.halted_strategies.discard(strategy_id)
        return self.set_halt_floor(strategy_id, equity)

    def clear_margin_halt(self, symbol: str) -> None:
        self.state.margin_halted.discard(symbol)

    def kill(self) -> None:
        self.state.killed = True

    def unkill(self) -> None:
        self.state.killed = False

    def set_symbol_cooldown(self, symbol: str, ms: int) -> None:
        self.state.symbol_cooldown_until[symbol] = self.clock() + ms

    def daily_pnl_pct(self, total_equity: float) -> float | None:
        sod = self.state.start_of_day_equity
        return None if sod <= 0 else (total_equity - sod) / sod * 100.0

    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        st = self.state
        total = self.portfolio.total_equity(prices)
        return {
            "killed": st.killed, "daily_halted": st.daily_halted, "engine_running": st.engine_running,
            "start_of_day_date": st.start_of_day_date, "start_of_day_equity": st.start_of_day_equity,
            "daily_pnl_pct": self.daily_pnl_pct(total), "halt_at_pct": -self.settings.daily_halt_pct * 100,
            "gross_notional": self.portfolio.gross_notional(prices),
            "gross_cap": self.settings.max_total_notional_mult * total,
            "net_notional": {sym: self.portfolio.net_notional(sym, prices.get(sym, 0.0)) for sym in
                             {p.symbol for p in self.portfolio.positions.values()}},
            "net_cap": self.settings.max_net_notional_mult * total,
            "margin_halted": sorted(st.margin_halted), "halted_strategies": sorted(st.halted_strategies),
            "symbol_cooldowns": {k: v for k, v in st.symbol_cooldown_until.items() if v > self.clock()},
        }
