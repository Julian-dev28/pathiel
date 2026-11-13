"""contracts/pool: the statement, the member file, the offline rules, the
scoreboard schema. Offline. Every key here is made in the test and dropped."""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

import contracts.pool as pool
from contracts import runtime

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ADDR = "0x" + "ab" * 20
SIGNER = "0x" + "cd" * 20
AT = "2026-11-12T14:03:22Z"
NOW = pool.ms_from_iso("2026-11-12T15:00:00Z")
EM_DASH = chr(0x2014)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="ascii"))


def _signed(kind: str, account, key_owner, at: str = AT) -> dict:
    """A statement file for `account`'s address signed by `key_owner`'s key."""
    address, signer = account.address.lower(), key_owner.address.lower()
    message = pool.statement_text(kind, address, signer, at)
    sig = Account.sign_message(encode_defunct(text=message), key_owner.key).signature.hex()
    return pool.build_statement_file(kind, address, signer, at, "0x" + sig.removeprefix("0x"))


def _write(root: Path, sub: str, doc: dict) -> Path:
    folder = root / sub
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / pool.statement_filename(doc["address"])
    path.write_text(pool.dump_statement_file(doc), encoding="ascii")
    return path


# ── the statement ───────────────────────────────────────────────────────────

JOIN_TEXT = (
    "pathiel research pool: join\n"
    "Version: 1\n"
    "Network: hyperliquid-mainnet\n"
    f"Account: {ADDR}\n"
    f"Signer: {SIGNER}\n"
    f"Joined at: {AT}\n"
    "Project: github.com/Julian-dev28/pathiel\n"
    "\n"
    "I am the owner of this Hyperliquid account, or an API wallet it approved.\n"
    "I ask pathiel to grade this account's public fills on the pool coins from the time above.\n"
    "I agree that the graded results are published at least 24 hours late on the public "
    "scoreboard, next to this address.\n"
    "No trade, transfer, fee or payment is authorized by this statement.\n"
    "I can leave at any time with a signed leave statement."
)
LEAVE_TEXT = (
    "pathiel research pool: leave\n"
    "Version: 1\n"
    "Network: hyperliquid-mainnet\n"
    f"Account: {ADDR}\n"
    f"Signer: {SIGNER}\n"
    f"Left at: {AT}\n"
    "Project: github.com/Julian-dev28/pathiel\n"
    "\n"
    "I ask pathiel to remove this account from the research pool.\n"
    "The next scoreboard build drops this address and its graded results.\n"
    "No trade, transfer, fee or payment is authorized by this statement."
)


def test_statement_text_is_pinned_byte_for_byte():
    """Version 1 of the signed text. Changing it invalidates every stored
    signature: bump STATEMENT_VERSION and regenerate the fixtures instead."""
    assert pool.STATEMENT_VERSION == 1
    assert pool.statement_text("join", ADDR, SIGNER, AT) == JOIN_TEXT
    assert pool.statement_text("leave", ADDR, SIGNER, AT) == LEAVE_TEXT
    for text in (JOIN_TEXT, LEAVE_TEXT):
        assert text.isascii() and "\r" not in text and not text.endswith("\n")
    assert pool.statement_text("join", ADDR.upper().replace("0X", "0x"), SIGNER, AT) == JOIN_TEXT


def test_statement_names_network_account_signer_time_and_version():
    for needle in ("Version: 1", "hyperliquid-mainnet", ADDR, SIGNER, AT, pool.PROJECT,
                   "at least 24 hours late", "No trade, transfer, fee or payment"):
        assert needle in JOIN_TEXT, needle


@pytest.mark.parametrize("kind,address,signer,at", [
    ("rejoin", ADDR, SIGNER, AT), ("join", "0x12", SIGNER, AT), ("join", ADDR, "nope", AT),
    ("join", ADDR, SIGNER, "2026-11-12 14:03:22"), ("join", ADDR, SIGNER, "2026-13-40T00:00:00Z"),
    ("join", ADDR, SIGNER, "2026-11-12T14:03:22.000Z"),
])
def test_statement_text_refuses_bad_fields(kind, address, signer, at):
    with pytest.raises(pool.PoolError):
        pool.statement_text(kind, address, signer, at)


def test_time_helpers_round_trip_and_are_strict():
    ms = pool.ms_from_iso(AT)
    assert pool.iso_from_ms(ms) == AT and pool.iso_from_ms(ms + 999) == AT
    assert pool.day_of(ms) == "2026-11-12"
    for bad in ("", None, "2026-11-12", "2026-11-12T14:03:22+00:00", 5):
        with pytest.raises(pool.PoolError):
            pool.ms_from_iso(bad)


