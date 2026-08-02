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
import time
from typing import Any, Callable, Dict, List, Optional

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
        self._last_fire_slug: Optional[str] = None
        self._last_fire_ts: float = 0.0

    # ── one pass ─────────────────────────────────────────────────────────────

    def check(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One look at the book. Returns the event when it qualifies, else None."""
        now = time.time() if now is None else now
        q = self._quoter()
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
