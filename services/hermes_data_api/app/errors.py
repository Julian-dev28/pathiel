"""RFC 7807 problem+json error handlers for the FastAPI app.

``install_error_handlers(app)`` replaces FastAPI's default JSON error shape with
``application/problem+json`` bodies (type, title, status, detail, instance) for:

  * ``fastapi.HTTPException`` / ``starlette.HTTPException`` — expected 4xx/5xx.
  * ``fastapi.exceptions.RequestValidationError`` — request body/query validation (422).
  * bare ``Exception`` — anything unhandled (500). Stack traces are logged server-side
    with the request id and NEVER returned to the client.

Bodies are built as plain dicts (not via ``app.schemas.Problem``) so this module has no
hard dependency on the schema package and cannot be broken by a schema import error.
"""
from __future__ import annotations

import http
import logging
from typing import Any, Optional

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .logging_setup import new_request_id, request_id

log = logging.getLogger("hermes.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"
_DEFAULT_TYPE = "about:blank"


def _title_for(status_code: int) -> str:
    """Reason phrase for a status code, e.g. 404 -> 'Not Found'."""
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _resolve_request_id(request: Request) -> str:
    """Best-effort request id: ContextVar, else scope state, else mint a fresh one.

    The ContextVar set inside the rate-limit middleware may not always propagate to the
    exception-handling context, so fall back to ``request.state`` (scope-backed, shared
    across the middleware stack) before generating a new id.
    """
    rid = request_id.get()
    if rid and rid != "-":
        return rid
    rid = getattr(getattr(request, "state", None), "request_id", None)
    if rid:
        return rid
    return new_request_id()


def _problem_response(
    *,
    status_code: int,
    detail: str,
    instance: str,
    request_id_value: str,
    type_: str = _DEFAULT_TYPE,
    title: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title or _title_for(status_code),
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    if extra:
        # RFC 7807 permits extension members alongside the standard fields.
        body.update(extra)

    resp_headers = {"X-Request-Id": request_id_value}
    if headers:
        resp_headers.update(headers)

    return JSONResponse(
        body,
        status_code=status_code,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=resp_headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register RFC 7807 handlers on ``app``. Call once at startup."""

    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = _resolve_request_id(request)
        status_code = exc.status_code
        detail = exc.detail if isinstance(exc.detail, str) else _title_for(status_code)

        # Preserve auth/rate headers the raiser attached (WWW-Authenticate, Retry-After...).
        passthrough = dict(getattr(exc, "headers", None) or {})

        if status_code >= 500:
            log.error(
                "http_exception status=%s path=%s detail=%s request_id=%s",
                status_code, request.url.path, detail, rid,
            )

        return _problem_response(
            status_code=status_code,
            detail=detail,
            instance=request.url.path,
            request_id_value=rid,
            headers=passthrough,
        )

    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _resolve_request_id(request)
        # jsonable_encoder makes the raw pydantic errors JSON-safe (ctx may hold exceptions).
        errors = jsonable_encoder(exc.errors())
        return _problem_response(
            status_code=422,
            detail="Request validation failed. See 'errors' for field-level detail.",
            instance=request.url.path,
            request_id_value=rid,
            extra={"errors": errors},
        )

    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = _resolve_request_id(request)
        # Full traceback to server logs only — never to the client.
        log.exception(
            "unhandled_exception path=%s request_id=%s error=%s",
            request.url.path, rid, exc.__class__.__name__,
        )
        return _problem_response(
            status_code=500,
            detail="An internal error occurred. Reference the request id when contacting support.",
            instance=request.url.path,
            request_id_value=rid,
        )

    # FastAPI's HTTPException subclasses Starlette's; register both explicitly so the
    # mapping is unambiguous regardless of which class a route raises.
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(FastAPIHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
