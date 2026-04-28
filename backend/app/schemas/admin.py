"""Admin schemas for user management and alert operations.

Provides schemas for creating/updating users and viewing system alerts.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserCreateRequest(BaseModel):
    """Request to create a new user (admin only).

    Attributes:
        email: Unique email address for authentication.
        password: Plain-text password (will be hashed server-side).
        full_name: User display name.
        role: Access level (admin, manager, viewer).
        org_id: Organization UUID the user belongs to.
    """

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, max_length=128, description="Plain-text password")
    full_name: str = Field(..., min_length=1, max_length=255, description="Display name")
    role: str = Field("viewer", description="User role: admin, manager, viewer")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """Ensure role is one of the allowed values."""
        allowed = {"admin", "manager", "viewer"}
        if v not in allowed:
            msg = f"role must be one of {allowed}"
            raise ValueError(msg)
        return v


class UserUpdateRequest(BaseModel):
    """Request to update an existing user (admin only).

    All fields are optional; only provided fields will be updated.

    Attributes:
        full_name: New display name.
        role: New access level.
        is_active: Whether the account is enabled.
    """

    full_name: str | None = Field(None, min_length=1, max_length=255, description="Display name")
    role: str | None = Field(None, description="User role: admin, manager, viewer")
    is_active: bool | None = Field(None, description="Account enabled state")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        """Ensure role is one of the allowed values when provided."""
        if v is not None:
            allowed = {"admin", "manager", "viewer"}
            if v not in allowed:
                msg = f"role must be one of {allowed}"
                raise ValueError(msg)
        return v


class UserResponse(BaseModel):
    """User response for admin listing.

    Attributes:
        id: User UUID.
        email: Email address.
        full_name: Display name.
        role: Access level.
        is_active: Account enabled state.
        org_id: Organization UUID.
        created_at: Account creation timestamp.
        updated_at: Last update timestamp.
    """

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentStatusResponse(BaseModel):
    """Agent/device status response for admin listing.

    Attributes:
        id: Device UUID.
        hostname: Machine hostname.
        user_name: Owner's display name.
        status: online if heartbeat < 5 min ago, offline otherwise.
        cpu_usage: Latest CPU usage percentage (stub).
        memory_usage: Latest memory usage percentage (stub).
        last_seen: Last heartbeat timestamp.
        agent_version: Installed agent version.
        os_version: Operating system version.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    hostname: str
    user_name: str
    status: str = Field(..., description="online or offline")
    cpu_usage: float | None = Field(None, description="CPU usage %")
    memory_usage: float | None = Field(None, description="Memory usage %")
    last_seen: datetime | None = Field(None, description="Last heartbeat")
    agent_version: str | None = None
    os_version: str | None = None

    model_config = {"from_attributes": True}


class AlertUpdateRequest(BaseModel):
    """Request to update an alert's status.

    Attributes:
        status: New alert status (acknowledged, resolved, dismissed).
    """

    status: str = Field(..., description="New status: acknowledged, resolved, dismissed")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Ensure status is one of the allowed values."""
        allowed = {"acknowledged", "resolved", "dismissed"}
        if v not in allowed:
            msg = f"status must be one of {allowed}"
            raise ValueError(msg)
        return v


class AlertResponse(BaseModel):
    """System alert response.

    Attributes:
        id: Alert UUID.
        type: Alert type (e.g. inactivity, anomaly, threshold).
        message: Human-readable alert message.
        severity: Alert severity (info, warning, critical).
        created_at: When the alert was generated.
    """

    id: uuid.UUID
    type: str = Field(..., description="Alert type")
    message: str = Field(..., description="Alert message")
    severity: str = Field(..., description="Severity: info, warning, critical")
    created_at: datetime
