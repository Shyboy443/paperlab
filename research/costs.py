"""Cost schedule used by every round in this scaffold.

Centralised so that the documented fee tier is one constant, and the
cost-breakeven analysis in the prior research can be re-derived
mechanically from this file.

Costs are **per side** as a fraction of notional. For multi-leg
strategies the round's engine multiplies by leg count when reporting.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class CostTier:
    name: str
    fee_bps_per_side: float        # 1 bp = 0.01% = 0.0001
    slip_bps_per_side: float
    notes: str = ""

# Mirrors the tiers listed in the user's cost-breakeven analysis.
TIERS: dict[str, CostTier] = {
    "std_taker":   CostTier("std_taker",  10.0,  5.0, "0.1% fee + 0.05% slip per side (BTC/ETH baseline)"),
    "std_taker_alt": CostTier("std_taker_alt", 10.0, 10.0, "0.1% fee + 0.10% slip per side (alt baseline)"),
    "bnb_disc":    CostTier("bnb_disc",    7.5,  5.0, "BNB 25% off taker, same slip"),
    "vip_maker_lo": CostTier("vip_maker_lo", 6.0,  3.0, "Low-VIP maker — needs ~$1M/mo volume"),
    "vip_maker_hi": CostTier("vip_maker_hi", 2.0,  2.0, "Top VIP maker — needs ~$100M+/mo volume"),
}

# The default for walk-forward runs. Per-round engines may override.
DEFAULT_TIER = "std_taker"

def round_trip_cost(tier: str = DEFAULT_TIER, legs: int = 1) -> float:
    """Total cost as a fraction of notional for one round trip on `legs` legs."""
    t = TIERS[tier]
    return (t.fee_bps_per_side + t.slip_bps_per_side) / 10_000.0 * legs * 2
