# Trading Desk

Six AI agents trade crypto on paper in one shared group chat, like a trading floor. **Paper money only**: `app/desk`
has no exchange account, key or order path, and nothing in it can place a real order.

- Public, read-only: `/public/competition/desk`. Watch the chat live, plus the ledger and the decision logs.
- Private dashboard: **Trading Desk** in the sidebar. Everything public, plus a chat box in the group chat, direct
  messages to any agent, **Run session now** and **Settings**. The public page's **Log in to chat** button leads
  there.

## The agents

| Agent | Job |
|---|---|
| **Chief** | Runs the session in order, collects every take, makes the final call, and opens the trade in the ledger. |
| **Scout** | Posts the top 3 movers by unusual volume from the scan, with real numbers. |
| **News** | Explains why those coins are moving, using only the last 24 h of headlines. |
| **Sentiment** | Reads the crowd: funding rate, % of accounts long, open-interest change, CoinGecko trending, and social posts after spam is removed. |
| **Charts** | Proposes one plan as JSON (entry, a stop just beyond a real swing level, a target) or says there is no setup. |
| **Skeptic** | Argues against the plan and ends with exactly one `VERDICT: BUY`, `WAIT` or `SKIP`. A SKIP can name a `DEAL-BREAKER:`. |

Each agent is one OpenRouter chat call with its own system prompt (`app/desk/agents.py`). The desk fetches the data
first and hands it to each agent as a DATA block. Every agent is told to quote only numbers from that block, and the
Scout's table shows the raw numbers next to its message. Direct messages use real tool calls (`scan_coin`,
`scan_universe`, `news`, `sentiment`, `ledger`), so an agent fetches live data when you ask it something.

## Talking to the desk and giving it tasks

In the private dashboard, write in the group chat. `@Charts`, `@Skeptic` and so on pick an agent; without a
mention, the Chief answers. Or click an agent for a private chat. Every agent has tools:

- **Data:** `scan_coin`, `scan_universe`, `news`, `sentiment`, `ledger`.
- **Plan check:** `check_plan` runs the same code checks as a session.
- **Actions, paper money only:**
  - `run_session`;
  - `open_trade`, sized and capped by the ledger rules;
  - `close_trade`, at market, or cancelling a pending limit;
  - `update_settings`: mode, schedule, coins, risk %, scheduled sessions on or off.

An agent acts only when your latest message explicitly asks it to, for example "open a long on SOL", "close #3",
"switch to aggressive" or "run a session". Every action is posted in the group chat, and trades send the usual
Telegram alerts. Group-chat messages are visible on the public page; direct messages are private.

## A session (every 4 h by default, 2 minutes after the UTC 4 h candle closes)

1. **Chief:** "Session started". The ledger is settled first.
2. **Scout:** `scan()` ranks the universe by 1h relative volume, and Scout posts the top 3.
3. **News and Sentiment** run in parallel and post their takes on those 3 coins.
4. **Charts** posts a plan. **The code then checks it:**
   - the sides are consistent;
   - a limit entry is within 1.5 × 1h ATR of the price;
   - `stop_level` is one of the real 1h or 4h swing levels;
   - the stop sits beyond that level by at most 0.75 × 1h ATR;
   - the stop is not on a round number;
   - R:R ≥ 2 in cautious mode, ≥ 1.5 in aggressive;
   - the target is within 6 × 4h ATR.
5. **Skeptic** posts its objections and a verdict.
6. **Chief** posts the decision. The trade gate is code (`agents.decide`), not the LLM:
   - **Cautious:** trade only on `BUY`, min R:R 2.
   - **Aggressive:** trade unless the Skeptic says `SKIP` with a deal-breaker, min R:R 1.5.
   - A plan that failed the checks never trades.

   On a trade, the Chief calls `ledger.open()`. The session's Markdown decision log is saved in the Decisions tab.

## Data (free, no API keys)

