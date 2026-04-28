"""Dashboard API endpoints.

Provides organization overview, individual user dashboard,
and time-series trend data for analytics charts.
"""

import uuid
from datetime import date

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.models.user import User
from app.schemas.dashboard import (
    DashboardOverviewResponse,
    TrendsResponse,
    UserDashboardResponse,
)
from app.services import dashboard_service
from app.storage.database import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

logger = structlog.stdlib.get_logger(__name__)


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    summary="Get organization-wide dashboard overview",
)
async def get_overview(
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get organization-wide dashboard overview.

    Aggregates metrics across all users in the current user's
    organization including user counts, activity hours, and top
    apps/domains.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Organization-wide overview with metrics and top lists.
    """
    return await dashboard_service.get_overview(
        org_id=current_user.org_id,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )


@router.get(
    "/user/{target_user_id}",
    response_model=UserDashboardResponse,
    summary="Get individual user dashboard",
)
async def get_user_dashboard(
    target_user_id: uuid.UUID,
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get individual user dashboard data.

    Regular users can only view their own dashboard. Admins and
    managers can view any user's dashboard within their organization.

    Args:
        target_user_id: UUID of the user to view.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        User dashboard with activity hours, top apps/domains, and score.

    Raises:
        HTTPException: 403 if viewer tries to access another user's data.
    """
    if target_user_id != current_user.id and current_user.role not in {"admin", "manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to view this user's dashboard",
        )

    return await dashboard_service.get_user_dashboard(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )


@router.get(
    "/trends",
    response_model=TrendsResponse,
    summary="Get time-series trend data",
)
async def get_trends(
    start_date: date | None = Query(None, description="Start date (defaults to 30 days ago)"),
    end_date: date | None = Query(None, description="End date (defaults to today)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get time-series trend data for dashboard charts.

    Produces daily active hours, idle hours, and productivity scores
    for the requested date range across the user's organization.

    Args:
        start_date: Start date (defaults to 30 days ago).
        end_date: End date (defaults to today).
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Trend data with dates, active_hours, idle_hours, and scores.
    """
    return await dashboard_service.get_trends(
        org_id=current_user.org_id,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )
