"""WATCHDOG (app/live/watchdog.py): program health -> debounced operator alerts, the live mirror's SAFE MODE, bot-level
alerts for the bots the operator follows, the daily digest and the public deep-health payload. No network, no threads."""
from __future__ import annotations

from app.live import watchdog as wd


class Svc:
    def __init__(self, status="LIVE", ws=True, age=1.0, bots=None, enabled=True):
        self.enabled, self.started_at = enabled, 0.0
        self.h = {"status": status, "ws": ws, "klines_age_s": age, "restarts": 0, "error": None}
        self.status = {"bots": bots or []}

    def health(self):
        return dict(self.h)


class Notifier:
    def __init__(self, rules=None):
        self.sent, self.store = [], {}
        self.rules = rules or {"v8": {"CONTROL"}, "v11": set()}

    def alert(self, text):
        self.sent.append(text)

    def wants(self, program, role):
        return program in self.rules and (not self.rules[program] or role in self.rules[program])

    def kv(self, k, v=None):
        if v is None:
            return self.store.get(k)
        self.store[k] = v
        return v

    def health(self):
        return {"linked": True, "errors": 0}


class Mirror:
    def __init__(self):
        self.safe = {}

    def set_program_safe(self, program, reason):
        if reason:
            self.safe[program] = reason
        else:
            self.safe.pop(program, None)

    def health(self):
        return {"armed": 1, "with_position": 0, "safe_mode": dict(self.safe)}


def make(services, t=[10_000.0]):
    clock = {"t": 1_790_000_000.0 + 3600 * 2}           # mid-day UTC: no digest timing surprises
    n, m = Notifier(), Mirror()
    w = wd.Watchdog(services, mirror=m, notifier=n, clock=lambda: clock["t"])
    w.started = clock["t"] - 3600
    n.store["digest_day"] = "x"                          # keep the digest out of the way unless a test wants it
    return w, n, m, clock


def tick(w, clock, n=1, dt=30.0):
    for _ in range(n):
        clock["t"] += dt
        w.check()


def test_problem_rules():
    assert wd.problem_of({"status": "LIVE", "ws": True, "klines_age_s": 2}, 60) is None
    assert "stale" in wd.problem_of({"status": "LIVE", "ws": True, "klines_age_s": 400}, 60)
    assert "websocket" in wd.problem_of({"status": "LIVE", "ws": False, "klines_age_s": 2}, 60)
    assert wd.problem_of({"status": "LIVE", "ws": False, "klines_age_s": None}, 60, ws_required=False) is None
    assert wd.problem_of({"status": "MARKET_CLOSED"}, 60) is None
    assert wd.problem_of({"status": "WARMING_UP"}, 60) is None
    assert "warming up" in wd.problem_of({"status": "WARMING_UP"}, 3600)
    assert "restarting" in wd.problem_of({"status": "RESTARTING", "error": "worker exited with code 1"}, 60)


def test_a_sustained_outage_alerts_once_turns_on_safe_mode_and_recovers():
    v8 = Svc()
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    n.sent.clear()
    v8.h.update(klines_age_s=500.0)
    tick(w, clock)                                       # one bad check: not yet
    assert not n.sent and "v8" not in m.safe
    tick(w, clock)                                       # confirmed
    assert len(n.sent) == 1 and "V8 DOWN" in n.sent[0] and "stale" in m.safe["v8"]
    tick(w, clock, 5)
    assert len(n.sent) == 1                              # no repeats while it lasts
    v8.h.update(klines_age_s=1.0)
    tick(w, clock, 2)
    assert "V8 recovered" in n.sent[-1] and "v8" not in m.safe


def test_a_flapping_program_alerts_at_most_once_per_cooldown():
    v8 = Svc()
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    for _ in range(4):                                   # down 1 min, up 1 min, repeatedly
        v8.h.update(status="DEGRADED")
        tick(w, clock, 2)
        v8.h.update(status="LIVE")
        tick(w, clock, 2)
    downs = [x for x in n.sent if "DOWN" in x]
    assert len(downs) == 1 and len([x for x in n.sent if "recovered" in x]) == 1


def test_v9_without_its_websocket_is_healthy_on_rest():
    v9 = Svc(ws=False, age=None)
    w, n, m, clock = make({"v9": v9})
    tick(w, clock, 4)
    assert not [x for x in n.sent if "DOWN" in x] and w.deep_health()["ok"]


def test_worker_restart_and_boot_are_announced():
    v8 = Svc()
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    assert any("PaperLab restarted" in x for x in n.sent)
    v8.h["restarts"] = 1
    tick(w, clock)
    assert any("worker restarted" in x for x in n.sent)


def test_bot_alerts_only_for_followed_bots_and_only_on_change():
    bots = [{"key": "V8.3-ENA-5M", "role": "CONTROL", "program_status": "ACTIVE", "net": 1.0, "trades": 40},
            {"key": "V8.3-ENA-5M+JEV", "role": "JEV", "program_status": "ACTIVE", "net": 0.5, "trades": 30}]
    v8 = Svc(bots=bots)
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    n.sent.clear()
    bots[0]["program_status"] = "QUALIFIED"
    bots[1]["program_status"] = "ELIMINATED"             # a twin: the operator doesn't follow it
    tick(w, clock)
    assert len(n.sent) == 1 and "QUALIFIED" in n.sent[0] and "V8.3-ENA-5M" in n.sent[0]


def test_daily_digest_after_midnight_with_the_change():
    bots = [{"key": "V11.3-SCAN", "role": "CONTROL", "net": 2.0, "trades": 10}]
    v11 = Svc(bots=bots)
    w, n, m, clock = make({"v11": v11})
    n.store.pop("digest_day")
    tick(w, clock)                                       # first run: snapshot only
    assert not [x for x in n.sent if "Daily summary" in x]
    clock["t"] += 86_400
    bots[0].update(net=3.5, trades=14)
    tick(w, clock)
    d = [x for x in n.sent if "Daily summary" in x]
    assert len(d) == 1 and "+1.50 today, 4 trades" in d[0]
    tick(w, clock)
    assert len([x for x in n.sent if "Daily summary" in x]) == 1


def test_deep_health_is_503_material_and_carries_no_secrets():
    v8, v11 = Svc(), Svc()
    w, n, m, clock = make({"v8": v8, "v11": v11})
    tick(w, clock)
    assert w.deep_health()["ok"] is True
    v11.h.update(status="ERROR", error="boom")
    tick(w, clock, 2)
    d = w.deep_health()
    assert d["ok"] is False and d["down"] == ["v11"] and "error" in d["programs"]["v11"]["problem"]
    assert set(d) == {"ok", "down", "programs", "live_mirrors", "telegram", "checked_s_ago"}


def test_deep_health_survives_nan_and_odd_values():
    import json
    v8 = Svc(age=float("nan"))
    v8.h["restarts"] = None
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    json.dumps(w.deep_health(), allow_nan=False)        # what JSONResponse does


def test_the_public_deep_health_route_answers_200_or_503():
    import asyncio
    import json

    from app.main import create_app
    app = create_app()
    route = next(r for r in app.routes if getattr(r, "path", "") == "/public/health/deep")
    assert asyncio.run(route.endpoint()).status_code == 503                 # no watchdog yet: starting
    v8 = Svc()
    w, n, m, clock = make({"v8": v8})
    tick(w, clock)
    app.state.watchdog = w
    resp = asyncio.run(route.endpoint())
    assert resp.status_code == 200 and json.loads(resp.body)["ok"] is True
    v8.h.update(status="ERROR")
    tick(w, clock, 2)
    assert asyncio.run(route.endpoint()).status_code == 503
