"""Gate test for docs/GETTING_STARTED.md and its figures.

Deterministic, offline, no network: every check here reads files already on
disk and compares them against each other. Keeps the onboarding doc honest
the same way tests/test_mcp_overhaul.py keeps the tool table honest - a
renamed tool, a moved file, or a broken image link fails this test instead
of being noticed by a reader first.

    python3 -m pytest -q tests/test_getting_started_doc.py
"""
from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Set

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "GETTING_STARTED.md"
FIGURES_DIR = ROOT / "docs" / "getting-started"
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"

SVG_NS = "{http://www.w3.org/2000/svg}"

# Banned per CLAUDE.md's writing-style rule. Checked against the doc's own
# words, so a future edit that slips one back in fails here, not in review.
BANNED_WORDS = [
    "delve", "crucial", "robust", "comprehensive", "nuanced", "multifaceted",
    "furthermore", "moreover", "pivotal", "landscape", "tapestry",
    "underscore", "foster", "showcase", "intricate", "vibrant",
    "fundamental", "significant", "interplay",
]

# Backticked identifiers used in the doc that look like a tool name
# (lowercase snake_case) but are parameter names, config fields, or the
# project name, not MCP tools. Keeping this list explicit means a real typo
# in a tool name still fails the test below.
NON_TOOL_IDENTIFIERS = {
    "dry_run", "entry_px", "max_leverage", "max_open_positions", "pathiel",
}


def _load_server():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_getting_started", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tool_names() -> Set[str]:
    mod = _load_server()
    return {t["name"] for t in mod.TOOLS}


class TestDocExists:
    def test_doc_and_figures_dir_exist(self):
        assert DOC.is_file(), f"missing {DOC}"
        assert FIGURES_DIR.is_dir(), f"missing {FIGURES_DIR}"


class TestWritingStyle:
    def test_no_em_dash(self, doc_text):
        assert "—" not in doc_text, "em dash found; CLAUDE.md bans it"

    def test_no_banned_words(self, doc_text):
        lower = doc_text.lower()
        hits = [w for w in BANNED_WORDS if w in lower]
        assert not hits, f"banned word(s) found: {hits}"


class TestImageLinks:
    """Every markdown image link resolves to a real file, and carries a
    one-line 'Figure N.' caption on the next non-blank line."""

    def _image_links(self, doc_text: str):
        # ![alt](path)
        return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", doc_text)

    def test_at_least_three_images(self, doc_text):
        links = self._image_links(doc_text)
        assert len(links) >= 3, f"expected at least 3 figures, found {len(links)}"

    def test_every_image_link_resolves(self, doc_text):
        links = self._image_links(doc_text)
        assert links, "no image links found in the doc"
        for link in links:
            resolved = (DOC.parent / link).resolve()
            assert resolved.is_file(), f"image link does not resolve to a file: {link}"

    def test_every_image_has_a_figure_caption(self, doc_text):
        lines = doc_text.splitlines()
        image_line_idxs = [i for i, line in enumerate(lines) if re.search(r"!\[[^\]]*\]\([^)]+\)", line)]
        assert image_line_idxs
        for i in image_line_idxs:
            caption = ""
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    caption = lines[j].strip()
                    break
            assert caption.startswith("Figure "), f"no 'Figure N.' caption after image at line {i + 1}: {caption!r}"


class TestSVGFigures:
    """Every SVG in docs/getting-started/ is valid XML with a <title> and a
    viewBox, per the operator's figure spec."""

    def _svgs(self):
        svgs = sorted(FIGURES_DIR.glob("*.svg"))
        assert len(svgs) >= 3, f"expected at least 3 hand-written SVG figures, found {len(svgs)}"
        return svgs

    def test_every_svg_parses_and_has_title_and_viewbox(self):
        for svg_path in self._svgs():
            try:
                tree = ET.parse(svg_path)
            except ET.ParseError as exc:
                pytest.fail(f"{svg_path.name} is not valid XML: {exc}")
            root = tree.getroot()
            assert root.tag == f"{SVG_NS}svg", f"{svg_path.name}: root element is not <svg>"
            assert root.get("viewBox"), f"{svg_path.name}: no viewBox on the root <svg>"
            title_el = root.find(f"{SVG_NS}title")
            assert title_el is not None, f"{svg_path.name}: no <title> element"
            assert (title_el.text or "").strip(), f"{svg_path.name}: <title> is empty"

    def test_every_figure_referenced_in_doc_exists_on_disk(self, doc_text):
        links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", doc_text)
        svg_links = [link for link in links if link.endswith(".svg")]
        assert svg_links
        for link in svg_links:
            resolved = (DOC.parent / link).resolve()
            assert resolved.is_file(), f"doc references {link}, not found on disk"


