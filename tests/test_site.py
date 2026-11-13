"""The GitHub Pages site (scripts/publish_site.py, site/): what it serves and
what it says.

Pages first served all of docs/ from main (2026-11-13), research docs with
backtest figures included. The site is now the gh-pages branch holding the
landing page, the sitemap, the icon and the scoreboard, nothing else. The
landing page is the one crawlable web page about pathiel outside GitHub, so
its search metadata and its disclosures are pinned here.
"""
from __future__ import annotations

import html.parser
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_site.py"
SITE_URL = "https://julian-dev28.github.io/pathiel/"

sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("publish_site", SCRIPT)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
B = S.B


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the landing page, parsed once ────────────────────────────────────────────

class _Page(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta, self.links, self.scripts, self.hrefs = {}, {}, [], []
        self.title, self.h1, self._in, self._buf = "", "", None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "link" and a.get("rel"):
            self.links[a["rel"]] = a.get("href", "")
        elif tag == "script":
            self.scripts.append(a)
            self._in, self._buf = "script", []
        elif tag in ("title", "h1"):
            self._in, self._buf = tag, []
        elif tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == self._in:
            text = "".join(self._buf)
            if tag == "title":
                self.title = text.strip()
            elif tag == "h1":
                self.h1 = text.strip()
            elif tag == "script":
                self.scripts[-1]["_body"] = text
            self._in = None


def page_text() -> str:
    return (ROOT / "site" / "index.html").read_text(encoding="utf-8")


def parsed() -> _Page:
    p = _Page()
    p.feed(page_text())
    return p


def json_ld() -> dict:
    blocks = [s["_body"] for s in parsed().scripts if s.get("type") == "application/ld+json"]
    assert len(blocks) == 1
    return json.loads(blocks[0])


def visible_text() -> str:
    body = page_text().split("<body>", 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


# ── search metadata (shared with tests/test_eval_site_seo.py) ───────────────

def seo_rows() -> list:
    """(check, got, want, ok) for every search-facing property."""
    p, ld = parsed(), json_ld()
    desc = p.meta.get("description", "")
    rows = [
        ("title length 30..65", len(p.title), "30..65", 30 <= len(p.title) <= 65),
        ("title names Hyperliquid and Claude Code", p.title,
         "both", "Hyperliquid" in p.title and "Claude Code" in p.title),
        ("meta description length 120..160", len(desc), "120..160", 120 <= len(desc) <= 160),
        ("one h1", bool(p.h1), True, bool(p.h1)),
        ("canonical", p.links.get("canonical"), SITE_URL, p.links.get("canonical") == SITE_URL),
        ("og:title, og:description, og:url, og:image",
         sum(k in p.meta for k in ("og:title", "og:description", "og:url", "og:image")), 4,
         all(k in p.meta for k in ("og:title", "og:description", "og:url", "og:image"))),
        ("og:url is the canonical", p.meta.get("og:url"), SITE_URL, p.meta.get("og:url") == SITE_URL),
        ("JSON-LD SoftwareApplication, price 0", ld.get("@type"), "SoftwareApplication",
         ld.get("@type") == "SoftwareApplication" and ld.get("offers", {}).get("price") == "0"),
        ("sitemap lists the canonical and the scoreboard", "", "2 urls",
         all(u in (ROOT / "site" / "sitemap.xml").read_text() for u in (SITE_URL, SITE_URL + "scoreboard/"))),
    ]
    return rows


def test_search_metadata_is_complete():
    failed = [r for r in seo_rows() if not r[3]]
    assert failed == []


def test_site_url_is_the_pool_scoreboard_host():
    pool = _load("_site_pool_contract", ROOT / "contracts" / "pool" / "__init__.py")
    assert pool.SCOREBOARD_URL == SITE_URL + "scoreboard/"


def test_facts_on_the_page_match_the_repo():
    """Version, tool count and install lines come from the sources that ship."""
    init = (ROOT / "pathiel" / "__init__.py").read_text()
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    assert json_ld()["softwareVersion"] == version
    badge = re.search(r"badge/MCP-(\d+)_tools", (ROOT / "README.md").read_text()).group(1)
    assert f"{badge} tools" in visible_text()
    copy = _load("_site_public_copy", ROOT / "tests" / "test_public_copy.py")
    for line in copy.INSTALL_LINES:
        assert line in page_text(), line


def test_the_page_carries_the_disclosures():
    import contracts.runtime as runtime
    text = visible_text()
    for must in ("Real money.", "PATHIEL_NETWORK=testnet", "never withdraw", "not financial advice",
                 "US persons", "no demonstrated edge", "1% of equity at risk", "3x"):
        assert must in text, must
    if runtime.BUILDER_FEE_WAIVED:
        assert "pathiel charges no fee" in text
    else:
        pytest.fail("the fee is on: say the rate and the approval on the landing page")


def test_the_page_loads_nothing_from_elsewhere():
    p = parsed()
    assert all(not s.get("src") for s in p.scripts), "no external or local script files"
    assert [s.get("type") for s in p.scripts] == ["application/ld+json"]
    assert "default-src 'none'" in p.meta.get("content-security-policy", "")
    assert not re.search(r'<link[^>]+rel="stylesheet"', page_text())
    for href in p.hrefs:
        if not href.startswith("https://"):
            assert href in ("scoreboard/",), href


def test_copy_follows_the_style_rules(copy_lint):
    assert copy_lint.style_hits(visible_text()) == []
    assert copy_lint.claim_hits(visible_text()) == []


# ── what the branch holds ───────────────────────────────────────────────────

def test_the_site_holds_the_page_and_the_scoreboard_only():
    entries = S.site_entries(B.ls_tree(B.INDEX))
    assert {"index.html", "sitemap.xml", "icon.png", ".nojekyll", "scoreboard/index.html",
            "scoreboard/scoreboard.json"} <= set(entries)
    extra = [p for p in entries if p not in S.FIXED and not p.startswith("scoreboard/")]
    assert extra == []
    assert not any(p.endswith(".md") for p in entries)


def test_a_missing_source_stops_the_build():
    files = B.ls_tree(B.INDEX)
    files.pop("site/index.html")
    with pytest.raises(SystemExit, match="site/index.html"):
        S.site_entries(files)


# ── publish and check, on a throwaway clone ─────────────────────────────────

def _clean_env() -> dict:
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # An identity for the throwaway clone's commits, by environment: CI has no
    # git identity, and writing one with `git config` once leaked into the
    # real repo (2026-11-13). These variables change no repository state.
    env.update(GIT_AUTHOR_NAME="pathiel test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="pathiel test", GIT_COMMITTER_EMAIL="test@example.invalid")
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=_clean_env()).stdout.strip()


def _repo_state(repo: Path) -> str:
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=repo,
                          capture_output=True, text=True, env=_clean_env()).stdout
    config = subprocess.run(["git", "config", "--local", "--list"], cwd=repo, capture_output=True,
                            text=True, env=_clean_env()).stdout
    return refs + "\n--config--\n" + config


@pytest.fixture
def real_repo_unchanged():
    before = _repo_state(ROOT)
    yield
    assert _repo_state(ROOT) == before, "a site test changed a ref or the config of the real repo"


def test_publish_refuses_inside_a_git_hook(tmp_path, real_repo_unchanged):
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=_clean_env())
    env = dict(_clean_env(), GIT_DIR=str(decoy / ".git"))
    proc = subprocess.run([sys.executable, str(SCRIPT), "publish", "--ref", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "publish refused" in proc.stderr


def test_publish_check_and_drift_on_a_clone(tmp_path, real_repo_unchanged):
    clone = tmp_path / "clone"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(clone)],
                   check=True, env=_clean_env())
    _git(clone, "checkout", "-q", "--detach", head)
    for name in ("publish_site.py", "build_plugin_bundle.py"):   # this version, uncommitted
        shutil.copy(ROOT / "scripts" / name, clone / "scripts" / name)
    if not (clone / "site" / "index.html").exists():                # first commit of the site
        shutil.copytree(ROOT / "site", clone / "site")
        _git(clone, "add", "site")
        _git(clone, "commit", "-q", "-m", "site sources")
    for ref in ("refs/remotes/origin/gh-pages", "refs/heads/gh-pages"):
        subprocess.run(["git", "update-ref", "-d", ref], cwd=clone, env=_clean_env())

    def site(*args):
        return subprocess.run([sys.executable, str(clone / "scripts" / "publish_site.py"), *args],
                              cwd=clone, capture_output=True, text=True, env=_clean_env())

    first = site("publish").stdout.strip()
    assert first and _git(clone, "rev-parse", "gh-pages") == first, site("publish").stderr
    assert site("publish").stdout.strip() == first
    assert site("check", "--against", "gh-pages").returncode == 0
    listed = _git(clone, "ls-tree", "-r", "--name-only", "gh-pages").splitlines()
    assert "index.html" in listed and not any(p.endswith(".md") for p in listed)

    board = clone / "docs" / "scoreboard" / "scoreboard.json"
    board.write_text(board.read_text() + "\n", encoding="utf-8")
    _git(clone, "commit", "-q", "-am", "a new scoreboard build")
    stale = site("check", "--against", "gh-pages")
    assert stale.returncode == 1 and "MISMATCH" in stale.stderr
    second = site("publish").stdout.strip()
    assert _git(clone, "rev-parse", f"{second}^") == first

    readme = clone / "README.md"
    readme.write_text(readme.read_text() + "\nchanged\n", encoding="utf-8")
    _git(clone, "commit", "-q", "-am", "an unpublished file")
    assert site("publish").stdout.strip() == second
