"""Both-sides arb watcher for the 5m up/down pair.

Fires when the two complementary asks sum to less than $1 net of fees: buy both,
redeem the winner for $1, keep the difference. The trade is real arithmetic —
what is unproven is whether the opportunity is ever reachable from here.

**SHADOW BY DEFAULT.** Live execution is off unless `mode="live"` is passed AND
an executor is injected. There is deliberately no order-placement code in this
module: this repo has no Polymarket execution path, and adding one silently
behind a watcher would be the wrong way to get one. What this does instead is
record every qualifying print — price, size, and how long it survived — so the
question "could we have taken it?" gets an evidence answer before any capital
is committed.

Three guards, all on by default:

  min_edge      net edge must clear this before anything fires (default 1 tick)
  max_notional  per-fire cap, so a fat-fingered config cannot scale
  cooldown_s    one fire per window, so a persistent quote is one event not 500

Reads the websocket feed, so detection is ~0ms after the book changes rather
than a poll behind it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.trend_engine import env
from services.trend_engine.updown_edges import (
    current_market, pair_quote, slug_for, tokens_for,
)

LEDGER = os.path.join(env.state_dir(), "trend_engine", "arb_events.jsonl")
# One tick is the smallest edge that can exist on a 1c grid; anything smaller is
# a rounding artifact, not an opportunity.
MIN_EDGE = 0.01
MAX_NOTIONAL_USD = 50.0
COOLDOWN_S = 300.0          # one fire per 5m window


class ArbWatcher:
    """Watches the live pair and records (or, if armed, executes) sub-$1 prints."""

    def __init__(self, mode: str = "shadow", min_edge: float = MIN_EDGE,
                 max_notional_usd: float = MAX_NOTIONAL_USD,
                 cooldown_s: float = COOLDOWN_S,
                 ledger_path: str = LEDGER,
                 executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
                 quoter: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        if mode not in ("shadow", "live"):
            raise ValueError("mode must be 'shadow' or 'live'")
        if mode == "live" and executor is None:
            # Refusing beats pretending: there is no built-in Polymarket order
            # path, so "live" without an injected executor would silently be
            # shadow while reporting itself as live.
            raise ValueError(
                "live mode requires an injected executor — this module places no "
                "orders itself and will not pretend to")
        self.mode = mode
        self.min_edge = float(min_edge)
        self.max_notional_usd = float(max_notional_usd)
        self.cooldown_s = float(cooldown_s)
        self.ledger_path = ledger_path
        self._executor = executor
        self._quoter = quoter or (lambda: pair_quote())
        self.checks = 0
        self.hits = 0
        self.fires = 0
        self.best_edge: Optional[float] = None
        # The quote `check()` actually decided on. Callers that need to show it
        # (the preflight endpoint) must not re-quote: a second read is a second
        # book, so the number on the button would not be the number that armed
        # it — and on the REST fallback it is another 300ms round trip.
        self.last_quote: Dict[str, Any] = {}
        self._last_fire_slug: Optional[str] = None
        self._last_fire_ts: float = 0.0

    # ── one pass ─────────────────────────────────────────────────────────────

    def check(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One look at the book. Returns the event when it qualifies, else None."""
        now = time.time() if now is None else now
        q = self._quoter()
        self.last_quote = q if isinstance(q, dict) else {}
        self.checks += 1
        if q.get("status") != "ok":
            return None

        buy, sell = q.get("buy_both") or {}, q.get("sell_both") or {}
        best_side, best = None, None
        for name, blk in (("buy_both", buy), ("sell_both", sell)):
            edge = blk.get("net_edge")
            if edge is None:
                continue
            if best is None or edge > best.get("net_edge", -9):
                best_side, best = name, blk
        if best is None:
            return None

        edge = float(best.get("net_edge") or -9)
        if self.best_edge is None or edge > self.best_edge:
            self.best_edge = edge
        if edge < self.min_edge:
            return None
        self.hits += 1

        size = float(best.get("size") or 0.0)
        notional = min(size, self.max_notional_usd)
        event = {
            "ts": int(now),
            "slug": q.get("slug"),
            "side": best_side,
            "net_edge": round(edge, 4),
            "gross_edge": best.get("gross_edge"),
            "fee_bps": q.get("fee_bps"),
            "book_size": size,
            "notional_usd": round(notional, 2),
            "expected_profit_usd": round(edge * notional, 4),
            "up": q.get("up"), "down": q.get("down"),
            "source": q.get("source"),
            "mode": self.mode,
        }

        if self._cooling(q.get("slug"), now):
            event["action"] = "skipped_cooldown"
            self._record(event)
            return event

        if self.mode == "live":
            try:
                result = self._executor(event) or {}
                event["action"] = "executed"
                event["result"] = result
            except Exception as exc:
                event["action"] = "execute_failed"
                event["error"] = str(exc)[:200]
        else:
            event["action"] = "shadow"

        self.fires += 1
        self._last_fire_slug = q.get("slug")
        self._last_fire_ts = now
        self._record(event)
        return event

    def _cooling(self, slug: Optional[str], now: float) -> bool:
        if self._last_fire_slug and slug == self._last_fire_slug:
            return True
        return (now - self._last_fire_ts) < self.cooldown_s

    def _record(self, event: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
            with open(self.ledger_path, "a") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        except Exception:
            pass

    # ── loop ─────────────────────────────────────────────────────────────────

    def run(self, poll_s: float = 0.25, max_checks: int = 0,
            sleeper: Callable[[float], None] = time.sleep,
            printer: Callable[[str], None] = print) -> Dict[str, Any]:
        """Poll the (websocket-backed) quote until stopped.

        `poll_s` is a read of an in-memory quote, not an HTTP call, so it can be
        aggressive without hitting anyone's rate limit.
        """
        printer(f"[arb-watch] {self.mode.upper()} — min edge {self.min_edge:.2f}, "
                f"cap ${self.max_notional_usd:.0f}, ledger {self.ledger_path}")
        if self.mode == "shadow":
            printer("[arb-watch] shadow: recording only, no orders placed")
        started = time.time()
        subscribed: Optional[str] = None
        try:
            while not max_checks or self.checks < max_checks:
                slug = slug_for()
                if slug != subscribed:
                    # keep the socket pointed at the live window
                    try:
                        toks = tokens_for(slug)
                        if toks:
                            from services.trend_engine.updown_ws import feed
                            feed().subscribe(toks)
                            subscribed = slug
                    except Exception:
                        pass
                ev = self.check()
                if ev and ev.get("action") not in (None, "skipped_cooldown"):
                    printer(f"[arb-watch] {time.strftime('%H:%M:%S')} {ev['side']} "
                            f"net {ev['net_edge']:+.3f} on {ev['slug']} "
                            f"size {ev['book_size']} -> {ev['action']} "
                            f"(${ev['expected_profit_usd']})")
                sleeper(poll_s)
        except KeyboardInterrupt:
            printer("[arb-watch] stopped")
        return self.summary(started)

    def summary(self, started: Optional[float] = None) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "checks": self.checks,
            "hits": self.hits,
            "fires": self.fires,
            "best_edge": self.best_edge,
            "min_edge": self.min_edge,
            "elapsed_s": round(time.time() - started, 1) if started else None,
            "ledger": self.ledger_path,
            "verdict": (f"{self.fires} qualifying print(s) in {self.checks} checks"
                        if self.fires else
                        f"no sub-$1 pair in {self.checks} checks — best seen was "
                        f"{self.best_edge if self.best_edge is not None else 'n/a'} "
                        f"vs a {self.min_edge} floor"),
        }


