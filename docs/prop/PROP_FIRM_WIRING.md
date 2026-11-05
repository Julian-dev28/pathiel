# Wiring pathiel into a futures prop firm (oil)

2026-11-05. Operator: "research and write a doc to wire us into a prop firm
that trades oil futures, i want to use the same mechanism we have here but
with a prop firm, obv it would need api".

## The answer

**It can be done, with three changes:**
1. We trade **WTI** (CME `MCL`/`CL`), not Brent. Almost no futures prop firm
   lists Brent (BZ).
2. We size in **contracts**, not leverage.
3. A **prop-rules engine** has to sit in front of every order.

The decision loop stays: Claude Code, the pathiel MCP tools, the desk-tick
agent, the thesis, no-break, the event blackout and the review.

**Best firm: DayTraders.com.**
- In writing, it allows "AI tools, automations, scripts, custom algorithms
  and trading bots" and "running your platform or tools on a VPS".
- It runs on Rithmic.
- It lists CL, QM and MCL.

**Runner-up: Topstep, from the Mac only.**
- Its API (ProjectX) is the cleanest.
- Its own help page says "your server can watch and record, but it cannot
  trade". VPS and remote servers are banned.
- The API is sim-only, with no live funded accounts.
- The rented-server plan in DEPLOY.md is therefore out for Topstep.

**What gives me pause is the edge, not the wiring.**
- Our real trades average **+0.19R**.
- At a size the drawdown rules survive (1 MCL ≈ $75 a trade), a $3,000
  target is about **40R**: roughly 200 trades.
- Do not buy an evaluation until shadow mode (phase 3 below) shows the edge
  holds on CME.

**Before any money: get written confirmation from the firm's support.** Ask
about this exact setup: a server-side Claude Code agent placing 1 to 4
bracket orders a day through their API. Several firms' rules are
discretionary.

## Which firms allow it

Verified on each firm's own help center 2026-11-05 unless marked.

| Firm | Bots / AI | Server / VPS | API | Oil | 50k account | Notes |
|---|---|---|---|---|---|---|
| **DayTraders.com** | **allowed**: "AI tools, automations, scripts, custom algorithms and trading bots", no HFT | **allowed** | Rithmic, same login on eval and funded, no API fee found | CL, QM, MCL (no ICE/Brent) | Static: $50, target $3,750, **$1,000 static intraday max loss**, no DLL, 6 contracts / 60 micros, 50% consistency | Overnight and weekend holding allowed. Real-money tier caps at $50k |
| **Topstep** | allowed in sim | **banned**: "All trading activity must originate from your personal device" (updated 2026-09-17) | ProjectX Gateway REST + SignalR, $29/mo; **sim only**, no Live funded | CL, QM, MCL | ~$3,000 target, ~$2,000 trailing (third-party numbers) | Only the Mac could run it. Express Funded is sim, so payouts still pay |
| **MyFundedFutures** | allowed: "automated trading strategies tailored to their own specific settings" | not addressed | not published (likely Rithmic) | not published | ~$3,000 / ~$2,000 trailing EOD (third-party) | Ask support for the platform and the oil list |
| **Funded Futures Network** | "allowed but at the discretion of FFN" | not addressed | "your own Rithmic login" bundled | MCL confirmed | $3,000 target, $2,000 EOD, $1,250 DLL, 4 minis | **No overnight** (flat 4:50pm ET). **EIA rule**: flat incl. resting orders 1 min before to 1 min after. Breaks our multi-day holds |
| **Bulenox** | needs management **approval** | n/a | Rithmic, $100/mo API fee | CL, MCL, QM | $175, $3,000 target, $2,500 DD, 7 contracts | Approval is a gate, not a no |
| Tradeify | allowed with proof of ownership | not addressed | lost ProjectX in Feb 2026; now Tradovate/Rithmic | unconfirmed | pricing page was placeholder text | Verify everything with support |
| FundedNext Futures | EAs/bots allowed | n/a | Tradovate/NinjaTrader only (local EA, no server API found) | CL, QM, MCL | $2,500 target, $1,500 DD | A local-PC EA, not our setup |
| Apex, Alpha Futures, Elite Trader Funding, Take Profit Trader | **banned** (Apex and Alpha name "AI"; ETF needs written approval; TPT reported) | | | | | Do not use |

ProjectX ended its API for every firm except Topstep around 2026-02-28.
Blogs that list Tradeify or Lucid as "ProjectX firms" are stale.

## The math in our units

1 MCL = 100 barrels, so $1 of price is $100. Our 0.75 stop buffer is about
**$75 a contract**, so 1R ≈ $75 at 1 MCL.

| Firm (50k) | Max loss | Target | Room at 1 MCL | Target in R | Trades at our +0.19R | at +0.5R |
|---|---|---|---|---|---|---|
| DayTraders Static | $1,000 static intraday | $3,750 | 13R | 50R | ~260 | ~100 |
| Topstep / MFFU / FFN | $2,000 EOD | $3,000 | 27R | 40R | ~210 | ~80 |
| Bulenox | $2,500 | $3,000 | 33R | 40R | ~210 | ~80 |

