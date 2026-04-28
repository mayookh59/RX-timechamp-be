"""Audit logging service for security and compliance tracking.

Records security-relevant actions with full context including the
acting user, affected resource, before/after values, and client
information for forensic analysis and compliance reporting.
"""

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = structlog.stdlib.get_logger(__name__)

# Standard audit action constants
LOGIN = "LOGIN"
LOGOUT = "LOGOUT"
LOGIN_FAILED = "LOGIN_FAILED"
VIEW_SCREENSHOT = "VIEW_SCREENSHOT"
DELETE_SCREENSHOT = "DELETE_SCREENSHOT"
UPDATE_USER = "UPDATE_USER"
CREATE_USER = "CREATE_USER"
DELETE_USER = "DELETE_USER"
CREATE_DEVICE = "CREATE_DEVICE"
DEACTIVATE_DEVICE = "DEACTIVATE_DEVICE"
ROTATE_API_KEY = "ROTATE_API_KEY"
REVOKE_API_KEY = "REVOKE_API_KEY"
EXPORT_DATA = "EXPORT_DATA"
DELETE_DATA = "DELETE_DATA"
UPDATE_CONSENT = "UPDATE_CONSENT"
ADMIN_ACTION = "ADMIN_ACTION"


async def log_action(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Record an auditable action in the audit log.

    Captures the full context of a security-relevant action including
    who performed it, what was affected, and the client origin.

    Args:
        db: Async database session.
        user_id: UUID of the user performing the action.
        action: Action identifier (e.g., LOGIN, DELETE_SCREENSHOT).
        resource_type: Type of the affected resource (e.g., "screenshot", "user").
        resource_id: Identifier of the specific resource affected.
        old_value: JSON-serializable dict of the resource state before the action.
        new_value: JSON-serializable dict of the resource state after the action.
        request: FastAPI Request object for extracting IP and User-Agent.

    Returns:
        The created AuditLog entry.
    """
    ip_address: str | None = None
    user_agent: str | None = None

    if request is not None:
        # Extract client IP, respecting X-Forwarded-For for proxied requests
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip_address = forwarded_for.split(",")[0].strip()
        else:
            ip_address = request.client.host if request.client else None

        user_agent = request.headers.get("User-Agent")

    entry = AuditLog(
        id=uuid.uuid4(),
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    await db.flush()

    logger.info(
        "audit_log_created",
        audit_id=str(entry.id),
        user_id=str(user_id),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
    )

    return entry


async def query_audit_trail(
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Query the audit trail with optional filters and pagination.

    Supports filtering by user, action type, resource, and date range.
    Results are ordered by timestamp descending (most recent first).

    Args:
        db: Async database session.
        user_id: Filter by the acting user's UUID.
        action: Filter by action type (e.g., "LOGIN").
        resource_type: Filter by resource type (e.g., "screenshot").
        resource_id: Filter by specific resource identifier.
        start_date: Inclusive start date for the query range.
        end_date: Inclusive end date for the query range.
        page: Page number (1-indexed).
        per_page: Number of results per page (max 100).

    Returns:
        Dict with items list, total count, page, per_page, and pages.
    """
    per_page = min(per_page, 100)

    base_stmt = select(AuditLog)

    if user_id is not None:
        base_stmt = base_stmt.where(AuditLog.user_id == user_id)

    if action is not None:
        base_stmt = base_stmt.where(AuditLog.action == action)

    if resource_type is not None:
        base_stmt = base_stmt.where(AuditLog.resource_type == resource_type)

    if resource_id is not None:
        base_stmt = base_stmt.where(AuditLog.resource_id == resource_id)

    if start_date is not None:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        base_stmt = base_stmt.where(AuditLog.timestamp >= start_dt)

    if end_date is not None:
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
        base_stmt = base_stmt.where(AuditLog.timestamp <= end_dt)

    # Count total matching entries
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch paginated results
    offset = (page - 1) * per_page
    data_stmt = (
        base_stmt
        .order_by(AuditLog.timestamp.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(data_stmt)
    entries = list(result.scalars().all())

    total_pages = (total + per_page - 1) // per_page if total > 0 else 0

    items = [
        {
            "id": str(entry.id),
            "user_id": str(entry.user_id),
            "action": entry.action,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "old_value": entry.old_value,
            "new_value": entry.new_value,
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "timestamp": entry.timestamp.isoformat(),
        }
        for entry in entries
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": total_pages,
    }
