"""Screenshot service for local-storage screenshot management.

Handles screenshot ingestion from agent (base64), local file storage,
paginated gallery listing, and deletion.
"""

import base64
import os
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity import Screenshot

logger = structlog.stdlib.get_logger(__name__)

# Local screenshot storage directory
SCREENSHOT_DIR = Path(os.environ.get("SCREENSHOT_DIR", "screenshots_store"))
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


async def ingest_screenshot(
    device_id: uuid.UUID,
    user_id: uuid.UUID,
    screenshot_id: str,
    captured_at: str,
    image_base64: str,
    db: AsyncSession,
) -> dict:
    """Ingest a base64-encoded screenshot from the agent and store locally."""
    # Decode and save to disk
    image_bytes = base64.b64decode(image_base64)
    file_name = f"{screenshot_id}.jpg"
    device_dir = SCREENSHOT_DIR / str(device_id)
    device_dir.mkdir(parents=True, exist_ok=True)
    file_path = device_dir / file_name
    file_path.write_bytes(image_bytes)

    storage_key = f"{device_id}/{file_name}"
    client_id = uuid.UUID(screenshot_id) if screenshot_id else uuid.uuid4()

    # Check for duplicate
    existing = await db.execute(
        select(Screenshot).where(Screenshot.client_id == client_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "screenshot_id": screenshot_id}

    captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))

    screenshot = Screenshot(
        id=uuid.uuid4(),
        client_id=client_id,
        device_id=device_id,
        user_id=user_id,
        storage_key=storage_key,
        captured_at=captured_dt,
        file_size=len(image_bytes),
    )
    db.add(screenshot)
    await db.flush()

    logger.info("screenshot_ingested", screenshot_id=screenshot_id, size=len(image_bytes))
    return {"status": "ok", "screenshot_id": screenshot_id}


async def list_screenshots(
    user_id: uuid.UUID | None,
    start_date: date | None,
    end_date: date | None,
    page: int,
    per_page: int,
    db: AsyncSession,
    device_id: uuid.UUID | None = None,
) -> dict:
    """List screenshots with pagination.

    Args:
        user_id: Owner user UUID (None = all users for admin).
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        page: Page number (1-indexed).
        per_page: Results per page.
        db: Async database session.
        device_id: Optional device filter.

    Returns:
        Dict with screenshots list, total count, and has_more flag.
    """
    base_stmt = select(Screenshot).where(Screenshot.file_size > 0)
    if user_id is not None:
        base_stmt = base_stmt.where(Screenshot.user_id == user_id)

    if start_date is not None:
        base_stmt = base_stmt.where(func.date(Screenshot.captured_at) >= start_date.isoformat())

    if end_date is not None:
        base_stmt = base_stmt.where(func.date(Screenshot.captured_at) <= end_date.isoformat())

    if device_id is not None:
        base_stmt = base_stmt.where(Screenshot.device_id == device_id)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    base_stmt = base_stmt.order_by(Screenshot.captured_at.desc())
    offset = (page - 1) * per_page
    base_stmt = base_stmt.offset(offset).limit(per_page)

    result = await db.execute(base_stmt)
    screenshots = list(result.scalars().all())

    screenshot_responses = []
    for s in screenshots:
        download_url = f"/screenshots/file/{s.storage_key}"
        screenshot_responses.append({
            "id": s.id,
            "user_id": s.user_id,
            "device_id": s.device_id,
            "captured_at": s.captured_at,
            "file_size": s.file_size,
            "download_url": download_url,
        })

    has_more = (page * per_page) < total

    return {
        "screenshots": screenshot_responses,
        "total": total,
        "has_more": has_more,
    }


async def get_screenshot(
    screenshot_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Get a single screenshot with a presigned download URL.

    Args:
        screenshot_id: Screenshot UUID.
        db: Async database session.

    Returns:
        Dict with screenshot metadata and download URL.

    Raises:
        HTTPException: If the screenshot is not found.
    """
    result = await db.execute(
        select(Screenshot).where(Screenshot.id == screenshot_id)
    )
    screenshot = result.scalar_one_or_none()

    if screenshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot not found",
        )

    download_url = f"/screenshots/file/{screenshot.storage_key}"

    return {
        "id": screenshot.id,
        "user_id": screenshot.user_id,
        "device_id": screenshot.device_id,
        "captured_at": screenshot.captured_at,
        "file_size": screenshot.file_size,
        "download_url": download_url,
    }


async def delete_screenshot(
    screenshot_id: uuid.UUID,
    deleted_by: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Soft-delete a screenshot by removing the S3 object and zeroing file_size.

    Marks the screenshot as deleted by setting file_size to 0 and logs
    the deletion for audit purposes.

    Args:
        screenshot_id: Screenshot UUID to delete.
        deleted_by: UUID of the user performing the deletion.
        db: Async database session.

    Raises:
        HTTPException: If the screenshot is not found.
    """
    result = await db.execute(
        select(Screenshot).where(Screenshot.id == screenshot_id)
    )
    screenshot = result.scalar_one_or_none()

    if screenshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot not found",
        )

    # Attempt to delete from local storage
    try:
        file_path = SCREENSHOT_DIR / screenshot.storage_key
        if file_path.exists():
            file_path.unlink()
    except Exception as exc:
        logger.error(
            "file_delete_failed",
            screenshot_id=str(screenshot_id),
            error=str(exc),
        )

    # Soft-delete: zero out file_size as marker
    screenshot.file_size = 0
    await db.flush()

    logger.info(
        "screenshot_deleted",
        screenshot_id=str(screenshot_id),
        deleted_by=str(deleted_by),
        storage_key=screenshot.storage_key,
    )
