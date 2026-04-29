"""Dashboard analytics service for organization and user insights.

Provides overview metrics, individual user dashboards,
and time-series trend data for charting.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

import structlog
from sqlalchemy import func, select, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivitySession, AppUsage, UrlVisit
from app.models.device import Device
from app.models.user import User
from app.services import app_service, url_service

logger = structlog.stdlib.get_logger(__name__)


async def get_overview(
    org_id: uuid.UUID,
    start_date: date | None,
    end_date: date | None,
    db: AsyncSession,
) -> dict:
    """Compute organization-wide dashboard overview.

    Aggregates user counts, activity hours, idle ratios,
    and top apps/domains across all users in the organization.

    Args:
        org_id: Organization UUID.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        db: Async database session.

    Returns:
        Dict matching DashboardOverviewResponse fields.
    """
    # Total users in org
    total_users_result = await db.execute(
        select(func.count(User.id)).where(User.org_id == org_id, User.is_active.is_(True))
    )
    total_users = total_users_result.scalar() or 0

    # Users active today — count from activity sessions OR recent device heartbeats
    today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    today_end = datetime.combine(date.today(), time.max, tzinfo=timezone.utc)
    active_today_stmt = (
        select(func.count(func.distinct(ActivitySession.user_id)))
        .join(User, ActivitySession.user_id == User.id)
        .where(
            User.org_id == org_id,
            ActivitySession.start_time >= today_start,
            ActivitySession.start_time <= today_end,
        )
    )
    active_today = (await db.execute(active_today_stmt)).scalar() or 0

    # Fallback: also count users with devices that sent heartbeats today
    if active_today == 0:
        from app.models.device import Device
        active_devices_stmt = (
            select(func.count(func.distinct(Device.user_id)))
            .join(User, Device.user_id == User.id)
            .where(
                User.org_id == org_id,
                Device.last_heartbeat >= today_start,
            )
        )
        active_today = (await db.execute(active_devices_stmt)).scalar() or 0

    # Build date filters for activity
    org_user_ids_stmt = select(User.id).where(User.org_id == org_id)
    org_user_ids = (await db.execute(org_user_ids_stmt)).scalars().all()

    if not org_user_ids:
        period_start = start_date.isoformat() if start_date else "all"
        period_end = end_date.isoformat() if end_date else "now"
        return {
            "period": f"{period_start} to {period_end}",
            "total_users": total_users,
            "active_today": active_today,
            "avg_active_hours": 0.0,
            "avg_idle_ratio": 0.0,
            "top_apps": [],
            "top_domains": [],
        }

    # Avg active hours and idle ratio across org
    active_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(
        ActivitySession.user_id.in_(org_user_ids),
        ActivitySession.session_type == "active",
    )
    idle_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(
        ActivitySession.user_id.in_(org_user_ids),
        ActivitySession.session_type.in_(["idle", "away", "locked"]),
    )

    if start_date is not None:
        active_stmt = active_stmt.where(func.date(ActivitySession.start_time) >= start_date)
        idle_stmt = idle_stmt.where(func.date(ActivitySession.start_time) >= start_date)
    if end_date is not None:
        active_stmt = active_stmt.where(func.date(ActivitySession.start_time) <= end_date)
        idle_stmt = idle_stmt.where(func.date(ActivitySession.start_time) <= end_date)

    active_secs = (await db.execute(active_stmt)).scalar() or 0
    idle_secs = (await db.execute(idle_stmt)).scalar() or 0
    total_secs = active_secs + idle_secs

    avg_active_hours = round((active_secs / 3600) / max(total_users, 1), 2)
    avg_idle_ratio = round(idle_secs / max(total_secs, 1), 4)

    # Top apps (org-wide, pick first user's org scope)
    top_apps_stmt = (
        select(
            AppUsage.process_name,
            func.coalesce(func.sum(AppUsage.duration_sec), 0).label("total_secs"),
            func.count(AppUsage.id).label("session_count"),
        )
        .where(AppUsage.user_id.in_(org_user_ids))
        .group_by(AppUsage.process_name)
        .order_by(func.sum(AppUsage.duration_sec).desc().nulls_last())
        .limit(10)
    )
    if start_date is not None:
        top_apps_stmt = top_apps_stmt.where(func.date(AppUsage.start_time) >= start_date)
    if end_date is not None:
        top_apps_stmt = top_apps_stmt.where(func.date(AppUsage.start_time) <= end_date)

    app_rows = (await db.execute(top_apps_stmt)).all()
    top_apps = [
        {"name": r.process_name, "total_hours": round((r.total_secs or 0) / 3600, 2), "session_count": r.session_count}
        for r in app_rows
    ]

    # Top domains (org-wide)
    top_domains_stmt = (
        select(
            UrlVisit.domain,
            func.coalesce(func.sum(UrlVisit.duration_sec), 0).label("total_secs"),
            func.count(UrlVisit.id).label("visit_count"),
        )
        .where(UrlVisit.user_id.in_(org_user_ids))
        .group_by(UrlVisit.domain)
        .order_by(func.sum(UrlVisit.duration_sec).desc().nulls_last())
        .limit(10)
    )
    if start_date is not None:
        top_domains_stmt = top_domains_stmt.where(func.date(UrlVisit.visit_time) >= start_date)
    if end_date is not None:
        top_domains_stmt = top_domains_stmt.where(func.date(UrlVisit.visit_time) <= end_date)

    domain_rows = (await db.execute(top_domains_stmt)).all()
    top_domains = [
        {"domain": r.domain, "total_hours": round((r.total_secs or 0) / 3600, 2), "visit_count": r.visit_count}
        for r in domain_rows
    ]

    period_start = start_date.isoformat() if start_date else "all"
    period_end = end_date.isoformat() if end_date else "now"

    return {
        "period": f"{period_start} to {period_end}",
        "total_users": total_users,
        "active_today": active_today,
        "avg_active_hours": avg_active_hours,
        "avg_idle_ratio": avg_idle_ratio,
        "top_apps": top_apps,
        "top_domains": top_domains,
    }


async def get_user_dashboard(
    user_id: uuid.UUID,
    start_date: date | None,
    end_date: date | None,
    db: AsyncSession,
) -> dict:
    """Compute individual user dashboard data.

    Args:
        user_id: User UUID.
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.
        db: Async database session.

    Returns:
        Dict matching UserDashboardResponse fields.

    Raises:
        HTTPException: If the user is not found (via service layer).
    """
    # Get user info
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Active hours
    active_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(
        ActivitySession.user_id == user_id,
        ActivitySession.session_type == "active",
    )
    idle_stmt = select(
        func.coalesce(func.sum(ActivitySession.duration_sec), 0)
    ).where(
        ActivitySession.user_id == user_id,
        ActivitySession.session_type.in_(["idle", "away", "locked"]),
    )

    # Use func.date() so stored timestamps with timezone suffix get reduced
    # to a DATE before comparison. Pass date objects (not isoformat strings)
    # so the right-hand bind is DATE — PostgreSQL has no `date >= varchar`.
    if start_date is not None:
        active_stmt = active_stmt.where(func.date(ActivitySession.start_time) >= start_date)
        idle_stmt = idle_stmt.where(func.date(ActivitySession.start_time) >= start_date)
    if end_date is not None:
        active_stmt = active_stmt.where(func.date(ActivitySession.start_time) <= end_date)
        idle_stmt = idle_stmt.where(func.date(ActivitySession.start_time) <= end_date)

    active_secs = (await db.execute(active_stmt)).scalar() or 0
    idle_secs = (await db.execute(idle_stmt)).scalar() or 0
    total_secs = active_secs + idle_secs

    total_active_hours = round(active_secs / 3600, 2)
    total_idle_hours = round(idle_secs / 3600, 2)
    productivity_score = round((active_secs / max(total_secs, 1)) * 100, 1)

    top_apps = await app_service.get_top_apps(user_id, start_date, end_date, 10, db)
    top_domains = await url_service.get_top_domains(user_id, start_date, end_date, 10, db)

    # First and last activity timestamps (login/logout approximation)
    # Use only "active" sessions so away/idle periods from sleep/wake
    # don't register as a false early login time.
    first_activity_stmt = select(func.min(ActivitySession.start_time)).where(
        ActivitySession.user_id == user_id,
        ActivitySession.session_type == "active",
    )
    last_activity_stmt = select(func.max(ActivitySession.end_time)).where(
        ActivitySession.user_id == user_id,
        ActivitySession.session_type == "active",
    )
    if start_date is not None:
        first_activity_stmt = first_activity_stmt.where(func.date(ActivitySession.start_time) >= start_date)
        last_activity_stmt = last_activity_stmt.where(func.date(ActivitySession.start_time) >= start_date)
    if end_date is not None:
        first_activity_stmt = first_activity_stmt.where(func.date(ActivitySession.start_time) <= end_date)
        last_activity_stmt = last_activity_stmt.where(func.date(ActivitySession.start_time) <= end_date)

    first_activity = (await db.execute(first_activity_stmt)).scalar()
    last_activity = (await db.execute(last_activity_stmt)).scalar()

    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "total_active_hours": total_active_hours,
        "total_idle_hours": total_idle_hours,
        "top_apps": top_apps,
        "top_domains": top_domains,
        "productivity_score": productivity_score,
        "first_activity": str(first_activity) if first_activity else None,
        "last_activity": str(last_activity) if last_activity else None,
    }


async def get_trends(
    org_id: uuid.UUID,
    start_date: date | None,
    end_date: date | None,
    db: AsyncSession,
) -> dict:
    """Compute time-series trend data for dashboard charts.

    Produces daily active hours, idle hours, and productivity scores
    for the requested date range.

    Args:
        org_id: Organization UUID.
        start_date: Inclusive start date (defaults to 30 days ago).
        end_date: Inclusive end date (defaults to today).
        db: Async database session.

    Returns:
        Dict matching TrendsResponse fields.
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    org_user_ids_stmt = select(User.id).where(User.org_id == org_id)
    org_user_ids = (await db.execute(org_user_ids_stmt)).scalars().all()

    dates_list: list[date] = []
    active_hours_list: list[float] = []
    idle_hours_list: list[float] = []
    productivity_scores_list: list[float] = []

    current = start_date
    while current <= end_date:
        dates_list.append(current)

        if not org_user_ids:
            active_hours_list.append(0.0)
            idle_hours_list.append(0.0)
            productivity_scores_list.append(0.0)
            current += timedelta(days=1)
            continue

        day_start = datetime.combine(current, time.min, tzinfo=timezone.utc)
        day_end = datetime.combine(current, time.max, tzinfo=timezone.utc)

        active_stmt = select(
            func.coalesce(func.sum(ActivitySession.duration_sec), 0)
        ).where(
            ActivitySession.user_id.in_(org_user_ids),
            ActivitySession.session_type == "active",
            ActivitySession.start_time >= day_start,
            ActivitySession.start_time <= day_end,
        )
        idle_stmt = select(
            func.coalesce(func.sum(ActivitySession.duration_sec), 0)
        ).where(
            ActivitySession.user_id.in_(org_user_ids),
            ActivitySession.session_type.in_(["idle", "away", "locked"]),
            ActivitySession.start_time >= day_start,
            ActivitySession.start_time <= day_end,
        )

        active_secs = (await db.execute(active_stmt)).scalar() or 0
        idle_secs = (await db.execute(idle_stmt)).scalar() or 0
        total_secs = active_secs + idle_secs

        active_hours_list.append(round(active_secs / 3600, 2))
        idle_hours_list.append(round(idle_secs / 3600, 2))
        score = round((active_secs / max(total_secs, 1)) * 100, 1)
        productivity_scores_list.append(score)

        current += timedelta(days=1)

    return {
        "dates": dates_list,
        "active_hours": active_hours_list,
        "idle_hours": idle_hours_list,
        "productivity_scores": productivity_scores_list,
    }


