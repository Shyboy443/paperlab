"""EDGE_TO_COST_RATIO: is this candidate worth paying a round trip for?

    candidate signal -> cost gate -> (Jev, if enabled) -> RiskManager -> execution

    edge-to-cost = expected move / expected round-trip cost

The expected move is the strategy's own figure when it states one (`meta["expected_move_pct"]`:
v2 strategies give the distance their first objective needs, or for a reversion the distance to
the mean they exit at). A strategy that states nothing gets a CONSERVATIVE proxy -- the smaller of
its first target and `proxy_atr_cap` x ATR -- and the source is recorded, so a proxy is never
mistaken for a prediction.

The round trip is two taker fees plus two half-spreads (entry and exit) from the same execution
model the replay fills with. Funding is not included (it is paid on holding time, not per trade,
and is small at these horizons); `extra_cost_bps` exists for a stress run.

The gate is deterministic and optional. Rejections are counted, and when the replay shadows them
the report can show what the gate avoided and what it gave up.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CostGateConfig:
    min_edge_to_cost: float = 2.0
    atr_n: int = 14
    proxy_atr_cap: float = 1.0
    extra_cost_bps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def expected_move(sig: Any, g: Mapping[str, Any], cfg: CostGateConfig) -> tuple[float | None, str]:
    meta = getattr(sig, "meta", None) or {}
    stated = meta.get("expected_move_pct")
    if isinstance(stated, (int, float)) and stated > 0:
        return float(stated), str(meta.get("expected_move_source") or "strategy")
    price = float(g.get("price") or sig.entry_price or 0.0)
    if price <= 0:
        return None, "no price"
    cands = []
    tps = getattr(sig, "take_profits", None) or []
    if tps:
        cands.append(abs(float(tps[0].price) - float(sig.entry_price)) / price)
    atr = g.get("atr")
    if atr:
        cands.append(cfg.proxy_atr_cap * float(atr) / price)
    if not cands:
        return None, "no target and no ATR"
    return min(cands), f"proxy: min(first target, {cfg.proxy_atr_cap:g} ATR)"


def round_trip_cost(g: Mapping[str, Any], cfg: CostGateConfig) -> float:
    taker = float(g.get("taker_fee") or 0.0)
    half = float(g.get("half_spread_bps") or 0.0)
    return 2.0 * taker + 2.0 * half / 1e4 + cfg.extra_cost_bps / 1e4


class CostGate:
    def __init__(self, cfg: CostGateConfig = CostGateConfig()):
        self.cfg = cfg

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
        move, source = expected_move(sig, g, self.cfg)
        cost = round_trip_cost(g, self.cfg)
        if move is None or cost <= 0:
            return False, {"edge_to_cost": None, "expected_move_pct": move, "round_trip_cost_pct": cost,
                           "expected_move_source": source, "cost_gate": "no estimate"}
        ratio = move / cost
        return ratio >= self.cfg.min_edge_to_cost, {
            "edge_to_cost": round(ratio, 3), "expected_move_pct": round(move, 6),
            "round_trip_cost_pct": round(cost, 6), "expected_move_source": source,
            "cost_gate": "pass" if ratio >= self.cfg.min_edge_to_cost else "reject"}
