"""Test isolation: redirect agent state files to a throwaway temp dir BEFORE any
pathiel module imports, so a test can never read or truncate the live
.agent-memory.json / .agent-config.json (a pytest run wiped live trading state
on 2026-06-15). This runs at conftest import — before test modules are collected,
hence before memory.py / config_store.py freeze their module-level paths.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="pathiel-test-state-")
# Force (not setdefault): even if the dev shell exports these, tests must use
# disposable paths.
os.environ["PATHIEL_AGENT_MEMORY_FILE"] = os.path.join(_tmp, ".agent-memory.json")
os.environ["PATHIEL_AGENT_CONFIG_FILE"] = os.path.join(_tmp, ".agent-config.json")
os.environ["PATHIEL_DSL_STATE_FILE"] = os.path.join(_tmp, ".dsl-state.json")
# Every process-local state file (backup receipts, supervisor/alert-evaluator
# timestamps, ...) routes through pathiel.state.state_file(), which honors
# PATHIEL_STATE_DIR. Point it at the temp dir so the suite never touches the
# live state directory.
os.environ["PATHIEL_STATE_DIR"] = _tmp
# Session log too: session_log.py freezes SESSION_LOG_PATH at import. This was the ONE
# state file not redirected — pytest wrote fabricated C1/C2/C3 book events into the LIVE
# activity feed, which pnl_by_book attribution reads (audit 2026-07-09).
os.environ["SESSION_LOG_PATH"] = os.path.join(_tmp, "session-log.jsonl")


# `_reset_claims_registry` (a claims-registry reset between the strategy-book
# test files) was removed 2026-10-23 (wave 2): `ClaimsRegistry` itself went in
# wave 1 (headless desk) when the seven strategy books were retired, and the
# test files it protected against cross-contaminating went with it — the
# fixture had been a no-op (clearing an attribute and a file nothing sets any
# more) since that wave.


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _outside_eia_blackout(monkeypatch):
    """place_bracket refuses new oil entries 2h either side of the EIA report
    (services/strategies/event_blackout.py). Pin its clock to a Monday so a
    suite run on a Wednesday afternoon stays deterministic; blackout tests
    set their own time."""
    from services.strategies import event_blackout
    monkeypatch.setattr(event_blackout, "now_ms", lambda: 1793635200000)  # Mon 2026-11-02 16:00Z


# ── Public-install test kit (2026-11-12, pathiel 0.4.0 public plugin) ────────
#
# Three things every public-install unit needs and would otherwise each
# invent: a subprocess env that can never load the checkout's live .env.local
# or reach the venue (public_env), a claims and style lint with a one-hop
# link closure (copy_lint), and a scorecard emitter for the offline evals
# (scorecard). Fixtures, not an importable module: tests/ has no __init__.py,
# so a cross-file import would depend on pytest's import mode.
#
# These fixtures live in tests/ only. services/*/tests and contracts/ tests do
# not see them, by design: the units with tests there do not need them.

import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, Iterable, List, Optional, Set  # noqa: E402
from urllib.parse import unquote  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]

# requests honors these, so any venue call in a subprocess goes to a loopback
# port that nothing serves (discard, port 9) and fails fast. NO_PROXY is empty
# so no host bypasses them. A client that sets trust_env=False ignores them;
# pathiel's sessions do not, and tests/test_eval_public_kit.py proves it for
# the default requests session.
_DEAD_PROXY = "http://127.0.0.1:9"
_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
               "https_proxy", "http_proxy", "all_proxy")


@pytest.fixture
def public_env(tmp_path) -> Callable[..., Dict[str, str]]:
    """make(**overrides) -> a complete, explicit env for subprocess.run(env=...).

    Nothing is inherited from the pytest process except PATH: the root
    conftest has already loaded the checkout's .env.local into os.environ, and
    a subprocess that inherited it would see the operator's addresses and
    state dir. A value of None in overrides removes that key. PATHIEL_NETWORK
    is not set; a test adds it. make.env_file is the env file the fixture
    created (one comment line), so scripts/_state_env.py stops there and never
    falls back to <checkout>/.env.local or the real ~/.pathiel/.env.local.
    """
    home = tmp_path / "home"
    pathiel_home = home / ".pathiel"
    pathiel_home.mkdir(parents=True, exist_ok=True)
    env_file = pathiel_home / ".env.local"
    env_file.write_text("# pathiel test env file (tests/conftest.py public_env)\n",
                        encoding="utf-8")

    base: Dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PATHIEL_HOME": str(pathiel_home),
        "PATHIEL_ENV_FILE": str(env_file),
        "PATHIEL_STATE_DIR": str(tmp_path / "state-testnet"),
        "PATHIEL_AGENT_CONFIG_FILE": str(pathiel_home / ".agent-config.json"),
        "PATHIEL_UNIVERSE_CACHE_DIR": str(tmp_path / "universe"),
        "PATHIEL_AGENT_MEMORY_FILE": str(tmp_path / ".agent-memory.json"),
        "PATHIEL_DSL_STATE_FILE": str(tmp_path / ".dsl-state.json"),
        "SESSION_LOG_PATH": str(tmp_path / "session-log.jsonl"),
        "PATHIEL_INSTALL": "public",
        # Present and empty: every pathiel loader uses setdefault, so an empty
        # value is what keeps a later env file from adding a real key.
        "HYPERLIQUID_PRIVATE_KEY": "",
        "HYPERLIQUID_MASTER_PRIVATE_KEY": "",
        "HYPERLIQUID_WALLET_ADDRESS": "",
        "HYPERLIQUID_MASTER_ADDRESS": "",
        "PATHIEL_BUILDER_ADDRESS": "",
        "PATHIEL_DESK_BACKGROUND_REFRESH": "0",
        "PATHIEL_DESK_WARMUP": "0",
        "PATHIEL_META_PREWARM": "0",
        "PATHIEL_GRADE_LOOP": "0",
        "PATHIEL_DASHBOARD_LIVE_EQUITY": "0",
        "NO_PROXY": "",
        "no_proxy": "",
    }
    for key in _PROXY_KEYS:
        base[key] = _DEAD_PROXY

    def make(**overrides: Any) -> Dict[str, str]:
        env = dict(base)
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return env

    make.env_file = env_file  # type: ignore[attr-defined]
    return make


def _load_banned_words() -> List[str]:
    """The banned-word list from tests/test_getting_started_doc.py, loaded from
    that file so the two can never drift. Fails loudly if it moved."""
    src = Path(__file__).resolve().parent / "test_getting_started_doc.py"
    spec = importlib.util.spec_from_file_location("_pathiel_kit_getting_started_words", src)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"copy_lint: cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    words = getattr(mod, "BANNED_WORDS", None)
    if not words:
        raise RuntimeError(
            f"copy_lint: {src} has no BANNED_WORDS list; the style lint reads it "
            "from there so the two stay one list")
    return list(words)


class _CopyLint:
    """Claims and style lint for public copy, plus a one-hop link closure.

    The claims check is a pattern list, not understanding: a new phrasing of a
    return claim can slip past, and the negation window lets 'not only
    profitable' through. Add the phrasing here with a planted case in
    tests/test_public_kit.py when one turns up.
    """

    PERFORMANCE_PATTERNS = (
        r"\bROI\b",
        r"\d+(\.\d+)?\s?%\s*(a|per|/)\s*(day|week|month|year)\b",
        r"\bprofitable\b",
        r"\boutperform\w*",
        r"\bguaranteed? (returns?|profits?|income|gains?|wins?)\b",
        r"\brisk[- ]free (returns?|profits?|income|money|trades?)\b",
        r"\bthe edge is\b",
        r"\bproven edge\b",
        r"\bbeats? the market\b",
        r"\bpassive income\b",
        r"\bmakes? money\b",
        r"[+-]\d+(\.\d+)?R\b",
        r"\$\d[\d,.]*\s+(ends|grows? to|grew to|turns? into|turned into|became)\s+\$\d",
    )
    NEGATIONS = re.compile(r"\b(no|not|never|without|nor)\b", re.IGNORECASE)
    NEGATION_WINDOW = 30
    EM_DASH = "—"

    # [text](target) and [text](<target> "title")
    _MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
    # [id]: target
    _REF_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.MULTILINE)
    _URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
    # bare or backticked repo paths: README.md, docs/X.md, ./X.md, ../a/X.md
    _BARE_MD = re.compile(r"(?<![\w./-])((?:\.{1,2}/|/)?[\w.-]+(?:/[\w.-]+)*\.md)\b")
    _SKIP_DIRS = {".git", ".venv", "node_modules", ".claude"}

    def __init__(self, root: Path, banned_words: List[str]):
        self.root = root.resolve()
        self.BANNED_WORDS = tuple(banned_words)
        self._claims = [re.compile(p, re.IGNORECASE) for p in self.PERFORMANCE_PATTERNS]
        self._banned = [(w, re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE))
                        for w in self.BANNED_WORDS]

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _context(text: str, start: int, end: int, pad: int = 30) -> str:
        return text[max(0, start - pad):end + pad].strip()

    def claim_hits(self, text: str) -> List[str]:
        """One 'pattern :: context' per match on normalize(text). A match is
        skipped when the 30 characters before it hold no/not/never/without/nor."""
        flat = self.normalize(text)
        hits: List[str] = []
        for pattern, rx in zip(self.PERFORMANCE_PATTERNS, self._claims):
            for m in rx.finditer(flat):
                before = flat[max(0, m.start() - self.NEGATION_WINDOW):m.start()]
                if self.NEGATIONS.search(before):
                    continue
                hits.append(f"{pattern} :: {self._context(flat, m.start(), m.end())}")
        return hits

    def style_hits(self, text: str) -> List[str]:
        """Em dashes (U+2014) and BANNED_WORDS as whole words, any case."""
        flat = self.normalize(text)
        hits: List[str] = []
        for m in re.finditer(self.EM_DASH, flat):
            hits.append(f"em dash :: {self._context(flat, m.start(), m.end())}")
        for word, rx in self._banned:
            for m in rx.finditer(flat):
                hits.append(f"banned word {word!r} :: "
                            f"{self._context(flat, m.start(), m.end())}")
        return hits

    def _inside_repo(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return False
        return not (set(rel.parts) & self._SKIP_DIRS)

    def _resolve(self, target: str, folder: Path) -> Optional[Path]:
        target = unquote(target.split("#", 1)[0].split("?", 1)[0]).strip()
        if not target.lower().endswith(".md"):
            return None
        if target.startswith("/"):
            candidates = [self.root / target.lstrip("/")]
        else:
            candidates = [folder / target, self.root / target]
        for cand in candidates:
            cand = cand.resolve()
            if cand.is_file() and self._inside_repo(cand):
                return cand
        return None

    def linked_docs(self, path: Path) -> Set[Path]:
        """Every existing repo .md file `path` points at: Markdown link targets
        and bare or backticked paths ending in .md, resolved against the file's
        folder then the repo root. Anchors stripped; URLs and files outside
        the repo ignored."""
        path = Path(path).resolve()
        text = path.read_text(encoding="utf-8", errors="replace")
        targets: List[str] = []
        for rx in (self._MD_LINK, self._REF_LINK):
            targets += [m.group(1) for m in rx.finditer(text)
                        if not self._URL.match(m.group(1))]
        targets += [m.group(1) for m in self._BARE_MD.finditer(self._URL.sub(" ", text))]
        out: Set[Path] = set()
        for target in targets:
            hit = self._resolve(target, path.parent)
            if hit is not None and hit != path:
                out.add(hit)
        return out

    def reach(self, seeds: Iterable[Path]) -> List[Path]:
        """Sorted unique existing seeds plus linked_docs of every .md seed.
        One hop: what a linked doc links is not followed."""
        found: Set[Path] = set()
        for seed in seeds:
            seed = Path(seed)
            if not seed.is_absolute():
                seed = self.root / seed
            seed = seed.resolve()
            if not seed.is_file():
                continue
            found.add(seed)
            if seed.suffix.lower() == ".md":
                found |= self.linked_docs(seed)
        return sorted(found)


@pytest.fixture(scope="session")
def copy_lint() -> _CopyLint:
    """PERFORMANCE_PATTERNS, BANNED_WORDS, normalize, claim_hits, style_hits,
    linked_docs, reach. See _CopyLint."""
    return _CopyLint(_REPO_ROOT, _load_banned_words())


def _emit_scorecard(name: str, rows: List[dict]) -> dict:
    for row in rows:
        missing = {"check", "got", "want", "ok"} - set(row)
        if missing:
            raise ValueError(f"scorecard {name}: row {row!r} lacks {sorted(missing)}")
    passed = sum(1 for r in rows if r["ok"])
    card = {"name": name, "passed": passed, "total": len(rows), "rows": rows}

    width = max([len(str(r["check"])) for r in rows] + [5])
    lines = [f"\nscorecard {name}: {passed}/{len(rows)}",
             f"  {'check'.ljust(width)}  {'got':>14}  {'want':>14}  result"]
    for r in rows:
        lines.append(f"  {str(r['check']).ljust(width)}  {str(r['got']):>14}  "
                     f"{str(r['want']):>14}  {'PASS' if r['ok'] else 'FAIL'}")
    print("\n".join(lines))
    sys.stdout.flush()

    out_dir = os.environ.get("PATHIEL_EVAL_OUT")
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.json").write_text(
            json.dumps(card, indent=2, default=str) + "\n", encoding="utf-8")
    return card


@pytest.fixture(scope="session")
def scorecard() -> Callable[[str, List[dict]], dict]:
    """emit(name, rows): print a table (visible with -s), write
    $PATHIEL_EVAL_OUT/<name>.json when that variable is set, return
    {'name', 'passed', 'total', 'rows'}. Rows are
    {'check': str, 'got': Any, 'want': Any, 'ok': bool}."""
    return _emit_scorecard
