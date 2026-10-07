"""Bot identity for the specialist arena: strategy + one coin + one timeframe + risk profile.

The old competition entered a strategy once and let it trade every symbol. That conflates three
different questions -- does the strategy work, does it work on THIS market, does it work on THIS
timeframe -- and answers none of them cleanly. A bot here trades exactly one symbol on exactly one
signal timeframe, so a result attributes to a specific combination:

    S08-SOLUSDT-5m@20x-v1     is a different competitor from
    S08-BNBUSDT-5m@20x-v1     and from
    S08-v2-SOLUSDT-15m@20x    (a timeframe-configurable version, once one exists)

Each gets its own wallet, ledger, metrics, qualification and version fingerprint. They are never
merged, and a bot may not switch symbol mid-competition because its assigned market is going
against it -- proving the strategy works on the coin it was given is the entire point.

`preflight()` decides whether a candidate can actually compete, and says why when it cannot. A
candidate that fails is NOT_ENTERED and does not count toward the arena's minimum size: a
competition padded with bots that cannot place an order is not a competition.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from app.core.types import MarketRules
from app.execution.config import BINANCE_USDM

# The arena is built around these. 1m stays available for execution simulation (intrabar fills,
# stops, liquidation, latency) but is deliberately not a signal timeframe: a 1m-signal competition
# is dominated by cost, not edge.
ARENA_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m")
ARENA_LEVERAGES: tuple[int, ...] = (5, 10, 20)

RiskProfileName = Literal["AGGRESSIVE", "BALANCED", "CONSERVATIVE"]
AttackState = Literal["ATTACK", "NORMAL", "DEFENSIVE", "HALTED"]
Quality = Literal["ORDINARY", "STRONG", "EXCEPTIONAL"]
QUALITIES: tuple[str, ...] = ("ORDINARY", "STRONG", "EXCEPTIONAL")


@dataclass(frozen=True)
class RiskProfile:
    """How hard a bot is allowed to push. Every number is configurable research input.

    "Aggressive" means participating in the opportunities a strategy finds and sizing up only when
    BOTH the bot's own recent evidence and the signal's grade support it -- never martingale, never
    averaging into a loser, never sizing up after a loss, never around the fee gate. Risk targets
    are ceilings; the actual size still comes from the stop distance through the RiskManager, which
    also enforces minQty / minNotional, the fee gate and margin.

        state       ORDINARY signal   STRONG signal   EXCEPTIONAL signal
        NORMAL      1.00%             1.00%           1.00%
        ATTACK      (not allowed)     1.50%           2.00%   (the hard ceiling)
        DEFENSIVE   0.50%             0.50%           0.50%
        HALTED      0                 0               0

    ATTACK needs strong recent health, a controlled drawdown AND a graded signal. None of the v1
    strategies grade their signals, so in a v1 arena ATTACK cannot occur and no trade risks more
    than 1%. Grading signals is a strategy change and belongs to a new strategy version.
    """
    name: RiskProfileName = "AGGRESSIVE"
    starting_balance: float = 20.0
    max_leverage: int = 20
    ordinary_risk_pct: float = 0.010         # the routine target
    strong_risk_pct: float = 0.015           # ATTACK on a STRONG signal
    exceptional_risk_pct: float = 0.020      # ATTACK on an EXCEPTIONAL signal
    max_risk_pct: float = 0.020              # hard ceiling, never exceeded by anything
    attack_multiplier: float = 1.50
    defensive_multiplier: float = 0.50
    # Bot health, measured on the bot's own closed trades and equity. Nothing here reacts to a
    # single loss: health needs a sample, and drawdown only ever moves size DOWN.
    health_window: int = 20                  # most recent closed trades considered
    attack_min_trades: int = 10              # fewer closed trades than this = unproven
    attack_min_expectancy_r: float = 0.10
    attack_max_drawdown: float = 0.10
    defensive_expectancy_r: float = -0.25
    defensive_drawdown: float = 0.18
    halt_drawdown: float = 0.30
    # ATTACK v2: numeric signal_quality in [0, 1] (v2 strategies) maps to a grade, and ATTACK also
    # needs the cost gate to have passed with room to spare. v1 signals carry no quality and can't.
    strong_quality: float = 0.70
    exceptional_quality: float = 0.85
    attack_min_edge_to_cost: float = 3.0

    def risk_for(self, state: AttackState, quality: Quality = "ORDINARY") -> float:
        """Risk fraction for one signal, after the state machine and the hard ceiling."""
        if state == "HALTED":
            return 0.0
        base = self.ordinary_risk_pct
        if state == "DEFENSIVE":
            risk = base * self.defensive_multiplier
        elif state == "ATTACK" and quality == "EXCEPTIONAL":
            risk = self.exceptional_risk_pct
        elif state == "ATTACK" and quality == "STRONG":
            risk = min(base * self.attack_multiplier, self.strong_risk_pct)
        else:
            risk = base                       # NORMAL, or ATTACK asked for on an ungraded signal
        return min(risk, self.max_risk_pct)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


AGGRESSIVE = RiskProfile()
BALANCED = RiskProfile(name="BALANCED", ordinary_risk_pct=0.005, strong_risk_pct=0.0075,
                       exceptional_risk_pct=0.01, max_risk_pct=0.01, attack_multiplier=1.5)
CONSERVATIVE = RiskProfile(name="CONSERVATIVE", ordinary_risk_pct=0.0025, strong_risk_pct=0.0025,
                           exceptional_risk_pct=0.0025, max_risk_pct=0.0025,
                           attack_multiplier=1.0, max_leverage=5)
PROFILES: dict[str, RiskProfile] = {p.name: p for p in (AGGRESSIVE, BALANCED, CONSERVATIVE)}


def signal_quality(sig: Any) -> Quality:
    """A signal's grade, only as the STRATEGY stated it (`meta["quality"]`).

    It is never inferred here -- not from reward:risk, not from size. A strategy with a fixed 4R
    target would otherwise grade every one of its signals STRONG, which is not a judgement at all.
    """
    q = str((getattr(sig, "meta", None) or {}).get("quality", "")).upper()
    return q if q in QUALITIES else "ORDINARY"  # type: ignore[return-value]


def attack_state(expectancy_r: float | None, drawdown: float | None,
                 profile: RiskProfile = AGGRESSIVE, trades: int | None = None,
                 quality: Quality = "ORDINARY") -> AttackState:
    """Bot health + signal grade -> state. Ordered worst-first: drawdown outranks a good streak.

    What is NOT here: nothing escalates because of a losing streak. Size goes DOWN as drawdown
    deepens or recent expectancy turns negative, and only ever comes back up with evidence.
    """
    dd = abs(drawdown or 0.0)
    exp = expectancy_r or 0.0
    n = profile.attack_min_trades if trades is None else trades
    if dd >= profile.halt_drawdown:
        return "HALTED"
    if dd >= profile.defensive_drawdown:
        return "DEFENSIVE"
    if n >= profile.attack_min_trades and exp <= profile.defensive_expectancy_r:
        return "DEFENSIVE"
    healthy = (n >= profile.attack_min_trades and exp >= profile.attack_min_expectancy_r
               and dd <= profile.attack_max_drawdown)
    if healthy and quality in ("STRONG", "EXCEPTIONAL"):
        return "ATTACK"
    return "NORMAL"


def quality_tier(sig: Any, profile: RiskProfile = AGGRESSIVE) -> Quality:
    """Grade from a numeric `signal_quality` in [0, 1] when the strategy states one, else from a
    stated grade string (`quality`), else ORDINARY. Nothing is inferred."""
    meta = getattr(sig, "meta", None) or {}
    q = meta.get("signal_quality")
    if isinstance(q, (int, float)) and not isinstance(q, bool):
        if q >= profile.exceptional_quality:
            return "EXCEPTIONAL"
        if q >= profile.strong_quality:
            return "STRONG"
        return "ORDINARY"
    return signal_quality(sig)


class AttackPolicy:
    """The per-signal sizing hook ReplayEngine calls before the RiskManager sees a signal.

    Returns a multiplier on the ordinary risk (the replay is configured at `ordinary_risk_pct`) and
    what it decided, which lands on the entry fill so every trade carries its state and risk.
    """

    def __init__(self, profile: RiskProfile = AGGRESSIVE):
        self.profile = profile

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        p = self.profile
        recent = list(health.get("r") or [])[-p.health_window:]
        exp = sum(recent) / len(recent) if recent else 0.0
        quality = quality_tier(sig, p)
        e2c = (getattr(sig, "meta", None) or {}).get("edge_to_cost")
        cost_ok = isinstance(e2c, (int, float)) and e2c >= p.attack_min_edge_to_cost
        if quality != "ORDINARY" and not cost_ok:
            quality = "ORDINARY"              # no ATTACK without a passed cost gate
        dd = float(health.get("drawdown") or 0.0)
        state = attack_state(exp, dd, p, trades=len(recent), quality=quality)
        risk = p.risk_for(state, quality)
        mult = risk / p.ordinary_risk_pct if p.ordinary_risk_pct > 0 else 0.0
        return mult, {"attack_state": state, "quality": quality, "target_risk_pct": risk,
                      "signal_quality": (getattr(sig, "meta", None) or {}).get("signal_quality"),
                      "edge_to_cost": e2c,
                      "recent_expectancy_r": round(exp, 4), "recent_trades": len(recent),
                      "drawdown_at_entry": round(dd, 4)}


@dataclass(frozen=True)
class BotSpec:
    """One competitor. The symbol, timeframe and parameter version are part of its identity."""
    strategy_id: str
    symbol: str
    timeframe: str
    max_leverage: int = 20
    profile: str = "AGGRESSIVE"
    params_version: str = "v1"
    name: str = ""

    @property
    def coin(self) -> str:
        """BTCUSDT -> BTC, for display."""
        return self.symbol[:-4] if self.symbol.endswith("USDT") else self.symbol

    @property
    def key(self) -> str:
        """Human-readable identity, e.g. S08-SOLUSDT-5m@20x-v1."""
        return f"{self.strategy_id}-{self.symbol}-{self.timeframe}@{self.max_leverage}x-{self.params_version}"

    def version(self, params: Any = None) -> str:
        """Fingerprint over everything that can change a result, including the parameters.

        Two bots differing in any of strategy, symbol, timeframe, leverage, profile, parameter
        version or a single parameter value are different competitors and must not share a record.
        """
        values: dict[str, Any] = {}
        if params is not None:
            try:
                values = {f.name: getattr(params, f.name) for f in dataclasses.fields(params)}
            except TypeError:
                values = {}
        blob = json.dumps({"s": self.strategy_id, "sym": self.symbol, "tf": self.timeframe,
                           "lev": self.max_leverage, "profile": self.profile,
                           "pv": self.params_version, "p": values},
                          sort_keys=True, default=str)
        return f"{self.key}:{hashlib.sha256(blob.encode()).hexdigest()[:8]}"

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["key"] = self.key
        d["coin"] = self.coin
        return d


@dataclass
class Preflight:
    """Why a candidate may or may not enter. `ok=False` means NOT_ENTERED with a stated reason."""
    spec: BotSpec
    ok: bool = True
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.spec.key, "ok": self.ok, "reason": self.reason,
                "detail": dict(self.detail), **self.spec.to_dict()}


# ---- timeframes ------------------------------------------------------------------------------

_GATE = re.compile(r"\.tf\s*(?:!=|==)\s*[\"'](\d+[mhd])[\"']")


def _gate_timeframes(cls: Any) -> tuple[str, ...]:
    """Timeframes named in `on_candle`'s own `c.tf != "5m"` style gate, in source order."""
    try:
        src = inspect.getsource(cls.on_candle)
    except (OSError, TypeError):
        return ()
    return tuple(dict.fromkeys(_GATE.findall(src)))


