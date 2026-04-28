"""Celery pipeline tasks for periodic data aggregation and maintenance.

Defines scheduled tasks that run daily, weekly, and monthly to
compute aggregation summaries, archive old screenshots, and
clean up temporary files.
"""

import logging
import os
import glob
import shutil
from datetime import date, datetime, timedelta
from typing import Any, Dict

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.app.services.aggregation_service import AggregationService
from backend.app.services.archive_service import ArchiveService

logger = logging.getLogger(__name__)

# Database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://trackme:changeme@localhost:5432/trackme",
)
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "trackme-screenshots")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/trackme")


def _get_async_session() -> AsyncSession:
    """Create an async database session for task execution.

    Returns:
        A new async SQLAlchemy session instance.
    """
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


# =========================================================================
# Daily aggregation task
# =========================================================================

@shared_task(
    name="pipeline.run_daily_aggregation",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_daily_aggregation(self, target_date_str: str = None) -> Dict[str, Any]:
    """Run daily per-user activity aggregation.

    Computes a summary for each user's activity on the target date,
    including active/idle hours, top applications, and productivity
    scores.

    Args:
        target_date_str: ISO date string (YYYY-MM-DD) for the date
            to aggregate. Defaults to yesterday.

    Returns:
        A dictionary with aggregation statistics.
    """
    import asyncio

    target_date = (
        date.fromisoformat(target_date_str)
        if target_date_str
        else date.today() - timedelta(days=1)
    )

    logger.info(
        "task_daily_aggregation_start",
        extra={"target_date": target_date.isoformat(), "task_id": self.request.id},
    )

    async def _run() -> Dict[str, Any]:
        async with _get_async_session() as session:
            service = AggregationService(session)
            return await service.run_daily_aggregation(target_date)

    try:
        result = asyncio.run(_run())
        logger.info(
            "task_daily_aggregation_complete",
            extra={"result": result, "task_id": self.request.id},
        )
        return result
    except Exception as exc:
        logger.exception(
            "task_daily_aggregation_failed",
            extra={"task_id": self.request.id},
        )
        raise self.retry(exc=exc)


# =========================================================================
# Weekly aggregation task
# =========================================================================

@shared_task(
    name="pipeline.run_weekly_aggregation",
    bind=True,
    max_retries=3,
    default_retry_delay=600,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_weekly_aggregation(self, week_start_str: str = None) -> Dict[str, Any]:
    """Run weekly per-team productivity aggregation.

    Rolls up daily user summaries into team-level weekly metrics
    including average productivity, total hours, and participation.

    Args:
        week_start_str: ISO date string (YYYY-MM-DD) for the Monday
            of the target week. Defaults to the previous completed week.

    Returns:
        A dictionary with aggregation statistics.
    """
    import asyncio

    week_start = (
        date.fromisoformat(week_start_str) if week_start_str else None
    )

    logger.info(
        "task_weekly_aggregation_start",
        extra={
            "week_start": week_start.isoformat() if week_start else "auto",
            "task_id": self.request.id,
        },
    )

    async def _run() -> Dict[str, Any]:
        async with _get_async_session() as session:
            service = AggregationService(session)
            return await service.run_weekly_aggregation(week_start)

    try:
        result = asyncio.run(_run())
        logger.info(
            "task_weekly_aggregation_complete",
            extra={"result": result, "task_id": self.request.id},
        )
        return result
    except Exception as exc:
        logger.exception(
            "task_weekly_aggregation_failed",
            extra={"task_id": self.request.id},
        )
        raise self.retry(exc=exc)


# =========================================================================
# Archive old screenshots task
# =========================================================================

@shared_task(
    name="pipeline.archive_old_screenshots",
    bind=True,
    max_retries=3,
    default_retry_delay=600,
    acks_late=True,
    reject_on_worker_lost=True,
)
def archive_old_screenshots(self, batch_size: int = 100) -> Dict[str, Any]:
    """Archive old screenshots to Glacier and delete expired ones.

    Runs a two-phase lifecycle operation:
    1. Move screenshots older than 30 days to Glacier storage class.
    2. Permanently delete screenshots older than 90 days.

    Args:
        batch_size: Number of screenshots to process per phase.

    Returns:
        A dictionary with archive and deletion statistics.
    """
    import asyncio

    logger.info(
        "task_archive_screenshots_start",
        extra={"batch_size": batch_size, "task_id": self.request.id},
    )

    async def _run() -> Dict[str, Any]:
        async with _get_async_session() as session:
            service = ArchiveService(
                session=session,
                s3_bucket=S3_BUCKET,
            )

            archive_stats = await service.archive_old_screenshots(
                batch_size=batch_size
            )
            delete_stats = await service.delete_expired_screenshots(
                batch_size=batch_size
            )

            return {
                "archive": archive_stats,
                "delete": delete_stats,
            }

    try:
        result = asyncio.run(_run())
        logger.info(
            "task_archive_screenshots_complete",
            extra={"result": result, "task_id": self.request.id},
        )
        return result
    except Exception as exc:
        logger.exception(
            "task_archive_screenshots_failed",
            extra={"task_id": self.request.id},
        )
        raise self.retry(exc=exc)


# =========================================================================
# Cleanup temporary files task
# =========================================================================

@shared_task(
    name="pipeline.cleanup_temp_files",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def cleanup_temp_files(
    self,
    max_age_hours: int = 24,
) -> Dict[str, Any]:
    """Remove temporary files older than the specified age.

    Cleans up the application temp directory by removing files and
    empty subdirectories that have not been modified within the
    max_age_hours window.

    Args:
        max_age_hours: Maximum age in hours before a temp file is
            removed. Defaults to 24 hours.

    Returns:
        A dictionary with cleanup statistics.
    """
    logger.info(
        "task_cleanup_temp_start",
        extra={
            "temp_dir": TEMP_DIR,
            "max_age_hours": max_age_hours,
            "task_id": self.request.id,
        },
    )

    stats: Dict[str, Any] = {
        "files_removed": 0,
        "dirs_removed": 0,
        "bytes_freed": 0,
        "errors": 0,
    }

    if not os.path.isdir(TEMP_DIR):
        logger.info(
            "task_cleanup_temp_skip",
            extra={"reason": "temp_dir_not_found", "temp_dir": TEMP_DIR},
        )
        return stats

    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    cutoff_timestamp = cutoff.timestamp()

    try:
        for root, dirs, files in os.walk(TEMP_DIR, topdown=False):
            # Remove old files
            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime < cutoff_timestamp:
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        stats["files_removed"] += 1
                        stats["bytes_freed"] += file_size
                except OSError:
                    logger.warning(
                        "cleanup_file_failed",
                        extra={"filepath": filepath},
                    )
                    stats["errors"] += 1

            # Remove empty directories (but not the temp root)
            for dirname in dirs:
                dirpath = os.path.join(root, dirname)
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                        stats["dirs_removed"] += 1
                except OSError:
                    logger.warning(
                        "cleanup_dir_failed",
                        extra={"dirpath": dirpath},
                    )
                    stats["errors"] += 1

    except Exception as exc:
        logger.exception(
            "task_cleanup_temp_failed",
            extra={"task_id": self.request.id},
        )
        raise self.retry(exc=exc)

    logger.info(
        "task_cleanup_temp_complete",
        extra={
            "stats": stats,
            "task_id": self.request.id,
        },
    )
    return stats