# ── verification ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,kind,role", [
    ("join_master.json", "join", "master"), ("join_agent.json", "join", "agent"),
    ("leave_agent.json", "leave", "agent"),
])
def test_fixtures_verify(name, kind, role):
    doc = _fixture(name)
    v = pool.verify_statement(doc, kind=kind)
    assert v["kind"] == kind and v["signer_role"] == role
    assert v["address"] == doc["address"] and v["signer"] == doc["signer"]
    assert (FIXTURES / name).read_text(encoding="ascii") == pool.dump_statement_file(doc)


def test_fixture_leave_is_the_join_agents_and_later():
    join, leave = _fixture("join_agent.json"), _fixture("leave_agent.json")
    assert leave["address"] == join["address"] and leave["signer"] == join["signer"]
    assert pool.ms_from_iso(leave["left_at"]) > pool.ms_from_iso(join["joined_at"])


def test_sign_and_recover_round_trip_with_a_throwaway_key():
    acct = Account.create()
    doc = _signed("join", acct, acct)
    assert pool.recover_signer(doc["message"], doc["signature"]) == acct.address.lower()
    assert pool.verify_statement(doc)["signer_role"] == "master"
    assert pool.statement_sha256(doc["message"]) == pool.statement_sha256(doc["message"])
    assert len(pool.statement_sha256(doc["message"])) == 64


def _tamper(doc: dict, **changes) -> dict:
    out = copy.deepcopy(doc)
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def test_every_tamper_is_refused_with_its_code():
    acct, other = Account.create(), Account.create()
    good = _signed("join", acct, acct)
    stranger = _signed("join", other, other)
    later = "2026-11-12T14:03:23Z"
    cases = [
        ("not_object", [good]),
        ("kind", _tamper(good, kind="house")),
        ("keys", _tamper(good, note="hi")),
        ("keys", _tamper(good, signature=None)),
        ("schema", _tamper(good, schema=2)),
        ("schema", _tamper(good, schema=True)),
        ("network", _tamper(good, network="testnet")),
        ("address", _tamper(good, address="0x" + good["address"][2:].upper())),   # not stored lowercase
        ("signer", _tamper(good, signer="0x12")),
        ("time", _tamper(good, joined_at="yesterday")),
        ("message", _tamper(good, joined_at=later)),                   # time moved, text not
        ("message", _tamper(good, message=good["message"] + " ")),
        ("message", _tamper(good, address=other.address.lower())),     # another account's name on it
        ("signature_format", _tamper(good, signature="0x" + "0" * 12)),
        ("signature_format", _tamper(good, signature=good["signature"].upper())),
        ("signature", _tamper(good, signature=stranger["signature"])),
    ]
    for code, doc in cases:
        with pytest.raises(pool.PoolError) as e:
            pool.verify_statement(doc)
        assert e.value.code == code, (code, e.value.code, str(e.value))
    # a statement rebuilt for another signer verifies only with that signer's key
    forged = pool.build_statement_file("join", acct.address.lower(), other.address.lower(), AT,
                                       good["signature"])
    with pytest.raises(pool.PoolError) as e:
        pool.verify_statement(forged)
    assert e.value.code == "signature"
    with pytest.raises(pool.PoolError) as e:
        pool.verify_statement(good, kind="leave")
    assert e.value.code == "kind"
    with pytest.raises(pool.PoolError) as e:
        pool.verify_statement(good, filename="someone-else.json")
    assert e.value.code == "filename"


def test_a_join_signature_never_verifies_as_a_leave():
    acct = Account.create()
    join = _signed("join", acct, acct)
    as_leave = pool.build_statement_file("leave", join["address"], join["signer"], AT, join["signature"])
    with pytest.raises(pool.PoolError) as e:
        pool.verify_statement(as_leave)
    assert e.value.code == "signature"


def test_load_statement_checks_name_size_and_encoding(tmp_path):
    acct = Account.create()
    doc = _signed("join", acct, acct)
    path = _write(tmp_path, "members", doc)
    assert pool.load_statement(str(path), "join")["address"] == acct.address.lower()
    wrong = tmp_path / "members" / "0xnot-the-address.json"
    wrong.write_text(pool.dump_statement_file(doc), encoding="ascii")
    big = tmp_path / "members" / ("0x" + "11" * 20 + ".json")
    big.write_text(" " * (pool.MAX_FILE_BYTES + 1), encoding="ascii")
    junk = tmp_path / "members" / ("0x" + "22" * 20 + ".json")
    junk.write_bytes(b"\xff\xfe{}")
    for p, code in ((wrong, "filename"), (big, "too_big"), (junk, "not_json"),
                    (tmp_path / "members" / "missing.json", "unreadable")):
        with pytest.raises(pool.PoolError) as e:
            pool.load_statement(str(p), "join")
        assert e.value.code == code, p.name


