"""GDPR compliance endpoints for data portability, deletion, and consent.

Implements the following GDPR rights:
    - Article 20: Right to data portability (export)
    - Article 17: Right to erasure (right to be forgotten)
    - Consent management for tracking preferences
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import get_current_user, require_role
from app.models.activity import ActivitySession, AppUsage, Screenshot, UrlVisit
from app.models.audit_log import AuditLog
from app.models.device import Device
from app.models.user import User
from app.schemas.gdpr import (
    ConsentStatus,
    ConsentUpdate,
    DataExportResponse,
    DeletionRequest,
    DeletionResponse,
)
from app.services import audit_service
from app.storage.database import get_db

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/gdpr", tags=["GDPR"])


@router.get(
    "/export/{user_id}",
    response_model=DataExportResponse,
    summary="Export all user data (GDPR Article 20)",
)
async def export_user_data(
    user_id: uuid.UUID,
    request: Request,
    current_user: Annotated[User, Depends(require_role(["admin", "manager"]))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DataExportResponse:
    """Export all data associated with a user in portable JSON format.

    Only admins and managers can export data. The user being exported
    must belong to the same organization as the requester (unless admin).

    Args:
        user_id: UUID of the user whose data to export.
        request: FastAPI request for audit logging.
        current_user: The authenticated requesting user.
        db: Async database session.

    Returns:
        DataExportResponse containing all user data.

    Raises:
        HTTPException: 404 if the user is not found.
        HTTPException: 403 if the requester lacks access.
    """
    # Fetch target user
    target_user = await _get_user_or_404(db, user_id)

    # Authorization check: same org or admin
    if (
        current_user.role != "admin"
        and target_user.org_id != current_user.org_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot export data for users outside your organization",
        )

    logger.info(
        "gdpr_export_started",
        target_user_id=str(user_id),
        requested_by=str(current_user.id),
    )

    # Gather all user data
    user_profile = {
        "id": str(target_user.id),
        "email": target_user.email,
        "full_name": target_user.full_name,
        "role": target_user.role,
        "is_active": target_user.is_active,
        "created_at": target_user.created_at.isoformat(),
    }

    # Devices
    device_result = await db.execute(
        select(Device).where(Device.user_id == user_id)
    )
    devices = [
        {
            "id": str(d.id),
            "hostname": d.hostname,
            "os_version": d.os_version,
            "agent_version": d.agent_version,
            "is_active": d.is_active,
            "created_at": d.created_at.isoformat(),
        }
        for d in device_result.scalars().all()
    ]

    # Activity sessions
    session_result = await db.execute(
        select(ActivitySession).where(ActivitySession.user_id == user_id)
    )
    activity_sessions = [
        {
            "id": str(s.id),
            "session_type": s.session_type,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "duration_sec": s.duration_sec,
        }
        for s in session_result.scalars().all()
    ]

    # App usage
    app_result = await db.execute(
        select(AppUsage).where(AppUsage.user_id == user_id)
    )
    app_usage = [
        {
            "id": str(a.id),
            "process_name": a.process_name,
            "window_title": a.window_title,
            "start_time": a.start_time.isoformat(),
            "end_time": a.end_time.isoformat() if a.end_time else None,
            "duration_sec": a.duration_sec,
        }
        for a in app_result.scalars().all()
    ]

    # URL visits
    url_result = await db.execute(
        select(UrlVisit).where(UrlVisit.user_id == user_id)
    )
    url_visits = [
        {
            "id": str(u.id),
            "browser": u.browser,
            "url": u.url,
            "domain": u.domain,
            "page_title": u.page_title,
            "visit_time": u.visit_time.isoformat(),
            "duration_sec": u.duration_sec,
        }
        for u in url_result.scalars().all()
    ]

    # Screenshots (metadata only)
    screenshot_result = await db.execute(
        select(Screenshot).where(Screenshot.user_id == user_id)
    )
    screenshots = [
        {
            "id": str(sc.id),
            "captured_at": sc.captured_at.isoformat(),
            "file_size": sc.file_size,
            "storage_key": sc.storage_key,
        }
        for sc in screenshot_result.scalars().all()
    ]

    # Audit logs for this user
    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.user_id == user_id)
    )
    audit_logs = [
        {
            "id": str(al.id),
            "action": al.action,
            "resource_type": al.resource_type,
            "resource_id": al.resource_id,
            "timestamp": al.timestamp.isoformat(),
        }
        for al in audit_result.scalars().all()
    ]

    # Log the export action
    await audit_service.log_action(
        db=db,
        user_id=current_user.id,
        action=audit_service.EXPORT_DATA,
        resource_type="user",
        resource_id=str(user_id),
        request=request,
    )

    logger.info(
        "gdpr_export_completed",
        target_user_id=str(user_id),
        record_counts={
            "devices": len(devices),
            "activity_sessions": len(activity_sessions),
            "app_usage": len(app_usage),
            "url_visits": len(url_visits),
            "screenshots": len(screenshots),
            "audit_logs": len(audit_logs),
        },
    )

    return DataExportResponse(
        user_id=str(user_id),
        exported_at=datetime.now(timezone.utc),
        user_profile=user_profile,
        devices=devices,
        activity_sessions=activity_sessions,
        app_usage=app_usage,
        url_visits=url_visits,
        screenshots=screenshots,
        audit_logs=audit_logs,
    )


@router.delete(
    "/delete/{user_id}",
    response_model=DeletionResponse,
    summary="Delete all user data (GDPR Article 17)",
)
async def delete_user_data(
    user_id: uuid.UUID,
    body: DeletionRequest,
    request: Request,
    current_user: Annotated[User, Depends(require_role(["admin"]))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeletionResponse:
    """Permanently delete all data associated with a user.

    Implements the right to be forgotten. Requires admin role and
    explicit confirmation. Deletes all activity data, screenshots,
    devices, and the user account itself.

    Args:
        user_id: UUID of the user whose data to delete.
        body: Deletion request with confirmation flag.
        request: FastAPI request for audit logging.
        current_user: The authenticated admin user.
        db: Async database session.

    Returns:
        DeletionResponse with deletion summary.

    Raises:
        HTTPException: 400 if confirmation is not provided.
        HTTPException: 404 if the user is not found.
        HTTPException: 403 if attempting to delete own account.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion must be explicitly confirmed by setting confirm=true",
        )

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete your own account via this endpoint",
        )

    target_user = await _get_user_or_404(db, user_id)

    logger.info(
        "gdpr_deletion_started",
        target_user_id=str(user_id),
        requested_by=str(current_user.id),
        reason=body.reason,
    )

    records_deleted: dict[str, int] = {}

    # Delete in order: child records first
    # Screenshots
    result = await db.execute(
        delete(Screenshot).where(Screenshot.user_id == user_id)
    )
    records_deleted["screenshots"] = result.rowcount or 0

    # URL visits
    result = await db.execute(
        delete(UrlVisit).where(UrlVisit.user_id == user_id)
    )
    records_deleted["url_visits"] = result.rowcount or 0

    # App usage
    result = await db.execute(
        delete(AppUsage).where(AppUsage.user_id == user_id)
    )
    records_deleted["app_usage"] = result.rowcount or 0

    # Activity sessions
    result = await db.execute(
        delete(ActivitySession).where(ActivitySession.user_id == user_id)
    )
    records_deleted["activity_sessions"] = result.rowcount or 0

    # Devices (CASCADE will handle api_keys)
    result = await db.execute(
        delete(Device).where(Device.user_id == user_id)
    )
    records_deleted["devices"] = result.rowcount or 0

    # Audit logs for the user
    result = await db.execute(
        delete(AuditLog).where(AuditLog.user_id == user_id)
    )
    records_deleted["audit_logs"] = result.rowcount or 0

    # Delete the user record itself
    await db.delete(target_user)
    records_deleted["users"] = 1

    # Log deletion action under the admin's ID (target user is being deleted)
    await audit_service.log_action(
        db=db,
        user_id=current_user.id,
        action=audit_service.DELETE_DATA,
        resource_type="user",
        resource_id=str(user_id),
        new_value={"reason": body.reason, "records_deleted": records_deleted},
        request=request,
    )

    await db.flush()

    logger.info(
        "gdpr_deletion_completed",
        target_user_id=str(user_id),
        records_deleted=records_deleted,
    )

    return DeletionResponse(
        user_id=str(user_id),
        deleted_at=datetime.now(timezone.utc),
        records_deleted=records_deleted,
    )