def native_timeframe(cls: Any) -> str | None:
    """The one timeframe a v1 strategy actually emits signals on.

    `timeframes` lists what a strategy SUBSCRIBES to, which includes context series: S06 subscribes
    to 5m and 15m but opens `on_candle` with `if c.tf != "5m": return []`, so it only ever trades
    5m. Entering "S06 on 15m" produces a bot that cannot emit a single signal -- the previous arena
    did exactly that and counted five such bots as active. This reads the gate instead.

    Order of evidence: an explicit `native_timeframe` class attribute, then the `on_candle` gate,
    then a single declared timeframe. Anything ambiguous is None, and None is never entered.
    """
    explicit = getattr(cls, "native_timeframe", None)
    if explicit:
        return str(explicit)
    gates = _gate_timeframes(cls)
    if len(gates) == 1:
        return gates[0]
    declared = tuple(getattr(cls, "timeframes", ()) or ())
    if not gates and len(declared) == 1:
        return declared[0]
    return None


def supported_timeframes(cls: Any) -> tuple[str, ...]:
    """Arena signal timeframes a strategy may genuinely be entered on.

    An explicit `supported_timeframes` class attribute is a claim that the strategy's timeframe is
    configurable, and wins. No v1 strategy makes that claim, so for v1 this is the native timeframe
    when it is an arena timeframe and nothing otherwise. Making the timeframe configurable is a
    strategy change and must arrive as a new strategy version (S08-v2), never by editing S08-v1.
    """
    declared = tuple(getattr(cls, "supported_timeframes", ()) or ())
    if declared:
        return tuple(tf for tf in declared if tf in ARENA_TIMEFRAMES)
    native = native_timeframe(cls)
    return (native,) if native in ARENA_TIMEFRAMES else ()


