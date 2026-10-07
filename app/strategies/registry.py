"""Registry of the 20 strategies + first-boot defaults.

`load_all(strict=True)` imports every module listed in STRATEGY_MODULES. With strict=False a missing
module is skipped with a warning (used while the strategy set is being built); any other import error
always raises, because a broken strategy must not be silently dropped.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Iterable

from app.strategies.base import Strategy

log = logging.getLogger("paperlab.registry")

# (id, module name under app.strategies, class name)
STRATEGY_MODULES: list[tuple[str, str, str]] = [
    ("S01", "s01_ema_cross", "EmaCrossMomentum"),
    ("S02", "s02_rsi_sniper", "RsiExtremeSniper"),
    ("S03", "s03_bb_squeeze", "BollingerSqueezeBreak"),
    ("S04", "s04_donchian", "DonchianBreakout"),
    ("S05", "s05_vwap_reclaim", "VwapReclaimScalp"),
    ("S06", "s06_supertrend_flip", "SupertrendFlip"),
    ("S07", "s07_macd_accel", "MacdAcceleration"),
    ("S08", "s08_orb", "OpeningRangeBreak"),
    ("S09", "s09_atr_expansion", "AtrExpansionBreak"),
    ("S10", "s10_dual_tf_pullback", "DualTfMomentumPullback"),
    ("S11", "s11_vol_grid", "VolatilityGrid"),
    ("S12", "s12_ema_pullback", "EmaPullbackContinuation"),
    ("S13", "s13_volume_range_break", "VolumeSpikeRangeBreak"),
    ("S14", "s14_stochrsi_gate", "StochRsiTrendGate"),
    ("S15", "s15_liq_cascade_fade", "LiquidationCascadeFade"),
    ("S16", "s16_funding_fade", "FundingExtremeFade"),
    ("S17", "s17_book_imbalance", "OrderBookImbalanceScalp"),
    ("S18", "s18_rs_rotation", "RelativeStrengthRotation"),
    ("S19", "s19_vol_burst", "VolatilityBurstNewsProxy"),
    ("S20", "s20_ensemble_vote", "EnsembleVote"),
    # --- candidates added from public TradingView techniques; each one is backtested on real futures
    # data before it is trusted, and starts DISABLED until it earns a place (see CANDIDATES).
    ("S21", "s21_fvg_retest", "FairValueGapRetest"),
    ("S22", "s22_keltner_reversion", "KeltnerBandReversion"),
    ("S23", "s23_rsi_divergence", "RsiDivergence"),
    ("S24", "s24_pivot_reversal", "PivotLevelReversal"),
    ("S25", "s25_inside_bar_break", "InsideBarBreak"),
    # --- wide-target trend structures: low win rate, uncapped winners, no fixed take-profit.
    ("S26", "s26_trend_rider", "TrendRider"),
    ("S27", "s27_breakout_runner", "BreakoutRunner"),
]
STRATEGY_IDS: tuple[str, ...] = tuple(sid for sid, _, _ in STRATEGY_MODULES)

# Version 2: revised, cost-aware, timeframe-configurable. Same family ids, different competitors.
V2_MODULES: list[tuple[str, str, str]] = [
    ("S03", "v2.s03v2_squeeze_expansion", "SqueezeExpansionV2"),
    ("S04", "v2.s04v2_trend_breakout", "TrendBreakoutV2"),
    ("S12", "v2.s12v2_trend_pullback", "TrendPullbackV2"),
    ("S22", "v2.s22v2_range_reversion", "RangeReversionV2"),
    ("S26", "v2.s26v2_trend_rider", "TrendRiderV2"),
]


def load_v2() -> dict[str, type[Strategy]]:
    """The v2 families by id. Never merged into load_all(): the live engine and every stored v1
    experiment keep running the v1 classes."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V2_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v2":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v2")
        out[sid] = cls
    return out


