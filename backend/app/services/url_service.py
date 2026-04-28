"""URL visit service for ingestion and analytics.

Handles batch URL visit ingestion with idempotency,
paginated retrieval, and top-domains aggregation.
"""

import uuid
from datetime import date, datetime, time, timezone

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import UrlVisit

logger = structlog.stdlib.get_logger(__name__)


async def ingest_url_visits(
    device_id: uuid.UUID,
    user_id: uuid.UUID | None,
    records: list[dict],
    db: AsyncSession,
) -> int:
    """Batch insert URL visit records with idempotency.

    Uses PostgreSQL ON CONFLICT DO NOTHING on the client_id unique
    constraint to safely handle duplicate submissions.

    Args:
        device_id: UUID of the reporting device.
        user_id: UUID of the device owner.
        records: List of URL visit dicts.
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
            "browser": r["browser"],
            "url": r["url"],
            "domain": r["domain"],
            "page_title": r["page_title"],
            "visit_time": r["visit_time"],
            "duration_sec": r.get("duration_sec"),
        }
        for r in records
    ]

    stmt = pg_insert(UrlVisit).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["client_id"])
    result = await db.execute(stmt)
    await db.flush()

    inserted = result.rowcount  # type: ignore[union-attr]
    logger.info(
        "url_visits_ingested",
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
    """Apply common filters to a URL visit query.

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
        stmt = stmt.where(UrlVisit.user_id == user_id)

    if start_date is not None:
        stmt = stmt.where(func.date(UrlVisit.visit_time) >= start_date.isoformat())

    if end_date is not None:
        stmt = stmt.where(func.date(UrlVisit.visit_time) <= end_date.isoformat())

    if device_id is not None:
        stmt = stmt.where(UrlVisit.device_id == device_id)

    return stmt


async def get_url_visits(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    page: int,
    per_page: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
    sort: str = "visit_time",
    order: str = "desc",
) -> tuple[list[UrlVisit], int]:
    """Retrieve paginated URL visit records for a user.

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
        Tuple of (list of UrlVisit objects, total count).
    """
    base_stmt = select(UrlVisit)
    base_stmt = _apply_filters(base_stmt, user_id, start_date, end_date, device_id)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    sort_col = getattr(UrlVisit, sort, UrlVisit.visit_time)
    if order == "asc":
        base_stmt = base_stmt.order_by(sort_col.asc())
    else:
        base_stmt = base_stmt.order_by(sort_col.desc())

    offset = (page - 1) * per_page
    base_stmt = base_stmt.offset(offset).limit(per_page)

    result = await db.execute(base_stmt)
    records = list(result.scalars().all())

    return records, total


async def get_top_domains(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
) -> list[dict]:
    """Get top domains by total visit time.

    Args:
        user_id: User UUID.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        limit: Maximum number of domains to return.
        db: Async database session.
        device_id: Optional device filter.

    Returns:
        List of dicts with domain, total_hours, and visit_count.
    """
    stmt = (
        select(
            UrlVisit.domain,
            func.coalesce(func.sum(UrlVisit.duration_sec), 0).label("total_secs"),
            func.count(UrlVisit.id).label("visit_count"),
        )
        .group_by(UrlVisit.domain)
        .order_by(func.sum(UrlVisit.duration_sec).desc().nulls_last())
        .limit(limit)
    )
    stmt = _apply_filters(stmt, user_id, start_date, end_date, device_id)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "domain": row.domain,
            "total_hours": round((row.total_secs or 0) / 3600, 2),
            "visit_count": row.visit_count,
        }
        for row in rows
    ]
