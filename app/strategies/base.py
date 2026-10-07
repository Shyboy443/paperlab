"""Strategy ABC, GUI-editable params, documentation block and the MarketContext protocol.

Every strategy module (s01 ... s20) defines:
    @dataclass class Params:  fields declared with P(default, min=, max=, step=) so the GUI can render sliders
    class <Name>(Strategy):   ClassVars id/name/doc/timeframes/..., hooks on_candle / on_book / on_funding /
                              on_tick / manage / state.
Strategies never do I/O and never import anything outside app.core.types, app.core.indicators, this module and
the standard library. They receive everything through `ctx: MarketContext`.
"""
from __future__ import annotations

import dataclasses
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, Sequence

from app.core.types import (BookSnapshot, Candle, ExitUpdate, FundingInfo, MarketRules, Signal, TakeProfit,
                            Tick, TrailSpec, VirtualPosition)


# ---- params ---------------------------------------------------------------------

def P(default: Any, *, min: Any = None, max: Any = None, step: Any = None, label: str | None = None,
      help: str = "") -> Any:
    """Declare a GUI-editable parameter on a Params dataclass."""
    return field(default=default, metadata={"min": min, "max": max, "step": step, "label": label, "help": help})


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    type: str  # int | float | bool
    default: Any
    min: Any
    max: Any
    step: Any
    help: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _ptype(f: dataclasses.Field) -> str:
    t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
    if t in ("bool",):
        return "bool"
    if t in ("int",):
        return "int"
    return "float"


def param_specs(params_cls: type) -> list[ParamSpec]:
    specs: list[ParamSpec] = []
    for f in dataclasses.fields(params_cls):
        md = f.metadata or {}
        specs.append(ParamSpec(
            name=f.name, label=md.get("label") or f.name.replace("_", " "), type=_ptype(f),
            default=f.default, min=md.get("min"), max=md.get("max"), step=md.get("step"), help=md.get("help", "")))
    return specs


def params_to_dict(params: Any) -> dict[str, Any]:
    return dataclasses.asdict(params)


def apply_params(params_cls: type, current: Any, updates: dict[str, Any]) -> tuple[Any, list[str]]:
    """Return (new_params, warnings). Unknown names are ignored with a warning; values are clamped and coerced."""
    warnings: list[str] = []
    values = dataclasses.asdict(current) if current is not None else {}
    specs = {s.name: s for s in param_specs(params_cls)}
    for name, raw in updates.items():
        spec = specs.get(name)
        if spec is None:
            warnings.append(f"unknown param {name!r} ignored")
            continue
        try:
            if spec.type == "bool":
                val: Any = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
            elif spec.type == "int":
                val = int(round(float(raw)))
            else:
                val = float(raw)
        except (TypeError, ValueError):
            warnings.append(f"{name}: cannot coerce {raw!r}, kept {values.get(name)!r}")
            continue
        if spec.type != "bool":
            if spec.min is not None and val < spec.min:
                warnings.append(f"{name}: {val} clamped to min {spec.min}")
                val = spec.min
            if spec.max is not None and val > spec.max:
                warnings.append(f"{name}: {val} clamped to max {spec.max}")
                val = spec.max
        values[name] = val
    return params_cls(**values), warnings


# ---- documentation ------------------------------------------------------------

@dataclass(frozen=True)
class StrategyDoc:
    idea: str
    timeframe: str
    symbols: str
    entry: str
    stop: str
    targets: str
    sizing: str
    why_aggressive: str

    def to_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


# ---- market context -------------------------------------------------------------

class VoteLike(Protocol):
    strategy_id: str
    symbol: str
    side: str
    ts: int
    stop: float
    tf: str
    signal_id: str
    source: str


class BoardLike(Protocol):
    def votes(self, symbol: str, contributors: Sequence[str], ttl_ms: int, now_ms: int) -> list[Any]: ...
    def last(self, strategy_id: str) -> Any: ...


class MarketContext(Protocol):
    """What a strategy may ask the engine for. Implemented by the engine and by tests (FakeCtx)."""
    symbols: list[str]
    board: BoardLike

    def now_ms(self) -> int: ...
    def venue_kind(self) -> str: ...
    def candles(self, symbol: str, tf: str) -> Sequence[Candle]: ...
    def forming(self, symbol: str, tf: str) -> Candle | None: ...
    def last_price(self, symbol: str) -> float | None: ...
    def mark_price(self, symbol: str) -> float | None: ...
    def book(self, symbol: str) -> BookSnapshot | None: ...
    def funding(self, symbol: str) -> FundingInfo | None: ...
    def ind(self, symbol: str, tf: str, name: str, **params: Any) -> Any: ...
    def ind_last(self, symbol: str, tf: str, name: str, **params: Any) -> float | None: ...
    def positions_of(self, strategy_id: str) -> list[VirtualPosition]: ...
    def rules(self, symbol: str) -> MarketRules: ...
    def recent_trades(self, symbol: str, window_ms: int) -> list[Tick]: ...
    def is_enabled(self, strategy_id: str) -> bool: ...
    def wallet_info(self, strategy_id: str) -> dict[str, float]: ...
    # -> {"allocation", "equity", "margin_used", "available"} for that strategy's virtual wallet


