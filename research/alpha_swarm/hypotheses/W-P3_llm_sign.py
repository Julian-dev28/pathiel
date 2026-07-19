#!/usr/bin/env python
"""W-P3: sign each of the 308 W-P1 EDGAR events with the local Claude Code CLI.

Invocation shape = the repo's ai_brain.py ClaudeCliBrain verbatim:
  claude -p --output-format json --max-turns 1 --tools "" --safe-mode
         --no-session-persistence
prompt on stdin, JSON envelope on stdout. NO web search, NO tools. cwd is an
empty scratch dir so no project CLAUDE.md leaks into the context — the prompt
contains ONLY ticker/company/form/acceptance-datetime/filing-text (spec:
findings/W-P3_llm_signed_edgar.md, pre-registered).

Checkpointing: every completed event is written to W-P3_results.json under
"llm" keyed by accession — a usage-limit hit or crash resumes for free.
Usage-limit errors: global pause 15 min, up to 4 retries/event, then UNSCORED.
Malformed model output: one immediate re-call, then UNSCORED.

Progress: /tmp/wp3-llm/progress.log (tail -f to follow).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EV_CACHE = os.path.join(HERE, "W-P3_cache_events.json")
TEXT_DIR = os.path.join(HERE, "W-P3_cache_texts")
RESULTS = os.path.join(HERE, "W-P3_results.json")

LOG_DIR = "/tmp/wp3-llm"
LOG = os.path.join(LOG_DIR, "progress.log")

WORKERS = 3
CALL_TIMEOUT_S = 300
TEXT_PROMPT_CHARS = 6000
LIMIT_SLEEP_S = 900
LIMIT_RETRIES = 4
ITEM_RE = re.compile(r"Item\s+\d+\.\d+", re.IGNORECASE)

_lock = threading.Lock()          # results + log writes
_pause_until = 0.0                # global usage-limit backoff
_pause_lock = threading.Lock()
_done = 0

CLI_ARGS = ["claude", "-p", "--output-format", "json", "--max-turns", "1",
            "--tools", "", "--safe-mode", "--no-session-persistence"]

PROMPT = """You are a filings analyst. Below is a single SEC filing, read at the exact moment EDGAR accepted it. Using ONLY the filing content provided — no outside knowledge, no memory of this company's news, earnings outcomes, or price action after this datetime — judge the likely direction of the company's stock price over the next 24 hours after acceptance.

Rules:
- LONG if the content is materially positive for the stock, SHORT if materially negative, SKIP if routine, ambiguous, or immaterial.
- Base the call ONLY on the text below.
- Respond with STRICT JSON only, no prose, no code fences:
{{"direction": "LONG"|"SHORT"|"SKIP", "conviction": <number 0-1>, "reason": "<max 20 words>"}}