def v2_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each v2 module's source, plus the shared v2 base. Recorded in the TEST run's
    configuration: a v2 result is only a v2 result if these match docs/V2_FREEZE.md."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("base", "v2.base")] + [(sid, mod) for sid, mod, _ in V2_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(f"app.strategies.{mod}"))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


V3_MODULES: list[tuple[str, str, str]] = [
    ("S31", "v3.s31_momentum_breakout", "MomentumBreakoutV3"),
    ("S32", "v3.s32_volatility_expansion", "VolatilityExpansionV3"),
    ("S33", "v3.s33_pullback_continuation", "PullbackContinuationV3"),
    ("S34", "v3.s34_range_rejection", "RangeRejectionV3"),
    ("S35", "v3.s35_flow_momentum", "FlowMomentumV3"),
    ("S36", "v3.s36_fast_mean_reversion", "FastMeanReversionV3"),
]


def load_v3() -> dict[str, type[Strategy]]:
    """The V3 intraday families by id (docs/V3_PROTOCOL.md). Never merged into load_all()/load_v2()."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V3_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v3":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v3")
        out[sid] = cls
    return out


def v3_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each V3 module's source and the shared V3 base: docs/V3_FREEZE.md."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("base", "v3.base")] + [(sid, mod) for sid, mod, _ in V3_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(f"app.strategies.{mod}"))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


V31_MODULES: list[tuple[str, str, str]] = [
    ("S33.1", "v31.s33_1_pullback_regime", "PullbackRegimeV31"),
    ("S35.1", "v31.s35_1_flow_persistence", "FlowPersistenceV31"),
    ("S37", "v31.s37_impulse_continuation", "ImpulseContinuationV31"),
]


def load_v31() -> dict[str, type[Strategy]]:
    """The V3.1 continuation families by id (docs/V31_PROTOCOL.md). Never merged into any other loader."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V31_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v3.1":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v3.1")
        out[sid] = cls
    return out


def v31_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each V3.1 module, the V3.1 base and the V3 base it inherits from."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("v3_base", "v3.base"), ("base", "v31.base")] + [(sid, mod) for sid, mod, _ in V31_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(f"app.strategies.{mod}"))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


V4_MODULES: list[tuple[str, str, str]] = [
    ("V4.1", "v4.f1_trend_pullback", "TrendPullbackV4"),
    ("V4.2", "v4.f2_squeeze_expansion", "SqueezeExpansionV4"),
    ("V4.3", "v4.f3_breakout_retest", "BreakoutRetestV4"),
    ("V4.4", "v4.f4_momentum_continuation", "MomentumContinuationV4"),
    ("V4.5", "v4.f5_failed_breakout", "FailedBreakoutV4"),
    ("V4.6", "v4.f6_trend_range_rejection", "TrendRangeRejectionV4"),
    ("V4.7", "v4.f7_flow_breakout", "FlowBreakoutV4"),
]


def load_v4() -> dict[str, type[Strategy]]:
    """The V4 intraday specialists by id (docs/V4_PROTOCOL.md). Never merged into any other loader."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V4_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v4":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v4")
        out[sid] = cls
    return out


def v4_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each V4 module, the V4 base and the V3 base it inherits from: docs/V4_FREEZE.md."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("v3_base", "v3.base"), ("base", "v4.base")] + [(sid, mod) for sid, mod, _ in V4_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(f"app.strategies.{mod}"))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


V5_MODULES: list[tuple[str, str, str]] = [
    ("V5.1", "v5.p1_positioning_trend", "PositioningTrendV5"),
    ("V5.2", "v5.p2_crowding_reversal", "CrowdingReversalV5"),
    ("V5.3", "v5.p3_compression_breakout", "CompressionBreakoutV5"),
    ("V5.4", "v5.p4_trend_pullback", "TrendPullbackV5"),
    ("V5.5", "v5.p5_deleveraging_reversal", "DeleveragingReversalV5"),
    ("V5.6", "v5.p6_carry_trend", "CarryTrendV5"),
    ("V5.7", "v5.p7_range_reversal", "RangeReversalV5"),
    ("V5.8", "v5.p8_momentum_continuation", "MomentumContinuationV5"),
]


def load_v5() -> dict[str, type[Strategy]]:
    """The V5 hourly / daily positioning families by id (docs/V5_PROTOCOL.md). Never merged into any other loader."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V5_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v5":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v5")
        out[sid] = cls
    return out


