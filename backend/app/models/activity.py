"""Activity tracking models for employee monitoring data.

Contains models for activity sessions, application usage,
URL visits, and screenshots. Each model supports client-side
idempotency via the client_id field.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class ActivitySession(Base):
    """Activity session tracking user presence state.

    Records periods of active, idle, away, or locked states
    as reported by the desktop agent.

    Attributes:
        id: Unique identifier for the session record.
        client_id: Client-generated UUID for idempotent ingestion.
        device_id: Foreign key to the reporting device.
        user_id: Foreign key to the session owner.
        session_type: Type of session (active, idle, away, locked).
        start_time: When the session began.
        end_time: When the session ended.
        duration_sec: Computed duration in seconds.
    """

    __tablename__ = "activity_sessions"
    __table_args__ = (
        CheckConstraint(
            "session_type IN ('active', 'idle', 'away', 'locked')",
            name="ck_activity_sessions_type",
        ),
        Index("ix_activity_sessions_user_start", "user_id", "start_time"),
        Index("ix_activity_sessions_device_start", "device_id", "start_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        unique=True,
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_sec: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return string representation of the activity session."""
        return (
            f"<ActivitySession(id={self.id}, type={self.session_type!r}, "
            f"duration={self.duration_sec}s)>"
        )


class AppUsage(Base):
    """Application usage tracking for desktop applications.

    Records which applications were in focus and for how long.

    Attributes:
        id: Unique identifier for the app usage record.
        client_id: Client-generated UUID for idempotent ingestion.
        device_id: Foreign key to the reporting device.
        user_id: Foreign key to the user.
        process_name: Executable process name.
        window_title: Active window title text.
        start_time: When the application became active.
        end_time: When the application lost focus.
        duration_sec: Usage duration in seconds.
    """

    __tablename__ = "app_usage"
    __table_args__ = (
        Index("ix_app_usage_user_start", "user_id", "start_time"),
        Index("ix_app_usage_device_start", "device_id", "start_time"),
        Index("ix_app_usage_process", "process_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        unique=True,
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    process_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    window_title: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_sec: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return string representation of the app usage record."""
        return (
            f"<AppUsage(id={self.id}, process={self.process_name!r}, "
            f"duration={self.duration_sec}s)>"
        )


class UrlVisit(Base):
    """URL visit tracking for browser activity monitoring.

    Records URLs visited by the user with browser context.

    Attributes:
        id: Unique identifier for the URL visit record.
        client_id: Client-generated UUID for idempotent ingestion.
        device_id: Foreign key to the reporting device.
        user_id: Foreign key to the user.
        browser: Browser application name.
        url: Full URL visited.
        domain: Extracted domain from the URL.
        page_title: Page title at time of visit.
        visit_time: When the URL was visited.
        duration_sec: Time spent on the page in seconds.
    """

    __tablename__ = "url_visits"
    __table_args__ = (
        Index("ix_url_visits_user_visit", "user_id", "visit_time"),
        Index("ix_url_visits_device_visit", "device_id", "visit_time"),
        Index("ix_url_visits_domain", "domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        unique=True,
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    browser: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    page_title: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    visit_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    duration_sec: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return string representation of the URL visit."""
        return f"<UrlVisit(id={self.id}, domain={self.domain!r})>"


class Screenshot(Base):
    """Screenshot storage metadata for captured screen images.

    Screenshots are stored in S3; this table holds the metadata
    and storage key for retrieval.

    Attributes:
        id: Unique identifier for the screenshot record.
        client_id: Client-generated UUID for idempotent ingestion.
        device_id: Foreign key to the reporting device.
        user_id: Foreign key to the user.
        storage_key: S3 object key for the screenshot file.
        captured_at: When the screenshot was taken.
        file_size: Size of the screenshot file in bytes.
    """

    __tablename__ = "screenshots"
    __table_args__ = (
        Index("ix_screenshots_user_captured", "user_id", "captured_at"),
        Index("ix_screenshots_device_captured", "device_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        unique=True,
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return string representation of the screenshot."""
        return f"<Screenshot(id={self.id}, captured_at={self.captured_at})>"
