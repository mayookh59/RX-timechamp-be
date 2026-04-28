"""Pydantic schemas for authentication request and response payloads.

Defines data validation models for login, token refresh, and user
information endpoints.
"""

import uuid

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Schema for user login credentials.

    Attributes:
        email: User's registered email address.
        password: User's plaintext password for verification.
    """

    email: EmailStr = Field(
        ...,
        description="Registered email address",
        examples=["user@company.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="User password",
    )


class TokenResponse(BaseModel):
    """Schema for JWT token pair response.

    Attributes:
        access_token: Short-lived JWT for API access.
        refresh_token: Long-lived token for obtaining new access tokens.
        token_type: Bearer token type identifier.
    """

    access_token: str = Field(
        ...,
        description="JWT access token",
    )
    refresh_token: str = Field(
        ...,
        description="JWT refresh token",
    )
    token_type: str = Field(
        default="bearer",
        description="Token type (always 'bearer')",
    )


class RefreshRequest(BaseModel):
    """Schema for token refresh request.

    Attributes:
        refresh_token: The refresh token to exchange for a new access token.
    """

    refresh_token: str = Field(
        ...,
        description="Valid refresh token",
    )


class UserResponse(BaseModel):
    """Schema for user information response.

    Attributes:
        id: Unique user identifier.
        email: User's email address.
        full_name: User's display name.
        role: User's access role (admin, manager, viewer).
        org_id: Organization the user belongs to.
    """

    id: uuid.UUID = Field(
        ...,
        description="User unique identifier",
    )
    email: str = Field(
        ...,
        description="User email address",
    )
    full_name: str = Field(
        ...,
        description="User display name",
    )
    role: str = Field(
        ...,
        description="User role (admin, manager, viewer)",
    )
    org_id: uuid.UUID = Field(
        ...,
        description="Organization identifier",
    )

    model_config = {"from_attributes": True}
