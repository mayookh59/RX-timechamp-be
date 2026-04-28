"""Dead Letter Queue service for failed record processing.

Provides functions to enqueue failed records and periodically retry
them with exponential backoff. Records exceeding the maximum retry
count remain in the DLQ for manual inspection.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dead_letter import DeadLetterQueue
from app.storage.database import async_session_factory

logger = structlog.stdlib.get_logger(__name__)

# Maximum number of automatic retry attempts before giving up
MAX_RETRIES = 3

# Batch size for retry processing
RETRY_BATCH_SIZE = 50


async def enqueue_failed_record(
    endpoint: str,
    payload: dict,
    error: str,
    db: AsyncSession,
) -> DeadLetterQueue:
    """Store a failed record in the dead letter queue.

    Creates a new DLQ entry with the original payload and error details
    for later retry or manual inspection.

    Args:
        endpoint: The API endpoint or event type that failed processing.
        payload: The original request payload that could not be processed.
        error: Human-readable description of the failure.
        db: Async database session.

    Returns:
        The created DeadLetterQueue entry.
    """
    entry = DeadLetterQueue(
        id=uuid.uuid4(),
        event_type=endpoint,
        payload=payload,
        error_message=error,
        retry_count=0,
    )
    db.add(entry)
    await db.flush()

    logger.info(
        "dlq_record_enqueued",
        dlq_id=str(entry.id),
        event_type=endpoint,
        error=error,
    )

    return entry


async def retry_dlq_entries() -> dict[str, int]:
    """Periodically retry failed DLQ entries up to the maximum retry count.

    Fetches a batch of retryable entries (retry_count < MAX_RETRIES),
    attempts to reprocess each one, and updates the retry metadata.
    Entries that still fail have their retry count incremented.
    Entries that succeed are deleted from the DLQ.

    Returns:
        A dict with counts of retried, succeeded, and permanently_failed entries.
    """
    stats: dict[str, int] = {
        "retried": 0,
        "succeeded": 0,
        "permanently_failed": 0,
    }

    async with async_session_factory() as db:
        try:
            stmt = (
                select(DeadLetterQueue)
                .where(DeadLetterQueue.retry_count < MAX_RETRIES)
                .order_by(DeadLetterQueue.created_at.asc())
                .limit(RETRY_BATCH_SIZE)
            )
            result = await db.execute(stmt)
            entries = list(result.scalars().all())

            if not entries:
                logger.debug("dlq_retry_no_entries")
                return stats

            logger.info("dlq_retry_started", entry_count=len(entries))

            for entry in entries:
                stats["retried"] += 1
                try:
                    success = await _attempt_reprocess(entry, db)

                    if success:
                        await db.delete(entry)
                        stats["succeeded"] += 1
                        logger.info(
                            "dlq_retry_succeeded",
                            dlq_id=str(entry.id),
                            event_type=entry.event_type,
                        )
                    else:
                        entry.retry_count += 1
                        entry.last_retried_at = datetime.now(timezone.utc)

                        if entry.retry_count >= MAX_RETRIES:
                            stats["permanently_failed"] += 1
                            logger.warning(
                                "dlq_entry_permanently_failed",
                                dlq_id=str(entry.id),
                                event_type=entry.event_type,
                                retry_count=entry.retry_count,
                            )
                        else:
                            logger.info(
                                "dlq_retry_failed_will_retry",
                                dlq_id=str(entry.id),
                                event_type=entry.event_type,
                                retry_count=entry.retry_count,
                            )

                except Exception as exc:
                    entry.retry_count += 1
                    entry.last_retried_at = datetime.now(timezone.utc)
                    entry.error_message = str(exc)

                    logger.error(
                        "dlq_retry_exception",
                        dlq_id=str(entry.id),
                        event_type=entry.event_type,
                        error=str(exc),
                        retry_count=entry.retry_count,
                    )

            await db.commit()

        except Exception as exc:
            await db.rollback()
            logger.error("dlq_retry_batch_failed", error=str(exc))
            raise

    logger.info(
        "dlq_retry_completed",
        retried=stats["retried"],
        succeeded=stats["succeeded"],
        permanently_failed=stats["permanently_failed"],
    )

    return stats


async def _attempt_reprocess(
    entry: DeadLetterQueue,
    db: AsyncSession,
) -> bool:
    """Attempt to reprocess a single DLQ entry.

    Dispatches the entry payload to the appropriate handler based on
    the event_type. Returns True if reprocessing succeeded.

    Args:
        entry: The DLQ entry to reprocess.
        db: Async database session.

    Returns:
        True if the entry was successfully reprocessed; False otherwise.
    """
    # Import handlers lazily to avoid circular imports
    from app.services import activity_service, app_service, url_service

    handler_map: dict = {
        "activity_sessions": activity_service,
        "app_usage": app_service,
        "url_visits": url_service,
    }

    handler = handler_map.get(entry.event_type)
    if handler is None:
        logger.warning(
            "dlq_unknown_event_type",
            event_type=entry.event_type,
            dlq_id=str(entry.id),
        )
        return False

    try:
        # Re-attempt ingestion using the stored payload
        await handler.ingest(entry.payload, db)
        return True
    except Exception as exc:
        logger.warning(
            "dlq_reprocess_failed",
            dlq_id=str(entry.id),
            event_type=entry.event_type,
            error=str(exc),
        )
        return False


async def get_dlq_stats(db: AsyncSession) -> dict:
    """Get summary statistics for the dead letter queue.

    Args:
        db: Async database session.

    Returns:
        Dict with total, retryable, and permanently_failed counts.
    """
    from sqlalchemy import func

    total_stmt = select(func.count()).select_from(DeadLetterQueue)
    total = (await db.execute(total_stmt)).scalar() or 0

    retryable_stmt = (
        select(func.count())
        .select_from(DeadLetterQueue)
        .where(DeadLetterQueue.retry_count < MAX_RETRIES)
    )
    retryable = (await db.execute(retryable_stmt)).scalar() or 0

    return {
        "total": total,
        "retryable": retryable,
        "permanently_failed": total - retryable,
    }
