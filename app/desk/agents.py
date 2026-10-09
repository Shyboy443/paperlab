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
- Report only what has happened: never say something "is being re-run", "will be checked" or "is coming" unless the DATA shows it.
- Write like a sharp trader on a desk chat: plain text only (no Markdown: no **, no #, no bullets), no disclaimers, at most {words} words.
- This is a team: when a colleague's message is in the DATA (scout_said, colleagues, skeptic_said, ...), react to it by name -- agree, push back or add to it ("Scout's right that...", "Charts, your stop...").
- This is research on paper; never tell anyone to trade real money."""

ROLES = {
    "scout": """Your job: from the scan, post the top 3 movers by unusual volume. For each: coin, price, 24h change, 1h and 4h relative volume, RSI, trend, and any flags. One line per coin, then one sentence on which looks most interesting and why. Relative volume below 1.5 is NOT unusual: if no coin is above it, say the market is quiet.""",
    "news": """Your job: explain WHY the coins in focus are moving, using only the headlines in the DATA block (cite the outlet). If no headline explains a coin's move, say so plainly ("no news; likely flow/technical"). Mention any market-wide headline that matters.""",
    "sentiment": """Your job: read the crowd for the coins in focus from the positioning numbers (funding_pct_longs_pay: positive means longs pay shorts, negative means shorts pay longs; % of accounts long and its 24h change, open-interest change), CoinGecko trending rank and any posts in the DATA block. Say whether the crowd is crowded long, crowded short or neutral, and whether that supports or fights a move. Ignore hype; spam/paid-signal posts were already removed.""",
    "charts": """Your job: propose up to {max_plans} trade plans, each on a DIFFERENT coin, for the best setups on ANY coin in the DATA (coins_in_focus or other_coins), best first -- or say there is no setup. Read what Scout, News and Sentiment said and use it. Look at every coin: a clean trend pullback, a breakdown or a bounce off a real level all count.
Rules for a plan:
- entry: "market" ONLY if the setup is valid right now; if your thesis needs a retest, bounce or rejection at a level first, use a LIMIT entry at that level (it waits up to 24 h);
- stop: just beyond a REAL swing level (a clear gap past it, not on it) listed in the DATA (1h or 4h support for a long, resistance for a short) -- name that level as stop_level -- at least one 1h ATR away from the entry (so normal noise does not hit it), and NOT on a round number (round numbers are listed);
- target: a real level in the trade's direction -- the next one or a FURTHER one (4h levels make good swing targets); reward:risk must be at least {min_rr} measured from entry to stop and target;
- ALWAYS run check_plan on every plan before you answer and fix it until it passes; drop plans that cannot pass; never quote a reward:risk you did not get from check_plan.
Answer with ONLY a JSON object, no prose around it:
{{"plans": [{{"coin": "SOL", "side": "long", "entry": "market", "stop": 0, "stop_level": 0, "target": 0, "timeframe": "4h", "why": "one or two sentences with the numbers"}}]}}
or, if nothing qualifies: {{"plans": [], "why": "the reason, with numbers"}}""",
    "skeptic": """Your job: argue hard against each plan. Look for: a market entry that contradicts the plan's own reasoning (it says "wait for a rejection" but buys now), news risk, a crowded trade, a stop inside noise (compare stop distance with ATR), a target past a real level, trading against the 4h trend, low volume, overbought/oversold traps. Be specific with numbers from the DATA, and judge each plan on its own merits -- a weak plan does not sink a good one. You MUST end with exactly one verdict line PER PLAN, using its coin (a plan without a verdict line is skipped):
VERDICT SOL: BUY   (take this plan as written, long or short)
VERDICT SOL: WAIT  (not now; say what would change your mind)
VERDICT SOL: SKIP  (reject it) -- and if you SKIP for a clear deal-breaker, add the line DEAL-BREAKER SOL: <one sentence> before it
If there is no plan, say why waiting is right and end with VERDICT: SKIP.""",
    "chief": """Your job: you ran this session. In a short message, summarise each colleague's key point in one line each (Scout, News, Sentiment, Charts, Skeptic -- including how the Charts/Skeptic debate ended), then announce the DECISION exactly as given in the DATA (you cannot change it) and explain it in one or two sentences. Finish your last sentence.""",
    "charts_reply": """Your job now: the Skeptic has attacked your plans. Answer the Skeptic by name in under 110 words: concede the points that are right, defend the ones that are wrong with numbers from the DATA, or revise a plan (re-test every change with check_plan). Then end with the final plans as one JSON object on its own: {{"plans": [...]}} -- keep, revise or drop each plan (an empty list withdraws them all).""",
    "dm": """The desk's operator is talking to you (directly, or by @mentioning you in the group chat). Answer in your role and DO the tasks they ask, with your tools:
- data: scan_coin, scan_universe, news, sentiment, ledger -- call them whenever numbers are needed; never invent numbers;
- check_plan: run the desk's code checks on a trade plan before proposing it;
- actions (paper money only): run_session, open_trade, close_trade, update_settings;
- colleagues: ask_agent asks another desk agent (Scout, News, Sentiment, Charts, Skeptic, Chief) and their answer is posted in this chat -- use it when the operator wants the team's view, or when a question belongs to a colleague (levels -> Charts, headlines -> News, crowd -> Sentiment, risks -> Skeptic), then build on what they said.
Take an action ONLY when the operator's latest message explicitly asks for it (e.g. "open a long on SOL", "close #3", "switch to aggressive", "run a session"); otherwise suggest it and ask. For open_trade, base the stop and target on real levels from scan_coin and say what you set. After any action, confirm exactly what the tool reported (trade id, prices, risk) or the error it gave. If a tool fails, say so plainly.""",
}