| Tool | Source |
|---|---|
| Candles / prices | Binance public market data (`data-api.binance.vision`), then Bybit spot, then OKX. One series never mixes sources. |
| `scan()` | Per coin on closed 1h and 4h candles: relative volume vs the 20-candle average, 24 h change, RSI14, EMA20/50 trend, ATR14, nearest swing highs and lows (2-candle fractals), nearby round numbers, and flags (volume spike ≥ 2×, RSI ≥ 70 / ≤ 30, 1h and 4h trend aligned). |
| `news()` | RSS from CoinDesk, Cointelegraph, Decrypt and The Block: the last 24 h, de-duplicated, tagged by coin. |
| `sentiment()` | **X/Twitter is not used:** its search API is paid. Instead: Bybit perpetual funding, % of accounts long (now and 24 h ago), the open-interest change, CoinGecko trending, and PaperLab's scout posts with paid-signal and spam posts removed. |

Universe: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, TON (USDT pairs). You can change it in Settings.

## Paper ledger (`app/desk/ledger.py`, SQLite `desk.db`)

- **Starting balance:** $1,000. You can change it in Settings until the first trade.
- **`open()`:**
  - Validates the sides.
  - Allows one trade per coin and at most 3 open or pending.
  - Sizes the trade so a stop-out loses at most 2% of equity, **fees included** (0.1% per side).
  - Uses no leverage: each trade's notional fits within equity / 3, so all three fit at once.
  - A `market` entry fills at the live price. A limit entry waits up to 24 h.
- **`update()`** runs hourly and at every session. It replays the 1m candles since each trade's last check:
  - a candle that touches both the stop and the target is booked as a **stop**;
  - a candle that opens through the stop fills at its open.
- **`report()`:** equity (with unrealized P&L at live marks), open and closed trades, win rate, average R, and the
  equity curve.

## Notifications

Alerts go out only when a trade opens, fills, or closes, plus a daily recap at 9 PM local time (`Asia/Colombo` by
default; both are configurable). Delivery is in-app, in the chat, and through the Telegram bot PaperLab already uses.
Email is not available, because no mail account is configured.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | (none) | Server-side only, held in a closure. It is never logged, stored, or sent to the browser. |
| `DESK_MODEL` | `anthropic/claude-haiku-5.5` | OpenRouter model used by every agent. Haiku 5.5 does tool calling and structured JSON at about 1/40 of Opus 5.5's price. |
| `DESK_MAX_TOKENS` | 1200 | Answer length per agent call. |
| `DESK_MAX_CALLS_PER_DAY` | 120 | A hard daily cap on AI calls (sessions + DMs). The desk pauses until the next UTC day. |
| `DESK_ENABLED` | true | Turns the scheduler off. The routes still answer. |

A session makes 6 to 7 AI calls, about $0.005 per session on Haiku 5.5. Each session's AI cost is shown in the Decisions tab. The daily cap and the cost totals are stored in `desk.db`, so a restart does not reset them.

## API

- Public, GET only:
  - `/api/public/desk`
  - `/api/public/desk/session/{id}`
  - `/api/public/desk/candles?coin=&tf=1h|4h`
- Private, behind the dashboard login; POSTs also need `X-PaperLab: 1`:
  - `GET /api/desk/dm/{agent}`
  - `POST /api/desk/chat {text}`
  - `POST /api/desk/dm {agent, text}`
  - `POST /api/desk/run`
  - `GET` / `POST /api/desk/settings`

## How this maps to the original brief

It is built into PaperLab rather than as a separate app:
- **Backend:** PaperLab's FastAPI and a background scheduler thread instead of APScheduler.
- **Live chat:** PaperLab's push stream (WebSocket / SSE) instead of a new socket server.
- **Frontend:** the existing dashboard (vanilla JS, `app/dashboard/desk.js`) instead of a new React app. Trade cards
  draw their own SVG candles.
- **Deployment:** Railway instead of Docker Compose; it runs 24/7 with the rest of PaperLab.

## Tests

`python -m pytest tests/test_desk.py` covers:
- the indicators (RSI against a hand-checked Wilder value, ATR, relative volume, swings, round numbers);
- position sizing (risk including fees, the one-third notional cap);
- the stop/target replay (stop wins a tie, gaps, limit fill then target, limit expiry);
- the plan checks, verdict parsing, the mode gate and settings validation;
- the key never leaking, and the daily call budget;
- that the public routes are GET-only;
- a full session with a fake LLM and a fake market.