# ── execution readiness ──────────────────────────────────────────────────────
# Placing a CLOB order needs BOTH auth layers. L2 is the API-key triplet on the
# request headers; L1 is an EIP-712 signature over the order struct, which needs
# the Polygon key that owns the funder address. An API key alone cannot sign, so
# a button wired to a half-credentialed client would fail at the exchange rather
# than here — which is the wrong place to find out.
REQUIRED_CREDS: Tuple[Tuple[str, str], ...] = (
    ("POLYMARKET_ADDRESS", "funder address that holds the USDC"),
    ("POLYMARKET_API_KEY", "L2 header: API key"),
    ("POLYMARKET_SECRET", "L2 header: HMAC secret"),
    ("POLYMARKET_PASSPHRASE", "L2 header: passphrase"),
    ("POLYMARKET_PRIVATE_KEY", "L1: signs the EIP-712 order struct"),
)


def execution_readiness(getenv: Callable[[str], Optional[str]] = os.environ.get,
                        ) -> Dict[str, Any]:
    """What is missing before a fire can be anything but shadow.

    Reports names only, never values — this dict is rendered on a web page.
    """
    missing = [{"key": k, "why": why} for k, why in REQUIRED_CREDS if not getenv(k)]
    return {
        "ready": not missing,
        "present": [k for k, _ in REQUIRED_CREDS if getenv(k)],
        "missing": missing,
        "blocker": None if not missing else
        (f"{len(missing)} credential(s) missing — a fire records the ticket and "
         f"stops. No order path exists in this repo yet."),
    }


