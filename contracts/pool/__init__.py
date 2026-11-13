"""Research pool contract: who is a member, what they signed, what is published.

The research pool is opt-in. A member registers a Hyperliquid address by a
signed statement; the pool grades that address's PUBLIC fills with the same
harness as services/review and publishes graded rows at least 24 hours late.
Nobody uploads anything and nothing here places an order.

Every unit of services/pool codes against this module and nowhere else:

  * the statement a member signs, byte for byte (`statement_text`)
  * the member file and how it is verified offline (`verify_statement`,
    `load_statement`, `scan`, `active_members`)
  * the rules that need no network: the 24 hour delay (`cutoff_ms`), the
    freshness window, the agent approval decision on two venue replies
    (`agent_approved`), which trades count as one independent bet
    (`cluster_ids`), and the one rule a pull request must pass before an
    address may become active (`reinstatement_problem`)
  * the venue request shapes (`fills_request`, `orders_request`,
    `extra_agents_request`, `user_role_request`)
  * the published scoreboard schema and its validator (`validate_scoreboard`)
  * the names that glue units together: MCP tools, CLI commands, paths

Stdlib only, plus one lazy import of eth_account inside `recover_signer`
(already in requirements.lock). Imports contracts.runtime and nothing from
pathiel or services, so any unit can depend on it. No function here reads the
network or a key.

See README.md and docs/overhaul/RESEARCH_POOL_2026-11-12.md.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypedDict
from urllib.parse import quote

from contracts import runtime

CONTRACT_VERSION = "1.1.0"

# ── identity of the pool ────────────────────────────────────────────────────
STATEMENT_VERSION = 1            # the signed text; a change needs every member to sign again
FILE_SCHEMA = 1                  # the member / leave / house file
SCOREBOARD_SCHEMA = 1            # docs/scoreboard/scoreboard.json
NETWORK = "mainnet"              # the pool is mainnet only: testnet fills are not a record
NETWORK_LABEL = "hyperliquid-mainnet"
REPO_SLUG = "Julian-dev28/pathiel"
PROJECT = "github.com/" + REPO_SLUG
DEFAULT_BRANCH = "main"

# ── repo layout (paths relative to the repo root, forward slashes) ──────────
POOL_DIR = "pool"
MEMBERS_SUBDIR = "members"       # pool/members/<address>.json  signed join
LEAVES_SUBDIR = "leaves"         # pool/leaves/<address>.json   signed leave
HOUSE_FILE = "house.json"        # pool/house.json              the maintainer's own record
SCOREBOARD_DIR = "docs/scoreboard"
SCOREBOARD_JSON = "scoreboard.json"
SCOREBOARD_HTML = "index.html"
SCOREBOARD_URL = "https://julian-dev28.github.io/pathiel/scoreboard/"
EXPLORER_URL = "https://app.hyperliquid.xyz/explorer/address/{address}"

# ── local state layout (under the state dir, see services/pool/paths.py) ────
STATE_SUBDIR = "pool"            # <state>/pool/
OUTBOX_SUBDIR = "outbox"         # <state>/pool/outbox/{members,leaves}/<address>.json
LEDGER_SUBDIR = "ledger"         # <state>/pool/ledger/<address>.jsonl

# ── rules ───────────────────────────────────────────────────────────────────
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
DELAY_MS = DAY_MS                # nothing closed inside the last 24h is ever published
FRESH_MAX_AGE_S = 7 * 86_400     # a statement in a pull request is at most this old
FRESH_FUTURE_SKEW_S = 600        # and at most this far ahead of the checker's clock
CONFIRM_MAX_AGE_S = 900          # dry run to confirm: the shown time stays signable this long
CLUSTER_SPAN_MS = DAY_MS         # same coin and side, entered within this long of the
                                 # cluster's FIRST entry: one independent bet (see cluster_ids)
MIN_N_EDGE = 10                  # services.review.stats.MIN_N_EDGE (pinned by a contract test)
LABELS = ("EDGE", "LEAK", "LEANING+", "LEANING-", "FLUKE", "NOISE")
MAX_FILE_BYTES = 4096

# The desks' coins. Fills on any other coin are dropped before anything is
# computed or written.
POOL_DESK_OF: Dict[str, str] = {
    "xyz:BRENTOIL": "oil", "xyz:CL": "oil", "xyz:SILVER": "metals", "xyz:GOLD": "metals",
}
POOL_COINS: Tuple[str, ...] = tuple(POOL_DESK_OF)

# services.review.analysis dimensions the pool publishes. The thesis ones are
# absent on purpose: the pool never reads a thesis.
PUBLISHED_DIMENSIONS: Tuple[str, ...] = (
    "all", "desk", "side", "week", "hour", "planned_r", "range", "vs_20d_trend")

# The maintainer's record is the pool's seed row. Its start is the loss
# audit's fixed start date, set before the pool existed.
HOUSE_COUNT_FROM = "2026-10-20T00:00:00Z"

NOTES: Tuple[str, ...] = (
    "Graded results of public fills, at least 24 hours late. Not live positions, not advice.",
    "No edge is demonstrated until a row with 10 or more independent trades says EDGE.",
    "Rows are ordered by number of graded trades, not by result.",
)

# What a member is told before signing, word for word, in the join tool, the
# /pathiel:share command and docs/PRIVACY.md.
PUBLISHED: Tuple[str, ...] = (
    "Your account address, shortened, with a link to its public explorer page.",
    "The date you joined.",
    "How many trades you closed on the pool coins since you joined, and how many were graded.",
    "Your graded row: win rate, mean result in R, their ranges, a p value, a label "
    "(EDGE, LEAK, LEANING, FLUKE or NOISE) and a luck score.",
    "Pool totals that include your trades.",
)
NOT_PUBLISHED: Tuple[str, ...] = (
    "Anything closed in the last 24 hours, and any open position.",
    "Single trades, prices, sizes, dollar results, account value or leverage.",
    "Fills on coins outside the pool coins.",
    "Any key. Nothing is uploaded: pathiel reads fills that Hyperliquid already makes public.",
)

# What the scoreboard may never carry, as a substring of any key at any depth:
# dollars, prices, sizes, positions, leverage, trade-level timestamps.
FORBIDDEN_KEY_PARTS: Tuple[str, ...] = (
    "usd", "equity", "px", "price", "size", "position", "leverage", "entry", "exit", "fill",
    "trades_list", "open",
)

# ── glue names ──────────────────────────────────────────────────────────────
# python -m services.pool <command> ...  ->  <module>.main(argv) -> int
CLI_COMMANDS: Dict[str, str] = {
    "verify": "services.pool.verify_cli",
    "join": "services.pool.join_cli",
    "leave": "services.pool.join_cli",
    "status": "services.pool.join_cli",
    "build": "services.pool.build_cli",
    "report": "services.pool.report_cli",
}

# tool name -> (module, function). Each handler takes the arguments dict and
# returns a JSON string. The MCP server imports the module on first call.
MCP_HANDLERS: Dict[str, Tuple[str, str]] = {
    "pool_status": ("services.pool.join_tools", "handle_pool_status"),
    "pool_report": ("services.pool.report_tool", "handle_pool_report"),
    "pool_join": ("services.pool.join_tools", "handle_pool_join"),
    "pool_leave": ("services.pool.join_tools", "handle_pool_leave"),
}

# tool name -> the MCP server's tool class (scripts/pathiel-mcp-server.py TOOL_CLASSES).
MCP_TOOL_CLASS: Dict[str, str] = {
    "pool_status": "local_read", "pool_report": "account_read",
    "pool_join": "local_write", "pool_leave": "local_write",
}

MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "pool_status",
        "description": (
            "Research pool (opt-in): whether this account has joined, what is published, "
            "the pool's counts and how to leave. Reads local files only."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pool_report",
        "description": (
            "Your own graded trade report from your public fills since you joined the "
            "research pool: fluke or edge, with ranges. Members only. Read only; this "
            "call publishes nothing."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pool_join",
        "description": (
            "Research pool join. SIGNS a statement with your key; no order, no money moves. "
            "Call only when the user asked to join. Without confirm it shows the exact "
            "statement and signs nothing. With confirm=true and the joined_at it returned "
            "it signs that statement and writes the member file for a pull request."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean",
                            "description": "true only after the user read the statement and said yes"},
                "joined_at": {"type": "string",
                              "description": "the joined_at value the dry run returned"},
            },
        },
    },
    {
        "name": "pool_leave",
        "description": (
            "Research pool leave. SIGNS a statement with your key; no order, no money moves. "
            "Call only when the user asked to leave. Without confirm it shows the exact "
            "statement and signs nothing. With confirm=true and the left_at it returned it "
            "signs that statement and writes the leave file for a pull request."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean",
                            "description": "true only after the user read the statement and said yes"},
                "left_at": {"type": "string",
                            "description": "the left_at value the dry run returned"},
            },
        },
    },
]


# ── types ───────────────────────────────────────────────────────────────────

class PoolError(ValueError):
    """A rule of this contract was broken. `code` is a short stable id."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Verified(TypedDict):
    """One statement file that passed `verify_statement`."""
    kind: str                    # "join" | "leave"
    address: str                 # the account, 0x + 40 lowercase hex
    signer: str                  # who signed, same form
    signer_role: str             # "master" (signer == address) | "agent"
    at: str                      # joined_at or left_at, YYYY-MM-DDTHH:MM:SSZ
    at_ms: int


