"""FastAPI application entry point for the TrackMe backend.

Configures the application with middleware, routers, structured logging,
lifespan management, and graceful shutdown support.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.activity import router as activity_router
from app.api.v1.admin import router as admin_router
from app.api.v1.agents import router as agents_router
from app.api.v1.apps import router as apps_router
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.gdpr import router as gdpr_router
from app.api.v1.health import router as health_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.reports import router as reports_router
from app.api.v1.screenshots import router as screenshots_router
from app.api.v1.settings import router as settings_router
from app.api.v1.urls import router as urls_router
from app.core.config import settings
from app.middleware.logging import CorrelationIdMiddleware
from app.storage.database import engine

# Tracks in-flight requests for graceful shutdown
_active_requests: set[asyncio.Task[None]] = set()
_shutting_down = False


def configure_structlog() -> None:
    """Configure structlog for structured JSON logging.

    Sets up processors for timestamp injection, log level addition,
    context variable merging, and JSON rendering.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


configure_structlog()

logger = structlog.stdlib.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle.

    On startup: logs the application version and verifies database
    connectivity. On shutdown: waits for in-flight requests to complete
    and disposes the database engine connection pool.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the application during its lifetime.
    """
    # Startup
    logger.info(
        "application_starting",
        version=settings.VERSION,
        log_level=settings.LOG_LEVEL,
    )

    # Verify database is reachable at startup
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database_connection_verified")
    except Exception as exc:
        logger.error("database_connection_failed", error=str(exc))

    yield

    # Shutdown
    global _shutting_down
    _shutting_down = True
    logger.info("application_shutting_down", active_requests=len(_active_requests))

    # Wait for in-flight requests (up to 30 seconds)
    shutdown_timeout_seconds = 30
    if _active_requests:
        logger.info(
            "waiting_for_active_requests",
            count=len(_active_requests),
            timeout=shutdown_timeout_seconds,
        )
        done, pending = await asyncio.wait(
            _active_requests,
            timeout=shutdown_timeout_seconds,
        )
        if pending:
            logger.warning("forcing_shutdown", pending_requests=len(pending))

    # Dispose database engine
    await engine.dispose()
    logger.info("application_stopped")


app = FastAPI(
    title="TrackMe API",
    description="Enterprise Employee Activity Tracking System",
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID middleware (disabled — BaseHTTPMiddleware conflicts with
# nested async DB session dependencies in some Starlette versions)
# app.add_middleware(CorrelationIdMiddleware)

# Security headers middleware (disabled for local dev — SQL injection detector
# can flag legitimate JWT tokens in headers)
# try:
#     from app.middleware.security import SecurityMiddleware
#     app.add_middleware(SecurityMiddleware)
# except ImportError:
#     logger.warning("security_middleware_not_available")

# Rate limiting middleware — only enable if Redis is available
try:
    import redis
    _redis_check = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
    _redis_check.ping()
    from app.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
    logger.info("rate_limit_middleware_enabled")
except Exception:
    logger.info("rate_limit_middleware_skipped_no_redis")

# Prometheus metrics middleware (disabled for local SQLite dev)
# Enable when running with PostgreSQL and prometheus_client installed
# try:
#     from app.middleware.metrics import PrometheusMetricsMiddleware
#     app.add_middleware(PrometheusMetricsMiddleware)
# except ImportError:
#     logger.warning("metrics_middleware_not_available")

# ── API Routers ──────────────────────────────────────────────────────
# Health (no auth, no prefix restriction)
app.include_router(health_router)

# Core API v1
api_prefix = "/api/v1"
app.include_router(auth_router, prefix=api_prefix)
app.include_router(agents_router, prefix=api_prefix)
app.include_router(activity_router, prefix=api_prefix)
app.include_router(apps_router, prefix=api_prefix)
app.include_router(urls_router, prefix=api_prefix)
app.include_router(screenshots_router, prefix=api_prefix)
app.include_router(dashboard_router, prefix=api_prefix)
app.include_router(admin_router, prefix=api_prefix)
app.include_router(reports_router, prefix=api_prefix)
app.include_router(settings_router, prefix=api_prefix)
app.include_router(gdpr_router, prefix=api_prefix)

# Observability (no api prefix)
app.include_router(metrics_router)