Our worst real stretch: **8 losses in a row, -9.1R**; worst day **-5.5R**
(11-02). At 2 MCL ($150/R), DayTraders' 13R room halves to 6.7R, and that
streak fails the account. **1 MCL is the size; patience is the cost.** At
1 to 2 trades a day that is months, plus the monthly evaluation fees.

The prop firm does not fix a thin edge. It fixes the **sizing**: our real
account risks 15% a trade and lost $55 on trades worth +3R. On fixed $75
risk the same trades are positive. See the 2026-11-05 answer in chat: +$3,036
at 1% on $100k.

## What changes in pathiel

The desk-tick agent, the thesis and the MCP tool names stay. The venue moves
behind one contract.

### 1. A venue contract (`contracts/venue/` + `services/venues/<name>/`)

What the tick and bracket code call today, and the futures equivalent:

| Today (`pathiel/client/exchange.py`) | Venue contract | Rithmic (async_rithmic) | ProjectX (Topstep) |
|---|---|---|---|
| `place_hl_bracket_order` (resting limit + stop + target) | `place_bracket(symbol, side, qty, entry_px or None, stop_px, tp_px)` | bracket order with stop and target ticks | `Order/place` with `stopLossBracket` + `takeProfitBracket` (account must be in **Auto OCO Brackets** mode, set in the platform UI) |
| `place_hl_order` + 2 `place_hl_trigger_order` (market + stop + target) | same call, `entry_px=None` | market + attached bracket | `type=2` + brackets |
| `_position_size_on_coin` | `position(symbol)` | positions | `Position/searchOpen` |
| `_resting_orders_for_coin`, `attached_child_oids` | `open_orders(symbol)` | open orders | `Order/searchOpen` |
| `cancel_orders`, `cancel_stranded_reduce_only_orders` | `cancel(order_id)` | cancel | `Order/cancel` |
| `get_hl_price`, candles in `oil_levels.py` | `mid(symbol)`, `bars(symbol, 1h/15m, n)` | ticker plant + history plant | `History/retrieveBars` (50 req/30s), market hub |
| `set_leverage`, `get_max_leverage` | **gone**: futures margin is per contract | n/a | n/a |
| fills for `services/review` | `fills(since)` | order/PnL plant | `Trade/search` |

`HyperliquidVenue` wraps today's exchange module unchanged. A parity test
runs the same bracket script against both with fakes, so the bracket logic
cannot drift.

### 2. Instruments and the roll

- **Desk:** a new desk `oil_cme`, thesis instrument `xyz:CL` (WTI; pathiel
  already knows it), execution symbol `MCL`.
- **Thesis vs execution prices:** the thesis and levels must use **CME bars
  from the venue**, not Hyperliquid's xyz:CL. Stops sit on CME prices.
- **The roll:** front month, rolled 3 business days before last trade.
  `services/venues/cme_calendar.py` keeps it pure and tested. CL's last
  trade is 3 business days before the 25th of the month before delivery;
  MCL a day earlier (verify on cmegroup.com).

### 3. Sizing in contracts

    contracts = floor(risk_usd / (|entry - stop| x 100))   # MCL: $100 per $1
    contracts = min(contracts, firm_max_micros)

`risk_usd` comes from the prop-rules engine (below), never a share of
equity. Under 1 contract: no trade.

### 4. Prop-rules engine (`services/prop_rules/`, deterministic)

A gate in front of `place_bracket`, like the event blackout today. It reads
the venue's account P&L each tick and refuses entries that could break:
- **Max loss:** static or trailing, intraday or end of day, per firm. Keep
  a 25% buffer: stop trading at 75% of the room used.
- **Daily loss limit:** stop new entries for the day at about 60% of it.
- **Consistency rule:** best day under X% of total profit. Warn, and cap
  size on a big day.
- **Contract limits.**
- **Session rules:** CME Globex Sun 18:00 to Fri 17:00 ET, daily halt
  17:00 to 18:00 ET. No entries 30 min before the halt. Flatten before the
  close where the firm bans overnight (FFN).
- **Firm news rules:** FFN's EIA flat window, merged into the existing
  `event_blackout`. Ours already covers EIA, FOMC, CPI, OPEC+ and elections.

Each firm is one config file (`services/prop_rules/firms/daytraders_static_50k.json`).
Tests replay our real ledger through each config.

### 5. Credentials and where it runs

- **Credentials:** `.env.local`: `RITHMIC_USER`, `RITHMIC_PASSWORD`,
  `RITHMIC_SYSTEM`, `RITHMIC_APP_NAME` (or `PROJECTX_USERNAME`,
  `PROJECTX_API_KEY`). Declared in `scripts/preflight_secrets.py` like
  `HYPERLIQUID_VPS_PRIVATE_KEY`. Never committed.
- **DayTraders:** the Mac or the rented server (`scripts/vps/`), with
  Claude Code logged in to Max as now.
- **Topstep:** the Mac only.

### 6. What stays the same

