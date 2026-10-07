"""Display names for the paper bots: a name and a personality instead of "V8.3-ENA-5M". Display only -- nothing in a
trading path reads this, so renaming never touches a frozen experiment.

Every bot is named after a League of Legends champion (the naming the user picked in the Lovable dashboard). A CONTROL
bot's champion is picked exactly as that dashboard did: an FNV-1a hash of "<program>--<control key>" into NAMES, probed
past collisions with the controls taken in sorted order, so a bot keeps its champion across restarts and deploys and
matches what the Lovable app showed. Its twins share it: "+JEV" is "<name> AI" (Jev may veto its trades) and "+LADDER"
is "<name> Ladder" (takes profit in three steps); a V14 copy is "<the original's name> HTF". The personality comes
from the strategy family; the coin and timeframe stay visible next to the name.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

NAMES = (  # League of Legends champions, in the Lovable dashboard's order (the order decides the hash slots)
    "Aatrox", "Ahri", "Akali", "Akshan", "Alistar", "Ambessa", "Amumu", "Anivia", "Annie", "Aphelios", "Ashe",
    "Aurelion Sol", "Aurora", "Azir", "Bard", "Bel'Veth", "Blitzcrank", "Brand", "Braum", "Briar", "Caitlyn",
    "Camille", "Cassiopeia", "Cho'Gath", "Corki", "Darius", "Diana", "Dr. Mundo", "Draven", "Ekko", "Elise",
    "Evelynn", "Ezreal", "Fiddlesticks", "Fiora", "Fizz", "Galio", "Gangplank", "Garen", "Gnar", "Gragas", "Graves",
    "Gwen", "Hecarim", "Heimerdinger", "Hwei", "Illaoi", "Irelia", "Ivern", "Janna", "Jarvan IV", "Jax", "Jayce",
    "Jhin", "Jinx", "K'Sante", "Kai'Sa", "Kalista", "Karma", "Karthus", "Kassadin", "Katarina", "Kayle", "Kayn",
    "Kennen", "Kha'Zix", "Kindred", "Kled", "Kog'Maw", "LeBlanc", "Lee Sin", "Leona", "Lillia", "Lissandra",
    "Lucian", "Lulu", "Lux", "Malphite", "Malzahar", "Maokai", "Master Yi", "Mel", "Milio", "Miss Fortune",
    "Mordekaiser", "Morgana", "Naafiri", "Nami", "Nasus", "Nautilus", "Neeko", "Nidalee", "Nilah", "Nocturne",
    "Nunu", "Olaf", "Orianna", "Ornn", "Pantheon", "Poppy", "Pyke", "Qiyana", "Quinn", "Rakan", "Rammus", "Rek'Sai",
    "Rell", "Renata", "Renekton", "Rengar", "Riven", "Rumble", "Ryze", "Samira", "Sejuani", "Senna", "Seraphine",
    "Sett", "Shaco", "Shen", "Shyvana", "Singed", "Sion", "Sivir", "Skarner", "Smolder", "Sona", "Soraka", "Swain",
    "Sylas", "Syndra", "Tahm Kench", "Taliyah", "Talon", "Taric", "Teemo", "Thresh", "Tristana", "Trundle",
    "Tryndamere", "Twisted Fate", "Twitch", "Udyr", "Urgot", "Varus", "Vayne", "Veigar", "Vel'Koz", "Vex", "Vi",
    "Viego", "Viktor", "Vladimir", "Volibear", "Warwick", "Wukong", "Xayah", "Xerath", "Xin Zhao", "Yasuo", "Yone",
    "Yorick", "Yuumi", "Zac", "Zed", "Zeri", "Ziggs", "Zilean", "Zoe", "Zyra")

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


def control_of(key: str) -> tuple[str, str]:
    """(control key, role) of a bot key."""
    for role in ("JEV", "LADDER"):
        if key.endswith("+" + role):
            return key[: -len(role) - 1], role
    return key, "CONTROL"


def _fnv1a(text: str) -> int:
    """32-bit FNV-1a over UTF-16 code units: the same number the Lovable dashboard's JavaScript hash gives."""
    h = 2166136261
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = ((h ^ (data[i] | data[i + 1] << 8)) * 16777619) & 0xFFFFFFFF
    return h


def assign(controls: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """(program, control key) -> a unique champion name, stable for a given field.

    Controls claim names in sorted "<program>--<key>" order, each at its hash slot or the next free one. V14 copies
    take part in the claiming (as the Lovable app's did), but are shown as "<the original's name> HTF"."""
    taken: set[str] = set()
    out: dict[tuple[str, str], str] = {}
    for prog, key in sorted(set(controls), key=lambda pk: f"{pk[0]}--{pk[1]}"):
        i = _fnv1a(f"{prog}--{key}") % len(NAMES)
        for step in range(len(NAMES)):
            name = NAMES[(i + step) % len(NAMES)]
            if name not in taken:
                break
        else:                                         # more bots than champions: number them
            name = f"{NAMES[i]} {len(taken) + 1}"
        taken.add(name)
        out[(prog, key)] = name
    for prog, key in [pk for pk in out if pk[0] == "v14"]:       # V14: "<the original's name> HTF"
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