WORDS = {"scout": 150, "news": 160, "sentiment": 130, "charts": 160, "charts_reply": 130, "skeptic": 220, "chief": 200,
         "dm": 180}


def system_prompt(agent: str, min_rr: float = 2.0, dm: bool = False, max_plans: int = 1) -> str:
    """`agent` is an agent id, or a session role of one ("charts_reply" is Charts answering the Skeptic)."""
    a = AGENTS[agent.split("_")[0]]
    role = ROLES["dm"] + "\nYour role on the desk: " + a["role"] + "." if dm else \
        ROLES[agent].format(min_rr=min_rr, max_plans=max_plans)
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
        min_gap = max(0.1 * atr1, 0.0005 * price)
        if gap <= 0:
            problems.append(f"the stop must sit beyond the level {lvl} ({'below' if long_ else 'above'} it)")
        elif gap < min_gap:
            problems.append(f"the stop is only {gap:.6g} beyond the level {lvl}; it needs at least {min_gap:.6g} "
                            "(0.1 x 1h ATR) so a touch of the level does not stop it out")
        elif atr1 and gap > 0.75 * atr1:
            problems.append(f"the stop is {gap:.6g} beyond the level, more than 0.75 x 1h ATR -- not 'just beyond'")
    if atr1 and abs(entry - plan.stop) < atr1 * 0.999:
        problems.append(f"the stop is {abs(entry - plan.stop):.6g} from entry, inside one 1h ATR ({atr1:.6g}): normal noise would hit it")
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
def parse_plans(text: str) -> list[Plan] | None:
    """Charts' plans: {"plans": [...]} (or one bare plan object, or {"coin": null}). None if unreadable. A plan entry
    that cannot be read is skipped; an empty list means "no setup"."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if "plans" not in d:
        one = parse_plan(m.group(0))
        return None if one is None else ([] if one.coin is None else [one])
    out, seen = [], set()
    for item in d.get("plans") or []:
        pl = parse_plan(json.dumps(item)) if isinstance(item, dict) else None
        if pl is not None and pl.coin and pl.coin not in seen:
            seen.add(pl.coin)
            out.append(pl)
    return out


def unreadable_plans(text: str) -> list[dict[str, Any]]:
    """Plan entries in Charts' JSON that cannot be used (a missing or non-numeric field)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    items = d.get("plans") if isinstance(d, dict) and isinstance(d.get("plans"), list) else []
    return [i for i in items if isinstance(i, dict) and i.get("coin") and parse_plan(json.dumps(i)) is None]


def parse_verdicts(text: str, coins: Sequence[str]) -> dict[str, tuple[str | None, str | None]]:
    """Per-coin (verdict, deal-breaker) from "VERDICT SOL: BUY" / "DEAL-BREAKER SOL: ..." lines; a bare "VERDICT: X"
    applies to every coin without its own line."""
    out: dict[str, tuple[str | None, str | None]] = {}
    bare, bare_breaker = parse_verdict(text)
    for coin in coins:
        v = re.findall(rf"VERDICT\s+\**{re.escape(coin)}\**\s*:\s*\**\s*(BUY|WAIT|SKIP)\b", text or "", re.I)
        b = re.findall(rf"DEAL-BREAKER\s+\**{re.escape(coin)}\**\s*:\s*(.+)", text or "", re.I)
        out[coin] = (v[-1].upper() if v else bare,
                     b[-1].strip(" *") if b and b[-1].strip(" *") else (bare_breaker if not v else None))
    return out


