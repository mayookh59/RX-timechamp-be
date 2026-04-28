"""Settings API endpoints for organization-level configuration.

Provides endpoints to read and update the organization's settings
stored in the ``organizations.settings`` JSONB column.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_admin, get_current_user
from app.models.organization import Organization
from app.models.user import User
from app.schemas.settings import OrgSettingsResponse, OrgSettingsUpdateRequest
from app.storage.database import get_db

router = APIRouter(prefix="/settings", tags=["Settings"])

logger = structlog.stdlib.get_logger(__name__)


@router.get(
    "",
    response_model=OrgSettingsResponse,
    summary="Get organization settings",
)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve the current organization's settings.

    Settings are stored as a JSONB column on the organizations table.
    Returns an empty dict when no settings have been configured yet.

    Args:
        current_user: Authenticated user.
        db: Async database session.

    Returns:
        Organization settings dictionary.

    Raises:
        HTTPException: 404 if the organization is not found.
    """
    org = await _get_organization(db, current_user)

    logger.info(
        "settings_retrieved",
        org_id=str(current_user.org_id),
        user_id=str(current_user.id),
    )

    return {"settings": org.settings or {}}


@router.post(
    "",
    response_model=OrgSettingsResponse,
    summary="Update organization settings",
)
async def update_settings(
    body: OrgSettingsUpdateRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update the current organization's settings.

    Replaces the existing settings JSONB column with the provided
    settings object. Only admins are allowed to modify settings.

    Args:
        body: Request body containing the new settings.
        current_user: Authenticated admin user.
        db: Async database session.

    Returns:
        The updated organization settings.

    Raises:
        HTTPException: 404 if the organization is not found.
    """
    org = await _get_organization(db, current_user)

    org.settings = body.settings
    await db.flush()
    await db.refresh(org)

    logger.info(
        "settings_updated",
        org_id=str(current_user.org_id),
        updated_by=str(current_user.id),
    )

    return {"settings": org.settings or {}}


async def _get_organization(
    db: AsyncSession,
    current_user: User,
) -> Organization:
    """Fetch the organization for the current user.

    Args:
        db: Async database session.
        current_user: Authenticated user whose org to fetch.

    Returns:
        The Organization instance.

    Raises:
        HTTPException: 404 if the organization is not found.
    """
    stmt = select(Organization).where(Organization.id == current_user.org_id)
    result = await db.execute(stmt)
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return org