`/desk-loops`, the 15-minute ticks, `trigger_watch.py`, the thesis writer
(local Claude, no hosted API), no-break, the event calendar and blackout,
the review and its yes/no on leaks, and the one-trader-per-account rule.

## Build plan

| Phase | What | Done when | Effort |
|---|---|---|---|
| 0 | Written OK from DayTraders support (and Topstep if Mac-only is fine) for "a Claude Code agent on my server placing 1-4 bracket orders a day through Rithmic". Ask: R\|Protocol API access for a custom app (conformance? `app_name`?), paper/eval credentials | a support transcript in `docs/prop/` | 1 day of waiting |
| 1 | Venue contract + `HyperliquidVenue` (no behavior change) + parity tests | gate suite green, live desk unchanged | 1 session |
| 2 | `RithmicVenue` on async_rithmic against Rithmic Paper Trading (or `ProjectXVenue` on project-x-py against a Topstep Combine) | e2e: place, read, cancel, close a 1-MCL bracket on paper | 1-2 sessions |
| 3 | **Shadow mode**: `oil_cme` desk runs every tick in dry-run next to the live desk for 2 weeks, logging what it would place, fills checked against CME bars | shadow ledger in the review; same R as the real desk ±noise | 2 weeks, no money |
| 4 | Prop-rules engine + firm config, replayed over the shadow and real ledgers | no replayed day breaks a rule | 1 session |
| 5 | Buy one evaluation (DayTraders Static 50k, $50), 1 MCL, live on the eval | first 20 trades logged and reviewed | ongoing |

## Costs (monthly, approximate)

| | DayTraders | Topstep |
|---|---|---|
| Evaluation | $50 (Static 50k, one-time per attempt) | ~$49-165/mo (third-party) |
| API | none found (Rithmic bundled) | $29/mo ProjectX |
| Data | add-ons $10-30 if depth is needed (1h/15m bars do not need depth) | included |
| Claude | the Max plan we already pay | same |

## Risks and open questions

- **Bot enforcement is discretionary.** A forum report says one firm
  treated ATM bracket management as "bot activity". Written confirmation
  first.
- **Rithmic custom apps:** async_rithmic needs Rithmic's `.proto` files and
  usually a conformance step for a new `app_name`. Whether a DayTraders
  login can connect a custom app is the first support question. Fallback:
  a conformed platform with a scripting API (NinjaTrader, Sierra Chart DTC,
  Quantower) acting as the bridge.
- **WTI is not Brent.** The thesis and its Polymarket/Hormuz inputs carry
  over, but the Brent-WTI spread moves. The `oil_cme` desk's own review
  judges it.
- **CME closes.** No weekend trading, a daily halt and gaps on Sunday open.
  Stops rest on the exchange, but a Sunday gap can fill past them.
- **Static intraday drawdown (DayTraders Static)** counts open losses. A
  20x-style wide swing is not allowed; 1 MCL keeps a normal stop under $100.
- **One account, one trader** still holds: never run the Hyperliquid oil
  desk and `oil_cme` on correlated sizes without the review counting both.

## Sources

- DayTraders automation policy: https://daytraders.com/help/articles/17704087 (fetched 2026-11-05: "You can use AI tools, automations, scripts, custom algorithms and trading bots..."; "Running your platform or tools on a VPS")
- DayTraders VPS / instruments / rules: daytraders.com/help/articles 17704093, 17704134, 17704074, 17704079
- Topstep API access: https://help.topstep.com/en/articles/11187768 (updated 2026-09-17: "The use of VPS, VPNs, and remote servers is prohibited"; "Live funded accounts are not allowed to trade through the ProjectX API")
- Topstep instruments: help.topstepbrokerage.com/en/articles/15033698
- ProjectX Gateway API: https://gateway.docs.projectx.com/docs/intro (auth, `Order/place` brackets, `History/retrieveBars`, rate limits)
- ProjectX third-party shutdown (Feb 2026): financemagnates.com
- MyFundedFutures fair play: intercom.help/myfundedfutures/en/articles/8444599
- Funded Futures Network FAQ and platforms: fundedfuturesnetwork.com/faq, /preferred-platforms
- Bulenox Terms of Use and rates: bulenox.com/legal/Terms_of_Use.pdf, Bulenox-Rates.pdf
- Apex automation ban: support.apextraderfunding.com/hc/en-us/articles/31519788944411 (search cache; direct fetch 403)
- Alpha Futures: help.alpha-futures.com/en/articles/9508585
- Elite Trader Funding ToS: elitetraderfunding.app/terms-of-service (2026-11-04)
- Rithmic Python client: https://github.com/rundef/async_rithmic ; ProjectX Python SDK: https://github.com/TexasCoding/project-x-py
- Tradovate API (fallback): https://partner.ninjatrader.com/ (`placeOSO` brackets, 80-min tokens)
- CME contract specs: cmegroup.com/markets/energy/crude-oil (MCL 100 bbl, $0.01 = $1; CL 1,000 bbl, $0.01 = $10), cross-checked on broker sites; verify before phase 2
