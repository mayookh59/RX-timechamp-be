"""Device model for tracking enrolled desktop agents.

Each device represents a desktop agent installation that reports
activity data back to the TrackMe platform.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


class Device(Base):
    """Device entity representing an enrolled desktop agent.

    Attributes:
        id: Unique identifier for the device.
        user_id: Foreign key to the device owner.
        org_id: Foreign key to the organization.
        hostname: Machine hostname for identification.
        os_version: Operating system version string.
        agent_version: Installed agent software version.
        api_key_hash: Hashed API key for device authentication.
        last_heartbeat: Timestamp of last heartbeat received.
        is_active: Whether the device is currently active.
        created_at: Timestamp when the device was enrolled.
    """

    __tablename__ = "devices"
    __table_args__ = (
        {"comment": "Enrolled desktop agent devices"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hostname: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    os_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    api_key_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(  # noqa: F821
        "User",
        back_populates="devices",
    )
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization",
        back_populates="devices",
    )

    def __repr__(self) -> str:
        """Return string representation of the device."""
        return f"<Device(id={self.id}, hostname={self.hostname!r})>"
