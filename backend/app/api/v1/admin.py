"""Admin API endpoints for user management, device agents, and system alerts.

Provides CRUD operations for users (admin only), device/agent listing,
and alert retrieval/update (admin/manager).
"""

import math
import uuid
from datetime import date, datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_admin, get_current_manager_or_admin
from app.models.device import Device
from app.models.user import User
from app.schemas.admin import (
    AgentStatusResponse,
    AlertResponse,
    AlertUpdateRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.schemas.common import PaginatedResponse
from app.storage.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

logger = structlog.stdlib.get_logger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.get(
    "/users",
    response_model=PaginatedResponse[UserResponse],
    summary="List all users (admin only)",
)
async def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("created_at", description="Sort column"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all users in the admin's organization with pagination.

    Args:
        page: Page number (1-indexed).
        per_page: Results per page.
        sort: Column to sort by.
        order: Sort direction.
        current_user: Authenticated admin user.
        db: Async database session.

    Returns:
        Paginated list of users.
    """
    base_stmt = select(User).where(User.org_id == current_user.org_id)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    sort_col = getattr(User, sort, User.created_at)
    if order == "asc":
        base_stmt = base_stmt.order_by(sort_col.asc())
    else:
        base_stmt = base_stmt.order_by(sort_col.desc())

    offset = (page - 1) * per_page
    base_stmt = base_stmt.offset(offset).limit(per_page)

    result = await db.execute(base_stmt)
    users = list(result.scalars().all())

    pages = math.ceil(total / per_page) if total > 0 else 0

    return {
        "items": [UserResponse.model_validate(u) for u in users],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (admin only)",
)
async def create_user(
    body: UserCreateRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create a new user in the specified organization.

    The password is hashed with bcrypt before storage.

    Args:
        body: User creation request with email, password, name, role, org_id.
        current_user: Authenticated admin user.
        db: Async database session.

    Returns:
        The newly created User object.

    Raises:
        HTTPException: 409 if the email already exists.
    """
    # Check for email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=body.email,
        password_hash=pwd_context.hash(body.password),
        full_name=body.full_name,
        role=body.role,
        org_id=current_user.org_id,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    logger.info(
        "user_created",
        user_id=str(user.id),
        email=user.email,
        role=user.role,
        created_by=str(current_user.id),
    )

    return user


@router.put(
    "/users/{target_user_id}",
    response_model=UserResponse,
    summary="Update a user (admin only)",
)
async def update_user(
    target_user_id: uuid.UUID,
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Update an existing user's profile or status.

    Only provided (non-None) fields are updated.

    Args:
        target_user_id: UUID of the user to update.
        body: Update request with optional full_name, role, is_active.
        current_user: Authenticated admin user.
        db: Async database session.

    Returns:
        The updated User object.

    Raises:
        HTTPException: 404 if the user is not found.
    """
    result = await db.execute(select(User).where(User.id == target_user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.flush()
    await db.refresh(user)

    logger.info(
        "user_updated",
        user_id=str(target_user_id),
        updated_by=str(current_user.id),
        changes=body.model_dump(exclude_none=True),
    )

    return user


@router.get(
    "/alerts",
    response_model=list[AlertResponse],
    summary="Get system alerts (admin/manager)",
)
async def get_alerts(
    start_date: date | None = Query(None, description="Start date filter"),
    end_date: date | None = Query(None, description="End date filter"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("created_at", description="Sort column"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction"),
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Retrieve system alerts for the organization.

    Currently returns an empty list as the alerting system
    is a future enhancement. The endpoint is wired up and ready
    for integration with the alert engine.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        page: Page number.
        per_page: Results per page.
        sort: Sort column.
        order: Sort direction.
        current_user: Authenticated admin/manager.
        db: Async database session.

    Returns:
        List of system alerts (currently empty placeholder).
    """
    logger.info(
        "alerts_queried",
        user_id=str(current_user.id),
        role=current_user.role,
    )
    # Placeholder: alert table/engine to be implemented in a future phase.
    return []


# ── Agent / Device endpoints ────────────────────────────────────────

# Device status thresholds (seconds since last heartbeat)
_THRESHOLD_ONLINE = 120    # < 2 min  → online (actively tracking)
_THRESHOLD_IDLE   = 300    # 2-5 min  → idle (user inactive, machine on)
_THRESHOLD_SLEEP  = 1800   # 5-30 min → sleep (machine likely sleeping)
# > 30 min → offline (shutdown or disconnected)


@router.get(
    "/agents",
    response_model=list[AgentStatusResponse],
    summary="List all devices/agents with status (admin/manager)",
)
async def list_agents(
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all enrolled devices (agents) with their online/offline status.

    A device is considered *online* when its last heartbeat was received
    within the last 5 minutes, *offline* otherwise.

    Args:
        current_user: Authenticated admin or manager user.
        db: Async database session.

    Returns:
        List of agent status objects.
    """
    try:
        stmt = (
            select(Device, User.full_name)
            .join(User, Device.user_id == User.id)
            .where(Device.org_id == current_user.org_id)
            .order_by(Device.created_at.desc())
        )
        result = await db.execute(stmt)
        rows = result.all()

        now = datetime.utcnow()
        agents: list[dict] = []
        for device, user_name in rows:
            if device.last_heartbeat is not None:
                hb = device.last_heartbeat.replace(tzinfo=None) if device.last_heartbeat.tzinfo else device.last_heartbeat
                diff = (now - hb).total_seconds()
                if diff < _THRESHOLD_ONLINE:
                    status_label = "online"
                elif diff < _THRESHOLD_IDLE:
                    status_label = "idle"
                elif diff < _THRESHOLD_SLEEP:
                    status_label = "sleep"
                else:
                    status_label = "offline"
            else:
                status_label = "offline"

            agents.append(
                {
                    "id": str(device.id),
                    "user_id": str(device.user_id),
                    "hostname": device.hostname,
                    "user_name": user_name,
                    "status": status_label,
                    "cpu_usage": None,
                    "memory_usage": None,
                    "last_seen": (lambda dt: dt.isoformat() + ("Z" if "+" not in dt.isoformat() and dt.isoformat()[-1] != "Z" else ""))(device.last_heartbeat) if device.last_heartbeat else None,
                    "agent_version": device.agent_version,
                    "os_version": device.os_version,
                }
            )

        logger.info(
            "agents_listed",
            user_id=str(current_user.id),
            count=len(agents),
        )
        return agents
    except Exception as exc:
        logger.error("list_agents_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Alert update endpoint (stub) ────────────────────────────────────


@router.patch(
    "/alerts/{alert_id}",
    response_model=AlertResponse,
    summary="Update an alert status (admin/manager)",
)
async def update_alert(
    alert_id: uuid.UUID,
    body: AlertUpdateRequest,
    current_user: User = Depends(get_current_manager_or_admin),
) -> dict:
    """Update the status of a system alert.

    This is an in-memory stub. When the alerts table is implemented,
    this will persist the update to the database.

    Args:
        alert_id: UUID of the alert to update.
        body: Request body with the new status.
        current_user: Authenticated admin or manager user.

    Returns:
        The updated alert object.
    """
    logger.info(
        "alert_updated",
        alert_id=str(alert_id),
        new_status=body.status,
        updated_by=str(current_user.id),
    )
    # Stub response — return the alert as if it was updated
    return {
        "id": alert_id,
        "type": "system",
        "message": f"Alert {alert_id} status updated to {body.status}",
        "severity": "info",
        "created_at": datetime.now(timezone.utc),
    }
