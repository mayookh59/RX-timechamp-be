"""URL visit API endpoints.

Provides endpoints for desktop agent URL visit ingestion,
paginated retrieval, and top-domains analytics.
"""

import math
import uuid
from datetime import date

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_device_from_api_key
from app.models.device import Device
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.urls import TopDomainsResponse, UrlVisitIngestRequest, UrlVisitResponse
from app.services import url_service
from app.storage.database import get_db

router = APIRouter(prefix="/urls", tags=["urls"])

logger = structlog.stdlib.get_logger(__name__)


@router.post(
    "/visits",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest URL visit records from a desktop agent",
)
async def ingest_url_visits(
    body: UrlVisitIngestRequest,
    device: Device = Depends(get_device_from_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Batch ingest URL visit records from a desktop agent.

    Uses the device API key for authentication. Duplicates by client_id
    are silently ignored.

    Args:
        body: Ingestion request with device_id and records list.
        device: Authenticated device from API key.
        db: Async database session.

    Returns:
        Dict with count of accepted records.
    """
    records_data = [r.model_dump() for r in body.records]
    inserted = await url_service.ingest_url_visits(
        device_id=device.id,
        user_id=device.user_id,
        records=records_data,
        db=db,
    )
    logger.info(
        "url_visits_ingest_complete",
        device_id=str(device.id),
        submitted=len(body.records),
        accepted=inserted,
    )
    return {"accepted": inserted}


@router.get(
    "/visits",
    response_model=PaginatedResponse[UrlVisitResponse],
    summary="List URL visit records for a user",
)
async def list_url_visits(
    start_date: date | None = Query(None, description="Start date filter (inclusive)"),
    end_date: date | None = Query(None, description="End date filter (inclusive)"),
    user_id: uuid.UUID | None = Query(None, description="Filter by user ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Filter by device ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("visit_time", description="Sort column"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve paginated URL visit records.

    Regular users see only their own records. Admins and managers
    can filter by user_id.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        user_id: Optional user filter (admin/manager only).
        device_id: Optional device filter.
        page: Page number (1-indexed).
        per_page: Results per page.
        sort: Column to sort by.
        order: Sort direction.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Paginated response with URL visit records.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    records, total = await url_service.get_url_visits(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        per_page=per_page,
        db=db,
        device_id=device_id,
        sort=sort,
        order=order,
    )

    pages = math.ceil(total / per_page) if total > 0 else 0

    return {
        "items": [UrlVisitResponse.model_validate(r) for r in records],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get(
    "/top-domains",
    response_model=list[TopDomainsResponse],
    summary="Get top domains by visit time",
)
async def get_top_domains(
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    user_id: uuid.UUID | None = Query(None, description="User ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Device ID filter"),
    limit: int = Query(10, ge=1, le=50, description="Max domains to return"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get top domains ranked by total visit time.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        user_id: Optional user filter (admin/manager only).
        device_id: Optional device filter.
        limit: Maximum number of domains to return.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        List of top domains with total hours and visit counts.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    return await url_service.get_top_domains(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        db=db,
        device_id=device_id,
    )
