"""Model-duel store — A/B evaluation of the primary LLM against a second
("duelist") LLM, both answering the SAME research prompt.

The duelist is a pure OBSERVER: it never executes, never gates, and never
appears in the system prompt (so it cannot know it is being compared). Its
verdict is recorded next to the primary's and, when a trade opened from the
primary's verdict later closes, the same entry-context snapshot that the
forward signal backtest uses (executor.record_entry_context -> record_close)
carries the duelist verdict into the outcome row. That join is what makes the
A/B report honest: both models are scored on the SAME realized trades, and
the duelist's column is "what would have happened if it had been live".

Persistence is an append-only JSONL (one line per paired call) — the ledger
pattern, NOT agent-memory: the duel log is an evaluation artifact that grows
unboundedly and is never truncated, and a corrupt line is one lost row, not a
wiped live state. Path is overridable via HERMES_DUEL_FILE (conftest isolates
it, like HERMES_LEDGER_FILE).

Model identity + settings live in the agent config's `llm` block (hot,
read at call time) with an env fallback — endpoint/key stay in .env.local.
The NESTED per-slot shape (each slot owns its model/sampling/budget/template
kwargs in one place):

    "llm": {
        "primary": {            # primary slot (env fallbacks: LLM_MODEL,
                                # LLM_MAX_TOKENS)
            "model": "...",
            "sampling": {...},         # primary POST-body sampling
            "max_tokens": 8192,        # primary completion budget
            "chat_template_kwargs": {"enable_thinking": false}  # optional
        },
        "duelist": {           # duelist slot (env fallbacks: LLM_DUEL_MODEL,
                                # LLM_DUEL_MAX_TOKENS; absent everywhere
                                # = duelist disabled)
            "model": "...",
            "sampling": {...},         # duelist POST-body sampling
            "max_tokens": 8192         # duelist completion budget
        }
    }

The FLAT keys (`llm.model`, `llm.duelist_model`, `llm.sampling`,
`llm.duelist_sampling`, `llm.max_tokens`, `llm.duelist_max_tokens`) remain
as LEGACY FALLBACKS, resolved per-key below the nested slot keys, so a
pre-nest config keeps working unchanged.

A model swap is a same-inode config flip (no container recreate). When no
model is named anywhere the duelist is fully dormant: zero extra LLM calls,
zero rows, and the primary path is byte-for-byte the old behavior (it still
just calls _call_ai, which accepts explicit endpoint args with the same env
fallbacks).

Thread-safety: research runs on a worker pool (research_max_workers > 1), so
all file appends take a module lock (the session_log pattern) and the duelist
call runs in a fresh event loop on the calling thread (the _call_ai pattern —
never a shared loop across threads).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from hermes_trader.agents.config_store import read_agent_config, resolve_model_slot

logger = logging.getLogger(__name__)

# Env var names (DUEL prefix: this is the second, observation-only model).
# Read at CALL time — mirroring _call_ai's primary LLM_* handling — so a
# test can monkeypatch the env and a running process picks up a new duelist
# model without a restart.
_DUEL_URL_VARS = ("LLM_DUEL_BASE_URL",)
_DUEL_MODEL_VARS = ("LLM_DUEL_MODEL",)
_DUEL_KEY_VARS = ("LLM_DUEL_API_KEY",)

# Overridable for tests (mirrors HERMES_LEDGER_FILE / HERMES_AGENT_MEMORY_FILE).
_DUEL_FILE = os.environ.get(
    "HERMES_DUEL_FILE",
    os.path.expanduser("~/.hermes-trader-duel.jsonl"),
)

_log_lock = threading.Lock()


def _first_env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "")
        if v:
            return v
    return ""


DEFAULT_MAX_TOKENS = 8192

# Env var names for the MODEL fallbacks (config `llm.model` /
# `llm.duelist_model` win; these are read at CALL time as the fallback so a
# test can monkeypatch the env and a running process picks up a new model
# without a restart). Endpoint/key vars keep their own names (DUEL prefix:
# the second, observation-only model).
_PRIMARY_MODEL_ENV = ("LLM_MODEL", "OPENROUTER_MODEL")
_PRIMARY_MODEL_DEFAULT = "x-ai/grok-4.3"


def llm_block() -> Dict[str, Any]:
    """The agent config's `llm` block (hot read, no cache), or {} on any
    fault — fail-open: the LLM call path must never break on a config
    problem. Shared by both slots' resolvers (the duelist helpers here and
    research.effective_llm_sampling)."""
    try:
        block = read_agent_config().get("llm")
        if isinstance(block, dict):
            return block
    except Exception:  # noqa: BLE001 — fail-open (see docstring)
        pass
    return {}


def llm_slot(slot: str) -> Dict[str, Any]:
    """The agent config's `llm.<slot>` sub-dict (`slot` = ``"primary"`` or
    ``"duelist"``), hot read (no cache), or {} on any fault — fail-open.

    The nested per-slot shape (2026-09-10): each slot owns its model,
    sampling, max_tokens and chat_template_kwargs in one place:

        "llm": {
            "primary": { "model", "sampling", "max_tokens",
                         "chat_template_kwargs" },
            "duelist": { "model", "sampling", "max_tokens",
                         "chat_template_kwargs" }
        }

    The FLAT keys (``llm.model``, ``llm.duelist_model``, ``llm.sampling``,
    ``llm.duelist_sampling``, ``llm.max_tokens``, ``llm.duelist_max_tokens``)
    remain LEGACY FALLBACKS — resolved per-key by `slot_get`, so a
    pre-nest config keeps working unchanged."""
    block = llm_block()  # already fail-open ({} on any fault)
    s = block.get(slot)
    if isinstance(s, dict):
        return s
    return {}


def slot_get(slot: str, key: str, legacy_key: Optional[str] = None) -> Any:
    """``llm.<slot>.<key>`` (nested) first, then the legacy flat
    ``llm.<legacy_key>`` — or None when absent in both. Hot (no cache),
    fail-open (llm_block degrades to {} on any config fault, so this never
    raises). Per-key, NOT replace-whole-block: a partial nested slot keeps
    every legacy/env/code default the slot doesn't name.

    Priority: nested slot key > flat legacy key > (caller's) env fallback >
    code default."""
    block = llm_block()
    s = resolve_model_slot(block.get(slot))
    v = s.get(key) if isinstance(s, dict) else None
    if v is None and legacy_key is not None:
        v = block.get(legacy_key)
    return v


def effective_primary_model() -> str:
    """The PRIMARY slot's model name: agent config `llm.primary.model` first
    (hot, no cache), then the legacy flat `llm.model`, then the LLM_MODEL /
    OPENROUTER_MODEL env vars, then the code default. A model swap is a
    same-inode config flip — no recreate."""
    m = str(slot_get("primary", "model", "model") or "").strip()
    if m:
        return m
    for n in _PRIMARY_MODEL_ENV:
        v = os.environ.get(n, "")
        if v:
            return v
    return _PRIMARY_MODEL_DEFAULT


def effective_duelist_model() -> Optional[str]:
    """The DUELIST slot's model name, or None = duelist disabled: agent
    config `llm.duelist.model` first (hot), then the legacy flat
    `llm.duelist_model`, then the LLM_DUEL_MODEL env var. The model
    deliberately does NOT fall back to the primary's model: a
    silent "duel the primary against itself" doubles LLM load with no A/B
    value — the feature is dormant until a duelist model is named."""
    m = str(slot_get("duelist", "model", "duelist_model") or "").strip()
    if m:
        return m
    return _first_env(*_DUEL_MODEL_VARS) or None


# Qwen3.5-9B sampling profile — "instruct (non-thinking) mode for reasoning
# tasks" per the model card: temperature=1.0, top_p=0.95, top_k=20,
# min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0. Used by the
# duelist (whose recipe serves a Qwen3.5-9B derivative with thinking
# Verified accepted by the local llama.cpp server 2026-08-27
# (HTTP 200, finish=stop) — llama.cpp maps the OpenAI fields onto its native
# sampling flags, so top_k/min_p go in the standard body, no extra_body.
# The primary keeps temperature 0.1: it is a fine-tuned trading model, not
# base Qwen, and its verdicts have been calibrated under 0.1.
# (2026-09-09: both slots' profiles became hot-configurable — this constant
# and research.PRIMARY_SAMPLING_DEFAULT are now only the CODE DEFAULTS,
# overridable per-key at call time by the `llm.duelist_sampling` /
# `llm.sampling` agent-config blocks. Absent blocks = these defaults, so the
# on-the-wire body is byte-identical to pre-change behavior; per-model note:
# the sampling travels WITH the model choice per slot.)
DUELIST_SAMPLING_PROFILE: Dict[str, Any] = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repetition_penalty": 1.0,
}


def effective_duelist_sampling() -> Dict[str, Any]:
    """The duelist slot's sampling profile, read at CALL time (hot, no cache).

    Merge rule: {**DUELIST_SAMPLING_PROFILE, **config["llm"]["duelist"]["sampling"]}
    — per-key override, NOT replace-whole-dict, so a partial block keeps every
    default key it doesn't name. The legacy flat `llm.duelist_sampling` is the
    fallback when the nested slot key is absent. Absent both = the constant,
    i.e. today's exact body. Fail-open: any config fault degrades to the pure
    default — the primary's verdict must never cost a duelist profile read.
    """
    merged = dict(DUELIST_SAMPLING_PROFILE)
    try:
        overrides = slot_get("duelist", "sampling", "duelist_sampling")
        if isinstance(overrides, dict):
            merged.update(overrides)
    except Exception:  # noqa: BLE001 — fail-open (see docstring)
        pass
    logger.debug(f"[duel] duelist sampling: {merged}")
    return merged


def resolve_max_tokens(env_name: str, fallback: int = DEFAULT_MAX_TOKENS) -> int:
    """The env var's completion-token budget (fallback when unset/invalid/non-positive).

    Read at CALL time (same pattern as the LLM_* endpoint vars) so an
    operator can retune it without a rebuild. It caps the response length
    ONLY — it is NOT a prompt/context limit; the model server's ctx_size is
    the hard cap on input+output (2026-08-25 incident: the duelist returned
    200 OK at n_tokens=4191, well under the then-hardcoded 8192).
    """
    raw = os.environ.get(env_name, "").strip()
    if raw.isdigit():
        v = int(raw)
        if v > 0:
            return v
    return fallback


def _parse_max_tokens(v: Any) -> Optional[int]:
    """A positive-integer completion budget from a config value, or None when
    absent/invalid/non-positive. Accepts an int or an all-digit string
    (mirrors the env resolver's tolerance). bool is rejected explicitly
    (it is an int subclass)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v > 0:
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            n = int(s)
            if n > 0:
                return n
    return None


def effective_primary_max_tokens() -> int:
    """The PRIMARY slot's completion-token budget, read at CALL time (hot, no
    cache): agent config `llm.primary.max_tokens` first (a same-inode config
    flip — no recreate), then the legacy flat `llm.max_tokens`, then the
    LLM_MAX_TOKENS env var, then DEFAULT_MAX_TOKENS. It caps the RESPONSE
    length only (not a prompt/context limit)."""
    c = _parse_max_tokens(slot_get("primary", "max_tokens", "max_tokens"))
    if c is not None:
        return c
    return resolve_max_tokens("LLM_MAX_TOKENS")


def effective_duelist_max_tokens() -> int:
    """The DUELIST slot's completion-token budget, read at CALL time (hot, no
    cache): agent config `llm.duelist.max_tokens` first, then the legacy flat
    `llm.duelist_max_tokens`, then the LLM_DUEL_MAX_TOKENS env var, then the
    PRIMARY's fully-resolved budget (`effective_primary_max_tokens`), then
    DEFAULT_MAX_TOKENS. The duelist inherits the primary's resolved budget
    when its own is unset — mirrors the base_url/api_key fallback (setting
    one value controls both models)."""
    c = _parse_max_tokens(slot_get("duelist", "max_tokens", "duelist_max_tokens"))
    if c is not None:
        return c
    return resolve_max_tokens("LLM_DUEL_MAX_TOKENS", effective_primary_max_tokens())


def effective_chat_template_kwargs(slot: str) -> Dict[str, Any]:
    """The slot's ``chat_template_kwargs``, passed VERBATIM to the model
    server in the POST body (e.g. ``{"enable_thinking": false}`` — disables a
    thinking model's thinking PER REQUEST instead of loading the model with
    ``--llamacpp-args "--reasoning off"``). Nested-only:
    ``llm.<slot>.chat_template_kwargs`` (no flat/env — the env vars are for
    endpoint/key/model/budget, not template internals). Must be a dict —
    anything else (and any config fault) degrades to {}, so the POST body is
    byte-identical to the pre-feature default (fail-open, no-op). Hot, no
    cache; a copy is returned so the body can never mutate the config dict."""
    v = llm_slot(slot).get("chat_template_kwargs")
    return dict(v) if isinstance(v, dict) else {}


def duel_file() -> str:
    """Current duel-log path (read at call time so tests can redirect)."""
    return os.environ.get("HERMES_DUEL_FILE", _DUEL_FILE)


def server_processing_ms(data: Any) -> Optional[int]:
    """The model server's own processing time for a completion, in ms — or
    None when it isn't reported.

    llama.cpp-backed OpenAI-compatible servers (lemonade-server) attach a
    non-standard ``timings`` object to every NON-STREAMED completion:
    ``prompt_ms`` (prefill) + ``predicted_ms`` (generation). Their sum is the
    actual inference work and EXCLUDES queue wait — a request queued behind
    another on a serial server reports only its own work, not the time it
    spent waiting. That is exactly what the wall-clock ``*_ms`` fields
    cannot distinguish from real generation, which is why this exists.

    Fail-open by design: OpenRouter and other OpenAI-compatible endpoints do
    NOT send ``timings``, and streaming responses drop it — so any absent /
    malformed shape degrades to None (the caller records nothing extra),
    never an exception. The bot always sends ``stream: false``, so the full
    block is present whenever the backend provides it. ``predicted_ms`` is a
    PREDICTION (per-token speed × token count), not a stopwatch, but the
    predicted speed matches observed generation within a few % in practice;
    cross-checkable as ``usage.completion_tokens / predicted_per_second``.
    """
    try:
        t = data.get("timings") if isinstance(data, dict) else None
        if not isinstance(t, dict):
            return None
        prompt = float(t.get("prompt_ms") or 0.0)
        gen = float(t.get("predicted_ms") or 0.0)
        total = prompt + gen
        if total <= 0:
            return None
        return int(round(total))
    except Exception:  # noqa: BLE001 — fail-open (see docstring)
        return None


def duelist_config() -> Dict[str, Any]:
    """The duelist endpoint, resolved from the agent config's `llm` block
    with an env fallback (all read at CALL time).

    The MODEL resolves `llm.duelist_model` → LLM_DUEL_MODEL (see
    `effective_duelist_model`) and is the only duelist-specific requirement —
    base_url/api_key fall back to the PRIMARY LLM's values when unset, so
    pointing the duelist at a differently-named model on the same server is
    a one-line change. The model deliberately does NOT fall back to the
    primary's model: a silent "duel the primary against itself" would double
    LLM load with no A/B value — the feature is dormant until a duelist
    model is named somewhere. max_tokens resolves the same layered way
    (`effective_duelist_max_tokens`).
    """
    model = effective_duelist_model()
    return {
        "base_url": _first_env(*_DUEL_URL_VARS)
        or os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        "api_key": _first_env(*_DUEL_KEY_VARS)
        or os.environ.get("LLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")),
        "model": model or "",
        # llm.duelist_max_tokens → LLM_DUEL_MAX_TOKENS → the primary's
        # resolved budget → DEFAULT_MAX_TOKENS (see effective_duelist_max_tokens;
        # the duelist inherits the primary's value when its own is unset).
        "max_tokens": effective_duelist_max_tokens(),
    }


def duelist_enabled() -> bool:
    return effective_duelist_model() is not None


# ── Store ──────────────────────────────────────────────────────────────────

def record_duel(entry: Dict[str, Any]) -> None:
    """Append one paired-call row. Best-effort: an A/B artifact must never
    interrupt trading, so disk errors are swallowed (session_log pattern)."""
    row = {"ts": int(time.time() * 1000), **entry}
    try:
        with _log_lock:
            with open(duel_file(), "a") as f:
                f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def load_duels(limit: int = 5000) -> List[Dict[str, Any]]:
    """All recorded rows, oldest first, malformed lines skipped."""
    out: List[Dict[str, Any]] = []
    try:
        lines = [ln for ln in open(duel_file()).read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def resolve(coin: str, perception_id: str, max_scan: int = 400) -> Optional[Dict[str, Any]]:
    """The duelist row for a perception, or None when the duelist was disabled
    or produced no row. Scans from the end (recent first) and stops at the
    matching perception_id — perception ids are process-unique uuids, so the
    first match is THE record for this research call.

    Callers must pass the ORIGINAL perception dict (not a trimmed copy) — the
    executor's record_entry_context snapshot happens in the execute path,
    where only the analysis dict survives.
    """
    if not coin or not perception_id or perception_id == "unknown":
        return None
    for row in reversed(load_duels(limit=max_scan)):
        if row.get("perception_id") == perception_id:
            return row
    return None


# ── Live call ──────────────────────────────────────────────────────────────

def call_duelist(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout_s: float = 120.0,
    max_tokens: Optional[int] = None,
) -> tuple:
    """POST the SAME prompt to the duelist endpoint. Returns ``(text,
    server_ms)`` — the raw text plus the model server's self-reported
    processing time (None when the backend doesn't send ``timings``) —
    with ``("", None)`` on ANY failure. NEVER raises: a duelist outage must
    not cost the primary's verdict. Same shape as research._async_do_call
    (402-affordability retry included), with the 402 branch omitted: the
    duelist is a shadow/eval consumer, so a paid-provider credit failure
    degrades to a missing row rather than burning a shrunk call.
    """
    if not api_key:
        logger.warning("[duel] duelist LLM_API_KEY not set — skipping duelist call")
        return "", None
    if max_tokens is None:
        max_tokens = effective_duelist_max_tokens()
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _async_duel_call(api_key, base_url, model, system_prompt, user_message,
                             timeout_s, max_tokens)
        )
    except Exception as e:  # noqa: BLE001 — the primary path must survive any duelist fault
        # LOUD by design (2026-08-27): this used to be DEBUG — the duelist
        # doom-loop runaways were invisible in trader.log for weeks, leaving
        # only the bare "no text" line at the call site. Name the failure:
        # a ReadTimeout means the server was STILL GENERATING when the
        # client gave up (the runaway signature — the server log shows the
        # task completing minutes later); any other fault is a dead/broken
        # endpoint. Both deserve a line an operator can act on.
        if isinstance(e, httpx.TimeoutException):
            logger.warning(
                f"[duel] duelist call TIMED OUT after {timeout_s:.0f}s (non-fatal) — "
                f"server was still generating (possible runaway); verdict not recorded"
            )
        else:
            logger.warning(f"[duel] duelist call failed (non-fatal): {type(e).__name__}: {e}")
        return "", None
    finally:
        loop.close()


async def _async_duel_call(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout_s: float,
    max_tokens: int,
) -> tuple:
    """Returns ``(text, server_ms)``: the raw response text and the model
    server's self-reported processing time (``server_processing_ms`` — None
    when the backend doesn't send ``timings``). See the caller for the
    failure contract (any fault yields ``("", None)``)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        url = base_url.rstrip("/") + "/chat/completions"
        # Per-request chat-template overrides (same contract as the primary
        # slot — e.g. {"enable_thinking": false} for a thinking-capable
        # duelist model). Absent = {} → the key is NOT sent (no-op).
        chat_kwargs = effective_chat_template_kwargs("duelist")

        async def _post(max_toks: int):
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "max_tokens": max_toks,
                **effective_duelist_sampling(),
            }
            if chat_kwargs:
                body["chat_template_kwargs"] = chat_kwargs
            return await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )

        try:
            resp = await _post(max_tokens)
        except httpx.TimeoutException:
            # Single retry on timeout (2026-08-27, user-requested). A
            # ReadTimeout means the server was still generating when the
            # client gave up — with a doom-loop runaway that can be a slot
            # held for minutes, so one identical retry is cheap insurance
            # against catching the endpoint mid-task. Same budget, same
            # params; the httpx timeout still bounds the worst case at
            # ~2 x timeout_s, and the second timeout propagates to
            # call_duelist's LOUD handler below. Non-timeout faults (dead
            # endpoint, bad JSON) are NOT retried — they're not transient.
            logger.info(
                f"[duel] duelist timeout after {timeout_s:.0f}s — retrying once "
                f"(server may be mid-generation on another task)"
            )
            resp = await _post(max_tokens)
        if resp.status_code == 402:
            m = re.search(r"can only afford (\d+)", resp.text or "")
            if m and int(m.group(1)) >= 500:
                resp = await _post(int(m.group(1)) - 50)
        if resp.is_success:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                # server_ms from the response's own timings block (None when
                # the backend doesn't report it) — the queue-free processing
                # time the wall-clock timer can't separate from real work.
                return (msg.get("content") or msg.get("reasoning") or "",
                        server_processing_ms(data))
            logger.error("[duel] duelist returned 200 but no choices")
            return "", None
        logger.warning(f"[duel] duelist call FAILED: HTTP {resp.status_code} (non-fatal)")
    return "", None


# ── Report ─────────────────────────────────────────────────────────────────

def _model_stats(pnls: List[Optional[float]]) -> Dict[str, Any]:
    vals = [p for p in pnls if p is not None]
    if not vals:
        return {"closes": 0, "wins": 0, "losses": 0, "win_rate": None,
                "realized_pnl_usd": None, "avg_pnl_usd": None,
                "avg_win_usd": None, "avg_loss_usd": None}
    wins = [p for p in vals if p > 0]
    losses = [p for p in vals if p < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = abs(sum(losses) / len(losses)) if losses else None
    return {
        "closes": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(vals), 3),
        "realized_pnl_usd": round(sum(vals), 2),
        "avg_pnl_usd": round(sum(vals) / len(vals), 2),
        "avg_win_usd": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss_usd": round(avg_loss, 2) if avg_loss is not None else None,
    }


def _duelist_pnl(close: Dict[str, Any], dl: Dict[str, Any]) -> Optional[float]:
    """The duelist's realized P&L IF its verdict had been the live one.

    - Verdict direction == live side (it concurs): identical to the live P&L.
    - Verdict direction == opposite side (it would have traded against us):
      mirrored at the same % magnitude (the exchange path is symmetric:
      leveraged spot move flips sign, fees are side-agnostic).
    - PASS/CLOSE (it would have done nothing, or it closed): 0.0 — a flat
      outcome, so it counts as a "win" only when the live trade lost (the
      comparison that matters: did the duelist avoid the loss / take the win?).
    """
    side = close.get("side")
    dl_side = dl.get("side")
    pct = close.get("realized_pnl_pct")
    live_usd = close.get("realized_pnl_usd")
    if dl_side in ("long", "short"):
        if dl_side == side:
            return live_usd
        if dl_side != side and pct is not None:
            return round(-1 * (live_usd if live_usd is not None else 0.0), 2)
    return 0.0


def _latency_stats(vals: List[Optional[float]]) -> Dict[str, Any]:
    """Mean/median/max wall time of the LLM calls that reported a latency.

    Rows written before the latency fields shipped have no *_ms and are
    excluded from the mean/median (the sample size reflects that), so
    enabling the duelist mid-run can't skew the numbers with zeros.
    """
    nums = [v for v in vals if v is not None]
    if not nums:
        return {"n": 0, "avg_ms": None, "median_ms": None, "max_ms": None}
    nums_sorted = sorted(nums)
    n = len(nums_sorted)
    mid = n // 2
    median = (nums_sorted[mid] + nums_sorted[mid - 1]) / 2 if n % 2 == 0 else float(nums_sorted[mid])
    return {
        "n": n,
        "avg_ms": round(sum(nums) / n, 1),
        "median_ms": round(median, 1),
        "max_ms": max(nums),
    }


def aggregate() -> Dict[str, Any]:
    """The A/B report: primary vs duelist, scored on the SAME realized trades.

    Join: duel rows (perception_id) -> entry context (record_entry_context)
    -> outcome store (record_close carries duelist_at_entry when it was
    snapshotted). Positions closed before this shipped (or entered while the
    duelist was disabled) have no duelist column and count toward the
    primary's stats only.
    """
    from hermes_trader.agents.memory import memory

    # Idempotent: the trading loop already loaded, but the `hermes duel` CLI
    # (and any dashboard/MCP caller) imports memory fresh — hydrate so
    # get_closes() sees the persisted outcome rows. load() is read-only here
    # (aggregate never flushes).
    memory.load()

    duels = load_duels()
    closes = memory.get_closes() or []
    by_id = {d.get("perception_id"): d for d in duels if d.get("perception_id")}

    primary_pnls: List[Optional[float]] = []
    duelist_pnls: List[Optional[float]] = []
    matched = 0
    for c in closes:
        p_pct = c.get("realized_pnl_pct")
        p_usd = c.get("realized_pnl_usd")
        primary_pnls.append(p_usd if p_usd is not None else
                            (round((p_pct or 0) / 100 * (c.get("notional_usd") or 0), 2)))
        dl = c.get("duelist_at_entry")
        if not isinstance(dl, dict) or not dl:
            continue
        matched += 1
        # Backfill the verdict from the duel row when the entry snapshot only
        # carried the side (defensive — both are written together, but the
        # report should not depend on field completeness of one write).
        row = by_id.get(c.get("perception_id")) or {}
        dl_eff = {**row, **dl}
        duelist_pnls.append(_duelist_pnl(c, dl_eff))

    # Verdict agreement on the paired calls (independent of execution), and the
    # wall time each model's LLM call took (ms, from the row's *_ms fields —
    # absent on rows written before latency tracking shipped).
    agree = 0
    splits = 0
    dl_verdicts: Dict[str, int] = {}
    primary_ms: List[Optional[float]] = []
    duelist_ms: List[Optional[float]] = []
    primary_server_ms: List[Optional[float]] = []
    duelist_server_ms: List[Optional[float]] = []
    for d in duels:
        v = d.get("duelist_verdict")
        if v:
            dl_verdicts[v] = dl_verdicts.get(v, 0) + 1
        if v and d.get("primary_verdict"):
            if v == d.get("primary_verdict"):
                agree += 1
            else:
                splits += 1
        primary_ms.append(d.get("primary_ms"))
        duelist_ms.append(d.get("duelist_ms"))
        # Server-reported processing time (queue-free). Absent on rows written
        # before this shipped AND on endpoints that don't send `timings` —
        # excluded from the stats, exactly like the wall-clock fields.
        primary_server_ms.append(d.get("primary_server_ms"))
        duelist_server_ms.append(d.get("duelist_server_ms"))

    return {
        "duel_calls": len(duels),
        "realized_closes": len(closes),
        "closes_with_duelist": matched,
        "duelist": {"model": duelist_config().get("model") or "(disabled)"},
        "agreement": {"agree": agree, "split": splits,
                      "rate": round(agree / (agree + splits), 3) if (agree + splits) else None},
        "primary": _model_stats(primary_pnls),
        "duelist_if_live": _model_stats(duelist_pnls),
        "duelist_verdicts": dl_verdicts,
        "latency": {
            "primary": _latency_stats(primary_ms),
            "duelist": _latency_stats(duelist_ms),
            # wall minus server ≈ queue wait + transport (the distortion the
            # wall-only numbers can't show on a serial server under load).
            "primary_server": _latency_stats(primary_server_ms),
            "duelist_server": _latency_stats(duelist_server_ms),
        },
    }


def _latency_line(label: str, s: Dict[str, Any], kind: str = "wall") -> str:
    if not s["n"]:
        return (f"  {label:<10} {kind:6}  --  "
                f"(no {'*_server_ms' if kind == 'server' else '*_ms'} fields recorded yet)")
    return (f"  {label:<10} {kind:6}  avg {s['avg_ms']:>8.1f} ms   "
            f"median {s['median_ms']:>8.1f} ms   max {s['max_ms']:>9.1f} ms   "
            f"(n={s['n']})")


def print_report() -> None:
    """Human-readable A/B report for the `hermes duel` CLI command."""
    r = aggregate()
    p, d = r["primary"], r["duelist_if_live"]
    a = r["agreement"]
    print(f"Model duel — {r['duel_calls']} paired call(s), "
          f"{r['realized_closes']} realized close(s), {r['closes_with_duelist']} with duelist verdict")
    print(f"  duelist model: {r['duelist']['model']}")
    print(f"  verdict agreement: {a['agree']} agree / {a['split']} split ", end="")
    if a["rate"] is None:
        print("--")
    else:
        print(f"{a['rate'] * 100:.0f}% agree")
    print(f"  duelist verdict mix: {r['duelist_verdicts'] or '{}'}")
    # Wall-clock (submit→retrieve; includes queue wait on a serial server)
    # AND server-reported processing time (queue-free, from the response's
    # `timings`). The gap between the two is the queue-wait distortion the
    # original wall-only numbers hid.
    lat = r["latency"]
    print(_latency_line("primary", lat["primary"], "wall"))
    print(_latency_line("duelist", lat["duelist"], "wall"))
    print(_latency_line("primary", lat["primary_server"], "server"))
    print(_latency_line("duelist", lat["duelist_server"], "server"))
    if r["closes_with_duelist"] == 0:
        print("\n  No realized trades carry a duelist verdict yet — the P&L table")
        print("  fills in as trades opened since the duelist shipped get closed.")
        return

    def _line(label: str, s: Dict[str, Any]) -> str:
        wr = f"{s['win_rate'] * 100:5.1f}%" if s["win_rate"] is not None else "   --"
        pnl = f"{s['realized_pnl_usd']:+9.2f}" if s["realized_pnl_usd"] is not None else "       --"
        avg = f"{s['avg_pnl_usd']:+7.2f}" if s["avg_pnl_usd"] is not None else "     --"
        aw = f"{s['avg_win_usd']:+6.2f}" if s["avg_win_usd"] is not None else "   --"
        al = f"{s['avg_loss_usd']:+6.2f}" if s["avg_loss_usd"] is not None else "   --"
        return (f"  {label:<10} closes {s['closes']:>3}  win {s['wins']:>3}/{s['closes']:<3} "
                f"WR {wr}  PnL {pnl} USD  avg {avg}  win {aw} / loss -{al}")

    print()
    print(_line("primary", p))
    print(_line("duelist", d))
    print("  (duelist = what would have happened if its verdict had been the live one;")
    print("   PASS/CLOSE verdicts on a trade score 0 USD)")