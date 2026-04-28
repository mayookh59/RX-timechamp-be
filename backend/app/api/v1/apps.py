"""App usage API endpoints.

Provides endpoints for desktop agent app usage ingestion,
paginated retrieval, and top-apps analytics.
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
from app.schemas.apps import AppUsageIngestRequest, AppUsageResponse, TopAppsResponse
from app.schemas.common import PaginatedResponse
from app.services import app_service
from app.storage.database import get_db

router = APIRouter(prefix="/apps", tags=["apps"])

logger = structlog.stdlib.get_logger(__name__)


@router.post(
    "/usage",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest app usage records from a desktop agent",
)
async def ingest_app_usage(
    body: AppUsageIngestRequest,
    device: Device = Depends(get_device_from_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Batch ingest app usage records from a desktop agent.

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
    inserted = await app_service.ingest_app_usage(
        device_id=device.id,
        user_id=device.user_id,
        records=records_data,
        db=db,
    )
    logger.info(
        "app_usage_ingest_complete",
        device_id=str(device.id),
        submitted=len(body.records),
        accepted=inserted,
    )
    return {"accepted": inserted}


@router.get(
    "/usage",
    response_model=PaginatedResponse[AppUsageResponse],
    summary="List app usage records for a user",
)
async def list_app_usage(
    start_date: date | None = Query(None, description="Start date filter (inclusive)"),
    end_date: date | None = Query(None, description="End date filter (inclusive)"),
    user_id: uuid.UUID | None = Query(None, description="Filter by user ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Filter by device ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("start_time", description="Sort column"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve paginated app usage records.

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
        Paginated response with app usage records.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    records, total = await app_service.get_app_usage(
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
        "items": [AppUsageResponse.model_validate(r) for r in records],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get(
    "/top",
    response_model=list[TopAppsResponse],
    summary="Get top applications by usage time",
)
async def get_top_apps(
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    user_id: uuid.UUID | None = Query(None, description="User ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Device ID filter"),
    limit: int = Query(10, ge=1, le=50, description="Max apps to return"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get top applications ranked by total usage time.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        user_id: Optional user filter (admin/manager only).
        device_id: Optional device filter.
        limit: Maximum number of apps to return.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        List of top applications with total hours and session counts.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    return await app_service.get_top_apps(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        db=db,
        device_id=device_id,
    )