def parse_verdict(text: str) -> tuple[str | None, str | None]:
    """(BUY | WAIT | SKIP | None, deal-breaker sentence or None) from the Skeptic's last VERDICT line."""
    verdicts = re.findall(r"VERDICT\s*:\s*\**\s*(BUY|WAIT|SKIP)\b", text or "", re.I)
    breaker = re.findall(r"DEAL-BREAKER\s*:\s*(.+)", text or "", re.I)
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


ACTION_TOOLS = [
    {"type": "function", "function": {"name": "check_plan", "description": "Run the desk's code checks (real swing level, stop just beyond it, not a round number, reward:risk) on a plan.",
                                      "parameters": {"type": "object", "properties": {
                                          "coin": {"type": "string"}, "side": {"type": "string", "enum": ["long", "short"]},
                                          "entry": {"description": "a price, or the string market"}, "stop": {"type": "number"},
                                          "stop_level": {"type": "number"}, "target": {"type": "number"}},
                                          "required": ["coin", "side", "entry", "stop", "stop_level", "target"]}}},
    {"type": "function", "function": {"name": "run_session", "description": "Start a full desk session now (all six agents, may open a trade).",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "open_trade", "description": "Open a PAPER trade in the desk ledger (sized to the risk rule, max 3 open, one per coin). Only when the operator explicitly asks.",
                                      "parameters": {"type": "object", "properties": {
                                          "coin": {"type": "string"}, "side": {"type": "string", "enum": ["long", "short"]},
                                          "entry": {"description": "a limit price, or the string market"}, "stop": {"type": "number"},
                                          "target": {"type": "number"}, "reason": {"type": "string"}},
                                          "required": ["coin", "side", "entry", "stop", "target"]}}},
    {"type": "function", "function": {"name": "close_trade", "description": "Close an open PAPER trade at the market price now, or cancel a pending limit. Only when the operator explicitly asks.",
                                      "parameters": {"type": "object", "properties": {"trade_id": {"type": "integer"}}, "required": ["trade_id"]}}},
    {"type": "function", "function": {"name": "update_settings", "description": "Change desk settings when the operator explicitly asks: mode (cautious/aggressive), schedule_hours (1,2,4,6,8,12,24), universe (list of tickers), risk_pct (0.25-5), enabled (scheduled sessions on/off).",
                                      "parameters": {"type": "object", "properties": {
                                          "mode": {"type": "string", "enum": ["cautious", "aggressive"]}, "schedule_hours": {"type": "integer"},
                                          "universe": {"type": "array", "items": {"type": "string"}}, "risk_pct": {"type": "number"},
                                          "enabled": {"type": "boolean"}, "max_plans": {"type": "integer"},
                                          "max_open": {"type": "integer"}}}}},
]


COLLAB_TOOLS = [
    {"type": "function", "function": {"name": "ask_agent", "description": "Ask a colleague on the desk a question; their answer is posted in this chat and returned to you.",
                                      "parameters": {"type": "object", "properties": {
                                          "agent": {"type": "string", "enum": list(AGENTS)}, "question": {"type": "string"}},
                                          "required": ["agent", "question"]}}},
]


def mention(text: str) -> str | None:
    """The agent an operator message @mentions first (by id or name), if any."""
    for m in re.finditer(r"@(\w+)", text or ""):
        word = m.group(1).lower()
        for aid, a in AGENTS.items():
            if word in (aid, a["name"].lower()):
                return aid
    return None


def decision_log(session: dict[str, Any], messages: Sequence[dict[str, Any]]) -> str:
    """The session's Markdown decision log."""
    lines = [f"# Desk session {session['id']}", "",
             f"- Mode: {session.get('mode')}", f"- Coin: {session.get('coin') or '-'}",
             f"- Skeptic verdict: {session.get('verdict') or '-'}", f"- Decision: {session.get('decision') or '-'}", ""]
    for m in messages:
        who = AGENTS.get(m["author"], {}).get("name", m["author"])
        lines += [f"## {who}", "", m["text"].strip(), ""]
    return "\n".join(lines)
