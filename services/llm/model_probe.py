"""Probe which model alias actually answers right now, and remember it.

SWARM_RULES.md section 3: "Default model is the best available one; verify
the id with a one-line ping before hardcoding it." MODEL_PROBE_CANDIDATES
(config.py) is ordered best-first; the first alias that answers a trivial
ping becomes the default and is cached under
``$PATHIEL_STATE_DIR/llm/model_probe.json`` for MODEL_PROBE_TTL_S, so a
process restart (or a dev server's autoreload) does not re-spend real money
re-probing on every boot. ``PATHIEL_LLM_MODEL`` overrides and skips probing
entirely - same env-outranks-everything rule ai_brain uses for
AI_BRAIN_PROVIDER.

Verified live 2026-10-17 (see services/llm/README.md for the full transcript):
alias "fable" resolves to canonical model id claude-fable-5-1, "opus" to
claude-opus-5-5, "sonnet" to claude-sonnet-5-5 - exactly the candidates named
in the overhaul brief, confirmed callable, not guessed.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from services.llm import config
from services.llm.cli import build_args, run_cli
from services.llm.errors import LlmError

logger = logging.getLogger(__name__)

_PING_PROMPT = "Reply with exactly: OK"
_PING_USER_TEXT = "ping"

# In-process memo: at most one probe per process unless force=True, even if
# the on-disk cache is stale or absent, so a burst of early calls in the same
# process does not fire N pings while the first one is still landing.
_memo: dict[str, Any] | None = None


def probe_default_model(*, force: bool = False) -> str:
    """Return the model alias to pass as --model.

    Never raises. If every candidate is unreachable, returns "" (meaning:
    omit --model and let the CLI use its own installed default) and logs
    loudly - a thesis run on an unpinned model is recoverable; a crashed
    desk refresh is not.
    """
    global _memo

    env_pin = os.environ.get("PATHIEL_LLM_MODEL")
    if env_pin:
        return env_pin

    if not force and _memo is not None and _memo_is_fresh(_memo):
        return str(_memo["model"])

    if not force:
        cached = _read_cache()
        if cached is not None:
            _memo = cached
            return str(cached["model"])

    for candidate in config.MODEL_PROBE_CANDIDATES:
        if _ping(candidate):
            _memo = {"model": candidate, "probed_at": time.time()}
            _write_cache(_memo)
            logger.info(f"[llm] model probe: {candidate!r} is live, using it as the default")
            return candidate
        logger.warning(f"[llm] model probe: {candidate!r} did not answer")

    logger.error("[llm] model probe: no candidate answered; falling back to the CLI's own default model")
    _memo = {"model": "", "probed_at": time.time()}
    _write_cache(_memo)
    return ""


def _memo_is_fresh(memo: dict[str, Any]) -> bool:
    """The in-process memo follows the same TTL rule as the disk cache, so a
    long-running scheduler process re-probes after a degraded pick too."""
    best = config.MODEL_PROBE_CANDIDATES[0] if config.MODEL_PROBE_CANDIDATES else ""
    ttl = config.MODEL_PROBE_TTL_S if memo.get("model") == best else config.MODEL_PROBE_DEGRADED_TTL_S
    try:
        return (time.time() - float(memo.get("probed_at", 0))) <= ttl
    except (TypeError, ValueError):
        return False


def _ping(model: str) -> bool:
    args = build_args(
        binary=config.claude_binary(),
        model=model,
        system_prompt=_PING_PROMPT,
        max_turns=1,
        tools="",
    )
    try:
        envelope = run_cli(args, _PING_USER_TEXT, timeout_s=30.0)
    except LlmError as exc:
        logger.warning(f"[llm] probe ping for {model!r} failed: {exc}")
        return False
    return bool(str(envelope.get("result") or "").strip())


def _read_cache() -> dict[str, Any] | None:
    path = config.model_probe_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    probed_at = data.get("probed_at", 0)
    best = config.MODEL_PROBE_CANDIDATES[0] if config.MODEL_PROBE_CANDIDATES else ""
    ttl = config.MODEL_PROBE_TTL_S if data.get("model") == best else config.MODEL_PROBE_DEGRADED_TTL_S
    try:
        fresh = (time.time() - float(probed_at)) <= ttl
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        return None
    if data.get("model") not in (*config.MODEL_PROBE_CANDIDATES, ""):
        return None
    return data


def _write_cache(data: dict[str, Any]) -> None:
    path = config.model_probe_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(f"[llm] could not write model probe cache: {exc}")


def _reset_memo_for_tests() -> None:
    """Test-only: clear the in-process memo so a test can force a fresh probe."""
    global _memo
    _memo = None
