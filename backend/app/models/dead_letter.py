"""Dead letter queue model for failed event processing.

Stores events that failed to process after all retry attempts,
allowing manual inspection and reprocessing.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from app.storage.types import GUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class DeadLetterQueue(Base):
    """Dead letter queue entry for failed event processing.

    Attributes:
        id: Unique identifier for the dead letter entry.
        event_type: Type of the event that failed processing.
        payload: Original event payload as JSON.
        error_message: Description of the failure.
        retry_count: Number of processing attempts made.
        created_at: When the event was added to the dead letter queue.
        last_retried_at: When the last retry attempt occurred.
    """

    __tablename__ = "dead_letter_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )
    error_message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_retried_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return string representation of the dead letter entry."""
        return (
            f"<DeadLetterQueue(id={self.id}, event_type={self.event_type!r}, "
            f"retries={self.retry_count})>"
        )
