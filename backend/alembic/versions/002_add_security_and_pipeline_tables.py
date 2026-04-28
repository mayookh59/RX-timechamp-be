"""Add security, audit, and pipeline aggregation tables.

Revision ID: 002
Revises: 001
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create api_keys, audit_logs, and aggregation tables."""

    # ── API Keys ─────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(10)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rotated_from",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id"),
            nullable=True,
        ),
    )
    op.create_index("idx_api_keys_device_status", "api_keys", ["device_id", "status"])
    op.create_index("idx_api_keys_prefix", "api_keys", ["key_prefix"])

    # ── Audit Logs ───────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("old_value", postgresql.JSONB, nullable=True),
        sa.Column("new_value", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_audit_user_time", "audit_logs", ["user_id", "timestamp"])
    op.create_index("idx_audit_action", "audit_logs", ["action"])
    op.create_index(
        "idx_audit_resource", "audit_logs", ["resource_type", "resource_id"]
    )

    # ── Daily User Summary ───────────────────────────────────────────
    op.create_table(
        "daily_user_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("active_hours", sa.Float, nullable=False, server_default="0"),
        sa.Column("idle_hours", sa.Float, nullable=False, server_default="0"),
        sa.Column("top_app", sa.String(255), nullable=True),
        sa.Column("top_domain", sa.String(255), nullable=True),
        sa.Column("productivity_score", sa.Float, nullable=True),
        sa.Column("total_sessions", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "date", name="uq_daily_user_date"),
    )
    op.create_index(
        "idx_daily_summary_user_date",
        "daily_user_summaries",
        ["user_id", "date"],
    )

    # ── Weekly Team Summary ──────────────────────────────────────────
    op.create_table(
        "weekly_team_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("avg_productivity", sa.Float, nullable=True),
        sa.Column("min_productivity", sa.Float, nullable=True),
        sa.Column("max_productivity", sa.Float, nullable=True),
        sa.Column("total_active_hours", sa.Float, nullable=False, server_default="0"),
        sa.Column("member_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("participation_rate", sa.Float, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "org_id", "team_id", "week_start", name="uq_weekly_team_week"
        ),
    )

    # ── Monthly Org Summary ──────────────────────────────────────────
    op.create_table(
        "monthly_org_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month", sa.Date, nullable=False),
        sa.Column("total_users", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active_users", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_productivity", sa.Float, nullable=True),
        sa.Column("total_active_hours", sa.Float, nullable=False, server_default="0"),
        sa.Column("top_apps", postgresql.JSONB, nullable=True),
        sa.Column("top_domains", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("org_id", "month", name="uq_monthly_org_month"),
    )

    # ── Add consent columns to users table ───────────────────────────
    op.add_column(
        "users",
        sa.Column("consent_level", sa.String(20), nullable=True, server_default="full"),
    )
    op.add_column(
        "users",
        sa.Column("consent_granted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop all Phase 10-12 tables."""
    op.drop_column("users", "consent_granted_at")
    op.drop_column("users", "consent_level")
    op.drop_table("monthly_org_summaries")
    op.drop_table("weekly_team_summaries")
    op.drop_table("daily_user_summaries")
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
