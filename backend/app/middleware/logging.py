"""Correlation ID middleware for request tracing.

Adds a unique correlation ID to every request for distributed tracing.
The ID is propagated through structlog context and returned in
response headers.
"""

import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Context variable for correlation ID accessible across async tasks
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

CORRELATION_ID_HEADER = "X-Correlation-ID"

logger = structlog.stdlib.get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns and propagates correlation IDs.

    Extracts an existing correlation ID from the request header
    or generates a new one. The ID is stored in a context variable,
    bound to structlog, and returned in the response header.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request with correlation ID tracking.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response with the correlation ID header attached.
        """
        # Extract or generate correlation ID
        request_correlation_id = request.headers.get(
            CORRELATION_ID_HEADER, str(uuid.uuid4())
        )
        correlation_id_ctx.set(request_correlation_id)

        # Bind correlation ID to structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=request_correlation_id,
            method=request.method,
            path=request.url.path,
        )

        logger.info(
            "request_started",
            client_host=request.client.host if request.client else "unknown",
        )

        response = await call_next(request)

        response.headers[CORRELATION_ID_HEADER] = request_correlation_id

        logger.info(
            "request_completed",
            status_code=response.status_code,
        )

        return response