# ---- strategy ABC -------------------------------------------------------------

class Strategy(ABC):
    id: ClassVar[str] = "S00"
    name: ClassVar[str] = "unnamed"
    Params: ClassVar[type] = type("Params", (), {})
    doc: ClassVar[StrategyDoc] = StrategyDoc("", "", "", "", "", "", "", "")
    timeframes: ClassVar[tuple[str, ...]] = ("1m",)
    needs_book: ClassVar[bool] = False
    needs_funding: ClassVar[bool] = False
    needs_ticks: ClassVar[bool] = False
    max_positions: ClassVar[int] = 1
    min_rr: ClassVar[float] = 2.5
    contributes_votes: ClassVar[bool] = False
    supported_venues: ClassVar[frozenset[str]] = frozenset({"futures", "spot"})
    # Signal timeframes this strategy may be entered on as a single-market bot. `timeframes` above
    # is what it SUBSCRIBES to (a dual-timeframe strategy needs both); this is what it may be
    # *triggered* on. Declaring it is a claim that the timeframe is configurable. Empty means the
    # strategy signals on its native timeframe only (read from its `on_candle` gate, or from
    # `native_timeframe` when set) -- see app.competition.bots.native_timeframe.
    supported_timeframes: ClassVar[tuple[str, ...]] = ()
    native_timeframe: ClassVar[str | None] = None
    # Behaviour version. v1 classes are frozen; a changed strategy is a NEW class with version "v2"
    # (app/strategies/v2), so stored experiments stay reproducible.
    version: ClassVar[str] = "v1"
    warmup_bars: ClassVar[int] = 200
    default_leverage: ClassVar[int] = 15
    badges: ClassVar[tuple[str, ...]] = ()

    def __init__(self, params: Any = None):
        self.params = params if params is not None else self.Params()

    # -- subscriptions --------------------------------------------------------
    def subscriptions(self, symbols: Sequence[str]) -> set[tuple[str, str]]:
        return {(s, tf) for s in symbols for tf in self.timeframes}

    def supports(self, venue_kind: str) -> bool:
        return venue_kind in self.supported_venues

    # -- hooks (override what you need) -------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        return []

    def on_book(self, b: BookSnapshot, ctx: MarketContext) -> list[Signal]:
        return []

    def on_funding(self, f: FundingInfo, ctx: MarketContext) -> list[Signal]:
        return []

    def on_tick(self, t: Tick, ctx: MarketContext) -> list[Signal]:
        return []

    def manage(self, pos: VirtualPosition, c: Candle, ctx: MarketContext) -> ExitUpdate | None:
        return None

    def state(self) -> dict[str, Any]:
        return {}

    def reset(self) -> None:
        return None

    def cooldown_ms(self) -> int:
        return 0

    # -- helpers ---------------------------------------------------------------
    def make_entry(self, *, symbol: str, side: str, ts: int, tf: str, price: float, stop: float,
                   tps_r: Sequence[tuple[float, float]] = (), tps_price: Sequence[tuple[float, float]] = (),
                   trail: TrailSpec | None = None, be_at_r: float | None = None, max_hold_s: int | None = None,
                   valid_bars: int = 1, size_mult: float = 1.0, leg: str | None = None, reason: str = "",
                   meta: dict[str, Any] | None = None) -> Signal:
        """Build an entry signal; `tps_r` are (R multiple, fraction) pairs converted to prices from the stop."""
        dist = abs(price - stop)
        sgn = 1 if side == "long" else -1
        tps = [TakeProfit(price + sgn * r * dist, frac) for r, frac in tps_r]
        tps += [TakeProfit(p, frac) for p, frac in tps_price]
        return Signal(strategy_id=self.id, symbol=symbol, kind="entry", side=side, ts=ts, tf=tf,  # type: ignore[arg-type]
                      entry_price=price, stop=stop, take_profits=tps, trail=trail, be_at_r=be_at_r,
                      max_hold_s=max_hold_s, valid_bars=valid_bars, size_mult=size_mult, leg=leg,
                      reason=reason, meta=dict(meta or {}))

    def make_exit(self, *, symbol: str, side: str, ts: int, tf: str, reason: str, leg: str | None = None,
                  price: float = 0.0, meta: dict[str, Any] | None = None) -> Signal:
        return Signal(strategy_id=self.id, symbol=symbol, kind="exit", side=side, ts=ts, tf=tf,  # type: ignore[arg-type]
                      entry_price=price, leg=leg, reason=reason, meta=dict(meta or {}))

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "id": cls.id, "name": cls.name, "doc": cls.doc.to_dict(), "timeframes": list(cls.timeframes),
            "needs_book": cls.needs_book, "needs_funding": cls.needs_funding, "needs_ticks": cls.needs_ticks,
            "max_positions": cls.max_positions, "min_rr": cls.min_rr, "contributes_votes": cls.contributes_votes,
            "supported_venues": sorted(cls.supported_venues), "warmup_bars": cls.warmup_bars,
            "default_leverage": cls.default_leverage, "badges": list(cls.badges),
            "params": [s.to_dict() for s in param_specs(cls.Params)],
        }
