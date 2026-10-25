# Divergence Desk inside PaperLab

The user's reference is the existing Trading Desk: keep the PaperLab sidebar and header, a left-hand roster, and a conversation in the main column. Open Group chat by default. The Ledger, Decisions, Charts and Historical results are views in that same panel, not new browser tabs or standalone pages.

Use the existing Space Grotesk/JetBrains Mono typography and dark navy dashboard. Agent identities: structure green #39cfa3, risk blue #5cbbf4, news violet #ac86f0, skeptic rose #ff7477; system records amber #f4b847. Agent colors identify speakers rather than imply trade performance.

```
PaperLab sidebar | Divergence Desk / health / shared AI budget
                 | Four reviewers | Group chat / Ledger / Decisions / Charts / Historical results
                 |                | Recorded setup -> actual reviewers -> recorded decision
```

Only saved prospective reviews become conversation messages. Missing or invalid reviews appear as system records, never invented dialogue. Historical signals remain chart evidence. Separate paper books retain their actual fills and funding. No trading rules or execution controls change.

The existing desk layout is shared via scoped selectors. Chart and ledger styles are confined to the divergence panel. Navigation stops inactive polling and releases Vela instances. Desktop and mobile preserve readable messages, visible focus and no horizontal page overflow. Legacy /public/divergence redirects to /public/competition/divergence; the authenticated dashboard uses #divergence/divergence.