def timeframe_table(classes: dict[str, Any]) -> list[dict[str, Any]]:
    """native / supported / context timeframes for every strategy, for the report."""
    out = []
    for sid in sorted(classes):
        cls = classes[sid]
        native = "configurable" if hasattr(cls, "for_timeframe") else native_timeframe(cls)
        declared = tuple(getattr(cls, "timeframes", ()) or ())
        out.append({"strategy_id": sid, "name": getattr(cls, "name", sid),
                    "native_timeframe": native, "supported_timeframes": list(supported_timeframes(cls)),
                    "declared_timeframes": list(declared),
                    "context_timeframes": [tf for tf in declared if tf != native],
                    "gate": list(_gate_timeframes(cls))})
    return out


# ---- sizing feasibility --------------------------------------------------------------------------

def sizing_window(rules: MarketRules, profile: RiskProfile, leverage: int, price: float,
                  taker_fee: float = BINANCE_USDM.taker_rate, max_fee_share_of_r: float = 0.25,
                  safety: float = 1.0, risk_pct: float | None = None) -> dict[str, float]:
    """Can risk-based sizing ever produce a legal order for this symbol?

    Two constraints squeeze from opposite directions and can genuinely cross:

        floor    the smallest order Binance accepts at `price`: max(minQty, minNotional / price)
                 rounded UP to the lot step, times price. `safety` > 1 scales the minNotional part
                 and is a PaperLab preference, never an exchange rule (default 1.0).
        ceiling  the RiskManager's fee gate: a trade whose round trip costs more than
                 `max_fee_share_of_r` of its risk is refused, so notional <= share x risk / (2 x taker).

    The same pair read as stop distances: the fee gate needs stop >= 2 x taker / share (0.40% at
    0.05% taker), and reaching the floor needs stop <= risk / floor. Between the two is the band of
    stops a bot can actually trade; when it is empty the bot is arithmetically unable to trade.
    """
    risk_pct = profile.ordinary_risk_pct if risk_pct is None else risk_pct
    balance = profile.starting_balance
    risk_usd = risk_pct * balance
    exchange_min = rules.min_order_notional(price, 1.0)
    floor = rules.min_order_notional(price, safety)
    ceiling_fee = (max_fee_share_of_r * risk_usd / (2.0 * taker_fee)) if taker_fee > 0 else float("inf")
    ceiling_lev = balance * leverage
    ceiling = min(ceiling_fee, ceiling_lev)
    min_stop = (2.0 * taker_fee / max_fee_share_of_r) if max_fee_share_of_r > 0 else 0.0
    max_stop = risk_usd / floor if floor > 0 else float("inf")
    need_risk = (floor * 2.0 * taker_fee / (max_fee_share_of_r * balance)) if balance > 0 else float("inf")
    by_qty = rules.min_qty * price
    return {"price": price, "risk_pct": risk_pct, "risk_usd": risk_usd,
            "min_notional": rules.min_notional, "min_qty": rules.min_qty, "step": rules.step,
            "min_qty_notional": by_qty,
            "binding_minimum": "minQty" if by_qty > rules.min_notional * safety else "minNotional",
            "exchange_min": exchange_min, "floor": floor, "safety": safety,
            "ceiling_fee": ceiling_fee, "ceiling_leverage": ceiling_lev, "ceiling": ceiling,
            "min_stop_pct": min_stop, "max_stop_pct": max_stop,
            "min_risk_pct_needed": need_risk,
            "feasible": float(floor <= ceiling + 1e-9)}


