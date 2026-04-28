"""Organization model for multi-tenant isolation.

Each organization represents a company or team using TrackMe.
All users, devices, and activity data are scoped to an organization.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


class Organization(Base):
    """Organization entity for multi-tenant data isolation.

    Attributes:
        id: Unique identifier for the organization.
        name: Display name of the organization.
        settings: JSON blob for organization-level configuration.
        created_at: Timestamp when the organization was created.
        updated_at: Timestamp of the last update.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    settings: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User",
        back_populates="organization",
        lazy="selectin",
    )
    devices: Mapped[list["Device"]] = relationship(  # noqa: F821
        "Device",
        back_populates="organization",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return string representation of the organization."""
        return f"<Organization(id={self.id}, name={self.name!r})>"
