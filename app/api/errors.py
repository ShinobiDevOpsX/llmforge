"""Exception -> HTTP response mapping.

Every error response has the same shape (``{"error": {...}}``), carries the
request ID, and contains only messages written for API consumers.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import request_id_var
from app.providers.base import (
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.errors import (
    AuthenticationError,
    InvalidParameterError,
    ModelNotAllowedError,
    OverloadedError,
    PromptTooLongError,
    RateLimitedError,
    ServiceError,
)

logger = logging.getLogger(__name__)

_SERVICE_STATUS: dict[type[ServiceError], int] = {
    AuthenticationError: 401,
    ModelNotAllowedError: 400,
    InvalidParameterError: 422,
    PromptTooLongError: 422,
    RateLimitedError: 429,
    OverloadedError: 503,
}

# (status, public code) per provider error type; most specific first.
_PROVIDER_STATUS: list[tuple[type[ProviderError], int, str]] = [
    (ProviderTimeoutError, 504, "runtime_timeout"),
    (ProviderUnavailableError, 503, "runtime_unavailable"),
    (ModelNotFoundError, 503, "model_not_available"),
    (ProviderError, 502, "runtime_error"),
]


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    details: list[dict] | None = None,
) -> JSONResponse:
    body: dict = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
    status = next((s for cls, s in _SERVICE_STATUS.items() if isinstance(exc, cls)), 400)
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "ApiKey"
    return error_response(status, exc.code, exc.message, headers=headers or None)


async def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    for cls, status, code in _PROVIDER_STATUS:
        if isinstance(exc, cls):
            logger.warning("provider error", extra={"error_type": exc.error_type, "status_code": status})
            return error_response(status, code, exc.message)
    raise AssertionError("unreachable")  # pragma: no cover


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors include the offending input; drop it so prompts are
    # never echoed back (or into logs by intermediaries).
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed.", details=details)


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ServiceError, _service_error)  # type: ignore[arg-type]
    app.add_exception_handler(ProviderError, _provider_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
