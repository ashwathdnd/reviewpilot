from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

# --- Rate limiter ------------------------------------------------------------

# Token bucket: max 5 requests per second for webhooks.
# GitHub sends at most one webhook per PR action; bursts of 3 are normal
# (opened + initial push + sync).  5 req/s allows headroom.
_RATE_LIMIT_WINDOW = 1.0  # seconds
_MAX_REQUESTS = 5

_rate_limit_state: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str) -> bool:
    """Return True if the request should be allowed."""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW
    bucket = _rate_limit_state[key]
    # Evict stale entries.
    bucket[:] = [t for t in bucket if t > window_start]
    if len(bucket) >= _MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


# --- Request ID middleware ----------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request.

    The ID is available to other middleware and route handlers via
    ``request_id_ctx`` and is returned in the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = str(uuid.uuid4())[:8]
        request_id_ctx.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# --- Rate limit middleware ----------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply token-bucket rate limiting to the /webhook endpoint."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        from app.config import get_settings

        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        if request.url.path.rstrip("/") == "/webhook":
            key = request.client.host if request.client else "unknown"
            if not _check_rate_limit(key):
                return Response(
                    content='{"detail":"Too many requests"}',
                    status_code=429,
                    media_type="application/json",
                )
        return await call_next(request)


# --- Request logging middleware ----------------------------------------------


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        rid = request_id_ctx.get("")
        logger.info(
            "%s %s → %d  %.1fms  [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            rid,
        )
        return response


# --- Error handlers ----------------------------------------------------------


def register_error_handlers(app):
    """Register structured error handlers for common HTTP and app errors."""

    from app.exceptions import (
        ConfigurationError,
        GitHubAPIError,
        GitHubAuthError,
        LLMError,
        ReviewError,
        ReviewPilotError,
        WebhookValidationError,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.warning("HTTP %d on %s: %s", exc.status_code, request.url.path, exc.detail)
        return Response(
            content=f'{{"detail":"{exc.detail}","status":{exc.status_code}}}',
            status_code=exc.status_code,
            media_type="application/json",
        )

    @app.exception_handler(WebhookValidationError)
    async def webhook_error_handler(request: Request, exc: WebhookValidationError):
        return Response(
            content=f'{{"detail":"{exc.message}"}}',
            status_code=401,
            media_type="application/json",
        )

    @app.exception_handler(GitHubAuthError)
    async def auth_error_handler(request: Request, exc: GitHubAuthError):
        logger.error("GitHub auth error: %s", exc.message)
        return Response(
            content=f'{{"detail":"Authentication failed","error":"{exc.message}"}}',
            status_code=502,
            media_type="application/json",
        )

    @app.exception_handler(GitHubAPIError)
    async def github_error_handler(request: Request, exc: GitHubAPIError):
        logger.error("GitHub API error: %s", exc.message)
        return Response(
            content=f'{{"detail":"GitHub API request failed","error":"{exc.message}"}}',
            status_code=502,
            media_type="application/json",
        )

    @app.exception_handler(LLMError)
    async def llm_error_handler(request: Request, exc: LLMError):
        logger.error("LLM error: %s", exc.message)
        return Response(
            content=f'{{"detail":"AI review generation failed","error":"{exc.message}"}}',
            status_code=502,
            media_type="application/json",
        )

    @app.exception_handler(ReviewError)
    async def review_error_handler(request: Request, exc: ReviewError):
        logger.error("Review error: %s", exc.message)
        return Response(
            content=f'{{"detail":"Review processing failed","error":"{exc.message}"}}',
            status_code=500,
            media_type="application/json",
        )

    @app.exception_handler(ConfigurationError)
    async def config_error_handler(request: Request, exc: ConfigurationError):
        logger.critical("Configuration error: %s", exc.message)
        return Response(
            content=f'{{"detail":"Server configuration error","error":"{exc.message}"}}',
            status_code=500,
            media_type="application/json",
        )

    @app.exception_handler(ReviewPilotError)
    async def base_error_handler(request: Request, exc: ReviewPilotError):
        logger.error("Unhandled app error: %s", exc.message)
        return Response(
            content=f'{{"detail":"Internal error","error":"{exc.message}"}}',
            status_code=500,
            media_type="application/json",
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        rid = request_id_ctx.get("")
        logger.exception("Unhandled exception [%s]: %s", rid, exc)
        return Response(
            content=f'{{"detail":"Internal server error","request_id":"{rid}"}}',
            status_code=500,
            media_type="application/json",
        )
