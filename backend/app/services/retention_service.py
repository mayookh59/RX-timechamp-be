"""Data retention service for automatic purging of expired records.

Enforces configurable retention policies by deleting activity data
and screenshots that have exceeded their retention period. Uses
batch deletion for performance on large datasets.
"""

from datetime import datetime, timedelta, timezone

import boto3
import structlog
from botocore.config import Config as BotoConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity import ActivitySession, AppUsage, Screenshot, UrlVisit
from app.storage.database import async_session_factory

logger = structlog.stdlib.get_logger(__name__)

# Retention periods in days
ACTIVITY_RETENTION_DAYS = 90
SCREENSHOT_RETENTION_DAYS = 30

# Batch size for deletion operations
DELETION_BATCH_SIZE = 500


def _get_s3_client():
    """Create a configured boto3 S3 client for screenshot cleanup.

    Returns:
        A boto3 S3 client configured with application settings.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2},
        ),
    )


async def purge_expired_records(
    activity_retention_days: int = ACTIVITY_RETENTION_DAYS,
) -> dict[str, int]:
    """Delete activity records that have exceeded the retention period.

    Purges ActivitySession, AppUsage, and UrlVisit records older than
    the configured retention window. Uses batch deletion for performance.

    Args:
        activity_retention_days: Number of days to retain activity data.
            Defaults to ACTIVITY_RETENTION_DAYS (90).

    Returns:
        Dict mapping table names to the number of records deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=activity_retention_days)
    results: dict[str, int] = {}

    logger.info(
        "retention_purge_started",
        cutoff=cutoff.isoformat(),
        retention_days=activity_retention_days,
    )

    async with async_session_factory() as db:
        try:
            # Purge activity sessions
            results["activity_sessions"] = await _batch_delete_by_time(
                db, ActivitySession, ActivitySession.start_time, cutoff
            )

            # Purge app usage
            results["app_usage"] = await _batch_delete_by_time(
                db, AppUsage, AppUsage.start_time, cutoff
            )

            # Purge URL visits
            results["url_visits"] = await _batch_delete_by_time(
                db, UrlVisit, UrlVisit.visit_time, cutoff
            )

            await db.commit()

        except Exception as exc:
            await db.rollback()
            logger.error("retention_purge_failed", error=str(exc))
            raise

    logger.info("retention_purge_completed", results=results)
    return results


async def _batch_delete_by_time(
    db: AsyncSession,
    model: type,
    time_column,
    cutoff: datetime,
) -> int:
    """Delete records older than a cutoff date in batches.

    Args:
        db: Async database session.
        model: SQLAlchemy model class to delete from.
        time_column: The datetime column to filter on.
        cutoff: Records older than this datetime will be deleted.

    Returns:
        Total number of records deleted.
    """
    total_deleted = 0

    while True:
        # Select a batch of IDs to delete
        id_stmt = (
            select(model.id)
            .where(time_column < cutoff)
            .limit(DELETION_BATCH_SIZE)
        )
        result = await db.execute(id_stmt)
        ids = list(result.scalars().all())

        if not ids:
            break

        del_stmt = delete(model).where(model.id.in_(ids))
        del_result = await db.execute(del_stmt)
        total_deleted += del_result.rowcount or 0
        await db.flush()

        logger.debug(
            "retention_batch_deleted",
            table=model.__tablename__,
            batch_count=len(ids),
            total_deleted=total_deleted,
        )

    return total_deleted


async def purge_expired_screenshots(
    screenshot_retention_days: int = SCREENSHOT_RETENTION_DAYS,
) -> dict[str, int]:
    """Delete expired screenshots from the database and S3 storage.

    Fetches screenshots older than the retention period, deletes their
    S3 objects, and then removes the database records in batches.

    Args:
        screenshot_retention_days: Number of days to retain screenshots.
            Defaults to SCREENSHOT_RETENTION_DAYS (30).

    Returns:
        Dict with db_deleted and s3_deleted counts plus s3_errors count.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=screenshot_retention_days)
    results: dict[str, int] = {
        "db_deleted": 0,
        "s3_deleted": 0,
        "s3_errors": 0,
    }

    logger.info(
        "screenshot_purge_started",
        cutoff=cutoff.isoformat(),
        retention_days=screenshot_retention_days,
    )

    s3_client = _get_s3_client()

    async with async_session_factory() as db:
        try:
            while True:
                stmt = (
                    select(Screenshot)
                    .where(Screenshot.captured_at < cutoff)
                    .limit(DELETION_BATCH_SIZE)
                )
                result = await db.execute(stmt)
                screenshots = list(result.scalars().all())

                if not screenshots:
                    break

                # Delete S3 objects first
                for screenshot in screenshots:
                    try:
                        s3_client.delete_object(
                            Bucket=settings.S3_BUCKET,
                            Key=screenshot.storage_key,
                        )
                        results["s3_deleted"] += 1
                    except Exception as exc:
                        results["s3_errors"] += 1
                        logger.warning(
                            "screenshot_s3_delete_failed",
                            screenshot_id=str(screenshot.id),
                            storage_key=screenshot.storage_key,
                            error=str(exc),
                        )

                # Delete database records
                ids = [s.id for s in screenshots]
                del_stmt = delete(Screenshot).where(Screenshot.id.in_(ids))
                del_result = await db.execute(del_stmt)
                results["db_deleted"] += del_result.rowcount or 0
                await db.flush()

                logger.debug(
                    "screenshot_purge_batch",
                    batch_count=len(screenshots),
                    total_db_deleted=results["db_deleted"],
                    total_s3_deleted=results["s3_deleted"],
                )

            await db.commit()

        except Exception as exc:
            await db.rollback()
            logger.error("screenshot_purge_failed", error=str(exc))
            raise

    logger.info("screenshot_purge_completed", results=results)
    return results
