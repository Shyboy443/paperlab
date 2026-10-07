"""Jev (TypeSafe AI) through OpenRouter's Decisions API, as a filter on strategy signals.

    Candidate signal -> JevStateBuilder -> JevClient -> typed decision -> JevPolicy -> RiskManager

Jev never originates a trade here (that would be JEV_NATIVE mode, not enabled), never reverses a
side, never moves a stop and never reaches the broker. The OpenRouter key lives only in the server
process environment (OPENROUTER_API_KEY) and is never logged, stored, returned or sent to a browser.
"""
