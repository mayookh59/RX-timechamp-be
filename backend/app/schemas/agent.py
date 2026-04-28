"""Pydantic schemas for agent registration and heartbeat payloads.

Defines data validation models for desktop agent communication
with the backend API.
"""

import uuid
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class AgentRegisterRequest(BaseModel):
    """Schema for new agent device registration.

    Attributes:
        hostname: Machine hostname for identification.
        os_version: Operating system version string.
        agent_version: Installed agent software version.
        user_email: Email of the user this device belongs to.
    """

    hostname: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Machine hostname",
        examples=["DESKTOP-ABC123"],
    )
    os_version: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Operating system version",
        examples=["Windows 11 Pro 10.0.22631"],
    )
    agent_version: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Agent software version",
        examples=["1.0.0"],
    )
    user_email: EmailStr = Field(
        ...,
        description="Email of the device owner",
        examples=["user@company.com"],
    )


class AgentRegisterResponse(BaseModel):
    """Schema for agent registration response.

    Attributes:
        device_id: Unique identifier assigned to the registered device.
        api_key: API key for authenticating future requests from this device.
    """

    device_id: uuid.UUID = Field(
        ...,
        description="Assigned device identifier",
    )
    api_key: str = Field(
        ...,
        description="API key for device authentication",
    )


class HeartbeatRequest(BaseModel):
    """Schema for agent heartbeat signal.

    Attributes:
        device_id: The registered device identifier.
        agent_version: Current installed agent version.
        cpu_usage: Current CPU usage percentage (0-100).
        ram_usage: Current RAM usage percentage (0-100).
        queue_depth: Number of items pending in the offline sync queue.
    """

    device_id: uuid.UUID = Field(
        ...,
        description="Registered device identifier",
    )
    agent_version: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Current agent version",
        examples=["1.0.0"],
    )
    cpu_usage: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="CPU usage percentage",
    )
    ram_usage: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="RAM usage percentage",
    )
    queue_depth: int = Field(
        default=0,
        ge=0,
        description="Offline queue depth",
    )
    log_tail: str = Field(
        default="",
        max_length=20000,
        description="Optional last few KB of agent.log for remote diagnostics",
    )


class HeartbeatResponse(BaseModel):
    """Schema for heartbeat response with optional configuration updates.

    Attributes:
        config_update: Optional configuration changes to apply on the agent.
        force_upgrade_required: Whether the agent must upgrade immediately.
        force_upgrade_url: Download URL for the required upgrade, if any.
    """

    config_update: dict[str, Any] | None = Field(
        default=None,
        description="Optional agent configuration update",
    )
    force_upgrade_required: bool = Field(
        default=False,
        description="Whether a forced upgrade is required",
    )
    force_upgrade_url: str | None = Field(
        default=None,
        description="URL to download the required agent upgrade",
    )
