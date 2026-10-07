# Live dashboard prices

The dashboard uses a dedicated, read-only Bybit linear ticker WebSocket. One connection serves all viewers, subscribing to ARB, ENA, XRP, DOGE, BTC and ETH. Snapshot/delta messages are merged, and invalid or out-of-order messages are rejected. The server publishes complete display snapshots every 250 ms through the existing WebSocket/SSE transport, without retaining quote history in the replay buffer.

Both V6 and V7 display live mid-price estimates for open positions. The browser revalues each authoritative book from its latest equity and position snapshot, preserving booked fees and funding. Estimates never accumulate from earlier estimates. No display quote is passed into execution, persisted as a fill, or used to modify strategy rules. The frozen experiment manifests remain unchanged.

Numbers update in place with brief directional highlights; reduced-motion preferences are respected. Quotes become stale after five seconds or a disconnection. The interface then uses authoritative book values for PnL and marks stale numbers. Reconnects require fresh exchange snapshots. Thirty-second REST refreshes reconcile historical dashboard content, while both bot versions receive state pushes between refreshes.

Read-only diagnostics: `/api/public/prices`. Each quote includes source and receipt timestamps, sequence, age, and stale status. Display cadence is capped at four updates per second; unchanged prices remain unchanged.

Validation: `python -m pytest -q` and `node --test tests/test_prices_frontend.cjs`. Tests cover delta merging, sequence rejection, reconnect snapshots, invalid prices, staleness, public payloads, exact long/short PnL, booked costs, and frozen experiment integrity.

Source protocol: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker
