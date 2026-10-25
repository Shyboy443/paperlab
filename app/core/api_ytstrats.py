"""YouTube-ranking strategies (docs/YT_STRATEGIES_STUDY.md): the pre-registered crypto test of the PDF's claims.
Public, GET-only, read-only: historical results from app/ytstrats/results.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/public")
RESULTS = Path(__file__).resolve().parents[1] / "ytstrats" / "results.json"


def load() -> dict[str, Any]:
    try:
        return json.loads(RESULTS.read_text())
    except (OSError, ValueError):
        return {}


@router.get("/yt-strategies")
async def strategies(trades: bool = False) -> dict[str, Any]:
    d = load()
    if not d:
        raise HTTPException(503, "the study has not been run on this server yet")
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_y2_trades"} for r in d.get("strategies", [])]
    return {**{k: v for k, v in d.items() if k != "strategies"}, "strategies": rows,
            "proven": [r["key"] for r in rows if r["verdict"].startswith("PROVEN")], "read_only": True, "historical_only": True}


GOLD = Path(__file__).resolve().parents[1] / "ytstrats" / "gold_results.json"


@router.get("/gold-scalp")
async def gold_scalp() -> dict[str, Any]:
    """The operator's "Gold Scalping Strategy (1-Min TF)" PDF tested on XAUUSD only (docs/GOLD_SCALP_STUDY.md)."""
    try:
        d = json.loads(GOLD.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the gold study has not been run on this server yet") from None
    return {**d, "pairs": [{k: v for k, v in p.items()} for p in d.get("pairs", [])], "read_only": True, "historical_only": True}


SCRIBD = Path(__file__).resolve().parents[1] / "ytstrats" / "scribd_results.json"


@router.get("/scribd-strategies")
async def scribd_strategies(trades: bool = False) -> dict[str, Any]:
    """The Scribd scalping strategies' pre-registered test (docs/SCRIBD_SCALPING_STUDY.md)."""
    try:
        d = json.loads(SCRIBD.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("strategies", [])]
    return {**{k: v for k, v in d.items() if k != "strategies"}, "strategies": rows,
            "passed": [r["key"] for r in rows if r["verdict"].startswith("PASS")], "read_only": True, "historical_only": True}


SWING = Path(__file__).resolve().parents[1] / "ytstrats" / "swing_results.json"


@router.get("/scribd-swing")
async def scribd_swing(trades: bool = False) -> dict[str, Any]:
    """Scribd round 2: swing, trend and session-breakout strategies (docs/SCRIBD_SWING_STUDY.md)."""
    try:
        d = json.loads(SWING.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd swing study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}


ROUND3 = Path(__file__).resolve().parents[1] / "ytstrats" / "round3_results.json"


@router.get("/scribd-round3")
async def scribd_round3(trades: bool = False) -> dict[str, Any]:
    """Scribd round 3: NR7, IBS, the SPY intraday-momentum paper and a Bitcoin thesis (docs/SCRIBD_ROUND3_STUDY.md)."""
    try:
        d = json.loads(ROUND3.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd round 3 study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}


ROUND4 = Path(__file__).resolve().parents[1] / "ytstrats" / "round4_results.json"


@router.get("/scribd-round4")
async def scribd_round4(trades: bool = False) -> dict[str, Any]:
    """Scribd round 4: monthly momentum / rotation systems and ibs_qqq's leverage sensitivity (docs/SCRIBD_ROUND4_STUDY.md)."""
    try:
        d = json.loads(ROUND4.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd round 4 study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}


SCALP2 = Path(__file__).resolve().parents[1] / "ytstrats" / "scalp2_results.json"


@router.get("/scribd-scalp2")
async def scribd_scalp2(trades: bool = False) -> dict[str, Any]:
    """Scribd scalping round 2: new scalping documents on six years of 1-minute data (docs/SCRIBD_SCALPING_ROUND2_STUDY.md)."""
    try:
        d = json.loads(SCALP2.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd scalping round 2 study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}


ROUND6 = Path(__file__).resolve().parents[1] / "ytstrats" / "round6_results.json"


@router.get("/scribd-round6")
async def scribd_round6(trades: bool = False) -> dict[str, Any]:
    """Scribd round 6: the index mean-reversion family out of sample (docs/SCRIBD_ROUND6_STUDY.md)."""
    try:
        d = json.loads(ROUND6.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd round 6 study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}


ROUND7 = Path(__file__).resolve().parents[1] / "ytstrats" / "round7_results.json"


@router.get("/scribd-round7")
async def scribd_round7(trades: bool = False) -> dict[str, Any]:
    """Scribd round 7: volatility-ETF shorts, calendar seasonality and the lunar effect (docs/SCRIBD_ROUND7_STUDY.md)."""
    try:
        d = json.loads(ROUND7.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "the Scribd round 7 study has not been run on this server yet") from None
    rows = [r if trades else {k: v for k, v in r.items() if k != "recent_b_trades"} for r in d.get("rules", [])]
    return {**{k: v for k, v in d.items() if k != "rules"}, "rules": rows,
            "passed": [r["key"] for r in rows if r["verdict"] == "PASS"], "read_only": True, "historical_only": True}
