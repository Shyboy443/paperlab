"""One minimal authenticated Jev request, run on the server where OPENROUTER_API_KEY lives.

    railway ssh -- python scripts/jev_smoke.py

Prints only non-secret facts (status, resolved model, latency, tokens, cost) and records them as the
JEV API HEALTH check in the service database. The key is read from the environment by the client and
never printed, logged or stored.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.service import HEALTH_META_KEY, JevService  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def main() -> int:
    db = Path(os.environ.get("DATA_DIR") or PROJECT / "data") / "paperlab.db"
    storage = Storage(db)
    svc = JevService.from_env(storage)
    check = svc.client.smoke()
    storage.set_meta(HEALTH_META_KEY, json.dumps(check))
    print(json.dumps({k: check.get(k) for k in ("ok", "status", "model_requested", "model_resolved",
                                                "provider", "latency_ms", "attempts", "http_status",
                                                "input_tokens", "cost_usd", "answer_type",
                                                "error_code", "error_message")}, indent=1))
    storage.close()
    return 0 if check.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
