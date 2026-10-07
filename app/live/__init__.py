"""Live shadow: frozen bots trading simulated books on live Binance USD-M market data.

Nothing in this package can place an order. It reads public market data, runs each bot through the
same ReplayEngine the arena uses (same fees, execution model, RiskManager, cost gate, ATTACK policy),
and records what the simulated books did. See docs/LIVE_SHADOW.md.
"""
