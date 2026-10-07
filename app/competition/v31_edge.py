"""EXPECTED NET EDGE model and the deterministic EDGE GATE (docs/V31_PROTOCOL.md).

Evidence. A RAW observation run replays every V3.1 family x coin x timeframe with an observer in the
gate slot: every candidate that passed legality at TAKE size is simulated in the engine's shadow book
(same fills, fees, spread and exits as a real trade) and nothing is ever traded. The candidates are
then SEQUENCED like an ungated bot would have taken them (flat, out of cooldown), so overlapping
signals of the same move are not counted twice. One observation = one sequenced candidate:

    features  strategy, timeframe, coin, quality band, regime (4h trend vs the trade), vol band (1h ATR rank)
    outcome   gross R = realized net R + the candidate's own modelled round-trip cost in R;
              whether the first target was reached (P(target)), whether it was stopped (P(stop)),
              winner and loser R

Prediction for a candidate at time t uses ONLY observations whose exit happened before t (DEVELOPMENT
replays are causal); TEST uses the DEVELOPMENT ledger, frozen -- a holdout outcome never enters it.
The expected gross R of the candidate's bucket is estimated by hierarchical shrinkage:

    family (strategy)            mu_F  = S_F / (n_F + k)                     (skeptical prior: zero edge)
    strategy x timeframe         mu_ST = (S_ST + k mu_F[-ST]) / (n_ST + k)
    strategy x timeframe x coin  mu_C  = (S_C  + k mu_ST[-C]) / (n_C  + k)
    exact bucket                 mu_E  = (S_E  + k mu_C[-E])  / (n_E  + k)     (+ quality band, regime, vol band)

where mu_P[-X] is the parent's estimate from the parent's OTHER observations (leave-child-out: a child's
trades are never counted twice), and the family needs at least `min_family_n` resolved observations,
otherwise the verdict is INSUFFICIENT_EVIDENCE: confidence is never manufactured from a tiny sample. The
standard error counts each level's own observations plus at most k of its parent's other observations
(n_eff), never the prior.

    predicted net R  = mu_E - cost R of THIS candidate (2 taker fees + 2 half-spreads over its stop)
    EDGE HEADROOM    = expected gross edge - expected execution cost (bps of notional; also as a ratio)
    TAKE             predicted net R >= take_margin_r
    ATTACK-eligible  (net R - 1 se) >= attack_lower_r, gross >= attack_headroom_ratio x cost,
                     signal quality >= attack_min_quality
    EXCEPTIONAL      (net R - 1 se) >= exceptional_lower_r and quality >= exceptional_quality
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.competition.v31_config import EdgeConfig

LEVELS = ("family", "strategy_tf", "coin", "exact")


def qband(quality: Any, bands: Sequence[float] = (0.40, 0.60)) -> str:
    if not isinstance(quality, (int, float)):
        return "UNKNOWN"
    return "LOW" if quality < bands[0] else "MID" if quality < bands[1] else "HIGH"


def bucket_keys(sid: str, tf: str, coin: str, qb: str, regime: str, vol: str) -> tuple[tuple, ...]:
    return ((sid,), (sid, tf), (sid, tf, coin), (sid, tf, coin, qb, regime, vol))


def cost_r_of(taker_fee: float, half_spread_bps: float, stop_pct: float) -> float | None:
    """Modelled round trip (two taker fees, two half-spreads) in R for a stop `stop_pct` away."""
    if not stop_pct or stop_pct <= 0:
        return None
    return (2.0 * float(taker_fee) + 2.0 * float(half_spread_bps) / 1e4) / float(stop_pct)


@dataclass
class _Acc:
    n: int = 0
    s: float = 0.0
    ss: float = 0.0
    tp: int = 0
    stop: int = 0
    wins: int = 0
    win_s: float = 0.0
    loss_s: float = 0.0

    def add(self, o: Mapping[str, Any]) -> None:
        g = float(o["gross_r"])
        self.n += 1
        self.s += g
        self.ss += g * g
        self.tp += 1 if o.get("tp_hit") else 0
        self.stop += 1 if o.get("stopped") else 0
        if g > 0:
            self.wins += 1
            self.win_s += g
        else:
            self.loss_s += g

    @property
    def sd(self) -> float | None:
        if self.n < 2:
            return None
        var = (self.ss - self.s * self.s / self.n) / (self.n - 1)
        return math.sqrt(max(var, 0.0))


@dataclass
class EdgeModel:
    """Causal hierarchical-shrinkage estimate of a candidate's expected gross R."""
    observations: Sequence[Mapping[str, Any]]
    cfg: EdgeConfig = field(default_factory=EdgeConfig)

    def __post_init__(self) -> None:
        obs = [o for o in self.observations if o.get("sequenced", True) and o.get("gross_r") is not None
               and o.get("exit_ts") is not None]
        self._obs = sorted(obs, key=lambda o: int(o["exit_ts"]))
        self._exits = [int(o["exit_ts"]) for o in self._obs]
        self._i = 0
        self._acc: dict[tuple, _Acc] = defaultdict(_Acc)
        self._ts = -1

    # -- evidence ------------------------------------------------------------------------------------
    def advance(self, ts: int) -> None:
        """Absorb every observation that EXITED strictly before `ts`. Time only moves forward."""
        if ts < self._ts:
            raise ValueError("EdgeModel.advance: time went backwards (one model per chronological replay)")
        self._ts = ts
        j = bisect.bisect_left(self._exits, ts)
        while self._i < j:
            o = self._obs[self._i]
            for key in bucket_keys(o["sid"], o["tf"], o["coin"], o["qband"], o["regime"], o["vol_band"]):
                self._acc[key].add(o)
            self._i += 1

    @property
    def absorbed(self) -> int:
        return self._i

    def fingerprint(self) -> str:
        blob = json.dumps([[o["sid"], o["tf"], o["coin"], o["ts"], o["exit_ts"], round(float(o["gross_r"]), 6)]
                           for o in self._obs], separators=(",", ":"))
        return hashlib.sha256((blob + json.dumps(self.cfg.to_dict(), sort_keys=True)).encode()).hexdigest()[:16]

    # -- prediction ----------------------------------------------------------------------------------
    def predict(self, *, sid: str, tf: str, coin: str, quality: Any, regime: str, vol_band: str,
                cost_r: float | None, stop_pct: float | None) -> dict[str, Any]:
        c, k = self.cfg, float(self.cfg.shrinkage_k)
        qb = qband(quality, c.quality_bands)
        keys = bucket_keys(sid, tf, coin, qb, regime or "UNKNOWN", vol_band or "UNKNOWN")
        accs = [self._acc.get(key) or _Acc() for key in keys]
        n = [a.n for a in accs]
        base = {"qband": qb, "regime": regime, "vol_band": vol_band, "n": dict(zip(LEVELS, n)),
                "evidence": self._i, "cost_r": _r(cost_r)}
        if n[0] < c.min_family_n or cost_r is None or not stop_pct:
            why = "INSUFFICIENT_EVIDENCE" if n[0] < c.min_family_n else "NO_COST_ESTIMATE"
            return {**base, "status": "REJECT", "reason": why, "net_r": None, "gross_r": None, "lower_r": None,
                    "se": None, "attack_eligible": False, "exceptional": False, "attack_block": why}
        # Leave-child-out shrinkage: a level's prior is its parent's estimate from the parent's OTHER
        # observations, so a child's trades are never counted twice (a coin bucket and its exact bucket
        # holding the same 3 trades must not move the estimate twice).
        a0 = accs[0]
        mu, den = a0.s / (a0.n + k), a0.n + k                     # family: skeptical prior of zero edge
        tp_p, stop_p, cden = a0.tp / a0.n, a0.stop / a0.n, float(a0.n)   # its own rates (no prior on a frequency)
        n_eff = float(a0.n)
        for a in accs[1:]:
            rest = den - a.n
            prior = (mu * den - a.s) / rest if rest > 1e-9 else mu
            crest = cden - a.n
            prior_tp = min(1.0, max(0.0, (tp_p * cden - a.tp) / crest)) if crest > 1e-9 else tp_p
            prior_stop = min(1.0, max(0.0, (stop_p * cden - a.stop) / crest)) if crest > 1e-9 else stop_p
            mu, den = (a.s + k * prior) / (a.n + k), a.n + k
            tp_p, stop_p, cden = (a.tp + k * prior_tp) / (a.n + k), (a.stop + k * prior_stop) / (a.n + k), a.n + k
            n_eff = a.n + min(k, max(0.0, n_eff - a.n))
        sd = next((a.sd for a in reversed(accs) if a.n >= 30 and a.sd is not None), accs[0].sd) or 1.0
        se = sd / math.sqrt(max(n_eff, 1.0))
        deep = next((a for a in reversed(accs) if a.n >= 10), accs[0])
        avg_win = deep.win_s / deep.wins if deep.wins else None
        avg_loss = deep.loss_s / (deep.n - deep.wins) if deep.n > deep.wins else None
        net = mu - cost_r
        lower = net - se
        gross_bps, cost_bps = mu * stop_pct * 1e4, cost_r * stop_pct * 1e4
        ratio = (mu / cost_r) if cost_r > 0 else None
        q = float(quality) if isinstance(quality, (int, float)) else 0.0
        block = None
        if lower < c.attack_lower_r:
            block = f"lower bound {lower:+.3f}R < {c.attack_lower_r:+.2f}R"
        elif ratio is None or ratio < c.attack_headroom_ratio:
            block = f"gross/cost {ratio if ratio is None else round(ratio, 2)} < {c.attack_headroom_ratio:g}"
        elif q < c.attack_min_quality:
            block = f"quality {q:.2f} < {c.attack_min_quality:.2f}"
        eligible = block is None
        exceptional = eligible and lower >= c.exceptional_lower_r and q >= c.exceptional_quality
        status = "PASS" if net >= c.take_margin_r else "REJECT"
        reason = "PASS" if status == "PASS" else ("NEGATIVE_EDGE" if net < 0 else "EDGE_BELOW_MARGIN")
        return {**base, "status": status, "reason": reason, "gross_r": _r(mu), "net_r": _r(net),
                "lower_r": _r(lower), "se": _r(se), "n_eff": _r(n_eff, 1), "sd": _r(sd),
                "p_target": _r(tp_p), "p_stop": _r(stop_p), "avg_win_r": _r(avg_win), "avg_loss_r": _r(avg_loss),
                "edge_bps": _r(gross_bps - cost_bps, 2), "gross_bps": _r(gross_bps, 2), "cost_bps": _r(cost_bps, 2),
                "headroom_bps": _r(gross_bps - cost_bps, 2), "headroom_ratio": _r(ratio, 3),
                "attack_eligible": eligible, "exceptional": exceptional, "attack_block": block}