def v5_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each V5 module, the V5 base and the V3 base it inherits from: docs/V5_FREEZE.md."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("v3_base", "v3.base"), ("base", "v5.base")] + [(sid, mod) for sid, mod, _ in V5_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(f"app.strategies.{mod}"))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


V6_MODULES: list[tuple[str, str, str]] = [
    ("V6.1", "v6.p1_positioning_pullback", "PositioningPullbackV6"),
    ("V6.2", "v6.p2_momentum_oi", "MomentumOIV6"),
    ("V6.3", "v6.p3_funding_crowding", "FundingCrowdingV6"),
    ("V6.4", "v6.p4_oi_breakout", "OIBreakoutV6"),
    ("V6.5", "v6.p5_deleveraging", "DeleveragingV6"),
    ("V6.6", "v6.p6_market_aligned", "MarketAlignedV6"),
]


def load_v6() -> dict[str, type[Strategy]]:
    """The V6 forward families by id (docs/V6_PROTOCOL.md). Never merged into any other loader."""
    out: dict[str, type[Strategy]] = {}
    for sid, mod, cls_name in V6_MODULES:
        cls = getattr(importlib.import_module(f"app.strategies.{mod}"), cls_name)
        if cls.id != sid or getattr(cls, "version", "v1") != "v6":
            raise ValueError(f"{mod}.{cls_name} must declare id {sid} and version v6")
        out[sid] = cls
    return out


def v6_fingerprints() -> dict[str, str]:
    """sha256 (12 hex) of each V6 module, the V6 base, the V3 base it inherits from and the V6 feature definitions:
    docs/V6_FREEZE.json. The live forward worker refuses to trade when these differ from the freeze."""
    import hashlib
    import inspect
    out: dict[str, str] = {}
    mods = [("v3_base", "app.strategies.v3.base"), ("base", "app.strategies.v6.base"),
            ("features", "app.competition.v6_features")] + [(sid, f"app.strategies.{mod}") for sid, mod, _ in V6_MODULES]
    for name, mod in mods:
        src = inspect.getsource(importlib.import_module(mod))
        out[name] = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    return out


ENSEMBLE_CONTRIBUTORS: tuple[str, ...] =("S01", "S03", "S06", "S10", "S12", "S13", "S19")

# The lab is a bake-off: every strategy boots armed at full size with its own isolated book. Nothing
# starts disabled, shadow-only or paused. The manual toggle still exists for switching one off by hand.
FIRST_BOOT_ENABLED = True
FIRST_BOOT_SIZE_MULT = 1.0


def load_all(strict: bool = True) -> dict[str, type[Strategy]]:
    classes: dict[str, type[Strategy]] = {}
    for sid, module_name, class_name in STRATEGY_MODULES:
        full = f"app.strategies.{module_name}"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError as exc:
            if strict or exc.name != full:
                raise
            log.warning("strategy %s skipped: module %s not found", sid, full)
            continue
        cls = getattr(module, class_name, None)
        if cls is None or not isinstance(cls, type) or not issubclass(cls, Strategy):
            raise ImportError(f"{full} does not define Strategy subclass {class_name}")
        if cls.id != sid:
            raise ImportError(f"{full}.{class_name}.id is {cls.id!r}, expected {sid!r}")
        classes[sid] = cls
    return classes


def first_boot_state(registered: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Every registered strategy starts armed at full size."""
    return {sid: {"enabled": FIRST_BOOT_ENABLED, "size_mult": FIRST_BOOT_SIZE_MULT} for sid in registered}


def describe_all(classes: dict[str, type[Strategy]]) -> list[dict[str, Any]]:
    return [classes[sid].describe() for sid in sorted(classes)]
