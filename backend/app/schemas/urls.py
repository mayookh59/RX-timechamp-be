"""URL visit schemas for request validation and response serialization.

Covers URL visit ingestion, paginated retrieval,
and top-domains analytics.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class UrlVisitCreate(BaseModel):
    """Single URL visit record submitted by the desktop agent.

    Attributes:
        client_id: Client-generated UUID for idempotent ingestion.
        browser: Browser application name.
        url: Full URL visited.
        domain: Extracted domain from the URL.
        page_title: Page title at time of visit.
        visit_time: When the URL was visited.
        duration_sec: Time spent on the page in seconds.
    """

    client_id: uuid.UUID = Field(..., description="Client-generated idempotency key")
    browser: str = Field(..., max_length=100, description="Browser application name")
    url: str = Field(..., max_length=2048, description="Full URL visited")
    domain: str = Field(..., max_length=255, description="Extracted domain")
    page_title: str = Field(..., max_length=1024, description="Page title")
    visit_time: datetime = Field(..., description="Visit timestamp (UTC)")
    duration_sec: int | None = Field(None, ge=0, description="Duration in seconds")


class UrlVisitIngestRequest(BaseModel):
    """Batch URL visit ingestion request from a device.

    Attributes:
        device_id: UUID of the reporting device.
        records: List of URL visit records to ingest.
    """

    device_id: uuid.UUID = Field(..., description="Reporting device UUID")
    records: list[UrlVisitCreate] = Field(
        ..., min_length=1, max_length=1000, description="URL visit records to ingest"
    )


class UrlVisitResponse(BaseModel):
    """URL visit response for API consumers.

    Attributes:
        id: Server-assigned record UUID.
        browser: Browser application name.
        url: Full URL visited.
        domain: Extracted domain.
        page_title: Page title.
        visit_time: When the URL was visited.
        duration_sec: Time spent in seconds.
    """

    id: uuid.UUID
    browser: str
    url: str
    domain: str
    page_title: str
    visit_time: datetime
    duration_sec: int | None

    model_config = {"from_attributes": True}


class TopDomainsResponse(BaseModel):
    """Top domain by total visit time.

    Attributes:
        domain: Domain name.
        total_hours: Total time spent in hours.
        visit_count: Number of visits.
    """

    domain: str = Field(..., description="Domain name")
    total_hours: float = Field(..., ge=0, description="Total time spent in hours")
    visit_count: int = Field(..., ge=0, description="Number of visits")
