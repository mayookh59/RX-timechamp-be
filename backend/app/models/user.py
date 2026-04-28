"""User model for authentication and authorization.

Users belong to an organization and have role-based access control
with roles: admin, manager, and viewer.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


class User(Base):
    """User entity with role-based access control.

    Attributes:
        id: Unique identifier for the user.
        org_id: Foreign key to the user's organization.
        email: Unique email address used for authentication.
        password_hash: Bcrypt-hashed password.
        full_name: User's display name.
        role: Access level - one of 'admin', 'manager', 'viewer'.
        is_active: Whether the user account is enabled.
        created_at: Timestamp when the user was created.
        updated_at: Timestamp of the last update.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'manager', 'viewer')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="viewer",
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization",
        back_populates="users",
    )
    devices: Mapped[list["Device"]] = relationship(  # noqa: F821
        "Device",
        back_populates="user",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return string representation of the user."""
        return f"<User(id={self.id}, email={self.email!r}, role={self.role!r})>"
