"""Dashboard schemas for analytics response serialization.

Provides organization-level overview, individual user dashboard,
and trend data for charting.
"""

import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.schemas.apps import TopAppsResponse
from app.schemas.urls import TopDomainsResponse


class DashboardOverviewResponse(BaseModel):
    """Organization-wide dashboard overview.

    Attributes:
        period: Human-readable period description.
        total_users: Total users in the organization.
        active_today: Users with activity today.
        avg_active_hours: Average active hours per user.
        avg_idle_ratio: Average idle ratio across all users.
        top_apps: Top applications by total usage.
        top_domains: Top domains by total visits.
    """

    period: str = Field(..., description="Human-readable period")
    total_users: int = Field(..., ge=0, description="Total org users")
    active_today: int = Field(..., ge=0, description="Users active today")
    avg_active_hours: float = Field(..., ge=0, description="Avg active hours per user")
    avg_idle_ratio: float = Field(..., ge=0, le=1, description="Avg idle ratio")
    top_apps: list[TopAppsResponse] = Field(default_factory=list)
    top_domains: list[TopDomainsResponse] = Field(default_factory=list)


class UserDashboardResponse(BaseModel):
    """Individual user dashboard data.

    Attributes:
        user_id: User UUID.
        full_name: User display name.
        total_active_hours: Total active hours in the period.
        total_idle_hours: Total idle hours in the period.
        top_apps: Top applications used by this user.
        top_domains: Top domains visited by this user.
        productivity_score: Computed productivity score (0-100).
    """

    user_id: uuid.UUID
    full_name: str
    total_active_hours: float = Field(..., ge=0)
    total_idle_hours: float = Field(..., ge=0)
    top_apps: list[TopAppsResponse] = Field(default_factory=list)
    top_domains: list[TopDomainsResponse] = Field(default_factory=list)
    productivity_score: float = Field(..., ge=0, le=100, description="Productivity score 0-100")
    first_activity: str | None = Field(None, description="First activity timestamp (login approximation)")
    last_activity: str | None = Field(None, description="Last activity timestamp (logout approximation)")


class TrendsResponse(BaseModel):
    """Time-series trend data for dashboard charts.

    Attributes:
        dates: List of dates in the series.
        active_hours: Active hours per date.
        idle_hours: Idle hours per date.
        productivity_scores: Productivity scores per date.
    """

    dates: list[date] = Field(..., description="Dates in the series")
    active_hours: list[float] = Field(..., description="Active hours per date")
    idle_hours: list[float] = Field(..., description="Idle hours per date")
    productivity_scores: list[float] = Field(..., description="Scores per date")
