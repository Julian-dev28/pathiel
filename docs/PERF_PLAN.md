# Performance plan — addressable in Python without rewriting

Five fixes ranked by **risk-adjusted impact**. Do in order; each is shippable
independently.

---

## 1. Persistent HTTP connection pool (lowest risk, ~10-15% scan speedup)

**Problem:** every `_http_post` in `hermes_trader/client/hl_client.py:88` opens
a fresh TCP + TLS handshake. At 60 markets × 2 fetches per scan + 8 dex
queries + heartbeat ledger, that's ~140 handshakes per minute. Each handshake
adds 50-200ms on first connect.

**Fix:** module-level `requests.Session()` (or `httpx.Client(http2=True)`).

**File:** `hermes_trader/client/hl_client.py`

**Sketch:**
```python
# top of file
_session: requests.Session | None = None
_session_lock = threading.Lock()

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                # Pool size matches our max parallelism (8 dex queries + headroom)
                a = requests.adapters.HTTPAdapter(
                    pool_connections=16, pool_maxsize=16,
                    max_retries=urllib3.Retry(total=2, backoff_factor=0.3,
                                              status_forcelist=[502, 503, 504]),
                )
                s.mount("https://", a)
                _session = s
    return _session

def _http_post(path, payload):
    r = _get_session().post(f"{HL_API}{path}", json=payload, timeout=5)
    r.raise_for_status()
    return r.json()
```

**Risk:** thread-safety. `requests.Session` is mostly thread-safe but adapter
internals aren't. With 8 concurrent dex queries in `fetch_account_state`
fan-out, we may hit edge cases. Mitigation: `httpx.Client` is explicitly
thread-safe; prefer it.

**Rollback:** revert the file; sync POST returns instantly.

**Verify:** time a scan before/after with `time python3 -c "..."`. Expect
~1-2s improvement on a full 60-market scan.

---

## 2. More TTL caches on read-heavy endpoints (low risk, big UX win)

**Problem:** the dashboard polls `/api/dashboard/summary`, `/equity-curve`,
`/closed-trades` every few seconds. Each re-reads the session log (now 800KB+)
from disk and re-parses JSONL. The positions endpoint already has a 5s TTL
(shipped); others don't.

**Fix:** wrap `_summary_payload`, `_equity_curve_payload`, `_closed_trades_payload`
in TTL caches.

**File:** `hermes_trader/dashboard.py`

**Sketch:**
```python
_CACHE: dict[str, tuple[float, Any]] = {}

def _ttl_cache(key: str, ttl: float, fn):
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val

# usage:
def dashboard_summary():
    return JSONResponse(_ttl_cache("summary", 2.0, _summary_payload))
```

