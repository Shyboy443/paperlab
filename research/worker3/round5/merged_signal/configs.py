"""The pre-registered config list (16 candidates + 2 references). build(D, cfg) -> (legs, scheme)."""
import numpy as np
import lib

REF = {
    "W1": dict(legs=["R", "M", "T"]),                       # Worker 1 itself (reference)
    "H": dict(legs=["H"]),                                  # high52 alone on W1's panel (reference)
}
CAND = {
    # A: rank-average of W1's rotation score (14d return) and the 30d range score (George-Hwang combination)
    "A1_rank_w": dict(legs=["RANKw", "M", "T"]),
    "A2_rank_d": dict(legs=["RANKd", "M", "T"]),
    "A3_rank_rec_w": dict(legs=["RANKRECw", "M", "T"]),     # + Bhootra-Hur recency of the high
    # B: high52 as a filter on W1's picks
    "B1_filt50_all": dict(legs=["R", "M", "T"], filt=0.5),
    "B2_filt70_all": dict(legs=["R", "M", "T"], filt=0.7),
    "B3_filt70_longs": dict(legs=["R", "M", "T"], filt=0.7, longs_only=True),
    "B4_Rrefill50": dict(legs=["RREF50", "M", "T"]),
    "B5_Rrefill70": dict(legs=["RREF70", "M", "T"]),
    # C: W1 picks with high52-style holding
    "C1_Rhold70": dict(legs=["RHOLD70", "M", "T"]),
    "C2_Rhold85": dict(legs=["RHOLD85", "M", "T"]),
    "C3_Tlongs_near_high50": dict(legs=["R", "M", "TF50L"]),
    # D: one combined book, high52 as a 4th leg, inverse-vol (and EW / ERC) sizing
    "D1_RMTH_IV": dict(legs=["R", "M", "T", "H"]),
    "D2_RMTH_EW": dict(legs=["R", "M", "T", "H"], scheme="EW"),
    "D3_RMTH_ERC": dict(legs=["R", "M", "T", "H"], scheme="ERC"),
    # E: combinations
    "E1_rank_w_plus_H": dict(legs=["RANKw", "M", "T", "H"]),
    "E2_filt50_plus_H": dict(legs=["R", "M", "T", "H"], filt=0.5, filt_legs="RMT"),
}


def leg(D, name, cfg, drop=(), rng_=None, P=None):
    """P: neighbour overrides, e.g. {'L':20,'X':0.6,'lb':10,'k':8}."""
    P = P or {}
    L = P.get("L", 30); lb = P.get("lb", 14)
    lib.set_dom(D)
    if name in ("R", "M", "T"):
        wp = {"R": dict(lb=lb), "M": {}, "T": dict(look=P.get("look", 60))}[name]
        F = lib.w1_leg(D, name, drop=drop, rng_=rng_, p=wp)
        X = P.get("X", cfg["filt"]) if "filt" in cfg else None
        if X is not None and name in cfg.get("filt_legs", "RMT"):
            F = lib.filt(D, F, X, L, cfg.get("longs_only", False))
        return lib.mk(name, F)
    if name == "H":
        return lib.mk("M", lib.leg_H(D, L, P.get("k", 10), drop=drop, rng_=rng_))
    if name.startswith("RANK"):
        F = lib.leg_rank(D, P.get("w_mom", 0.5), lb, L, P.get("k", 10), use_rec="REC" in name, drop=drop, rng_=rng_)
        return lib.mk("R" if name.endswith("w") else "M", F)
    if name.startswith("RREF"):
        return lib.mk("R", lib.leg_R_refill(D, P.get("X", int(name[4:]) / 100), lb, L, P.get("k", 10), drop=drop, rng_=rng_))
    if name.startswith("RHOLD"):
        return lib.mk("M", lib.leg_R_hold(D, P.get("X", int(name[5:]) / 100), lb, L, P.get("k", 10), drop=drop, rng_=rng_))
    if name.startswith("TF"):
        F = lib.w1_leg(D, "T", drop=drop, rng_=rng_, p=dict(look=P.get("look", 60)))
        F = lib.filt(D, F, P.get("X", int(name[2:4]) / 100), L, longs_only=True)
        return lib.mk("T", F)
    raise KeyError(name)


def build(D, cfg, drop=(), rng_=None, P=None):
    legs = [leg(D, n, cfg, drop, rng_, P) for n in cfg["legs"]]
    return legs, cfg.get("scheme", "IV")


ALL = {**REF, **CAND}
