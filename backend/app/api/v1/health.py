"""Health check endpoints for Kubernetes probes.

Provides liveness and readiness endpoints. Liveness is a simple
heartbeat. Readiness checks database, Redis, and S3 connectivity.
"""

from typing import Any

import structlog
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.storage.database import async_session_factory

router = APIRouter(prefix="/health", tags=["health"])

logger = structlog.stdlib.get_logger(__name__)


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Kubernetes liveness probe endpoint.

    Returns a simple OK response to indicate the process is alive.
    No dependency checks are performed.

    Returns:
        Dict with status "ok".
    """
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> dict[str, Any]:
    """Kubernetes readiness probe endpoint.

    Checks connectivity to all critical dependencies:
    - PostgreSQL database
    - Redis cache
    - S3-compatible object storage

    Returns:
        Dict with overall status and individual check results.
    """
    checks: dict[str, str] = {}

    # Check database connectivity
    checks["database"] = await _check_database()

    # Check Redis connectivity
    checks["redis"] = await _check_redis()

    # Check S3 connectivity
    checks["s3"] = await _check_s3()

    all_healthy = all(status == "ok" for status in checks.values())
    overall_status = "ok" if all_healthy else "degraded"

    if not all_healthy:
        logger.warning("readiness_check_degraded", checks=checks)

    return {
        "status": overall_status,
        "version": settings.VERSION,
        "checks": checks,
    }


async def _check_database() -> str:
    """Check PostgreSQL database connectivity.

    Executes a simple SELECT 1 query to verify the database
    connection pool is functional.

    Returns:
        "ok" if the database is reachable, "error" otherwise.
    """
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.error("database_health_check_failed", error=str(exc))
        return "error"


async def _check_redis() -> str:
    """Check Redis connectivity.

    Sends a PING command to the Redis server.

    Returns:
        "ok" if Redis responds, "error" otherwise.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await client.ping()
            return "ok"
        finally:
            await client.aclose()
    except Exception as exc:
        logger.error("redis_health_check_failed", error=str(exc))
        return "error"


async def _check_s3() -> str:
    """Check S3-compatible storage connectivity.

    Attempts to perform a HEAD request on the configured S3 bucket
    using boto3 to verify access.

    Returns:
        "ok" if S3 is reachable, "error" otherwise.
    """
    try:
        import boto3
        from botocore.config import Config as BotoConfig

        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=BotoConfig(
                connect_timeout=5,
                read_timeout=5,
                retries={"max_attempts": 1},
            ),
        )
        s3_client.head_bucket(Bucket=settings.S3_BUCKET)
        return "ok"
    except Exception as exc:
        logger.error("s3_health_check_failed", error=str(exc))
        return "error"


@router.get("/debug-db")
async def debug_db():
    import os, re, traceback
    from app.core.config import settings
    from app.storage.database import _db_url, _is_sqlite, async_session_factory
    raw_os = os.environ.get("DATABASE_URL", "NOT_SET")
    masked_os = re.sub(r'://([^:]+):([^@]+)@', r'://\\1:***@', raw_os) if raw_os != "NOT_SET" else "NOT_SET"
    masked_db = re.sub(r'://([^:]+):([^@]+)@', r'://\\1:***@', _db_url)
    # Try actual DB connection
    db_test = "not_tested"
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text as sa_text
            result = await session.execute(sa_text("SELECT COUNT(*) FROM users"))
            count = result.scalar()
            db_test = f"OK - {count} users"
    except Exception as e:
        db_test = f"FAILED: {type(e).__name__}: {str(e)[:200]}"
    return {"os_environ": masked_os, "engine_url": masked_db, "db_test": db_test}