def preflight(spec: BotSpec, cls: Any, rules: MarketRules | None,
              available_symbols: Iterable[str], bars_available: int,
              profile: RiskProfile = AGGRESSIVE, min_bars: int = 5000,
              taker_fee: float = BINANCE_USDM.taker_rate, max_fee_share_of_r: float = 0.25,
              price: float | None = None, safety: float = 1.0) -> Preflight:
    """Can this bot actually trade? Every refusal names the missing thing."""
    from app.backtest.replay import unsupported

    pf = Preflight(spec)

    why = unsupported(spec.strategy_id, cls)
    if why:
        pf.ok, pf.reason = False, f"needs {why} (not available in a historical kline replay)"
        return pf

    native = native_timeframe(cls)
    allowed = supported_timeframes(cls)
    pf.detail.update({"native_timeframe": native, "supported_timeframes": list(allowed)})
    if spec.timeframe not in allowed:
        if native and native not in ARENA_TIMEFRAMES:
            pf.ok, pf.reason = False, (f"native signal timeframe is {native}; the arena trades "
                                       f"{'/'.join(ARENA_TIMEFRAMES)} and v1 cannot be retimed")
        elif native is None:
            pf.ok, pf.reason = False, "native signal timeframe could not be determined"
        else:
            pf.ok, pf.reason = False, (f"{spec.timeframe} is not a signal timeframe of this strategy "
                                       f"(native {native})")
        return pf

    if spec.symbol not in set(available_symbols):
        pf.ok, pf.reason = False, "symbol not in the eligible universe"
        return pf

    if rules is None:
        pf.ok, pf.reason = False, "no exchange metadata for this symbol"
        return pf

    if bars_available < min_bars:
        pf.ok, pf.reason = False, (f"insufficient history: {bars_available:,} bars "
                                   f"< {min_bars:,} required")
        pf.detail["bars"] = bars_available
        return pf

    if not price or price <= 0:
        pf.ok, pf.reason = False, "no reference price for the sizing check"
        return pf

    # Can risk-based sizing ever produce a legal order here? Leverage headroom alone is not
    # enough: the fee gate caps the notional from above while the exchange minimum pushes from below.
    w = sizing_window(rules, profile, spec.max_leverage, price, taker_fee, max_fee_share_of_r, safety)
    pf.detail.update({"bars": bars_available, **w})
    if not w["feasible"]:
        binding = "fee gate" if w["ceiling_fee"] <= w["ceiling_leverage"] else "leverage"
        pf.ok, pf.reason = False, (
            f"unreachable order size: the smallest legal order is {w['floor']:.2f} USDT "
            f"({w['binding_minimum']}) but the {binding} caps a {profile.ordinary_risk_pct:.2%} "
            f"risk trade at {w['ceiling']:.2f} USDT; needs >= {w['min_risk_pct_needed']:.2%} risk")
        return pf
    return pf


