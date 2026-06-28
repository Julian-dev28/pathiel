# Audit data sources (read-only — never touch live code/loop)

Python: `/Users/julian_dev/Documents/code/hermes-trader/.venv/bin/python` (needs `.env.local` loaded for API).
Load env: read `/Users/julian_dev/Documents/code/hermes-trader/.env.local`, set each `K=V` into os.environ.

## Session log (the "activity feed") — `~/.hermes-trader-session-log.jsonl`
86MB / ~368k JSONL events. PARSE EFFICIENTLY: stream line-by-line, filter by `event`, focus a RECENT
window (last ~10-14 days by `ts` ms) so the forward-fetch is bounded and the behavior is current. Dedupe coins.
Event shapes (keys vary; guard with .get):
- `ta_skip` (263k): TA-filter rejections. coin + reason.
- `research` (31k): AI verdict. `{coin, verdict: LONG|SHORT|PASS, confidence, reasoning, entry_px, stop_px, tp_px, ts}`.
- `entry_preflight` (1.4k): PRE-research skips. `{coin, score, trigger_score, reason}` — reason ∈ {runner_gate_blocked…, liquidity_floor_preflight…, history_floor_preflight…, daily_giveback_gate…, insufficient_free_margin_preflight…, cooldown…}.
- `execute` (14k): `{coin, side, executed: bool, detail, blocked_by, size_usd, entry_px, stop_px, tp_px, ts}`.
- `scan` (27k): heartbeat w/ `coins` + `coin_scores` [{coin, score, triggers}].
- strategy-book events: `rally_exhaustion`, `engulf_short`, `crash_continue_div_short`, `premium_fade_short`,
  `hail_mary_short`, `extreme_fade_candidates`, `xs_rebalance` — each logs its candidates/opened coins (use to
  ATTRIBUTE a coin's open to a book; everything else opened = main-engine).
- exits: `dsl_exit`, `ai_close`, `close_position`.

## Fills / realized PnL (HL API)
```python
from hermes_trader.client.hl_client import resolve_user_address, _http_post, fetch_hl_candles
addr = resolve_user_address()
fills = _http_post('/info', {'type':'userFillsByTime','user':addr,'startTime': since_ms})
# each fill: {coin, dir ('Open Long'/'Close Long'/'Open Short'/'Close Short'), px, sz, closedPnl, fee, time, oid}
```
Realized PnL = sum(closedPnl) on closing fills; net = closedPnl - fee.

## Forward prices (did a coin moon/crash AFTER an event?)
`fetch_hl_candles(coin, '1d'|'1h'|'5m', count)` → recent candles ending now. For an event at ts T, the
forward window is candles with t > T. Compute fwd max-up / max-down / N-day return. Cache/dedupe per coin.

## Rules
Read-only, no live-code edits, no pytest on the live tree, no order placement. Survivorship: a skipped coin
that later delisted is absent → a skip that avoided a death looks neutral (be aware). Write outputs to
`scratchpad/findings/<name>.md` (+ scripts to `scratchpad/`). Be honest; a "the gate was RIGHT" result is as
valuable as "the gate cost us alpha".
