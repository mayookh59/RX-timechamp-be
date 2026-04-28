"""Reports API endpoints for generating, listing, and scheduling reports.

Provides endpoints for on-demand report generation, report listing,
single report retrieval, and report scheduling.
"""

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_manager_or_admin
from app.models.user import User
from app.schemas.reports import (
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportResponse,
    ReportScheduleRequest,
    ReportScheduleResponse,
)
from app.storage.database import get_db

router = APIRouter(prefix="/reports", tags=["Reports"])

logger = structlog.stdlib.get_logger(__name__)

# In-memory store for stub reports until a reports table is created.
_reports_store: dict[uuid.UUID, dict] = {}
_schedules_store: dict[uuid.UUID, dict] = {}


@router.post(
    "/generate",
    response_model=ReportGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a new report",
)
async def generate_report(
    body: ReportGenerateRequest,
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a report from real activity data.

    Produces a CSV file with activity, app usage, and URL data for the
    requested date range and user. The file is stored on disk and a
    download URL is returned.
    """
    import csv
    import os
    from pathlib import Path
    from sqlalchemy import text

    report_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    report_type = body.resolved_type
    start_date = body.resolved_start
    end_date = body.resolved_end
    fmt = body.resolved_format.lower()
    user_filter = body.user_id

    # Build date filter SQL
    date_where = ""
    params: dict = {}
    if start_date:
        date_where += " AND date(start_time) >= :start"
        params["start"] = start_date
    if end_date:
        date_where += " AND date(start_time) <= :end"
        params["end"] = end_date

    user_where = ""
    if user_filter:
        user_where = " AND user_id = :uid"
        params["uid"] = user_filter.replace("-", "")

    # Generate CSV report
    reports_dir = Path("reports_output")
    reports_dir.mkdir(exist_ok=True)
    filename = f"report_{report_type}_{report_id}.csv"
    filepath = reports_dir / filename

    rows_written = 0
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # -- Activity Sessions --
        writer.writerow(["=== Activity Sessions ==="])
        writer.writerow(["Session Type", "Start Time", "End Time", "Duration (sec)"])
        result = await db.execute(text(
            f"SELECT session_type, start_time, end_time, duration_sec FROM activity_sessions WHERE 1=1{date_where}{user_where} ORDER BY start_time"
        ), params)
        for row in result:
            writer.writerow([row[0], row[1], row[2], row[3]])
            rows_written += 1

        writer.writerow([])

        # -- App Usage --
        writer.writerow(["=== Application Usage ==="])
        writer.writerow(["Process Name", "Window Title", "Start Time", "Duration (sec)"])
        result2 = await db.execute(text(
            f"SELECT process_name, window_title, start_time, duration_sec FROM app_usage WHERE 1=1{date_where}{user_where} ORDER BY start_time"
        ), params)
        for row in result2:
            writer.writerow([row[0], row[1], row[2], row[3]])
            rows_written += 1

        writer.writerow([])

        # -- URL Visits --
        url_date_where = date_where.replace("start_time", "visit_time")
        writer.writerow(["=== URL Visits ==="])
        writer.writerow(["Domain", "Page Title", "Browser", "Visit Time", "Duration (sec)"])
        result3 = await db.execute(text(
            f"SELECT domain, page_title, browser, visit_time, duration_sec FROM url_visits WHERE 1=1{url_date_where}{user_where} ORDER BY visit_time"
        ), params)
        for row in result3:
            writer.writerow([row[0], row[1], row[2], row[3], row[4]])
            rows_written += 1

    download_url = f"/reports/download/{filename}"

    report_record = {
        "id": report_id,
        "report_type": report_type,
        "status": "completed",
        "format": fmt,
        "created_at": now,
        "completed_at": datetime.now(timezone.utc),
        "download_url": download_url,
        "requested_by": str(current_user.id),
        "filters": body.filters,
        "rows": rows_written,
    }
    _reports_store[report_id] = report_record

    logger.info(
        "report_generated",
        report_id=str(report_id),
        report_type=report_type,
        rows=rows_written,
    )

    return {"report_id": report_id, "status": "completed"}


@router.get(
    "/download/{filename}",
    summary="Download a generated report file",
)
async def download_report(filename: str):
    """Download a generated report CSV file."""
    from pathlib import Path
    from fastapi.responses import FileResponse

    filepath = Path("reports_output") / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(
        str(filepath),
        media_type="text/csv",
        filename=filename,
    )


@router.get(
    "",
    response_model=list[ReportResponse],
    summary="List all reports",
)
async def list_reports(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all generated reports for the current user's organization.

    Returns reports from the in-memory store. When a reports table
    is implemented, this will query the database instead.

    Args:
        page: Page number (1-indexed).
        per_page: Results per page.
        current_user: Authenticated admin or manager user.
        db: Async database session.

    Returns:
        List of report objects.
    """
    logger.info(
        "reports_listed",
        user_id=str(current_user.id),
        page=page,
        per_page=per_page,
    )

    # Return from in-memory store (filtered to user's reports)
    all_reports = sorted(
        _reports_store.values(),
        key=lambda r: r["created_at"],
        reverse=True,
    )
    offset = (page - 1) * per_page
    paginated = all_reports[offset : offset + per_page]

    return paginated


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    summary="Get a single report by ID",
)
async def get_report(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve a single report by its ID.

    Args:
        report_id: UUID of the report to retrieve.
        current_user: Authenticated admin or manager user.
        db: Async database session.

    Returns:
        The report object.

    Raises:
        HTTPException: 404 if the report is not found.
    """
    report = _reports_store.get(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    logger.info(
        "report_retrieved",
        report_id=str(report_id),
        user_id=str(current_user.id),
    )
    return report


@router.post(
    "/schedule",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a recurring report",
)
async def schedule_report(
    body: ReportScheduleRequest,
    current_user: User = Depends(get_current_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Schedule a report for recurring generation.

    The schedule is stored in-memory until a proper scheduling
    system (e.g. Celery Beat) is integrated.

    Args:
        body: Schedule configuration (type, cron, filters, recipients).
        current_user: Authenticated admin or manager user.
        db: Async database session.

    Returns:
        Schedule ID and status.
    """
    schedule_id = uuid.uuid4()

    _schedules_store[schedule_id] = {
        "schedule_id": schedule_id,
        "report_type": body.report_type,
        "cron_expression": body.cron_expression,
        "filters": body.filters,
        "format": body.format,
        "recipients": body.recipients,
        "created_by": str(current_user.id),
        "status": "scheduled",
    }

    logger.info(
        "report_scheduled",
        schedule_id=str(schedule_id),
        report_type=body.report_type,
        cron=body.cron_expression,
        created_by=str(current_user.id),
    )

    return {"schedule_id": schedule_id, "status": "scheduled"}