# ── the pool on disk ────────────────────────────────────────────────────────

def test_scan_join_leave_rejoin_pending_and_problems(tmp_path):
    a, b, c, d, agent = (Account.create() for _ in range(5))
    _write(tmp_path, "members", _signed("join", a, a, "2026-11-12T10:00:00Z"))
    _write(tmp_path, "members", _signed("join", b, agent, "2026-11-12T10:00:00Z"))
    _write(tmp_path, "leaves", _signed("leave", b, agent, "2026-11-13T10:00:00Z"))
    _write(tmp_path, "members", _signed("join", c, c, "2026-11-14T10:00:00Z"))      # rejoined
    _write(tmp_path, "leaves", _signed("leave", c, c, "2026-11-13T10:00:00Z"))
    _write(tmp_path, "members", _signed("join", d, d, "2026-11-30T10:00:00Z"))      # not yet
    (tmp_path / "members" / ".gitkeep").write_text("")
    as_of = pool.ms_from_iso("2026-11-20T00:00:00Z")
    found = pool.scan(str(tmp_path), as_of)
    assert found["problems"] == [] and found["house"] is None
    assert [m["address"] for m in found["members"]] == sorted([a.address.lower(), c.address.lower()])
    assert found["left"] == [b.address.lower()] and found["pending"] == [d.address.lower()]
    by = {m["address"]: m for m in found["members"]}
    assert by[c.address.lower()]["count_from"] == "2026-11-14T10:00:00Z"
    assert by[a.address.lower()]["signer_role"] == "master" and by[a.address.lower()]["kind"] == "member"
    assert pool.active_members(str(tmp_path), as_of) == found["members"]

    assert pool.membership(str(tmp_path), a.address)["state"] == "member"
    assert pool.membership(str(tmp_path), b.address)["state"] == "left"
    assert pool.membership(str(tmp_path), b.address)["count_from_ms"] is None
    assert pool.membership(str(tmp_path), c.address)["count_from_ms"] == pool.ms_from_iso("2026-11-14T10:00:00Z")
    assert pool.membership(str(tmp_path), Account.create().address)["state"] == "none"


def test_a_leave_at_the_same_second_as_the_join_leaves(tmp_path):
    a = Account.create()
    _write(tmp_path, "members", _signed("join", a, a, AT))
    _write(tmp_path, "leaves", _signed("leave", a, a, AT))
    assert pool.scan(str(tmp_path), NOW)["left"] == [a.address.lower()]


def test_a_bad_file_is_a_problem_and_active_members_fails_closed(tmp_path):
    a, b = Account.create(), Account.create()
    _write(tmp_path, "members", _signed("join", a, a))
    bad = _signed("join", b, b)
    bad["message"] += "x"
    _write(tmp_path, "members", bad)
    found = pool.scan(str(tmp_path), NOW)
    assert [m["address"] for m in found["members"]] == [a.address.lower()]
    assert [(p["path"], p["code"]) for p in found["problems"]] == [
        (f"members/{b.address.lower()}.json", "message")]
    with pytest.raises(pool.PoolError) as e:
        pool.active_members(str(tmp_path), NOW)
    assert e.value.code == "invalid_pool"
    assert pool.membership(str(tmp_path), b.address)["state"] == "invalid"


def test_an_empty_or_missing_pool_root_is_an_empty_pool(tmp_path):
    assert pool.active_members(str(tmp_path / "nothing"), NOW) == []
    assert pool.scan(str(tmp_path), None) == {"house": None, "members": [], "left": [],
                                              "pending": [], "problems": []}


def _house(tmp_path: Path, **changes) -> None:
    doc = {"schema": 1, "kind": "house", "network": "mainnet",
           "address": runtime.DEFAULT_BUILDER_ADDRESS.lower(),
           "count_from": pool.HOUSE_COUNT_FROM, "label": "maintainer"}
    doc.update(changes)
    (tmp_path / pool.HOUSE_FILE).write_text(json.dumps(doc), encoding="ascii")


def test_house_row_is_first_and_only_the_builder_address_may_be_one(tmp_path):
    a = Account.create()
    _write(tmp_path, "members", _signed("join", a, a))
    _house(tmp_path)
    rows = pool.active_members(str(tmp_path), NOW)
    assert [r["kind"] for r in rows] == ["house", "member"]
    assert rows[0]["address"] == runtime.DEFAULT_BUILDER_ADDRESS.lower()
    assert rows[0]["count_from"] == pool.HOUSE_COUNT_FROM and rows[0]["signer_role"] is None
    for changes, code in (({"address": a.address.lower()}, "address"),
                          ({"count_from": "2026-01-01T00:00:00Z"}, "time"),
                          ({"kind": "member"}, "schema"), ({"extra": 1}, "keys"), ({"label": ""}, "keys")):
        _house(tmp_path, **changes)
        with pytest.raises(pool.PoolError) as e:
            pool.load_house(str(tmp_path))
        assert e.value.code == code, changes
        assert pool.scan(str(tmp_path), NOW)["problems"][0]["path"] == pool.HOUSE_FILE


