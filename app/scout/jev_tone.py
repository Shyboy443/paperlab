"""Headline tone from Jev: up to 10 (headline, asset) pairs per call, each answered BEARISH / NEUTRAL / BULLISH with
probabilities. tone = P(BULLISH) - P(BEARISH), in -1 .. +1. A failed call leaves the pairs unscored (never guessed).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

BATCH = 10
PROMPT_VERSION = "JEV_SCOUT_TONE_V1"
CHOICES = ("BEARISH", "NEUTRAL", "BULLISH")


def questions_for(pairs: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"h{i}": {"type": "choice",
                      "instructions": (f"Headline h{i} in `headlines` is about {p['symbol']}. For {p['symbol']}'s price "
                                       "over the next few hours, is this news BEARISH, NEUTRAL or BULLISH? Judge the "
                                       "news itself; no price history is given."),
                      "criteria": {"BEARISH": "likely to push the price down (bad results, legal trouble, hacks, "
                                              "downgrades, outflows, weak demand)",
                                   "NEUTRAL": "routine, mixed, already-known or unrelated to the price",
                                   "BULLISH": "likely to push the price up (strong results, approvals, upgrades, "
                                              "inflows, adoption, big buyers)"}}
            for i, p in enumerate(pairs)}


def score(client: Any, pairs: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any] | None], float]:
    """Tones for the pairs (None where unanswered) and the call's cost in USD. `client` is a JevClient."""
    pairs = list(pairs)[:BATCH]
    state = {"task": "news tone for the named asset", "prompt_version": PROMPT_VERSION,
             "headlines": [{"id": f"h{i}", "asset": p["symbol"], "headline": str(p["headline"])[:300],
                            "summary": str(p.get("summary") or "")[:400]} for i, p in enumerate(pairs)]}
    out = client.decide(state, questions_for(pairs), parse=False)
    if not out.ok:
        return [None] * len(pairs), 0.0
    raw = out.extra.get("raw") or {}
    answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
    res: list[dict[str, Any] | None] = []
    for i in range(len(pairs)):
        a = answers.get(f"h{i}") or {}
        probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
        if a.get("choice") not in CHOICES or not probs:
            res.append(None)
            continue
        pb, pn = float(probs.get("BULLISH") or 0.0), float(probs.get("BEARISH") or 0.0)
        res.append({"choice": a["choice"], "tone": round(pb - pn, 4), "p_bull": pb, "p_bear": pn})
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return res, float(usage.get("cost") or 0.0)
