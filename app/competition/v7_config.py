"""Active paper challenger, isolated database and identity; V6 remains frozen."""
import dataclasses
import hashlib
import importlib
import json

from app.competition import v6_config as baseline
from app.strategies.v7.active import load_v7

FREEZE_FILE = baseline.DOCS / "V7_FREEZE.json"
PROTOCOL = "V7_ACTIVE_PAPER_V1"


@dataclasses.dataclass(frozen=True)
class BotSpecV7(baseline.BotSpecV6):
    @property
    def control_key(self):
        return f"{self.strategy_id}-{self.coin}-15M"

    @property
    def timeframe(self):
        return "15m"

    @property
    def pair_id(self):
        return "v7pair:" + self.control_key


def field_plan(coins, jev=False):
    if jev:
        raise ValueError("V7 is a CONTROL-only paper challenger")
    return [BotSpecV7(sid, coin, "ACTIVE") for sid in load_v7() for coin in coins]


def manifest():
    base = baseline.load_freeze()
    if baseline.verify_freeze(base):
        raise ValueError("V6 dependencies differ from their freeze")
    modules = ("app.strategies.v7.active", "app.competition.v7_config", "app.live.v6_worker")
    out = {**base, "protocol": PROTOCOL, "baseline_fingerprint": base["fingerprint"],
           "challenger_code": {m: baseline._src(importlib.import_module(m)) for m in modules},
           "challenger_params": {k: dataclasses.asdict(v.Params()) for k, v in load_v7().items()},
           "profile": {"signal": "15m", "risk_cap": 0.02, "base_risk": 0.01, "jev": False,
                       "storage": "v7-forward.db", "families": list(load_v7())}}
    out.pop("fingerprint", None)
    out["fingerprint"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:16]
    return out


def load_freeze():
    return json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None


def verify_freeze(value):
    try:
        return [] if value == manifest() else ["V7 code/configuration differs from docs/V7_FREEZE.json"]
    except ValueError as exc:
        return [str(exc)]


def experiment_identity(man, jev_model=None):
    ident = {"protocol": PROTOCOL, "manifest": man["fingerprint"], "venue": baseline.VENUE, "jev": False}
    return "v7x-" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12], ident
