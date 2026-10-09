"""The six Trading Desk agents: who they are, what they are told, and the code checks the LLM cannot talk its way past.

The session flow is fixed and run by the Chief's orchestrator (app/desk/service.py); each agent gets the REAL data it
needs in a DATA block (fetched by the desk's own tools), and must quote only numbers from that block. Charts answers in
JSON so its plan can be checked in code; the Skeptic must end with one verdict line; the Chief's trade gate is code
(`decide`), the Chief's LLM only writes the explanation.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.desk import indicators as ind

AGENTS: dict[str, dict[str, str]] = {
    "chief": {"name": "Chief", "color": "#f2b24c", "initials": "CH", "role": "Runs the session and makes the call"},
    "scout": {"name": "Scout", "color": "#4cb5f2", "initials": "SC", "role": "Finds coins moving on unusual volume"},
    "news": {"name": "News", "color": "#a78bfa", "initials": "NW", "role": "Explains why they are moving"},
    "sentiment": {"name": "Sentiment", "color": "#f472b6", "initials": "SE", "role": "Reads the crowd's mood and positioning"},
    "charts": {"name": "Charts", "color": "#34d399", "initials": "CT", "role": "Plans entry, stop and target"},
    "skeptic": {"name": "Skeptic", "color": "#f87171", "initials": "SK", "role": "Argues against every plan"},
}

COMMON = """You are {name} on PaperLab's Trading Desk, a group chat of six AI agents who paper-trade crypto (simulated money only).
House rules:
- Use ONLY numbers and facts that appear in the DATA block (or in tool results). Never invent a price, level, percentage, headline or statistic. If something you would want is missing, say it is not available.
- Quote prices with the precision given. Name the timeframe (1h / 4h) when you cite an indicator.
- Write like a sharp trader on a desk chat: plain sentences, no headings, no disclaimers, at most {words} words.
- This is research on paper; never tell anyone to trade real money."""

ROLES = {
    "scout": """Your job: from the scan, post the top 3 movers by unusual volume. For each: coin, price, 24h change, 1h and 4h relative volume, RSI, trend, and any flags. One line per coin, then one sentence on which looks most interesting and why. Relative volume below 1.5 is NOT unusual: if no coin is above it, say the market is quiet.""",
    "news": """Your job: explain WHY the coins in focus are moving, using only the headlines in the DATA block (cite the outlet). If no headline explains a coin's move, say so plainly ("no news; likely flow/technical"). Mention any market-wide headline that matters.""",
    "sentiment": """Your job: read the crowd for the coins in focus from the positioning numbers (funding rate, % of accounts long and its 24h change, open-interest change), CoinGecko trending rank and any posts in the DATA block. Say whether the crowd is crowded long, crowded short or neutral, and whether that supports or fights a move. Ignore hype; spam/paid-signal posts were already removed.""",
    "charts": """Your job: propose ONE trade plan for the best candidate among the coins in focus, or say there is no setup.
Rules for a plan:
- entry: "market" or a limit price near the current price;
- stop: just beyond a REAL swing level listed in the DATA (1h or 4h support for a long, resistance for a short) -- name that level as stop_level -- and NOT on a round number (round numbers are listed);
- target: at or before the next real level in the trade's direction; reward:risk must be at least {min_rr} measured from entry to stop and target.
Answer with ONLY a JSON object, no prose around it:
{{"coin": "SOL", "side": "long", "entry": "market", "stop": 0, "stop_level": 0, "target": 0, "timeframe": "4h", "why": "one or two sentences with the numbers"}}
or, if nothing qualifies: {{"coin": null, "why": "the reason, with numbers"}}""",
    "skeptic": """Your job: argue hard against the plan. Look for: news risk, a crowded trade, a stop inside noise (compare stop distance with ATR), a target past a real level, trading against the 4h trend, low volume, overbought/oversold traps. Be specific with numbers from the DATA. Then end with EXACTLY one final line:
VERDICT: BUY   (take the plan as written, long or short)
VERDICT: WAIT  (not now; say what would change your mind)
VERDICT: SKIP  (reject it) -- and if you SKIP for a clear deal-breaker, add a line before the verdict: DEAL-BREAKER: <one sentence>
If there is no plan, say why waiting is right and end with VERDICT: SKIP.""",
    "chief": """Your job: you ran this session. In a short message, summarise each colleague's key point in one line each (Scout, News, Sentiment, Charts, Skeptic), then announce the DECISION exactly as given in the DATA (you cannot change it) and explain it in one or two sentences.""",
    "dm": """A user is messaging you directly. Answer their question in your role, using your tools to fetch real data when numbers are needed. Never invent numbers; if a tool fails, say so.""",
}