TTLs:
- `summary`: 2s (kpis update fast)
- `equity-curve`: 30s (24h chart doesn't change visibly)
- `closed-trades`: 10s (new closes are rare)
- `operator/trackers`: 5s

**Risk:** very low — pure read endpoints.

**Verify:** `curl -w "%{time_total}\n" http://localhost:8000/api/dashboard/summary`
in a loop. Should hit ~0.5ms on cache hits.

---

## 3. Token-bucket rate limiter (medium, fixes 429 storms)

**Problem:** the backtest scripts and live loop both hit HL hard during scan
fan-out. Current pacing is `time.sleep(0.3)` between batches — crude. The
backtest gets 200+ "no candles" skips from 429s.

**Fix:** central rate limiter using a token bucket. HL allows 1200 weight/min;
budget tokens centrally; every `_http_post` acquires before sending.

**File:** new `hermes_trader/client/rate_limit.py`, integrated in `_http_post`.

**Sketch:**
```python
class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self._capacity = capacity
        self._tokens = capacity
        self._refill = refill_per_sec
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 20, max_wait: float = 5.0) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._refill,
                )
                self._last = now
                if self._tokens >= weight:
                    self._tokens -= weight
                    return True
                sleep_for = (weight - self._tokens) / self._refill
            if time.monotonic() + sleep_for > deadline:
                return False
            time.sleep(min(sleep_for, 0.5))

# HL: 1200 weight/min = 20/sec. Capacity 1200 = burst 1min.
_HL_LIMITER = TokenBucket(capacity=1200, refill_per_sec=20.0)

# in _http_post:
weight = _endpoint_weight(payload.get("type"))  # 20 for candles, 2 for mids
if not _HL_LIMITER.acquire(weight):
    raise RuntimeError("rate budget exhausted; backing off")
```

**Endpoint weights** (per HL docs):
- `candleSnapshot`: 20
- `allMids`: 2
- `clearinghouseState`: 2
- `l2Book`: 2
- `userNonFundingLedgerUpdates`: 2 (probably)
- `metaAndAssetCtxs`: 20

**Risk:** if the limiter is too tight, scans block waiting. If too loose, still
get 429s. Tune `refill_per_sec` from 20 → 18 if 429s persist.

**Verify:** run `scripts/backtest_full.py --hours 4 --tick-min 5` and check
the log for 429 count. Should go from 100s → near zero.

---

## STATUS (2026-05-28): #1, #2, #3 shipped. #4, #5 deliberately SKIPPED.

After shipping the connection pool (#1), TTL caches (#2), and token-bucket
rate limiter (#3), measured scan times:
- cold-cache scan (5m + 1h refetch): ~47s — paced by the rate limiter
- warm scan (1h cached): **4.5s** (down from 9s — connection-pool win)

**Key finding that invalidates #4/#5's premise:** the workload is now
*rate-limit bound*, not latency bound. HL allows ~1200 weight/min; a full
5m-candle scan is ~1200 weight. Async/parallel requests (#4) and background
prefetch (#5) fire requests *faster*, but the token bucket throttles total
throughput to the same per-minute ceiling regardless of concurrency. So
they'd add real regression risk (async rewrite of the hot trading path) for
**zero throughput gain**. Skipped intentionally.

If HL ever raises the per-IP weight budget, revisit — async would then
convert the freed budget into lower latency. Until then, the win is in
*reducing request count* (longer cache TTLs, fewer markets), not parallelism.

Original #4/#5 notes kept below for reference.

---

## 4. Async HTTP everywhere (highest impact, highest blast radius) [SKIPPED — see status above]

**Problem:** scan fans out 60 markets sequentially in batches of 20 with thread
pools. The thread overhead + GIL is fine, but the *batching* is wasteful — we
could fire all 60 candle fetches in parallel async coroutines and the event
loop would handle them in ~1 RTT × max-parallelism instead of 3 batches × 7s.

**Fix:** convert `_http_post` and `fetch_hl_candles` to async; use `httpx.AsyncClient`.
`scan_once` becomes async; trading_loop's main loop already drives an asyncio
event loop for the heartbeat path, so this slots in.

**File:** `hermes_trader/client/hl_client.py` + `hermes_trader/agents/perception.py` + scripts.

**Strategy:**
1. Add `_http_post_async` alongside the sync version (don't break callers)
2. Add `fetch_hl_candles_async`
3. Convert `_scan_single_market` to async, use `asyncio.gather` to run all
   markets in parallel
4. Migrate other call sites file-by-file
5. Eventually remove sync versions

**Sketch:**
```python
async def _http_post_async(path: str, payload: dict) -> Any:
    async with _async_client() as c:
        r = await c.post(f"{HL_API}{path}", json=payload, timeout=5)
        r.raise_for_status()
        return r.json()

# in perception:
async def scan_once_async(...):
    candle_tasks = [
        fetch_hl_candles_async(m["coin"], "5m", 100) for m in markets
    ]
    candle_results = await asyncio.gather(*candle_tasks, return_exceptions=True)
    # ... rest of scan logic
```

**Risk:** highest. Touches the hot path. Need:
- All 57 tests still pass (some test scan_once directly — keep sync wrapper for them)
- Live trading loop unchanged in behavior (just faster)
- Error handling for partial async failures
- Rate limiter (#3) becomes async-aware (use anyio or async semaphore)

**Rollback:** keep sync versions, flip an env flag to disable async.

**Verify:** scan time goes from ~9s → ~2-3s. Big win.

---

## 5. Background prefetching (depends on #4)

**Problem:** during a scan, we sit idle for ~3s waiting on LLM research per
triggered candidate. Meanwhile the NEXT scan's candles are stale and about to
need fetching.

**Fix:** after kicking off LLM research for the current scan's candidates,
start an async task to pre-fetch candles for the next scan in the background.
When the LLM responses come back and we move to the next tick, candles are
already warm in the cache.

**File:** `hermes_trader/agents/perception.py` (after async migration)

**Sketch:**
```python
async def scan_loop():
    while True:
        candles = await fetch_universe_candles()  # first tick: full fetch
        prefetch_task = None
        while True:
            perceptions = run_triggers(candles)
            # Kick off prefetch in background while we do AI research
            prefetch_task = asyncio.create_task(fetch_universe_candles())
            await run_research_and_execute(perceptions)
            await asyncio.sleep(scan_interval - elapsed)
            candles = await prefetch_task  # already warm
```

**Risk:** moderate. Need to handle the case where prefetch fails — fall back
to fresh fetch. Don't let prefetch task accumulate if scan is slow.

**Verify:** scan-to-scan latency drops from ~9s to ~0.5s (just the trigger
math + LLM). Effective scan cadence becomes whatever the LLM step takes.

---

## Order of operations

1. **Persistent connection pool (#1)** — 30 min, isolated, low risk
2. **TTL caches everywhere (#2)** — 1 hour, isolated, low risk
3. **Token-bucket rate limiter (#3)** — 2-3 hours, new module, integration in `_http_post`
4. **Async HTTP (#4)** — half-day to full day, biggest payoff, highest care needed
5. **Background prefetching (#5)** — couple hours, only after #4

Total: ~1.5 days of focused work. After all five:
- Scan latency: 9s → 2-3s (60-70% reduction)
- Dashboard load: already fast, becomes instant on all endpoints
- 429 errors: rare to zero
- Backtest reliability: full window completes without truncation

## Don't do

- Rewrite to Go/Rust. Network-bound code doesn't benefit; ecosystem cost is huge.
- Convert to `pandas` for triggers. Our trigger math is already vectorless and
  fast (<10ms). pandas adds memory overhead and serialization cost.
- Build a thread pool from scratch. `concurrent.futures.ThreadPoolExecutor`
  works fine; the wins come from async I/O, not better threading.

## Test gates before declaring each fix done

- `python3 -m pytest tests/test_cleanup.py` — 57/57 still pass
- `scripts/restart.sh restart` succeeds clean
- `tail logs/trading_loop.log` shows a healthy scan within 60s
- For #3 and #4: `scripts/backtest_full.py --hours 4 --tick-min 5` runs
  without 429 storms (look for `429 Client Error` count → near zero)