def _r(x: Any, nd: int = 4) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


def features_of(sig: Any) -> dict[str, Any]:
    meta = getattr(sig, "meta", None) or {}
    return {"sid": sig.strategy_id, "tf": sig.tf, "coin": sig.symbol.replace("USDT", ""),
            "quality": meta.get("signal_quality"), "regime": meta.get("regime") or "UNKNOWN",
            "vol_band": meta.get("vol_band") or "UNKNOWN", "stop_pct": meta.get("stop_pct")}


class EdgeGate:
    """ReplayEngine cost-gate slot: the expected-net-edge verdict, BEFORE sizing, legality and Jev.

    Writes the verdict to `sig.meta["edge"]` (sizing and Jev read it) and keeps one row per candidate
    for the participation funnel and the edge-calibration analysis. `probe`, when set, answers "would
    the RiskManager accept this at TAKE size right now" (dry run, no side effects), so the funnel can
    count legal setups in the order raw -> legal -> positive edge -> Jev -> executed."""

    def __init__(self, model: EdgeModel, probe: Callable[[Any], Any] | None = None, keep_rows: bool = True):
        self.model = model
        self.probe = probe
        self.keep_rows = keep_rows
        self.rows: list[dict[str, Any]] = []

    def __call__(self, sig: Any, g: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
        f = features_of(sig)
        self.model.advance(int(sig.ts))
        cr = cost_r_of(float(g.get("taker_fee") or 0.0), float(g.get("half_spread_bps") or 0.0), f["stop_pct"] or 0.0)
        v = self.model.predict(sid=f["sid"], tf=f["tf"], coin=f["coin"], quality=f["quality"], regime=f["regime"],
                               vol_band=f["vol_band"], cost_r=cr, stop_pct=f["stop_pct"])
        sig.meta["edge"] = v
        legal = None
        if self.probe is not None:
            try:
                d = self.probe(sig)
                legal = bool(d.approved)
                legal_why = None if d.approved else str(d.reason).split(":")[0]
            except Exception as exc:          # the probe must never change the outcome
                legal, legal_why = None, f"probe error {type(exc).__name__}"
        else:
            legal_why = None
        cid = f"eg-{sig.ts}-{sig.side}-{len(self.rows)}"
        if self.keep_rows:
            self.rows.append({"id": cid, "ts": int(sig.ts), "side": sig.side, "quality": f["quality"],
                              "qband": v["qband"], "regime": f["regime"], "vol_band": f["vol_band"],
                              "stop_pct": f["stop_pct"], "status": v["status"], "reason": v["reason"],
                              "net_r": v["net_r"], "gross_r": v["gross_r"], "lower_r": v["lower_r"], "se": v["se"],
                              "cost_r": v["cost_r"], "edge_bps": v.get("edge_bps"), "headroom_ratio": v.get("headroom_ratio"),
                              "attack_eligible": v["attack_eligible"], "exceptional": v["exceptional"],
                              "n_exact": v["n"]["exact"], "n_family": v["n"]["family"], "legal": legal,
                              "legal_reason": legal_why})
        sig.meta["edge_candidate_id"] = cid
        info = {"edge_to_cost": v.get("headroom_ratio"), "edge_status": v["status"], "edge_reason": v["reason"],
                "edge_net_r": v["net_r"], "cost_gate": "pass" if v["status"] == "PASS" else "reject",
                "candidate_id": cid}
        return v["status"] == "PASS", info


# ---- observations from a RAW record ---------------------------------------------------------------------

def sequence(cands: Sequence[dict[str, Any]], cooldown_ms: int) -> None:
    """Mark the candidates an UNGATED bot would have taken: flat at the signal and out of cooldown.
    In place: `sequenced` True/False. Candidates with no simulated outcome are never sequenced."""
    last_exit, last_sig = -1, -10 ** 15
    for c in sorted(cands, key=lambda c: (int(c["ts"]), c["id"])):
        ok = c.get("exit_ts") is not None and int(c["ts"]) >= last_exit and int(c["ts"]) >= last_sig + cooldown_ms
        c["sequenced"] = bool(ok)
        if ok:
            last_exit, last_sig = int(c["exit_ts"]), int(c["ts"])


def observations(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The model's evidence rows from a finished RAW record (sequenced candidates with an outcome)."""
    idn = rec["identity"]
    out = []
    for c in rec.get("observations") or []:
        if not c.get("sequenced") or c.get("net_r") is None or c.get("cost_r") is None:
            continue
        out.append({"sid": idn["strategy_id"], "tf": idn["timeframe"], "coin": idn["coin"], "ts": c["ts"],
                    "exit_ts": c["exit_ts"], "qband": c["qband"], "regime": c.get("regime") or "UNKNOWN",
                    "vol_band": c.get("vol_band") or "UNKNOWN", "gross_r": float(c["net_r"]) + float(c["cost_r"]),
                    "net_r": float(c["net_r"]), "cost_r": float(c["cost_r"]), "tp_hit": bool(c.get("tp_hit")),
                    "stopped": bool(c.get("stopped")), "sequenced": True})
    return out


def load_evidence(records: Iterable[Mapping[str, Any]], until_ms: int | None = None) -> list[dict[str, Any]]:
    """All RAW observations, optionally only those that EXITED by `until_ms` (the frozen DEV ledger)."""
    out = []
    for rec in records:
        for o in observations(rec):
            if until_ms is None or int(o["exit_ts"]) <= until_ms:
                out.append(o)
    return out


# ---- calibration: does the predicted edge rank realized outcomes? -----------------------------------------

PRED_EDGES = (-9e9, -0.10, 0.0, 0.05, 0.10, 0.20, 0.40, 9e9)


def calibration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Predicted net R vs realized net R over candidates with an outcome (traded or shadow)."""
    pts = [(float(r["net_r"]), float(r["realized_r"])) for r in rows
           if isinstance(r.get("net_r"), (int, float)) and isinstance(r.get("realized_r"), (int, float))]
    out: dict[str, Any] = {"n": len(pts), "buckets": []}
    for lo, hi in zip(PRED_EDGES, PRED_EDGES[1:]):
        part = [(p, y) for p, y in pts if lo <= p < hi]
        lab = (f"< {hi:+.2f}" if lo < -1e9 else f">= {lo:+.2f}" if hi > 1e9 else f"{lo:+.2f} .. {hi:+.2f}")
        out["buckets"].append({"bucket": lab, "n": len(part),
                               "predicted": _r(sum(p for p, _ in part) / len(part)) if part else None,
                               "realized": _r(sum(y for _, y in part) / len(part)) if part else None,
                               "win_rate": _r(sum(1 for _, y in part if y > 0) / len(part), 3) if part else None})
    if len(pts) >= 10:
        xs, ys = [p for p, _ in pts], [y for _, y in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        syy = sum((y - my) ** 2 for y in ys)
        out["slope"] = _r(sxy / sxx) if sxx > 0 else None
        out["pearson"] = _r(sxy / math.sqrt(sxx * syy)) if sxx > 0 and syy > 0 else None
        out["spearman"] = _r(_spearman(xs, ys))
        out["mean_predicted"], out["mean_realized"] = _r(mx), _r(my)
        passed = [y for p, y in pts if p >= 0.05]
        refused = [y for p, y in pts if p < 0.05]
        out["passed_realized"] = _r(sum(passed) / len(passed)) if passed else None
        out["refused_realized"] = _r(sum(refused) / len(refused)) if refused else None
        out["passed_n"], out["refused_n"] = len(passed), len(refused)
    return out


def _ranks(v: Sequence[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    ranks = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None