class ActiveMember(TypedDict):
    """One address the scoreboard build grades."""
    address: str
    kind: str                    # "member" | "house"
    signer_role: Optional[str]   # "master" | "agent"; None for the house
    count_from: str              # ISO; only trades entered at or after it count
    count_from_ms: int


class ScanProblem(TypedDict):
    path: str                    # relative to the pool root
    code: str
    message: str


class PoolScan(TypedDict):
    house: Optional[ActiveMember]
    members: List[ActiveMember]  # sorted by address; never holds the house address
    left: List[str]              # addresses with a leave in force, sorted
    pending: List[str]           # joined after `as_of_ms`, sorted
    problems: List[ScanProblem]


class Membership(TypedDict):
    """One address's standing in one pool root (the repo's pool/ or an outbox)."""
    address: str
    state: str                   # "none" | "member" | "left" | "invalid"
    join: Optional[Verified]
    leave: Optional[Verified]
    count_from_ms: Optional[int]
    problems: List[ScanProblem]


class Standing(TypedDict):
    """One address's standing on this machine: the repo copy and the local
    outbox read together (`local_standing`)."""
    address: str
    state: str                   # see STANDING_STATES
    count_from_ms: Optional[int]  # set when the address may be graded
    join: Optional[Verified]     # the join in force, wherever it sits
    leave: Optional[Verified]
    problems: List[ScanProblem]


class SliceRow(TypedDict):
    """One pool-level slice. Stats are over independent trades (clusters)."""
    key: str                     # "all" or "<dim>=<value>"
    dim: str
    value: str
    n: int                       # independent trades with an R
    n_trades: int                # member trades behind them
    members: int                 # distinct addresses behind them
    wins: int
    win_rate: Optional[float]
    win_lo: Optional[float]
    win_hi: Optional[float]
    mean_r: Optional[float]
    r_lo: Optional[float]
    r_hi: Optional[float]
    p: Optional[float]
    label: str


class MemberRow(TypedDict):
    address: str
    short: str
    explorer: str
    kind: str                    # "member" | "house"
    signer_role: Optional[str]
    since: str                   # UTC date the count starts, YYYY-MM-DD
    closed_trades: int           # closed before the cutoff
    n: int                       # of those, graded (an opening stop gives them an R)
    wins: int
    win_rate: Optional[float]
    win_lo: Optional[float]
    win_hi: Optional[float]
    mean_r: Optional[float]
    r_lo: Optional[float]
    r_hi: Optional[float]
    p: Optional[float]
    label: str
    luck_median: Optional[float]  # median random-timing percentile, 0 to 100
    luck_n: int


class Counts(TypedDict):
    members: int                 # signed members with a row
    house: int                   # 0 or 1
    left: int                    # leave statements in force
    closed_trades: int
    graded_trades: int           # THE outcome metric: 19 on 2026-11-12, the maintainer alone
    independent_trades: int
    slices: int                  # pool rows, the "all" row included
    slices_at_min_n: int         # pool rows with n >= MIN_N_EDGE
    edge: int
    leak: int


