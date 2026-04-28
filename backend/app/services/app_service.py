"""App usage service for ingestion and analytics.

Handles batch app usage ingestion with idempotency,
paginated retrieval, and top-apps aggregation.
"""

import uuid
from datetime import date, datetime, time, timezone

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import AppUsage

logger = structlog.stdlib.get_logger(__name__)


async def ingest_app_usage(
    device_id: uuid.UUID,
    user_id: uuid.UUID | None,
    records: list[dict],
    db: AsyncSession,
) -> int:
    """Batch insert app usage records with idempotency.

    Uses PostgreSQL ON CONFLICT DO NOTHING on the client_id unique
    constraint to safely handle duplicate submissions.

    Args:
        device_id: UUID of the reporting device.
        user_id: UUID of the device owner.
        records: List of app usage dicts.
        db: Async database session.

    Returns:
        Number of new rows inserted.
    """
    if not records:
        return 0

    values = [
        {
            "client_id": r["client_id"],
            "device_id": device_id,
            "user_id": user_id,
            "process_name": r["process_name"],
            "window_title": r["window_title"],
            "start_time": r["start_time"],
            "end_time": r.get("end_time"),
            "duration_sec": r.get("duration_seconds"),
        }
        for r in records
    ]

    stmt = pg_insert(AppUsage).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
    result = await db.execute(stmt)
    await db.flush()

    inserted = result.rowcount  # type: ignore[union-attr]
    logger.info(
        "app_usage_ingested",
        device_id=str(device_id),
        submitted=len(records),
        inserted=inserted,
    )
    return inserted


def _apply_filters(
    stmt: Select,
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    device_id: uuid.UUID | None = None,
) -> Select:
    """Apply common filters to an app usage query.

    Args:
        stmt: The base SELECT statement.
        user_id: Filter by user UUID.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        device_id: Optional device UUID filter.

    Returns:
        The filtered SELECT statement.
    """
    if user_id is not None:
        stmt = stmt.where(AppUsage.user_id == user_id)

    if start_date is not None:
        stmt = stmt.where(func.date(AppUsage.start_time) >= start_date.isoformat())

    if end_date is not None:
        stmt = stmt.where(func.date(AppUsage.start_time) <= end_date.isoformat())

    if device_id is not None:
        stmt = stmt.where(AppUsage.device_id == device_id)

    return stmt


async def get_app_usage(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    page: int,
    per_page: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
    sort: str = "start_time",
    order: str = "desc",
) -> tuple[list[AppUsage], int]:
    """Retrieve paginated app usage records for a user.

    Args:
        user_id: User UUID to query records for.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        db: Async database session.
        device_id: Optional device filter.
        sort: Column name to sort by.
        order: Sort direction (asc or desc).

    Returns:
        Tuple of (list of AppUsage objects, total count).
    """
    base_stmt = select(AppUsage)
    base_stmt = _apply_filters(base_stmt, user_id, start_date, end_date, device_id)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    sort_col = getattr(AppUsage, sort, AppUsage.start_time)
    if order == "asc":
        base_stmt = base_stmt.order_by(sort_col.asc())
    else:
        base_stmt = base_stmt.order_by(sort_col.desc())

    offset = (page - 1) * per_page
    base_stmt = base_stmt.offset(offset).limit(per_page)

    result = await db.execute(base_stmt)
    records = list(result.scalars().all())

    return records, total


async def get_top_apps(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
) -> list[dict]:
    """Get top applications by total usage time.

    Args:
        user_id: User UUID.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        limit: Maximum number of apps to return.
        db: Async database session.
        device_id: Optional device filter.

    Returns:
        List of dicts with name, total_hours, and session_count.
    """
    stmt = (
        select(
            AppUsage.process_name,
            func.coalesce(func.sum(AppUsage.duration_sec), 0).label("total_secs"),
            func.count(AppUsage.id).label("session_count"),
        )
        .group_by(AppUsage.process_name)
        .order_by(func.sum(AppUsage.duration_sec).desc().nulls_last())
        .limit(limit)
    )
    stmt = _apply_filters(stmt, user_id, start_date, end_date, device_id)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "name": row.process_name,
            "total_hours": round((row.total_secs or 0) / 3600, 2),
            "session_count": row.session_count,
        }
        for row in rows
    ]
