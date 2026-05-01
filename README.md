# AOMI Kalshi Trader

> Autonomous BTC prediction market agent powered by [`@aomi-labs/client`](https://github.com/aomi-labs/aomi-sdk).

**What it does:** Watches Kalshi's KXBTC15M market (15-min BTC binaries), searches the live web for signals, decides YES or NO, and places the order — with no human in the loop.

---

## The problem it solves

Kalshi's KXBTC15M market opens and closes every 15 minutes, 96 times a day. Most traders catch 20 of those windows. The other 76 close without them, regardless of whether the setup was good.

This agent covers all 96. Enable Auto Mode once and walk away.

---

## What AOMI does here

Every analysis cycle, AOMI:

1. **Searches the live web** via `brave_search` — BTC price action, news, sentiment, whale activity. Not pre-computed signals — whatever is happening right now.
2. **Reasons over the market snapshot** — current BTC vs strike, YES/NO ask prices, time left in the window.
3. **Returns a verdict** — `BUY YES`, `BUY NO`, or `PASS` with a confidence level.
4. **Auto-executes** if confidence ≥ 55% (configurable via risk slider).
5. **Retries in 30 seconds** if the verdict is PASS, until it finds an edge or the window closes.

The agent uses a single `Session` from `@aomi-labs/client`. That's it — no custom model calls, no prompt chains, no evals infra.

---

## Setup (< 5 minutes)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_HANDLE/aomi-kalshi-trader
cd aomi-kalshi-trader
npm install
```

### 2. Configure environment

```bash
cp .env.local.example .env.local
```

Fill in `.env.local`:

```bash
# AOMI — no API key needed for the default app
AOMI_BASE_URL=https://api.aomi.dev
AOMI_APP=default

# Kalshi — get your key pair at kalshi.com/settings/api
KALSHI_API_KEY=your-key-id
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem
```

### 3. Run

```bash
npm run dev
# → http://localhost:3000/agent
```

> **No Kalshi keys?** The app still runs — market data and AOMI analysis work without credentials. Only order execution requires keys.

---

## How the AOMI integration works

```typescript
import { AomiClient, Session } from '@aomi-labs/client'

const client  = new AomiClient({ baseUrl: process.env.AOMI_BASE_URL })
const session = new Session(client, { app: process.env.AOMI_APP })

// Inject live market context, then let AOMI search + reason
const prompt = `
  Market: KXBTC15M-26MAY01-0545
  BTC spot: $77,315 | Strike: $77,360 | NO ask: 82¢ | 4 min left
  BTC is $45 BELOW strike — NO currently winning.

  Search for the latest BTC price action and news.
  Give me a direct YES or NO verdict with confidence. Be decisive.
`

// Stream the response
for await (const event of session.stream(prompt)) {
  if (event.type === 'message') console.log(event.text)
}
// → "BUY NO @ 82¢ — Confidence: 85% ..."
```

AOMI handles the `brave_search` tool call internally. Your code just sends a prompt and streams the result.

---

## Architecture

```
Browser (Next.js App Router)
│
├── /agent          ← Agent page: auto mode, chat, market bar
├── /dashboard      ← Price chart, market card, positions panel
│
└── /api/
    ├── aomi/chat   ← Server-side AOMI session, SSE stream to browser
    ├── aomi/history← Load prior session messages
    ├── place-order ← Kalshi order execution (RSA-PSS signed)
    ├── balance      ← Live Kalshi balance (for position sizing)
    ├── positions    ← Open positions
    └── markets      ← Active KXBTC15M market discovery
```

**Key files:**

| File | What it does |
|------|-------------|
| `lib/aomi-session.ts` | `Session` wrapper, market prompt builder |
| `app/api/aomi/chat/route.ts` | SSE-streaming AOMI responses to the browser |
| `app/agent/page.tsx` | Autonomous loop, risk slider, chat UI |
| `lib/kalshi-trade.ts` | Order placement, balance, positions |
| `lib/kalshi-auth.ts` | RSA-PSS request signing |

---

## Auto Mode

The agent page has one control: a risk % slider (1–50% of live balance). Enable Auto Mode and the loop runs:

```
analyze → BUY? → execute → wait for next window
             ↓
           PASS? → wait 30s → analyze again
```

State (auto mode on/off, last analysis time, traded window) persists in `sessionStorage` — survives navigating between pages within the same tab, resets on new tab.

---

Built for the AOMI DevRel take-home · [`@aomi-labs/client`](https://github.com/aomi-labs/aomi-sdk) · Kalshi API · Next.js 16