def test_local_standing_reads_the_repo_copy_and_the_outbox_together(tmp_path):
    repo, outbox = tmp_path / "pool", tmp_path / "outbox"
    a, agent = Account.create(), Account.create()
    addr = a.address

    def state():
        s = pool.local_standing(str(repo), str(outbox), addr)
        assert s["state"] in pool.STANDING_STATES
        assert (s["count_from_ms"] is not None) == (s["state"] in pool.GRADED_STATES)
        return s["state"], s["count_from_ms"]

    t1, t2, t3, t4 = (f"2026-11-1{d}T10:00:00Z" for d in (2, 3, 4, 5))
    assert state() == ("none", None)
    _write(outbox, "members", _signed("join", a, agent, t1))                 # signed here
    assert state() == ("signed_not_merged", pool.ms_from_iso(t1))
    _write(repo, "members", _signed("join", a, agent, t1))                   # merged, plugin updated
    assert state() == ("member", pool.ms_from_iso(t1))
    _write(outbox, "leaves", _signed("leave", a, agent, t2))                 # leave signed here
    assert state() == ("leave_signed_not_merged", None)
    _write(repo, "leaves", _signed("leave", a, agent, t2))                   # leave merged
    assert state() == ("left", None)
    _write(outbox, "members", _signed("join", a, a, t3))                     # rejoin signed here
    assert state() == ("signed_not_merged", pool.ms_from_iso(t3))
    _write(repo, "members", _signed("join", a, a, t3))
    assert state() == ("member", pool.ms_from_iso(t3))
    _write(outbox, "leaves", _signed("leave", a, a, t4))
    assert state() == ("leave_signed_not_merged", None)
    (outbox / "leaves" / pool.statement_filename(addr)).write_text("{}", encoding="ascii")
    s = pool.local_standing(str(repo), str(outbox), addr)
    assert s["state"] == "invalid" and s["problems"][0]["path"].startswith("outbox:leaves/")


def test_local_standing_of_the_house_address(tmp_path):
    _house(tmp_path)
    s = pool.local_standing(str(tmp_path), str(tmp_path / "outbox"), runtime.DEFAULT_BUILDER_ADDRESS)
    assert s["state"] == "house" and s["count_from_ms"] == pool.ms_from_iso(pool.HOUSE_COUNT_FROM)
    other = pool.local_standing(str(tmp_path), str(tmp_path / "outbox"), ADDR)
    assert other["state"] == "none"


def test_repo_paths():
    assert pool.member_repo_path(ADDR) == f"pool/members/{ADDR}.json"
    assert pool.leave_repo_path(ADDR) == f"pool/leaves/{ADDR}.json"
    assert pool.member_path("/r", ADDR) == os.path.join("/r", "members", ADDR + ".json")
    assert pool.repo_pool_root("/repo") == os.path.join("/repo", "pool")
    assert pool.short_address(ADDR) == "0xabab...abab"
    assert pool.explorer_url(ADDR) == f"https://app.hyperliquid.xyz/explorer/address/{ADDR}"


# ── offline rules ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("as_of,cutoff", [
    ("2026-11-14T00:00:00Z", "2026-11-13T00:00:00Z"),
    ("2026-11-14T09:30:00Z", "2026-11-13T00:00:00Z"),
    ("2026-11-14T23:59:59Z", "2026-11-13T00:00:00Z"),
    ("2026-11-15T00:00:00Z", "2026-11-14T00:00:00Z"),
])
def test_cutoff_is_the_midnight_before_the_build_day(as_of, cutoff):
    ms = pool.ms_from_iso(as_of)
    assert pool.iso_from_ms(pool.cutoff_ms(ms)) == cutoff
    assert ms - pool.cutoff_ms(ms) >= pool.DELAY_MS            # never less than 24h late
    assert ms - pool.cutoff_ms(ms) < 2 * pool.DELAY_MS


def test_publishable_is_closed_and_before_the_cutoff():
    cut = pool.cutoff_ms(NOW)
    assert pool.publishable(cut - 1, cut) is True
    assert pool.publishable(cut, cut) is False
    assert pool.publishable(NOW, cut) is False
    assert pool.publishable(None, cut) is False                # an open trade, never