class TestToolNamesAreReal:
    """Every MCP tool name the doc presents as a tool - a bare inline
    mention or the identifier a fenced prompt block opens with - exists in
    the live server's TOOLS list."""

    def _inline_tool_shaped_tokens(self, doc_text: str) -> Set[str]:
        no_fences = re.sub(r"```.*?```", "", doc_text, flags=re.DOTALL)
        spans = re.findall(r"`([^`\n]+)`", no_fences)
        return {s for s in spans if re.fullmatch(r"[a-z][a-z0-9_]*", s)} - NON_TOOL_IDENTIFIERS

    def _fenced_prompt_identifiers(self, doc_text: str) -> Set[str]:
        names: Set[str] = set()
        for _lang, body in re.findall(r"```([a-zA-Z]*)\n(.*?)```", doc_text, flags=re.DOTALL):
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^([a-z][a-z0-9_]*)(\s*\{.*\})?$", line)
                if m:
                    names.add(m.group(1))
        return names

    def test_inline_mentions_are_real_tools(self, doc_text, tool_names):
        mentioned = self._inline_tool_shaped_tokens(doc_text)
        assert mentioned, "expected at least one inline tool mention in the doc"
        unknown = mentioned - tool_names
        assert not unknown, f"doc mentions tool(s) not in the server's TOOLS: {sorted(unknown)}"

    def test_fenced_prompt_blocks_name_real_tools(self, doc_text, tool_names):
        used = self._fenced_prompt_identifiers(doc_text)
        assert used, "expected at least one fenced tool-call prompt in the doc"
        unknown = used - tool_names
        assert not unknown, f"doc's prompt block(s) call tool(s) not in the server's TOOLS: {sorted(unknown)}"

    def test_live_money_tools_marked_as_such(self, doc_text, tool_names):
        """Every live-money tool the doc mentions has a 'LIVE MONEY' marker
        within 400 characters of at least one mention (its own heading,
        bullet, or the paragraph right after its prompt block)."""
        mod = _load_server()
        live_money = mod.LIVE_MONEY_TOOLS
        mentioned = (self._inline_tool_shaped_tokens(doc_text) | self._fenced_prompt_identifiers(doc_text)) & tool_names
        live_mentioned = mentioned & live_money
        assert live_mentioned, "expected at least one LIVE MONEY tool mentioned in the doc"
        marker_positions = [m.start() for m in re.finditer("LIVE MONEY", doc_text)]
        assert marker_positions, "doc mentions a live-money tool but never writes LIVE MONEY"
        for name in live_mentioned:
            name_positions = [m.start() for m in re.finditer(re.escape(name), doc_text)]
            near = any(abs(n - p) <= 400 for n in name_positions for p in marker_positions)
            assert near, f"{name} is a live-money tool but no LIVE MONEY marker sits near its mention"


class TestRepoPathsExist:
    """Every repo path the doc links or mentions in an inline code span
    resolves to a real file or directory. Scoped to markdown links and
    inline (single-backtick) spans outside fenced code blocks, since those
    are the paths the doc presents as real repo locations; command-line
    arguments inside shell examples (e.g. a GitHub 'owner/repo' flag) are
    not repo paths and are deliberately out of scope."""

    def _markdown_link_targets(self, doc_text: str):
        # [text](path), excluding image links ![..](..)
        targets = []
        for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", doc_text):
            targets.append(m.group(1))
        return targets

    def _inline_path_candidates(self, doc_text: str):
        no_fences = re.sub(r"```.*?```", "", doc_text, flags=re.DOTALL)
        spans = re.findall(r"`([^`\n]+)`", no_fences)
        candidates = []
        for s in spans:
            if "/" not in s:
                continue
            if "://" in s:
                continue
            if s.startswith("/") or s.startswith("~"):
                continue
            candidates.append(s.split("::", 1)[0])
        return candidates

    def test_markdown_links_resolve(self, doc_text):
        targets = self._markdown_link_targets(doc_text)
        non_image = [t for t in targets if not t.endswith(".svg")]
        assert non_image, "expected at least one non-image markdown link in the doc"
        for target in non_image:
            resolved = (DOC.parent / target).resolve()
            assert resolved.exists(), f"linked path does not exist: {target}"

    def test_inline_code_paths_resolve(self, doc_text):
        candidates = self._inline_path_candidates(doc_text)
        assert candidates, "expected at least one inline repo-path mention in the doc"
        for candidate in candidates:
            resolved = (ROOT / candidate).resolve()
            assert resolved.exists(), f"inline path does not exist in the repo: {candidate}"


class TestReadmePointsAtDoc:
    def test_readme_links_getting_started(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "docs/GETTING_STARTED.md" in readme, (
            "README.md should link docs/GETTING_STARTED.md near the top"
        )
