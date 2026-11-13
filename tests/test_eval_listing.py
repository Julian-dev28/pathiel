"""Eval: is pathiel's public listing ready for a stranger to read?

One scorecard over the listing files and the public copy, measured against
the outcome this unit exists for: a stranger who lands on any listing sees
one version, mainnet by default with testnet as the opt-in, the fee disclosed
with its exact rate and address, the risks and the jurisdiction notice, no
return claim anywhere one hop from the copy, and no path into the
maintainer's mainnet setup without a warning.

Rows (every one must meet its target):

  version agreement                       3/3
  server.json rule checks                 N/N
  listing network rules                   N/N
  README disclosure checklist             7/7
  stale network wording in owned files    0
  builder address problems                0
  claim hits in the PUBLIC_COPY reach set 0
  research-doc links in public copy       0
  clone instructions without the warning  0
  em dashes in public copy                0
  banned words in public copy             0
  ROI doc references outside .gitignore   0
  secrets declared in listing files       0

Offline. Set PATHIEL_EVAL_OUT to keep the JSON.

    .venv/bin/python -m pytest -q -s tests/test_eval_listing.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import List

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def _load(name: str) -> ModuleType:
    """The gate test modules hold the checks; the eval scores the same ones,
    so a check can never pass in one place and fail in the other. Loaded by
    path because tests/ is not a package."""
    spec = importlib.util.spec_from_file_location(f"_eval_listing_{name}", HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None, name
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(copy_lint) -> List[dict]:
    meta = _load("test_listing_metadata")
    copy = _load("test_public_copy")

    def row(check: str, got, want, ok: bool) -> dict:
        return {"check": check, "got": got, "want": want, "ok": bool(ok)}

    rows: List[dict] = []

    versions = meta.version_sources()
    agree = sum(1 for _, v in versions if v == meta.package_version())
    rows.append(row("version agreement", f"{agree}/{len(versions)}", f"{len(versions)}/{len(versions)}",
                    agree == len(versions) == 3))

    rules = meta.server_json_rules()
    passed = sum(1 for _, ok in rules if ok)
    rows.append(row("server.json rule checks", f"{passed}/{len(rules)}", f"{len(rules)}/{len(rules)}",
                    passed == len(rules)))

    nets = meta.network_defaults()
    met_nets = sum(1 for _, ok in nets if ok)
    rows.append(row("listing network rules (mainnet default, testnet opt-in)",
                    f"{met_nets}/{len(nets)}", f"{len(nets)}/{len(nets)}", met_nets == len(nets)))

    disclosure = copy.disclosure_results(copy._read("README.md"))
    met = sum(1 for _, ok, _ in disclosure if ok)
    want = len(copy.DISCLOSURE_CHECKLIST)      # 7 with the fee on, 6 while it is waived
    rows.append(row("README disclosure checklist", f"{met}/{len(disclosure)}", f"{want}/{want}",
                    met == len(disclosure) == want))

    stale = sum(len(copy.stale_network_hits(copy._read(rel))) for rel in copy.NETWORK_OWNED)
    rows.append(row("stale network wording in owned files", stale, 0, stale == 0))

    files = copy.public_copy_files()
    addr = len(copy.builder_address_problems({rel: copy._read(rel) for rel in files}))
    rows.append(row("builder address problems in public copy", addr, 0, addr == 0))

    reach = copy_lint.reach([ROOT / p for p in files])
    claims = sum(len(copy_lint.claim_hits(p.read_text(encoding="utf-8", errors="replace")))
                 for p in reach)
    rows.append(row(f"claim hits in the reach set ({len(reach)} files)", claims, 0, claims == 0))

    research = sum(len(copy.research_doc_mentions(rel, copy_lint)) for rel in files)
    rows.append(row("research-doc links in public copy", research, 0, research == 0))

    clones = len(copy.unwarned_clone_lines("README.md")) + \
        len(copy.unwarned_clone_lines("docs/GETTING_STARTED.md"))
    rows.append(row("clone instructions without the warning", clones, 0, clones == 0))

    style = [h for rel in files for h in copy_lint.style_hits(copy._read(rel))]
    dashes = sum(1 for h in style if h.startswith("em dash"))
    banned = sum(1 for h in style if h.startswith("banned word"))
    rows.append(row("em dashes in public copy", dashes, 0, dashes == 0))
    rows.append(row("banned words in public copy", banned, 0, banned == 0))

    roi = len(copy.tracked_roi_references())
    rows.append(row("ROI doc references outside .gitignore", roi, 0, roi == 0))

    secrets = len(meta.declared_secrets())
    rows.append(row("secrets declared in listing files", secrets, 0, secrets == 0))
    return rows


def test_eval_listing_meets_every_target(copy_lint, scorecard):
    card = scorecard("listing", run(copy_lint))
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"listing eval: {card['passed']}/{card['total']}; failed: {failed}"
