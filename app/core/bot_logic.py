"""A bot's trading logic as plain text, for the dashboard's "Copy logic" button: the bot's name and key, its program,
its strategy (thesis, rules, parameters, when it fails) and the source file it comes from.

The strategy is found from the bot key: the last "V<major>.<minor>" in it ("V8.3-ETH-5M" -> V8.3, "V17-BTC-V17.3" ->
V17.3, "V16-BTC-V6.6-CHOP" -> V6.6). V14 bots are run-time copies of other strategies with a higher-timeframe rule,
so they are described as the original plus that rule. Read-only; nothing here touches a running bot.
"""
from __future__ import annotations

import dataclasses
import functools
import importlib
import inspect
import pkgutil
import re
import textwrap
from typing import Any

PROGRAM_LABEL = {"v6": "V6 Forward Arena (hourly, Bybit positioning)", "v7": "V7 15-minute trend", "v8": "V8 Scalp",
                 "v9": "V9 US stocks", "v11": "V11 Scan (30 coins)", "v12": "V12 Bizzy", "v13": "V13 Snapback",
                 "v14": "V14 HTF copies", "v16": "V16 Conditional", "v17": "V17 Swing"}
PREFERRED = {"V8.1": "app.strategies.v8.arena", "V8.2": "app.strategies.v8.arena", "V8.3": "app.strategies.v8.arena"}
V14_ORIGINAL = {"V14.1": "V8.3", "V14.2": "V11.1", "V14.3": "V11.2", "V14.4": "V13.1"}
SKIP_ATTRS = {"id", "name", "thesis", "fails_when", "family", "version", "journal", "feed", "market", "Params", "tag",
              "expected_hold", "expected_frequency", "source", "hypothesis"}


@functools.lru_cache(maxsize=1)
def strategy_classes() -> dict[str, type]:
    """Every strategy class in app/strategies by its id (the live module wins where ids repeat)."""
    import app.strategies as S
    found: dict[str, type] = {}
    for m in pkgutil.walk_packages(S.__path__, "app.strategies."):
        try:
            mod = importlib.import_module(m.name)
        except Exception:
            continue
        for _, c in inspect.getmembers(mod, inspect.isclass):
            sid = getattr(c, "id", None)
            if not (isinstance(sid, str) and re.fullmatch(r"V\d+\.\d+", sid) and c.__module__ == mod.__name__):
                continue
            if sid not in found or PREFERRED.get(sid) == c.__module__:
                found[sid] = c
    return found


def strategy_id(key: str) -> str | None:
    ids = re.findall(r"V\d+\.\d+", key or "")
    return ids[-1] if ids else None


def _module_part(doc: str, sid: str) -> str:
    """The paragraph of a module docstring that describes `sid` (the line naming it and its indented follow-ups)."""
    lines, out, on = doc.splitlines(), [], False
    starts = [i for i, ln in enumerate(lines) if re.match(rf"\s*{re.escape(sid)}\b", ln)]
    if starts:                                         # the family list entry ("    V8.3  VWAP snap-back ...")
        lines = lines[starts[0]:]
    for ln in lines:
        if not on and re.search(rf"\b{re.escape(sid)}\b", ln):
            on = True
            out.append(ln.strip())
            continue
        if on:
            if not ln.strip() or re.match(r"\s*V\d+\.\d+\b", ln):
                break
            out.append(ln.strip())
    return " ".join(out)


