"""Activity session service for ingestion and retrieval.

Handles batch ingestion with idempotency, paginated session queries,
and aggregated activity summaries.
"""

import math
import uuid
from datetime import date, datetime, time, timezone

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivitySession
from app.models.device import Device

logger = structlog.stdlib.get_logger(__name__)


async def ingest_sessions(
    device_id: uuid.UUID,
    user_id: uuid.UUID,
    sessions: list[dict],
    db: AsyncSession,
) -> int:
    """Batch insert activity sessions with idempotency.

    Uses PostgreSQL ON CONFLICT DO NOTHING on the client_id unique
    constraint to safely handle duplicate submissions.

    Args:
        device_id: UUID of the reporting device.
        user_id: UUID of the device owner.
        sessions: List of session dicts with client_id, session_type,
            start_time, and end_time.
        db: Async database session.

    Returns:
        Number of new rows inserted (excludes duplicates).
    """
    if not sessions:
        return 0

    values = [
        {
            "client_id": s["client_id"],
            "device_id": device_id,
            "user_id": user_id,
            "session_type": s["session_type"],
            "start_time": s["start_time"],
            "end_time": s.get("end_time"),
        }
        for s in sessions
    ]

    stmt = pg_insert(ActivitySession).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
    result = await db.execute(stmt)
    await db.flush()

    inserted = result.rowcount  # type: ignore[union-attr]
    logger.info(
        "activity_sessions_ingested",
        device_id=str(device_id),
        submitted=len(sessions),
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
    """Apply common filters to an activity session query.

    Args:
        stmt: The base SELECT statement.
        user_id: Filter by user UUID. None means all users (admin).
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        device_id: Optional device UUID filter.

    Returns:
        The filtered SELECT statement.
    """
    if user_id is not None:
        stmt = stmt.where(ActivitySession.user_id == user_id)

    # Use func.date() for portability across SQLite and PostgreSQL.
    # SQLite stores timestamps as ISO strings with "T" separator while
    # SQLAlchemy formats datetime parameters with space separator, breaking
    # naive >=/<= string comparisons. DATE() strips time/tz and works on both.
    if start_date is not None:
        stmt = stmt.where(func.date(ActivitySession.start_time) >= start_date)

    if end_date is not None:
        stmt = stmt.where(func.date(ActivitySession.start_time) <= end_date)

    if device_id is not None:
        stmt = stmt.where(ActivitySession.device_id == device_id)

    return stmt


async def get_sessions(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    page: int,
    per_page: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
    sort: str = "start_time",
    order: str = "desc",
) -> tuple[list[ActivitySession], int]:
    """Retrieve paginated activity sessions for a user.

    Args:
        user_id: User UUID to query sessions for.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        page: Page number (1-indexed).
        per_page: Number of results per page.
        db: Async database session.
        device_id: Optional device filter.
        sort: Column name to sort by.
        order: Sort direction (asc or desc).

    Returns:
        Tuple of (list of ActivitySession objects, total count).
    """
    base_stmt = select(ActivitySession)
    base_stmt = _apply_filters(base_stmt, user_id, start_date, end_date, device_id)

    # Count query
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Sort
    sort_col = getattr(ActivitySession, sort, ActivitySession.start_time)
    if order == "asc":
        base_stmt = base_stmt.order_by(sort_col.asc())
    else:
        base_stmt = base_stmt.order_by(sort_col.desc())

    # Paginate
    offset = (page - 1) * per_page
    base_stmt = base_stmt.offset(offset).limit(per_page)

    result = await db.execute(base_stmt)
    sessions = list(result.scalars().all())

    logger.debug(
        "activity_sessions_queried",
        user_id=str(user_id),
        total=total,
        page=page,
    )
    return sessions, total


async def get_summary(
    user_id: uuid.UUID,
    start_date: date | None,
    end_date: date | None,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
) -> dict:
    """Compute aggregated activity summary for a user and date range.

    Calculates total active hours, idle hours, active ratio,
    and session count.

    Args:
        user_id: User UUID to compute summary for.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        db: Async database session.
        device_id: Optional device filter.

    Returns:
        Dict with period, total_active_hours, total_idle_hours,
        active_ratio, and sessions_count.
    """
    # Active hours
    active_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(ActivitySession.session_type == "active")
    active_stmt = _apply_filters(active_stmt, user_id, start_date, end_date, device_id)
    active_secs = (await db.execute(active_stmt)).scalar() or 0

    # Idle hours (idle + away + locked)
    idle_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(ActivitySession.session_type.in_(["idle", "away", "locked"]))
    idle_stmt = _apply_filters(idle_stmt, user_id, start_date, end_date, device_id)
    idle_secs = (await db.execute(idle_stmt)).scalar() or 0

    # Session count
    count_stmt = select(func.count(ActivitySession.id))
    count_stmt = _apply_filters(count_stmt, user_id, start_date, end_date, device_id)
    sessions_count = (await db.execute(count_stmt)).scalar() or 0

    total_secs = active_secs + idle_secs
    active_ratio = (active_secs / total_secs) if total_secs > 0 else 0.0

    period_start = start_date.isoformat() if start_date else "all"
    period_end = end_date.isoformat() if end_date else "now"
    period = f"{period_start} to {period_end}"

    return {
        "period": period,
        "total_active_hours": round(active_secs / 3600, 2),
        "total_idle_hours": round(idle_secs / 3600, 2),
        "active_ratio": round(active_ratio, 4),
        "sessions_count": sessions_count,
    }
