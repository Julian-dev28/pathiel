#!/usr/bin/env python3
"""Live eval: does the site answer fast while a dashboard tab is open?

    .venv/bin/python scripts/site_latency_eval.py                 # against http://localhost:8000
    .venv/bin/python scripts/site_latency_eval.py --base http://127.0.0.1:8001 --json /tmp/x.json
    .venv/bin/python scripts/site_latency_eval.py --no-browser    # HTTP only

The bug it would have caught (2026-10-19): every dashboard poll did its own
fresh Hyperliquid account read (12 requests, 24 weight on the server's 2
weight/s budget). Reads took 6-12 s, the dashboard's 6 s and 10 s polls
stacked up and held the browser's six connections to the site, and clicking
Desks stalled. On the old server this eval measured the dashboard summary at
6.6 s and positions at 12.0 s.

And its sibling (2026-10-19, later): pathiel/server.py ran every /api/hl/* read,
the agent scan, research and the order routes on the server's one event loop,
so while any of them waited on Hyperliquid, no page on the site could answer.

What it does, read-only (GETs, plus the size preview POST the desk page itself
makes): one thread plays an open dashboard tab (summary every 6 s, positions
every 10 s, each waiting for its answer); meanwhile it walks the pages a user
clicks (Desks, a desk, its brief, the trade banner, "Your size") and, with
Playwright installed, clicks Desks and a desk in a real browser with the
dashboard open in another tab. It reads /api/health/system's hl_budget before
and after to report what the run cost the server's Hyperliquid budget. Last, it
starts 8 order-book reads at once (/api/hl/orderbook, one venue round trip
each, 16 weight) and times /api/health while they are in flight.

Thresholds (exit 1 when any fails):
  - every page/API request after warm-up: p95 < 1.0 s, max < 3.0 s
  - dashboard polls: p95 < 1.0 s
  - browser: Desks grid and desk page visible < 1.5 s after the click
  - /api/health while 8 venue reads are in flight: max < 0.25 s (a server
    that blocks its loop makes it wait for all 8, about 1.5 s)
  - foreground requests the server refused for lack of budget: 0
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DESKS = ["btc", "oil", "ai", "top5", "metals", "indices"]
SIZE_BODY = {"coin": "xyz:BRENTOIL", "entry_px": 105.5, "stop_px": 101.35,
             "leverage": 5, "targets": [110.31, 112.19]}
P95_MAX_S, MAX_S, CLICK_MAX_S = 1.0, 3.0, 1.5
LOOP_MAX_S = 0.25
BOOK_COINS = ("BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "BNB", "AVAX")


def _req(base: str, path: str, body: Optional[dict] = None, timeout: float = 60.0) -> Tuple[int, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:  # noqa: BLE001 - a timeout or refused connection is a result, not a crash
        code = 0
    return code, time.monotonic() - t0


def _budget(base: str) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(base + "/api/health/system", timeout=60) as r:
            return json.loads(r.read()).get("hl_budget") or {}
    except urllib.error.HTTPError as e:          # 503 = something unhealthy; the body still has it
        try:
            return json.loads(e.read()).get("hl_budget") or {}
        except Exception:  # noqa: BLE001
            return {}
    except Exception:  # noqa: BLE001
        return {}


def _refused_foreground(budget: Dict[str, Any]) -> int:
    return sum((budget.get("refused_requests") or {}).get("foreground", {}).values())


def _p95(xs: List[float]) -> float:
    """Nearest-rank 95th percentile: the ceil(0.95 n)-th smallest."""
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[math.ceil(0.95 * len(xs)) - 1]


class DashboardTab(threading.Thread):
    """An open dashboard tab, polling the way pathiel.js pollEvery does."""

    def __init__(self, base: str):
        super().__init__(daemon=True)
        self.base, self.stop, self.samples = base, threading.Event(), []

    def run(self) -> None:
        due = {"/api/dashboard/summary": 0.0, "/api/dashboard/positions": 0.0}
        every = {"/api/dashboard/summary": 6.0, "/api/dashboard/positions": 10.0}
        while not self.stop.is_set():
            now = time.monotonic()
            for path, at in due.items():
                if now >= at:
                    code, dt = _req(self.base, path)
                    self.samples.append((path, code, dt))
                    due[path] = time.monotonic() + every[path]
            self.stop.wait(0.2)


def _loop_check(base: str) -> Tuple[float, List[Tuple[int, float]]]:
    """Time /api/health while 8 order-book reads are in flight. Each read is
    one Hyperliquid round trip; on a server that makes it on its event loop,
    health answers only after all of them."""
    books: List[Tuple[int, float]] = []
    lock = threading.Lock()
    # Untimed: the client's first request builds urllib's opener and reads the
    # system proxy settings (100-400 ms on macOS), which is not the server's.
    _req(base, "/api/health", timeout=10)

    def read(coin: str) -> None:
        r = _req(base, f"/api/hl/orderbook?coin={coin}")
        with lock:
            books.append(r)

    threads = [threading.Thread(target=read, args=(c,), daemon=True) for c in BOOK_COINS]
    for t in threads:
        t.start()
    time.sleep(0.05)                       # the reads reach the server first
    health = [_req(base, "/api/health", timeout=30)[1] for _ in range(3)]
    for t in threads:
        t.join(60)
    return max(health), books


def _browser_clicks(base: str, rounds: int) -> List[Tuple[str, float]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        dash = ctx.new_page()
        dash.goto(base + "/")
        page = ctx.new_page()
        for i in range(rounds):
            page.goto(base + "/news", wait_until="domcontentloaded")
            t0 = time.monotonic()
            page.click('a.nav-link[href="/desks"]')
            page.wait_for_selector("#desks-grid:not([hidden]) > a", timeout=60000)
            out.append(("click Desks", time.monotonic() - t0))
            desk = DESKS[i % len(DESKS)]
            t0 = time.monotonic()
            page.click(f'#desks-grid a[href="/desks/{desk}"]')
            page.wait_for_selector("#desk-content:not([hidden])", timeout=60000)
            out.append((f"click {desk}", time.monotonic() - t0))
        browser.close()
    return out


def run(base: str, rounds: int = 6, browser: bool = True, json_path: Optional[str] = None) -> int:
    code, _ = _req(base, "/api/health", timeout=10)
    if code != 200:
        print(f"server not answering at {base} (/api/health -> {code})")
        return 2
    before = _budget(base)
    warm = _req(base, "/api/accounts/active/summary")      # the one cold read is allowed to be slow
    tab = DashboardTab(base)
    tab.start()
    time.sleep(1.0)

    samples: List[Tuple[str, int, float]] = []
    for i in range(rounds):
        desk = DESKS[i % len(DESKS)]
        for path, body in (("/desks", None), ("/api/desks", None), (f"/desks/{desk}", None),
                           (f"/api/desks/{desk}", None), ("/api/accounts/active/summary", None),
                           ("/api/risk/size", SIZE_BODY)):
            c, dt = _req(base, path, body)
            samples.append((path if body is None else "POST " + path, c, dt))
        time.sleep(2.0)
    clicks = _browser_clicks(base, 4) if browser else []
    loop_wait, books = _loop_check(base)
    tab.stop.set()
    tab.join(30)
    after = _budget(base)

    page_lat = [dt for _, c, dt in samples]
    dash_lat = [dt for _, c, dt in tab.samples]
    refused = _refused_foreground(after) - _refused_foreground(before)
    bad_codes = sorted({f"{p} -> {c}" for p, c, _ in samples + tab.samples if c >= 500 or c == 0}
                       | {f"/api/hl/orderbook -> {c}" for c, _ in books if c != 200})
    checks = [
        ("page/API p95 < 1.0 s", _p95(page_lat) < P95_MAX_S, f"{_p95(page_lat):.2f} s"),
        ("page/API max < 3.0 s", max(page_lat or [0]) < MAX_S, f"{max(page_lat or [0]):.2f} s"),
        ("dashboard polls p95 < 1.0 s", _p95(dash_lat) < P95_MAX_S,
         f"{_p95(dash_lat):.2f} s over {len(dash_lat)} polls"),
        ("no 5xx / dropped requests", not bad_codes, ", ".join(bad_codes) or "none"),
        ("foreground requests refused for budget: 0", refused <= 0, str(refused)),
        ("health during 8 venue reads < 0.25 s", loop_wait < LOOP_MAX_S,
         f"{loop_wait:.3f} s (reads took up to {max((dt for _, dt in books), default=0):.2f} s)"),
    ]
    if clicks:
        worst = max(dt for _, dt in clicks)
        checks.append(("browser clicks < 1.5 s", worst < CLICK_MAX_S, f"worst {worst:.2f} s"))

    print(f"warm-up account read: {warm[1]:.2f} s (HTTP {warm[0]})")
    by_path: Dict[str, List[float]] = {}
    for p, _, dt in samples + tab.samples:
        by_path.setdefault(p, []).append(dt)
    print(f"{'request':38} {'n':>3} {'median':>8} {'max':>8}")
    for p, xs in sorted(by_path.items()):
        print(f"{p:38} {len(xs):>3} {statistics.median(xs):>7.3f}s {max(xs):>7.3f}s")
    for name, dt in clicks:
        print(f"{name:38} {'':>3} {dt:>7.3f}s")
    print()
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name:44} {detail}")
    granted = {prio: sum(v.values()) for prio, v in (after.get("granted_weight") or {}).items()}
    granted_before = {prio: sum(v.values()) for prio, v in (before.get("granted_weight") or {}).items()}
    spent = {k: granted.get(k, 0) - granted_before.get(k, 0) for k in set(granted) | set(granted_before)}
    print(f"HL weight the server spent during the run: {spent or 'n/a (old server: no budget stats)'}")

    failed = [n for n, ok, _ in checks if not ok]
    if json_path:
        with open(json_path, "w") as f:
            json.dump({"base": base, "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                       "requests": [{"path": p, "code": c, "s": round(dt, 4)} for p, c, dt in samples],
                       "dashboard": [{"path": p, "code": c, "s": round(dt, 4)} for p, c, dt in tab.samples],
                       "clicks": [{"what": n, "s": round(dt, 4)} for n, dt in clicks],
                       "health_during_venue_reads_s": round(loop_wait, 4),
                       "hl_weight_spent": spent}, f, indent=2)
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    return run(a.base.rstrip("/"), rounds=a.rounds, browser=not a.no_browser, json_path=a.json)


if __name__ == "__main__":
    sys.exit(main())