def order_tickets(event: Optional[Dict[str, Any]], cap_usd: float = MAX_NOTIONAL_USD,
                  ) -> List[Dict[str, Any]]:
    """The two legs a fire WOULD send, priced and sized off the same quote.

    Marketable limits at the touch, not market orders: the edge here is one
    tick wide, so a leg that slips a tick is not a smaller profit, it is a
    loss. Both legs must fill or the position is a naked directional bet on
    BTC, which is not the trade.

    `None` (no crossing) is the common case, not an error: an empty ticket
    list is what an unarmed button renders.
    """
    if not event:
        return []
    side = event.get("side")
    up, dn = event.get("up") or {}, event.get("down") or {}
    if side == "buy_both":
        legs, action, field = ((up, "UP"), (dn, "DOWN")), "BUY", "ask"
    elif side == "sell_both":
        legs, action, field = ((up, "UP"), (dn, "DOWN")), "SELL", "bid"
    else:
        return []
    prices = [leg.get(field) for leg, _ in legs]
    if any(p is None for p in prices):
        return []
    # Size on the thinner leg: an unmatched share is naked BTC exposure.
    book_size = float(event.get("book_size") or 0.0)
    shares = min(book_size, cap_usd / max(sum(prices), 0.01))
    return [{
        "outcome": name,
        "action": action,
        "price": float(leg[field]),
        "shares": round(shares, 2),
        "notional_usd": round(shares * float(leg[field]), 2),
        "order_type": "limit",
        "time_in_force": "FOK",   # both legs or neither
    } for leg, name in legs]


def ensure_subscribed(slug: Optional[str] = None,
                      wait_s: float = 0.0) -> Optional[str]:
    """Point the CLOB websocket at the live window's two tokens.

    Idempotent and best-effort. Without this, a caller that only ever asks for
    a quote gets the REST fallback forever (~300ms) — and a one-tick edge that
    takes 300ms to see is one another desk already took. Returns the slug now
    subscribed, or None if the socket could not be reached.

    `wait_s > 0` blocks until both legs are two-sided, so the FIRST call after a
    window rolls is a socket read instead of a poll. The wait only happens on an
    actual (re)subscribe, and the slug check is backed by the feed's own asset
    list — a `stop_feed()` between calls would otherwise leave this believing it
    was still subscribed to a socket that no longer exists.
    """
    global _SUBSCRIBED
    try:
        from services.trend_engine.updown_ws import feed
        slug = slug or slug_for()
        f = feed()
        if slug and slug == _SUBSCRIBED and f.health().get("assets"):
            return slug
        toks = tokens_for(slug)
        if not toks:
            return None
        f.subscribe(toks)
        _SUBSCRIBED = slug
        if wait_s > 0:
            f.wait_pair(toks, wait_s)
        return slug
    except Exception:
        return None


_ROLL_LOCK = threading.Lock()
_ROLL_THREAD: Optional[threading.Thread] = None
_LAST_USE: float = 0.0
ROLL_EVERY_S = 15.0
ROLL_IDLE_STOP_S = 300.0


