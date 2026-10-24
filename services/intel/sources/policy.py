"""Policy, bills, and "things in the making": Federal Register, OFAC
recent actions, and congress.gov bills (needs a free key).

Verified live 2026-10-17:
  Federal Register  federalregister.gov/api/v1/documents.json — reliable,
                     no key, agency slugs confirmed against
                     federalregister.gov/api/v1/agencies.json.
  OFAC               ofac.treasury.gov/recent-actions — HTML, no RSS/JSON
                     feed exists (ofac.treasury.gov/rss.xml is a general
                     content feed, not sorted or scoped to recent
                     sanctions actions — not used). The HTML page IS
                     server-rendered (not JS-fetched) with a stable
                     `search-result views-row` structure: a dated link per
                     action. Parsed with a narrow regex against that exact
                     structure, not a general HTML parser.
  GovTrack           CONFIRMED DEAD for automated access: every endpoint,
                     including the plain homepage, returns a Cloudflare JS
                     challenge page (HTTP 403, "Just a moment...") to
                     curl/httpx regardless of User-Agent. Not implemented;
                     bills run on congress.gov alone.
  congress.gov       api.congress.gov/v3/bill — confirmed it 403s with
                     API_KEY_MISSING when CONGRESS_API_KEY is unset (the
                     expected, documented case here). Parser below is
                     written against the documented v3 schema; it has not
                     been exercised against a live key (none available in
                     this environment) — see
                     tests/fixtures/congress_bill_list.json's header
                     comment-equivalent note in tests/test_sources_policy.py.
"""
from __future__ import annotations

import html as html_mod
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DeskSpec, PolicyItem
from services.intel import http_client, scoring
from services.intel.textnorm import dedup_by_title, stable_id

FEDERAL_REGISTER_URL = "https://www.federalregister.gov/api/v1/documents.json"
OFAC_RECENT_ACTIONS_URL = "https://ofac.treasury.gov/recent-actions"
CONGRESS_BILLS_URL = "https://api.congress.gov/v3/bill"

# Slugs verified against federalregister.gov/api/v1/agencies.json 2026-10-17.
AGENCY_SLUGS: Dict[str, str] = {
    "doe": "energy-department",
    "treasury": "treasury-department",
    "ofac": "foreign-assets-control-office",
    "epa": "environmental-protection-agency",
    "commerce": "commerce-department",
    "sec": "securities-and-exchange-commission",
    "cftc": "commodity-futures-trading-commission",
}

_TYPE_TO_STATUS = {
    "Rule": "regulation",
    "Proposed Rule": "proposed",
    "Notice": "announced",
    "Presidential Document": "executive_action",
}


def _fr_item_to_policy(doc: Dict[str, Any], desk_id: str) -> PolicyItem:
    agencies = doc.get("agencies") or []
    body = ", ".join(a.get("name", "") for a in agencies[:2]) or "Federal Register"
    pub_date = doc.get("publication_date")
    dt = None
    if pub_date:
        try:
            dt = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
    title = doc.get("title") or ""
    summary = doc.get("abstract")
    return PolicyItem(
        id=stable_id("policy", "fr", doc.get("document_number") or doc.get("html_url") or title),
        title=title[:500], url=doc.get("html_url") or "about:blank", body=body,
        status=_TYPE_TO_STATUS.get(doc.get("type") or "", "unknown"),
        date=dt, summary=(summary[:500] if summary else None),
        stance=scoring.stance(f"{title} {summary or ''}", desk_id),
    )


def fetch_federal_register(spec: DeskSpec, *, per_topic: int = 8
                            ) -> Tuple[List[PolicyItem], Optional[str]]:
    if not spec.policy_topics:
        return [], None
    items: List[PolicyItem] = []
    errors: List[str] = []
    agency_slugs = list(AGENCY_SLUGS.values())
    for term in spec.policy_topics:
        params = [("per_page", str(per_topic)), ("order", "newest"),
                  ("conditions[term]", term)]
        for slug in agency_slugs:
            params.append(("conditions[agencies][]", slug))
        for field in ("title", "publication_date", "type", "html_url", "agencies",
                      "document_number", "abstract"):
            params.append(("fields[]", field))
        data, err = http_client.fetch_json(FEDERAL_REGISTER_URL, params=params)
        if err:
            errors.append(f"policy:federal_register:{term[:20]}:{err}")
            continue
        if not isinstance(data, dict):
            errors.append(f"policy:federal_register:{term[:20]}:bad_shape")
            continue
        for doc in (data.get("results") or [])[:per_topic]:
            items.append(_fr_item_to_policy(doc, spec.id))
    return items, ("; ".join(errors) if errors else None)


# ── OFAC recent actions (HTML, no feed) ──────────────────────────────

# One <a href="/recent-actions/YYYYMMDD[_n]" hreflang="en">Title</a> per
# action, followed by "Month DD, YYYY -  <a ...>Category</a>" — verified
# against a live capture 2026-10-17 (tests/fixtures/ofac_recent_actions.html).
_OFAC_ROW = re.compile(
    r'<a href="/recent-actions/(\d{8})(?:_\d+)?" hreflang="en">([^<]+)</a>.*?'
    r'(\w+ \d{1,2}, \d{4})\s*-\s*\n?<a href="/recent-actions/([a-z-]+)">([^<]+)</a>',
    re.S,
)