def test_fresh_window():
    assert pool.fresh_problem(NOW, NOW) is None
    assert pool.fresh_problem(NOW + pool.FRESH_FUTURE_SKEW_S * 1000, NOW) is None
    assert "future" in pool.fresh_problem(NOW + pool.FRESH_FUTURE_SKEW_S * 1000 + 1000, NOW)
    assert pool.fresh_problem(NOW - pool.FRESH_MAX_AGE_S * 1000, NOW) is None
    assert "7 days old" in pool.fresh_problem(NOW - pool.FRESH_MAX_AGE_S * 1000 - 1000, NOW)


@pytest.mark.parametrize("extra,role,want", [
    ([{"name": "pathiel", "address": SIGNER, "validUntil": NOW + 1}], {"role": "missing"}, (True, "extraAgents")),
    ([{"name": "pathiel", "address": SIGNER.upper().replace("0X", "0x")}], None, (True, "extraAgents")),
    ([{"name": "pathiel", "address": SIGNER, "validUntil": NOW}],
     {"role": "agent", "data": {"user": ADDR}}, (False, "expired")),       # the list is the last word
    ([{"name": "other", "address": "0x" + "ee" * 20, "validUntil": NOW + 1}],
     {"role": "agent", "data": {"user": ADDR}}, (True, "userRole")),
    ([], {"role": "agent", "data": {"user": ADDR.upper().replace("0X", "0x")}}, (True, "userRole")),
    ([], {"role": "agent", "data": {"user": "0x" + "ee" * 20}}, (False, "not_approved")),
    ([], {"role": "user"}, (False, "not_approved")),
    ([], {"role": "agent"}, (False, "not_approved")),
    (None, None, (False, "not_approved")),
    ({"error": "x"}, "text", (False, "not_approved")),
])
def test_agent_approved_table(extra, role, want):
    assert pool.agent_approved(ADDR, SIGNER, extra, role, NOW) == want


def test_leave_signer_allowed():
    master = {"kind": "leave", "address": ADDR, "signer": ADDR, "signer_role": "master", "at": AT, "at_ms": 0}
    agent = {**master, "signer": SIGNER, "signer_role": "agent"}
    join = {**agent, "kind": "join"}
    other_join = {**join, "signer": "0x" + "ee" * 20}
    assert pool.leave_signer_allowed(master, None, False)
    assert pool.leave_signer_allowed(agent, join, False)            # the key that joined may leave
    assert not pool.leave_signer_allowed(agent, other_join, False)
    assert not pool.leave_signer_allowed(agent, None, False)
    assert pool.leave_signer_allowed(agent, other_join, True)       # an approved API wallet
    # Documented, accepted: the key that joined may leave with NO venue read, so it still can
    # after the account revoked it. agent_ok=False stands for "not approved now".
    assert pool.leave_signer_allowed(agent, join, False)
    assert "REVOKED" in pool.leave_signer_allowed.__doc__


def test_cluster_ids_anchor_on_the_first_entry_of_one_coin_and_side():
    w = pool.CLUSTER_SPAN_MS
    rows = [
        ("xyz:BRENTOIL", "long", 10 * w),            # 0: the anchor
        ("xyz:GOLD", "long", 10 * w),                # separate coin
        ("xyz:BRENTOIL", "long", 11 * w),            # 0: exactly one window after the anchor
        ("xyz:BRENTOIL", "short", 10 * w + 5),       # other side
        ("xyz:BRENTOIL", "long", 11 * w + 1),        # a new bet: outside the anchor's window,
        ("xyz:BRENTOIL", "long", 12 * w),            # ... and this one is in the NEW anchor's window
        ("xyz:BRENTOIL", "long", 12 * w + 2),        # a third bet
    ]
    ids = pool.cluster_ids(rows)
    assert ids[0] == ids[2]
    assert ids[4] == ids[5] and ids[4] != ids[0]     # not chained through the middle one
    assert len({ids[0], ids[1], ids[3], ids[4], ids[6]}) == 5
    assert sorted(set(ids)) == list(range(5))
    assert pool.cluster_ids(list(reversed(rows))) == list(reversed(ids))     # input order does not matter
    assert pool.cluster_ids([]) == []
    # ten members on one call are one bet
    assert len(set(pool.cluster_ids([("xyz:BRENTOIL", "long", 10 * w + i * 60_000) for i in range(10)]))) == 1


