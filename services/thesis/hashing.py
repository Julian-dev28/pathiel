"""Canonical sha256 of a contract payload, for Thesis.analytics_ref /
intel_ref. One function so both fallback.py and assemble.py hash the same
way - a mismatch between them would make analytics_ref useless as an
integrity check.

Keys are sorted before hashing (``sort_keys=True``) so the hash depends only
on content, not on pydantic's field-declaration order - two payloads with
identical data always hash identically, even if one came from
``model_dump_json()`` directly and the other from a dict built by hand (as a
test fixture might).
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def hash_payload(model: BaseModel) -> str:
    canonical = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
