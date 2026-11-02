"""Outermost ASGI middleware.

Responsibilities, in order:
* assign/propagate a correlation ID (``X-Request-ID``) and W3C trace ID;
* reject oversized request bodies before they reach the application;
* record request count, latency and in-flight gauge;
* emit one structured access-log line per request;
* convert any unhandled exception into a generic JSON 500 (no stack trace leaks).

Implemented as raw ASGI rather than ``BaseHTTPMiddleware`` to avoid buffering
and to keep contextvars visible to the endpoint.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import request_id_var, trace_id_var
from app.metrics import ACTIVE_REQUESTS, REQUEST_DURATION, REQUESTS_TOTAL

logger = logging.getLogger("llmforge.access")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


class _BodyTooLarge(Exception):
    pass


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = _header(scope, b"x-request-id")
        request_id = incoming_id if incoming_id and _REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex
        traceparent = _header(scope, b"traceparent")
        match = _TRACEPARENT_RE.match(traceparent or "")
        rid_token = request_id_var.set(request_id)
        tid_token = trace_id_var.set(match.group(1) if match else None)

        method = scope["method"]
        status_code = 500
        response_started = False
        started = time.perf_counter()
        ACTIVE_REQUESTS.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.extend(SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        try:
            declared = _header(scope, b"content-length")
            if declared and declared.isdigit() and int(declared) > self.max_body_bytes:
                raise _BodyTooLarge
            await self.app(scope, self._limited_receive(receive), send_wrapper)
        except _BodyTooLarge:
            if not response_started:
                await self._send_error(
                    send_wrapper, 413, "payload_too_large", "Request body is too large.", request_id
                )
        except Exception:
            logger.exception("unhandled error while processing request")
            if not response_started:
                await self._send_error(
                    send_wrapper, 500, "internal_error", "An internal error occurred.", request_id
                )
        finally:
            duration = time.perf_counter() - started
            route = scope.get("route")
            route_label = getattr(route, "path", None) or "unmatched"
            REQUESTS_TOTAL.labels(method, route_label, str(status_code)).inc()
            REQUEST_DURATION.labels(method, route_label).observe(duration)
            ACTIVE_REQUESTS.dec()
            client = scope.get("client")
            logger.info(
                "request completed",
                extra={
                    "method": method,
                    "path": scope.get("path"),
                    "route": route_label,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "client_ip": client[0] if client else None,
                },
            )
            request_id_var.reset(rid_token)
            trace_id_var.reset(tid_token)

    def _limited_receive(self, receive: Receive) -> Receive:
        received = 0

        async def wrapped() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _BodyTooLarge
            return message

        return wrapped

    @staticmethod
    async def _send_error(send: Send, status: int, code: str, message: str, request_id: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
