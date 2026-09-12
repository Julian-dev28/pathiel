"""The demo dashboard, as a self-contained sub-application.

Mounted at `/demo`, so `/demo/` is the demo landing page and
`/demo/api/dashboard/summary` is its data — the same paths as the live app with
one prefix in front. The front-end toggle is therefore a string, not a mode.

WHY A SECOND MODULE INSTANCE RATHER THAN A FLAG

`pathia/dashboard.py` resolves the session-log path, and several caches, at
import time:

    _LOG_PATH = Path(session_log.SESSION_LOG_FILE)

That is deliberate and good — it is what keeps the tail-parse incremental — but
it means a single imported instance can serve exactly one dataset. A per-request
"are we in demo mode" flag would have to mutate module state under a lock and
hope no live request interleaved, and the failure mode of getting that wrong is
the live dashboard rendering invented equity. Not a bug worth risking for a
toggle.

So this loads the module a SECOND time, under its own name, with the environment
pointed at generated files. The two instances share no state: separate
`_LOG_PATH`, separate caches, separate position snapshots. Demo numbers cannot
reach a live endpoint because the code serving them is not the code serving the
live endpoint, and no request can change which is which.

The cost is one extra module in memory. The benefit is that the demo exercises
the real renderers — the funnel, the drawdown walk, the fee-drag maths — rather
than a parallel mock that would drift.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from services.demo.generator import materialize

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_SRC = _REPO_ROOT / "pathia" / "dashboard.py"
_MODULE_NAME = "pathia_dashboard_demo"


@contextmanager
def _environment(overrides: Dict[str, str]) -> Iterator[None]:
    """Apply env overrides for the duration of an import, then put it back.

    The demo module reads these at import; the live one must never see them, so
    the restore is not optional and not best-effort.
    """
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, was in saved.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was


# The modules that freeze a filesystem path at import time. `dashboard.py`
# reads every one of its datasets through these, so a second dashboard instance
# is only isolated if these are re-imported too — the first attempt at this
# loaded dashboard.py twice, both copies resolved `session_log` to the one
# already in sys.modules, and the "isolated" demo served the live log.
_PATH_FROZEN_MODULES = (
    "pathia.session_log",
    "pathia.positions_snapshot",
    "pathia.agents.config_store",
    "pathia.agents.memory",
)


def _load_demo_dashboard(data_dir: str) -> Any:
    """Import pathia/dashboard.py a second time, pointed at synthetic data.

    The live dashboard is imported FIRST and deliberately: it must bind the real
    paths before anything here touches the environment. Then the path-frozen
    modules above are lifted out of `sys.modules` so the demo import re-executes
    them under the demo environment and binds its own copies, and afterwards the
    live objects go back.

    Each dashboard instance holds direct references to the module objects it saw
    at its own import, so restoring `sys.modules` does not reach back into the
    demo instance. Two dashboards, two sets of paths, no shared mutable state.
    """
    import pathia.dashboard  # noqa: F401  — bind live paths before we shadow them

    overrides = materialize(data_dir)

    # Popping from sys.modules is only half of it. `dashboard.py` reaches these
    # as `from pathia import session_log`, which is an attribute lookup on the
    # already-imported package object — so the parent's attribute has to go too,
    # or the re-import resolves straight back to the live module. That was the
    # second failed attempt at this.
    shadowed = {name: sys.modules.pop(name)
                for name in _PATH_FROZEN_MODULES if name in sys.modules}
    shadowed_attrs = {}
    for name in shadowed:
        parent_name, _, attr = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, attr):
            shadowed_attrs[name] = (parent, attr, getattr(parent, attr))
            delattr(parent, attr)
    # dashboard.py itself must not be resolved from cache either.
    shadowed_dashboard = sys.modules.pop("pathia.dashboard", None)
    try:
        with _environment(overrides):
            spec = importlib.util.spec_from_file_location(_MODULE_NAME, _DASHBOARD_SRC)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"could not load {_DASHBOARD_SRC}")
            module = importlib.util.module_from_spec(spec)
            # Registered before exec so a circular import inside the module
            # resolves to this instance rather than re-entering the loader.
            sys.modules[_MODULE_NAME] = module
            spec.loader.exec_module(module)
    finally:
        # Put the live modules back, dropping the demo copies that took their
        # names during the import above.
        for name in _PATH_FROZEN_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(shadowed)
        for parent, attr, was in shadowed_attrs.values():
            setattr(parent, attr, was)
        if shadowed_dashboard is not None:
            sys.modules["pathia.dashboard"] = shadowed_dashboard
    return module


def build_demo_app(data_dir: str | None = None) -> FastAPI:
    """A FastAPI app serving the demo dashboard and its JSON API.

    Mount it at `/demo`. It carries its own static mount because a sub-app does
    not inherit the parent's, and its pages ask for `/static/...` relative to
    the site root — which the parent already serves, so the mount here exists
    only for the case where this app is run on its own.
    """
    data_dir = data_dir or os.path.join(tempfile.gettempdir(), "pathia-demo")
    module = _load_demo_dashboard(data_dir)

    app = FastAPI(
        title="pathia — demo",
        description="Synthetic trading history. No exchange connection, no credentials.",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    module.register_routes(app)

    # The house-account routes are operator-gated, and that gate reads the
    # environment per REQUEST — so setting PATHIA_PUBLIC_DASHBOARD during the
    # import above bought nothing, and the demo answered 401 to its own data.
    #
    # Overriding the dependency on THIS app is the scoped version of the same
    # intent: every number here was generated in this process a moment ago, so
    # there is no house account to protect. It cannot widen the live app's gate,
    # which an environment variable could.
    app.dependency_overrides[module._require_operator_role] = lambda: None

    static_dir = _REPO_ROOT / "pathia" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/demo/whoami")
    async def whoami() -> Dict[str, Any]:
        """States plainly what this sub-app is, for anything that asks."""
        return {"demo": True,
                "source": "services/demo/generator.py",
                "data_dir": data_dir}

    return app
