"""Gate test for the deploy config: Dockerfile, .dockerignore, fly.toml, and
k8s/statefulset.yaml.

This is the test that would have caught `services/` going missing from a
3-months-stale Dockerfile (the app grew services/trend_engine and
services/polymarket_scout after the last deploy-config touch; the Dockerfile
never learned about either). It works by NOT hardcoding the expected package
list — it statically scans every import under hermes_trader/, scripts/, and
services/ for `import services.<x>` / `from services.<x> import ...` /
`from hermes_trader...`, then asserts the Dockerfile actually COPYs each
referenced top-level package. Add a new services/<name> package and start
importing it from the app, and this test fails on the next commit until the
Dockerfile is updated to match — same mechanism that would have caught the
original staleness.

The process side works the same way in reverse: scripts/restart.sh defines
one `<NAME>_PATTERN` string per managed process (used for `pgrep -f`, so
they're already designed to be a distinctive substring of that process's
real command line). This test asserts every one of those patterns shows up
in both fly.toml's [processes] table and k8s/statefulset.yaml's containers,
so a process restart.sh gains (or loses) can't silently drift out of sync
with either deploy target.

Deterministic, offline (no docker, no network, no subprocess), <2s.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
FLY_TOML = ROOT / "fly.toml"
STATEFULSET = ROOT / "k8s" / "statefulset.yaml"
CONFIGMAP = ROOT / "k8s" / "configmap.yaml"
RESTART_SH = ROOT / "scripts" / "restart.sh"

# Directories actually scanned for imports (mirrors what a managed process
# can reach at runtime). services/hermes_data_api is its own deploy unit —
# own Dockerfile, own Postgres deps, own requirements.txt — and must never
# be bundled into the main image, so it is excluded from the import scan on
# purpose: nothing under it should ever be "required" by this Dockerfile.
SCAN_DIRS = ("hermes_trader", "scripts", "services")
EXCLUDE_PREFIXES = ("services/hermes_data_api",)


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
    r"^\s*(?:from|import)\s+(hermes_trader|services)(?:\.([a-zA-Z0-9_]+))?"
)


def _imported_top_level_packages() -> set[str]:
    """Every top-level package (or services.<subpackage>) referenced by an
    import statement anywhere under SCAN_DIRS. Returns entries like
    {"hermes_trader", "services.trend_engine", "services.polymarket_scout"}."""
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
                if top == "hermes_trader":
                    found.add("hermes_trader")
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
        # substring check against fly.toml / k8s command strings.
        patterns[name] = pattern.replace("\\.", ".")
    return patterns


def _fly_processes() -> dict[str, str]:
    data = tomllib.loads(FLY_TOML.read_text())
    return dict(data.get("processes", {}))


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
    assert "hermes_trader" in IMPORTED_PACKAGES
    assert "services.trend_engine" in IMPORTED_PACKAGES
    assert "services.polymarket_scout" in IMPORTED_PACKAGES


def test_dockerfile_copies_every_imported_top_level_package():
    """The regression test: every package hermes_trader/scripts/services
    actually imports must have a matching `COPY <pkg>/ <pkg>/` line in the
    Dockerfile. This is what would have caught services/trend_engine and
    services/polymarket_scout being entirely absent from the image."""
    copies = _dockerfile_copy_sources()
    for pkg in sorted(IMPORTED_PACKAGES):
        expected_dir = pkg.replace(".", "/") + "/"
        assert any(c == expected_dir or c.startswith(expected_dir) for c in copies), (
            f"{pkg!r} is imported under hermes_trader/scripts/services but the "
            f"Dockerfile has no `COPY {expected_dir}...` line — the built image "
            f"would ship a partial app that fails on first import of {pkg}."
        )


def test_dockerfile_does_not_bundle_hermes_data_api():
    """services/hermes_data_api is its own deploy unit (own Dockerfile, own
    Postgres deps) — bundling it here would ship dead weight (and its own
    requirements.txt deps, never installed by this Dockerfile, so importing
    it from the main image would fail anyway)."""
    copies = _dockerfile_copy_sources()
    assert not any("hermes_data_api" in c for c in copies)


def test_dockerfile_installs_the_package_before_copying_source():
    """Layer-caching sanity: `pip install -e .` must run before the bulk
    `COPY hermes_trader/ ...` / `COPY services/...` lines, or every source
    change invalidates the (slow) dependency-install layer."""
    text = _dockerfile_text()
    install_at = text.index("pip install -e .")
    bulk_copy_at = text.index("COPY hermes_trader/ hermes_trader/")
    assert install_at < bulk_copy_at


# ── .dockerignore ────────────────────────────────────────────────────────────

REQUIRED_DOCKERIGNORE_ENTRIES = [
    ".env.local",
    "logs/",
    "research/",
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


# ── process completeness: restart.sh vs fly.toml vs k8s ────────────────────

RESTART_SH_PATTERNS = _restart_sh_process_patterns()


def test_restart_sh_defines_the_five_known_processes():
    """Sanity check on the parser — if restart.sh's own process list changes
    shape, this fails loudly instead of the real checks below silently
    passing on an empty set."""
    assert set(RESTART_SH_PATTERNS) >= {
        "LOOP", "SERVER", "SCHED", "SAMPLER", "ROTATOR",
    }


def test_every_restart_sh_process_is_in_fly_toml():
    fly_processes = _fly_processes()
    fly_commands = " ".join(fly_processes.values())
    for name, pattern in RESTART_SH_PATTERNS.items():
        assert pattern in fly_commands, (
            f"scripts/restart.sh manages a {name} process (pgrep pattern "
            f"{pattern!r}) but no command in fly.toml's [processes] table "
            f"contains it — this process would never run on Fly."
        )


def test_every_restart_sh_process_is_in_k8s_statefulset():
    k8s_args = _k8s_args_blob()
    for name, pattern in RESTART_SH_PATTERNS.items():
        assert pattern in k8s_args, (
            f"scripts/restart.sh manages a {name} process (pgrep pattern "
            f"{pattern!r}) but no container's `args:` in "
            f"k8s/statefulset.yaml contains it — this process would never "
            f"run in the StatefulSet."
        )


def test_fly_toml_mounts_data_volume_for_state_bearing_processes():
    data = tomllib.loads(FLY_TOML.read_text())
    mounts = data.get("mounts") or [data.get("mounts", {})]
    # tomllib returns [[mounts]] as a list under the "mounts" key.
    mount_procs: set[str] = set()
    for m in data.get("mounts", []) if isinstance(data.get("mounts"), list) else []:
        mount_procs.update(m.get("processes", []))
    fly_processes = set(_fly_processes())
    # web/loop/sched/sampler read or write /data (directly or via
    # HERMES_STATE_DIR); rotator deliberately does not (see DEPLOY.md
    # "Runtime state").
    for proc in fly_processes - {"rotator"}:
        assert proc in mount_procs, (
            f"fly.toml process {proc!r} is not in [[mounts]].processes — "
            f"it would boot without /data and lose state on every restart."
        )


def test_k8s_statefulset_has_a_container_per_process():
    container_names = set(_k8s_container_names())
    fly_processes = set(_fly_processes())
    assert container_names == fly_processes, (
        f"k8s containers {container_names} and fly.toml processes "
        f"{fly_processes} have drifted apart — every process should exist "
        f"in both deploy targets under the same name."
    )


def test_k8s_configmap_and_fly_env_declare_the_same_shared_config():
    """fly.toml [env] and k8s/configmap.yaml `data:` are documented as
    mirrors of each other (see configmap.yaml's own comment) — catch them
    drifting apart."""
    fly_env = tomllib.loads(FLY_TOML.read_text()).get("env", {})
    cm_text = CONFIGMAP.read_text()
    for key, value in fly_env.items():
        assert re.search(rf'^\s*{re.escape(key)}:\s*"{re.escape(str(value))}"',
                         cm_text, re.M), (
            f"fly.toml [env] sets {key}={value!r} but k8s/configmap.yaml "
            f"does not set the same value for it."
        )
