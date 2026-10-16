"""Decision-voice observer — a different CLASS of LLM research call.

The primary and duelist are chat-completion models: they receive a ~4.2k-token
prose prompt and GENERATE a verdict as text, which `research.parse_verdict`
scrapes off the last line. That pipeline has a structural failure class the
duel/audit history keeps hitting: a model that emits prose without a final
JSON line is silently recorded as PASS conf 0.0 (masked verdicts), and
thinking models burn the whole token budget before any answer exists.

Decision models (Cloudflare Clef / Clef-Flash, Jev, Kev, Laya — llama.cpp
decision-model support merged 2026-10-03, PR ggml-org/llama.cpp#29831) invert
the contract: the request is a STATE (the situation, as text or JSON) plus a
SCHEMA of typed questions with a CLOSED list of allowed answers, and the
response is a probability for every allowed option of every question in ONE
forward pass. No generation, no parsing — the masked-verdict class cannot
occur by construction. Published median latency for clef-flash is ~39ms
(vs ~5s for the chat arms), so it rides the scan cycle for free.

This module is a SHADOW OBSERVER, exactly like the duelist: it answers the
same perception through the typed schema, records the verdict next to the
primary's (own JSONL + prompt_log role="decision_voice" + the analysis dict's
`decision_voice_at_entry` for the ledger join), and NEVER gates, NEVER
executes, and NEVER appears in any prompt. Accrual first; thresholds and the
promote/die decision are priced OFFLINE from the logged rows (house pattern —
see .hermes/WATCHLIST.md).

Endpoint note (verified 2026-10-07): llama.cpp serves this as
POST <root>/v1/systemone. lemonade-server's proxy does NOT forward that route
(its binary only routes /v1/chat/completions and friends), so the base URL
must point at a llama-server root directly (e.g. http://192.168.1.16:8080),
NOT at lemonade's :13305/api/v1. A server running clef serves ONLY the
systemone endpoint — text generation is unavailable on it.

Dormant by default: no endpoint/model named anywhere = zero extra calls,
zero rows, primary path byte-for-byte unchanged.

Config (agent config `llm.decision_voice`, hot read at call time — same
nested-slot pattern as primary/duelist):

    "llm": {
        "decision_voice": {
            "model": "clef-flash",          # required (else dormant)
            "max_state_chars": 12000,       # state JSON budget (optional)
            "timeout_s": 30.0               # per-call budget (optional)
        }
    }

Endpoint/key are env (like the other slots' endpoint vars, read at CALL
time): LLM_DV_BASE_URL (llama-server ROOT, e.g. http://192.168.1.16:8080),
LLM_DV_API_KEY (optional — bare llama-server needs no auth).

PRIMARY-ENGINE seat (2026-10-13): the SAME machinery can drive the entry
verdict INSTEAD of the chat LLM (research() branches on it — see
`primary_engine_config` below). Sibling block, deliberately separate from
the observer seat so the two can name different models/endpoints:

    "llm": {
        "primary_engine": {
            "engine": "llm",                # "llm" (default) | "systemone"
            "model": "",                    # required when systemone
            "base_url": "",                 # llama-server ROOT; env
                                            # LLM_SYSTEMONE_BASE_URL wins
            "timeout_s": 30.0,              # per-call budget (optional)
            "max_state_chars": 12000        # state JSON budget (optional)
        }
    }

Absent block / any fault / unknown engine = "llm" — the current chat path,
byte-for-byte. Env vars: LLM_SYSTEMONE_BASE_URL (overrides the config
base_url), LLM_SYSTEMONE_API_KEY (optional).

Persistence: append-only JSONL, path via HERMES_DV_FILE (the duel-store
pattern — an evaluation artifact, never truncated, corrupt line = one row).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from hermes_trader.agents import config_store
from hermes_trader.agents.config_store import read_agent_config, resolve_model_slot

logger = logging.getLogger(__name__)

_DV_URL_VARS = ("LLM_DV_BASE_URL",)
_DV_KEY_VARS = ("LLM_DV_API_KEY",)

_DV_FILE = os.environ.get(
    "HERMES_DV_FILE",
    os.path.expanduser("~/.hermes-trader-dv.jsonl"),
)

_log_lock = threading.Lock()

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_STATE_CHARS = 12000

# The question schema. Clef reads ALL questions of a request jointly in one
# prompt (server-decision.cpp: is_joint() == CLEF), so one call answers the
# whole verdict surface. Types follow the llama.cpp/TypeSafe contract:
#   choice -> criteria maps option id -> description (<=255 options)
#   score  -> criteria is an ORDERED list of 2..10 level descriptions
#   noul   -> probability the statement is true (optional true/false descs)
# The descriptions carry the doctrine the chat system prompt states in prose
# (trend-alignment king, VETO = active rejection vs PASS = abstention,
# coin-specific news only) — the schema is where that policy now lives.
DIRECTION_QUESTION = {
    "type": "choice",
    "instructions": (
        "For this Hyperliquid perpetual setup right now: which side, if any, "
        "is the trade? Default to the direction of the 4h/1d trend. Never "
        "long when both 4h and 1d are bearish; never short when both are "
        "bullish. 'none' means no compelling setup either way (abstain)."
    ),
    "criteria": {
        "long": "Clean bullish 4h/1d EMA alignment, or whale-accumulation "
                "counter-trend flag; entry structure present.",
        "short": "Clean bearish 4h/1d EMA alignment; a 1h bounce in a "
                 "downtrend is a short, not a dip-buy.",
        "none": "No coherent multi-TF direction, or the setup is marginal.",
    },
}

CONVICTION_QUESTION = {
    "type": "score",
    "instructions": "How strong is this setup as a trade, overall?",
    "criteria": [
        "no edge at all — muddled or conflicting signals",
        "marginal — would cost money to take",
        "respectable — partial alignment, some structure",
        "strong — clean 4h/1d trend plus entry structure",
        "exceptional — trend + structure + catalyst/positioning all agree",
    ],
}

TRAP_QUESTION = {
    "type": "noul",
    "instructions": (
        "Is ENTERING this setup right now actively dangerous — a trap: late "
        "chase straight into reversion pressure, entry pinned against a wall, "
        "thin-stop chop, or structure/news risk that makes either side a "
        "loser's bet? (Not merely 'unexciting' — that is not a trap.)"
    ),
}

NEWS_NEG_QUESTION = {
    "type": "noul",
    "instructions": (
        "Does the recent news contain a confirmed adverse event SPECIFIC to "
        "this coin — its own hack/exploit, lawsuit/enforcement/delisting/halt "
        "naming it, or its own earnings miss? Generic market/macro headlines "
        "are NOT negative news for this coin."
    ),
}

CLOSE_QUESTION = {
    "type": "noul",
    "instructions": (
        "The account HOLDS a position in this coin. Has the structure flipped "
        "against the held position such that it should be closed now? (A "
        "position that is merely at a small loss with intact trend structure "
        "should keep running.) state.held_position carries entry_px, "
        "unrealized_pnl_usd and pnl_pct_vs_entry (signed for the side: "
        "negative = losing): weigh how deep the loss is against whether the "
        "structure can still recover it."
    ),
}


def dv_enabled() -> bool:
    """True when a decision-voice model AND endpoint are named. Hot read."""
    return _dv_config() is not None


def _dv_config() -> Optional[Dict[str, Any]]:
    """Resolve the decision-voice slot: config `llm.decision_voice.model` +
    env LLM_DV_BASE_URL. Returns None (fully dormant) when either is absent.
    Fail-open: any config fault degrades to dormant, never raises."""
    try:
        block = read_agent_config().get("llm")
        raw_slot = block.get("decision_voice") if isinstance(block, dict) else None
        slot = resolve_model_slot(raw_slot)
        if not isinstance(slot, dict):
            slot = {}
        if str(slot.get("enabled", True)).lower() in ("false", "0", "off"):
            return None
        model = str(slot.get("model") or "").strip()
        if not model:
            return None
        # base_url precedence mirrors primary_engine_config: PROFILE base_url
        # (a shadow seat names its own server — e.g. the d3-nano profile
        # pointing at nano-serve) > env vars > inline slot key.
        base_url = ""
        if isinstance(raw_slot, dict) and raw_slot.get("model_profile"):
            base_url = str(
                config_store.read_model_profile(raw_slot["model_profile"]).get("base_url") or ""
            ).strip()
        if not base_url:
            for n in _DV_URL_VARS:
                base_url = os.environ.get(n, "")
                if base_url:
                    break
        if not base_url:
            base_url = str(slot.get("base_url") or "").strip()
        if not base_url:
            return None
        api_key = ""
        for n in _DV_KEY_VARS:
            api_key = os.environ.get(n, "")
            if api_key:
                break
        return {
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "max_state_chars": int(slot.get("max_state_chars") or DEFAULT_MAX_STATE_CHARS),
            "timeout_s": float(slot.get("timeout_s") or DEFAULT_TIMEOUT_S),
            "fade_fields": str(slot.get("fade_fields", True)).lower() not in ("false", "0", "off"),
            "composite_score": str(slot.get("composite_score", False)).lower() not in ("false", "0", "off"),
            "vwap_fields": str(slot.get("vwap_fields", False)).lower() not in ("false", "0", "off"),
            # t_a99fdac2 (B.44): side+magnitude breakout label in seat state
            # (breakoutUp:0.42% / breakoutDown:0.12%). Default off = today's
            # byte-identical state; profile opt-in, hot rollback.
            "breakout_side_label": str(slot.get("breakout_side_label", False)).lower() not in ("false", "0", "off"),
        }
    except Exception:  # noqa: BLE001 — fail-open (see module docstring)
        return None


def dv_file() -> str:
    """Current decision-voice log path (read at call time so tests redirect)."""
    return os.environ.get("HERMES_DV_FILE", _DV_FILE)


# ── primary-engine seat (2026-10-13) ───────────────────────────────────────

_LLM_ENGINE = {"engine": "llm"}


def _parse_trap_veto_overrides(raw) -> Dict[str, float]:
    """Normalise the `trap_veto_overrides` config slot: {tag: bar}. Malformed
    entries are dropped (fail-open to the global bar), never raise."""
    out: Dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[str(k).strip().lower()] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def _trap_veto_cohort_tag(state: Any, verdict_side: Any) -> Optional[str]:
    """Cohort tag for trap-veto bar overrides (B.46 fade-short promote).

    Returns "vwap-<shape>-<side>" when the state carries a vwap_deviation
    field whose suggested_side MATCHES the entry side — an entry WITH the
    signal. Counter-signal entries (side mismatch) and non-vwap states get
    None = the global bar. Pure: no logging, no config read (decision-
    function purity rule)."""
    try:
        vw = state.get("vwap_deviation") if isinstance(state, dict) else None
        if not isinstance(vw, dict):
            return None
        side = str(vw.get("suggested_side") or "").strip().lower()
        if not side or side != str(verdict_side or "").strip().lower():
            return None
        shape = str(vw.get("shape") or "").strip().lower()
        if not shape:
            return None
        return f"vwap-{shape}-{side}"
    except Exception:  # noqa: BLE001 — tagging must never break the veto path
        return None

_SYSTEMONE_URL_VARS = ("LLM_SYSTEMONE_BASE_URL",)
_SYSTEMONE_KEY_VARS = ("LLM_SYSTEMONE_API_KEY",)


def primary_engine_config() -> Dict[str, Any]:
    """Resolve the PRIMARY verdict engine from config `llm.primary_engine`.

    {"engine": "llm"} (the default when the block is absent) = the chat
    path in research(), byte-for-byte today's behavior. {"engine":
    "systemone", ...} = the decision model answers the typed
    state+questions schema INSTEAD of the LLM generation call.

    Hot read at every research() call (no cache — same pattern as the
    other llm slots, so arming the seat is a same-inode config flip).
    Fail-open: any config fault, an unknown engine name, or a systemone
    engine missing model/base_url degrades to "llm" — the switch can
    never break the primary path. A missing model/endpoint while asking
    for systemone logs LOUDLY (an intended flip that silently no-ops is
    worse than a crash).
    """
    try:
        block = read_agent_config().get("llm")
        raw_slot = block.get("primary_engine") if isinstance(block, dict) else None
        slot = resolve_model_slot(raw_slot)
        if not isinstance(slot, dict):
            return dict(_LLM_ENGINE)
        engine = str(slot.get("engine") or "llm").strip().lower()
        if engine != "systemone":
            return dict(_LLM_ENGINE)
        model = str(slot.get("model") or "").strip()
        # base_url precedence: PROFILE base_url (per-model endpoint — the
        # env var names one fixed server, so it must never silently
        # re-point a profile at the wrong model) > env vars (ops override
        # for inline configs, existing contract) > inline slot key.
        profile_base_url = ""
        if isinstance(raw_slot, dict) and raw_slot.get("model_profile"):
            profile_base_url = str(
                config_store.read_model_profile(raw_slot["model_profile"]).get("base_url") or ""
            ).strip()
        base_url = profile_base_url
        if not base_url:
            for n in _SYSTEMONE_URL_VARS:
                base_url = os.environ.get(n, "")
                if base_url:
                    break
        if not base_url:
            base_url = str(slot.get("base_url") or "").strip()
        if not model or not base_url:
            logger.warning(
                "[research] primary_engine=systemone but model/base_url "
                "missing — staying on the LLM engine (flip is INERT)"
            )
            return dict(_LLM_ENGINE)
        api_key = ""
        for n in _SYSTEMONE_KEY_VARS:
            api_key = os.environ.get(n, "")
            if api_key:
                break
        return {
            "engine": "systemone",
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "timeout_s": float(slot.get("timeout_s") or DEFAULT_TIMEOUT_S),
            "max_state_chars": int(slot.get("max_state_chars") or DEFAULT_MAX_STATE_CHARS),
            # Entry trap veto (owner call 2026-10-13, nano-tuned book):
            # threshold 0/absent = OFF (byte-identical seat mapping);
            # >0 = a LONG/SHORT whose trap noul EXCEEDS the threshold is
            # rejected. shadow defaults TRUE (house doctrine: new gates
            # ship log-only); the config flip sets it false to arm live.
            "trap_veto_threshold": float(slot.get("trap_veto_threshold") or 0.0),
            "trap_veto_shadow_mode": str(
                slot.get("trap_veto_shadow_mode", True)
            ).lower() not in ("false", "0", "off"),
            # Per-cohort trap-veto bar overrides (B.46 fade-short promote):
            # {cohort tag: bar}. A with-the-signal entry whose state carries
            # a matching vwap_deviation field uses the cohort bar INSTEAD of
            # the global one; absent key / malformed entries fail open to
            # the global bar (no-op merge — byte-identical without the key).
            "trap_veto_overrides": _parse_trap_veto_overrides(
                slot.get("trap_veto_overrides")
            ),
            # Fade-family state shape (B.44 gauntlet, MODEL-DEPENDENT):
            # true = side-aware fields, names stripped (nano-tuned, the
            # shipped default); false = names in, no fields (decision2 —
            # the fields price −46% there, see WATCHLIST B.44/B.45).
            "fade_fields": str(slot.get("fade_fields", True)).lower() not in ("false", "0", "off"),
            # composite_score in state: MODEL-DEPENDENT (B.45 re-run) —
            # harmful on nano, +12.04% on decision2 (scoreadd arm). Default
            # off (nano-tuned); decision2's profile turns it on.
            "composite_score": str(slot.get("composite_score", False)).lower() not in ("false", "0", "off"),
            # vwapDeviation numeric fields: keep-out was nano-tuned; the
            # verdict-side re-score reads mildly positive for decision2
            # (B.46 watch). Default off; profiles opt in per model.
            "vwap_fields": str(slot.get("vwap_fields", False)).lower() not in ("false", "0", "off"),
            # t_a99fdac2 (B.44): side+magnitude breakout label in seat state
            # (breakoutUp:0.42% / breakoutDown:0.12%). Default off = today's
            # byte-identical state; profile opt-in, hot rollback.
            "breakout_side_label": str(slot.get("breakout_side_label", False)).lower() not in ("false", "0", "off"),
        }
    except Exception:  # noqa: BLE001 — fail-open: the LLM engine is the default
        return dict(_LLM_ENGINE)


def systemone_primary_verdict(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_raw: str,
    news: str,
    open_positions: List[Dict[str, Any]],
    win_rate: float,
    n_closes: int,
    cfg: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Run the decision model as the PRIMARY verdict engine for one research
    call (the seat the DV observer shadows). Reuses the exact observer
    machinery: build_state + build_questions + call_systemone +
    verdict_from_answers (whose output is parse_verdict-shaped, so every
    downstream gate/sizing/executor site is engine-agnostic).

    Returns {"parsed", "answers", "request_json", "wall_ms", "server_ms"}
    on success, None on ANY call failure — the CALLER owns the fallback
    policy (owner call 2026-10-13: failure-PASS + ai_down, NO LLM
    fallback, so the 27B stays unloaded). NEVER raises: call_systemone
    already swallows every transport fault and returns (None, ms, None).
    """
    held = any(p.get("coin") == coin for p in (open_positions or []))
    state = build_state(
        coin, perception, tf1h, tf4h, tf1d, funding_raw, news,
        open_positions, win_rate, n_closes,
        max_state_chars=cfg["max_state_chars"],
        fade_shape="fields" if cfg.get("fade_fields", True) else "names",
        emit_composite_score=bool(cfg.get("composite_score", False)),
        vwap_fields=bool(cfg.get("vwap_fields", False)),
        breakout_side_label=bool(cfg.get("breakout_side_label", False)),
    )
    questions = build_questions(held, news_live=_news_live(news))
    answers, wall_ms, server_ms = call_systemone(
        cfg["base_url"], cfg["api_key"], cfg["model"], state, questions,
        timeout_s=cfg["timeout_s"],
    )
    if answers is None:
        return None
    parsed = verdict_from_answers(answers, coin, perception)
    parsed.pop("_answers", None)  # raw probs go to the archive, not the analysis

    # Entry trap veto (seat-level, config `llm.primary_engine.trap_veto_*`):
    # the nano-tuned optimum (trap<=.35 rung, +$53.48 vs +$38.35 at live
    # bars on the 15,792-state walk) — a LONG/SHORT the seat calls with a
    # trap noul ABOVE the threshold is downgraded to VETO, the executor's
    # categorical rejection (never upgraded by whale/composite/breakout
    # force paths). Entry-only: CLOSE/PASS/VETO untouched; threshold 0 =
    # off, byte-identical to the seat without the knob.
    thr = cfg.get("trap_veto_threshold") or 0.0
    # Cohort tag (B.46): a with-the-signal entry whose state carries a
    # matching vwap field. Attached to the verdict so the executor's runner
    # gate can apply the model+cohort admission (the cohort's entries live
    # under the general conf floor AND outside its shape requirements —
    # 0/34 reach conf 0.65, 1/60 pass structured_short). Model-scoping is
    # structural: only THIS seat (its own state + model identity) tags;
    # another model in the seat would emit its own identity or nothing.
    cohort_tag = _trap_veto_cohort_tag(state, parsed.get("side"))
    if cohort_tag and parsed["verdict"] in ("LONG", "SHORT"):
        parsed["cohort_tag"] = cohort_tag
    if thr > 0 and parsed["verdict"] in ("LONG", "SHORT"):
        trap_p = float((answers.get("trap") or {}).get("noul", 0.0))
        # Per-cohort bar override (B.46): with-the-signal entries whose
        # state carries a matching vwap field use the cohort bar; anything
        # else keeps the global bar (absent overrides = byte-identical).
        bar = thr
        tag = cohort_tag
        if tag and tag in (cfg.get("trap_veto_overrides") or {}):
            bar = cfg["trap_veto_overrides"][tag]
        # Annotate the line with the cohort tag ONLY when the override is
        # what set the bar — without an override the lines stay byte-
        # identical to the pre-B.46 format (watchlist greps depend on it).
        note = f"({tag}) " if bar != thr else ""
        if trap_p > bar:
            if cfg.get("trap_veto_shadow_mode", True):
                logger.warning(
                    f"[research] {coin}: primary trap {trap_p:.2f} > {bar:.2f} "
                    f"{note}"
                    f"— WOULD BE VETOED (shadow; entry proceeds)"
                )
            else:
                parsed["reasoning"] = (
                    (parsed.get("reasoning") or "")
                    + f" [trap_veto: trap {trap_p:.2f} > {bar:.2f}]"
                )
                parsed["verdict"], parsed["side"] = "VETO", None
                logger.warning(
                    f"[research] {coin}: primary trap {trap_p:.2f} > {bar:.2f} "
                    f"{note}"
                    f"— VETOED (entry blocked)"
                )
        elif bar > thr and trap_p > thr:
            # The cohort bar is the ONLY reason this entry proceeds — log it
            # so the promoted cohort stays grep-separable for the ledger join.
            # Regime annotation (B.46): the archive read is regime-SYMMETRIC
            # for fade-shorts (+1.59%/ep down vs +1.55%/ep up) but the window
            # never saw a sustained euphoric tape — tag every override-pass
            # so the live join can cut by regime and gate later WITH evidence
            # if a regime dependency appears. Fail-open: a regime-read fault
            # must never cost the entry or the line.
            try:
                from hermes_trader.agents import market_regime
                _reg = market_regime.detect_regime("BTC")
            except Exception:  # noqa: BLE001
                _reg = "unknown"
            logger.info(
                f"[research] {coin}: trap {trap_p:.2f} <= cohort bar {bar:.2f} "
                f"({tag}) — global bar {thr:.2f} overridden, entry proceeds "
                f"(regime={_reg})"
            )
    return {
        "parsed": parsed,
        "answers": answers,
        # Self-contained rendered request (same shape as the decision_voice
        # prompt-log rows) — archived as user_message so llm_ab_replay prep
        # cohorts keep working unchanged.
        "request_json": json.dumps({"state": state, "questions": questions}),
        "wall_ms": wall_ms,
        "server_ms": server_ms,
    }


