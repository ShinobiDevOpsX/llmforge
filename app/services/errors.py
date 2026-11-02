"""Gateway-level errors. HTTP status mapping lives in ``app.api.errors``."""

from __future__ import annotations


class ServiceError(Exception):
    code = "service_error"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class AuthenticationError(ServiceError):
    code = "unauthorized"


class ModelNotAllowedError(ServiceError):
    code = "model_not_allowed"


class PromptTooLongError(ServiceError):
    code = "prompt_too_long"


class InvalidParameterError(ServiceError):
    code = "invalid_parameter"


class RateLimitedError(ServiceError):
    code = "rate_limited"


class OverloadedError(ServiceError):
    code = "overloaded"