class Scoreboard(TypedDict):
    schema: int
    network: str
    day: str                     # UTC date of the build, YYYY-MM-DD
    cutoff: str                  # 00:00:00Z of the day before `day`
    delay_hours: int
    min_n_edge: int
    counts: Counts
    pool: List[SliceRow]         # "all" first
    members: List[MemberRow]     # most graded trades first, then address
    notes: List[str]


_STATEMENT_KEYS = {
    "join": ("schema", "kind", "network", "address", "signer", "joined_at", "message", "signature"),
    "leave": ("schema", "kind", "network", "address", "signer", "left_at", "message", "signature"),
}
_TIME_KEY = {"join": "joined_at", "leave": "left_at"}
_HOUSE_KEYS = ("schema", "kind", "network", "address", "count_from", "label")
_SCOREBOARD_KEYS = tuple(Scoreboard.__annotations__)
_COUNT_KEYS = tuple(Counts.__annotations__)
_SLICE_KEYS = tuple(SliceRow.__annotations__)
_MEMBER_KEYS = tuple(MemberRow.__annotations__)

_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_SIGNATURE_RE = re.compile(r"^0x[0-9a-f]{130}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── small pure helpers ──────────────────────────────────────────────────────

def normalize_address(raw: Any) -> str:
    """0x + 40 lowercase hex, or PoolError('address')."""
    text = str(raw or "").strip().lower()
    if not _ADDRESS_RE.match(text):
        raise PoolError("address", f"not a 0x address of 40 hex characters: {raw!r}")
    return text


def short_address(address: str) -> str:
    a = str(address or "")
    return f"{a[:6]}...{a[-4:]}" if len(a) > 12 else a


def explorer_url(address: str) -> str:
    return EXPLORER_URL.format(address=normalize_address(address))


def iso_from_ms(ms: int) -> str:
    """UTC, second precision, the only time format in pool files."""
    return datetime.fromtimestamp(int(ms) // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_from_iso(text: Any) -> int:
    """Strict inverse of `iso_from_ms`. PoolError('time') on anything else."""
    s = str(text or "")
    if not _ISO_RE.match(s):
        raise PoolError("time", f"time must look like 2026-11-12T14:03:22Z, got {text!r}")
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise PoolError("time", f"not a real UTC time: {text!r}") from e
    return int(dt.timestamp()) * 1000


def day_of(ms: int) -> str:
    return iso_from_ms(ms)[:10]


def cutoff_ms(as_of_ms: int) -> int:
    """The 24 hour delay rule. A trade is published only if it closed before
    this: 00:00:00Z of the day before the build's UTC day, so every published
    trade is between 24 and 48 hours old at least. Two builds on one UTC day
    share a cutoff, which keeps the scoreboard byte-identical across them."""
    return ((int(as_of_ms) - DELAY_MS) // DAY_MS) * DAY_MS


def publishable(exit_ms: Optional[int], cutoff: int) -> bool:
    """Closed, and closed before the cutoff. An open trade never is."""
    return exit_ms is not None and int(exit_ms) < int(cutoff)


def fresh_problem(at_ms: int, now_ms: int) -> Optional[str]:
    """Why a statement's time is not acceptable in a pull request, or None.
    Bounds how far a join can be backdated to pick up a good week.

    `now_ms` is the clock of whoever runs the check, so the bound holds at
    CHECK time, not at merge time: a pull request that passed and then sat
    open is not checked again unless the check is run again. The maintainer's
    merge checklist covers that (docs/overhaul/RESEARCH_POOL_2026-11-12.md,
    "What Julien must do")."""
    if at_ms > now_ms + FRESH_FUTURE_SKEW_S * 1000:
        return f"statement time {iso_from_ms(at_ms)} is in the future"
    if at_ms < now_ms - FRESH_MAX_AGE_S * 1000:
        return (f"statement time {iso_from_ms(at_ms)} is more than "
                f"{FRESH_MAX_AGE_S // 86_400} days old: sign a new one")
    return None


# ── the signed statement ────────────────────────────────────────────────────

def statement_text(kind: str, address: str, signer: str, at: str) -> str:
    """The exact text a member signs (EIP-191 personal message, UTF-8, LF line
    ends, no trailing newline). ASCII only. Changing one character here is a
    STATEMENT_VERSION bump: every stored signature stops verifying."""
    address, signer = normalize_address(address), normalize_address(signer)
    ms_from_iso(at)
    head = [
        f"pathiel research pool: {kind}",
        f"Version: {STATEMENT_VERSION}",
        f"Network: {NETWORK_LABEL}",
        f"Account: {address}",
        f"Signer: {signer}",
    ]
    if kind == "join":
        body = [
            f"Joined at: {at}",
            f"Project: {PROJECT}",
            "",
            "I am the owner of this Hyperliquid account, or an API wallet it approved.",
            "I ask pathiel to grade this account's public fills on the pool coins from the time above.",
            "I agree that the graded results are published at least 24 hours late on the public "
            "scoreboard, next to this address.",
            "No trade, transfer, fee or payment is authorized by this statement.",
            "I can leave at any time with a signed leave statement.",
        ]
    elif kind == "leave":
        body = [
            f"Left at: {at}",
            f"Project: {PROJECT}",
            "",
            "I ask pathiel to remove this account from the research pool.",
            "The next scoreboard build drops this address and its graded results.",
            "No trade, transfer, fee or payment is authorized by this statement.",
        ]
    else:
        raise PoolError("kind", f"kind must be join or leave, got {kind!r}")
    return "\n".join(head + body)


def statement_sha256(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def recover_signer(message: str, signature: str) -> str:
    """The address whose key made `signature` over `message` as an EIP-191
    personal message (eth_account encode_defunct(text=message)), lowercase.
    Signing uses the same encoding: Account.sign_message(encode_defunct(text=
    message), key). An EIP-191 signature can never be replayed as a
    Hyperliquid action, which is EIP-712 typed data."""
    if not isinstance(signature, str) or not _SIGNATURE_RE.match(signature):
        raise PoolError("signature_format", "signature must be 0x and 130 lowercase hex characters")
    from eth_account import Account
    from eth_account.messages import encode_defunct
    try:
        return str(Account.recover_message(encode_defunct(text=message), signature=signature)).lower()
    except Exception as e:   # noqa: BLE001 - any failure to recover is one answer: not valid
        raise PoolError("signature", f"signature does not recover: {type(e).__name__}") from e


def build_statement_file(kind: str, address: str, signer: str, at: str, signature: str) -> Dict[str, Any]:
    """The member (kind 'join') or leave file as a dict, keys in file order."""
    if kind not in _STATEMENT_KEYS:
        raise PoolError("kind", f"kind must be join or leave, got {kind!r}")
    address, signer = normalize_address(address), normalize_address(signer)
    return {
        "schema": FILE_SCHEMA, "kind": kind, "network": NETWORK, "address": address,
        "signer": signer, _TIME_KEY[kind]: at,
        "message": statement_text(kind, address, signer, at), "signature": signature,
    }


def dump_statement_file(doc: Dict[str, Any]) -> str:
    """The file's text: two-space JSON, keys in file order, one trailing newline."""
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def statement_filename(address: str) -> str:
    return normalize_address(address) + ".json"


def verify_statement(doc: Any, *, kind: Optional[str] = None,
                     filename: Optional[str] = None) -> Verified:
    """Offline verification of one statement file. Raises PoolError with the
    first rule broken, in this order:

      not_object        the file is not a JSON object
      kind              kind is not join or leave, or not the `kind` expected
      keys              the key set is not exactly the file's keys
      schema            schema is not FILE_SCHEMA
      network           network is not 'mainnet'
      address, signer   not 0x + 40 lowercase hex, stored lowercase
      time              joined_at / left_at is not a real YYYY-MM-DDTHH:MM:SSZ
      message           message is not statement_text(...) for these fields
      signature_format  not 0x + 130 lowercase hex
      signature         the recovered signer is not `signer`
      filename          the file is not named <address>.json

    Whether an agent signer is approved by the account is NOT checked here:
    that needs the venue (`agent_approved`) and is done once, when the pull
    request is checked."""
    if not isinstance(doc, dict):
        raise PoolError("not_object", "the file must hold one JSON object")
    k = doc.get("kind")
    if k not in _STATEMENT_KEYS or (kind is not None and k != kind):
        raise PoolError("kind", f"kind is {k!r}, expected {kind or 'join or leave'}")
    want = _STATEMENT_KEYS[k]
    if tuple(sorted(doc)) != tuple(sorted(want)):
        raise PoolError("keys", f"keys must be exactly {list(want)}, got {sorted(doc)}")
    if doc["schema"] != FILE_SCHEMA or isinstance(doc["schema"], bool):
        raise PoolError("schema", f"schema must be {FILE_SCHEMA}")
    if doc["network"] != NETWORK:
        raise PoolError("network", f"network must be {NETWORK!r}: the pool is mainnet only")
    for field in ("address", "signer"):
        value = doc[field]
        if not isinstance(value, str) or not _ADDRESS_RE.match(value):
            raise PoolError(field, f"{field} must be 0x and 40 lowercase hex characters")
    at = doc[_TIME_KEY[k]]
    at_ms = ms_from_iso(at)
    if doc["message"] != statement_text(k, doc["address"], doc["signer"], at):
        raise PoolError("message", "message is not the statement for this address, signer and time")
    if recover_signer(doc["message"], doc["signature"]) != doc["signer"]:
        raise PoolError("signature", "the signature was not made by the signer named in the file")
    if filename is not None and filename != statement_filename(doc["address"]):
        raise PoolError("filename", f"the file must be named {statement_filename(doc['address'])}")
    return {"kind": k, "address": doc["address"], "signer": doc["signer"],
            "signer_role": "master" if doc["signer"] == doc["address"] else "agent",
            "at": at, "at_ms": at_ms}


def load_statement(path: str, kind: str) -> Verified:
    """Read and verify one statement file. The name must be <address>.json."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        raise PoolError("unreadable", f"cannot read the file: {type(e).__name__}") from e
    if len(raw) > MAX_FILE_BYTES:
        raise PoolError("too_big", f"the file is over {MAX_FILE_BYTES} bytes")
    try:
        doc = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PoolError("not_json", "the file is not ASCII JSON") from e
    return verify_statement(doc, kind=kind, filename=os.path.basename(path))


# ── the pool on disk ────────────────────────────────────────────────────────

def repo_pool_root(repo_root: str) -> str:
    return os.path.join(repo_root, POOL_DIR)


def member_path(pool_root: str, address: str) -> str:
    return os.path.join(pool_root, MEMBERS_SUBDIR, statement_filename(address))


def leave_path(pool_root: str, address: str) -> str:
    return os.path.join(pool_root, LEAVES_SUBDIR, statement_filename(address))


def member_repo_path(address: str) -> str:
    return f"{POOL_DIR}/{MEMBERS_SUBDIR}/{statement_filename(address)}"


def leave_repo_path(address: str) -> str:
    return f"{POOL_DIR}/{LEAVES_SUBDIR}/{statement_filename(address)}"


def load_house(pool_root: str) -> Optional[ActiveMember]:
    """pool/house.json, or None when the file is absent. The house is the
    maintainer's own account: the address must be the project's published
    builder address (contracts.runtime.DEFAULT_BUILDER_ADDRESS), so nobody
    can add another address as a house row by pull request. It is not signed:
    the maintainer commits it to his own repo. Delete the file to drop the row."""
    path = os.path.join(pool_root, HOUSE_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read(MAX_FILE_BYTES + 1).decode("ascii"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise PoolError("not_json", "house.json is not ASCII JSON") from e
    if not isinstance(doc, dict) or tuple(sorted(doc)) != tuple(sorted(_HOUSE_KEYS)):
        raise PoolError("keys", f"house.json keys must be exactly {list(_HOUSE_KEYS)}")
    if doc["schema"] != FILE_SCHEMA or doc["kind"] != "house" or doc["network"] != NETWORK:
        raise PoolError("schema", "house.json must be schema 1, kind house, network mainnet")
    if doc["address"] != runtime.DEFAULT_BUILDER_ADDRESS.lower() or not _ADDRESS_RE.match(doc["address"]):
        raise PoolError("address", "house.json must name the project's published builder address")
    count_from_ms = ms_from_iso(doc["count_from"])
    if count_from_ms < ms_from_iso(HOUSE_COUNT_FROM):
        raise PoolError("time", f"house.json count_from may not be before {HOUSE_COUNT_FROM}")
    if not isinstance(doc["label"], str) or not doc["label"]:
        raise PoolError("keys", "house.json label must be a non-empty string")
    return {"address": doc["address"], "kind": "house", "signer_role": None,
            "count_from": doc["count_from"], "count_from_ms": count_from_ms}


def _scan_dir(pool_root: str, subdir: str, kind: str,
              problems: List[ScanProblem]) -> Dict[str, Verified]:
    out: Dict[str, Verified] = {}
    folder = os.path.join(pool_root, subdir)
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if name == ".gitkeep":
            continue
        rel = f"{subdir}/{name}"
        try:
            v = load_statement(os.path.join(folder, name), kind)
        except PoolError as e:
            problems.append({"path": rel, "code": e.code, "message": str(e)})
            continue
        out[v["address"]] = v
    return out


def scan(pool_root: str, as_of_ms: Optional[int] = None) -> PoolScan:
    """Everything a pool root says, offline. Rules:

      * an address is a member when its join verifies and no leave is in force
      * a leave is in force when it verifies and left_at >= joined_at (a tie
        leaves: the safe side). A later join puts the address back, counting
        from the new joined_at
      * a join dated after `as_of_ms` is pending, not active
      * the house address has exactly one row, the house's; member and leave
        files for it are ignored
      * a file that does not verify is a problem, never a silent skip

    What this cannot see: a file that was DELETED. A scan reads the files
    that are there. Take pool/leaves/<address>.json away while the old member
    file stays and the address is a member again, counting from the OLD
    joined_at, with no new signature. So a change to pool/ is judged before
    it lands, as a move from one state to the next: `reinstatement_problem`,
    which the pull request check runs on every address a pull request touches.
    """
    problems: List[ScanProblem] = []
    joins = _scan_dir(pool_root, MEMBERS_SUBDIR, "join", problems)
    leaves = _scan_dir(pool_root, LEAVES_SUBDIR, "leave", problems)
    house: Optional[ActiveMember] = None
    try:
        house = load_house(pool_root)
    except PoolError as e:
        problems.append({"path": HOUSE_FILE, "code": e.code, "message": str(e)})
    house_address = house["address"] if house else None
    members: List[ActiveMember] = []
    left: List[str] = []
    pending: List[str] = []
    for address in sorted(set(joins) | set(leaves)):
        if address == house_address:
            continue
        join, leave = joins.get(address), leaves.get(address)
        if join is None or (leave is not None and leave["at_ms"] >= join["at_ms"]):
            left.append(address)
        elif as_of_ms is not None and join["at_ms"] > as_of_ms:
            pending.append(address)
        else:
            members.append({"address": address, "kind": "member", "signer_role": join["signer_role"],
                            "count_from": join["at"], "count_from_ms": join["at_ms"]})
    return {"house": house, "members": members, "left": left, "pending": pending, "problems": problems}


def active_members(pool_root: str, as_of_ms: int) -> List[ActiveMember]:
    """The addresses a build grades: the house first when there is one, then
    members by address. Fails closed: PoolError('invalid_pool') when any file
    under the pool root does not verify, so a broken file can neither add nor
    silently drop a row."""
    found = scan(pool_root, as_of_ms)
    if found["problems"]:
        detail = "; ".join(f"{p['path']}: {p['code']}" for p in found["problems"])
        raise PoolError("invalid_pool", f"pool files do not verify: {detail}")
    return ([found["house"]] if found["house"] else []) + found["members"]


def membership(pool_root: str, address: str) -> Membership:
    """One address's standing in one pool root. Works on the repo's pool/ and
    on a local outbox, which mirrors it (members/ and leaves/ inside)."""
    address = normalize_address(address)
    problems: List[ScanProblem] = []
    found: Dict[str, Optional[Verified]] = {"join": None, "leave": None}
    for kind, path in (("join", member_path(pool_root, address)),
                       ("leave", leave_path(pool_root, address))):
        if not os.path.exists(path):
            continue
        try:
            found[kind] = load_statement(path, kind)
        except PoolError as e:
            problems.append({"path": os.path.relpath(path, pool_root).replace(os.sep, "/"),
                             "code": e.code, "message": str(e)})
    join, leave = found["join"], found["leave"]
    if problems:
        state = "invalid"
    elif join is None:
        state = "left" if leave is not None else "none"
    elif leave is not None and leave["at_ms"] >= join["at_ms"]:
        state = "left"
    else:
        state = "member"
    return {"address": address, "state": state, "join": join, "leave": leave,
            "count_from_ms": join["at_ms"] if state == "member" and join else None,
            "problems": problems}


# house               the maintainer's row (pool/house.json names this address)
# member              a join is in force in the repo copy
# signed_not_merged   a join signed on this machine is newer than the repo copy knows
# leave_signed_not_merged   the repo copy says member, a leave signed here is in force
# left                a leave is in force
# none                no statement anywhere
# invalid             a file for this address does not verify (see problems)
STANDING_STATES = ("house", "member", "signed_not_merged", "leave_signed_not_merged", "left",
                   "none", "invalid")
GRADED_STATES = ("house", "member", "signed_not_merged")


def local_standing(repo_pool: str, outbox: str, address: str) -> Standing:
    """What this machine knows about `address`: the repo copy of pool/ (stale
    until the next pull or plugin update) and the outbox of statements signed
    here. The newest valid statement of either kind wins; a leave at or after
    the join wins the tie. Every unit that asks "is this account in the pool,
    and from when" asks here, so the tools agree."""
    address = normalize_address(address)
    repo, out = membership(repo_pool, address), membership(outbox, address)
    problems = [{**p, "path": f"{where}:{p['path']}"} for where, m in (("repo", repo), ("outbox", out))
                for p in m["problems"]]
    base: Standing = {"address": address, "state": "none", "count_from_ms": None, "join": None,
                      "leave": None, "problems": problems}
    try:
        house = load_house(repo_pool)
    except PoolError:
        house = None
    if house is not None and house["address"] == address:
        return {**base, "state": "house", "count_from_ms": house["count_from_ms"]}
    if problems:
        return {**base, "state": "invalid"}
    joins = [m["join"] for m in (repo, out) if m["join"] is not None]
    leaves = [m["leave"] for m in (repo, out) if m["leave"] is not None]
    join = max(joins, key=lambda v: v["at_ms"]) if joins else None
    leave = max(leaves, key=lambda v: v["at_ms"]) if leaves else None
    base = {**base, "join": join, "leave": leave}
    if join is None:
        return {**base, "state": "left" if leave is not None else "none"}
    if leave is not None and leave["at_ms"] >= join["at_ms"]:
        return {**base, "state": "leave_signed_not_merged" if repo["state"] == "member" else "left"}
    in_repo = repo["state"] == "member" and repo["join"] is not None and repo["join"]["at_ms"] == join["at_ms"]
    return {**base, "state": "member" if in_repo else "signed_not_merged", "count_from_ms": join["at_ms"]}


# ── venue reads (shapes only; the POST lives in services/pool/venue.py) ─────
#
# Confirmed against hyperliquid-python-sdk 0.24.0 (hyperliquid/info.py:
# user_fills_by_time, historical_orders, extra_agents, user_role) and the
# Hyperliquid info endpoint docs, 2026-11-12. All four are public reads that
# take a plain address and need no key.

FILLS_PAGE = 2000                # userFillsByTime returns at most this many per reply


def fills_request(address: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """Reply: a list of fills, oldest first, each with coin, px, sz, side
    ('B' | 'A'), time (ms), startPosition, dir, closedPnl, fee, oid, tid,
    hash, crossed, feeToken and, only when one was charged, builderFee. Both
    time bounds are inclusive. A reply of FILLS_PAGE rows means there may be
    more: ask again from the last row's time and drop rows already seen (by
    tid). The venue keeps only the 10000 most recent fills of an account."""
    return {"type": "userFillsByTime", "user": normalize_address(address),
            "startTime": int(start_ms), "endTime": int(end_ms), "aggregateByTime": False}


def orders_request(address: str) -> Dict[str, Any]:
    """Reply: at most the 2000 most recent orders, each {order: {coin, side,
    limitPx, sz, oid, timestamp, triggerPx, orderType, reduceOnly, ...},
    status, statusTimestamp}. The opening stop and target of a trade are read
    from here; an account with more than 2000 newer orders loses them."""
    return {"type": "historicalOrders", "user": normalize_address(address)}


def extra_agents_request(address: str) -> Dict[str, Any]:
    """Reply: [{name: str, address: str, validUntil: int ms}, ...], the API
    wallets the account has approved by name."""
    return {"type": "extraAgents", "user": normalize_address(address)}


def user_role_request(address: str) -> Dict[str, Any]:
    """Reply: {role: 'missing' | 'user' | 'agent' | 'vault' | 'subAccount'},
    and for an agent {role: 'agent', data: {user: <the account it signs for>}}."""
    return {"type": "userRole", "user": normalize_address(address)}


def agent_approved(address: str, signer: str, extra_agents_reply: Any, user_role_reply: Any,
                   now_ms: int) -> Tuple[bool, str]:
    """Has `address` approved `signer` as an API wallet, judged on the two
    venue replies (extraAgents for `address`, userRole for `signer`)? Pure.

      1. signer listed in extraAgents: approved unless validUntil has passed
         ('extraAgents' / 'expired'). The list is the last word when it names
         the signer.
      2. not listed: approved when userRole says the signer is an agent of
         exactly this account ('userRole'). This covers an API wallet the
         named list does not carry.
      3. otherwise not approved ('not_approved').

    A failed read is the caller's to raise: it must never reach here as an
    empty reply, or 'could not ask' would read as 'not approved'."""
    address, signer = normalize_address(address), normalize_address(signer)
    if isinstance(extra_agents_reply, list):
        for entry in extra_agents_reply:
            if not isinstance(entry, dict) or str(entry.get("address") or "").lower() != signer:
                continue
            until = entry.get("validUntil")
            if until in (None, "", 0) or int(until) > int(now_ms):
                return True, "extraAgents"
            return False, "expired"
    if isinstance(user_role_reply, dict) and user_role_reply.get("role") == "agent":
        data = user_role_reply.get("data")
        if isinstance(data, dict) and str(data.get("user") or "").lower() == address:
            return True, "userRole"
    return False, "not_approved"


def leave_signer_allowed(leave: Verified, join: Optional[Verified], agent_ok: bool) -> bool:
    """Who may sign a leave: the account itself, the same key that signed the
    join in force (no venue read needed, so a lapsed API wallet can still
    leave), or an API wallet the account approves now. Leaving is never
    harder than joining.

    Known and accepted: the second rule never asks the venue, so an API
    wallet the account has since REVOKED can still take the account out of
    the pool, at any later time, with the key that signed the join. That is
    the opposite direction of a revoked wallet staying in (`agent_approved`
    is checked once): here a key the owner no longer trusts ends the
    membership. The damage is bounded: a leave only removes published rows,
    it moves no money and reads nothing, and the account can rejoin with a
    new signed join (which then names the new signer, so the old key loses
    this power). The alternative, a venue read on every agent-signed leave,
    would lock out a member whose wallet expired."""
    if leave["signer_role"] == "master":
        return True
    if join is not None and join["address"] == leave["address"] and join["signer"] == leave["signer"]:
        return True
    return bool(agent_ok)


# ── independent trades ──────────────────────────────────────────────────────

def cluster_ids(rows: Sequence[Tuple[str, str, int]]) -> List[int]:
    """One id per (coin, side, entry_ms) row, in input order. Rows on the same
    coin and side entered within CLUSTER_SPAN_MS (24 hours) of the cluster's
    FIRST entry share an id: ten members taking the same desk call are one
    bet, not ten. The next entry after that window opens a new cluster and
    becomes its anchor. Ids are numbered from 0 in (coin, side, entry_ms)
    order. Pure.

    Why anchored on the first entry, not chained from the previous one: a
    chain (each entry within the window of the one before) has no bound. With
    desks ticking every 15 minutes and enough members, one coin and side never
    goes quiet, and its whole history folds into ONE cluster: the count of
    independent trades stops growing however much evidence comes in. Anchored,
    no cluster spans more than CLUSTER_SPAN_MS, and n grows with time, never
    with members: at most one cluster per coin, side and 24 hours.

    Why 24 hours, not 60 minutes: members who enter the same coin and side an
    hour apart hold through the same price move, so their results are not
    independent. Measured on a driftless walk with bracket trades (12 members,
    30 days, median hold 4 hours; services/pool/evals/cluster_eval.py), the
    share of 200 runs where pure noise is labelled EDGE or LEAK is 49.5% when
    every trade counts, 33% with a 60 minute window, and 5.5% with this one.
    The eval gates it at 10%.

    What it does not claim: that two clusters a day apart are independent
    when positions are held for days (the same eval measures 11% for a median
    hold of 12 hours), or that a Brent long and a WTI long are two bets."""
    order = sorted(range(len(rows)), key=lambda i: (rows[i][0], rows[i][1], int(rows[i][2]), i))
    ids = [0] * len(rows)
    current = -1
    anchor: Optional[Tuple[str, str, int]] = None    # the current cluster's coin, side, first entry
    for i in order:
        coin, side, entry_ms = rows[i][0], rows[i][1], int(rows[i][2])
        if anchor is None or (coin, side) != anchor[:2] or entry_ms - anchor[2] > CLUSTER_SPAN_MS:
            current += 1
            anchor = (coin, side, entry_ms)
        ids[i] = current
    return ids


# ── a change to the pool, judged before it lands ────────────────────────────

def reinstatement_problem(before: Membership, after: Membership,
                          held_ms: Sequence[int] = ()) -> Optional[str]:
    """Would this change put an account INTO the pool without a new signed
    join? None when it would not; otherwise the reason. Pure.

    `before` and `after` are one address's `membership` in the base branch's
    pool/ and in the pool/ the pull request would leave behind. `held_ms` is
    every joined_at and left_at the base branch has EVER held for the address
    (its git history of the two files), so a statement that was removed still
    counts.

    The rule: an address may be a member after the change only when

      * it was a member before, on the same join (nothing about it changed), or
      * its join is newer than every join and leave the base branch holds or
        has ever held for it: a statement nobody has seen before, signed after
        the last leave.

    What it refuses, each of which a plain scan of the files would accept:

      * a leave file deleted while the old member file stays (the account
        would be back from the OLD joined_at, re-grading the time its owner
        believed it was out)
      * a leave file replaced by an older one, dated before the join
      * a member file put back from git history after it was removed on
        request, or swapped for an earlier join to count from further back

    Removing a member (deleting the member file, adding a leave) never trips
    it: only becoming active needs consent, and consent is a new signature."""
    if after["state"] != "member" or after["join"] is None:
        return None
    join = after["join"]
    if before["state"] == "member" and before["join"] == join:
        return None
    held = [int(ms) for ms in held_ms]
    held += [v["at_ms"] for v in (before["join"], before["leave"]) if v is not None]
    if not held or join["at_ms"] > max(held):
        return None
    return (f"this change would put {short_address(join['address'])} back in the pool from "
            f"{join['at']} with no new consent: the repository already held a statement for it "
            f"dated {iso_from_ms(max(held))}. An account comes back only with a join signed after that")


# ── pull request link ───────────────────────────────────────────────────────

def pr_new_file_url(repo_path: str, content: str) -> str:
    """GitHub's prefilled new-file form for `repo_path` with `content`. For
    someone without write access it forks the repo and opens a pull request."""
    return (f"https://github.com/{REPO_SLUG}/new/{DEFAULT_BRANCH}"
            f"?filename={quote(repo_path, safe='/')}&value={quote(content, safe='')}")


# ── the scoreboard ──────────────────────────────────────────────────────────

def dump_scoreboard(doc: Dict[str, Any]) -> str:
    """The published bytes: sorted keys, one-space indent, trailing newline.
    Same doc, same bytes."""
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def _num_or_none(v: Any) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def _forbidden_keys(node: Any, trail: str = "") -> List[str]:
    hits: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            low = str(key).lower()
            if any(part in low for part in FORBIDDEN_KEY_PARTS):
                hits.append(f"{trail}{key}")
            hits += _forbidden_keys(value, f"{trail}{key}.")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits += _forbidden_keys(value, f"{trail}{i}.")
    return hits


def _row_problems(row: Any, keys: Tuple[str, ...], where: str) -> List[str]:
    if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(keys)):
        return [f"{where}: keys must be exactly {list(keys)}"]
    out: List[str] = []
    for key in ("win_rate", "win_lo", "win_hi", "mean_r", "r_lo", "r_hi", "p"):
        if not _num_or_none(row[key]):
            out.append(f"{where}.{key}: must be a number or null")
    for key in ("n", "wins"):
        if not isinstance(row[key], int) or isinstance(row[key], bool) or row[key] < 0:
            out.append(f"{where}.{key}: must be a whole number")
    if row["label"] not in LABELS:
        out.append(f"{where}.label: {row['label']!r} is not a label")
    elif row["label"] in ("EDGE", "LEAK") and isinstance(row["n"], int) and row["n"] < MIN_N_EDGE:
        out.append(f"{where}.label: {row['label']} under {MIN_N_EDGE} trades")
    return out


def validate_scoreboard(doc: Any) -> List[str]:
    """Every way `doc` breaks the published schema; [] when it is valid.

    Beyond shapes it pins the promises: the cutoff is midnight UTC of the day
    before the build day (the 24 hour delay), no key anywhere names dollars,
    prices, sizes, positions or trade-level detail (FORBIDDEN_KEY_PARTS), no
    row under MIN_N_EDGE trades is EDGE or LEAK, each address appears once,
    and rows are ordered by trade count then address, never by result."""
    if not isinstance(doc, dict) or tuple(sorted(doc)) != tuple(sorted(_SCOREBOARD_KEYS)):
        return [f"top level keys must be exactly {list(_SCOREBOARD_KEYS)}"]
    out: List[str] = []
    if doc["schema"] != SCOREBOARD_SCHEMA:
        out.append(f"schema must be {SCOREBOARD_SCHEMA}")
    if doc["network"] != NETWORK:
        out.append(f"network must be {NETWORK!r}")
    if doc["delay_hours"] != DELAY_MS // HOUR_MS or doc["min_n_edge"] != MIN_N_EDGE:
        out.append("delay_hours and min_n_edge must be the contract's")
    if not isinstance(doc["day"], str) or not _DATE_RE.match(doc["day"]):
        out.append("day must be YYYY-MM-DD")
    else:
        try:
            want = iso_from_ms(ms_from_iso(doc["day"] + "T00:00:00Z") - DAY_MS)
            if doc["cutoff"] != want:
                out.append(f"cutoff must be {want}, the midnight before the build day")
        except PoolError:
            out.append("day is not a real date")
    if doc["notes"] != list(NOTES):
        out.append("notes must be the contract's NOTES")
    out += [f"forbidden key: {k}" for k in _forbidden_keys(doc)]

    counts = doc["counts"]
    if not isinstance(counts, dict) or tuple(sorted(counts)) != tuple(sorted(_COUNT_KEYS)):
        out.append(f"counts keys must be exactly {list(_COUNT_KEYS)}")
        counts = None
    elif any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts.values()):
        out.append("counts must be whole numbers")
        counts = None

    pool = doc["pool"] if isinstance(doc["pool"], list) else None
    if pool is None:
        out.append("pool must be a list")
    else:
        for i, row in enumerate(pool):
            where = f"pool[{i}]"
            probs = _row_problems(row, _SLICE_KEYS, where)
            out += probs
            if probs:
                continue
            if row["dim"] not in PUBLISHED_DIMENSIONS:
                out.append(f"{where}.dim: {row['dim']!r} is not published")
            if row["key"] != ("all" if row["dim"] == "all" else f"{row['dim']}={row['value']}"):
                out.append(f"{where}.key does not match dim and value")
            if not (isinstance(row["n_trades"], int) and isinstance(row["members"], int)
                    and row["n_trades"] >= row["n"] and row["members"] >= (1 if row["n"] else 0)):
                out.append(f"{where}: n_trades and members must cover n")
        if pool and isinstance(pool[0], dict) and pool[0].get("key") != "all":
            out.append("pool[0] must be the 'all' row")
        keys = [r.get("key") for r in pool if isinstance(r, dict)]
        if len(keys) != len(set(keys)):
            out.append("pool rows must have distinct keys")

    members = doc["members"] if isinstance(doc["members"], list) else None
    if members is None:
        out.append("members must be a list")
    else:
        clean = []
        for i, row in enumerate(members):
            where = f"members[{i}]"
            probs = _row_problems(row, _MEMBER_KEYS, where)
            out += probs
            if probs:
                continue
            clean.append(row)
            addr = row["address"]
            if not isinstance(addr, str) or not _ADDRESS_RE.match(addr):
                out.append(f"{where}.address: not 0x and 40 lowercase hex")
                continue
            if row["short"] != short_address(addr) or row["explorer"] != explorer_url(addr):
                out.append(f"{where}: short and explorer must derive from the address")
            if row["kind"] not in ("member", "house"):
                out.append(f"{where}.kind: must be member or house")
            if row["signer_role"] not in ("master", "agent", None):
                out.append(f"{where}.signer_role: must be master, agent or null")
            if not isinstance(row["since"], str) or not _DATE_RE.match(row["since"]):
                out.append(f"{where}.since: must be YYYY-MM-DD")
            if not (isinstance(row["closed_trades"], int) and row["closed_trades"] >= row["n"]):
                out.append(f"{where}: closed_trades must cover n")
            if not _num_or_none(row["luck_median"]) or not isinstance(row["luck_n"], int):
                out.append(f"{where}: luck_median is a number or null, luck_n a whole number")
        addrs = [r["address"] for r in clean]
        if len(addrs) != len(set(addrs)):
            out.append("members: an address appears more than once")
        if clean != sorted(clean, key=lambda r: (-r["n"], str(r["address"]))):
            out.append("members must be ordered by n (most first), then address")
        if counts is not None and len(clean) == len(members):
            house = sum(1 for r in clean if r["kind"] == "house")
            if counts["house"] != house or counts["members"] != len(clean) - house or house > 1:
                out.append("counts.members and counts.house must match the rows")
            if counts["closed_trades"] != sum(r["closed_trades"] for r in clean) \
                    or counts["graded_trades"] != sum(r["n"] for r in clean):
                out.append("counts.closed_trades and counts.graded_trades must sum the rows")
    if counts is not None and pool is not None and not any("pool[" in p for p in out):
        if counts["slices"] != len(pool) \
                or counts["slices_at_min_n"] != sum(1 for r in pool if r["n"] >= MIN_N_EDGE) \
                or counts["edge"] != sum(1 for r in pool if r["label"] == "EDGE") \
                or counts["leak"] != sum(1 for r in pool if r["label"] == "LEAK"):
            out.append("counts.slices, slices_at_min_n, edge and leak must match the pool rows")
        if pool and counts["independent_trades"] != pool[0]["n"]:
            out.append("counts.independent_trades must be the 'all' row's n")
    return out


def empty_scoreboard(as_of_ms: int) -> Dict[str, Any]:
    """A valid scoreboard with no rows: what a build publishes for an empty pool."""
    cutoff = cutoff_ms(as_of_ms)
    all_row: Dict[str, Any] = {
        "key": "all", "dim": "all", "value": "all", "n": 0, "n_trades": 0, "members": 0, "wins": 0,
        "win_rate": None, "win_lo": None, "win_hi": None, "mean_r": None, "r_lo": None,
        "r_hi": None, "p": None, "label": "NOISE"}
    return {
        "schema": SCOREBOARD_SCHEMA, "network": NETWORK, "day": day_of(as_of_ms),
        "cutoff": iso_from_ms(cutoff), "delay_hours": DELAY_MS // HOUR_MS, "min_n_edge": MIN_N_EDGE,
        "counts": {"members": 0, "house": 0, "left": 0, "closed_trades": 0, "graded_trades": 0,
                   "independent_trades": 0, "slices": 1, "slices_at_min_n": 0, "edge": 0, "leak": 0},
        "pool": [all_row], "members": [], "notes": list(NOTES),
    }


# ── glue ────────────────────────────────────────────────────────────────────

def mcp_handler(name: str) -> Callable[[Dict[str, Any]], str]:
    """The handler for one pool tool, imported on first call so the MCP server
    pays nothing for the pool until a pool tool is used."""
    module, func = MCP_HANDLERS[name]

    def call(params: Dict[str, Any]) -> str:
        return getattr(importlib.import_module(module), func)(params)

    call.__name__ = f"handle_{name}"
    return call