def test_a_run_of_entries_with_no_quiet_hour_does_not_fold_into_one_cluster():
    """The review finding of 2026-11-12: chained on the previous entry, a coin
    and side that never goes an hour without an entry was ONE cluster for
    ever, so the count of independent trades could not grow. Anchored, a
    cluster never spans more than CLUSTER_SPAN_MS."""
    quarter = 15 * 60_000
    days = 30
    rows = [("xyz:BRENTOIL", "long", i * quarter) for i in range(days * 96)]     # every 15 minutes
    ids = pool.cluster_ids(rows)
    assert len(set(ids)) == days                                # one per 24 hours, not one in all
    span = {}
    for (_, _, entry), cid in zip(rows, ids):
        lo, hi = span.get(cid, (entry, entry))
        span[cid] = (min(lo, entry), max(hi, entry))
    assert max(hi - lo for lo, hi in span.values()) <= pool.CLUSTER_SPAN_MS
    # more members on the same stream add trades, never clusters
    crowd = rows + [(c, s, e + 60_000) for c, s, e in rows] + [(c, s, e + 120_000) for c, s, e in rows]
    assert len(set(pool.cluster_ids(crowd))) == days


def test_cluster_span_is_bounded_on_seeded_random_streams():
    import random
    for seed in range(25):
        rng = random.Random(seed)
        rows = [(rng.choice(pool.POOL_COINS), rng.choice(("long", "short")), rng.randrange(0, 20 * pool.DAY_MS))
                for _ in range(400)]
        ids = pool.cluster_ids(rows)
        seen = {}
        for (coin, side, entry), cid in zip(rows, ids):
            key, lo, hi = seen.get(cid, ((coin, side), entry, entry))
            assert key == (coin, side)                          # a cluster is one coin and one side
            seen[cid] = (key, min(lo, entry), max(hi, entry))
        assert all(hi - lo <= pool.CLUSTER_SPAN_MS for _, lo, hi in seen.values())
        assert sorted(set(ids)) == list(range(len(seen)))


# ── a change to the pool, judged before it lands ────────────────────────────