def _params(cls: type) -> dict[str, Any]:
    out: dict[str, Any] = {}
    P = getattr(cls, "Params", None)
    if P is not None and dataclasses.is_dataclass(P):
        for f in dataclasses.fields(P):
            if f.default is not dataclasses.MISSING:
                out[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:          # type: ignore[misc]
                out[f.name] = f.default_factory()                       # type: ignore[misc]
    for klass in reversed(cls.__mro__):
        if klass.__module__ != cls.__module__:         # the strategy's own settings, not the engine base classes
            continue
        for k, v in vars(klass).items():
            if k.startswith("_") or k in SKIP_ATTRS or callable(v) or isinstance(v, (classmethod, staticmethod, property)):
                continue
            if isinstance(v, (int, float, str, bool)) or (isinstance(v, tuple) and len(v) <= 8):
                out[k] = v
    return out


def _describe(cls: type, sid: str) -> list[str]:
    mod = inspect.getmodule(cls)
    rules = (inspect.getdoc(cls) or "").strip() if cls.__doc__ else ""
    if not rules and mod is not None and mod.__doc__:
        rules = _module_part(mod.__doc__, sid) or textwrap.shorten(" ".join(mod.__doc__.split()), 1500)
    out = [f"Strategy: {sid} {getattr(cls, 'name', cls.__name__)}"]
    if getattr(cls, "thesis", None):
        out.append(f"Thesis: {cls.thesis}")
    if rules:
        out += ["Rules:", textwrap.fill(" ".join(rules.split()), 110)]
    for label, attr in (("Expected hold", "expected_hold"), ("Expected frequency", "expected_frequency")):
        if getattr(cls, attr, None):
            out.append(f"{label}: {getattr(cls, attr)}")
    params = _params(cls)
    if params:
        out.append("Parameters: " + ", ".join(f"{k}={v}" for k, v in params.items()))
    if getattr(cls, "fails_when", None):
        out.append(f"Fails when: {cls.fails_when}")
    out.append(f"Source: {cls.__module__.replace('.', '/')}.py ({cls.__name__})")
    return out


def logic_text(program: str, key: str, name: str = "") -> str:
    sid = strategy_id(key)
    who = f"{name} ({key})" if name and name != key else key
    lines = [f"Bot: {who}", f"Program: {PROGRAM_LABEL.get(program, program.upper())} -- paper trading, simulated fills"]
    parts = key.split("-")
    if len(parts) >= 2 and parts[1].isalpha() and parts[1].isupper() and parts[1] not in ("SCAN", "SNAP"):
        lines.append(f"Coin: {parts[1]}USDT")
    if key.endswith("+JEV"):
        lines.append("Variant: Jev twin -- the same strategy, with the Jev AI model able to veto or resize each trade")
    elif key.endswith("+LADDER"):
        lines.append("Variant: ladder twin -- the same entries, taking profit in three steps (25% / 50% / 25%)")
    cond = re.search(r"-(CHOP|BULL|BEAR)$", key)
    if cond:
        lines.append(f"Condition: enters only while BTC's market condition is {cond.group(1)}; exits are unchanged")
    if sid is None:
        return "\n".join(lines + ["Strategy: not found for this bot key"])
    classes = strategy_classes()
    if sid in V14_ORIGINAL:
        orig = V14_ORIGINAL[sid]
        lines.append(f"Strategy: {sid} = a copy of {orig} that also checks the higher timeframes before every entry")
        try:
            htf = importlib.import_module("app.strategies.v14.htf")
            lines.append("HTF rule: " + " ".join((htf.__doc__ or "").split()))
        except Exception:
            pass
        if orig in classes:
            lines += ["", "Original strategy:"] + _describe(classes[orig], orig)
        return "\n".join(lines)
    if sid not in classes:
        return "\n".join(lines + [f"Strategy: {sid} (no strategy class found)"])
    out = lines + _describe(classes[sid], sid)
    if program in ("v11", "v14"):
        note = ladder_note(sid, key)
        if note:
            out.append(note)
    return "\n".join(out)


def ladder_note(family: str, key: str) -> str:
    """V11 / V14 scanners exit on the operator's take-profit ladder, NOT on Params.target_r."""
    try:
        from app.strategies.v11 import ladder
    except Exception:
        return ""
    k = ladder.SPACING.get(family)
    if k is None:
        return ""
    shares = ladder.SHARES.get(family, (0.25, 0.5, 0.25))
    lock = ladder.LOCK_AFTER_TP1.get(family, 0.0)
    return (f"Exits actually used: take-profit ladder {int(shares[0] * 100)}/{int(shares[1] * 100)}/{int(shares[2] * 100)}% "
            f"at {k:g}R / {2 * k:g}R / {3 * k:g}R (app/strategies/v11/ladder.py); after TP1 the stop moves to entry + the "
            f"fees or {lock:g} of the way to TP1, after TP2 onto TP1; the time stop closes the rest. The target_r "
            f"parameter above is NOT used for exits"
            + (" (a +JEV twin stretches the rungs by Jev's confidence)" if key.endswith("+JEV") else "") + ".")
