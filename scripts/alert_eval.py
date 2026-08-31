#!/usr/bin/env python3
"""Evaluate k8s/prometheusrule.yaml locally and actually deliver the alerts.

Why this exists
---------------
The rules were written for the Prometheus Operator. Nothing here runs
Kubernetes, no Prometheus is scraping, no Alertmanager is routing. So all seven
alerts — every one of them written for a silent failure this system has really
had — were documentation. A trading system whose failure modes are all silent,
whose alerting is a YAML file nobody reads, finds out it is broken by someone
happening to look. That is the failure the rules were supposed to prevent.

This reads the SAME YAML, so there is one definition of "what is wrong". If
this file and the operator ever disagreed, the alerts you get would not be the
alerts you reviewed.

The expression subset
---------------------
Deliberately tiny: `metric OP number`, joined by `and`. That is every
expression the rules actually use. It is NOT a PromQL implementation and must
never grow into one — a half-built PromQL evaluator that silently mis-parses is
strictly worse than no evaluator, because it reports "no alerts firing".

So an expression this cannot parse is an ERROR, reported loudly and exiting
non-zero. A rule that cannot be evaluated is never quietly treated as "not
firing" — that is the exact bug class every one of these alerts exists to catch.

Delivery
--------
macOS notification (osascript, no dependency), a line in logs/alerts.log, and
.state/alerts.json for the dashboard. Notification happens on TRANSITION only —
firing once, resolved once. Re-notifying every tick is how alerts get muted, and
a muted alert is the same as no alert.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "k8s", "prometheusrule.yaml")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# One definition of where state lives — see scripts/_state_env.py
# for the reason it is not inlined here.
import _state_env
_state_env.load_env_local(ROOT)
STATE_DIR = _state_env.state_dir(ROOT)
STATE = os.path.join(STATE_DIR, "alerts.json")
ALERT_LOG = os.path.join(ROOT, "logs", "alerts.log")
METRICS_URL = os.environ.get("HERMES_METRICS_URL", "http://localhost:8000/metrics")

_OPS = {
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}
# `hermes_foo > 900`, `hermes_foo == 0`, `hermes_disk_free_bytes < 2e9`
_TERM = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*$")

_DUR = re.compile(r"^(\d+)([smhd])$")
_DUR_MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class UnsupportedExpr(ValueError):
    """The evaluator cannot parse this rule. Loud on purpose — see module doc."""


def parse_duration(text: str) -> float:
    m = _DUR.match((text or "").strip())
    if not m:
        raise UnsupportedExpr(f"cannot parse duration {text!r}")
    return int(m.group(1)) * _DUR_MULT[m.group(2)]


def metrics_referenced(expr: str) -> List[str]:
    return [_TERM.match(t).group(1) for t in expr.split(" and ")
            if _TERM.match(t)]


def evaluate(expr: str, samples: Dict[str, float]) -> bool:
    """True when the alert condition holds. Raises on anything unparseable or
    on a metric that is not being exported."""
    terms = [t for t in expr.split(" and ")]
    if not terms:
        raise UnsupportedExpr(f"empty expression {expr!r}")
    for term in terms:
        m = _TERM.match(term)
        if not m:
            raise UnsupportedExpr(
                f"unsupported expression {term.strip()!r} — this evaluator "
                f"handles `metric OP number` joined by `and`, nothing more")
        name, op, num = m.group(1), m.group(2), float(m.group(3))
        if name not in samples:
            raise UnsupportedExpr(
                f"{name} is not exported by {METRICS_URL} — an alert on a "
                f"metric nobody emits can never fire")
        if not _OPS[op](samples[name], num):
            return False
    return True


def scrape(url: str = METRICS_URL, timeout: float = 10.0) -> Dict[str, float]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return parse_metrics(body)


def parse_metrics(body: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or "{" in parts[0]:
            continue
        try:
            out[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return out


def load_rules(path: str = RULES) -> List[Dict[str, Any]]:
    import yaml
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    return [r for g in doc["spec"]["groups"] for r in g["rules"]]


def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_state(payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, STATE)


def transition(name: str, holds: bool, for_s: float, pending_since: Optional[float],
               was_firing: bool, now: float) -> Tuple[str, Optional[float]]:
    """Pure: Prometheus `for:` semantics for one rule.

    Returns (state, new_pending_since) where state is one of
    pending / firing / started_firing / resolved / inactive.
    Split from delivery so the timing is testable without clocks or processes.
    """
    if not holds:
        return ("resolved" if was_firing else "inactive"), None
    since = pending_since if pending_since is not None else now
    if was_firing:
        return "firing", since
    if now - since >= for_s:
        return "started_firing", since
    return "pending", since


# ── annotation templates ────────────────────────────────────────────────────
# The rules are written for Prometheus, whose annotations are Go templates.
# Prometheus substitutes them; nothing did here, so the first alert this
# evaluator ever delivered read "Max drawdown {{ $value }}% over the risk
# window" — delivered, logged, notified, and useless. An alert a human cannot
# read is not much better than one that never fires.
#
# The subset below is exactly what the rules use. An unrecognised template is
# LEFT INTACT and reported, never silently blanked: a summary quietly missing
# its number reads as a complete sentence and hides the omission.

_DUR_UNITS = ((86400, "d"), (3600, "h"), (60, "m"), (1, "s"))


def humanize_duration(seconds: float) -> str:
    """Prometheus' humanizeDuration, close enough for a notification."""
    seconds = abs(float(seconds))
    if seconds < 1:
        return f"{seconds:.3g}s"
    parts, left = [], int(seconds)
    for size, suffix in _DUR_UNITS:
        if left >= size:
            parts.append(f"{left // size}{suffix}")
            left %= size
        if len(parts) == 2:
            break
    return " ".join(parts) or "0s"


