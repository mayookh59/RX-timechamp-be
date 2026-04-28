"""Prometheus metrics middleware for FastAPI.

Collects request-level, database, storage, and agent metrics
for Prometheus scraping and Grafana visualization.
"""

import time
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from prometheus_client import Counter, Histogram, Gauge, Info

logger = structlog.stdlib.get_logger(__name__)

# ---------------------------------------------------------------------------
# Application info
# ---------------------------------------------------------------------------
APP_INFO = Info("trackme", "TrackMe application metadata")
APP_INFO.info({"version": "1.0.0", "environment": "production"})

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "trackme_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
)

REQUEST_DURATION = Histogram(
    "trackme_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ACTIVE_CONNECTIONS = Gauge(
    "trackme_active_connections",
    "Number of active HTTP connections",
)

# ---------------------------------------------------------------------------
# Database metrics
# ---------------------------------------------------------------------------
DB_QUERY_DURATION = Histogram(
    "trackme_db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0),
)

DB_POOL_SIZE = Gauge(
    "trackme_db_pool_size",
    "Current database connection pool size",
)

DB_POOL_CHECKED_OUT = Gauge(
    "trackme_db_pool_checked_out",
    "Number of currently checked-out database connections",
)

# ---------------------------------------------------------------------------
# S3 / storage metrics
# ---------------------------------------------------------------------------
S3_UPLOAD_COUNT = Counter(
    "trackme_s3_uploads_total",
    "Total number of S3 upload attempts",
    ["status"],  # "success" | "failure"
)

S3_UPLOAD_DURATION = Histogram(
    "trackme_s3_upload_duration_seconds",
    "S3 upload duration in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ---------------------------------------------------------------------------
# Agent metrics
# ---------------------------------------------------------------------------
AGENTS_ONLINE = Gauge(
    "trackme_agents_online",
    "Number of agents currently online",
)

AGENTS_OFFLINE = Gauge(
    "trackme_agents_offline",
    "Number of agents currently offline",
)

# ---------------------------------------------------------------------------
# Business metrics
# ---------------------------------------------------------------------------
ACTIVE_USERS = Gauge(
    "trackme_active_users",
    "Number of currently active users",
)

SCREENSHOT_PROCESSING_DURATION = Histogram(
    "trackme_screenshot_processing_duration_seconds",
    "Screenshot processing duration in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def _normalize_path(path: str) -> str:
    """Collapse dynamic path segments to avoid high-cardinality labels.

    Args:
        path: The raw request path.

    Returns:
        A normalized path string with UUIDs and numeric IDs replaced.
    """
    import re

    # Replace UUIDs
    path = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "{id}",
        path,
    )
    # Replace numeric IDs
    path = re.sub(r"/\d+", "/{id}", path)
    return path


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that records Prometheus metrics for every HTTP request.

    Tracks request count, duration, and active connections. Path segments
    containing dynamic IDs are normalized to prevent label explosion.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request and record metrics.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            The HTTP response after recording metrics.
        """
        # Skip metrics endpoint itself to avoid recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = _normalize_path(request.url.path)

        ACTIVE_CONNECTIONS.inc()
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            status_code = str(response.status_code)
        except Exception:
            status_code = "500"
            raise
        finally:
            duration = time.perf_counter() - start_time
            REQUEST_COUNT.labels(
                method=method, path=path, status_code=status_code
            ).inc()
            REQUEST_DURATION.labels(method=method, path=path).observe(duration)
            ACTIVE_CONNECTIONS.dec()

            logger.debug(
                "request_metrics",
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_seconds": round(duration, 4),
                },
            )

        return response


# ---------------------------------------------------------------------------
# Helper functions for instrumenting non-HTTP operations
# ---------------------------------------------------------------------------

def track_db_query(operation: str, table: str) -> Callable:
    """Return a context-manager-style timer for database queries.

    Args:
        operation: The SQL operation (SELECT, INSERT, UPDATE, DELETE).
        table: The target table name.

    Returns:
        A Histogram timer context manager.
    """
    return DB_QUERY_DURATION.labels(operation=operation, table=table).time()


def track_s3_upload() -> Callable:
    """Return a context-manager-style timer for S3 uploads.

    Returns:
        A Histogram timer context manager.
    """
    return S3_UPLOAD_DURATION.time()


def record_s3_upload_success() -> None:
    """Increment the S3 upload success counter."""
    S3_UPLOAD_COUNT.labels(status="success").inc()


def record_s3_upload_failure() -> None:
    """Increment the S3 upload failure counter."""
    S3_UPLOAD_COUNT.labels(status="failure").inc()


def update_agent_gauges(online: int, offline: int) -> None:
    """Set the current agent online/offline gauges.

    Args:
        online: Number of agents currently online.
        offline: Number of agents currently offline.
    """
    AGENTS_ONLINE.set(online)
    AGENTS_OFFLINE.set(offline)


def update_db_pool_metrics(pool_size: int, checked_out: int) -> None:
    """Set the current database connection pool metrics.

    Args:
        pool_size: Total pool size.
        checked_out: Number of currently checked-out connections.
    """
    DB_POOL_SIZE.set(pool_size)
    DB_POOL_CHECKED_OUT.set(checked_out)
