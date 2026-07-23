"""Hermes Data API — application factory. Wires config, logging, DB, middleware,
error handlers, and routers into a single ASGI app. Entrypoint: `app.main:app`."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import get_settings
from .db import ApiKeyStore, init_db
from .errors import install_error_handlers
from .logging_setup import setup_logging
from .rate_limit import RateLimitMiddleware
from .routers import health, market

logger = logging.getLogger("hermes.main")


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level, s.log_json)
    init_db()
    # dev convenience: a known key so the SDK/tests/curl work out of the box.
    if s.env == "dev":
        try:
            ApiKeyStore.seed_demo_key("demo-token")
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_demo_key skipped: %s", exc)

    app = FastAPI(
        title=s.service_name, version=s.version,
        description="First-party market-data platform (UW-compatible contract, our own "
                    "resources). Licensed-data endpoints are honest adapter slots.",
    )
    app.add_middleware(RateLimitMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(market.router)
    logger.info("hermes-data-api ready", extra={"extra_fields": {"env": s.env, "version": s.version}})
    return app


app = create_app()
