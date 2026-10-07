"""V4 EXPECTED-EDGE model: one causal cell per family x timeframe, shrunk toward zero edge.

Why simpler than V3.1: the V3.1 model estimated six nested buckets (family -> timeframe -> coin -> quality
band x regime x volatility band) and was UNCALIBRATED on both windows -- the candidates it passed realized
-0.250R and the ones it refused -0.237R (DEVELOPMENT). With a few hundred observations per family, deep
buckets fit noise. V4 asks one question the evidence can answer: has THIS family on THIS timeframe, pooled
over the universe's coins, had a positive gross expectancy over its most recent resolved setups?

    evidence   the RAW observation ledger of the SAME window (every legal setup simulated at TAKE size,
               sequenced like an ungated bot), used CAUSALLY: a candidate at t sees only observations that
               EXITED before t. The model is identical in DEVELOPMENT and TEST; no outcome from another
               window ever enters it, so nothing is carried from DEVELOPMENT into the holdout.
    estimate   mu = sum(gross R of the last `window` observations) / (n + k)      (k pseudo-trades of zero edge)
    cost       THIS candidate's modelled round trip in R (two taker fees + two half spreads over its stop)
    TAKE       n >= min_n and mu - cost >= take_margin_r
    ATTACK     (mu - cost - se) >= attack_lower_r, se = sd / sqrt(n + k)       (CONTROL's deterministic tier)

The EdgeGate of V3.1 (v31_edge.EdgeGate) is reused unchanged as the engine slot: it only needs `advance`,
`predict` and `fingerprint`.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.competition.v4_config import EdgeConfigV4
from app.competition.v31_edge import qband


def _r(x: Any, nd: int = 4) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


@dataclass
class EdgeModelV4:
    observations: Sequence[Mapping[str, Any]]
    cfg: EdgeConfigV4 = field(default_factory=EdgeConfigV4)

    def __post_init__(self) -> None:
        obs = [o for o in self.observations if o.get("sequenced", True) and o.get("gross_r") is not None
               and o.get("exit_ts") is not None]
        self._obs = sorted(obs, key=lambda o: (int(o["exit_ts"]), str(o.get("coin")), int(o.get("ts") or 0)))
        self._exits = [int(o["exit_ts"]) for o in self._obs]
        self._i = 0
        self._cells: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=int(self.cfg.window)))
        self._ts = -1

    def advance(self, ts: int) -> None:
        """Absorb every observation that EXITED strictly before `ts`. Time only moves forward."""
        if ts < self._ts:
            raise ValueError("EdgeModelV4.advance: time went backwards (one model per chronological replay)")
        self._ts = ts
        j = bisect.bisect_left(self._exits, ts)
        while self._i < j:
            o = self._obs[self._i]
            self._cells[(o["sid"], o["tf"])].append(float(o["gross_r"]))
            self._i += 1

    @property
    def absorbed(self) -> int:
        return self._i

    def fingerprint(self) -> str:
        blob = json.dumps([[o["sid"], o["tf"], o["coin"], o.get("ts"), o["exit_ts"], round(float(o["gross_r"]), 6)]
                           for o in self._obs], separators=(",", ":"))
        return hashlib.sha256((blob + json.dumps(self.cfg.to_dict(), sort_keys=True)).encode()).hexdigest()[:16]

    def cell(self, sid: str, tf: str) -> dict[str, Any]:
        xs = list(self._cells.get((sid, tf)) or ())
        n, k = len(xs), float(self.cfg.shrinkage_k)
        if not n:
            return {"n": 0, "mu": 0.0, "sd": None, "se": None, "raw_mean": None}
        s = sum(xs)
        mean = s / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n >= 2 else None
        return {"n": n, "mu": s / (n + k), "sd": sd, "se": (sd or 1.0) / math.sqrt(n + k), "raw_mean": mean}

    def predict(self, *, sid: str, tf: str, coin: str, quality: Any, regime: str, vol_band: str,
                cost_r: float | None, stop_pct: float | None) -> dict[str, Any]:
        c = self.cfg
        cell = self.cell(sid, tf)
        n = cell["n"]
        base = {"qband": qband(quality), "regime": regime, "vol_band": vol_band,
                "n": {"family": n, "strategy_tf": n, "coin": None, "exact": n}, "evidence": self._i,
                "cost_r": _r(cost_r), "model": "V4_FAMILY_TF_ROLLING"}
        if n < c.min_n or cost_r is None or not stop_pct:
            why = "INSUFFICIENT_EVIDENCE" if n < c.min_n else "NO_COST_ESTIMATE"
            return {**base, "status": "REJECT", "reason": why, "net_r": None, "gross_r": None, "lower_r": None,
                    "se": None, "attack_eligible": False, "exceptional": False, "attack_block": why}
        mu, se = cell["mu"], cell["se"]
        net = mu - cost_r
        lower = net - se
        gross_bps, cost_bps = mu * stop_pct * 1e4, cost_r * stop_pct * 1e4
        eligible = lower >= c.attack_lower_r
        status = "PASS" if net >= c.take_margin_r else "REJECT"
        reason = "PASS" if status == "PASS" else ("NEGATIVE_EDGE" if net < 0 else "EDGE_BELOW_MARGIN")
        return {**base, "status": status, "reason": reason, "gross_r": _r(mu), "net_r": _r(net), "lower_r": _r(lower),
                "se": _r(se), "sd": _r(cell["sd"]), "raw_mean_r": _r(cell["raw_mean"]),
                "edge_bps": _r(gross_bps - cost_bps, 2), "gross_bps": _r(gross_bps, 2), "cost_bps": _r(cost_bps, 2),
                "headroom_bps": _r(gross_bps - cost_bps, 2), "headroom_ratio": _r(mu / cost_r, 3) if cost_r > 0 else None,
                "attack_eligible": eligible, "exceptional": False,
                "attack_block": None if eligible else f"lower bound {lower:+.3f}R < {c.attack_lower_r:+.2f}R"}
