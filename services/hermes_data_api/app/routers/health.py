"""Liveness + service banner — the only unauthenticated routes in the API.

``GET /health`` is the machine-readable liveness probe (load balancers, uptime
checks, k8s). ``GET /`` is a tiny human-facing banner pointing at the docs. Both
are intentionally auth-free so a monitor can hit them without an API key.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..schemas import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> dict:
    """Report service liveness plus build identity, straight from settings."""
    s = get_settings()
    return {
        "status": "ok",
        "service": s.service_name,
        "version": s.version,
        "env": s.env,
    }


@router.get("/", summary="Service banner")
async def root() -> dict:
    """Minimal landing banner: what this is and where the interactive docs live."""
    s = get_settings()
    return {
        "service": s.service_name,
        "version": s.version,
        "env": s.env,
        "docs": "/docs",
    }
