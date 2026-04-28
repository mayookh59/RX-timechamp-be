"""Audit log model for tracking security-relevant user actions.

Records create, read, update, and delete operations on sensitive
resources with before/after values for full audit trail compliance.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class AuditLog(Base):
    """Audit log entry for security and compliance tracking.

    Captures who did what, when, where, and the before/after state
    of the affected resource.

    Attributes:
        id: Unique identifier for the audit log entry.
        user_id: UUID of the user who performed the action.
        action: Action performed (e.g., LOGIN, DELETE_SCREENSHOT).
        resource_type: Type of resource affected (e.g., screenshot, user).
        resource_id: Identifier of the affected resource.
        old_value: JSON snapshot of the resource state before the action.
        new_value: JSON snapshot of the resource state after the action.
        ip_address: Client IP address at the time of the action.
        user_agent: Client User-Agent header string.
        timestamp: When the action was performed.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_timestamp", "user_id", "timestamp"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    old_value: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )
    new_value: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return string representation of the audit log entry."""
        return (
            f"<AuditLog(id={self.id}, user_id={self.user_id}, "
            f"action={self.action!r}, resource={self.resource_type!r})>"
        )