def start_autoroll(every_s: float = ROLL_EVERY_S, idle_stop_s: float = ROLL_IDLE_STOP_S,
                   sleeper: Callable[[float], None] = time.sleep) -> None:
    """Keep the socket pointed at the LIVE window while the tab is in use.

    Nothing re-subscribes on its own: the window rolls every 300s, and a feed
    that only re-points when a caller asks is subscribed to a resolved market
    the entire time nobody is looking. Measured 2026-08-03: after one idle
    window the next preflight was served `source: rest` — a 300ms poll on the
    one click that wanted 0ms.

    Idle-stops itself `idle_stop_s` after the last preflight so a closed tab
    does not leave this process parsing ~390 events/second forever.
    """
    global _ROLL_THREAD
    with _ROLL_LOCK:
        if _ROLL_THREAD is not None and _ROLL_THREAD.is_alive():
            return

        def _loop() -> None:
            global _ROLL_THREAD
            try:
                while time.time() - _LAST_USE < idle_stop_s:
                    ensure_subscribed()
                    sleeper(every_s)
            finally:
                from services.trend_engine.updown_ws import stop_feed
                global _SUBSCRIBED
                stop_feed()                 # release the socket with the thread
                _SUBSCRIBED = None
                with _ROLL_LOCK:
                    _ROLL_THREAD = None

        _ROLL_THREAD = threading.Thread(target=_loop, name="arb-autoroll", daemon=True)
        _ROLL_THREAD.start()


def ws_health() -> Dict[str, Any]:
    """This process's socket state, for the dashboard.

    Deliberately read HERE rather than out of the cached lane payload: that one
    is written by the refresher, which exits seconds after starting a socket, so
    it always reads as down. The button's status line has to come from the
    process that owns the live feed.
    """
    try:
        from services.trend_engine.updown_ws import feed
        return feed(start=False).health()
    except Exception as exc:
        return {"connected": False, "running": False,
                "last_error": str(exc)[:120]}


_SUBSCRIBED: Optional[str] = None
# First call after a window rolls pays this at most, and only once per window.
SUBSCRIBE_WAIT_S = 2.0


def preflight(quoter: Optional[Callable[[], Dict[str, Any]]] = None,
              min_edge: float = MIN_EDGE,
              cap_usd: float = MAX_NOTIONAL_USD,
              getenv: Callable[[str], Optional[str]] = os.environ.get,
              subscribe: bool = True,
              ) -> Dict[str, Any]:
    """One-shot: is there an arb RIGHT NOW, and could we take it?

    Backs the dashboard button. Always read-only — it prices the trade and
    reports what would be sent, and it places nothing regardless of creds.

    Subscribes the websocket on the way in and waits (bounded) for both legs, so
    even the first call after a window rolls is served from memory at 0ms rather
    than a 300ms poll. `quote.source` still says which one answered — a `rest`
    source on a live book means the button is a poll behind the market — and
    `ws` carries THIS process's socket health, which is the only honest place to
    read it from.
    """
    if subscribe and quoter is None:
        global _LAST_USE
        _LAST_USE = time.time()
        ensure_subscribed(wait_s=SUBSCRIBE_WAIT_S)
        start_autoroll()          # keep it warm across the next window roll
    w = ArbWatcher(mode="shadow", min_edge=min_edge, max_notional_usd=cap_usd,
                   cooldown_s=0.0, quoter=quoter)
    event = w.check()
    ready = execution_readiness(getenv)
    q = w.last_quote                      # the quote that armed it, not a new one
    armed = bool(event) and event.get("action") != "skipped_cooldown"
    return {
        "status": "ok",
        "armed": armed,
        "event": event,
        "tickets": order_tickets(event, cap_usd) if armed else [],
        "readiness": ready,
        "ws": ws_health(),
        "quote": {k: q.get(k) for k in
                  ("slug", "source", "fee_bps", "best_net_edge", "arb",
                   "ticks_to_gross_arb", "buy_both", "sell_both", "up", "down")},
        "would_execute": bool(armed and ready["ready"]),
        "verdict": (
            "no crossing right now — nothing to fire" if not armed else
            (f"ARB LIVE: {event['side']} net {event['net_edge']:+.3f}/share, "
             f"${event['expected_profit_usd']} on ${event['notional_usd']} — "
             + ("ready to send" if ready["ready"] else ready["blocker"]))),
    }


def load_events(path: str = LEDGER, cap: int = 50_000) -> List[Dict[str, Any]]:
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
