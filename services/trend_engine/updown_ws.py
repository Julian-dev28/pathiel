"""Live top-of-book over the CLOB websocket — push instead of poll.

Polling the REST book costs ~300ms per read and that is raw RTT to Polymarket's
edge, not something code can shave. The market channel pushes instead: measured
2026-08-02, one 5m pair produced **6,630 `price_change` events and 106 `book`
snapshots in 25 seconds**. Reading top-of-book out of memory is ~0ms and always
fresher than a poll.

Two facts make this simple:

  - `price_change` events carry `best_bid` / `best_ask` on every change entry,
    per `asset_id`. There is NO need to maintain a full order book and apply
    deltas — the top of book, which is all any of our maths uses, arrives
    pre-computed.
  - `book` events are full snapshots, so a reconnect re-syncs itself.

FEE CORRECTION (2026-08-02). Gamma's market payload advertises
`takerBaseFee: 1000` and `makerBaseFee: 1000`. Executed trades on the wire say
otherwise: **79 of 79 `last_trade_price` events carried `fee_rate_bps: "0"`**.
The Gamma field is not the rate charged on these markets, so an arb calculation
that trusts it understates the edge by up to 5c/share. `observed_fee_bps()`
reports what the tape actually charged, and `updown_edges.FEE_BPS_DEFAULT` is 0
because that is the measurement.

The feed is READ-ONLY: it subscribes to a public channel, holds no key, and
places nothing. `POLYMARKET_API_KEY` is only needed for the `user` channel,
which this does not touch.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# Beyond this the cached top-of-book is not trustworthy and callers fall back
# to HTTP. Generous next to a market that prints thousands of events a minute.
STALE_AFTER_S = 5.0
RECONNECT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)


def _ssl_opt() -> Dict[str, Any]:
    """CA bundle for the handshake.

    The python.org macOS build ships no system trust store, so a plain
    `create_connection` dies with CERTIFICATE_VERIFY_FAILED while `requests`
    (which vendors certifi) works — a difference that looks like the endpoint
    being down.
    """
    try:
        import certifi
        return {"ca_certs": certifi.where()}
    except Exception:
        return {}


class BookFeed:
    """Live top-of-book for a set of CLOB tokens, maintained on a daemon thread.

    Not a general order-book engine: it keeps best bid / best ask / last trade
    per asset, which is everything `pair_quote` and the sampler read. Call
    `subscribe()` when the window rolls; the thread resubscribes itself.
    """

    def __init__(self, url: str = WS_URL, stale_after_s: float = STALE_AFTER_S,
                 connector: Optional[Callable[..., Any]] = None) -> None:
        self.url = url
        self.stale_after_s = stale_after_s
        self._connector = connector
        self._lock = threading.Lock()
        self._top: Dict[str, Dict[str, Any]] = {}
        self._assets: List[str] = []
        self._ws: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._resubscribe = threading.Event()
        self.connected = False
        self.events = 0
        self.books = 0
        self.price_changes = 0
        self.trades = 0
        self.reconnects = 0
        self.timeouts = 0
        self.last_msg_ts: float = 0.0
        self.last_error: str = ""
        self._fee_bps_seen: Dict[str, int] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> "BookFeed":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="clob-bookfeed",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def subscribe(self, asset_ids: List[str]) -> None:
        """Point the feed at a new set of tokens (a new 5m window)."""
        with self._lock:
            if list(asset_ids) == self._assets:
                return
            self._assets = list(asset_ids)
            # drop the old window's book rather than let it read as live
            self._top = {a: v for a, v in self._top.items() if a in self._assets}
        self._resubscribe.set()
        try:
            if self._ws is not None:
                self._send_subscribe()
        except Exception:
            pass                                # the run loop will reconnect

    def wait_ready(self, timeout_s: float = 10.0) -> bool:
        """Block until at least one asset has a quote (or the timeout)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self._lock:
                if any(v.get("bid") is not None or v.get("ask") is not None
                       for v in self._top.values()):
                    return True
            time.sleep(0.05)
        return False

    def wait_pair(self, asset_ids: Sequence[str], timeout_s: float = 3.0) -> bool:
        """Block until BOTH legs are two-sided (or the timeout).

        `wait_ready` is too weak for anything that prices a pair: one leg
        holding a lone bid satisfies it, while `pair_quote` needs bid AND ask on
        both legs before it will trust the socket. A caller that waits on the
        weaker condition believes it warmed the feed and then quotes over REST.
        """
        deadline = time.time() + timeout_s
        while True:
            rows = self.pair(list(asset_ids))
            if rows and all(r.get("bid") is not None and r.get("ask") is not None
                            for r in rows):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    # ── reads ────────────────────────────────────────────────────────────────

    def top(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Best bid/ask for one token, or None when missing or stale."""
        with self._lock:
            row = self._top.get(str(asset_id))
            if not row:
                return None
            age = time.time() - float(row.get("ts") or 0)
            if age > self.stale_after_s:
                return None
            return {**row, "age_s": round(age, 3)}

    def pair(self, asset_ids: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Both sides, or None if either is missing/stale — never half a pair."""
        rows = [self.top(a) for a in asset_ids]
        return rows if all(r is not None for r in rows) else None   # type: ignore

    def observed_fee_bps(self) -> Optional[float]:
        """Fee actually charged on executed trades, from `last_trade_price`.

        This is the number to trust. Gamma advertises 1000bps on these markets
        and the tape has charged 0 on every trade observed.
        """
        with self._lock:
            if not self._fee_bps_seen:
                return None
            # the modal observed rate; a stray outlier must not move it
            return float(max(self._fee_bps_seen.items(), key=lambda kv: kv[1])[0])

    def health(self) -> Dict[str, Any]:
        """State of THIS process's socket.

        `pid` and `running` are here because the failure that actually happened
        was reading a health block written by another, short-lived process: the
        lane refresher starts a feed, exits seconds later, and its snapshot
        renders forever as a dead socket. `running=False` means the thread was
        never started (or has died) — a different fault from `connected=False`,
        which means the handshake is failing and `last_error` says why.
        """
        with self._lock:
            quotes = len([v for v in self._top.values() if v.get("ask") is not None])
        age = (time.time() - self.last_msg_ts) if self.last_msg_ts else None
        return {
            "pid": os.getpid(),
            "running": bool(self._thread is not None and self._thread.is_alive()),
            "measured_at": int(time.time()),
            "connected": self.connected,
            "assets": list(self._assets),
            "quotes": quotes,
            "events": self.events,
            "books": self.books,
            "price_changes": self.price_changes,
            "trades": self.trades,
            "reconnects": self.reconnects,
            "quiet_timeouts": self.timeouts,
            "last_msg_age_s": round(age, 2) if age is not None else None,
            "stale": bool(age is None or age > self.stale_after_s),
            "observed_fee_bps": self.observed_fee_bps(),
            "last_error": self.last_error,
        }

    # ── the thread ───────────────────────────────────────────────────────────

    def _connect(self) -> Any:
        if self._connector is not None:
            return self._connector(self.url)
        import websocket
        return websocket.create_connection(self.url, timeout=20,
                                           sslopt=_ssl_opt())

    def _send_subscribe(self) -> None:
        with self._lock:
            assets = list(self._assets)
        if not assets or self._ws is None:
            return
        self._ws.send(json.dumps({"assets_ids": assets, "type": "market"}))

    @staticmethod
    def _is_timeout(exc: BaseException) -> bool:
        """A quiet socket, not a broken one.

        `recv()` raises after its 20s ceiling whenever nothing was pushed —
        which is NORMAL here: a 5m market goes silent the moment it resolves,
        and stays silent until someone subscribes the next window's tokens.
        Treating that as a dead connection tore the socket down and rebuilt it
        on a loop: 257 reconnects in one server session, and every teardown is
        a window where `pair_quote` silently falls back to the 300ms REST path.
        """
        return type(exc).__name__ in (
            "WebSocketTimeoutException", "timeout", "TimeoutError")

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._ws = self._connect()
                self.connected = True
                attempt = 0
                self._send_subscribe()
                while not self._stop.is_set():
                    try:
                        raw = self._ws.recv()
                    except Exception as exc:
                        if not self._is_timeout(exc):
                            raise
                        # hold the connection open: ping so the far end keeps
                        # it, and flush a subscribe that arrived while quiet
                        self.timeouts += 1
                        self._ws.ping()
                        if self._resubscribe.is_set():
                            self._resubscribe.clear()
                            self._send_subscribe()
                        continue
                    if not raw:
                        continue
                    self._ingest(raw)
                    if self._resubscribe.is_set():
                        self._resubscribe.clear()
                        self._send_subscribe()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            finally:
                self.connected = False
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None
            if self._stop.is_set():
                break
            self.reconnects += 1
            wait = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
            attempt += 1
            self._stop.wait(wait)

    def _ingest(self, raw: str) -> None:
        try:
            msgs = json.loads(raw)
        except Exception:
            return
        if isinstance(msgs, dict):
            msgs = [msgs]
        now = time.time()
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            self.events += 1
            self.last_msg_ts = now
            ev = msg.get("event_type")
            if ev == "book":
                self.books += 1
                self._apply_book(msg, now)
            elif ev == "price_change":
                self.price_changes += 1
                self._apply_price_change(msg, now)
            elif ev == "last_trade_price":
                self.trades += 1
                self._apply_trade(msg, now)

    def _apply_book(self, msg: Dict[str, Any], now: float) -> None:
        """Full snapshot — the reconnect resync path."""
        asset = str(msg.get("asset_id") or "")
        if not asset:
            return
        bids = msg.get("bids") or []
        asks = msg.get("asks") or []

        def best(levels: List[Dict[str, Any]], side: str):
            try:
                rows = [(float(x["price"]), float(x.get("size") or 0)) for x in levels]
            except Exception:
                return None, None
            if not rows:
                return None, None
            px, sz = (max(rows, key=lambda r: r[0]) if side == "bid"
                      else min(rows, key=lambda r: r[0]))
            return px, sz

        bid, bid_sz = best(bids, "bid")
        ask, ask_sz = best(asks, "ask")
        with self._lock:
            row = self._top.setdefault(asset, {})
            row.update({"bid": bid, "ask": ask, "bid_size": bid_sz,
                        "ask_size": ask_sz, "ts": now, "src": "book"})

    def _apply_price_change(self, msg: Dict[str, Any], now: float) -> None:
        """Incremental — but each entry already carries best_bid / best_ask, so
        the top of book needs no delta reconstruction."""
        for ch in (msg.get("changes") or []):
            if not isinstance(ch, dict):
                continue
            asset = str(ch.get("asset_id") or msg.get("asset_id") or "")
            if not asset:
                continue
            try:
                bid = float(ch["best_bid"]) if ch.get("best_bid") is not None else None
                ask = float(ch["best_ask"]) if ch.get("best_ask") is not None else None
            except Exception:
                continue
            with self._lock:
                row = self._top.setdefault(asset, {})
                if bid is not None:
                    row["bid"] = bid
                if ask is not None:
                    row["ask"] = ask
                # sizes are not carried on a top-of-book change; blank them so a
                # stale size can never be read as current depth
                row["bid_size"] = None
                row["ask_size"] = None
                row["ts"] = now
                row["src"] = "price_change"

    def _apply_trade(self, msg: Dict[str, Any], now: float) -> None:
        asset = str(msg.get("asset_id") or "")
        fee = msg.get("fee_rate_bps")
        if fee is not None:
            key = str(fee)
            with self._lock:
                self._fee_bps_seen[key] = self._fee_bps_seen.get(key, 0) + 1
        if not asset:
            return
        try:
            px = float(msg.get("price"))
        except Exception:
            return
        with self._lock:
            row = self._top.setdefault(asset, {})
            row["last_trade"] = px
            row["last_trade_ts"] = now


# ── process-wide singleton (one socket, not one per caller) ──────────────────

_FEED: Optional[BookFeed] = None
_FEED_LOCK = threading.Lock()


def feed(start: bool = True) -> BookFeed:
    """The shared feed. One socket per process — subscribing twice to the same
    tokens on two sockets doubles the firehose for no benefit."""
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = BookFeed()
            if start:
                _FEED.start()
        elif start:
            _FEED.start()
        return _FEED


def stop_feed() -> None:
    global _FEED
    with _FEED_LOCK:
        if _FEED is not None:
            _FEED.stop()
            _FEED = None