def record_dv(entry: Dict[str, Any]) -> None:
    """Append one row. Best-effort: an eval artifact must never interrupt
    trading (the duel-store pattern)."""
    row = {"ts": int(time.time() * 1000), **entry}
    try:
        with _log_lock:
            with open(dv_file(), "a") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass


# ── state + schema builders ────────────────────────────────────────────────

def build_state(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_raw: str,
    news: str,
    open_positions: List[Dict[str, Any]],
    win_rate: float,
    n_closes: int,
    max_state_chars: int = DEFAULT_MAX_STATE_CHARS,
    fade_shape: str = "fields",
    emit_composite_score: bool = False,
    vwap_fields: bool = False,
    breakout_side_label: bool = False,
) -> Dict[str, Any]:
    """The STATE: a compact structured snapshot of what the chat prompt says
    in prose. Deliberately lean (~1-2k tokens): the decision model's edge is
    reading facts and scoring them, not following a long instruction chain —
    the doctrine lives in the question descriptions instead.

    No lookahead, no prices beyond what the scan already had. Keys are stable
    so historical accrual rows stay comparable."""
    held = next(
        (p for p in (open_positions or []) if p.get("coin") == coin), None
    )
    # B.44 fade-family state fix 2026-10-14 (d3-nano gauntlet, 870 calls x 4
    # arms): the side-less names mfiExtreme/rsiDivergence in triggers_fired
    # act as danger-tokens for the decision model — trap-prob up, vetoes on
    # winners, zero directional value (strip-only arm: +40.5% priced). With
    # side-aware fields instead, alignment is 99% and the fields_nonames arm
    # nets +24.4% with a neutral trap delta. So: names out, fields in.
    triggers: List[str] = []
    fade_fields: Dict[str, Any] = {}

    def _label(t: Dict[str, Any]) -> str:
        # t_a99fdac2 (B.44 doctrine: names must carry side): render the
        # range-break as breakoutUp:0.42% / breakoutDown:0.12% instead of
        # the side-less "breakout" — the NXPC 2026-10-15 loss rode a 12bp
        # DOWNWARD scrape presented to the seat as a bullish-sounding name.
        # Render-site only: the internal trigger name, composite weights,
        # gate lookups and shadow logs keep using "breakout".
        if (breakout_side_label and t.get("name") == "breakout"
                and t.get("side") in ("up", "down")):
            pct = t.get("pct_break")
            pct_s = f":{pct:.2f}%" if isinstance(pct, (int, float)) else ""
            return f"breakout{'Up' if t['side'] == 'up' else 'Down'}{pct_s}"
        return t.get("name") or ""

    for t in (perception.get("triggers") or []):
        if not t.get("fired"):
            continue
        name = t.get("name")
        if fade_shape != "fields":
            # decision2 shape (B.44/B.45 re-run): names stay, no fields.
            triggers.append(_label(t))
            continue
        if name == "mfiExtreme":
            if t.get("side"):
                fade_fields["mfi_extreme"] = {
                    "signal": f"mfi_{t.get('kind')}",
                    "trade_side": t["side"],
                    "expected_move": "price_reverts_from_volume_exhaustion",
                    "mfi_value": t.get("mfi_value"),
                    "threshold": t.get("threshold"),
                }
            continue
        if name == "rsiDivergence":
            if t.get("side"):
                fade_fields["rsi_divergence"] = {
                    "signal": f"rsi_divergence_{t.get('flavour')}",
                    "trade_side": t["side"],
                    "expected_move": "momentum_reversal_from_divergence",
                    "price_extreme_pct": t.get("extreme_pct"),
                }
            continue
        triggers.append(_label(t))

    def _tf(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ema8": _round(d.get("ema8")),
            "ema21": _round(d.get("ema21")),
            "ema8_above_ema21": _bool_above(d.get("ema8"), d.get("ema21")),
            "rsi14": _round(d.get("rsi14")),
            "atr14": _round(d.get("atr14")),
            "adx14": _round(d.get("adx14")),
        }

    state: Dict[str, Any] = {
        "coin": coin,
        "mid": perception.get("mid"),
        "daily_move_pct": perception.get("daily_move_pct"),
        "funding_rate": funding_raw,
        "triggers_fired": triggers,
        "tf_1h": _tf(tf1h or {}),
        "tf_4h": _tf(tf4h or {}),
        "tf_1d": _tf(tf1d or {}),
    }
    # B.45 per-field gauntlet 2026-10-14: track_record is net-negative for
    # every seat model tested and no longer rides the state (add −11.96% on
    # nano, −2.75% on decision2 — a ~0.63 lifetime win rate reads as caution
    # and vetoed winners worth +12.21%). composite_score is MODEL-DEPENDENT:
    # harmful on nano (strip +1.67%, it suppressed vetoes) but HELPFUL on
    # decision2 (scoreadd arm +12.04%, 908 calls) — so it is a per-model
    # profile knob (emit_composite_score), default off. win_rate/n_closes
    # params kept for call-site stability; the chat seat still sees both in
    # prose.
    # B.45 2026-10-14: the news feed has NEVER fired since go-live (no
    # BRAVE_API_KEY — all 19,955 archived calls carry literal "no news"), so
    # news_recent was a constant token and news_negative was answered blind.
    # Owner call: drop the field + question while the feed is disabled; they
    # auto-return the moment real headlines flow.
    if _news_live(news):
        state["news_recent"] = news.strip()[:1500]
    if emit_composite_score and perception.get("composite_score") is not None:
        state["composite_score"] = _round(perception.get("composite_score"))
    # vwapDeviation numeric fields (B.46, d2-tuned): the gauntlet's vw-full
    # arm shape. The NAME already rides triggers_fired on the seat path
    # (the shadow filter is chat-prompt only); the numbers are the knob.
    # Verdict-side re-score: mildly POSITIVE for decision2 (+43pp on the
    # vwap cohort, thin) — profile-gated, logged so the live accrual can be
    # re-checked before it hardens.
    if vwap_fields:
        vw = next((t for t in (perception.get("triggers") or [])
                   if t.get("fired") and t.get("name") == "vwapDeviation"), None)
        if vw and vw.get("side"):
            state["vwap_deviation"] = {
                "shape": vw.get("shape"),
                "suggested_side": vw.get("side"),
                "z_sigma": vw.get("z"),
                "anchor_age_bars": vw.get("age"),
            }
            logger.info(
                f"[dv] vwap_deviation fields emitted: {state['vwap_deviation']} "
                f"(profile vwap_fields on — B.46 watch)"
            )
    whale = perception.get("whale_signal")
    if whale:
        state["whale_signal"] = whale if isinstance(whale, str) else str(whale)
    if held:
        hp: Dict[str, Any] = {
            "side": held.get("side"),
            "size_usd": _round(held.get("size_usd")),
        }
        # B.39 close-voice audit 2026-10-10: the close_now question was asked
        # blind to position depth (side+size only) — dv_close_now capped at
        # ~0.23 lifetime. Entry price + live PnL (abs + % vs entry, computed
        # against the scan's mid) ride the state so the decision model can
        # actually weigh "structure flipped" against how deep the bag is.
        entry = _round(held.get("entry_px"))
        if entry:
            hp["entry_px"] = entry
            mid = perception.get("mid")
            try:
                pnl = held.get("unrealized_pnl_usd")
                if pnl is not None:
                    hp["unrealized_pnl_usd"] = _round(pnl, 2)
                if mid:
                    raw = (mid - entry) / entry * 100
                    if held.get("side") == "short":
                        raw = -raw
                    hp["pnl_pct_vs_entry"] = _round(raw, 2)
            except (TypeError, ValueError):
                pass
        state["held_position"] = hp

    # Fade fields ride LAST (same position the gauntlet arms appended them).
    state.update(fade_fields)

    # Bound the state (encode_record's max_state_tokens analogue): if the
    # JSON is over budget, drop the news text first — it is the bulkiest and
    # the least structural part.
    try:
        if len(json.dumps(state)) > max_state_chars:
            if "news_recent" in state:
                state["news_recent"] = state["news_recent"][: max(0, max_state_chars // 4)]
    except (TypeError, ValueError):
        pass
    return state


def _news_live(news: Any) -> bool:
    """True only when the feed actually produced headline text (B.45)."""
    return bool(news) and str(news).strip().lower() not in ("no news", "none")


def build_questions(held: bool, news_live: bool = False) -> Dict[str, Dict[str, Any]]:
    """The question schema for one research call. `close_now` is asked ONLY
    when the coin is held — a decision model can't misread a CLOSE for a
    coin it was never asked about, so the phantom-CLOSE class (2026-09-04
    xyz:HOOD) is structurally impossible here, not just guarded.
    `news_negative` is asked ONLY when the news feed is live (B.45) —
    verdict_from_answers treats its absence as news_risk=none."""
    q = {
        "direction": DIRECTION_QUESTION,
        "conviction": CONVICTION_QUESTION,
        "trap": TRAP_QUESTION,
    }
    if news_live:
        q["news_negative"] = NEWS_NEG_QUESTION
    if held:
        q["close_now"] = CLOSE_QUESTION
    return q


def _round(v: Any, nd: int = 6) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, nd)


def _bool_above(a: Any, b: Any) -> Optional[bool]:
    try:
        return float(a) > float(b)
    except (TypeError, ValueError):
        return None


# ── the call ───────────────────────────────────────────────────────────────

def call_systemone(
    base_url: str,
    api_key: str,
    model: str,
    state: Dict[str, Any],
    questions: Dict[str, Dict[str, Any]],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple:
    """POST <base>/v1/systemone. Returns (answers, wall_ms, server_ms) or
    (None, wall_ms, None) on ANY failure — NEVER raises (the primary's verdict
    is already in hand; this is an observer). server_ms comes from the
    response's timings block when the backend reports it.

    Response contract (llama.cpp server-decision / TypeSafe): {"model",
    "answers": {qid: {...}}, "usage": {input_tokens, output_tokens: 0}}.
    choice -> {choice, confidence, probabilities}; score -> {score,
    confidence, legend, probabilities}; noul -> {noul: P(true)}."""
    t0 = time.monotonic()
    try:
        loop = asyncio.new_event_loop()
        try:
            answers, server_ms = loop.run_until_complete(
                _async_dv_call(base_url, api_key, model, state, questions, timeout_s)
            )
        finally:
            loop.close()
        return answers, int((time.monotonic() - t0) * 1000), server_ms
    except Exception as e:  # noqa: BLE001
        kind = "TIMED OUT" if isinstance(e, httpx.TimeoutException) else f"failed ({type(e).__name__})"
        logger.warning(f"[dv] decision-voice call {kind} (non-fatal) — row not recorded")
        return None, int((time.monotonic() - t0) * 1000), None


async def _async_dv_call(
    base_url: str,
    api_key: str,
    model: str,
    state: Dict[str, Any],
    questions: Dict[str, Dict[str, Any]],
    timeout_s: float,
) -> tuple:
    url = base_url.rstrip("/") + "/v1/systemone"
    body = {"model": model, "state": state, "questions": questions}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        resp = await client.post(url, json=body, headers=headers)
        if not resp.is_success:
            logger.warning(
                f"[dv] systemone HTTP {resp.status_code}: {(resp.text or '')[:200]}"
            )
            return None, None
        data = resp.json()
        answers = data.get("answers")
        if not isinstance(answers, dict) or not answers:
            logger.warning("[dv] systemone returned 200 but no answers object")
            return None, None
        server_ms = None
        timings = data.get("timings")
        if isinstance(timings, dict):
            try:
                server_ms = int(round(
                    float(timings.get("prompt_ms", 0)) + float(timings.get("predicted_ms", 0))
                ))
            except (TypeError, ValueError):
                server_ms = None
        return answers, server_ms


# ── answers -> verdict dict (parse_verdict-shaped) ─────────────────────────

def verdict_from_answers(
    answers: Dict[str, Any],
    coin: str,
    perception: Dict[str, Any],
) -> Dict[str, Any]:
    """Map the typed answers onto the SAME dict shape parse_verdict returns,
    so the accrual joins the existing duel/counterfactual scoring machinery.

    Rules (fixed, logged raw probabilities alongside so every threshold
    variant is priced offline — no tuning constants in the live path):
      - held + close_now true-prob > direction-margin rules -> CLOSE
      - direction long/short -> LONG/SHORT (side derived, like parse_verdict)
      - direction none + trap -> VETO (active rejection)
      - direction none, no trap -> PASS
      - confidence = conviction expected level / (levels-1), i.e. 0..1.
    entry/stop/tp are NOT produced by a decision model — entry_px = mid,
    stops stay 0 (the deterministic DSL owns the bracket anyway).
    """
    out = {
        "verdict": "PASS",
        "confidence": 0.0,
        "side": None,
        "entry_px": perception.get("mid", 0),
        "stop_px": 0.0,
        "tp_px": 0.0,
        "news_risk": "none",
        "reasoning": "",
        "ai_down": False,
        "close_guard_downgraded": False,
    }

    dir_a = answers.get("direction") or {}
    probs = dir_a.get("probabilities") or {}
    p_long = float(probs.get("long", 0.0))
    p_short = float(probs.get("short", 0.0))
    p_none = float(probs.get("none", 0.0))
    # "none" wins ties: an absent/empty probability map must degrade to
    # PASS, never to an accidental directional call.
    top = max(((p_long, "long"), (p_short, "short"), (p_none, "none")),
              key=lambda t: (t[0], t[1] == "none"))

    conv_a = answers.get("conviction") or {}
    levels = conv_a.get("probabilities") or {}
    n_levels = max(len(levels), 1)
    # expected level index / (n_levels-1) -> 0..1
    try:
        exp_level = sum(i * float(p) for i, p in enumerate(levels.values())) / max(
            sum(float(p) for p in levels.values()), 1e-9
        )
    except (TypeError, ValueError):
        exp_level = 0.0
    confidence = max(0.0, min(1.0, exp_level / (n_levels - 1))) if n_levels > 1 else 0.0

    trap_p = float((answers.get("trap") or {}).get("noul", 0.0))
    news_p = float((answers.get("news_negative") or {}).get("noul", 0.0))
    close_p = (answers.get("close_now") or {}).get("noul")

    if news_p >= 0.5:
        out["news_risk"] = "negative"

    # CLOSE only exists when the coin was held (the question is only asked
    # then) — the phantom-CLOSE class is impossible by construction.
    if close_p is not None and float(close_p) >= 0.5:
        out["verdict"] = "CLOSE"
    elif top[1] == "long":
        out["verdict"], out["side"] = "LONG", "long"
    elif top[1] == "short":
        out["verdict"], out["side"] = "SHORT", "short"
    elif trap_p >= 0.5:
        out["verdict"] = "VETO"
    else:
        out["verdict"] = "PASS"

    out["confidence"] = round(confidence, 4)
    out["reasoning"] = (
        f"dv: dir L{p_long:.2f}/S{p_short:.2f}/N{p_none:.2f} "
        f"conv{confidence:.2f} trap{trap_p:.2f} news{news_p:.2f}"
        + (f" close{float(close_p):.2f}" if close_p is not None else "")
    )
    # Raw answers ride along for offline threshold pricing (never in the
    # analysis dict — that stays lean; the DV JSONL keeps them).
    out["_answers"] = answers
    return out


# ── the research hook ──────────────────────────────────────────────────────

def decision_voice_verdict(
    coin: str,
    perception: Dict[str, Any],
    tf1h: Dict[str, Any],
    tf4h: Dict[str, Any],
    tf1d: Dict[str, Any],
    funding_raw: str,
    news: str,
    open_positions: List[Dict[str, Any]],
    win_rate: float,
    n_closes: int,
    primary_verdict: str,
    primary_confidence: float,
    primary_ms: int = 0,
) -> Optional[Dict[str, Any]]:
    """Run the decision voice for one research call. Returns the row dict
    (for the analysis snapshot + session log), or None when disabled/failed.

    Best-effort end to end, exactly like _duelist_verdict: any fault logs a
    warning and returns None — a failed observer is 'no observation', never
    a PASS."""
    try:
        cfg = _dv_config()
        if cfg is None:
            return None
        held = any(p.get("coin") == coin for p in (open_positions or []))
        state = build_state(
            coin, perception, tf1h, tf4h, tf1d, funding_raw, news,
            open_positions, win_rate, n_closes,
            max_state_chars=cfg["max_state_chars"],
            fade_shape="fields" if cfg.get("fade_fields", True) else "names",
            emit_composite_score=bool(cfg.get("composite_score", False)),
            vwap_fields=bool(cfg.get("vwap_fields", False)),
            breakout_side_label=bool(cfg.get("breakout_side_label", False)),
        )
        questions = build_questions(held, news_live=_news_live(news))
        answers, wall_ms, server_ms = call_systemone(
            cfg["base_url"], cfg["api_key"], cfg["model"], state, questions,
            timeout_s=cfg["timeout_s"],
        )
        if answers is None:
            return None
        parsed = verdict_from_answers(answers, coin, perception)
        answers = parsed.pop("_answers")

        row = {
            "coin": coin,
            "perception_id": perception.get("id", "unknown"),
            "mode": str(read_agent_config().get("mode", "OFF")),
            "dv_model": cfg["model"],
            "primary_model": _primary_model_name(),
            "primary_verdict": primary_verdict,
            "primary_confidence": primary_confidence,
            "dv_verdict": parsed["verdict"],
            "dv_confidence": parsed["confidence"],
            "dv_side": parsed["side"],
            # The two SCALARS the live gates read (2026-10-07 clef-gate scope,
            # .hermes/plans/2026-10-07-clef-gate-close-voice-scope.md Change 1).
            # These must ride the analysis whitelist + the executor entry
            # context too — a field not in the whitelist silently never
            # reaches the executor (the 2026-08 pitfall). Raw probabilities
            # stay in `dv_answers` for offline threshold pricing.
            "dv_trap": float((answers.get("trap") or {}).get("noul", 0.0)),
            "dv_close_now": (answers.get("close_now") or {}).get("noul"),
            "dv_news_risk": parsed["news_risk"],
            "dv_reasoning": parsed["reasoning"][:300],
            "dv_answers": answers,
            "held": held,
            "mid": perception.get("mid"),
            "composite_score": perception.get("composite_score"),
            "primary_ms": int(primary_ms or 0),
            "dv_ms": wall_ms,
            "dv_server_ms": server_ms,
        }
        record_dv(row)

        # Archive the typed request/response in the prompt-log under its own
        # role so the replay tooling can price it later. The "prompt" here is
        # the state+questions JSON (self-contained — no reference row).
        try:
            from hermes_trader.agents import prompt_log
            prompt_log.record_call({
                "ts": int(time.time() * 1000),
                "coin": coin,
                "role": "decision_voice",
                "model": cfg["model"],
                "perception_id": perception.get("id", "unknown"),
                "mode": row["mode"],
                "mid": perception.get("mid"),
                "composite_score": perception.get("composite_score"),
                "prior_block_injected": False,
                "system_prompt": None,
                "user_message": json.dumps({"state": state, "questions": questions}),
                "raw_response": json.dumps(answers),
                "parsed": {
                    "verdict": parsed["verdict"],
                    "confidence": parsed["confidence"],
                    "side": parsed["side"],
                    "entry_px": parsed["entry_px"],
                    "stop_px": parsed["stop_px"],
                    "tp_px": parsed["tp_px"],
                    "reasoning": parsed["reasoning"][:2000],
                    "ai_down": False,
                    "close_guard_downgraded": False,
                },
                "wall_ms": wall_ms,
                "server_ms": server_ms,
            })
        except Exception:  # noqa: BLE001 — archive is best-effort
            pass

        logger.info(
            f"[dv] {coin}: primary {primary_verdict} (conf {primary_confidence:.2f}) "
            f"vs decision-voice {cfg['model']} {parsed['verdict']} "
            f"(conf {parsed['confidence']:.2f}, {wall_ms}ms"
            + (f", {server_ms}ms srv" if server_ms is not None else "")
            + f") — {'AGREE' if parsed['verdict'] == primary_verdict else 'SPLIT'}"
        )
        return row
    except Exception as e:  # noqa: BLE001 — the primary verdict is already in hand
        logger.warning(f"[dv] decision-voice hook failed for {coin} (non-fatal): {e!r}")
        return None


def _primary_model_name() -> str:
    try:
        from hermes_trader.agents.duel_store import effective_primary_model
        return effective_primary_model()
    except Exception:  # noqa: BLE001
        return "?"