def parse_ofac_html(html: str, desk_id: str, *, max_items: int = 20) -> List[PolicyItem]:
    out = []
    for m in _OFAC_ROW.finditer(html):
        ymd, title, date_str, category_slug, category_name = m.groups()
        title = html_mod.unescape(title)
        category_name = html_mod.unescape(category_name)
        try:
            dt = datetime.strptime(date_str, "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
        status = "regulation" if "general-licenses" in category_slug else \
            "executive_action" if "sanctions-list" in category_slug else "announced"
        out.append(PolicyItem(
            id=stable_id("policy", "ofac", ymd, title),
            title=title.strip()[:500],
            url=f"https://ofac.treasury.gov/recent-actions/{ymd}",
            body="Treasury/OFAC", status=status, date=dt,
            summary=category_name.strip(),
            stance=scoring.stance(title, desk_id),
        ))
        if len(out) >= max_items:
            break
    return out


# ofac.treasury.gov/recent-actions answers in 10.1s to 10.3s (HTTP 200,
# 44KB) — measured twice on 2026-10-24, same the next hour. The previous
# 4s timeout with one retry therefore failed on every refresh and the brief
# carried `policy:ofac:timeout` for days. One attempt with a timeout just
# under build_packet's 12s FETCH_DEADLINE_S is the only shape that can
# succeed: the page is slow, not flaky, and a retry would be a second 10s.
# On a slow day the 4h TTL cache serves the last good copy (cache.py).
OFAC_TIMEOUT_S = 11.0


def fetch_ofac(spec: DeskSpec) -> Tuple[List[PolicyItem], Optional[str]]:
    html, err = http_client.fetch_text(OFAC_RECENT_ACTIONS_URL, timeout_s=OFAC_TIMEOUT_S, retries=0)
    if err:
        return [], f"policy:ofac:{err}"
    items = parse_ofac_html(html, spec.id)
    if not items:
        return [], "policy:ofac:no_rows_parsed"
    return items, None


# ── congress.gov bills (needs CONGRESS_API_KEY) ──────────────────────

_CONGRESS_STATUS_HINTS = (
    ("public law", "enacted"), ("became law", "enacted"), ("signed", "enacted"),
    ("passed senate", "passed_chamber"), ("passed house", "passed_chamber"),
    ("committee", "in_committee"), ("introduced", "proposed"),
)


def _congress_status(latest_action_text: str) -> str:
    low = (latest_action_text or "").lower()
    for needle, status in _CONGRESS_STATUS_HINTS:
        if needle in low:
            return status
    return "unknown"


def _congress_bill_to_policy(bill: Dict[str, Any], desk_id: str) -> PolicyItem:
    title = bill.get("title") or ""
    latest = (bill.get("latestAction") or {})
    action_text = latest.get("text") or ""
    action_date = latest.get("actionDate")
    dt = None
    if action_date:
        try:
            dt = datetime.strptime(action_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
    congress_n = bill.get("congress")
    bill_type = (bill.get("type") or "").lower()
    number = bill.get("number")
    url = (f"https://www.congress.gov/bill/{congress_n}th-congress/"
           f"{bill_type}/{number}") if congress_n and bill_type and number else "about:blank"
    return PolicyItem(
        id=stable_id("policy", "congress", str(congress_n), bill_type, str(number)),
        title=title[:500], url=url, body="US Congress",
        status=_congress_status(action_text), date=dt,
        summary=(action_text[:500] if action_text else None),
        stance=scoring.stance(f"{title} {action_text}", desk_id),
    )


def fetch_congress_bills(spec: DeskSpec, *, per_topic: int = 5
                          ) -> Tuple[List[PolicyItem], Optional[str]]:
    api_key = os.environ.get("CONGRESS_API_KEY")
    if not api_key:
        return [], "policy:congress_bills:CONGRESS_API_KEY_missing"
    if not spec.policy_topics:
        return [], None
    items: List[PolicyItem] = []
    errors: List[str] = []
    for term in spec.policy_topics:
        data, err = http_client.fetch_json(CONGRESS_BILLS_URL, params={
            "query": term, "api_key": api_key, "format": "json", "limit": per_topic,
        })
        if err:
            errors.append(f"policy:congress_bills:{term[:20]}:{err}")
            continue
        if not isinstance(data, dict):
            errors.append(f"policy:congress_bills:{term[:20]}:bad_shape")
            continue
        for bill in (data.get("bills") or [])[:per_topic]:
            items.append(_congress_bill_to_policy(bill, spec.id))
    return items, ("; ".join(errors) if errors else None)


# One callable per source, each `(spec) -> (List[PolicyItem], Optional[str])`.
# api.py fans these out to its own thread pool — see that module's 15s
# cold-build budget note.
ATOMIC_FETCHERS = (fetch_federal_register, fetch_ofac, fetch_congress_bills)


def dedup_policy(items: List[PolicyItem]) -> List[PolicyItem]:
    """Pure: newest-first, near-duplicate titles collapsed. Split out so
    api.py's parallel fetch path can merge every source's results once,
    after every future resolves."""
    return dedup_by_title(
        sorted(items, key=lambda p: p.date or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True),
        lambda p: p.title,
    )


def fetch_policy(spec: DeskSpec, now: datetime) -> Tuple[List[PolicyItem], List[str]]:
    """Convenience sequential path — see news.fetch_headlines's docstring
    for why this exists alongside ATOMIC_FETCHERS."""
    failed: List[str] = []
    all_items: List[PolicyItem] = []
    for fetch_fn in ATOMIC_FETCHERS:
        items, err = fetch_fn(spec)
        if err:
            failed.append(err)
        all_items.extend(items)
    return dedup_policy(all_items), failed