WORDS = {"scout": 150, "news": 140, "sentiment": 130, "charts": 120, "skeptic": 170, "chief": 170, "dm": 180}


def system_prompt(agent: str, min_rr: float = 2.0, dm: bool = False) -> str:
    a = AGENTS[agent]
    role = ROLES["dm"] + "\nYour role on the desk: " + a["role"] + "." if dm else ROLES[agent].format(min_rr=min_rr)
    return COMMON.format(name=a["name"], words=WORDS["dm" if dm else agent]) + "\n\n" + role


# -- compact data for prompts --------------------------------------------------------------------------------------------
def _r(x: Any, price: float | None = None) -> Any:
    """Round to a precision that keeps every meaningful digit of a price (5 significant figures)."""
    if not isinstance(x, (int, float)) or isinstance(x, bool) or x is None or not math.isfinite(x):
        return x
    if x == 0:
        return 0
    ref = abs(price) if price else abs(x)
    digits = max(0, 4 - int(math.floor(math.log10(ref))))
    return round(x, min(digits, 8))


def compact_scan(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("ok"):
        return {"coin": row.get("coin"), "error": row.get("reason")}
    p = row["price"]

    def frame(f: dict[str, Any]) -> dict[str, Any]:
        return {"close": _r(f["close"], p), "rsi14": round(f["rsi14"], 1) if f["rsi14"] is not None else None,
                "ema20": _r(f["ema20"], p), "ema50": _r(f["ema50"], p), "trend": f["trend"], "atr14": _r(f["atr14"], p),
                "rel_volume": round(f["rel_volume"], 2) if f["rel_volume"] is not None else None,
                "support": [_r(x, p) for x in f["levels"]["support"]], "resistance": [_r(x, p) for x in f["levels"]["resistance"]]}
    return {"coin": row["coin"], "price": _r(p, p), "change_24h_pct": round(row["change_24h_pct"], 2)
            if row.get("change_24h_pct") is not None else None, "1h": frame(row["h1"]), "4h": frame(row["h4"]),
            "round_numbers": {k: [_r(x, p) for x in v] for k, v in row["round_numbers"].items()},
            "flags": row["flags"], "data_source": row.get("source")}


def data_block(obj: Any) -> str:
    return "DATA:\n" + json.dumps(obj, default=str, separators=(",", ":"))


# -- Charts' plan --------------------------------------------------------------------------------------------------------
@dataclass
class Plan:
    coin: str | None
    side: str = ""
    entry: Any = None
    stop: float = 0.0
    stop_level: float = 0.0
    target: float = 0.0
    timeframe: str = ""
    why: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def parse_plan(text: str) -> Plan | None:
    """The first JSON object in Charts' answer (a code fence or stray prose around it is tolerated)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if not d.get("coin"):
        return Plan(coin=None, why=str(d.get("why") or ""), raw=d)
    try:
        entry = d.get("entry")
        entry = "market" if isinstance(entry, str) and entry.strip().lower() == "market" else float(entry)
        return Plan(coin=str(d["coin"]).upper().replace("USDT", ""), side=str(d.get("side") or "").lower(), entry=entry,
                    stop=float(d.get("stop")), stop_level=float(d.get("stop_level") or 0), target=float(d.get("target")),
                    timeframe=str(d.get("timeframe") or ""), why=str(d.get("why") or ""), raw=d)
    except (TypeError, ValueError):
        return None


def validate_plan(plan: Plan, row: dict[str, Any], price: float, min_rr: float) -> tuple[bool, list[str], dict[str, Any]]:
    """Code checks on a plan against the coin's real scan. Returns (ok, problems, facts)."""
    problems: list[str] = []
    if plan.side not in ("long", "short"):
        return False, ["side must be long or short"], {}
    entry = price if plan.entry == "market" else float(plan.entry)
    h1, h4 = row["h1"], row["h4"]
    atr1 = h1["atr14"] or 0.0
    long_ = plan.side == "long"
    if plan.entry != "market":
        if long_ and entry > price * 1.001:
            problems.append(f"a long limit entry ({entry}) must be at or below the price ({price})")
        if not long_ and entry < price * 0.999:
            problems.append(f"a short limit entry ({entry}) must be at or above the price ({price})")
        if atr1 and abs(entry - price) > 1.5 * atr1:
            problems.append(f"limit entry is more than 1.5 x 1h ATR ({atr1:.6g}) from the price")
    if long_ and not (plan.stop < entry < plan.target):
        problems.append("a long needs stop < entry < target")
    if not long_ and not (plan.target < entry < plan.stop):
        problems.append("a short needs target < entry < stop")
    side_levels = [*h1["levels"]["support"], *h4["levels"]["support"]] if long_ else \
                  [*h1["levels"]["resistance"], *h4["levels"]["resistance"]]
    tol = max(price * 0.002, 0.05 * atr1)
    real = [x for x in side_levels if abs(x - plan.stop_level) <= tol]
    if not real:
        problems.append(f"stop_level {plan.stop_level} is not one of the real swing levels {[round(x, 8) for x in side_levels]}")
    else:
        lvl = real[0]
        gap = (lvl - plan.stop) if long_ else (plan.stop - lvl)
        if gap <= 0:
            problems.append(f"the stop must sit beyond the level {lvl} ({'below' if long_ else 'above'} it)")
        elif atr1 and gap > 0.75 * atr1:
            problems.append(f"the stop is {gap:.6g} beyond the level, more than 0.75 x 1h ATR -- not 'just beyond'")
    if ind.near_round(plan.stop, price):
        problems.append(f"the stop {plan.stop} sits on a round number")
    risk = abs(entry - plan.stop)
    reward = abs(plan.target - entry)
    rr = reward / risk if risk > 0 else 0.0
    if rr + 1e-9 < min_rr:
        problems.append(f"reward:risk {rr:.2f} is below the minimum {min_rr}")
    atr4 = h4["atr14"] or 0.0
    if atr4 and reward > 6 * atr4:
        problems.append(f"target is more than 6 x 4h ATR away")
    facts = {"entry": entry, "risk_per_unit": risk, "rr": round(rr, 2), "stop_pct": 100 * risk / entry if entry else None,
             "stop_atr1h": round(risk / atr1, 2) if atr1 else None}
    return not problems, problems, facts


# -- Skeptic + decision --------------------------------------------------------------------------------------------------
def parse_verdict(text: str) -> tuple[str | None, str | None]:
    """(BUY | WAIT | SKIP | None, deal-breaker sentence or None) from the Skeptic's last VERDICT line."""
    verdicts = re.findall(r"VERDICT:\s*\**\s*(BUY|WAIT|SKIP)\b", text or "", re.I)
    breaker = re.findall(r"DEAL-BREAKER:\s*(.+)", text or "", re.I)
    return (verdicts[-1].upper() if verdicts else None,
            breaker[-1].strip().strip("*").strip() if breaker and breaker[-1].strip(" *") else None)


MIN_RR = {"cautious": 2.0, "aggressive": 1.5}


def decide(mode: str, plan_ok: bool, verdict: str | None, deal_breaker: str | None) -> tuple[bool, str]:
    """The Chief's trade gate. Cautious: only on a Skeptic BUY. Aggressive: unless the Skeptic SKIPs with a clear
    deal-breaker. A plan that failed the code checks never trades."""
    if not plan_ok:
        return False, "no valid plan"
    if verdict is None:
        return False, "the Skeptic gave no verdict"
    if mode == "aggressive":
        if verdict == "SKIP" and deal_breaker:
            return False, f"aggressive mode, but the Skeptic named a deal-breaker: {deal_breaker}"
        return True, f"aggressive mode: the Skeptic said {verdict}" + ("" if verdict == "BUY" else " without a deal-breaker")
    if verdict == "BUY":
        return True, "cautious mode: the Skeptic said BUY"
    return False, f"cautious mode: the Skeptic said {verdict}"


# -- tools for direct messages -----------------------------------------------------------------------------------------
TOOLS = [
    {"type": "function", "function": {"name": "scan_coin", "description": "Live 1h/4h indicators, levels and flags for one coin.",
                                      "parameters": {"type": "object", "properties": {"coin": {"type": "string"}}, "required": ["coin"]}}},
    {"type": "function", "function": {"name": "scan_universe", "description": "The whole coin universe ranked by unusual volume.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "news", "description": "Last 24h headlines naming a coin (or market-wide if coin is empty).",
                                      "parameters": {"type": "object", "properties": {"coin": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "sentiment", "description": "Funding, accounts long %, open-interest change, trending rank and posts for a coin.",
                                      "parameters": {"type": "object", "properties": {"coin": {"type": "string"}}, "required": ["coin"]}}},
    {"type": "function", "function": {"name": "ledger", "description": "The desk's paper ledger: equity, open and closed trades.",
                                      "parameters": {"type": "object", "properties": {}}}},
]


def decision_log(session: dict[str, Any], messages: Sequence[dict[str, Any]]) -> str:
    """The session's Markdown decision log."""
    lines = [f"# Desk session {session['id']}", "",
             f"- Mode: {session.get('mode')}", f"- Coin: {session.get('coin') or '-'}",
             f"- Skeptic verdict: {session.get('verdict') or '-'}", f"- Decision: {session.get('decision') or '-'}", ""]
    for m in messages:
        who = AGENTS.get(m["author"], {}).get("name", m["author"])
        lines += [f"## {who}", "", m["text"].strip(), ""]
    return "\n".join(lines)