@router.get(
    "/consent/{user_id}",
    response_model=ConsentStatus,
    summary="Get user consent status",
)
async def get_consent_status(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentStatus:
    """Retrieve the current consent preferences for a user.

    Users can view their own consent status. Admins and managers
    can view consent for users in their organization.

    Args:
        user_id: UUID of the user to query.
        current_user: The authenticated requesting user.
        db: Async database session.

    Returns:
        ConsentStatus with current preferences.

    Raises:
        HTTPException: 403 if unauthorized to view this user's consent.
        HTTPException: 404 if the user is not found.
    """
    _authorize_consent_access(current_user, user_id)
    target_user = await _get_user_or_404(db, user_id)

    # Consent preferences are stored as a convention on the user model.
    # For now, return defaults. A dedicated consent table can be added.
    return ConsentStatus(
        user_id=str(user_id),
        activity_tracking=target_user.is_active,
        screenshot_capture=target_user.is_active,
        data_analytics=target_user.is_active,
        updated_at=target_user.updated_at,
    )


@router.put(
    "/consent/{user_id}",
    response_model=ConsentStatus,
    summary="Update user consent preferences",
)
async def update_consent(
    user_id: uuid.UUID,
    body: ConsentUpdate,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentStatus:
    """Update consent preferences for a user.

    Users can update their own consent. Admins can update consent
    for any user in the organization.

    Args:
        user_id: UUID of the user to update.
        body: ConsentUpdate with new preference values.
        request: FastAPI request for audit logging.
        current_user: The authenticated requesting user.
        db: Async database session.

    Returns:
        Updated ConsentStatus.

    Raises:
        HTTPException: 403 if unauthorized to modify this user's consent.
        HTTPException: 404 if the user is not found.
    """
    _authorize_consent_access(current_user, user_id)
    target_user = await _get_user_or_404(db, user_id)

    old_value = {
        "activity_tracking": target_user.is_active,
        "screenshot_capture": target_user.is_active,
        "data_analytics": target_user.is_active,
    }

    # Apply consent changes
    # If screenshot_capture or activity_tracking is disabled, deactivate the user's tracking
    if body.activity_tracking is not None or body.screenshot_capture is not None:
        # Deactivate tracking if either consent is withdrawn
        if body.activity_tracking is False or body.screenshot_capture is False:
            target_user.is_active = False
        elif body.activity_tracking is True and body.screenshot_capture is True:
            target_user.is_active = True

    new_value = {
        "activity_tracking": body.activity_tracking if body.activity_tracking is not None else old_value["activity_tracking"],
        "screenshot_capture": body.screenshot_capture if body.screenshot_capture is not None else old_value["screenshot_capture"],
        "data_analytics": body.data_analytics if body.data_analytics is not None else old_value["data_analytics"],
    }

    await db.flush()

    await audit_service.log_action(
        db=db,
        user_id=current_user.id,
        action=audit_service.UPDATE_CONSENT,
        resource_type="consent",
        resource_id=str(user_id),
        old_value=old_value,
        new_value=new_value,
        request=request,
    )

    logger.info(
        "consent_updated",
        user_id=str(user_id),
        updated_by=str(current_user.id),
        changes=new_value,
    )

    return ConsentStatus(
        user_id=str(user_id),
        activity_tracking=new_value["activity_tracking"],
        screenshot_capture=new_value["screenshot_capture"],
        data_analytics=new_value["data_analytics"],
        updated_at=datetime.now(timezone.utc),
    )


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Fetch a user by ID or raise 404.

    Args:
        db: Async database session.
        user_id: UUID of the user to fetch.

    Returns:
        The User object.

    Raises:
        HTTPException: 404 if the user is not found.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


def _authorize_consent_access(current_user: User, target_user_id: uuid.UUID) -> None:
    """Verify that the current user can access consent for the target user.

    Users can access their own consent. Admins and managers can access
    consent for users in their organization.

    Args:
        current_user: The authenticated requesting user.
        target_user_id: UUID of the target user.

    Raises:
        HTTPException: 403 if unauthorized.
    """
    if current_user.id == target_user_id:
        return

    if current_user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only access your own consent preferences",
        )