# ---- candidate generation and selection ------------------------------------------------------

def candidates(strategies: dict[str, Any], symbols: Sequence[str],
               timeframes: Sequence[str] = ARENA_TIMEFRAMES,
               leverages: Sequence[int] = (20,),
               profile: str = "AGGRESSIVE", params_version: str = "v1") -> list[BotSpec]:
    """Every strategy x symbol x SUPPORTED timeframe x leverage combination, deterministically.

    Only timeframes the strategy genuinely signals on are generated -- a timeframe variant that
    cannot emit a signal is not a competitor. Order is sorted, never shuffled and never filtered by
    expected performance: picking combinations already known to look good is how a tournament gets
    rigged before it starts.
    """
    out: list[BotSpec] = []
    for sid in sorted(strategies):
        cls = strategies[sid]
        allowed = supported_timeframes(cls)
        for symbol in symbols:
            for tf in timeframes:
                if tf not in allowed:
                    continue
                for lev in leverages:
                    out.append(BotSpec(strategy_id=sid, symbol=symbol, timeframe=tf,
                                       max_leverage=int(lev), profile=profile,
                                       params_version=params_version,
                                       name=getattr(cls, "name", sid)))
    return out


def select(cands: Sequence[BotSpec], limit: int) -> list[BotSpec]:
    """Trim to `limit`, spreading the field across strategies AND coins, without looking at results.

    A Latin-square rotation: strategy i (in sorted order) is offered, in round r, the symbol at
    position (i + r) mod n of the sorted symbol list, falling through to the next symbol it has a
    candidate for. Round-robining on strategy alone -- what this did before -- gave every strategy
    its first-listed coin, which put 21 of 27 bots on SOL.

    Each round visits strategies whose timeframe has the fewest bots so far first, so a final
    partial round does not shortchange the few 15m strategies merely because they sort last. Both
    rules read labels only -- never a result.
    """
    if limit <= 0 or len(cands) <= limit:
        return list(cands)
    strategies = sorted({c.strategy_id for c in cands})
    index = {sid: i for i, sid in enumerate(strategies)}
    symbols = sorted({c.symbol for c in cands})
    pools: dict[str, list[BotSpec]] = {sid: [c for c in cands if c.strategy_id == sid]
                                       for sid in strategies}
    tf_of = {sid: pools[sid][0].timeframe for sid in strategies}
    out: list[BotSpec] = []
    rnd = 0
    while len(out) < limit and any(pools.values()):
        per_tf: dict[str, int] = {}
        for c in out:
            per_tf[c.timeframe] = per_tf.get(c.timeframe, 0) + 1
        order = sorted(strategies, key=lambda sid: (per_tf.get(tf_of[sid], 0), index[sid]))
        for sid in order:
            i = index[sid]
            pool = pools[sid]
            if not pool:
                continue
            rotation = [symbols[(i + rnd + k) % len(symbols)] for k in range(len(symbols))]
            pick = next((c for sym in rotation for c in pool if c.symbol == sym), pool[0])
            pool.remove(pick)
            out.append(pick)
            if len(out) >= limit:
                break
        rnd += 1
    return out
