"""Display names for the paper bots: a name and a personality instead of "V8.3-ENA-5M". Display only -- nothing in a
trading path reads this, so renaming never touches a frozen experiment.

Every CONTROL bot gets a first name from NAMES, picked by a hash of (program, control key) and probed past collisions in
a fixed order, so a bot keeps its name across restarts and deploys. Its twins share it: "+JEV" is "<name> AI" (Jev may
veto its trades) and "+LADDER" is "<name> Ladder" (takes profit in three steps). The personality comes from the strategy
family; the coin and timeframe stay visible next to the name.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

NAMES = (
    "Atlas", "Nova", "Pixel", "Rocket", "Ziggy", "Maverick", "Echo", "Blaze", "Comet", "Orbit", "Sparky", "Nimbus",
    "Vector", "Jolt", "Zephyr", "Ember", "Quasar", "Dash", "Rogue", "Cosmo", "Bolt", "Nyx", "Juno", "Onyx", "Ripley",
    "Rex", "Luna", "Sable", "Titan", "Vega", "Kilo", "Mako", "Pulse", "Radar", "Sonic", "Tango", "Volt", "Wren", "Zola",
    "Axel", "Blip", "Circuit", "Dex", "Flux", "Glitch", "Hex", "Ion", "Jinx", "Kit", "Lumen", "Mox", "Neon", "Opal",
    "Pip", "Quill", "Rune", "Sprocket", "Trix", "Unit", "Viper", "Widget", "Xeno", "Yeti", "Zap", "Aria", "Bishop",
    "Cypher", "Delta", "Enzo", "Fable", "Gizmo", "Halo", "Iris", "Jett", "Kodiak", "Lark", "Mica", "Nero", "Oscar",
    "Pluto", "Quinn", "Rusty", "Scout", "Tesla", "Uma", "Vandal", "Whisky", "Xyla", "Yoshi", "Zara", "Apex", "Brick",
    "Cobalt", "Dune", "Fizz", "Gamma", "Hopper", "Indigo", "Jazz", "Karma", "Loki", "Monty", "Nugget", "Otto", "Pepper",
    "Quartz", "Riot", "Sly", "Turbo", "Umbra", "Vortex", "Wizz", "Xander", "Yuki", "Zest", "Boomer", "Crash", "Diesel")

# strategy id -> (personality, what it does)
PERSONA: dict[str, tuple[str, str]] = {
    "V6.1": ("the patient one", "buys pullbacks while traders lean the wrong way"),
    "V6.2": ("the momentum rider", "follows moves that open interest backs up"),
    "V6.3": ("the contrarian", "fades crowded funding"),
    "V6.4": ("the breakout hunter", "trades breakouts backed by fresh open interest"),
    "V6.5": ("the bottom picker", "buys forced selling after a flush"),
    "V6.6": ("the trend follower", "rides alt trends the market agrees with"),
    "V7.1": ("the dip buyer", "buys 15m pullbacks inside a trend"),
    "V7.2": ("the breakout chaser", "jumps on 15m volume breakouts"),
    "V8.1": ("the grinder", "scalps 5m pullbacks"),
    "V8.2": ("the sprinter", "scalps 5m breakouts"),
    "V8.3": ("the snapper", "buys 5m stretches back to the average price"),
    "V9.1": ("the grinder", "scalps 5m pullbacks on US stocks"),
    "V9.2": ("the sprinter", "scalps 5m breakouts on US stocks"),
    "V9.3": ("the snapper", "buys 5m stock stretches back to the average price"),
    "V11.1": ("the scout", "scans 30 coins for the strongest breakouts · takes profit 25% / 50% / 25%"),
    "V11.2": ("the stalker", "scans 30 coins for leaders dipping in a trend · takes profit 25% / 50% / 25%"),
    "V11.3": ("the crash catcher", "scans 30 coins for flushes to buy back · takes profit 25% / 50% / 25%"),
    "V11.4": ("the degen", "every hour fades the biggest movers of 30 coins · takes profit 25% / 50% / 25%"),
    "V12.1": ("the grinder bee", "one day breakout a day on ETH / SOL / HYPE · full size 2x · rides to the UTC close"),
    "V13.1": ("the rubber band", "rests limit orders where 29 coins stretch far from their average · pays maker fees"),
    "V14.1": ("the snapper, HTF", "V8.3's 5m snap-back, only with the 4h and daily trend"),
    "V14.2": ("the scout, HTF", "V11.1's breakout scanner, only with the 4h and daily trend"),
    "V14.3": ("the stalker, HTF", "V11.2's pullback scanner, only with the 4h and daily trend"),
    "V14.4": ("the rubber band, HTF", "V13's limit-order snapback, only with the 4h and daily trend"),
}
TWIN = {"JEV": (" AI", "Jev can veto or size up its trades"), "LADDER": (" Ladder", "takes profit in three steps")}
TWIN_BY_PROGRAM = {("v11", "JEV"): (" AI", "Jev can skip its trades, and its confidence sets how far the TPs reach"),
                   ("v12", "JEV"): (" AI", "Jev decides whether each breakout is real (GO) or not yet (WAIT), as in beebots")}
# bots that keep the name they came with (not hashed from NAMES): beebots' Bizzy Bee
FIXED: dict[tuple[str, str], str] = {("v12", "V12.1-DAY"): "Bizzy", ("v13", "V13.1-SNAP"): "Bounce"}
PROGRAM_ORDER = ("v6", "v7", "v8", "v9", "v11", "v12", "v13", "v14")


def control_of(key: str) -> tuple[str, str]:
    """(control key, role) of a bot key."""
    for role in ("JEV", "LADDER"):
        if key.endswith("+" + role):
            return key[: -len(role) - 1], role
    return key, "CONTROL"


def assign(controls: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """(program, control key) -> a unique first name, stable for a given field."""
    taken: set[str] = set(FIXED.values())
    out: dict[tuple[str, str], str] = {}
    order = sorted(set(controls), key=lambda pk: (PROGRAM_ORDER.index(pk[0]) if pk[0] in PROGRAM_ORDER else 99, pk[1]))
    htf = [pk for pk in order if pk[0] == "v14"]                  # named after their originals, below
    order = [pk for pk in order if pk[0] != "v14"]
    for prog, key in order:
        if (prog, key) in FIXED:
            out[(prog, key)] = FIXED[(prog, key)]
            continue
        i = int(hashlib.sha1(f"{prog}|{key}".encode()).hexdigest(), 16) % len(NAMES)
        for step in range(len(NAMES)):
            name = NAMES[(i + step) % len(NAMES)]
            if name not in taken:
                break
        else:                                         # more bots than names: number them
            name = f"{NAMES[i]} {len(taken) + 1}"
        taken.add(name)
        out[(prog, key)] = name
    for prog, key in htf:                                        # V14: "<the original's name> HTF"
        src = htf_original(key)
        out[(prog, key)] = (out.get(src) or ("Copy " + key.split("-")[0])) + " HTF" if src else key
    return out


def htf_original(key: str) -> tuple[str, str] | None:
    """(program, control key) of the original a V14 bot copies (V14.1-ARB-5M -> v8 V8.3-ARB-5M)."""
    try:
        from app.competition import v14_config as v14
        spec = next(s for s in v14.field_plan() if s.key == key)
        return v14.original_key(spec)
    except (StopIteration, Exception):
        return None


def describe(program: str, row: Mapping[str, object], names: Mapping[tuple[str, str], str]) -> dict[str, str]:
    """name, personality and a one-line job description for one bot row of a program's leaderboard."""
    key = str(row.get("key") or "")
    ctl, role = control_of(key)
    first = names.get((program, ctl)) or ctl
    sid = str(row.get("strategy_id") or key.split("-")[0])
    persona, job = PERSONA.get(sid, ("the bot", str(row.get("family") or "")))
    suffix, twin_job = TWIN_BY_PROGRAM.get((program, role)) or TWIN.get(role, ("", ""))
    coin = str(row.get("coin") or "")
    where = ("29 coins" if coin == "ALL" and (program == "v13" or sid == "V14.4") else "30 coins" if coin == "ALL"
             else "ETH · SOL · HYPE" if program == "v12" else coin)
    return {"name": first + suffix, "persona": persona, "job": job + (" · " + twin_job if twin_job else ""),
            "where": where, "timeframe": str(row.get("timeframe") or "")}
