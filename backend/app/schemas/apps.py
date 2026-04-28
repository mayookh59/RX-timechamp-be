"""App usage schemas for request validation and response serialization.

Covers application usage ingestion, paginated retrieval,
and top-apps analytics.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AppUsageCreate(BaseModel):
    """Single app usage record submitted by the desktop agent.

    Attributes:
        client_id: Client-generated UUID for idempotent ingestion.
        process_name: Executable process name.
        window_title: Active window title text.
        start_time: When the application became active.
        end_time: When the application lost focus.
        duration_seconds: Usage duration in seconds.
    """

    client_id: uuid.UUID = Field(..., description="Client-generated idempotency key")
    process_name: str = Field(..., max_length=255, description="Process executable name")
    window_title: str = Field(..., max_length=1024, description="Active window title")
    start_time: datetime = Field(..., description="Application focus start (UTC)")
    end_time: datetime | None = Field(None, description="Application focus end (UTC)")
    duration_seconds: int | None = Field(None, ge=0, description="Duration in seconds")


class AppUsageIngestRequest(BaseModel):
    """Batch app usage ingestion request from a device.

    Attributes:
        device_id: UUID of the reporting device.
        records: List of app usage records to ingest.
    """

    device_id: uuid.UUID = Field(..., description="Reporting device UUID")
    records: list[AppUsageCreate] = Field(
        ..., min_length=1, max_length=1000, description="App usage records to ingest"
    )


class AppUsageResponse(BaseModel):
    """App usage response for API consumers.

    Attributes:
        id: Server-assigned record UUID.
        process_name: Process executable name.
        window_title: Active window title.
        start_time: When the application became active.
        end_time: When the application lost focus.
        duration_sec: Usage duration in seconds.
    """

    id: uuid.UUID
    process_name: str
    window_title: str
    start_time: datetime
    end_time: datetime | None
    duration_sec: int | None

    model_config = {"from_attributes": True}


class TopAppsResponse(BaseModel):
    """Top application by total usage time.

    Attributes:
        name: Process name of the application.
        total_hours: Total usage hours.
        session_count: Number of usage sessions.
    """

    name: str = Field(..., description="Application process name")
    total_hours: float = Field(..., ge=0, description="Total usage hours")
    session_count: int = Field(..., ge=0, description="Number of usage sessions")
