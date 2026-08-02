"""Microstructure edges on Polymarket BTC 5m up/down — the three claims, tested.

A widely-shared thread on 2M updown trades made three claims. Each one is
either measurable on data we already hold or measurable by an instrument this
module builds. Nothing here trades; it measures, and it says plainly when the
answer is "the edge is not reachable from here".

CLAIM 1 — "markets at 75-90c win 3-5pp more than priced; 95c+ is overpriced"
    Needs an UNBIASED sample of market prices. Our existing 898 scout rows are
    NOT that: they were recorded only when the LLM diverged from the market, so
    the price distribution is selected. `record_window()` fixes it by snapshotting
    both books at fixed offsets in EVERY window regardless of any view, and
    `price_calibration()` grades those snapshots offline against the klines.

CLAIM 2 — "the leading side still loses 9% with 30s left; priced at ~3.5%"
    The outcome half needs no market data at all, and it REPLICATES: see
    `tail_edge()`. At 60s left, when a Gaussian random walk says the leader wins
    with 99%+ probability (n=1305 over 21 days), the leader actually wins 97.2%.
    The tail is ~14x fatter than the model says, and every extreme bucket is
    negative at both decision minutes. Price moves jump; a Gaussian does not.
    Whether that is TRADEABLE depends on where the book actually prices the
    trailing side, which is what the sampler measures.

CLAIM 3 — "buy both sides for under $1, redeem for $1"
    True arithmetic, and `pair_quote()` checks it live in both directions
    (buy both under $1 / mint a set for $1 and sell both over $1). Two facts
    bound it hard, both read off the live market payload rather than assumed:
      - `orderPriceMinTickSize` is 0.01, so complementary asks summing to 0.97
        means the book is THREE ticks crossed — a stale-quote event, not a
        standing spread.
      - Fees: Gamma advertises `takerBaseFee: 1000` on these markets, but the
        tape disagrees. Every executed trade seen on the websocket
        (`last_trade_price.fee_rate_bps`) charged **0** — 79 of 79 on
        2026-08-02. So `FEE_BPS_DEFAULT` is 0, taken from what was actually
        charged rather than what is advertised, and `observed_fee_bps()` on the
        live feed keeps checking. `fee_per_share()` stays because a market that
        does charge must still be priced correctly.

    Net of a zero fee, a crossed pair IS free money — the constraint is purely
    that the pair sits at $1.00 +/- one tick and crossing it is a latency race.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.trend_engine import env
from services.trend_engine.metrics import mean, wilson
from services.trend_engine.updown_trends import (
    WINDOW_MIN, WINDOW_MS, _curl, _et, randomwalk_prob, sigma_bp_per_min,
)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# Measured 2026-08-02 from this machine (median of 8):
#   gamma /markets?slug   188ms      clob /book (one side)   327ms
#   clob POST /books      302ms      <- BOTH sides in a single round trip
#   curl subprocess 323ms vs keep-alive session 297ms — the TLS handshake is
#   NOT the bottleneck, raw RTT to their edge is. So the wins that exist in
#   code are (a) batch the two books into one call and (b) never pay for the
#   slug lookup on the critical path. 840ms -> ~300ms. The remaining 300ms is
#   geography: only a box closer to their infra, or the CLOB websocket, moves it.
_TOKENS: Dict[str, List[str]] = {}          # slug -> [UP token, DOWN token]
_SESSION: Any = None
SAMPLES = os.path.join(env.state_dir(), "trend_engine", "updown_book_samples.jsonl")
# Offsets (seconds REMAINING in the window) at which the sampler snapshots both
# books. Chosen to bracket the thread's "30 seconds left" claim and to give a
# price path, not a point.
SAMPLE_OFFSETS_S: Tuple[int, ...] = (240, 180, 120, 60, 30)
# MEASURED, not advertised. Gamma's payload says takerBaseFee = 1000 on these
# markets; 79 of 79 executed trades on the websocket reported fee_rate_bps = 0
# (2026-08-02). Trusting the Gamma field understated every arb by up to 5c a
# share and produced the wrong verdict on the both-sides trade.
FEE_BPS_DEFAULT = 0.0
GAMMA_ADVERTISED_FEE_BPS = 1000.0
TICK = 0.01


# ── fees ─────────────────────────────────────────────────────────────────────


def fee_per_share(price: float, fee_bps: float = FEE_BPS_DEFAULT) -> float:
    """Polymarket's binary-outcome fee: rate x min(p, 1-p) x shares.

    Symmetric in the two outcomes, which is why it cannot be dodged by picking
    a side, and why it dominates every tick-level edge on these markets.
    """
    p = max(0.0, min(1.0, float(price)))
    return (fee_bps / 10_000.0) * min(p, 1.0 - p)


def market_fee_bps(market: Optional[Dict[str, Any]],
                   observed: Optional[float] = None) -> float:
    """The fee rate to price with, in bps.

    Precedence: what the tape CHARGED (`observed`, from executed trades) beats
    what Gamma ADVERTISES, because they disagree and only one of them takes
    money. Falls back to the measured default rather than the advertised field
    — a wrong fee here flips the sign of every arb verdict.
    """
    if observed is not None:
        return float(observed)
    if not market:
        return FEE_BPS_DEFAULT
    for key in ("takerBaseFee", "makerBaseFee", "fee"):
        v = market.get(key)
        if v is not None:
            try:
                advertised = float(v)
            except Exception:
                continue
            # Only trust the advertised number when it is NOT the value we have
            # already disproved on the wire.
            return FEE_BPS_DEFAULT if advertised == GAMMA_ADVERTISED_FEE_BPS else advertised
    return FEE_BPS_DEFAULT


# ── live book pair ───────────────────────────────────────────────────────────


def _session() -> Any:
    """Keep-alive HTTP session (worth ~25ms/call, and it stops spawning curl)."""
    global _SESSION
    if _SESSION is None:
        import requests
        _SESSION = requests.Session()
        _SESSION.headers.update({"Content-Type": "application/json"})
    return _SESSION


def slug_for(ts: Optional[float] = None) -> str:
    t = int(time.time() if ts is None else ts)
    return f"btc-updown-5m-{(t // 300) * 300}"


def tokens_for(slug: str, getter: Optional[Callable[[str], Any]] = None,
               cache: Optional[Dict[str, List[str]]] = None) -> Optional[List[str]]:
    """[UP, DOWN] token ids for a window slug, cached forever.

    Token ids are immutable once the market exists, so this is a pure lookup
    that never needs repeating — and keeping it OFF the critical path is worth
    188ms on every quote. `prewarm()` fills it for the next window while the
    current one is still running.
    """
    cache = _TOKENS if cache is None else cache
    hit = cache.get(slug)
    if hit:
        return hit
    get = getter or _curl
    m = get(f"{GAMMA}/markets?slug={slug}")
    if not (isinstance(m, list) and m and isinstance(m[0], dict)):
        return None
    try:
        toks = json.loads(m[0].get("clobTokenIds") or "[]")
    except Exception:
        return None
    if len(toks) != 2:
        return None
    cache[slug] = toks
    # the rest of the payload is worth keeping too — fee and tick come from it
    cache[slug + ":market"] = m[0]          # type: ignore[assignment]
    return toks


def prewarm(ts: Optional[float] = None,
            getter: Optional[Callable[[str], Any]] = None) -> Optional[List[str]]:
    """Resolve the NEXT window's tokens now, so its first quote costs one call."""
    nxt = (int(time.time() if ts is None else ts) // 300) * 300 + 300
    return tokens_for(f"btc-updown-5m-{nxt}", getter=getter)


def books_batch(token_ids: Sequence[str],
                poster: Optional[Callable[[str, Any], Any]] = None) -> List[Dict[str, Any]]:
    """Both order books in ONE round trip via the CLOB's POST /books.

    Measured at the same ~300ms as a single GET /book, so the pair costs half
    what two sequential GETs do. Falls back to sequential GETs if the batch
    endpoint misbehaves — a slower read beats no read.
    """
    payload = [{"token_id": t} for t in token_ids]
    try:
        if poster is not None:
            raw = poster(f"{CLOB}/books", payload)
        else:
            resp = _session().post(f"{CLOB}/books", json=payload, timeout=10)
            raw = resp.json()
        if isinstance(raw, list) and len(raw) == len(token_ids):
            by_id = {str(b.get("asset_id")): b for b in raw if isinstance(b, dict)}
            ordered = [by_id.get(str(t)) for t in token_ids]
            if all(o is not None for o in ordered):
                return ordered            # type: ignore[return-value]
            return raw                    # order unknown; caller still gets both
    except Exception:
        pass
    out = []
    for t in token_ids:
        b = _curl(f"{CLOB}/book?token_id={t}")
        out.append(b if isinstance(b, dict) else {})
    return out


def _top(levels: Optional[List[Dict[str, Any]]], best: str) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    rows = sorted(levels, key=lambda x: (float(x["price"]) if best == "ask" else -float(x["price"])))
    return float(rows[0]["price"]), float(rows[0]["size"])


def current_market(now: Optional[float] = None,
                   getter: Optional[Callable[[str], Any]] = None) -> Optional[Dict[str, Any]]:
    """The current window's market payload, cached per slug.

    Cached because a window's tokens, tick and fee never change once it exists,
    and this lookup is 188ms that has no business on a quote's critical path.
    """
    slug = slug_for(now)
    cached = _TOKENS.get(slug + ":market")
    if isinstance(cached, dict):
        return cached
    if tokens_for(slug, getter=getter) is None:
        return None
    m = _TOKENS.get(slug + ":market")
    return m if isinstance(m, dict) else None


WS_WARM_S = 3.0


def warm_feed(market: Optional[Dict[str, Any]], wait_s: float = WS_WARM_S) -> bool:
    """Start + subscribe the socket for this window and wait for both legs.

    `pair_quote` subscribes lazily and falls back to REST until the first book
    lands, which is fine in a daemon and wrong in a SHORT-LIVED process: the
    lane refresher subscribes, quotes over REST in the same millisecond, records
    a health block from a socket that never got to connect, and exits. That
    snapshot then renders on the dashboard as a permanently dead feed while the
    server's own socket is live. Waiting here makes the cached snapshot mean
    what it says.

    Returns True when both legs are two-sided on the socket.
    """
    if not market:
        return False
    try:
        toks = json.loads(market.get("clobTokenIds") or "[]")
    except Exception:
        return False
    if len(toks) != 2:
        return False
    try:
        from services.trend_engine.updown_ws import feed as ws_feed
        f = ws_feed()                       # creates and starts the thread
        f.subscribe(list(toks))
        return bool(f.wait_pair(toks, wait_s)) if wait_s > 0 else False
    except Exception:
        return False


def pair_quote(market: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
               getter: Optional[Callable[[str], Any]] = None,
               fee_bps: Optional[float] = None,
               use_ws: bool = True) -> Dict[str, Any]:
    """Both sides of the current window's book, priced for the two arbs.

    `buy_both` = pay both asks, redeem the winner for $1.
    `sell_both` = mint a complete set for $1, hit both bids.

    Reads the WEBSOCKET feed when it is live and fresh (~0ms, and fresher than
    any poll), falling back to the batched REST call otherwise. `source` on the
    result says which one answered, so a silent fallback to a 300ms path is
    visible instead of assumed.

    Gross edge ignores fees; net subtracts the per-share fee on BOTH legs at
    the rate the tape actually charged.
    """
    get = getter or _curl
    market = market if market is not None else current_market(now, getter=get)
    if not market:
        return {"status": "no_market"}
    try:
        toks = json.loads(market.get("clobTokenIds") or "[]")
    except Exception:
        toks = []
    if len(toks) != 2:
        return {"status": "no_tokens"}
    observed = None
    sides: Optional[List[Dict[str, Any]]] = None
    source = "rest"

    if use_ws and getter is None:
        try:
            from services.trend_engine.updown_ws import feed as ws_feed
            f = ws_feed()
            f.subscribe(list(toks))
            rows = f.pair(list(toks))
            observed = f.observed_fee_bps()
            if rows:
                cand = [{"side": n, "bid": r.get("bid"), "ask": r.get("ask"),
                         "bid_size": r.get("bid_size"), "ask_size": r.get("ask_size"),
                         "age_s": r.get("age_s")}
                        for n, r in zip(("UP", "DOWN"), rows)]
                # A just-subscribed token has a row but no prices in it until
                # the first price_change lands. Accepting that hollow row makes
                # a live arb read as "no crossing" — silently, and exactly at
                # the moment a new 5m window opens. Fall through to REST until
                # BOTH legs are two-sided.
                if all(c["bid"] is not None and c["ask"] is not None for c in cand):
                    sides = cand
                    source = "websocket"
        except Exception:
            sides = None

    fee_bps = market_fee_bps(market, observed) if fee_bps is None else fee_bps

    if sides is None:
        # ONE round trip for both sides (see the latency note at the top). The
        # injected-getter path stays sequential so tests can stub a plain GET.
        books = books_batch(toks) if getter is None else [
            get(f"{CLOB}/book?token_id={t}") for t in toks]
        sides = []
        for name, book in zip(("UP", "DOWN"), books):
            if not isinstance(book, dict):
                return {"status": "no_book", "side": name}
            bid, bid_sz = _top(book.get("bids"), "bid")
            ask, ask_sz = _top(book.get("asks"), "ask")
            sides.append({"side": name, "bid": bid, "ask": ask,
                          "bid_size": bid_sz, "ask_size": ask_sz})
    up, dn = sides
    out: Dict[str, Any] = {
        "status": "ok",
        "slug": market.get("slug"),
        "source": source,
        "fee_bps": fee_bps,
        "fee_bps_observed": observed,
        "fee_bps_advertised": market.get("takerBaseFee"),
        "tick": float(market.get("orderPriceMinTickSize") or TICK),
        "min_order": float(market.get("orderMinSize") or 0.0),
        "up": up, "down": dn,
        "ts": int(time.time() if now is None else now),
    }

    if up["ask"] is not None and dn["ask"] is not None:
        cost = up["ask"] + dn["ask"]
        fees = fee_per_share(up["ask"], fee_bps) + fee_per_share(dn["ask"], fee_bps)
        out["buy_both"] = {
            "cost": round(cost, 4),
            "gross_edge": round(1.0 - cost, 4),
            "fees": round(fees, 4),
            "net_edge": round(1.0 - cost - fees, 4),
            "size": min(up["ask_size"] or 0.0, dn["ask_size"] or 0.0),
            "profitable": bool(1.0 - cost - fees > 0),
        }
    if up["bid"] is not None and dn["bid"] is not None:
        credit = up["bid"] + dn["bid"]
        fees = fee_per_share(up["bid"], fee_bps) + fee_per_share(dn["bid"], fee_bps)
        out["sell_both"] = {
            "credit": round(credit, 4),
            "gross_edge": round(credit - 1.0, 4),
            "fees": round(fees, 4),
            "net_edge": round(credit - 1.0 - fees, 4),
            "size": min(up["bid_size"] or 0.0, dn["bid_size"] or 0.0),
            "profitable": bool(credit - 1.0 - fees > 0),
        }
    best = max((out.get(k, {}).get("net_edge", -9) for k in ("buy_both", "sell_both")),
               default=-9)
    out["best_net_edge"] = None if best == -9 else best
    out["arb"] = bool(best > 0)
    # How many ticks the book would have to cross before ANY gross arb exists.
    if out.get("buy_both"):
        out["ticks_to_gross_arb"] = max(0, round(out["buy_both"]["cost"] * 100 - 100) + 1)
    return out


# ── the unbiased sampler (claim 1's missing instrument) ──────────────────────


def sample_row(quote: Dict[str, Any], window_start_ms: int, secs_left: float,
               spot: Optional[float], open_px: Optional[float],
               sigma_bp_min: Optional[float]) -> Dict[str, Any]:
    """One snapshot: book state + model state + everything needed to grade later.

    Deliberately records the BOOK and not a derived probability, so a later
    change to the model cannot retro-fit the record.
    """
    up, dn = quote.get("up") or {}, quote.get("down") or {}
    move_bp = ((spot / open_px - 1.0) * 10_000
               if (spot and open_px and open_px > 0) else None)
    p_model = (randomwalk_prob(move_bp, sigma_bp_min, secs_left / 60.0)
               if (move_bp is not None and sigma_bp_min) else None)
    return {
        "ts": int(time.time()),
        "window_start_ms": int(window_start_ms),
        "window_end_ms": int(window_start_ms) + WINDOW_MS,
        "secs_left": round(secs_left, 1),
        "slug": quote.get("slug"),
        "up_bid": up.get("bid"), "up_ask": up.get("ask"),
        "up_bid_size": up.get("bid_size"), "up_ask_size": up.get("ask_size"),
        "down_bid": dn.get("bid"), "down_ask": dn.get("ask"),
        "down_bid_size": dn.get("bid_size"), "down_ask_size": dn.get("ask_size"),
        "mid_up": (round((up["bid"] + up["ask"]) / 2, 4)
                   if up.get("bid") is not None and up.get("ask") is not None else None),
        "buy_both_net": (quote.get("buy_both") or {}).get("net_edge"),
        "sell_both_net": (quote.get("sell_both") or {}).get("net_edge"),
        "buy_both_gross": (quote.get("buy_both") or {}).get("gross_edge"),
        "sell_both_gross": (quote.get("sell_both") or {}).get("gross_edge"),
        "fee_bps": quote.get("fee_bps"),
        "fee_bps_observed": quote.get("fee_bps_observed"),
        # push vs poll: a REST snapshot is up to 300ms stale, a websocket one is
        # not. Recording which answered keeps that out of the analysis' blind spot.
        "source": quote.get("source"),
        "book_age_s": (quote.get("up") or {}).get("age_s"),
        "spot": spot, "open_px": open_px, "move_bp": move_bp,
        "sigma_bp_per_min": sigma_bp_min, "p_model_up": p_model,
    }


def append_samples(rows: Sequence[Dict[str, Any]], path: str = SAMPLES) -> int:
    if not rows:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def load_samples(path: str = SAMPLES, cap: int = 200_000) -> List[Dict[str, Any]]:
    try:
        with open(path) as fh:
            lines = fh.readlines()[-cap:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def record_window(offsets_s: Sequence[int] = SAMPLE_OFFSETS_S,
                  path: str = SAMPLES, sleeper: Callable[[float], None] = time.sleep,
                  getter: Optional[Callable[[str], Any]] = None,
                  now: Optional[float] = None,
                  clock: Callable[[], float] = time.time,
                  skip_partial: bool = True) -> List[Dict[str, Any]]:
    """Snapshot both books at each offset of ONE window, unconditionally.

    Unconditional is the entire point: a sampler that only fires when something
    looks interesting reproduces the selection bias that makes the existing 898
    scout rows unusable for a calibration curve.

    Blocks for up to one window (~5 min) by design — it runs as its own
    scheduled job, never inside a request.
    """
    from services.trend_engine.updown_trends import live_spot, load_1m, build_windows, enrich

    # One clock for both the window maths and the waits: mixing `now` with a
    # real `time.time()` made every offset look already-past under a fake clock.
    t0 = clock() if now is None else now
    w_start = int(t0 // 300) * 300
    # If the widest offset has already gone by, this window is a partial: the
    # daemon would otherwise burn through the tail of the window it just
    # finished and log a one-snapshot record for it. Wait for a whole one.
    if skip_partial and offsets_s:
        first_target = w_start + (WINDOW_MIN * 60 - max(offsets_s))
        if t0 > first_target:
            w_start += WINDOW_MIN * 60
    market = current_market(w_start, getter=getter)
    if not market:
        # the next window's market may not be listed yet — wait for its open
        wait = (w_start + 5) - clock()
        if wait > 0:
            sleeper(wait)
        market = current_market(w_start, getter=getter)
        if not market:
            return []
    bars = load_1m(1440)
    ws = enrich(build_windows(bars))
    sigma = sigma_bp_per_min(ws) if ws else None
    open_px = None
    for w in reversed(ws):
        if w["t"] == w_start * 1000:
            open_px = w["open"]
            break
    if open_px is None and ws:
        open_px = ws[-1]["close"]

    # Warm the socket BEFORE the first offset. pair_quote() subscribes lazily
    # and falls back to REST until the first book arrives, so without this the
    # earliest snapshot of every window is a 300ms-stale poll. No wait needed:
    # the loop below sleeps to the first offset anyway.
    if getter is None:
        warm_feed(market, wait_s=0.0)

    rows: List[Dict[str, Any]] = []
    for secs_left in sorted(offsets_s, reverse=True):
        target = w_start + (WINDOW_MIN * 60 - secs_left)
        wait = target - clock()
        if wait > 0:
            sleeper(wait)
        elif wait < -20:                      # already well past this offset
            continue
        q = pair_quote(market, getter=getter)
        if q.get("status") != "ok":
            continue
        rows.append(sample_row(q, w_start * 1000, secs_left, live_spot(), open_px, sigma))
    append_samples(rows, path)
    return rows


# ── grading the samples (all three claims, on our own data) ──────────────────


def _bucketed(pairs: Sequence[Tuple[float, bool]],
              edges: Sequence[float]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sel = [(p, w) for p, w in pairs if lo <= p < hi]
        if not sel:
            continue
        n = len(sel)
        k = sum(1 for _, w in sel if w)
        implied = mean([p for p, _ in sel])
        realized = k / n
        cl, ch = wilson(k, n)
        out.append({
            "bucket": f"{lo:.2f}-{hi:.2f}", "n": n,
            "implied": round(implied, 4), "realized": round(realized, 4),
            "diff_pp": round((realized - implied) * 100, 2),
            "ci_lo": round(cl, 4), "ci_hi": round(ch, 4),
            # the interval excludes the price the market charged
            "significant": bool(implied < cl or implied > ch),
        })
    return out


PRICE_EDGES = (0.0, 0.05, 0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90, 0.95, 1.0001)


def price_calibration(samples: Sequence[Dict[str, Any]],
                      resolver: Callable[[Optional[int]], Optional[bool]],
                      secs_left: Optional[int] = None) -> Dict[str, Any]:
    """CLAIM 1: is the market's own price calibrated, by price bucket?

    Uses the mid of the UP book as the market's probability, graded against the
    window's real outcome. Restrict to one `secs_left` offset to keep the
    sample homogeneous — calibration at 4 minutes out and at 30 seconds out are
    different questions.
    """
    pairs: List[Tuple[float, bool]] = []
    for s in samples:
        if secs_left is not None and int(s.get("secs_left") or -1) != secs_left:
            continue
        mid = s.get("mid_up")
        if mid is None:
            continue
        won = resolver(s.get("window_end_ms"))
        if won is None:
            continue
        pairs.append((float(mid), bool(won)))
    if len(pairs) < 30:
        return {"status": "too_small", "n": len(pairs),
                "hint": "the sampler needs to run — ~288 windows a day"}
    rows = _bucketed(pairs, PRICE_EDGES)
    hits = [r for r in rows if r["significant"]]
    return {
        "status": "ok", "n": len(pairs), "secs_left": secs_left,
        "rows": rows, "mispriced": hits,
        "verdict": ("no price bucket is mispriced beyond its own interval — "
                    "the book is calibrated at this horizon" if not hits else
                    f"{len(hits)} price bucket(s) sit outside their interval"),
    }


def tail_edge(windows: Sequence[Dict[str, Any]], minute: int = 4,
              vol_lookback: int = 24) -> Dict[str, Any]:
    """CLAIM 2, outcome half: how often does the leader actually lose?

    Pure — no market data. Replays each window at the end of `minute`, asks a
    driftless Gaussian random walk for the leader's win probability, and
    compares it with what happened. Needs no sampler and works on day one.

    The headline number is the >=99% bucket: a Gaussian says the leader is
    home; the tape says otherwise several percent of the time, because price
    jumps and a Gaussian does not.
    """
    rows: List[Tuple[float, bool]] = []
    for i, w in enumerate(windows):
        cs = w.get("c1m") or []
        if len(cs) < WINDOW_MIN or w["open"] <= 0 or minute >= WINDOW_MIN:
            continue
        sig = sigma_bp_per_min(windows[max(0, i - vol_lookback):i])
        if sig <= 0:
            continue
        move_bp = (cs[minute - 1] / w["open"] - 1.0) * 10_000
        p_up = randomwalk_prob(move_bp, sig, WINDOW_MIN - minute)
        if move_bp > 0:
            lead_p, lead_won = p_up, bool(w["up"])
        elif move_bp < 0:
            lead_p, lead_won = 1.0 - p_up, (not w["up"])
        else:
            continue                                  # dead flat: no leader
        rows.append((lead_p, lead_won))
    if len(rows) < 100:
        return {"status": "too_small", "n": len(rows)}
    edges = (0.50, 0.70, 0.80, 0.90, 0.95, 0.97, 0.99, 1.0001)
    table = _bucketed(rows, edges)
    extreme = [r for r in table if r["bucket"].startswith(("0.97", "0.99"))]
    fat = [r for r in extreme if r["diff_pp"] < 0 and r["significant"]]
    worst = min(table, key=lambda r: r["diff_pp"]) if table else None
    return {
        "status": "ok",
        "n": len(rows),
        "decision_minute": minute,
        "secs_left": (WINDOW_MIN - minute) * 60,
        "table": table,
        "tail_is_fat": bool(fat),
        "worst_bucket": worst,
        "verdict": (
            f"with {(WINDOW_MIN - minute) * 60:.0f}s left the leader is OVER-favoured by a "
            f"Gaussian: {worst['bucket']} priced {worst['implied']:.3f} resolved "
            f"{worst['realized']:.3f} (n={worst['n']}). Price jumps; a random walk does not."
            if fat and worst else
            "no significant fat tail at this horizon on this sample"),
    }


def tail_strategy_ev(samples: Sequence[Dict[str, Any]],
                     resolver: Callable[[Optional[int]], Optional[bool]],
                     max_ask: float = 0.05, secs_left: int = 30,
                     fee_bps: float = FEE_BPS_DEFAULT) -> Dict[str, Any]:
    """CLAIM 2, tradeable half: buy the CHEAP side late and hold to resolution.

    For every sample at `secs_left` where one side's ask is at or under
    `max_ask`, take it. Pays $1 if that side wins, 0 otherwise, minus the
    per-share fee. EV per $1 STAKED, so it is directly comparable to any other
    book in this repo.

    This is the strategy the thread describes as "small positions on the 5%
    side near the close". It only becomes real if the market actually prices
    the tail near the Gaussian — which is exactly what the sampler settles.
    """
    stakes: List[float] = []
    pnls: List[float] = []
    detail: List[Dict[str, Any]] = []
    for s in samples:
        if int(s.get("secs_left") or -1) != secs_left:
            continue
        won_up = resolver(s.get("window_end_ms"))
        if won_up is None:
            continue
        for side, ask_key in (("UP", "up_ask"), ("DOWN", "down_ask")):
            ask = s.get(ask_key)
            if ask is None or ask > max_ask or ask <= 0:
                continue
            side_won = won_up if side == "UP" else (not won_up)
            fee = fee_per_share(ask, fee_bps)
            pnl = ((1.0 - ask - fee) if side_won else (-ask - fee))
            stakes.append(ask)
            pnls.append(pnl)
            detail.append({"slug": s.get("slug"), "side": side, "ask": ask,
                           "won": bool(side_won), "pnl": round(pnl, 4)})
    n = len(pnls)
    if n < 20:
        return {"status": "too_small", "n": n,
                "hint": "needs more sampler runs to reach a verdict"}
    staked = sum(stakes)
    total = sum(pnls)
    wins = sum(1 for d in detail if d["won"])
    lo, hi = wilson(wins, n)
    breakeven = mean(stakes)                      # a $x ticket needs to win x of the time
    return {
        "status": "ok", "n": n, "secs_left": secs_left, "max_ask": max_ask,
        "mean_ask": round(mean(stakes), 4),
        "win_rate": round(wins / n, 4),
        "win_ci": [round(lo, 4), round(hi, 4)],
        "breakeven_win_rate": round(breakeven, 4),
        "ev_per_$staked": round(total / staked, 4) if staked > 0 else 0.0,
        "ev_per_ticket": round(total / n, 4),
        "total_pnl": round(total, 4),
        "fee_bps": fee_bps,
        "verdict": ("+EV: the cheap side wins more often than it costs, net of fees"
                    if lo > breakeven else
                    ("-EV: the cheap side is priced at or above its win rate"
                     if hi < breakeven else
                     "inconclusive — the win-rate interval straddles the breakeven price")),
    }


def row_fee_bps(row: Dict[str, Any]) -> float:
    """The fee to price a STORED sample with.

    Same distrust rule as `market_fee_bps`, applied on read instead of on
    record. Rows sampled before 2026-08-02 froze Gamma's advertised 1000 bps
    into `fee_bps`, and every `*_net` field on those rows was computed against
    it. Honouring that number would carry a disproved fee forward forever, so
    a stored 1000 is read as the measured default exactly like a live one.
    """
    v = row.get("fee_bps")
    if v is None:
        return FEE_BPS_DEFAULT
    try:
        stored = float(v)
    except (TypeError, ValueError):
        return FEE_BPS_DEFAULT
    return FEE_BPS_DEFAULT if stored == GAMMA_ADVERTISED_FEE_BPS else stored


def _net_from_gross(row: Dict[str, Any], side: str) -> Optional[float]:
    """Recompute a stored row's net edge at the fee we now trust.

    Never reads `buy_both_net` / `sell_both_net`: those are record-time values
    and a stale fee in them silently flips a real crossing to unprofitable.
    Gross is fee-free arithmetic off the book, so it is the honest input.
    """
    gross = row.get(f"{side}_both_gross")
    if gross is None:
        return None
    fee_bps = row_fee_bps(row)
    legs = ("up_ask", "down_ask") if side == "buy" else ("up_bid", "down_bid")
    prices = [row.get(k) for k in legs]
    if any(p is None for p in prices):
        # No leg prices stored: a zero fee costs nothing, anything else is
        # unpriceable, so decline rather than guess.
        return float(gross) if fee_bps == 0 else None
    fees = sum(fee_per_share(float(p), fee_bps) for p in prices)
    return float(gross) - fees


def arb_stats(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """CLAIM 3: how often does a sub-$1 pair actually exist at OUR latency?

    Counts snapshots where the two-sided book was crossed enough to pay, gross
    and net of fees. Both directions count: BUY both asks under $1 and redeem
    for $1, or SELL both bids over $1 against a $1 minted set. The sell side is
    the one that actually prints from here, so burying it was hiding the edge.

    Net is recomputed from gross at `row_fee_bps`, never read off the row.
    """
    rows = [s for s in samples if s.get("buy_both_gross") is not None]
    if not rows:
        return {"status": "no_samples",
                "hint": "run: python -m services.trend_engine.run --sample-updown"}
    buy_nets = [_net_from_gross(s, "buy") for s in rows]
    sell_nets = [_net_from_gross(s, "sell") for s in rows]
    gross_buy = [s for s in rows if (s.get("buy_both_gross") or 0) > 0]
    net_buy = [s for s, n in zip(rows, buy_nets) if n is not None and n > 0]
    gross_sell = [s for s in rows if (s.get("sell_both_gross") or 0) > 0]
    net_sell = [s for s, n in zip(rows, sell_nets) if n is not None and n > 0]
    costs = [1.0 - float(s["buy_both_gross"]) for s in rows]
    fees = [row_fee_bps(s) for s in rows]
    stale = sum(1 for s in rows if float(s.get("fee_bps") or 0) == GAMMA_ADVERTISED_FEE_BPS)
    best_buy = max((n for n in buy_nets if n is not None), default=-9.0)
    best_sell = max((n for n in sell_nets if n is not None), default=-9.0)
    if net_buy or net_sell:
        verdict = (f"{len(net_buy)}/{len(rows)} snapshots had a NET-profitable BUY pair, "
                   f"{len(net_sell)}/{len(rows)} a NET-profitable SELL pair "
                   f"(best ${max(best_buy, best_sell):.2f}/share)")
    elif gross_buy or gross_sell:
        verdict = (f"{len(gross_buy) + len(gross_sell)} snapshot(s) crossed gross but NONE "
                   f"survived the fee at {round(mean(fees))}bps")
    else:
        verdict = ("no crossed pair observed at our sampling latency — the book never "
                   "sat under $1 when we looked")
    return {
        "status": "ok",
        "samples": len(rows),
        "windows": len({s.get("window_start_ms") for s in rows}),
        "buy_both_gross_hits": len(gross_buy),
        "buy_both_net_hits": len(net_buy),
        "sell_both_gross_hits": len(gross_sell),
        "sell_both_net_hits": len(net_sell),
        "net_hits": len(net_buy) + len(net_sell),
        "mean_pair_cost": round(mean(costs), 4),
        "min_pair_cost": round(min(costs), 4),
        "mean_fee_bps": round(mean(fees), 1),
        "rows_repriced_off_advertised_fee": stale,
        "best_net_edge": round(best_buy, 4),
        "best_sell_net_edge": round(best_sell, 4),
        "verdict": verdict,
    }


# ── lane assembly ────────────────────────────────────────────────────────────


def read(windows: Optional[Sequence[Dict[str, Any]]] = None,
         samples: Optional[Sequence[Dict[str, Any]]] = None,
         resolver: Optional[Callable] = None,
         with_live: bool = True) -> Dict[str, Any]:
    """The microstructure block for the BTC 5m lane."""
    from services.trend_engine import updown_trends as ud

    if windows is None:
        windows = ud.enrich(ud.build_windows(ud.load_1m(30_240)))
    if samples is None:
        samples = load_samples()
    if resolver is None:
        from services.trend_engine.recorders import updown_resolver
        resolver = updown_resolver(windows)

    out: Dict[str, Any] = {
        "generated_at": int(time.time()),
        "tail_edge_60s": tail_edge(windows, minute=4),
        "tail_edge_120s": tail_edge(windows, minute=3),
        "arb": arb_stats(samples),
        "price_calibration_30s": price_calibration(samples, resolver, secs_left=30),
        "price_calibration_60s": price_calibration(samples, resolver, secs_left=60),
        "tail_strategy": tail_strategy_ev(samples, resolver, max_ask=0.05, secs_left=30),
        "samples": len(samples),
        "sample_offsets_s": list(SAMPLE_OFFSETS_S),
    }
    if with_live:
        # Warm BEFORE quoting: this function runs in the lane refresher, which
        # lives for seconds. Without the wait the snapshot is a REST quote next
        # to a health block from a socket that never connected.
        market = current_market()
        warm_feed(market)
        out["live_pair"] = pair_quote(market)
        try:
            from services.trend_engine.updown_ws import feed as ws_feed
            out["ws"] = ws_feed(start=False).health()
        except Exception as exc:
            out["ws"] = {"connected": False, "last_error": str(exc)[:120]}
    return out
