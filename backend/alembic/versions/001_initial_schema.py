"""Initial schema - all tables and indexes.

Revision ID: 001
Revises: None
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables, indexes, and constraints for the initial schema."""
    # Organizations table
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("settings", postgresql.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'manager', 'viewer')",
            name="ck_users_role",
        ),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # Devices table
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os_version", sa.String(100), nullable=False),
        sa.Column("agent_version", sa.String(20), nullable=False),
        sa.Column("api_key_hash", sa.String(255), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="Enrolled desktop agent devices",
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])
    op.create_index("ix_devices_org_id", "devices", ["org_id"])

    # Activity sessions table
    op.create_table(
        "activity_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_type", sa.String(20), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "duration_sec",
            sa.Integer(),
            sa.Computed("EXTRACT(EPOCH FROM (end_time - start_time))::INTEGER"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "session_type IN ('active', 'idle', 'away', 'locked')",
            name="ck_activity_sessions_type",
        ),
    )
    op.create_index("ix_activity_sessions_device_id", "activity_sessions", ["device_id"])
    op.create_index("ix_activity_sessions_user_id", "activity_sessions", ["user_id"])
    op.create_index(
        "ix_activity_sessions_user_start",
        "activity_sessions",
        ["user_id", "start_time"],
    )
    op.create_index(
        "ix_activity_sessions_device_start",
        "activity_sessions",
        ["device_id", "start_time"],
    )

    # App usage table
    op.create_table(
        "app_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("process_name", sa.String(255), nullable=False),
        sa.Column("window_title", sa.String(1024), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
    )
    op.create_index("ix_app_usage_device_id", "app_usage", ["device_id"])
    op.create_index("ix_app_usage_user_id", "app_usage", ["user_id"])
    op.create_index("ix_app_usage_user_start", "app_usage", ["user_id", "start_time"])
    op.create_index(
        "ix_app_usage_device_start", "app_usage", ["device_id", "start_time"]
    )
    op.create_index("ix_app_usage_process", "app_usage", ["process_name"])

    # URL visits table
    op.create_table(
        "url_visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("browser", sa.String(100), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("page_title", sa.String(1024), nullable=False),
        sa.Column("visit_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
    )
    op.create_index("ix_url_visits_device_id", "url_visits", ["device_id"])
    op.create_index("ix_url_visits_user_id", "url_visits", ["user_id"])
    op.create_index(
        "ix_url_visits_user_visit", "url_visits", ["user_id", "visit_time"]
    )
    op.create_index(
        "ix_url_visits_device_visit", "url_visits", ["device_id", "visit_time"]
    )
    op.create_index("ix_url_visits_domain", "url_visits", ["domain"])

    # Screenshots table
    op.create_table(
        "screenshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
    )
    op.create_index("ix_screenshots_device_id", "screenshots", ["device_id"])
    op.create_index("ix_screenshots_user_id", "screenshots", ["user_id"])
    op.create_index(
        "ix_screenshots_user_captured", "screenshots", ["user_id", "captured_at"]
    )
    op.create_index(
        "ix_screenshots_device_captured", "screenshots", ["device_id", "captured_at"]
    )

    # Dead letter queue table
    op.create_table(
        "dead_letter_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_retried_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dead_letter_queue_event_type", "dead_letter_queue", ["event_type"])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("dead_letter_queue")
    op.drop_table("screenshots")
    op.drop_table("url_visits")
    op.drop_table("app_usage")
    op.drop_table("activity_sessions")
    op.drop_table("devices")
    op.drop_table("users")
    op.drop_table("organizations")
