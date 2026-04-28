"""GDPR-related Pydantic schemas for data export, deletion, and consent.

Supports right to data portability (export), right to be forgotten
(deletion), and consent management endpoints.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DataExportResponse(BaseModel):
    """Response schema for GDPR data export (Article 20).

    Contains all user data in a portable JSON format.

    Attributes:
        user_id: UUID of the exported user.
        exported_at: Timestamp of the export.
        user_profile: User profile information.
        devices: List of enrolled devices.
        activity_sessions: Activity session records.
        app_usage: Application usage records.
        url_visits: URL visit records.
        screenshots: Screenshot metadata (without binary data).
        audit_logs: Audit trail entries for the user.
    """

    user_id: str
    exported_at: datetime
    user_profile: dict[str, Any]
    devices: list[dict[str, Any]]
    activity_sessions: list[dict[str, Any]]
    app_usage: list[dict[str, Any]]
    url_visits: list[dict[str, Any]]
    screenshots: list[dict[str, Any]]
    audit_logs: list[dict[str, Any]]


class DeletionRequest(BaseModel):
    """Request schema for GDPR data deletion (Article 17).

    Attributes:
        confirm: Must be True to proceed with deletion.
        reason: Optional reason for the deletion request.
    """

    confirm: bool = Field(
        ...,
        description="Must be True to confirm deletion. This action is irreversible.",
    )
    reason: str | None = Field(
        None,
        max_length=500,
        description="Optional reason for requesting data deletion.",
    )


class DeletionResponse(BaseModel):
    """Response schema for GDPR deletion confirmation.

    Attributes:
        user_id: UUID of the deleted user.
        deleted_at: Timestamp of the deletion.
        records_deleted: Summary of deleted record counts by type.
        status: Deletion status message.
    """

    user_id: str
    deleted_at: datetime
    records_deleted: dict[str, int]
    status: str = "completed"


class ConsentStatus(BaseModel):
    """Response schema for current consent preferences.

    Attributes:
        user_id: UUID of the user.
        activity_tracking: Whether the user consents to activity tracking.
        screenshot_capture: Whether the user consents to screenshot capture.
        data_analytics: Whether the user consents to data analytics.
        updated_at: When consent preferences were last modified.
    """

    user_id: str
    activity_tracking: bool = True
    screenshot_capture: bool = True
    data_analytics: bool = True
    updated_at: datetime | None = None


class ConsentUpdate(BaseModel):
    """Request schema for updating consent preferences.

    Attributes:
        activity_tracking: Consent to activity session tracking.
        screenshot_capture: Consent to periodic screenshot capture.
        data_analytics: Consent to aggregate data analytics.
    """

    activity_tracking: bool | None = Field(
        None,
        description="Consent to activity session tracking.",
    )
    screenshot_capture: bool | None = Field(
        None,
        description="Consent to periodic screenshot capture.",
    )
    data_analytics: bool | None = Field(
        None,
        description="Consent to aggregate data analytics.",
    )
