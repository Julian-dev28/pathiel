#!/usr/bin/env python3
"""End-to-end smoke for the /trends tab against a RUNNING server.

The gate tests prove each piece in isolation with the network stubbed out. This
proves the pieces are actually wired to each other in the process the operator
loads: every route answers, every lane payload carries the fields the page
renders, the operator surface is closed without a token and open with one, and
the refresh button completes a full round trip.

    python scripts/smoke_trends.py                 # read-only checks
    python scripts/smoke_trends.py --with-refresh  # also runs a real lane refresh

Exit code is the number of failures, so it can gate a deploy. Read-only by
default: `--with-refresh` spends a scan (~60s for HL).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE = os.environ.get("HERMES_SMOKE_BASE", "http://127.0.0.1:8000")
LANES = ("hl", "recorders")

# What the page reads out of each lane payload. A missing key here renders as a
# blank cell or `undefined` on the tab, which is indistinguishable from "the
# market is quiet" — the exact failure this file exists to catch.
LANE_CONTRACT: Dict[str, Tuple[str, ...]] = {
    "hl": ("status", "generated_at", "scanned", "reads", "regimes", "eval", "playbook"),
    "recorders": ("status", "generated_at", "summary", "books", "playbook"),
}

class Smoke:
    def __init__(self, token: Optional[str]) -> None:
        self.token = token
        self.rows: List[Tuple[bool, str, str]] = []

    # ── plumbing ────────────────────────────────────────────────────────────

    def call(self, path: str, method: str = "GET", auth: bool = True,
             timeout: float = 30.0) -> Tuple[int, Any]:
        headers = {"X-Operator-Token": self.token} if (auth and self.token) else {}
        req = urllib.request.Request(BASE + path, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, body
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()[:200]
        except Exception as exc:                      # server down, timeout, ...
            return 0, f"{type(exc).__name__}: {exc}"

    def check(self, ok: bool, name: str, detail: str = "") -> bool:
        self.rows.append((bool(ok), name, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""),
              flush=True)
        return bool(ok)

    def missing(self, payload: Any, keys: Tuple[str, ...]) -> List[str]:
        if not isinstance(payload, dict):
            return list(keys)
        return [k for k in keys if k not in payload]

    # ── the checks ──────────────────────────────────────────────────────────

    def page(self) -> None:
        print("\nPAGE")
        code, body = self.call("/trends", auth=False)
        self.check(code == 200, "/trends renders", f"http {code}")
        if not isinstance(body, str):
            return
        for marker in ('id="lane-hl"', 'id="lane-recorders"',
                       'id="hl-refresh"', 'href="/static/app.css"'):
            self.check(marker in body, f"page carries {marker}")
        self.check("http://" not in body and "https://" not in body,
                   "page pulls no third-party asset")

    def lanes(self) -> None:
        print("\nLANES (pure cache reads)")
        for lane in LANES:
            code, payload = self.call(f"/api/dashboard/trends/{lane}", auth=False)
            if not self.check(code == 200, f"GET {lane}", f"http {code}"):
                continue
            miss = self.missing(payload, LANE_CONTRACT[lane])
            age = payload.get("age_s")
            self.check(not miss, f"{lane} payload complete",
                       f"missing {miss}" if miss else
                       f"status={payload.get('status')} age={age}s")
            if payload.get("status") == "ok":
                self.check(not payload.get("stale"), f"{lane} cache is fresh",
                           f"age {age}s")
            pb = payload.get("playbook") or {}
            self.check(bool(pb.get("actions")), f"{lane} playbook has actions",
                       f"{len(pb.get('actions') or [])} actions")

    def operator_gate(self) -> None:
        print("\nOPERATOR GATE")
        for path, method in (("/api/dashboard/trends/hl/refresh", "POST"),
                             ("/api/dashboard/trends/hl/ai", "POST")):
            code, _ = self.call(path, method, auth=False)
            self.check(code in (401, 403, 503), f"{path} closed without a token",
                       f"http {code}")
        code, _ = self.call("/api/dashboard/trends/bogus")
        self.check(code == 404, "unknown lane is a 404", f"http {code}")

    def refresh(self, lane: str) -> None:
        print(f"\nREFRESH BUTTON ({lane})")
        if not self.token:
            self.check(False, "refresh", "no operator token")
            return
        _, before = self.call(f"/api/dashboard/trends/{lane}")
        started = time.time()
        code, job = self.call(f"/api/dashboard/trends/{lane}/refresh", "POST")
        if not self.check(code == 200 and job.get("job_id"), "job accepted", f"http {code}"):
            return
        result: Dict[str, Any] = {}
        while time.time() - started < 300:
            time.sleep(5)
            _, res = self.call(
                f"/api/dashboard/trends/job/result?job_id={job['job_id']}")
            if isinstance(res, dict) and res.get("status") != "running":
                result = res
                break
        elapsed = time.time() - started
        self.check(result.get("status") == "done", "job completed",
                   f"{elapsed:.0f}s status={result.get('status')} "
                   f"{result.get('error') or ''}")
        _, after = self.call(f"/api/dashboard/trends/{lane}")
        moved = (after.get("generated_at") or 0) > (before.get("generated_at") or 0)
        self.check(moved, "cache stamp advanced",
                   f"{before.get('generated_at')} -> {after.get('generated_at')}")

    def report(self) -> int:
        bad = [r for r in self.rows if not r[0]]
        print(f"\n{'-' * 64}\n{len(self.rows) - len(bad)}/{len(self.rows)} checks passed")
        for _, name, detail in bad:
            print(f"  FAIL  {name}  {detail}")
        return len(bad)


def read_token() -> Optional[str]:
    for path in (".env.local", ".env"):
        try:
            for line in open(path):
                if line.startswith("HERMES_OPERATOR_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return os.environ.get("HERMES_OPERATOR_TOKEN")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-refresh", metavar="LANE", nargs="?", const="hl",
                    help="run a real refresh job for LANE (default hl — cheap)")
    a = ap.parse_args(argv)

    print(f"smoke: {BASE}")
    s = Smoke(read_token())
    if not s.token:
        print("  WARN  no operator token found — gated checks will be skipped")
    s.page()
    s.lanes()
    s.operator_gate()
    if a.with_refresh:
        s.refresh(a.with_refresh)
    return s.report()


if __name__ == "__main__":
    sys.exit(main())