def _both(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def test_deleting_a_leave_would_restore_the_old_join_and_the_rule_refuses_it(tmp_path):
    """The consent gap of the 2026-11-12 review. scan() reads the files that
    are there: take the leave away and the OLD join is in force again."""
    a = Account.create()
    t_join, t_leave = "2026-11-12T10:00:00Z", "2026-11-20T10:00:00Z"
    base, head = _both(tmp_path, "base"), _both(tmp_path, "head")
    for root in (base, head):
        _write(root, "members", _signed("join", a, a, t_join))
    _write(base, "leaves", _signed("leave", a, a, t_leave))                  # head: the leave is gone
    as_of = pool.ms_from_iso("2026-12-01T00:00:00Z")
    assert pool.scan(str(base), as_of)["left"] == [a.address.lower()]
    back = pool.scan(str(head), as_of)["members"]
    assert [m["count_from"] for m in back] == [t_join]                       # the gap, pinned

    before, after = pool.membership(str(base), a.address), pool.membership(str(head), a.address)
    problem = pool.reinstatement_problem(before, after)
    assert problem and "no new consent" in problem and t_join in problem and t_leave in problem
    assert pool.short_address(a.address.lower()) in problem


def test_reinstatement_rule_table(tmp_path):
    a = Account.create()
    t0, t1, t2, t3 = (f"2026-11-1{d}T10:00:00Z" for d in (2, 3, 4, 5))
    ms = pool.ms_from_iso

    def state(name: str, join: str = "", leave: str = "") -> dict:
        root = _both(tmp_path, name)
        if join:
            _write(root, "members", _signed("join", a, a, join))
        if leave:
            _write(root, "leaves", _signed("leave", a, a, leave))
        return pool.membership(str(root), a.address)

    none = state("none")
    member0, member2 = state("m0", join=t0), state("m2", join=t2)
    left = state("left", join=t0, leave=t1)
    rejoined = state("rejoined", join=t2, leave=t1)
    rejoined_clean = state("rejoined_clean", join=t2)
    old_leave = state("old_leave", join=t2, leave=t0)
    assert (none["state"], member0["state"], left["state"], rejoined["state"]) == (
        "none", "member", "left", "member")

    ok = [
        (none, member0, ()),                    # a first join
        (member0, member0, ()),                 # nothing changed
        (member0, left, ()),                    # a leave: going out never needs the rule
        (left, rejoined, ()),                   # a rejoin signed after the leave
        (left, rejoined_clean, ()),             # the same, with the old leave file removed too
        (rejoined, rejoined_clean, ()),         # tidying away a leave that was not in force
        (member0, member2, ()),                 # a newer join replaces the old one
        (member0, none, ()),                    # removed on request
        (left, none, ()),
        (none, member2, (ms(t0), ms(t1))),      # removed long ago, back with a newer join
    ]
    for before, after, held in ok:
        assert pool.reinstatement_problem(before, after, held) is None, (before["state"], after["state"])

    refused = [
        (left, member0, ()),                    # the leave deleted, the old join untouched
        (state("left2", join=t2, leave=t3), old_leave, ()),     # the leave swapped for an older one
        (member2, member0, ()),                 # the join swapped for an earlier one: counts further back
        (none, member0, (ms(t0),)),             # a removed member file put back from git history
        (none, member0, (ms(t0), ms(t1))),      # ... or put back after its leave was removed as well
        (none, member2, (ms(t2),)),             # the same statement again is not a new one
    ]
    for before, after, held in refused:
        problem = pool.reinstatement_problem(before, after, held)
        assert problem and "no new consent" in problem, (before["state"], after["state"], held)


def test_pr_url_prefills_the_path_and_content():
    content = pool.dump_statement_file(_fixture("join_master.json"))
    url = pool.pr_new_file_url(pool.member_repo_path(ADDR), content)
    parsed = urlparse(url)
    assert parsed.netloc == "github.com" and parsed.path == "/Julian-dev28/pathiel/new/main"
    q = parse_qs(parsed.query, keep_blank_values=True)
    assert q["filename"] == [f"pool/members/{ADDR}.json"] and q["value"] == [content]
    assert len(url) < 4000


def test_venue_request_shapes():
    assert pool.fills_request(ADDR, 1, 2) == {"type": "userFillsByTime", "user": ADDR, "startTime": 1,
                                              "endTime": 2, "aggregateByTime": False}
    assert pool.orders_request(ADDR) == {"type": "historicalOrders", "user": ADDR}
    assert pool.extra_agents_request(ADDR) == {"type": "extraAgents", "user": ADDR}
    assert pool.user_role_request(ADDR) == {"type": "userRole", "user": ADDR}
    with pytest.raises(pool.PoolError):
        pool.fills_request("0x12", 1, 2)


# ── the scoreboard ──────────────────────────────────────────────────────────

def test_fixture_and_empty_scoreboards_are_valid_and_dump_stably():
    doc = _fixture("scoreboard.json")
    assert pool.validate_scoreboard(doc) == []
    assert (FIXTURES / "scoreboard.json").read_text(encoding="ascii") == pool.dump_scoreboard(doc)
    empty = pool.empty_scoreboard(NOW)
    assert pool.validate_scoreboard(empty) == []
    assert empty["day"] == "2026-11-12" and empty["cutoff"] == "2026-11-11T00:00:00Z"
    assert pool.dump_scoreboard(empty) == pool.dump_scoreboard(pool.empty_scoreboard(NOW + 3_600_000))


def _board() -> dict:
    return copy.deepcopy(_fixture("scoreboard.json"))


def test_scoreboard_validator_catches_each_broken_promise():
    def broken(mutate, needle):
        doc = _board()
        mutate(doc)
        problems = pool.validate_scoreboard(doc)
        assert any(needle in p for p in problems), (needle, problems)

    broken(lambda d: d.update(cutoff=d["day"] + "T00:00:00Z"), "cutoff must be")          # under 24h late
    broken(lambda d: d["members"][0].update(net_usd=12.5), "keys must be exactly")
    broken(lambda d: d["counts"].update(equity_usd=1), "forbidden key")
    broken(lambda d: d.update(positions=[]), "top level keys")
    broken(lambda d: d["pool"][2].update(label="EDGE"), "EDGE under 10 trades")
    broken(lambda d: d["members"][-1].update(label="LEAK"), "LEAK under 10 trades")
    broken(lambda d: d["members"].reverse(), "ordered by n")
    broken(lambda d: d["members"].append(copy.deepcopy(d["members"][0])), "more than once")
    broken(lambda d: d["members"][0].update(explorer="https://example.com/x"), "short and explorer")
    broken(lambda d: d["pool"][1].update(dim="thesis_7d", key="thesis_7d=oil"), "is not published")
    broken(lambda d: d["pool"].reverse(), "'all' row")
    broken(lambda d: d["counts"].update(graded_trades=999), "must sum the rows")
    broken(lambda d: d["counts"].update(slices_at_min_n=0), "must match the pool rows")
    broken(lambda d: d.update(notes=["Trust me."]), "notes must be")
    broken(lambda d: d["pool"][0].update(mean_r="0.3"), "must be a number or null")
    broken(lambda d: d.update(schema=2), "schema must be")
    assert pool.validate_scoreboard([]) and pool.validate_scoreboard({})


def test_no_schema_key_is_itself_forbidden():
    """FORBIDDEN_KEY_PARTS must never match a key the schema requires."""
    keys = set(pool.Scoreboard.__annotations__) | set(pool.Counts.__annotations__) \
        | set(pool.SliceRow.__annotations__) | set(pool.MemberRow.__annotations__)
    for key in keys:
        assert not any(part in key.lower() for part in pool.FORBIDDEN_KEY_PARTS), key


def test_the_disclosure_lists_are_plain_and_say_the_main_things():
    said, unsaid = " ".join(pool.PUBLISHED), " ".join(pool.NOT_PUBLISHED)
    for needle in ("address", "date you joined", "label", "Pool totals"):
        assert needle in said, needle
    for needle in ("last 24 hours", "open position", "dollar", "Any key", "Nothing is uploaded"):
        assert needle in unsaid, needle
    assert (said + unsaid).isascii()


def test_notes_are_plain_and_carry_the_delay():
    text = " ".join(pool.NOTES)
    assert "at least 24 hours late" in text and "not advice" in text
    assert EM_DASH not in text and text.isascii()


# ── pinned to the code it describes ─────────────────────────────────────────

def test_labels_and_min_n_match_the_review_harness():
    from services.review import stats as S
    assert pool.LABELS == S.LABELS and pool.MIN_N_EDGE == S.MIN_N_EDGE


def test_published_dimensions_exist_in_the_review_and_skip_the_thesis_ones():
    from services.review import analysis as A
    assert set(pool.PUBLISHED_DIMENSIONS) <= set(A.DIM_FUNCS)
    assert pool.PUBLISHED_DIMENSIONS[0] == "all"
    assert not any("thesis" in d or d in ("rewrite", "cause", "pair", "risk")
                   for d in pool.PUBLISHED_DIMENSIONS)


def test_pool_coins_cover_the_desk_coins_the_review_grades():
    from services.strategies.evals import loss_audit as LA
    for coin, desk in LA.DESK_OF.items():
        assert pool.POOL_DESK_OF[coin] == desk
    assert pool.POOL_COINS == tuple(pool.POOL_DESK_OF)
    assert pool.ms_from_iso(pool.HOUSE_COUNT_FROM) == LA.SINCE_MS


def test_mcp_tools_handlers_and_classes_agree():
    names = [t["name"] for t in pool.MCP_TOOLS]
    assert names == ["pool_status", "pool_report", "pool_join", "pool_leave"]
    assert set(names) == set(pool.MCP_HANDLERS) == set(pool.MCP_TOOL_CLASS)
    assert set(pool.MCP_TOOL_CLASS.values()) <= {"local_read", "account_read", "local_write"}
    for tool in pool.MCP_TOOLS:
        assert set(tool) == {"name", "description", "inputSchema"}
        assert tool["inputSchema"]["type"] == "object"
        assert EM_DASH not in tool["description"] and len(tool["description"]) < 420
    for name in ("pool_join", "pool_leave"):
        desc = next(t["description"] for t in pool.MCP_TOOLS if t["name"] == name)
        assert "SIGNS" in desc and "no money moves" in desc and "signs nothing" in desc
    assert all(m.startswith("services.pool.") for m, _ in pool.MCP_HANDLERS.values())
    assert all(m.startswith("services.pool.") for m in pool.CLI_COMMANDS.values())
    assert set(pool.CLI_COMMANDS) == {"verify", "join", "leave", "status", "build", "report"}


def test_mcp_handler_imports_lazily(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")           # the only install kind this ever runs on
    import types
    fake = types.ModuleType("services.pool.join_tools")
    fake.handle_pool_status = lambda params: json.dumps({"seen": params})
    handler = pool.mcp_handler("pool_status")                 # built without the module existing
    monkeypatch.setitem(sys.modules, "services.pool.join_tools", fake)
    assert json.loads(handler({"a": 1})) == {"seen": {"a": 1}}
    with pytest.raises(KeyError):
        pool.mcp_handler("pool_trade")


def test_mcp_handler_refuses_on_an_operator_install_without_importing_anything(monkeypatch):
    """HIGH finding (2026-11-13): mcp_handler used to import and run the real
    handler on every install kind; only tools/list (server_tools()) differed.
    Every one of the four tools must refuse by itself, before
    importlib.import_module ever runs, regardless of the caller (the MCP
    server, a different client, a raw script)."""
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)       # the operator's own checkout
    assert not runtime.is_public()
    for name in pool.MCP_HANDLERS:
        module, _func = pool.MCP_HANDLERS[name]
        monkeypatch.delitem(sys.modules, module, raising=False)
        handler = pool.mcp_handler(name)
        out = json.loads(handler({"confirm": True}))
        assert out == {"ok": False, "code": "operator_install", "error": pool.OPERATOR_INSTALL_MESSAGE}
        assert module not in sys.modules, f"{name}: {module} was imported despite the refusal"


def test_contract_module_is_stdlib_plus_runtime_at_import():
    """Module-level imports: stdlib and contracts.runtime only. eth_account
    is imported inside recover_signer, so reading the contract costs nothing."""
    src = (Path(pool.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top.append((node.module or "").split(".")[0])
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    assert set(top) - stdlib == {"contracts"}, sorted(set(top) - stdlib)
    assert "from contracts import runtime" in src
