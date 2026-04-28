"""Report schemas for report generation, listing, and scheduling.

Provides request/response models for the reports API endpoints.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    """Request to generate a new report.

    Attributes:
        report_type: Type of report (e.g. productivity, activity, summary).
        date_range_start: Start date for the report period.
        date_range_end: End date for the report period.
        filters: Optional key-value filters (user IDs, teams, etc.).
        format: Output format (pdf, csv, xlsx).
    """

    report_type: str | None = Field(None, description="Report type: productivity, activity, summary")
    type: str | None = Field(None, description="Alias for report_type (frontend compat)")
    date_range_start: str | None = Field(None, description="ISO date for report start")
    date_range_end: str | None = Field(None, description="ISO date for report end")
    start_date: str | None = Field(None, description="Alias for date_range_start (frontend compat)")
    end_date: str | None = Field(None, description="Alias for date_range_end (frontend compat)")
    filters: dict | None = Field(None, description="Optional filters (user_ids, teams, etc.)")
    user_id: str | None = Field(None, description="Filter report to a specific user")
    format: str | None = Field(None, description="Output format: pdf, csv, xlsx")
    export_format: str | None = Field(None, description="Alias for format (frontend compat)")
    include_charts: bool = True
    include_breakdowns: bool = True
    include_user_data: bool = False

    @property
    def resolved_type(self) -> str:
        return self.report_type or self.type or "productivity"

    @property
    def resolved_start(self) -> str | None:
        return self.date_range_start or self.start_date

    @property
    def resolved_end(self) -> str | None:
        return self.date_range_end or self.end_date

    @property
    def resolved_format(self) -> str:
        return self.format or self.export_format or "pdf"


class ReportGenerateResponse(BaseModel):
    """Response after requesting report generation.

    Attributes:
        report_id: Unique identifier for the report job.
        status: Current processing status.
    """

    report_id: uuid.UUID
    status: str = Field(..., description="processing, completed, failed")


class ReportResponse(BaseModel):
    """Full report object returned from listing or detail endpoints.

    Attributes:
        id: Report UUID.
        report_type: Type of report.
        status: Processing status.
        format: Output format.
        created_at: When the report was requested.
        completed_at: When the report finished generating.
        download_url: URL to download the report file (if ready).
    """

    id: uuid.UUID
    report_type: str
    status: str = Field(..., description="processing, completed, failed")
    format: str = Field("pdf", description="Output format")
    created_at: datetime
    completed_at: datetime | None = None
    download_url: str | None = None


class ReportScheduleRequest(BaseModel):
    """Request to schedule a recurring report.

    Attributes:
        report_type: Type of report to schedule.
        cron_expression: Cron expression for the schedule.
        filters: Optional filters for the report.
        format: Output format.
        recipients: Email addresses to receive the report.
    """

    report_type: str = Field(..., description="Report type: productivity, activity, summary")
    cron_expression: str = Field(..., description="Cron expression (e.g. '0 9 * * 1')")
    filters: dict | None = Field(None, description="Optional filters")
    format: str = Field("pdf", description="Output format: pdf, csv, xlsx")
    recipients: list[str] = Field(default_factory=list, description="Email recipients")


class ReportScheduleResponse(BaseModel):
    """Response after scheduling a report.

    Attributes:
        schedule_id: Unique identifier for the schedule.
        status: Schedule status.
    """

    schedule_id: uuid.UUID
    status: str = Field("scheduled", description="Schedule status")
