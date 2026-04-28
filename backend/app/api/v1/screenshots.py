"""Screenshot API endpoints.

Provides endpoints for agent screenshot ingestion (base64),
local file serving, paginated gallery listing, individual retrieval, and deletion.
"""

import uuid
from datetime import date
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_manager_or_admin, get_current_user, get_device_from_api_key
from app.models.device import Device
from app.models.user import User
from app.schemas.screenshots import (
    ScreenshotGalleryResponse,
    ScreenshotResponse,
)
from app.services import screenshot_service
from app.storage.database import get_db

router = APIRouter(prefix="/screenshots", tags=["screenshots"])

logger = structlog.stdlib.get_logger(__name__)


class ScreenshotIngestRequest(BaseModel):
    device_id: str
    screenshot_id: str
    captured_at: str
    image_base64: str


@router.post(
    "/ingest",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a base64-encoded screenshot from agent",
)
async def ingest_screenshot(
    body: ScreenshotIngestRequest,
    device: Device = Depends(get_device_from_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await screenshot_service.ingest_screenshot(
        device_id=device.id,
        user_id=device.user_id,
        screenshot_id=body.screenshot_id,
        captured_at=body.captured_at,
        image_base64=body.image_base64,
        db=db,
    )
    await db.commit()
    return result


@router.get(
    "/file/{device_id}/{filename}",
    summary="Serve a screenshot image file from local storage",
)
async def serve_screenshot_file(device_id: str, filename: str):
    file_path = screenshot_service.SCREENSHOT_DIR / device_id / filename
    if not file_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="image/jpeg")


@router.get(
    "",
    response_model=ScreenshotGalleryResponse,
    summary="List screenshots in a paginated gallery",
)
async def list_screenshots(
    start_date: date | None = Query(None, description="Start date filter (inclusive)"),
    end_date: date | None = Query(None, description="End date filter (inclusive)"),
    user_id: uuid.UUID | None = Query(None, description="Filter by user ID (admin/manager)"),
    device_id: uuid.UUID | None = Query(None, description="Filter by device ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve paginated screenshot gallery with presigned download URLs.

    Regular users see only their own screenshots. Admins and managers
    can filter by user_id.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        user_id: Optional user filter (admin/manager only).
        device_id: Optional device filter.
        page: Page number (1-indexed).
        per_page: Results per page.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Gallery response with screenshots, total count, and has_more flag.
    """
    target_user_id = current_user.id
    if current_user.role in {"admin", "manager"}:
        target_user_id = user_id  # None means all users for admin

    return await screenshot_service.list_screenshots(
        user_id=target_user_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        per_page=per_page,
        db=db,
        device_id=device_id,
    )


@router.get(
    "/{screenshot_id}",
    response_model=ScreenshotResponse,
    summary="Get a single screenshot with download URL",
)
async def get_screenshot(
    screenshot_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve a single screenshot with a presigned download URL.

    Args:
        screenshot_id: Screenshot UUID.
        current_user: Authenticated user from JWT.
        db: Async database session.

    Returns:
        Screenshot metadata with presigned download URL.
    """
    return await screenshot_service.get_screenshot(
        screenshot_id=screenshot_id,
        db=db,
    )


@router.delete(
    "/{screenshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a screenshot (admin/manager only)",
)
async def delete_screenshot(
    screenshot_id: uuid.UUID,
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a screenshot. Removes the S3 object and zeros the file_size.

    Requires admin or manager role.

    Args:
        screenshot_id: Screenshot UUID to delete.
        current_user: Authenticated admin/manager from JWT.
        db: Async database session.
    """
    await screenshot_service.delete_screenshot(
        screenshot_id=screenshot_id,
        deleted_by=current_user.id,
        db=db,
    )
    logger.info(
        "screenshot_deleted_via_api",
        screenshot_id=str(screenshot_id),
        deleted_by=str(current_user.id),
    )
