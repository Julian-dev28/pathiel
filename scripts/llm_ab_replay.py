#!/usr/bin/env python3
"""LLM A/B shadow-replay harness (prompt-log native).

Replays the EXACT archived research prompts (trader-logs/prompt-log/) through
one or more challenger endpoints and scores verdicts against the live book.
No prompt reconstruction: the archive (live since 2026-10-04) captured the
byte-exact system+user messages, so fidelity is 100% by construction.

Read-only w.r.t. the bot: never touches config, containers, or the live
lemonade model state beyond POSTing /chat/completions.

Usage (from repo root, with the project venv):
    .venv/bin/python scripts/llm_ab_replay.py prep                 # build cohort
    .venv/bin/python scripts/llm_ab_replay.py replay --arm TAG     # run one arm
    .venv/bin/python scripts/llm_ab_replay.py score                # join + report
    .venv/bin/python scripts/llm_ab_replay.py audit --arm TAG      # masked-PASS audit

Arms live in scripts/ab_arms.json. Artifacts: scratch/_ab_{cohort,<arm>_results}.jsonl
Resumable: replay skips (perception_id, arm) keys already present; error rows
are auto-purged on the next run.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

PROMPT_LOG = REPO / "trader-logs" / "prompt-log"
LEDGER = REPO / "trader-logs" / "trades.jsonl"
ARMS_FILE = REPO / "scripts" / "ab_arms.json"
SCRATCH = REPO / "scratch"
COHORT = SCRATCH / "_ab_cohort.jsonl"

# The bot's own parser — this is the point: we measure what the bot WOULD record.
from hermes_trader.agents.research import parse_verdict  # noqa: E402


# ---------------------------------------------------------------- prep ----

def iter_archive_rows():
    """Yield parsed call rows from calls.jsonl + rotated .zst chunks (index.jsonl)."""
    import zstandard
    files = []
    idx = PROMPT_LOG / "index.jsonl"
    if idx.exists():
        for line in idx.open():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "rotated":
                p = PROMPT_LOG / rec["chunk"]
                if p.exists():
                    files.append(p)
    live = PROMPT_LOG / "calls.jsonl"
    if live.exists():
        files.append(live)
    for p in files:
        if p.suffix == ".zst" or ".zst" in p.name:
            with p.open("rb") as fh:
                dctx = zstandard.ZstdDecompressor()
                data = dctx.stream_reader(fh).read()
            for raw in data.split(b"\n"):
                line = raw.decode("utf-8", "ignore").strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        else:
            with p.open(errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue


def cmd_prep(args):
    """Cohort = every archived primary LIVE call with a rendered prompt.

    All rows are kept (PASS controls included — a PASS-heavy model must be
    caught by the score step, not filtered out here). Deduped by perception_id.
    """
    seen = set()
    n = 0
    with COHORT.open("w") as out:
        for r in iter_archive_rows():
            if r.get("role") != "primary" or r.get("mode") != "LIVE":
                continue
            pid = r.get("perception_id")
            if not pid or pid in seen:
                continue
            if not r.get("system_prompt") or not r.get("user_message"):
                continue
            seen.add(pid)
            out.write(json.dumps({
                "perception_id": pid,
                "ts": r.get("ts"),
                "coin": r.get("coin"),
                "mid": r.get("mid"),
                "composite_score": r.get("composite_score"),
                "model_incumbent": r.get("model"),
                "incumbent_parsed": r.get("parsed"),
                "incumbent_wall_ms": r.get("wall_ms"),
                "system_prompt": r["system_prompt"],
                "user_message": r["user_message"],
            }) + "\n")
            n += 1
    print(f"cohort: {n} rows -> {COHORT}")


# -------------------------------------------------------------- replay ----

def load_arms():
    return {a["tag"]: a for a in json.loads(ARMS_FILE.read_text())["arms"]}


def live_primary_sampling():
    """llm.primary sampling + chat_template_kwargs + max_tokens from the LIVE config."""
    cfg = json.loads((REPO / ".agent-config.json").read_text())
    prim = (cfg.get("llm") or {}).get("primary") or {}
    sampling = prim.get("sampling") or {"temperature": 0.1}
    return sampling, prim.get("chat_template_kwargs") or {}, prim.get("max_tokens", 8192)


def results_path(tag):
    return SCRATCH / f"_ab_{tag}_results.jsonl"


# ---- vwap shadow-episode injection (state-schema A/B, B.43 gauntlet) ----
VWAP_EPS = SCRATCH / "_vwap_eps.json"


def load_vwap_episodes():
    """Deduped [vwap-deviation][SHADOW] episodes keyed by coin (ts epoch-ms)."""
    from datetime import datetime, timezone
    by_coin = {}
    for e in json.loads(VWAP_EPS.read_text()):
        ms = int(datetime.strptime(e["ts"], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc).timestamp() * 1000)
        by_coin.setdefault(str(e["coin"]).upper(), []).append((ms, e))
    return by_coin


def vwap_join(by_coin, coin, ts_ms, window_s=900):
    """Nearest episode for this coin within +/- window_s of the decision ts."""
    best = None
    for ms, e in by_coin.get(str(coin).upper(), []):
        dt = abs(ts_ms - ms)
        if dt <= window_s * 1000 and (best is None or dt < best[0]):
            best = (dt, ms, e)
    return best


MFI_FIRE_RE = re.compile(
    r"mfiExtreme: (long|short) — MFI (\d+) (<=|>=) (\d+) (oversold|overbought)")
RSI_FIRE_RE = re.compile(
    r"rsiDivergence: (long|short) — (bullish|bearish) RSI divergence: "
    r"price (lower low|higher high) (-?[\d.]+)% beyond prior")


def fade_fired(user_message):
    return bool(MFI_FIRE_RE.search(user_message) or RSI_FIRE_RE.search(user_message))


def apply_fade_mode(state, user_message, mode):
    """Fade-family state A/B (mfiExtreme/rsiDivergence are LIVE triggers, so the
    control state already carries their NAMES in triggers_fired — side-less).
    'strip' ablates the names: does d3 use them at all?
    'fields' adds side-aware structured fields parsed from the archived fired
    reason text, full2-style semantics-bearing wording."""
    if mode == "strip":
        state["triggers_fired"] = [t for t in state.get("triggers_fired", [])
                                   if t not in ("mfiExtreme", "rsiDivergence")]
    elif mode == "fields":
        m = MFI_FIRE_RE.search(user_message)
        if m:
            side, val, op, thr, kind = m.groups()
            state["mfi_extreme"] = {
                "signal": f"mfi_{kind}",
                "trade_side": side,
                "expected_move": "price_reverts_from_volume_exhaustion",
                "mfi_value": int(val), "threshold": int(thr),
            }
        m = RSI_FIRE_RE.search(user_message)
        if m:
            side, flavour, _lbl, ext = m.groups()
            state["rsi_divergence"] = {
                "signal": f"rsi_divergence_{flavour}",
                "trade_side": side,
                "expected_move": "momentum_reversal_from_divergence",
                "price_extreme_pct": float(ext),
            }


def apply_vwap_inject(state, ep, mode):
    """Treatment arms: 'name' appends the trigger name to triggers_fired;
    'full' adds the numeric vwap_deviation state fields; 'full2' adds the same
    facts with semantics-bearing field names (the expected move is STATED, not
    left to inference — tests whether the trap misread is a wording artifact)."""
    if not ep:
        return
    if mode in ("name", "full", "full2") and "vwapDeviation" not in state.get("triggers_fired", []):
        state.setdefault("triggers_fired", []).append("vwapDeviation")
    if mode == "full":
        state["vwap_deviation"] = {
            "shape": ep["shape"], "suggested_side": ep["side"],
            "z_sigma": ep["z"], "anchor_age_bars": ep["age"],
        }
    elif mode == "full2":
        state["vwap_deviation"] = {
            "signal": f"vwap_{ep['shape']}",
            "trade_side": ep["side"],
            "expected_move": ("price_reverts_to_anchored_vwap"
                              if ep["shape"] == "fade"
                              else "trend_continues_from_vwap_retest"),
            "stretch_sigma": ep["z"],
            "anchor_age_bars": ep["age"],
        }


def purge_errors(tag):
    p = results_path(tag)
    if not p.exists():
        return set()
    keep, done = [], set()
    for line in p.open(errors="ignore"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("error"):
            continue  # failed rows must not count as done
        keep.append(line)
        done.add(r["perception_id"])
    p.write_text("".join(keep))
    return done


def cmd_replay(args):
    arms = load_arms()
    if args.arm not in arms:
        sys.exit(f"arm '{args.arm}' not in {ARMS_FILE}; tags: {sorted(arms)}")
    arm = arms[args.arm]
    base_url = arm["base_url"]
    if "REPLACE-ME" in base_url or "REPLACE-ME" in arm.get("model", ""):
        sys.exit(f"arm '{args.arm}' not configured yet — fill scripts/ab_arms.json")
    api_key = os.environ.get(arm.get("api_key_env") or "", "")
    sampling = arm.get("sampling") or None
    ctk = arm.get("chat_template_kwargs")
    max_toks = arm.get("max_tokens")
    if sampling is None or ctk is None or max_toks is None:
        s0, c0, m0 = live_primary_sampling()
        sampling = sampling or s0
        ctk = c0 if ctk is None else ctk
        max_toks = max_toks or m0

    arm_tag = args.arm
    if args.inject_vwap:
        arm_tag += f"__vw-{args.inject_vwap}"
    if args.fade_mode:
        arm_tag += f"__fade-{args.fade_mode}"
    done = purge_errors(arm_tag)
    rows = [json.loads(l) for l in COHORT.open(errors="ignore") if l.strip()]
    if args.only_vwap_episodes or args.inject_vwap:
        by_coin = load_vwap_episodes()
        rows = [r for r in rows if vwap_join(by_coin, r["coin"], r["ts"])]
    if args.fade_mode:
        rows = [r for r in rows if fade_fired(r["user_message"])]
        if args.stride > 1:
            rows = rows[:: args.stride]
    if args.limit:
        rows = [r for r in rows if r["perception_id"] not in done][: args.limit]
    else:
        rows = [r for r in rows if r["perception_id"] not in done]
    print(f"arm={arm_tag} model={arm['model']} todo={len(rows)} (already done: {len(done)})")

    # decision-model arm (Clef family): typed state+questions via /v1/systemone,
    # reusing the bot's OWN decision_voice module for state build + verdict map.
    if arm.get("kind") == "systemone":
        return _replay_systemone(args, arm, api_key, rows, done, arm_tag)

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body_tmpl = {
        "model": arm["model"],
        "stream": False,
        "max_tokens": max_toks,
        **sampling,
    }
    if ctk:
        body_tmpl["chat_template_kwargs"] = ctk

    n_ok = n_err = 0
    from concurrent.futures import ThreadPoolExecutor
    import threading
    write_lock = threading.Lock()
    conc = max(1, args.concurrency)
    if conc > 1:
        # llama-server must be running with --parallel >= conc (n_slots) or
        # requests just queue server-side; slots split ctx (16384/2 = 8192 each).
        body_tmpl = dict(body_tmpl)

    def one(r):
        body = dict(body_tmpl, messages=[
            {"role": "system", "content": r["system_prompt"]},
            {"role": "user", "content": r["user_message"]},
        ])
        rec = {"perception_id": r["perception_id"], "arm": args.arm,
               "coin": r["coin"], "ts": r["ts"], "model": arm["model"]}
        t0 = time.monotonic()
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=180.0)
            rec["wall_ms"] = int((time.monotonic() - t0) * 1000)
            if not resp.is_success:
                rec["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                msg = (resp.json().get("choices") or [{}])[0].get("message", {})
                # mirror the bot's own extraction: content, else reasoning
                text = msg.get("content") or msg.get("reasoning") or ""
                rec["raw_response"] = text
                parsed = parse_verdict(
                    text, r["coin"],
                    {"mid": r.get("mid", 0), "id": r["perception_id"]},
                    held_coins=None,  # replay semantics: keep raw CLOSE token
                )
                rec["parsed"] = {k: parsed[k] for k in
                                 ("verdict", "confidence", "side", "news_risk")}
        except Exception as exc:  # noqa: BLE001 — log and keep the run going
            rec["wall_ms"] = int((time.monotonic() - t0) * 1000)
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return rec

    with results_path(args.arm).open("a") as out, \
            ThreadPoolExecutor(max_workers=conc) as ex:
        for i, rec in enumerate(ex.map(one, rows), 1):
            if rec.get("error"):
                n_err += 1
            else:
                n_ok += 1
            with write_lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
            if i % 25 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)} ok={n_ok} err={n_err}")
    print(f"done arm={args.arm} ok={n_ok} err={n_err} -> {results_path(args.arm)}")


# ------------------------------------------------------- systemone arm ----

def _state_from_prompt(coin, user_message):
    """Parse the archived chat user_message into the decision_voice state shape.

    The prompt layout is fixed (research.py renders it), so regex extraction is
    faithful: mid, perception score, fired triggers, per-TF EMA/RSI/ATR/ADX,
    funding, news, whale flag, held position. Fields the prompt states in prose
    map onto the same keys build_state uses, so the model sees the same facts.
    """
    import re
    def _f(pat, cast=float):
        m = re.search(pat, user_message)
        return cast(m.group(1)) if m else None

    state = {
        "coin": coin,
        "mid": _f(r"Current mid: \$?([\d.eE+-]+)"),
        "composite_score": _f(r"Perception score: (\d+(?:\.\d+)?)/100"),
        "daily_move_pct": _f(r"dailyMover: ([+-]?[\d.]+)% 24h mover"),
        "funding_rate": (_f(r"Funding rate \(latest\): ([\d.eE+-]+)%/hr") or 0.0),
        "triggers_fired": [],
    }
    m = re.search(r"Fired triggers: (.*)", user_message)
    if m and "none" not in m.group(1)[:6].lower():
        for part in m.group(1).split(","):
            name = part.split(":")[0].strip()
            if name:
                state["triggers_fired"].append(name)

    for tf in ("1h", "4h", "1d"):
        m = re.search(
            rf"^{tf}: EMA8=([\d.eE+-]+), EMA21=([\d.eE+-]+), (\w+).*?"
            rf"RSI\(14\)=([\d.]+) \| ATR\(14\)=([\d.eE+-]+) \| ADX\(14\)=([\d.]+)",
            user_message, re.M)
        if m:
            e8, e21, _lbl, rsi, atr, adx = m.groups()
            state[f"tf_{tf}"] = {
                "ema8": float(e8), "ema21": float(e21),
                "ema8_above_ema21": float(e8) > float(e21),
                "rsi14": float(rsi), "atr14": float(atr), "adx14": float(adx),
            }
        else:
            state[f"tf_{tf}"] = {}
    m = re.search(r"Recent news: (.*)", user_message)
    state["news_recent"] = (m.group(1).strip() if m else "no news")[:1500]
    m = re.search(r"Whale accumulation flag: (.*)", user_message)
    if m and "not flagged" not in m.group(1):
        state["whale_signal"] = m.group(1).strip()[:200]
    held = bool(re.search(rf"Open position on {re.escape(coin)}: (?!none)",
                          user_message))
    state["held_position"] = {"side": None, "size_usd": None} if held else None
    return state, held


def _replay_systemone(args, arm, api_key, rows, done, arm_tag):
    """Decision-model arm: state+questions -> POST /v1/systemone -> the bot's
    own verdict_from_answers mapping. No generation, no parse_verdict — the
    masked-verdict class cannot occur, so the audit step is a no-op for this arm."""
    from hermes_trader.agents.decision_voice import (
        build_questions, call_systemone, verdict_from_answers)
    url_root = arm["base_url"]
    model = arm["model"]
    timeout_s = float(arm.get("timeout_s", 30.0))
    conc = max(1, args.concurrency)
    inject = args.inject_vwap
    fade = args.fade_mode
    vwap_by_coin = load_vwap_episodes() if inject or args.only_vwap_episodes else None
    n_ok = n_err = 0
    from concurrent.futures import ThreadPoolExecutor
    import threading
    write_lock = threading.Lock()

    def one(r):
        rec = {"perception_id": r["perception_id"], "arm": arm_tag,
               "coin": r["coin"], "ts": r["ts"], "model": model}
        state, held = _state_from_prompt(r["coin"], r["user_message"])
        if fade:
            apply_fade_mode(state, r["user_message"], fade)
            mm = MFI_FIRE_RE.search(r["user_message"])
            rm = RSI_FIRE_RE.search(r["user_message"])
            if mm:
                rec["mfi"] = {"side": mm.group(1), "value": int(mm.group(2)),
                              "kind": mm.group(5)}
            if rm:
                rec["rsi"] = {"side": rm.group(1), "flavour": rm.group(2),
                              "ext_pct": float(rm.group(4))}
        j = vwap_join(vwap_by_coin, r["coin"], r["ts"]) if vwap_by_coin else None
        if j:
            rec["vwap"] = {"shape": j[2]["shape"], "side": j[2]["side"],
                           "z": j[2]["z"], "age": j[2]["age"],
                           "dt_s": int(j[0] / 1000)}
            if inject:
                apply_vwap_inject(state, j[2], inject)
        questions = build_questions(held)
        answers, wall_ms, server_ms = call_systemone(
            url_root, api_key, model, state, questions, timeout_s=timeout_s)
        rec["wall_ms"] = wall_ms
        rec["server_ms"] = server_ms
        if answers is None:
            rec["error"] = "systemone call failed (see harness stderr)"
            return rec
        parsed = verdict_from_answers(answers, r["coin"],
                                      {"mid": state.get("mid") or 0})
        answers = parsed.pop("_answers")
        rec["dv_answers"] = answers
        rec["parsed"] = {k: parsed[k] for k in
                         ("verdict", "confidence", "side", "news_risk")}
        return rec

    with results_path(arm_tag).open("a") as out, \
            ThreadPoolExecutor(max_workers=conc) as ex:
        for i, rec in enumerate(ex.map(one, rows), 1):
            if rec.get("error"):
                n_err += 1
            else:
                n_ok += 1
            with write_lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
            if i % 100 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)} ok={n_ok} err={n_err}")
    print(f"done arm={arm_tag} ok={n_ok} err={n_err} -> {results_path(arm_tag)}")


# --------------------------------------------------------------- audit ----

JSON_LINE_RE = re.compile(r'^\s*\{.*"verdict".*\}\s*$')


def cmd_audit(args):
    """Mandatory masked-PASS audit (skill pitfall 1): parse_verdict records a
    model that never emitted a final JSON line as PASS conf 0.0 — which can
    mask a bullish prose verdict. Classify every low-conf row.
    No-op for systemone (decision-model) arms: no generation, the class
    cannot occur by construction."""
    p = results_path(args.arm)
    if not p.exists():
        sys.exit(f"no results for arm '{args.arm}' — run replay first")
    arms = load_arms()
    if args.arm in arms and arms[args.arm].get("kind") == "systemone":
        print(f"arm={args.arm}: decision-model arm — masked-verdict class "
              "impossible (typed answers, no generation). Audit skipped.")
        return
    masked = clean = 0
    masked_rows = []
    for line in p.open(errors="ignore"):
        r = json.loads(line)
        if r.get("error"):
            continue
        parsed = r.get("parsed") or {}
        tail = (r.get("raw_response") or "")[-1500:]
        has_json = any(JSON_LINE_RE.match(l) for l in tail.split("\n"))
        if not has_json:
            masked += 1
            prose = tail.upper()
            bias = "LONG" if re.search(r'\b(LONG|BUY)\b', prose) and not re.search(r'\b(PASS|STAND ASIDE|NO TRADE)\b', prose) else "stand-aside"
            masked_rows.append((r["coin"], parsed.get("verdict"), parsed.get("confidence"), bias))
        else:
            clean += 1
    print(f"arm={args.arm}: clean={clean} masked(no final JSON line)={masked}")
    try:
        for c, v, cf, bias in masked_rows[:40]:
            print(f"  {c:10} recorded {v} conf {cf}  prose-bias: {bias}")
        if masked:
            print("VERDICT: masked rows inflate apparent conservatism — treat this arm's "
                  "PASS rate as unproven until prose bias is reviewed.")
    except BrokenPipeError:
        pass  # output piped to head — not an error


# --------------------------------------------------------------- score ----

def load_ledger():
    opens, closes = {}, []
    for line in LEDGER.open(errors="ignore"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event") == "OPEN":
            opens.setdefault(r["coin"], []).append(r)
        elif r.get("event") == "CLOSE":
            closes.append(r)
    for lst in opens.values():
        lst.sort(key=lambda r: r["ts"])
    # FIFO close per coin keyed by open ts
    close_by_open = {}
    stack = {}
    for c in sorted(closes, key=lambda r: r["ts"]):
        coin = c["coin"]
        st = stack.setdefault(coin, [])
        if opens.get(coin):
            o = opens[coin][0]
            close_by_open[o["ts"]] = c
    return opens, closes, close_by_open


def cmd_score(args):
    cohort = {json.loads(l)["perception_id"]: json.loads(l)
              for l in COHORT.open(errors="ignore") if l.strip()}
    arms = load_arms()
    opens, closes, close_by_open = load_ledger()

    # realized P/L join: cohort row -> nearest OPEN for coin within +90s of prompt ts
    def realized(pid_row):
        ts = pid_row["ts"]
        cands = [o for o in opens.get(pid_row["coin"], [])
                 if 0 <= o["ts"] - ts <= 90_000]
        if not cands:
            return None
        o = cands[0]
        c = close_by_open.get(o["ts"])
        if not c:
            return {"open": o, "close": None}
        return {"open": o, "close": c}

    for tag in (args.arm.split(",") if args.arm else sorted(arms)):
        p = results_path(tag)
        if not p.exists():
            continue
        rows = [json.loads(l) for l in p.open(errors="ignore") if l.strip() and not json.loads(l).get("error")]
        if not rows:
            continue
        inc_dir = chal_dir = agree = split = 0
        lat = []
        matrix = {}
        split_pl = {"chal_right": 0.0, "inc_right": 0.0, "unpriced": 0}
        chal_dir_unpriced = 0
        for r in rows:
            c = cohort.get(r["perception_id"]) or {}
            ip = c.get("incumbent_parsed") or {}
            cp = r.get("parsed") or {}
            iv, cv = ip.get("verdict"), cp.get("verdict")
            matrix[(iv, cv)] = matrix.get((iv, cv), 0) + 1
            lat.append(r.get("wall_ms", 0))
            idir, cdir = iv in ("LONG", "SHORT", "VETO"), cv in ("LONG", "SHORT", "VETO")
            if idir:
                inc_dir += 1
            if cdir:
                chal_dir += 1
            if iv == cv:
                agree += 1
            elif idir != cdir:  # one directional, other abstaining
                split += 1
                if cdir:
                    # incumbent abstained -> challenger-only entry, priced by
                    # the counterfactual subcommand (no ledger join possible)
                    chal_dir_unpriced += 1
                else:
                    j = realized(c) if c else None
                    if j and j.get("close"):
                        split_pl["inc_right"] += j["close"].get("realized_pnl_usd", 0.0)
        lat.sort()
        med = lat[len(lat) // 2] if lat else 0
        p90 = lat[int(len(lat) * 0.9)] if lat else 0
        print(f"\n=== arm {tag} (n={len(rows)}) ===")
        print(f"latency med={med}ms p90={p90}ms  (live scan cycle budget ~40s; "
              f"incumbent archived med={sorted((c.get('incumbent_wall_ms') or 0) for c in cohort.values())[len(cohort)//2]}ms)")
        print(f"agreement with incumbent: {agree}/{len(rows)} ({agree/len(rows)*100:.0f}%)  "
              f"abstain-vs-direct splits: {split}")
        print(f"directional verdicts: incumbent {inc_dir}, challenger {chal_dir} "
              f"(challenger-direct on rows incumbent skipped: {chal_dir_unpriced} — unpriced counterfactuals)")
        print(f"incumbent-traded rows challenger would have skipped: avoided P/L "
              f"{split_pl['inc_right']:+.2f} (negative avoided = challenger vetoes were RIGHT)")
        print("verdict matrix (incumbent -> challenger):")
        for (iv, cv), n in sorted(matrix.items(), key=lambda kv: -kv[1]):
            print(f"  {iv:>7} -> {cv:<7} {n}")


# --------------------------------------------------------- counterfactual ----

def cmd_counterfactual(args):
    """Stage 2: price the challenger's directional verdicts the incumbent skipped.

    For each challenger LONG/SHORT on a row where the incumbent abstained:
      1. deterministic gate stack replay (confidence bars, composite bar,
         per-coin cooldowns, no-pyramid, slot cap vs REAL book + sim book),
      2. fill at the archived mid,
      3. mirror the live DSL exit policy on cached 5m closes
         (max_loss, floor ratchet + phase2 tiers + breakeven lock, stale_flat).
    P/L on the SPOT basis: notional x spot%/100, no leverage multiplier.
    Disclosed optimistic bias: mid-fill (no slippage), 5m-close mirror
    (understates wick-whipsaw floor exits), gates limited to the
    deterministic subset replayable from config (forecast/tail gates need
    scan-time forecaster state we do not have).
    """
    import datetime as dtm
    cfg = json.loads((REPO / ".agent-config.json").read_text())
    dsl = cfg.get("dsl_exit", {})
    MAX_LOSS = float(dsl.get("max_loss_pct", 5.0))
    PROTECT = float(dsl.get("protect_pct", 1.0))
    RETRACE = float(dsl.get("retrace_threshold", 0.25))
    BREACH_N = int(dsl.get("consecutive_breaches_required", 1))
    STALE_MS = float(dsl.get("stale_flat_timeout_minutes", 240)) * 60_000
    HARD_MS = float(dsl.get("hard_timeout_minutes", 1800)) * 60_000
    BE_TRIG = float(dsl.get("breakeven_trigger_pct", 0)) or None
    BE_LOCK = float(dsl.get("breakeven_lock_pct", 0.0))
    TIERS = [(float(t["pct_above_entry"]), float(t["retrace_threshold"]))
             for t in dsl.get("phase2_tiers", [])]
    reg = dsl.get("regime_aware", {})
    NOTIONAL = float(cfg.get("max_trade_notional_usd") or 33.33)
    SLOTS = int(cfg.get("max_concurrent", 5))
    COOLDOWN = float(cfg.get("cooldown_min", 60)) * 60_000
    LOSS_COOLDOWN = float(cfg.get("loss_cooldown_min", 180)) * 60_000
    rg = cfg.get("runner_entry_gate", {})
    MIN_CONF = float(rg.get("min_confidence", cfg.get("min_ai_confidence", 0.7)))
    MIN_CONF_SHORT = float(rg.get("min_short_confidence", MIN_CONF))
    MIN_COMP = float(rg.get("min_composite", 30.0))
    FEE_RT = 0.017  # $ per round trip at ~$33 notional (HL taker), b19 convention
    STORE = REPO / "scratch" / "candlestore"

    cohort = {json.loads(l)["perception_id"]: json.loads(l)
              for l in COHORT.open(errors="ignore") if l.strip()}

    # real book occupancy walk (OPEN/CLOSE rows; unpaired OPENs capped at hard timeout)
    raw = []
    for line in LEDGER.open(errors="ignore"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("event") in ("OPEN", "CLOSE"):
            raw.append((e.get("ts", 0), e))
    raw.sort(key=lambda x: x[0])
    stacks, REAL = {}, []
    for t_ev, e in raw:
        key = (e.get("coin"), e.get("side"))
        if e.get("event") == "OPEN":
            stacks.setdefault(key, []).append([e.get("coin"), e.get("ts"), None])
            REAL.append(stacks[key][-1])
        else:
            cand = [o for o in stacks.get(key, []) if o[2] is None and o[1] <= t_ev]
            if cand:
                max(cand, key=lambda o: o[1])[2] = t_ev

    def _end(o):
        return o[2] if o[2] is not None else min(o[1] + HARD_MS, 10**16)

    def real_holding(coin, t):
        return any(x[0] == coin and x[1] <= t < _end(x) for x in REAL)

    def real_open_count(t):
        return sum(1 for x in REAL if x[1] <= t < _end(x))

    def last_close(coin, t):
        ev = [(o[1].get("ts"), o[1].get("realized_pnl_usd") or 0)
              for o in raw if o[1].get("event") == "CLOSE"
              and o[1].get("coin") == coin and o[1].get("ts", 0) <= t]
        return ev[-1] if ev else None

    def load_candles(coin, t0, t1):
        d = STORE / coin / "5m"
        if not d.is_dir():
            return None
        out = []
        for fn in d.iterdir():
            try:
                bars = json.loads(fn.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            out += [b for b in bars if t0 <= b["t"] <= t1]
        out.sort(key=lambda b: b["t"])
        return out or None

    def sim_exit(coin, entry_ms, side, entry_px):
        bars = load_candles(coin, entry_ms, entry_ms + STALE_MS + 600_000)
        if not bars:
            return None, None
        sgn = 1 if side == "long" else -1
        peak, floor, breaches = 0.0, None, 0
        for b in bars:
            if b["t"] < entry_ms:
                continue
            px = float(b["c"])
            spot = (px / entry_px - 1) * 100 * sgn
            if spot <= -MAX_LOSS:
                return -MAX_LOSS, "max_loss"
            peak = max(peak, spot)
            retrace = RETRACE
            for tp, tr in TIERS:
                if peak >= tp:
                    retrace = max(retrace, tr)
            if peak >= PROTECT:
                fl = peak * (1 - retrace)
                if BE_TRIG is not None and peak >= BE_TRIG:
                    fl = max(fl, BE_LOCK)
                floor = max(floor, fl) if floor is not None else fl
                if spot <= floor:
                    breaches += 1
                    if breaches >= BREACH_N:
                        return spot, "floor_breach"
                else:
                    breaches = 0
            if b["t"] - entry_ms >= STALE_MS and peak < PROTECT:
                return spot, "stale_flat"
        return None, "no_exit_window"

    arms = load_arms()
    tags = args.arm.split(",") if args.arm else sorted(
        t for t in arms if results_path(t).exists())
    for tag in tags:
        p = results_path(tag)
        if not p.exists():
            continue
        rows = [json.loads(l) for l in p.open(errors="ignore")
                if l.strip() and not json.loads(l).get("error")]
        # candidate entries: challenger directional, incumbent NOT directional
        cands = []
        for r in rows:
            c = cohort.get(r["perception_id"])
            if not c:
                continue
            cp, ip = r.get("parsed") or {}, c.get("incumbent_parsed") or {}
            if cp.get("verdict") not in ("LONG", "SHORT"):
                continue
            if ip.get("verdict") in ("LONG", "SHORT", "CLOSE"):
                continue  # incumbent acted; avoided-P/L handled in score
            cands.append((c["ts"], c["coin"], cp, c))
        cands.sort(key=lambda e: (e[1], e[0]))
        ded = []
        for e in cands:
            if ded and ded[-1][1] == e[1] and e[0] - ded[-1][0] < 3600_000:
                continue
            ded.append(e)

        sim_book = []

        def sim_holding(coin, t):
            return any(cc == coin and s <= t < en for cc, s, en in sim_book)

        def sim_open_count(t):
            return sum(1 for _, s, en in sim_book if s <= t < en)

        results, skips = [], {}
        for t, coin, cp, c in sorted(ded, key=lambda e: e[0]):
            conf = cp.get("confidence") or 0.0
            comp = c.get("composite_score") or 0.0
            side = cp.get("verdict").lower()
            bar = MIN_CONF_SHORT if side == "short" else MIN_CONF
            if conf < bar:
                skips["conf_bar"] = skips.get("conf_bar", 0) + 1
                continue
            if comp < MIN_COMP:
                skips["composite_bar"] = skips.get("composite_bar", 0) + 1
                continue
            if real_holding(coin, t) or sim_holding(coin, t):
                skips["pyramid"] = skips.get("pyramid", 0) + 1
                continue
            if real_open_count(t) + sim_open_count(t) >= SLOTS:
                skips["slot_cap"] = skips.get("slot_cap", 0) + 1
                continue
            lc = last_close(coin, t)
            if lc:
                if t - lc[0] < COOLDOWN:
                    skips["cooldown"] = skips.get("cooldown", 0) + 1
                    continue
                if lc[1] < 0 and t - lc[0] < LOSS_COOLDOWN:
                    skips["loss_cooldown"] = skips.get("loss_cooldown", 0) + 1
                    continue
            entry_px = c.get("mid") or 0
            if not entry_px:
                skips["no_mid"] = skips.get("no_mid", 0) + 1
                continue
            spot, reason = sim_exit(coin, t, side, entry_px)
            if spot is None:
                skips["no_candles"] = skips.get("no_candles", 0) + 1
                continue
            pnl = NOTIONAL * spot / 100 - FEE_RT
            hold_end = t + (STALE_MS if reason == "stale_flat" else 3600_000)
            sim_book.append((coin, t, hold_end))
            results.append((t, coin, side, conf, round(comp, 1),
                            round(spot, 2), round(pnl, 2), reason))

        out = SCRATCH / f"_ab_{tag}_counterfactual.jsonl"
        with out.open("w") as fh:
            for t, coin, side, conf, comp, spot, pnl, reason in results:
                fh.write(json.dumps({
                    "ts": t, "coin": coin, "side": side, "conf": conf,
                    "composite": comp, "spot_pct": spot, "pnl_usd": pnl,
                    "exit": reason}) + "\n")
        net = sum(r[6] for r in results)
        wins = sum(1 for r in results if r[6] > 0)
        print(f"\n=== counterfactual arm {tag} ===")
        print(f"challenger-only entries that cleared gates: n={len(results)} "
              f"net=${net:+.2f} wins {wins}/{len(results)}"
              + (f"  ex-best ${net - max((r[6] for r in results), default=0):+.2f}"
                 if results else ""))
        print(f"skips: {skips}")
        print(f"-> {out}")
        print("BIAS: mid-fill + 5m-close mirror = optimistic upper bound; "
              "forecast/tail gates not simulated (no scan-time forecaster state).")


# ---------------------------------------------------------------- cli ----

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prep")
    rp = sub.add_parser("replay")
    rp.add_argument("--arm", required=True)
    rp.add_argument("--limit", type=int, default=0)
    rp.add_argument("--concurrency", type=int, default=1,
                    help="parallel in-flight calls (remote llama-server must "
                         "run with --parallel >= this; slots split ctx)")
    rp.add_argument("--inject-vwap", choices=("none", "name", "full", "full2"), default=None,
                    help="systemone arms only: state-schema A/B against the "
                         "vwap shadow episodes (scratch/_vwap_eps.json). "
                         "none=control (join recorded, state untouched), "
                         "name=append vwapDeviation to triggers_fired, "
                         "full=+ numeric vwap_deviation fields. Results go to "
                         "_ab_<arm>__vw-<mode>_results.jsonl")
    rp.add_argument("--only-vwap-episodes", action="store_true",
                    help="restrict the cohort to archived calls within "
                         "+/-15min of a vwap shadow episode")
    rp.add_argument("--fade-mode", choices=("none", "strip", "fields"), default=None,
                    help="systemone arms only: fade-family state A/B on rows where "
                         "mfiExtreme/rsiDivergence fired. none=control (names left "
                         "in triggers_fired as today), strip=ablate the names, "
                         "fields=+ side-aware mfi_extreme/rsi_divergence fields. "
                         "Results go to _ab_<arm>__fade-<mode>_results.jsonl")
    rp.add_argument("--stride", type=int, default=1,
                    help="keep every Nth row after cohort filters (sampling)")
    au = sub.add_parser("audit")
    au.add_argument("--arm", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--arm", default="", help="comma list or empty=all arms")
    cf = sub.add_parser("counterfactual")
    cf.add_argument("--arm", default="", help="comma list or empty=all arms with results")
    args = ap.parse_args()
    {"prep": cmd_prep, "replay": cmd_replay, "audit": cmd_audit,
     "score": cmd_score, "counterfactual": cmd_counterfactual}[args.cmd](args)


if __name__ == "__main__":
    main()