FILING
Ticker: {ticker}
Company: {company}
Form: {form} (items: {items})
Accepted (UTC): {acc_iso}
--- FILING TEXT (truncated) ---
{text}"""


def log(msg: str):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    with _lock:
        print(line, flush=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")


def load_results() -> dict:
    if os.path.exists(RESULTS):
        with open(RESULTS) as f:
            return json.load(f)
    return {"llm": {}}


def save_result(acc: str, rec: dict):
    with _lock:
        res = load_results()
        res.setdefault("llm", {})[acc] = rec
        tmp = RESULTS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(res, f, indent=1)
        os.replace(tmp, RESULTS)


def build_prompt(e: dict) -> str | None:
    path = os.path.join(TEXT_DIR, f"{e['accession']}.txt")
    if not os.path.exists(path):
        return None
    txt = open(path).read()
    m = ITEM_RE.search(txt)
    start = m.start() if m else 0
    txt = txt[start:start + TEXT_PROMPT_CHARS]
    return PROMPT.format(ticker=e["ticker"], company=e["company"],
                         form=e["form"], items=e["items"] or "n/a",
                         acc_iso=e["acc_iso"], text=txt)


def parse_direction(result: str) -> dict | None:
    s = result.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s.strip())
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None
    d = str(obj.get("direction", "")).upper()
    if d not in ("LONG", "SHORT", "SKIP"):
        return None
    try:
        conv = max(0.0, min(1.0, float(obj.get("conviction", 0.0))))
    except (TypeError, ValueError):
        conv = 0.0
    return {"direction": d, "conviction": conv,
            "reason": str(obj.get("reason", ""))[:200]}


def call_cli(prompt: str, cwd: str) -> tuple[dict | None, str]:
    """Returns (envelope, err). err='limit' flags a usage-limit condition."""
    try:
        proc = subprocess.Popen(CLI_ARGS, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, cwd=cwd)
        stdout, stderr = proc.communicate(prompt, timeout=CALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except Exception:
            pass
        return None, "timeout"
    except Exception as exc:
        return None, f"launch: {exc}"
    env = None
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        pass
    if env is not None and not env.get("is_error") and proc.returncode == 0:
        return env, ""
    # Error path — classify limit vs other. The CLI phrases limits several
    # ways ("usage limit", "session limit · resets 3pm", "rate limit", 429),
    # so match broadly, but ONLY on error output, never on a success payload.
    err_text = (str(env.get("result") or "") if env is not None
                else f"{stdout}\n{stderr}").lower()
    if (env or {}).get("api_error_status") == 429 or "limit" in err_text \
            or "overloaded" in err_text:
        return None, "limit"
    if env is not None:
        return None, f"error envelope: {err_text[:300]}"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {(stderr or stdout)[:300]}"
    return None, f"non-json envelope: {stdout[:200]}"


def score_event(e: dict, cwd: str, total: int):
    global _done, _pause_until
    acc = e["accession"]
    prompt = build_prompt(e)
    if prompt is None:
        save_result(acc, {"status": "unscored", "err": "no cached text"})
        log(f"W-P3 LLM: {e['ticker']} {acc} UNSCORED (no text)")
        return
    limit_hits = 0
    malformed_retry = False
    while True:
        with _pause_lock:
            wait = _pause_until - time.time()
        if wait > 0:
            time.sleep(wait)
        env, err = call_cli(prompt, cwd)
        if err == "limit":
            limit_hits += 1
            if limit_hits > LIMIT_RETRIES:
                save_result(acc, {"status": "unscored", "err": "usage limit x4"})
                log(f"W-P3 LLM: {e['ticker']} {acc} UNSCORED (limit x{LIMIT_RETRIES})")
                return
            with _pause_lock:
                _pause_until = max(_pause_until, time.time() + LIMIT_SLEEP_S)
            log(f"W-P3 LLM: usage limit hit ({e['ticker']}), global sleep "
                f"{LIMIT_SLEEP_S//60}min (retry {limit_hits}/{LIMIT_RETRIES})")
            continue
        if env is None:
            if not malformed_retry:
                malformed_retry = True
                continue
            save_result(acc, {"status": "unscored", "err": err})
            log(f"W-P3 LLM: {e['ticker']} {acc} UNSCORED ({err})")
            return
        parsed = parse_direction(str(env.get("result") or ""))
        if parsed is None:
            if not malformed_retry:
                malformed_retry = True
                continue
            save_result(acc, {"status": "unscored", "err": "malformed x2",
                              "raw": str(env.get("result"))[:300]})
            log(f"W-P3 LLM: {e['ticker']} {acc} UNSCORED (malformed)")
            return
        rec = {"status": "ok", **parsed,
               "model": sorted((env.get("modelUsage") or {}).keys()),
               "cost_usd": env.get("total_cost_usd"),
               "duration_ms": env.get("duration_ms")}
        save_result(acc, rec)
        with _lock:
            _done += 1
            n = _done
        if n % 10 == 0 or n == total:
            log(f"W-P3 LLM: {n}/{total} scored ({100*n//total}%) — last "
                f"{e['ticker']} {parsed['direction']} conv={parsed['conviction']}")
        time.sleep(1.0)
        return


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    events = json.load(open(EV_CACHE))
    res = load_results()
    done = {a for a, r in res.get("llm", {}).items() if r.get("status") == "ok"}
    todo = [e for e in events if e["accession"] not in done]
    # re-attempt previous UNSCORED unless flagged --skip-unscored
    if "--skip-unscored" in sys.argv:
        todo = [e for e in todo if e["accession"] not in res.get("llm", {})]
    cwd = tempfile.mkdtemp(prefix="wp3_cli_")   # empty cwd: no CLAUDE.md leak
    log(f"W-P3 LLM: start — {len(todo)} to score of {len(events)} "
        f"({len(done)} done), workers={WORKERS}, follow: tail -f {LOG}")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for e in todo:
            ex.submit(score_event, e, cwd, len(todo))
    res = load_results()["llm"]
    ok = sum(1 for r in res.values() if r.get("status") == "ok")
    uns = sum(1 for r in res.values() if r.get("status") == "unscored")
    log(f"W-P3 LLM: COMPLETE — ok={ok} unscored={uns} of {len(events)}")


if __name__ == "__main__":
    main()
