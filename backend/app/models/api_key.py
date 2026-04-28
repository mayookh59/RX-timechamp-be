"""API key model for agent authentication and key rotation.

Supports zero-downtime key rotation with a grace period where both
old and new keys are valid, allowing agents to transition seamlessly.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class ApiKey(Base):
    """API key entity for device authentication.

    Stores hashed API keys with support for rotation. During rotation,
    both the old and new keys remain valid for a grace period.

    Attributes:
        id: Unique identifier for the API key record.
        device_id: Foreign key to the device this key authenticates.
        key_hash: Bcrypt hash of the API key string.
        key_prefix: First 10 characters of the key for display/lookup.
        status: Key status - one of 'active', 'rotating', 'revoked'.
        created_at: When the key was created.
        expires_at: Optional expiration timestamp.
        last_used_at: When the key was last used for authentication.
        rotated_from: UUID of the previous key this one replaced.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'rotating', 'revoked')",
            name="ck_api_keys_status",
        ),
        Index("ix_api_keys_device_status", "device_id", "status"),
        Index("ix_api_keys_prefix", "key_prefix"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    key_prefix: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rotated_from: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return string representation of the API key."""
        return (
            f"<ApiKey(id={self.id}, device_id={self.device_id}, "
            f"prefix={self.key_prefix!r}, status={self.status!r})>"
        )
