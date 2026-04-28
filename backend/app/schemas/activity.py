"""Activity session schemas for request validation and response serialization.

Covers ingestion from desktop agents, paginated session retrieval,
and aggregated activity summaries.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ActivitySessionCreate(BaseModel):
    """Single activity session submitted by the desktop agent.

    Attributes:
        client_id: Client-generated UUID for idempotent ingestion.
        session_type: Type of session (active, idle, away, locked).
        start_time: When the session began (UTC).
        end_time: When the session ended (UTC), null if ongoing.
    """

    client_id: uuid.UUID = Field(..., description="Client-generated idempotency key")
    session_type: str = Field(..., description="Session type: active, idle, away, locked")
    start_time: datetime = Field(..., description="Session start timestamp (UTC)")
    end_time: datetime | None = Field(None, description="Session end timestamp (UTC)")

    @field_validator("session_type")
    @classmethod
    def validate_session_type(cls, v: str) -> str:
        """Ensure session_type is one of the allowed values."""
        allowed = {"active", "idle", "away", "locked"}
        if v not in allowed:
            msg = f"session_type must be one of {allowed}"
            raise ValueError(msg)
        return v


class ActivityIngestRequest(BaseModel):
    """Batch activity session ingestion request from a device.

    Attributes:
        device_id: UUID of the reporting device.
        sessions: List of activity sessions to ingest.
    """

    device_id: uuid.UUID = Field(..., description="Reporting device UUID")
    sessions: list[ActivitySessionCreate] = Field(
        ..., min_length=1, max_length=1000, description="Activity sessions to ingest"
    )


class ActivitySessionResponse(BaseModel):
    """Activity session response for API consumers.

    Attributes:
        id: Server-assigned session UUID.
        client_id: Client-generated idempotency key.
        device_id: Reporting device UUID.
        user_id: Session owner UUID.
        session_type: Type of session.
        start_time: When the session began.
        end_time: When the session ended.
        duration_sec: Computed duration in seconds.
    """

    id: uuid.UUID
    client_id: uuid.UUID
    device_id: uuid.UUID
    user_id: uuid.UUID
    session_type: str
    start_time: datetime
    end_time: datetime | None
    duration_sec: int | None

    model_config = {"from_attributes": True}


class ActivitySummaryResponse(BaseModel):
    """Aggregated activity summary for a date range.

    Attributes:
        period: Human-readable period description (e.g. "2026-03-01 to 2026-03-26").
        total_active_hours: Total active hours in the period.
        total_idle_hours: Total idle hours in the period.
        active_ratio: Ratio of active to total tracked time (0.0-1.0).
        sessions_count: Total number of sessions in the period.
    """

    period: str = Field(..., description="Human-readable period description")
    total_active_hours: float = Field(..., ge=0, description="Total active hours")
    total_idle_hours: float = Field(..., ge=0, description="Total idle hours")
    active_ratio: float = Field(..., ge=0, le=1, description="Active time ratio")
    sessions_count: int = Field(..., ge=0, description="Total sessions in period")
