"""Aggregation service for computing periodic activity summaries.

Provides daily, weekly, and monthly aggregation jobs that roll up
raw activity data into pre-computed summary tables for fast
dashboard rendering. Designed to run as async Celery tasks.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.aggregation import (
    DailyUserSummary,
    MonthlyOrgSummary,
    WeeklyTeamSummary,
)

logger = logging.getLogger(__name__)


class AggregationService:
    """Computes and stores periodic activity aggregations.

    All aggregation methods are idempotent: running them multiple
    times for the same period will upsert (replace) existing
    summaries rather than creating duplicates.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the aggregation service.

        Args:
            session: An async SQLAlchemy session for database operations.
        """
        self._session = session

    # -----------------------------------------------------------------
    # Daily aggregation
    # -----------------------------------------------------------------

    async def run_daily_aggregation(
        self,
        target_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Compute per-user daily activity summaries.

        Aggregates all activity records for the given date into a
        single summary row per user, including active/idle hours,
        top application, top domain, and productivity score.

        Args:
            target_date: The date to aggregate. Defaults to yesterday.

        Returns:
            A dictionary with the aggregation results and statistics.
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        logger.info(
            "daily_aggregation_start",
            extra={"target_date": target_date.isoformat()},
        )

        query = text("""
            SELECT
                a.user_id,
                COALESCE(SUM(CASE WHEN a.is_active THEN
                    EXTRACT(EPOCH FROM (a.end_time - a.start_time)) / 3600.0
                ELSE 0 END), 0) AS active_hours,
                COALESCE(SUM(CASE WHEN NOT a.is_active THEN
                    EXTRACT(EPOCH FROM (a.end_time - a.start_time)) / 3600.0
                ELSE 0 END), 0) AS idle_hours,
                (
                    SELECT app_name FROM activities
                    WHERE user_id = a.user_id
                      AND DATE(start_time) = :target_date
                    GROUP BY app_name
                    ORDER BY SUM(EXTRACT(EPOCH FROM (end_time - start_time))) DESC
                    LIMIT 1
                ) AS top_app,
                (
                    SELECT domain FROM activities
                    WHERE user_id = a.user_id
                      AND DATE(start_time) = :target_date
                      AND domain IS NOT NULL
                    GROUP BY domain
                    ORDER BY SUM(EXTRACT(EPOCH FROM (end_time - start_time))) DESC
                    LIMIT 1
                ) AS top_domain,
                COUNT(DISTINCT s.id) AS screenshot_count
            FROM activities a
            LEFT JOIN screenshots s
                ON s.user_id = a.user_id AND DATE(s.captured_at) = :target_date
            WHERE DATE(a.start_time) = :target_date
            GROUP BY a.user_id
        """)

        result = await self._session.execute(
            query, {"target_date": target_date}
        )
        rows = result.fetchall()

        summaries_upserted = 0
        for row in rows:
            total_hours = row.active_hours + row.idle_hours
            productivity_score = (
                (row.active_hours / total_hours * 100.0) if total_hours > 0 else 0.0
            )

            values = {
                "user_id": row.user_id,
                "date": target_date,
                "active_hours": round(row.active_hours, 2),
                "idle_hours": round(row.idle_hours, 2),
                "total_tracked_hours": round(total_hours, 2),
                "top_app": row.top_app,
                "top_domain": row.top_domain,
                "productivity_score": round(productivity_score, 2),
                "screenshot_count": row.screenshot_count,
                "updated_at": datetime.utcnow(),
            }

            stmt = pg_insert(DailyUserSummary).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_daily_user_date",
                set_={k: v for k, v in values.items() if k not in ("user_id", "date")},
            )
            await self._session.execute(stmt)
            summaries_upserted += 1

        await self._session.commit()

        stats = {
            "target_date": target_date.isoformat(),
            "users_processed": len(rows),
            "summaries_upserted": summaries_upserted,
        }

        logger.info("daily_aggregation_complete", extra=stats)
        return stats

    # -----------------------------------------------------------------
    # Weekly aggregation
    # -----------------------------------------------------------------

    async def run_weekly_aggregation(
        self,
        week_start: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Compute per-team weekly productivity summaries.

        Rolls up daily user summaries into team-level weekly metrics,
        including average productivity, total hours, and participation.

        Args:
            week_start: The Monday of the target week. Defaults to
                the most recently completed week.

        Returns:
            A dictionary with the aggregation results and statistics.
        """
        if week_start is None:
            today = date.today()
            # Most recent Monday before today
            week_start = today - timedelta(days=today.weekday() + 7)

        week_end = week_start + timedelta(days=6)

        logger.info(
            "weekly_aggregation_start",
            extra={
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
            },
        )

        query = text("""
            SELECT
                u.team_id,
                AVG(d.productivity_score) AS avg_productivity,
                MIN(d.productivity_score) AS min_productivity,
                MAX(d.productivity_score) AS max_productivity,
                SUM(d.active_hours) AS total_active_hours,
                SUM(d.idle_hours) AS total_idle_hours,
                COUNT(DISTINCT d.user_id) AS active_members,
                (SELECT COUNT(*) FROM users WHERE team_id = u.team_id) AS total_members
            FROM daily_user_summaries d
            JOIN users u ON u.id = d.user_id
            WHERE d.date BETWEEN :week_start AND :week_end
              AND u.team_id IS NOT NULL
            GROUP BY u.team_id
        """)

        result = await self._session.execute(
            query, {"week_start": week_start, "week_end": week_end}
        )
        rows = result.fetchall()

        summaries_upserted = 0
        for row in rows:
            participation_rate = (
                (row.active_members / row.total_members * 100.0)
                if row.total_members > 0
                else 0.0
            )
            avg_active_per_user = (
                (row.total_active_hours / row.active_members)
                if row.active_members > 0
                else 0.0
            )

            values = {
                "team_id": row.team_id,
                "week_start": week_start,
                "avg_productivity": round(row.avg_productivity, 2),
                "min_productivity": round(row.min_productivity, 2),
                "max_productivity": round(row.max_productivity, 2),
                "total_active_hours": round(row.total_active_hours, 2),
                "total_idle_hours": round(row.total_idle_hours, 2),
                "avg_active_hours_per_user": round(avg_active_per_user, 2),
                "total_members": row.total_members,
                "active_members": row.active_members,
                "participation_rate": round(participation_rate, 2),
                "updated_at": datetime.utcnow(),
            }

            stmt = pg_insert(WeeklyTeamSummary).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_weekly_team_week",
                set_={k: v for k, v in values.items() if k not in ("team_id", "week_start")},
            )
            await self._session.execute(stmt)
            summaries_upserted += 1

        await self._session.commit()

        stats = {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "teams_processed": len(rows),
            "summaries_upserted": summaries_upserted,
        }

        logger.info("weekly_aggregation_complete", extra=stats)
        return stats

    # -----------------------------------------------------------------
    # Monthly aggregation
    # -----------------------------------------------------------------

    async def run_monthly_aggregation(
        self,
        target_month: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Compute per-organization monthly trend summaries.

        Rolls up weekly and daily data into organization-wide monthly
        metrics for executive dashboards and trend analysis.

        Args:
            target_month: The first day of the target month. Defaults
                to the first day of the previous month.

        Returns:
            A dictionary with the aggregation results and statistics.
        """
        if target_month is None:
            today = date.today()
            first_of_this_month = today.replace(day=1)
            target_month = (first_of_this_month - timedelta(days=1)).replace(day=1)

        # Calculate month end
        if target_month.month == 12:
            month_end = target_month.replace(year=target_month.year + 1, month=1) - timedelta(days=1)
        else:
            month_end = target_month.replace(month=target_month.month + 1) - timedelta(days=1)

        logger.info(
            "monthly_aggregation_start",
            extra={
                "target_month": target_month.isoformat(),
                "month_end": month_end.isoformat(),
            },
        )

        query = text("""
            SELECT
                o.id AS org_id,
                COUNT(DISTINCT u.id) AS total_users,
                COUNT(DISTINCT d.user_id) AS active_users,
                COUNT(DISTINCT CASE
                    WHEN u.created_at >= :month_start AND u.created_at <= :month_end
                    THEN u.id
                END) AS new_users,
                AVG(d.productivity_score) AS avg_productivity,
                SUM(d.active_hours) AS total_active_hours,
                SUM(d.idle_hours) AS total_idle_hours,
                SUM(d.screenshot_count) AS total_screenshots,
                COUNT(DISTINCT u.team_id) AS total_teams
            FROM organizations o
            JOIN users u ON u.organization_id = o.id
            LEFT JOIN daily_user_summaries d
                ON d.user_id = u.id
                AND d.date BETWEEN :month_start AND :month_end
            GROUP BY o.id
        """)

        result = await self._session.execute(
            query, {"month_start": target_month, "month_end": month_end}
        )
        rows = result.fetchall()

        summaries_upserted = 0
        for row in rows:
            # Calculate working days in the month (approximate: weekdays)
            working_days = sum(
                1
                for d in range((month_end - target_month).days + 1)
                if (target_month + timedelta(days=d)).weekday() < 5
            )
            avg_daily = (
                (row.total_active_hours / working_days)
                if working_days > 0 and row.total_active_hours
                else 0.0
            )

            values = {
                "org_id": row.org_id,
                "month": target_month,
                "total_users": row.total_users,
                "active_users": row.active_users or 0,
                "new_users": row.new_users or 0,
                "avg_productivity": round(row.avg_productivity or 0.0, 2),
                "total_active_hours": round(row.total_active_hours or 0.0, 2),
                "total_idle_hours": round(row.total_idle_hours or 0.0, 2),
                "avg_daily_active_hours": round(avg_daily, 2),
                "total_screenshots": row.total_screenshots or 0,
                "total_teams": row.total_teams or 0,
                "updated_at": datetime.utcnow(),
            }

            stmt = pg_insert(MonthlyOrgSummary).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_monthly_org_month",
                set_={k: v for k, v in values.items() if k not in ("org_id", "month")},
            )
            await self._session.execute(stmt)
            summaries_upserted += 1

        await self._session.commit()

        stats = {
            "target_month": target_month.isoformat(),
            "month_end": month_end.isoformat(),
            "orgs_processed": len(rows),
            "summaries_upserted": summaries_upserted,
        }

        logger.info("monthly_aggregation_complete", extra=stats)
        return stats
