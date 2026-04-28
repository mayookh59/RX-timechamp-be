"""Authentication middleware providing FastAPI dependencies for JWT and API key auth.

Includes role-based access control and per-client rate limiting for both
agent (API key) and dashboard (JWT) authentication flows.
"""

import time
from collections import defaultdict
from collections.abc import Callable
from typing import Annotated

import structlog
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.user import User
from app.services.auth_service import (
    ACCESS_TOKEN_TYPE,
    decode_token,
    validate_token_type,
    verify_password,
)
from app.storage.database import get_db

logger = structlog.stdlib.get_logger(__name__)

# Rate limiting configuration
AGENT_RATE_LIMIT = 100  # requests per minute per agent
DASHBOARD_RATE_LIMIT = 60  # requests per minute per dashboard user
RATE_WINDOW_SECONDS = 60

# In-memory rate limiting store (key -> list of timestamps)
_rate_limit_store: dict[str, list[float]] = defaultdict(list)

bearer_scheme = HTTPBearer(auto_error=False)


def _check_rate_limit(client_key: str, max_requests: int) -> None:
    """Check and enforce rate limiting for a client.

    Uses a sliding window approach to track request timestamps.
    Prunes expired entries and raises 429 if the limit is exceeded.

    Args:
        client_key: Unique identifier for the client (device_id or user_id).
        max_requests: Maximum allowed requests within the rate window.

    Raises:
        HTTPException: 429 Too Many Requests if the rate limit is exceeded.
    """
    now = time.time()
    window_start = now - RATE_WINDOW_SECONDS

    # Prune expired timestamps
    timestamps = _rate_limit_store[client_key]
    _rate_limit_store[client_key] = [
        ts for ts in timestamps if ts > window_start
    ]

    if len(_rate_limit_store[client_key]) >= max_requests:
        logger.warning(
            "rate_limit_exceeded",
            client_key=client_key,
            max_requests=max_requests,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )

    _rate_limit_store[client_key].append(now)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI dependency that extracts and validates the current user from a JWT.

    Decodes the Bearer token, validates its type, and fetches the
    corresponding user from the database. Enforces dashboard rate limiting.

    Args:
        credentials: HTTP Bearer authorization credentials.
        db: Async database session.

    Returns:
        The authenticated User object.

    Raises:
        HTTPException: 401 if the token is missing, invalid, or the user is inactive.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not validate_token_type(payload, ACCESS_TOKEN_TYPE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await _fetch_active_user(db, user_id)
    _check_rate_limit(f"user:{user_id}", DASHBOARD_RATE_LIMIT)

    return user


async def _fetch_active_user(db: AsyncSession, user_id: str) -> User:
    """Fetch an active user by ID from the database.

    Args:
        db: Async database session.
        user_id: The user's UUID string.

    Returns:
        The active User object.

    Raises:
        HTTPException: 401 if the user is not found or inactive.
    """
    import uuid as uuid_mod
    try:
        uid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    stmt = select(User).where(User.id == uid)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        logger.warning("user_not_found_or_inactive", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_device_id: Annotated[str | None, Header(alias="X-Device-Id")] = None,
    db: AsyncSession = Depends(get_db),
) -> Device:
    """FastAPI dependency that validates agent API key authentication.

    Looks up the device by ID, verifies the API key hash matches,
    and enforces agent rate limiting.

    Args:
        x_api_key: API key from the X-API-Key request header.
        x_device_id: Device ID from the X-Device-Id request header.
        db: Async database session.

    Returns:
        The authenticated Device object.

    Raises:
        HTTPException: 401 if credentials are missing or invalid.
        HTTPException: 429 if the agent rate limit is exceeded.
    """
    if not x_api_key or not x_device_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key or device ID",
        )

    stmt = select(Device).where(Device.id == x_device_id)
    result = await db.execute(stmt)
    device = result.scalar_one_or_none()

    if device is None:
        logger.warning("device_not_found", device_id=x_device_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device credentials",
        )

    if not device.is_active:
        logger.warning("device_inactive", device_id=x_device_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device is deactivated",
        )

    if not verify_password(x_api_key, device.api_key_hash):
        logger.warning("invalid_api_key", device_id=x_device_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    _check_rate_limit(f"device:{x_device_id}", AGENT_RATE_LIMIT)

    return device


def require_role(
    allowed_roles: list[str],
) -> Callable:
    """Factory that creates a FastAPI dependency enforcing role-based access.

    Args:
        allowed_roles: List of role strings that are permitted access
            (e.g., ['admin', 'manager']).

    Returns:
        A FastAPI dependency function that validates the user's role.
    """

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        """Validate that the authenticated user has a permitted role.

        Args:
            current_user: The authenticated user from get_current_user.

        Returns:
            The validated User object.

        Raises:
            HTTPException: 403 if the user's role is not in the allowed list.
        """
        if current_user.role not in allowed_roles:
            logger.warning(
                "insufficient_role",
                user_id=str(current_user.id),
                role=current_user.role,
                required=allowed_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker
