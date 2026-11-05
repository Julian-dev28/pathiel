#!/usr/bin/env python3
"""The deterministic half of the 12-hour event scan: dated items the public
APIs can see for the next 21 days, written into the scan overlay through
scripts/event_add.py. No LLM.

Operator, 2026-11-05: "I need you to do some kind of daily or a scan every 12
hours to get new events and wire them into our trading decision", after the
desk missed the midterms (Brent -9% on 11-04) and with a Senate Foreign
Relations hearing (Nov 10) and the Iran-oil sanctions bills (H.R. 1422,
S. 556) on the schedule.

Sources, each best effort (one failing source is reported in the summary,
never fatal):
  a. congress.gov committee meetings (api.congress.gov v3,
     /committee-meeting/{congress}/{chamber}?fromDateTime&toDateTime filters
     on updateDate, so: list the meetings updated in the last 14 days, then
     read each one's detail for its date, title, committees and bills). Kept
     when the committee is one of ALWAYS_KEEP or the title/committee matches
     KEYWORDS. Impact 1 for a hearing, 2 for a markup/business meeting on a
     matching bill. Canceled meetings drop their row.
  b. WATCHED_BILLS' actions (/bill/{congress}/{type}/{number}/actions). An
     action not seen by the last scan (state in the overlay's "state" block)
     becomes a row dated the action date: impact 2, 3 for a floor passage or
     a signature into law. A date named in the action text ("...on Monday,
     November 10, 2026") becomes a future "floor" row. Bills on a kept
     markup's agenda join the watched set.
  c. Federal Register documents (policy.fetch_federal_register_docs) with an
     effective date or a comment deadline inside the window.
Without CONGRESS_API_KEY, (a) and (b) are skipped and the summary says so.

    python3 scripts/event_scan.py            # scan and write the overlay
    python3 scripts/event_scan.py --dry-run  # print what it would add, write nothing

Summary: `EVENT-SCAN <utc> added=N updated=M removed=R sources_ok=... failed=... skipped=...`,
printed and appended to /tmp/oil-loop/progress.log (not in --dry-run).
The research half (peace talks, summits, OPEC+, elections abroad) is the
event-calendar skill's "Scan" section; it writes through event_add.py too.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import event_add  # noqa: E402  (scripts/event_add.py, same directory)
from services.intel import http_client  # noqa: E402
from services.intel import scheduled_events as S  # noqa: E402
from services.intel.sources import policy  # noqa: E402

CONGRESS_API = "https://api.congress.gov/v3"
DAYS = 21
MEETING_UPDATE_LOOKBACK_DAYS = 14
MAX_DETAILS_PER_CHAMBER = 120
LIST_LIMIT = 250
MAX_ERRORS_PER_SOURCE = 3
RECENT_ACTION_DAYS = 7          # on first sight of a bill, only actions this recent become rows
MAX_WATCHED_BILLS = 20
MAX_LOG_LINE = 400

KEYWORDS = ("iran", "hormuz", "oil", "petroleum", "crude", "energy", "sanction", "opec", "war powers",
            "defense authorization", "ndaa", "appropriations", "israel", "saudi", "russia",
            "strategic petroleum")
ALWAYS_KEEP = ("foreign relations", "energy and natural resources", "foreign affairs", "energy and commerce",
               "armed services", "appropriations")
# "119/hr/1422": congress/type/number as the API spells it. Labels are what
# the row says when the bill title has not been fetched yet.
WATCHED_BILLS: Dict[str, str] = {"119/hr/1422": "H.R. 1422 (Iran oil sanctions)",
                                 "119/s/556": "S. 556 (Iran oil sanctions)"}
# The oil desk's policy_topics (services/strategies/desks/oil.py) plus "crude oil".
FR_TERMS = ("Iran sanctions", "Strategic Petroleum Reserve", "Russia oil price cap", "Venezuela sanctions",
            "US shale permitting", "crude oil")
SKIP_ACTION_TYPES = frozenset({"IntroReferral", "NotUsed"})

_KW_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")", re.I)
_KEEP_RE = re.compile("|".join(re.escape(k) for k in ALWAYS_KEEP), re.I)
_BOTH_COINS_RE = re.compile(r"\b(iran|hormuz|war powers|sanction|israel|russia|armed services|defense)", re.I)
_MARKUP_RE = re.compile(r"\b(markup|mark-up|business meeting|executive business|executive session|to consider)\b", re.I)
_PASSAGE_RE = re.compile(r"\b(passed(/agreed to)? (the )?(house|senate)|passed/agreed to|became public law|"
                         r"signed by (the )?president|became law|cleared for the white house)\b", re.I)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_DATE_IN_TEXT_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:,\s*(\d{4}))?", re.I)
_BILL_ID_RE = re.compile(r"\d+/[a-z]+/\d+")
_UNSET = object()

Fetch = Callable[..., Tuple[Optional[Any], Optional[str]]]


# ── pure helpers ────────────────────────────────────────────────────────────

def congress_number(d: date) -> int:
    """The Congress sitting on `d`: a new one starts every odd year on Jan 3
    (the 119th on 2025-01-03, the 120th on 2027-01-03)."""
    y = d.year if (d.month, d.day) >= (1, 3) else d.year - 1
    return (y - 1789) // 2 + 1


def _day(s: Optional[str]) -> Optional[date]:
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _at_utc(s: Optional[str]) -> Optional[str]:
    """'2026-11-10T15:00:00Z' -> '2026-11-10T15:00Z' (the API gives UTC: the
    Nov 10 SFRC 10:00 ET hearing reads 15:00:00Z); a bare date or midnight -> None."""
    if not isinstance(s, str) or len(s) < 16 or s[10] != "T":
        return None
    try:
        dt = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return None if (dt.hour, dt.minute) == (0, 0) else dt.strftime("%Y-%m-%dT%H:%MZ")


def _coins_for(text: str) -> List[str]:
    return ["oil", "silver"] if _BOTH_COINS_RE.search(text or "") else ["oil"]


def action_key(a: Dict[str, Any]) -> str:
    text = (a.get("text") or "").strip()
    return f"{a.get('actionDate') or ''}|{hashlib.sha1(text.encode()).hexdigest()[:10]}"


def date_in_text(text: str, today: date, end: date) -> Optional[date]:
    """The first 'Month D[, YYYY]' in `text` that lands in (today, end]."""
    for m in _DATE_IN_TEXT_RE.finditer(text or ""):
        month = _MONTHS.index(m.group(1).lower()) + 1
        day = int(m.group(2))
        years = [int(m.group(3))] if m.group(3) else [today.year, today.year + 1]
        for y in years:
            try:
                d = date(y, month, day)
            except ValueError:
                continue
            if today < d <= end:
                return d
    return None


def classify_meeting(detail: Dict[str, Any], today: date, end: date) -> Optional[Tuple[str, Dict[str, Any]]]:
    """One committee-meeting detail -> ("add", row), ("cancel", row) or None
    (outside the window, or not ours). Pure."""
    m = detail.get("committeeMeeting") if isinstance(detail.get("committeeMeeting"), dict) else detail
    d = _day(m.get("date"))
    if d is None or not (today <= d <= end):
        return None
    title = " ".join((m.get("title") or "").split())
    committees = [c.get("name") or "" for c in (m.get("committees") or []) if isinstance(c, dict)]
    committee = committees[0] if committees else f"{m.get('chamber') or 'Congress'} committee"
    # Live 2026-11-05: relatedItems is an object {"bills": [...], "nominations": [...]};
    # the OpenAPI page draws it as a list of such objects. Take both.
    related = m.get("relatedItems") or {}
    related = related if isinstance(related, list) else [related]
    bills = [b for ri in related if isinstance(ri, dict)
             for b in (ri.get("bills") or []) if isinstance(b, dict)]
    bill_ids = [f"{b.get('congress')}/{str(b.get('type') or '').lower()}/{b.get('number')}" for b in bills]
    haystack = title + " " + " ".join(committees)
    keep = bool(_KEEP_RE.search(" ".join(committees)) or _KW_RE.search(haystack)
                or any(b in WATCHED_BILLS for b in bill_ids))
    if not keep:
        return None
    mtype = (m.get("type") or "Meeting").strip()
    markup = mtype.lower() == "markup" or bool(_MARKUP_RE.search(title))
    on_matching_bill = any(b in WATCHED_BILLS for b in bill_ids) or bool(_KW_RE.search(title))
    impact = 2 if markup and on_matching_bill else 1
    # Live types: House "Hearing"/"Markup"/"Meeting", Senate "Open Hearing"/"Open Business Meeting".
    word = "markup" if markup else ("hearing" if "hearing" in mtype.lower() else "meeting")
    status = (m.get("meetingStatus") or "Scheduled").strip()
    loc = m.get("location") or {}
    where = " ".join(x for x in (loc.get("room"), loc.get("building")) if x)
    congress = m.get("congress") or congress_number(today)
    chamber = (m.get("chamber") or "house").lower()
    event_id = m.get("eventId") or m.get("eventid") or ""
    row = {
        "date": d.isoformat(), "at_utc": _at_utc(m.get("date")),
        "event": f"{committee} {word}: {title[:120]}" if title else f"{committee} {word}",
        "kind": "congress_markup" if markup else "congress_hearing",
        "coins": _coins_for(haystack), "impact": impact,
        "verified": status == "Scheduled",
        "source": f"https://www.congress.gov/event/{congress}th-congress/{chamber}-event/{event_id}",
        "note": (f"congress.gov status {status}; time per the congress.gov API"
                 + (f"; {where}" if where else "")
                 + (f"; bills {', '.join(bill_ids)}" if bill_ids else "")),
    }
    row["_bills"] = [b for b in bill_ids if _BILL_ID_RE.fullmatch(b)]
    return ("cancel" if status == "Canceled" else "add", row)


def classify_action(a: Dict[str, Any], label: str, bill_url: str, today: date, end: date
                    ) -> List[Dict[str, Any]]:
    """One bill action -> its row(s): the action itself (impact 2, 3 for a
    passage or a signature) and, when the text names a coming date, a
    future floor row. Pure."""
    d = _day(a.get("actionDate"))
    text = " ".join((a.get("text") or "").split())
    if d is None or not text or a.get("type") in SKIP_ACTION_TYPES:
        return []
    passage = a.get("type") == "BecameLaw" or bool(_PASSAGE_RE.search(text))
    rows = [{
        "date": d.isoformat(), "at_utc": None, "event": f"{label}: {text[:120]}",
        "kind": "bill_action", "coins": _coins_for(label + " " + text), "impact": 3 if passage else 2,
        "verified": True, "source": bill_url,
        "note": f"congress.gov action type {a.get('type') or '?'}",
    }]
    future = date_in_text(text, today, end)
    if future:
        rows.append({
            "date": future.isoformat(), "at_utc": None,
            "event": f"{label}: floor consideration ({text[:90]})", "kind": "congress_floor",
            "coins": _coins_for(label + " " + text), "impact": 2, "verified": True, "source": bill_url,
            "note": f"date named in the {d.isoformat()} action text",
        })
    return rows


def classify_fr_doc(doc: Dict[str, Any], today: date, end: date) -> List[Dict[str, Any]]:
    """One Federal Register document -> a row per dated field in the window."""
    title = " ".join((doc.get("title") or "").split())
    if not title:
        return []
    url = doc.get("html_url") or "https://www.federalregister.gov/"
    dtype = doc.get("type") or "document"
    hot = bool(re.search(r"\b(iran|sanction|petroleum|crude|hormuz|opec|russia)", title, re.I))
    rows = []
    for field, label in (("effective_on", "effective"), ("comments_close_on", "comments close")):
        d = _day(doc.get(field))
        if d is None or not (today <= d <= end):
            continue
        rows.append({
            "date": d.isoformat(), "at_utc": None, "event": f"Federal Register {label}: {title[:110]}",
            "kind": "federal_register", "coins": ["oil"], "impact": 2 if dtype == "Rule" and hot else 1,
            "verified": True, "source": url,
            "note": f"{dtype}, published {doc.get('publication_date') or '?'}, {doc.get('document_number') or ''}".strip(", "),
        })
    return rows


# ── sources ─────────────────────────────────────────────────────────────────

def _q(api_key: str, **extra: Any) -> Dict[str, Any]:
    return dict({"api_key": api_key, "format": "json"}, **extra)


def meeting_rows(now: datetime, days: int, api_key: str, fetch: Fetch
                 ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], int]:
    """(rows to add, rows to cancel, errors, meetings checked)."""
    today, end = now.date(), now.date() + timedelta(days=days)
    congress = congress_number(today)
    frm = (now - timedelta(days=MEETING_UPDATE_LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    to = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    adds, cancels, errors = [], [], []
    checked = 0
    for chamber in ("senate", "house"):
        data, err = fetch(f"{CONGRESS_API}/committee-meeting/{congress}/{chamber}",
                          params=_q(api_key, limit=LIST_LIMIT, fromDateTime=frm, toDateTime=to))
        if err or not isinstance(data, dict):
            errors.append(f"congress_meetings:{chamber}:{err or 'bad_shape'}")
            continue
        n_err = 0
        for item in (data.get("committeeMeetings") or [])[:MAX_DETAILS_PER_CHAMBER]:
            url = (item.get("url") or "").split("?")[0]
            if not url:
                continue
            detail, derr = fetch(url, params=_q(api_key))
            checked += 1
            if derr or not isinstance(detail, dict):
                n_err += 1
                if n_err <= MAX_ERRORS_PER_SOURCE:
                    errors.append(f"congress_meetings:{chamber}:{item.get('eventId')}:{derr or 'bad_shape'}")
                continue
            got = classify_meeting(detail, today, end)
            if got is None:
                continue
            (cancels if got[0] == "cancel" else adds).append(got[1])
    return adds, cancels, errors, checked


def bill_rows(now: datetime, days: int, api_key: str, fetch: Fetch, state: Dict[str, Any],
              bills: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """New actions on `bills` since the last scan -> rows. `state` (the
    overlay's state block) is updated in place: seen action keys and the
    bill titles."""
    today, end = now.date(), now.date() + timedelta(days=days)
    seen_map: Dict[str, List[str]] = state.setdefault("bill_actions", {})
    titles: Dict[str, str] = state.setdefault("bill_titles", {})
    rows, errors = [], []
    for bill_id, label in bills.items():
        try:
            congress, btype, number = bill_id.split("/")
        except ValueError:
            continue
        base = f"{CONGRESS_API}/bill/{congress}/{btype}/{number}"
        public = f"https://www.congress.gov/bill/{congress}th-congress/{_bill_slug(btype)}/{number}"
        if bill_id not in titles:
            detail, derr = fetch(base, params=_q(api_key))
            if not derr and isinstance(detail, dict) and isinstance(detail.get("bill"), dict):
                t = " ".join((detail["bill"].get("title") or "").split())
                if t:
                    titles[bill_id] = f"{_bill_label(btype, number)} {t[:80]}"
        label = titles.get(bill_id) or label
        data, err = fetch(f"{base}/actions", params=_q(api_key, limit=LIST_LIMIT))
        if err or not isinstance(data, dict):
            errors.append(f"congress_bills:{bill_id}:{err or 'bad_shape'}")
            continue
        actions = [a for a in (data.get("actions") or []) if isinstance(a, dict)]
        first_time = bill_id not in seen_map
        seen = set(seen_map.get(bill_id, []))
        floor = today - timedelta(days=RECENT_ACTION_DAYS)
        for a in actions:
            k = action_key(a)
            if k in seen:
                continue
            d = _day(a.get("actionDate"))
            if d is None or (first_time and d < floor):
                continue
            rows.extend(classify_action(a, label, public, today, end))
        seen_map[bill_id] = sorted(seen | {action_key(a) for a in actions})[-300:]
    return rows, errors


def _bill_slug(btype: str) -> str:
    return {"hr": "house-bill", "s": "senate-bill", "hjres": "house-joint-resolution",
            "sjres": "senate-joint-resolution", "hres": "house-resolution", "sres": "senate-resolution",
            "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution"}.get(btype, btype)


def _bill_label(btype: str, number: str) -> str:
    return {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hres": "H.Res.",
            "sres": "S.Res.", "hconres": "H.Con.Res.", "sconres": "S.Con.Res."}.get(btype, btype.upper()) + f" {number}"


def federal_register_rows(now: datetime, days: int, fetch: Fetch, terms=FR_TERMS
                          ) -> Tuple[List[Dict[str, Any]], List[str]]:
    today, end = now.date(), now.date() + timedelta(days=days)
    docs, errors = policy.fetch_federal_register_docs(
        list(terms), per_topic=20, fields=policy.FR_FIELDS + policy.FR_DATE_FIELDS, fetch_json=fetch)
    rows, seen = [], set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for row in classify_fr_doc(doc, today, end):
            k = (doc.get("document_number") or doc.get("html_url"), row["date"], row["event"][:30])
            if k in seen:
                continue
            seen.add(k)
            rows.append(row)
    return rows, errors


# ── the scan ────────────────────────────────────────────────────────────────

def scan(now: Optional[datetime] = None, *, days: int = DAYS, api_key: Any = _UNSET, fetch: Optional[Fetch] = None,
         overlay_path: Optional[str] = None, dry_run: bool = False, log_path: Optional[str] = None,
         curated_path: str = S.DATA_PATH, terms: Optional[Tuple[str, ...]] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or http_client.fetch_json
    key = os.environ.get("CONGRESS_API_KEY") if api_key is _UNSET else api_key
    overlay_path = overlay_path or S.overlay_path()
    overlay = event_add.load_overlay(overlay_path)
    state = overlay.setdefault("state", {})
    before = json.dumps(state, sort_keys=True)
    proposed: List[Tuple[str, Dict[str, Any]]] = []
    cancels: List[Dict[str, Any]] = []
    ok, failed, skipped = [], [], []

    if key:
        adds, cancels, errs, checked = meeting_rows(now, days, key, fetch)
        proposed += [("congress_meetings", r) for r in adds]
        failed += errs
        if checked or not errs:
            ok.append(f"congress_meetings({checked} checked)")
        watched = dict(WATCHED_BILLS)
        extra: List[str] = list(state.get("watched_bills") or [])
        for r in adds:
            for b in r.get("_bills") or []:
                if b not in watched and b not in extra and r["kind"] == "congress_markup":
                    extra.append(b)
        extra = extra[-MAX_WATCHED_BILLS:]
        state["watched_bills"] = extra
        for b in extra:
            parts = b.split("/")
            watched.setdefault(b, _bill_label(parts[1], parts[2]) if len(parts) == 3 else b)
        rows, errs = bill_rows(now, days, key, fetch, state, watched)
        proposed += [("congress_bills", r) for r in rows]
        failed += errs
        if len(errs) < len(watched):
            ok.append(f"congress_bills({len(watched)} watched)")
    else:
        skipped += ["congress_meetings(no CONGRESS_API_KEY)", "congress_bills(no CONGRESS_API_KEY)"]

    rows, errs = federal_register_rows(now, days, fetch, terms or FR_TERMS)
    proposed += [("federal_register", r) for r in rows]
    failed += errs
    if rows or not errs:
        ok.append(f"federal_register({len(rows)} dated)")

    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    results: List[Tuple[str, str, Dict[str, Any]]] = []
    counts = {"added": 0, "updated": 0, "unchanged": 0, "curated": 0, "removed": 0, "invalid": 0}
    for src, row in proposed:
        row = {k: v for k, v in row.items() if not k.startswith("_")}
        try:
            status, stored = event_add.add_event(overlay, row, allow_impact_3=True, now=stamp,
                                                 curated_path=curated_path)
        except ValueError as exc:
            counts["invalid"] += 1
            failed.append(f"{src}:invalid:{str(exc)[:60]}")
            continue
        counts[status] += 1
        results.append((status, src, stored))
    for row in cancels:
        if event_add.remove_event(overlay, S.event_key(row)):
            counts["removed"] += 1
            results.append(("removed", "congress_meetings", row))
    state["last_scan"] = stamp
    changed = counts["added"] or counts["updated"] or counts["removed"] or json.dumps(state, sort_keys=True) != before
    if changed and not dry_run:
        event_add.save_overlay(overlay, overlay_path)

    line = (f"EVENT-SCAN {now.strftime('%Y-%m-%d %H:%MZ')} added={counts['added']} updated={counts['updated']} "
            f"removed={counts['removed']} sources_ok={','.join(ok) or '-'} failed={','.join(failed) or '-'}"
            + (f" skipped={','.join(skipped)}" if skipped else "") + (" dry_run" if dry_run else ""))
    line = line[:MAX_LOG_LINE]
    if not dry_run:
        _append_log(line, log_path)
    return {"line": line, "results": results, "counts": counts, "ok": ok, "failed": failed, "skipped": skipped,
            "overlay_path": overlay_path, "dry_run": dry_run}


def _append_log(line: str, path: Optional[str]) -> None:
    if path is None:
        try:
            from services.strategies.desk_log import log_path as desk_log_path
            path = desk_log_path("oil")
        except Exception:  # noqa: BLE001
            path = "/tmp/oil-loop/progress.log"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Scan public APIs for dated events and write the scan overlay.")
    p.add_argument("--dry-run", action="store_true", help="print the rows; write nothing")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--path", help="overlay file (default: $PATHIEL_STATE_DIR/intel/scanned_events.json)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    a = p.parse_args(argv)
    from _state_env import load_env_local  # scripts/_state_env.py: .env.local, never printed
    load_env_local()
    out = scan(days=a.days, overlay_path=a.path, dry_run=a.dry_run)
    if a.json:
        print(json.dumps({k: v for k, v in out.items() if k != "results"}
                         | {"rows": [dict(r, _status=s, _source=src) for s, src, r in out["results"]]}, indent=1))
    else:
        for status, src, row in out["results"]:
            print(f"{status.upper():<9} {src:<17} {event_add.format_row(row)}")
        print(out["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
