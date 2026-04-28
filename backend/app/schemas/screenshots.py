"""Screenshot schemas for request validation and response serialization.

Covers presigned URL generation, upload confirmation,
individual screenshot retrieval, and paginated gallery listing.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PresignRequest(BaseModel):
    """Request to generate a presigned S3 upload URL.

    Attributes:
        device_id: UUID of the reporting device.
        filename: Original filename of the screenshot.
        content_type: MIME type of the screenshot file.
    """

    device_id: uuid.UUID = Field(..., description="Reporting device UUID")
    filename: str = Field(..., max_length=255, description="Original filename")
    content_type: str = Field(
        "image/png",
        max_length=100,
        description="MIME content type",
    )


class PresignResponse(BaseModel):
    """Response containing a presigned S3 upload URL.

    Attributes:
        upload_url: Presigned S3 PUT URL for uploading the screenshot.
        screenshot_id: Server-assigned screenshot UUID for confirmation.
        storage_key: S3 object key where the file will be stored.
    """

    upload_url: str = Field(..., description="Presigned S3 PUT URL")
    screenshot_id: uuid.UUID = Field(..., description="Screenshot record UUID")
    storage_key: str = Field(..., description="S3 object key")


class ScreenshotConfirmRequest(BaseModel):
    """Confirmation that a screenshot was successfully uploaded to S3.

    Attributes:
        screenshot_id: The screenshot UUID returned from presign.
        file_size: Size of the uploaded file in bytes.
    """

    screenshot_id: uuid.UUID = Field(..., description="Screenshot record UUID")
    file_size: int = Field(..., gt=0, description="Uploaded file size in bytes")


class ScreenshotResponse(BaseModel):
    """Individual screenshot response with download URL.

    Attributes:
        id: Screenshot UUID.
        user_id: Owner user UUID.
        device_id: Device that captured the screenshot.
        captured_at: Timestamp of capture.
        file_size: File size in bytes.
        download_url: Presigned S3 GET URL for downloading.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    device_id: uuid.UUID
    captured_at: datetime
    file_size: int
    download_url: str = Field(..., description="Presigned S3 GET URL")

    model_config = {"from_attributes": True}


class ScreenshotGalleryResponse(BaseModel):
    """Paginated gallery of screenshots.

    Attributes:
        screenshots: List of screenshot responses for the current page.
        total: Total screenshots matching the query.
        has_more: Whether more pages are available.
    """

    screenshots: list[ScreenshotResponse]
    total: int = Field(..., ge=0, description="Total matching screenshots")
    has_more: bool = Field(..., description="Whether more pages exist")
