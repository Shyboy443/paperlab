"""SYNTHESIS — why the in-repo loop has hit the same data wall as the prior workspace.

This is the honest output of the first loop iteration. The loop policy
is structured, but structure doesn't help when the *inputs* are capped
by the public APIs. Rounds 1-8 in the prior workspace hit:
  - Coinbase hourly: ~300 days
  - OKX funding history: ~100 days
  - OKX mark-price history: ~24h (mislabeled 'history' endpoint — verified
    via 50-page probe, see research/scripts/probe_okx_depth.py)
  - Bybit mark-price: 1000 most recent bars (~16h on 1m)
  - Binance klines: deep history, but geo-blocked in many retail envs
  - Deribit options: deep history, but per-venue auth friction

The same wall is here. Continuing to run the loop in this scaffold
without an authenticated data source will just produce more
'INSUFFICIENT DATA' or 'STRUCTURAL FAILURE' verdicts.

Three concrete options, none of which the loop driver can resolve on
its own — they require a decision from the user:

  (A) Acquire authenticated bulk history (Binance/OKX/Bybit paid tier,
      Tardis, Databento, CoinAPI). This unlocks round 9+ at walk-forward
      scale. The cost is ~$50-300/mo for a research-tier subscription.
      This is the *only* path that lets the loop actually test the
      remaining input classes honestly.

  (B) Change markets. Move off crypto onto TradFi instruments with
      longer, free public history (US equity intraday via Polygon free
      tier ~5y minute bars, CBOE options, FRED daily). Different cost
      schedule (often 0 commission, narrower spread), different
      microstructure. The cost-breakeven analysis from the prior
      workspace does NOT apply to TradFi — its 0.3%-RT number was
      specific to crypto retail.

  (C) Accept the conclusion. The prior rounds 1-8 + this round 9 are
      four independent input classes (single-venue price patterns,
      vol-aware variants, delta-neutral funding carry, cross-venue
      basis) and the answer is the same every time. The conclusion in
      RESEARCH_HISTORY.md stands: 0.5%/day net is not achievable on
      public retail data with retail costs. Stop the loop.

The loop driver does not pick between A/B/C — that's a human decision.
What it CAN do is refuse to produce false-positive hits, which it has.
"""
import json, time, pathlib
from .stop_rule import StopCriteria, RoundMetrics, evaluate

ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

def write_synthesis() -> None:
    payload = {
        "title": "SYNTHESIS — in-repo loop hits the same data wall as the prior workspace",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rounds_in_repo": ["round 9 — cross-venue perpetual basis"],
        "structural_findings": [
            "round 4.2 (prior workspace): cross-venue spot basis p99 < cost, no edge",
            "round 9  (in-repo):       cross-venue mark basis p99 < cost, no edge (5 bps max vs 80 bps RT cost)",
        ],
        "data_walls": [
            {"venue": "Coinbase", "endpoint": "products/<id>/candles", "cap": "~300 hourly bars"},
            {"venue": "OKX", "endpoint": "funding-rate-history", "cap": "~302 payments (~100d)"},
            {"venue": "OKX", "endpoint": "history-mark-price-candles (mislabeled)", "cap": "24h rolling, NOT historical"},
            {"venue": "Bybit", "endpoint": "mark-price-kline", "cap": "1000 most recent bars"},
            {"venue": "Binance", "endpoint": "fapi/v1/markPriceKlines", "cap": "1000 most recent bars"},
        ],
        "options_for_user": [
            "A. Acquire authenticated bulk history (Tardis / Databento / paid tier)",
            "B. Move to TradFi (Polygon / CBOE / FRED) with longer free history and different cost schedule",
            "C. Accept the conclusion from rounds 1-9: 0.5%/day net is not achievable on public retail data with retail costs",
        ],
        "verdict": "Loop paused pending user decision. No false-positive hits produced."
    }
    p = LOG_DIR / "synthesis.json"
    p.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"[synthesis] wrote {p}")
    print(f"[synthesis] VERDICT: {payload['verdict']}")
    for opt in payload["options_for_user"]:
        print(f"[synthesis]   - {opt}")
