"""The repo's own pool/ directory verifies, offline, on every commit and in
CI (the Tests job, permissions: contents: read). This is the read-only check
that no member file with a bad signature can sit on main."""
from __future__ import annotations

import subprocess
from pathlib import Path

import contracts.pool as pool
from contracts import runtime

ROOT = Path(__file__).resolve().parents[3]
POOL = ROOT / pool.POOL_DIR


def test_every_file_under_pool_verifies():
    found = pool.scan(str(POOL))
    assert found["problems"] == [], found["problems"]
    for row in found["members"]:
        assert row["count_from_ms"] > pool.ms_from_iso(pool.HOUSE_COUNT_FROM)


def test_the_house_row_is_the_published_builder_address_or_absent():
    house = pool.load_house(str(POOL))
    if house is not None:
        assert house["address"] == runtime.DEFAULT_BUILDER_ADDRESS.lower()
        assert house["kind"] == "house" and house["count_from"] >= pool.HOUSE_COUNT_FROM


def test_pool_holds_only_statement_files():
    """pool/ is data: signed statements, house.json, a README and .gitkeep
    files. Code or anything else there would ride in on a membership pull
    request."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                          pool.POOL_DIR], cwd=ROOT, capture_output=True, text=True, check=True)
    allowed_top = {f"{pool.POOL_DIR}/{pool.HOUSE_FILE}", f"{pool.POOL_DIR}/README.md"}
    for rel in filter(None, out.stdout.splitlines()):
        parts = rel.split("/")
        if rel in allowed_top:
            continue
        assert len(parts) == 3 and parts[1] in (pool.MEMBERS_SUBDIR, pool.LEAVES_SUBDIR), rel
        assert parts[2] == ".gitkeep" or (parts[2].endswith(".json") and parts[2].startswith("0x")), rel
