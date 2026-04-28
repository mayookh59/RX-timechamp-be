"""Distributed rate limiting middleware using Redis sliding window.

Enforces per-client rate limits using a Redis-backed sliding window
algorithm. Supports different limits for agent (API key) and dashboard
(JWT) authentication flows. Returns 429 with Retry-After header when
limits are exceeded.
"""

import time
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = structlog.stdlib.get_logger(__name__)

# Rate limit configuration
AGENT_RATE_LIMIT = 100  # requests per minute per agent (by API key)
DASHBOARD_RATE_LIMIT = 60  # requests per minute per dashboard user (by JWT sub)
WINDOW_SECONDS = 60

# Redis key prefix for rate limiting
REDIS_KEY_PREFIX = "ratelimit:"


def _get_redis_client():
    """Create an async Redis client for rate limiting.

    Returns:
        An aioredis/redis.asyncio client instance.

    Raises:
        ImportError: If redis package is not installed.
    """
    import redis.asyncio as aioredis

    return aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )


async def _check_rate_limit_redis(
    client_key: str,
    max_requests: int,
    window_seconds: int = WINDOW_SECONDS,
) -> tuple[bool, int, int]:
    """Check rate limit using Redis sliding window algorithm.

    Uses a sorted set with timestamps as scores to implement a
    sliding window. Entries outside the window are pruned on each check.

    Args:
        client_key: Unique identifier for the client.
        max_requests: Maximum allowed requests in the window.
        window_seconds: Size of the sliding window in seconds.

    Returns:
        Tuple of (is_allowed, remaining_requests, retry_after_seconds).
    """
    redis_client = _get_redis_client()
    redis_key = f"{REDIS_KEY_PREFIX}{client_key}"
    now = time.time()
    window_start = now - window_seconds

    try:
        pipe = redis_client.pipeline()

        # Remove entries outside the sliding window
        pipe.zremrangebyscore(redis_key, 0, window_start)

        # Count current entries in the window
        pipe.zcard(redis_key)

        # Add the current request timestamp
        pipe.zadd(redis_key, {f"{now}": now})

        # Set TTL to auto-expire the key
        pipe.expire(redis_key, window_seconds + 1)

        results = await pipe.execute()
        current_count = results[1]  # zcard result

        await redis_client.aclose()

        if current_count >= max_requests:
            # Calculate when the oldest entry in the window will expire
            retry_after = int(window_seconds - (now - window_start))
            retry_after = max(retry_after, 1)
            remaining = 0
            return False, remaining, retry_after

        remaining = max(0, max_requests - current_count - 1)
        return True, remaining, 0

    except Exception as exc:
        logger.error("rate_limit_redis_error", error=str(exc))
        # Fail open: allow the request if Redis is unavailable
        try:
            await redis_client.aclose()
        except Exception:
            pass
        return True, max_requests, 0


def _extract_client_key(request: Request) -> tuple[str, int]:
    """Extract the rate limit client key and applicable limit from a request.

    Determines whether the request is from an agent (API key) or
    dashboard user (JWT) and returns the appropriate key and limit.

    Args:
        request: The incoming HTTP request.

    Returns:
        Tuple of (client_key, max_requests).
    """
    # Check for agent authentication via API key header
    api_key = request.headers.get("X-API-Key")
    device_id = request.headers.get("X-Device-Id")

    if api_key and device_id:
        return f"agent:{device_id}", AGENT_RATE_LIMIT

    # Check for JWT-based dashboard authentication
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Extract sub claim from JWT without full validation
        # (full validation happens in auth middleware)
        try:
            import json
            import base64

            # Decode JWT payload (middle segment)
            parts = token.split(".")
            if len(parts) == 3:
                # Add padding for base64 decoding
                payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                sub = payload.get("sub", "unknown")
                return f"user:{sub}", DASHBOARD_RATE_LIMIT
        except Exception:
            pass

    # Fallback: rate limit by IP address
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}", DASHBOARD_RATE_LIMIT


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware for distributed rate limiting via Redis.

    Intercepts all requests, identifies the client, checks the rate
    limit using a Redis sliding window, and returns 429 with
    Retry-After header when limits are exceeded.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request through the rate limiter.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            The response, either from the handler or a 429 error.
        """
        # Skip rate limiting for health checks
        if request.url.path in ("/api/v1/health", "/api/v1/health/ready"):
            return await call_next(request)

        client_key, max_requests = _extract_client_key(request)

        try:
            is_allowed, remaining, retry_after = await _check_rate_limit_redis(
                client_key, max_requests
            )
        except Exception as exc:
            # Fail open on any error
            logger.error("rate_limit_check_error", error=str(exc))
            return await call_next(request)

        if not is_allowed:
            logger.warning(
                "rate_limit_exceeded",
                client_key=client_key,
                max_requests=max_requests,
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        response = await call_next(request)

        # Add rate limit headers to successful responses
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
