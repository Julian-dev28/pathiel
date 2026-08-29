"""W-N1a — GDELT historical fetch for the news-catalyst replay (Lane N).

QUERY AMENDMENT (forced by the API, applied before any result was viewed):
the pre-registered query was '"<SYM>" crypto sourcelang:eng', but GDELT
structurally rejects quoted phrases shorter than 5 chars ("The specified
phrase is too short") and most perp symbols are 2-4 chars. Amended rule,
uniform across events AND controls: quote the symbol iff len(sym) >= 5,
else use it unquoted (AND-of-terms). Ambiguous short symbols (IP, MON, LIT)
lose sensitivity, not validity — each coin-day is scored against its own
baseline under the same query.

Pulls, for every event and control coin-day in W-N_events.json
(pre-registered in W-N0_events.py):

  1. TimelineVolRaw, query per the amended rule,
     startdatetime = day_open - 9d, enddatetime = day_open.
     Last 48h of the series = the signal window; the first 7d = the coin's
     hourly-baseline window. (If GDELT coarsens resolution beyond our need,
     W-N1_precedence.py detects the bucket width from the timestamps and
     adapts; nothing here assumes a resolution.)
  2. ArtList (EVENTS only), same query, startdatetime = day_open - 48h,
     enddatetime = day_open, maxrecords=100, sortby=datedesc — the actual
     pre-ignition headlines.

GDELT etiquette (it is flaky — observed ~1/5 success, 10-20s):
  - >= 6s between requests (global), timeout 45s
  - up to 5 retries per query, backoff 8/14/20/26/32s
  - EVERY successful payload cached to W-N_cache_gdelt.json (persisted after
    each response) so reruns are free. Failures cached as {"__failed__": true}
    only after all retries die; rerun with RETRY_FAILED=1 to re-attempt them.

Read-only background job -> progress to /tmp/w-n-gdelt/progress.log
(tail -f /tmp/w-n-gdelt/progress.log).

Run:  .venv/bin/python research/alpha_swarm/hypotheses/W-N1_fetch.py
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi

REPO = Path(__file__).resolve().parents[3]
HYP = REPO / "research" / "alpha_swarm" / "hypotheses"
EVENTS = HYP / "W-N_events.json"
CACHE = HYP / "W-N_cache_gdelt.json"
PROG = Path("/tmp/w-n-gdelt/progress.log")

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
MIN_INTERVAL_S = 10.0
RETRIES = 7
RATE_COOLDOWN_S = 65.0   # observed 2026-07-11: HTTP 429 persists until ~60s quiet
TIMEOUT_S = 45.0
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
RETRY_FAILED = os.environ.get("RETRY_FAILED") == "1"

_last_req = [0.0]


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    PROG.parent.mkdir(parents=True, exist_ok=True)
    with PROG.open("a") as f:
        f.write(line + "\n")


def gdt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d%H%M%S")


def fetch(url: str) -> dict | None:
    """Paced, retried GET -> parsed json or None after RETRIES failures."""
    for att in range(RETRIES):
        wait = MIN_INTERVAL_S - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            body = urllib.request.urlopen(
                req, timeout=TIMEOUT_S, context=SSL_CTX).read().decode("utf-8", "replace")
            if body.lstrip().startswith("Please limit"):
                log(f"    rate-limited text (attempt {att+1}) — "
                    f"{RATE_COOLDOWN_S:.0f}s cool-down")
                time.sleep(RATE_COOLDOWN_S)
                continue
            if not body.strip():
                raise RuntimeError("empty body")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"    429 (attempt {att+1}) — {RATE_COOLDOWN_S:.0f}s cool-down")
                time.sleep(RATE_COOLDOWN_S)
                continue
            log(f"    retry {att+1}/{RETRIES}: HTTP {e.code}")
            time.sleep(8 + 6 * att)
        except Exception as e:  # noqa: BLE001 — flaky remote, retry everything
            log(f"    retry {att+1}/{RETRIES}: {type(e).__name__} {str(e)[:70]}")
            time.sleep(8 + 6 * att)
    return None


def q_of(sym: str) -> str:
    """Amended query rule (see module docstring): quote iff len >= 5."""
    s = f'"{sym}"' if len(sym) >= 5 else sym
    return urllib.parse.quote(f"{s} crypto sourcelang:eng")


def timeline_url(sym: str, start_ms: int, end_ms: int) -> tuple[str, str]:
    key = f"tvraw::{sym}::{gdt(start_ms)}::{gdt(end_ms)}"
    url = (f"{BASE}?query={q_of(sym)}&mode=TimelineVolRaw&format=json"
           f"&startdatetime={gdt(start_ms)}&enddatetime={gdt(end_ms)}")
    return key, url


def artlist_url(sym: str, start_ms: int, end_ms: int) -> tuple[str, str]:
    key = f"art::{sym}::{gdt(start_ms)}::{gdt(end_ms)}"
    url = (f"{BASE}?query={q_of(sym)}&mode=ArtList&format=json&maxrecords=100"
           f"&sortby=datedesc&startdatetime={gdt(start_ms)}&enddatetime={gdt(end_ms)}")
    return key, url


def main() -> None:
    events = json.loads(EVENTS.read_text())["events"]
    cache: dict = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    # all timelines FIRST (W-N1/W-N2 need only these), artlists last — the
    # analysis can start while the headline queries are still draining.
    jobs: list[tuple[str, str]] = []           # (key, url)
    for e in events:
        sym, o = e["sym"], e["open_ms"]
        jobs.append(timeline_url(sym, o - 9 * DAY_MS, o))
        if e["control"]:
            oc = e["control"]["open_ms"]
            jobs.append(timeline_url(sym, oc - 9 * DAY_MS, oc))
    for e in events:
        jobs.append(artlist_url(e["sym"], e["open_ms"] - 2 * DAY_MS, e["open_ms"]))
    # dedup (same coin can share a control/event window)
    seen: set[str] = set()
    jobs = [j for j in jobs if not (j[0] in seen or seen.add(j[0]))]

    todo = [j for j in jobs if j[0] not in cache
            or (RETRY_FAILED and cache[j[0]].get("__failed__"))]
    log(f"W-N1 GDELT fetch: {len(jobs)} queries total, {len(todo)} to fetch "
        f"({len(jobs) - len(todo)} cached)")
    t0 = time.time()
    ok = fail = 0
    for i, (key, url) in enumerate(todo):
        d = fetch(url)
        if d is None:
            cache[key] = {"__failed__": True}
            fail += 1
        else:
            cache[key] = d
            ok += 1
        CACHE.write_text(json.dumps(cache))
        done = i + 1
        rate = done / max(time.time() - t0, 1e-9)
        eta_min = (len(todo) - done) / max(rate, 1e-9) / 60
        if done % 5 == 0 or done == len(todo):
            log(f"W-N1 GDELT fetch: {done}/{len(todo)} "
                f"({100*done/len(todo):.0f}%) ok={ok} fail={fail} "
                f"eta={eta_min:.0f}min")
    log(f"DONE ok={ok} fail={fail} cache={CACHE}")


if __name__ == "__main__":
    main()
