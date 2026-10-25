"""Gate test for the deploy config: Dockerfile, .dockerignore and
k8s/statefulset.yaml. (fly.toml was dropped 2026-10-25; Kubernetes is the one
container target.)

This is the test that would have caught `services/` going missing from a
3-months-stale Dockerfile (the app grew a services/ tree after the last
deploy-config touch; the Dockerfile never learned about it).
It works by NOT hardcoding the expected package
list — it statically scans every import under pathiel/, scripts/, and
services/ for `import services.<x>` / `from services.<x> import ...` /
`from pathiel...`, then asserts the Dockerfile actually COPYs each
referenced top-level package. Add a new services/<name> package and start
importing it from the app, and this test fails on the next commit until the
Dockerfile is updated to match — same mechanism that would have caught the
original staleness.

The process side works the same way in reverse: scripts/restart.sh defines
one `<NAME>_PATTERN` string per managed process (used for `pgrep -f`, so
they're already designed to be a distinctive substring of that process's
real command line). This test asserts every one of those patterns shows up
in k8s/statefulset.yaml's containers, so a process restart.sh gains (or
loses) can't silently drift out of sync with the deploy target.

Deterministic, offline (no docker, no network, no subprocess), <2s.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
STATEFULSET = ROOT / "k8s" / "statefulset.yaml"
RESTART_SH = ROOT / "scripts" / "restart.sh"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"

# Directories actually scanned for imports (mirrors what a managed process
# can reach at runtime). services/pathiel_data_api is its own deploy unit —
# own Dockerfile, own Postgres deps, own requirements.txt — and must never
# be bundled into the main image, so it is excluded from the import scan on
# purpose: nothing under it should ever be "required" by this Dockerfile.
SCAN_DIRS = ("pathiel", "scripts", "services")
# Own deploy unit, own Dockerfile or host — never bundled into the image,
# so their imports must not force a COPY line into it.
#   pathiel_data_api  own Dockerfile + Postgres deps
#   wallet_ui        an npm package built to a static asset; its Python tests
#                    boot a server against a built file, not the image
EXCLUDE_PREFIXES = ("services/pathiel_data_api", "services/wallet_ui")


# ── helpers ──────────────────────────────────────────────────────────────────

def _dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def _dockerfile_copy_sources() -> list[str]:
    """Every `COPY <src> <dst>` source path from the final build stage (skips
    `COPY --from=<stage>` lines, which copy build artifacts, not source)."""
    sources = []
    for line in _dockerfile_text().splitlines():
        line = line.strip()
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            sources.append(parts[1])
    return sources


_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(pathiel|services|contracts)(?:\.([a-zA-Z0-9_]+))?"
)


def _imported_top_level_packages() -> set[str]:
    """Every top-level package (or services.<subpackage>) referenced by an
    import statement anywhere under SCAN_DIRS. Returns entries like
    {"pathiel", "services.strategies"}."""
    found: set[str] = set()
    for base in SCAN_DIRS:
        base_path = ROOT / base
        if not base_path.exists():
            continue
        for py_file in base_path.rglob("*.py"):
            rel = py_file.relative_to(ROOT).as_posix()
            if "__pycache__" in rel or rel.startswith(EXCLUDE_PREFIXES):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                m = _IMPORT_RE.match(line)
                if not m:
                    continue
                top, sub = m.group(1), m.group(2)
                if top in ("pathiel", "contracts"):
                    # contracts/ was missing from this scan, and from the
                    # image, until 2026-10-25: pathiel.agents.config_store
                    # imports contracts.risk.models at module load, so the
                    # built image died on `python -m pathiel.server` with
                    # ModuleNotFoundError before serving a single request.
                    found.add(top)
                elif top == "services" and sub:
                    found.add(f"services.{sub}")
    return found


IMPORTED_PACKAGES = _imported_top_level_packages()


def _restart_sh_process_patterns() -> dict[str, str]:
    """name -> pgrep pattern, parsed straight from `<NAME>_PATTERN="..."`
    assignments in scripts/restart.sh. These are restart.sh's own canonical,
    distinctive-substring identifiers for each managed process."""
    text = RESTART_SH.read_text()
    patterns = {}
    for m in re.finditer(r'^([A-Z]+)_PATTERN="([^"]*)"', text, re.M):
        name, pattern = m.group(1), m.group(2)
        # These are pgrep -f (basic regex) patterns; unescape the one thing
        # restart.sh actually escapes (a literal dot) so we can do a plain
        # substring check against the k8s command strings.
        patterns[name] = pattern.replace("\\.", ".")
    return patterns


def _k8s_args_blob() -> str:
    """Every `args: [...]` list in k8s/statefulset.yaml, with each list's
    quoted tokens rejoined by a single space (so it reads like the real argv
    a container runs, e.g. `python3 scripts/log_rotate.py --daemon`) and
    concatenated across containers into one searchable string. Avoids a
    PyYAML dependency (not declared in pyproject.toml) for what only needs a
    substring check."""
    text = STATEFULSET.read_text()
    blobs = re.findall(r"args:\s*\[([^\]]*)\]", text)
    joined = []
    for blob in blobs:
        tokens = re.findall(r'"([^"]*)"', blob)
        joined.append(" ".join(tokens))
    return " | ".join(joined)


def _k8s_container_names() -> list[str]:
    text = STATEFULSET.read_text()
    # Container list entries look like `        - name: web` inside
    # `containers:`; volumeClaimTemplates also has a `- name: data` entry
    # this regex would catch, so scope to lines that precede an `image:` key
    # a few lines later (every real container has one; the PVC template
    # doesn't).
    names = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*-\s*name:\s*(\S+)\s*$", line)
        if not m:
            continue
        lookahead = "\n".join(lines[i:i + 4])
        if "image:" in lookahead:
            names.append(m.group(1))
    return names


# ── Dockerfile / package completeness ───────────────────────────────────────

def test_scan_found_the_known_services_packages():
    """Sanity check on the scanner itself — if this fails, the import scan is
    broken, not the Dockerfile, and every other assertion below is moot."""
    assert "pathiel" in IMPORTED_PACKAGES
    assert "services.strategies" in IMPORTED_PACKAGES


# 2026-10-17 overhaul swarm: services.strategies and services.thesis were
# real forward references (scripts/pathiel-mcp-server.py's new desk_* tools,
# guarded) to services two OTHER agents were building in separate worktrees;
# both have since merged, hence the empty set below for those two.
#
# services.accounts is this wave's live instance of the same situation:
# services/risk/api.py guards `from services.accounts import api` (the
# accounts agent owns that service, building it in its own worktree right
# now) and falls back to pathiel.client.hl_client when it's absent. Not a
# missing-package bug; remove once services/accounts merges and add its
# Dockerfile COPY line (cleanup/integrator territory, not the risk agent's).
_NOT_YET_BUILT_BY_ANOTHER_SWARM_AGENT: set = set()


def test_dockerfile_copies_every_imported_top_level_package():
    """The regression test: every package pathiel/scripts/services
    actually imports must have a matching `COPY <pkg>/ <pkg>/` line in the
    Dockerfile. This is what would have caught a whole services/<x> package
    being absent from the image (it did, for the trend engine, in 2026-08)."""
    copies = _dockerfile_copy_sources()
    for pkg in sorted(IMPORTED_PACKAGES - _NOT_YET_BUILT_BY_ANOTHER_SWARM_AGENT):
        expected_dir = pkg.replace(".", "/") + "/"
        assert any(c == expected_dir or c.startswith(expected_dir) for c in copies), (
            f"{pkg!r} is imported under pathiel/scripts/services but the "
            f"Dockerfile has no `COPY {expected_dir}...` line — the built image "
            f"would ship a partial app that fails on first import of {pkg}."
        )


def test_the_not_yet_built_allowlist_is_still_needed():
    """Passes now because neither package exists on disk in this worktree.
    Once one of their owning agents' worktrees merges, THIS test starts
    failing for that package -- no xfail marker needed, that failure IS
    the reminder to drop it from _NOT_YET_BUILT_BY_ANOTHER_SWARM_AGENT
    above (and to add its Dockerfile COPY line)."""
    for pkg in sorted(_NOT_YET_BUILT_BY_ANOTHER_SWARM_AGENT):
        assert not (Path(__file__).resolve().parents[1] / pkg.replace(".", "/")).is_dir(), (
            f"{pkg} now exists on disk -- remove it from the allowlist above")


def test_dockerfile_does_not_bundle_wallet_ui():
    """services/wallet_ui is an npm package, not part of the running app.

    Its only output is pathiel/static/wallet.js, which ships inside pathiel/. The
    sources and its 644 MB of node_modules have no business in the image.
    """
    copies = _dockerfile_copy_sources()
    assert not any(c.startswith("services/wallet_ui") for c in copies), (
        "the image copies services/wallet_ui — only its built asset, "
        "pathiel/static/wallet.js, belongs in the image"
    )


def test_dockerfile_does_not_bundle_pathiel_data_api():
    """services/pathiel_data_api is its own deploy unit (own Dockerfile, own
    Postgres deps) — bundling it here would ship dead weight (and its own
    requirements.txt deps, never installed by this Dockerfile, so importing
    it from the main image would fail anyway)."""
    copies = _dockerfile_copy_sources()
    assert not any("pathiel_data_api" in c for c in copies)


def test_dockerfile_installs_the_package_before_copying_source():
    """Layer-caching sanity: `pip install -e .` must run before the bulk
    `COPY pathiel/ ...` / `COPY services/...` lines, or every source
    change invalidates the (slow) dependency-install layer."""
    text = _dockerfile_text()
    install_at = text.index("RUN pip install --require-hashes -r requirements.lock")
    bulk_copy_at = text.index("COPY pathiel/ pathiel/")
    assert install_at < bulk_copy_at


def _run_lines() -> list[str]:
    return [ln.strip() for ln in _dockerfile_text().splitlines() if ln.strip().startswith("RUN ")]


def test_dockerfile_installs_only_the_hashed_lock():
    """Security review 2026-10-25: the image ran a bare `pip install -e .`,
    a fresh resolve of pyproject's ranges with no hash checked. Every pip
    install in the image now comes from requirements.lock in hash-checking
    mode, and the package itself goes in with --no-deps."""
    pip_runs = [ln for ln in _run_lines() if "pip install" in ln]
    assert "RUN pip install --require-hashes -r requirements.lock" in pip_runs
    for ln in pip_runs:
        assert "--require-hashes -r requirements.lock" in ln or "--no-deps" in ln, ln


def test_the_base_image_is_pinned_by_digest():
    froms = [ln for ln in _dockerfile_text().splitlines() if ln.startswith("FROM ")]
    assert froms and all(re.search(r"@sha256:[0-9a-f]{64}\b", f) for f in froms), froms


# ── never root ───────────────────────────────────────────────────────────────

def test_the_image_has_a_non_root_app_user():
    text = _dockerfile_text()
    assert "useradd --system --uid 10001" in text


def test_the_entrypoint_drops_root_before_running_anything():
    """The image may START as root (a docker volume mounts root-owned and only
    root can hand it over), but the entrypoint must leave root before it
    execs the process, and drop every capability on the way."""
    sh = ENTRYPOINT.read_text()
    root_branch = sh[sh.index('if [ "$(id -u)" = "0" ]'):sh.index("fi\n")]
    assert "exec setpriv --reuid=\"$APP_USER\" --regid=\"$APP_USER\"" in root_branch
    assert "--no-new-privs" in root_branch and "--bounding-set=-all" in root_branch
    # The only exec reachable as root is that setpriv one.
    before_drop = sh[:sh.index('if [ "$(id -u)" = "0" ]')]
    assert "exec " not in before_drop.replace("# ", "#")


def test_k8s_pods_run_non_root_with_no_privileges():
    text = STATEFULSET.read_text()
    for needle in ("runAsNonRoot: true", "runAsUser: 10001", "fsGroup: 10001",
                   "automountServiceAccountToken: false", "type: RuntimeDefault"):
        assert needle in text, needle
    n_containers = len(_k8s_container_names())
    assert text.count("allowPrivilegeEscalation: false") == n_containers
    assert text.count('drop: ["ALL"]') == n_containers
    assert text.count("readOnlyRootFilesystem: true") == n_containers


# ── .dockerignore ────────────────────────────────────────────────────────────

REQUIRED_DOCKERIGNORE_ENTRIES = [
    ".env.local",
    "logs/",
    ".state/",
    ".git/",
]


def test_dockerignore_excludes_secrets_and_runtime_artifacts():
    text = DOCKERIGNORE.read_text()
    lines = {l.strip() for l in text.splitlines()}
    for entry in REQUIRED_DOCKERIGNORE_ENTRIES:
        assert entry in lines, f".dockerignore must exclude {entry!r}"


def test_dockerignore_does_not_exclude_runtime_source():
    """None of the source the Dockerfile explicitly COPYs may be caught by a
    .dockerignore pattern — that would make the COPY silently ship nothing."""
    ignore_lines = [
        l.strip() for l in DOCKERIGNORE.read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    # Patterns that would blanket-exclude a runtime source directory (not
    # just a dotfile inside it, e.g. `.state/` must not match `services/`).
    blanket_dir_patterns = {
        l.rstrip("/") for l in ignore_lines
        if l.endswith("/") and not l.startswith("!")
    }
    for src in _dockerfile_copy_sources():
        top = src.split("/")[0]
        assert top not in blanket_dir_patterns, (
            f".dockerignore excludes {top!r}, but the Dockerfile COPYs {src!r} "
            f"from it — the build context would never contain it."
        )


# ── process completeness: restart.sh vs k8s ────────────────────────────────

RESTART_SH_PATTERNS = _restart_sh_process_patterns()


def test_restart_sh_defines_the_two_known_processes():
    """Sanity check on the parser — if restart.sh's own process list changes
    shape, this fails loudly instead of the real checks below silently
    passing on an empty set.

    2026-10-23 (wave 2): LOOP and SCHED (scripts/trading_loop.py,
    scripts/scheduler.py) are retired with the rest of the autonomous
    engine — restart.sh now manages only the server and the log rotator."""
    assert set(RESTART_SH_PATTERNS) >= {
        "SERVER", "ROTATOR",
    }
    assert "LOOP" not in RESTART_SH_PATTERNS and "SCHED" not in RESTART_SH_PATTERNS


def test_every_restart_sh_process_is_in_k8s_statefulset():
    k8s_args = _k8s_args_blob()
    for name, pattern in RESTART_SH_PATTERNS.items():
        assert pattern in k8s_args, (
            f"scripts/restart.sh manages a {name} process (pgrep pattern "
            f"{pattern!r}) but no container's `args:` in "
            f"k8s/statefulset.yaml contains it — this process would never "
            f"run in the StatefulSet."
        )


def test_k8s_statefulset_has_exactly_one_container_per_restart_sh_process():
    """restart.sh manages the server and the log rotator; the StatefulSet
    runs one container for each, under the names the docs use, and nothing
    else (a third container would be a process restart.sh does not know)."""
    container_names = _k8s_container_names()
    assert sorted(container_names) == ["rotator", "web"], container_names
    assert len(container_names) == len(RESTART_SH_PATTERNS)


def test_only_the_web_container_mounts_the_state_volume():
    """web reads and writes /data (through PATHIEL_STATE_DIR); rotator
    deliberately does not (see DEPLOY.md "Runtime state"). A rotator that
    mounted the volume could truncate state files it mistook for logs; a web
    that did not would lose every desk journal on restart."""
    text = STATEFULSET.read_text()
    web_start = text.index("- name: web")
    rotator_start = text.index("- name: rotator")
    web_block = text[web_start:rotator_start]
    rotator_block = text[rotator_start:text.index("volumeClaimTemplates")]
    assert "mountPath: /data" in web_block
    assert "mountPath: /data" not in rotator_block


def test_the_configmap_turns_off_first_caller_operator_bootstrap():
    """Security review 2026-10-25, P2-6: on a fresh database the first wallet
    to sign in claimed the operator role. A deployment never relies on being
    first; the role is granted with scripts/grant_operator.py (DEPLOY.md)."""
    cm = (ROOT / "k8s" / "configmap.yaml").read_text()
    assert re.search(r'^\s*PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR:\s*"1"\s*$', cm, re.M), cm
    assert "grant_operator.py" in (ROOT / "DEPLOY.md").read_text()


def test_restart_sh_sets_a_private_umask():
    """Logs and state the local processes write carry addresses, positions
    and order history; they were created world-readable (644) until the
    2026-10-25 review. The umask must be set before anything is started."""
    text = RESTART_SH.read_text()
    assert re.search(r"(?m)^umask 077$", text)
    assert text.index("umask 077") < text.index("nohup env")
