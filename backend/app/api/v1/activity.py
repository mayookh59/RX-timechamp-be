"""Activity session API endpoints.

Provides endpoints for desktop agent session ingestion,
paginated session retrieval, and activity summary aggregation.
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
from app.schemas.activity import (
    ActivityIngestRequest,
    ActivitySessionResponse,
    ActivitySummaryResponse,
)
from app.schemas.common import PaginatedResponse
from app.services import activity_service
from app.storage.database import get_db

router = APIRouter(prefix="/activity", tags=["activity"])

logger = structlog.stdlib.get_logger(__name__)


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest activity sessions from a desktop agent",
)
async def ingest_sessions(
    body: ActivityIngestRequest,
    device: Device = Depends(get_device_from_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Batch ingest activity sessions from a desktop agent.

    Uses the device API key for authentication. Duplicate sessions
    (by client_id) are silently ignored via ON CONFLICT DO NOTHING.

    Args:
        body: Ingestion request with device_id and sessions list.
        device: Authenticated device from API key.
        db: Async database session.

    Returns:
        Dict with count of accepted (newly inserted) sessions.
    """
    sessions_data = [s.model_dump() for s in body.sessions]
    inserted = await activity_service.ingest_sessions(
        device_id=device.id,
        user_id=device.user_id,
        sessions=sessions_data,
        db=db,
    )
    logger.info(
        "activity_ingest_complete",
        device_id=str(device.id),
        submitted=len(body.sessions),
        accepted=inserted,
    )
    return {"accepted": inserted}


@router.get(
    "/sessions",
    response_model=PaginatedResponse[ActivitySessionResponse],
    summary="List activity sessions for a user",
)
async def list_sessions(
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
    """Retrieve paginated activity sessions.

    Regular users see only their own sessions. Admins and managers
    can filter by user_id to view other users' sessions.

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
        Paginated response with activity sessions.
    """
    # Admin/manager: if no user_id filter, show ALL users' data
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users

    sessions, total = await activity_service.get_sessions(
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
        "items": [ActivitySessionResponse.model_validate(s) for s in sessions],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get(
    "/summary",
    response_model=ActivitySummaryResponse,
    summary="Get activity summary for a user",
)
async def get_summary(
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    user_id: uuid.UUID | None = Query(None, description="User ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Device ID filter"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get aggregated activity summary for a user and date range.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        user_id: Optional user filter (admin/manager only).
        device_id: Optional device filter.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Activity summary with hours, ratios, and session counts.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    return await activity_service.get_summary(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        db=db,
        device_id=device_id,
    )
