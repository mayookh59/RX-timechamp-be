"""Aggregation models for pre-computed analytics summaries.

Stores daily, weekly, and monthly rollups of user activity,
team productivity, and organization-level trends to accelerate
dashboard queries and reduce load on raw activity tables.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from app.storage.types import GUID
from sqlalchemy.orm import relationship

from app.storage.database import Base


class DailyUserSummary(Base):
    """Per-user daily activity summary.

    Aggregates a single user's activity data for one calendar day,
    including active/idle hours, top application and domain usage,
    and a computed productivity score.
    """

    __tablename__ = "daily_user_summaries"

    id: uuid.UUID = Column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: uuid.UUID = Column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: date = Column(Date, nullable=False, index=True)

    # Activity hours
    active_hours: float = Column(Float, nullable=False, default=0.0)
    idle_hours: float = Column(Float, nullable=False, default=0.0)
    total_tracked_hours: float = Column(Float, nullable=False, default=0.0)

    # Top usage
    top_app: str = Column(String(255), nullable=True)
    top_app_minutes: float = Column(Float, nullable=True, default=0.0)
    top_domain: str = Column(String(255), nullable=True)
    top_domain_minutes: float = Column(Float, nullable=True, default=0.0)

    # Productivity
    productivity_score: float = Column(Float, nullable=False, default=0.0)
    productive_minutes: float = Column(Float, nullable=False, default=0.0)
    unproductive_minutes: float = Column(Float, nullable=False, default=0.0)
    neutral_minutes: float = Column(Float, nullable=False, default=0.0)

    # Screenshot stats
    screenshot_count: int = Column(Integer, nullable=False, default=0)

    # Metadata
    created_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    user = relationship("User", backref="daily_summaries")

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_daily_user_date"),
        Index("ix_daily_user_summaries_user_date", "user_id", "date"),
    )

    def __repr__(self) -> str:
        return (
            f"<DailyUserSummary(user_id={self.user_id}, date={self.date}, "
            f"score={self.productivity_score})>"
        )


class WeeklyTeamSummary(Base):
    """Per-team weekly productivity summary.

    Rolls up daily user summaries into a team-level weekly view,
    providing average productivity, total active hours, and
    participation metrics.
    """

    __tablename__ = "weekly_team_summaries"

    id: uuid.UUID = Column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    org_id: uuid.UUID = Column(
        GUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    team_id: str = Column(String(255), nullable=True, index=True)
    week_start: date = Column(Date, nullable=False, index=True)

    # Productivity metrics
    avg_productivity: float = Column(Float, nullable=False, default=0.0)
    min_productivity: float = Column(Float, nullable=False, default=0.0)
    max_productivity: float = Column(Float, nullable=False, default=0.0)

    # Hours
    total_active_hours: float = Column(Float, nullable=False, default=0.0)
    total_idle_hours: float = Column(Float, nullable=False, default=0.0)
    avg_active_hours_per_user: float = Column(
        Float, nullable=False, default=0.0
    )

    # Participation
    total_members: int = Column(Integer, nullable=False, default=0)
    active_members: int = Column(Integer, nullable=False, default=0)
    participation_rate: float = Column(Float, nullable=False, default=0.0)

    # Top usage across team
    top_app: str = Column(String(255), nullable=True)
    top_domain: str = Column(String(255), nullable=True)

    # Metadata
    created_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    organization = relationship("Organization", backref="weekly_summaries")

    __table_args__ = (
        UniqueConstraint("team_id", "week_start", name="uq_weekly_team_week"),
        Index("ix_weekly_team_summaries_team_week", "team_id", "week_start"),
    )

    def __repr__(self) -> str:
        return (
            f"<WeeklyTeamSummary(team_id={self.team_id}, "
            f"week_start={self.week_start}, "
            f"avg_productivity={self.avg_productivity})>"
        )


class MonthlyOrgSummary(Base):
    """Per-organization monthly trend summary.

    Provides organization-wide monthly rollups for executive
    dashboards and long-term trend analysis.
    """

    __tablename__ = "monthly_org_summaries"

    id: uuid.UUID = Column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    org_id: uuid.UUID = Column(
        GUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    month: date = Column(Date, nullable=False, index=True)

    # User metrics
    total_users: int = Column(Integer, nullable=False, default=0)
    active_users: int = Column(Integer, nullable=False, default=0)
    new_users: int = Column(Integer, nullable=False, default=0)

    # Productivity
    avg_productivity: float = Column(Float, nullable=False, default=0.0)
    productivity_trend: float = Column(
        Float, nullable=True
    )  # Change from previous month

    # Hours
    total_active_hours: float = Column(Float, nullable=False, default=0.0)
    total_idle_hours: float = Column(Float, nullable=False, default=0.0)
    avg_daily_active_hours: float = Column(
        Float, nullable=False, default=0.0
    )

    # Top usage across org
    top_apps: str = Column(
        String(1024), nullable=True
    )  # JSON array of top 5 apps
    top_domains: str = Column(
        String(1024), nullable=True
    )  # JSON array of top 5 domains

    # Screenshot stats
    total_screenshots: int = Column(Integer, nullable=False, default=0)
    screenshots_archived: int = Column(Integer, nullable=False, default=0)

    # Team count
    total_teams: int = Column(Integer, nullable=False, default=0)

    # Metadata
    created_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: datetime = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    organization = relationship("Organization", backref="monthly_summaries")

    __table_args__ = (
        UniqueConstraint("org_id", "month", name="uq_monthly_org_month"),
        Index("ix_monthly_org_summaries_org_month", "org_id", "month"),
    )

    def __repr__(self) -> str:
        return (
            f"<MonthlyOrgSummary(org_id={self.org_id}, month={self.month}, "
            f"total_users={self.total_users}, "
            f"avg_productivity={self.avg_productivity})>"
        )