def humanize_percentage(value: float) -> str:
    return f"{float(value) * 100:.4g}%"


def render(text: str, expr: str, samples: Dict[str, float]) -> Tuple[str, bool]:
    """Substitute the annotation templates. Returns (text, fully_rendered)."""
    import re

    metrics = metrics_referenced(expr)
    value = samples.get(metrics[0]) if metrics else None

    def _fmt(v: float, pipe: str) -> str:
        if "humanizeDuration" in pipe:
            return humanize_duration(v)
        if "humanizePercentage" in pipe:
            return humanize_percentage(v)
        return f"{v:.4g}"

    # {{ with query "metric" }}{{ . | first | value | fn }}{{ end }}
    def _with_query(m):
        name, inner = m.group(1), m.group(2)
        if name not in samples:
            return m.group(0)
        return _fmt(samples[name], inner)

    text = re.sub(r'\{\{\s*with query "([a-zA-Z_][a-zA-Z0-9_]*)"\s*\}\}'
                  r'(.*?)\{\{\s*end\s*\}\}', _with_query, text, flags=re.S)

    if value is not None:
        text = re.sub(r"\{\{\s*\$value\s*(\|[^}]*)?\}\}",
                      lambda m: _fmt(value, m.group(1) or ""), text)

    text = " ".join(text.split())
    return text, "{{" not in text


def notify(title: str, message: str) -> None:
    """Best effort, never fatal. A failed notification must not stop the other
    alerts from being delivered."""
    try:
        safe_t = title.replace('"', "'")[:200]
        safe_m = message.replace('"', "'")[:400]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}"'],
            capture_output=True, timeout=15)
    except Exception:
        pass


def _log(line: str) -> None:
    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
    with open(ALERT_LOG, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    quiet = "--quiet" in argv
    now = time.time()

    try:
        samples = scrape()
    except Exception as exc:
        # The server being unreachable is itself an alert-worthy condition, and
        # the one case where reporting "nothing firing" would be a lie.
        msg = f"cannot scrape {METRICS_URL}: {str(exc)[:120]}"
        _log(f"ERROR {msg}")
        notify("hermes: metrics unreachable", msg)
        print(f"[alerts] ERROR {msg}", file=sys.stderr)
        return 2

    state = _read_state()
    prev: Dict[str, Any] = state.get("alerts") or {}
    now_state: Dict[str, Any] = {}
    firing, errors, lines = [], [], []

    for rule in load_rules():
        name = rule["alert"]
        p = prev.get(name) or {}
        try:
            holds = evaluate(rule["expr"], samples)
            for_s = parse_duration(rule.get("for", "0s"))
        except UnsupportedExpr as exc:
            errors.append(f"{name}: {exc}")
            now_state[name] = {"error": str(exc)}
            continue

        st, since = transition(name, holds, for_s, p.get("pending_since"),
                               bool(p.get("firing")), now)
        entry: Dict[str, Any] = {"firing": st in ("firing", "started_firing"),
                                 "pending_since": since,
                                 "severity": (rule.get("labels") or {}).get("severity", "warning")}
        raw_summary = (rule.get("annotations") or {}).get("summary", name)
        summary, rendered = render(raw_summary, rule["expr"], samples)
        if not rendered:
            errors.append(f"{name}: summary has a template this evaluator "
                          f"cannot render: {summary}")
        if st == "started_firing":
            entry["since"] = now
            notify(f"hermes {entry['severity']}: {name}", summary)
            _log(f"FIRING [{entry['severity']}] {name} — {summary}")
            lines.append(f"FIRING  {name}: {summary}")
        elif st == "firing":
            entry["since"] = p.get("since", now)
            lines.append(f"firing  {name}: {summary}")
        elif st == "resolved":
            notify(f"hermes resolved: {name}", summary)
            _log(f"RESOLVED {name}")
            lines.append(f"resolved {name}")
        elif st == "pending":
            waited = now - (since or now)
            lines.append(f"pending {name} ({waited:.0f}s of {for_s:.0f}s)")
        if entry["firing"]:
            firing.append(name)
        now_state[name] = entry

    _write_state({"ts": now, "firing": firing, "errors": errors,
                  "alerts": now_state})

    if not quiet:
        for line in lines or ["no alerts firing"]:
            print(f"[alerts] {line}")
    for e in errors:
        print(f"[alerts] ERROR {e}", file=sys.stderr)
        notify("hermes: alert rule cannot be evaluated", e)
    return 2 if errors else (1 if firing else 0)


if __name__ == "__main__":
    raise SystemExit(main())
